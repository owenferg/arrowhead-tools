'''
arcpy adapter for integrating new arrow data into GIUM release datasets

the historical datasets are used as templates and are never edited. new features
are staged, checked, and packaged before anything is copied to the release folder.
'''

from __future__ import annotations

from dataclasses import dataclass, field
import glob
import json
import os
import shutil
import tempfile
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import arcpy
import gium_integration_core as core


_LINE_ROLES = ('herd', 'country', 'season', 'class')
_POINT_ROLES = ('herd', 'season', 'type', 'rotation')
_SYSTEM_FIELD_TYPES = {'OID', 'Geometry', 'Blob', 'Raster', 'GlobalID'}
_NUMERIC_FIELD_TYPES = {'SmallInteger', 'Integer', 'BigInteger', 'Single', 'Double'}
_ADD_FIELD_TYPES = {
    'SmallInteger': 'SHORT', 'Integer': 'LONG', 'BigInteger': 'BIGINTEGER',
    'Single': 'FLOAT', 'Double': 'DOUBLE', 'String': 'TEXT', 'Date': 'DATE',
    'DateOnly': 'DATEONLY', 'TimeOnly': 'TIMEONLY',
    'TimestampOffset': 'TIMESTAMPOFFSET', 'Guid': 'GUID', 'GUID': 'GUID',
}


@dataclass
class IntegrationResult:
    '''paths returned to the ArcGIS toolbox as derived outputs'''

    line_output: Optional[str] = None
    line_zip: Optional[str] = None
    point_output: Optional[str] = None
    point_geojson: Optional[str] = None
    qa_csv: Optional[str] = None


@dataclass
class _Branch:
    '''inputs and working paths for either the line or point half of a release'''

    label: str
    shape_type: str
    target_input: object
    new_input: object
    requested_transformation: str
    roles: Tuple[str, ...]
    profile: object
    fallbacks: Dict[str, object]
    output_name: str
    target_path: str = ''
    target_sr: object = None
    source_sr: object = None
    transformation: str = ''
    historical_count: int = 0
    new_count: int = 0
    target_fields: Dict[str, object] = field(default_factory=dict)
    source_role_fields: Dict[str, str] = field(default_factory=dict)
    target_role_fields: Dict[str, str] = field(default_factory=dict)
    filled_counts: Dict[str, int] = field(default_factory=dict)
    staged_target: str = ''
    staged_new: str = ''
    staged_output: str = ''
    staged_release: str = ''


def _normalized_path(value) -> str:
    '''normalize a path before comparing inputs and outputs'''

    return os.path.normcase(os.path.abspath(str(value))).replace('\\', '/').lower()


def _same_spatial_reference(first, second) -> bool:
    '''check whether two ArcGIS spatial references are the same'''

    if not first or not second:
        return False
    first_code = getattr(first, 'factoryCode', 0)
    second_code = getattr(second, 'factoryCode', 0)
    if first_code and second_code:
        return first_code == second_code
    return first.exportToString() == second.exportToString()


def _valid_spatial_reference(dataset, label: str):
    '''get a defined spatial reference or show a useful input error'''

    spatial_reference = getattr(arcpy.Describe(dataset), 'spatialReference', None)
    if not spatial_reference or getattr(spatial_reference, 'name', 'Unknown') == 'Unknown':
        raise ValueError(
            f'{label} has an unknown coordinate system. Use Define Projection with the '
            'correct coordinate system, then run this tool again.'
        )
    return spatial_reference


def _count(dataset) -> int:
    '''get the feature count as an integer'''

    return int(arcpy.management.GetCount(dataset)[0])


def _field_lookup(dataset) -> Dict[str, object]:
    '''index ArcGIS fields by lowercase field name'''

    return {field.name.lower(): field for field in arcpy.ListFields(dataset)}


def _resolved_fields(dataset, profile, required=True):
    '''match the pure python field rules back to ArcGIS field objects'''

    fields = list(arcpy.ListFields(dataset))
    definitions = core.resolve_role_fields(fields, profile, required)
    by_name = {field.name.casefold(): field for field in fields}
    return {
        role: by_name[definition.name.casefold()] if definition else None
        for role, definition in definitions.items()
    }


def _gcs_code(spatial_reference):
    '''get a stable code or name for the geographic coordinate system'''

    gcs = getattr(spatial_reference, 'GCS', None)
    return getattr(gcs, 'factoryCode', None) or getattr(gcs, 'name', None)


def _choose_transformation(
    source_dataset,
    source_sr,
    target_sr,
    requested: str,
    label: str,
):
    '''validate or choose the best ArcGIS geographic transformation'''

    if _same_spatial_reference(source_sr, target_sr):
        if str(requested or '').strip():
            arcpy.AddWarning(
                f'{label} already uses the target coordinate system; its transformation '
                'selection will be ignored.'
            )
        return ''
    available = list(
        arcpy.ListTransformations(
            source_sr,
            target_sr,
            getattr(arcpy.Describe(source_dataset), 'extent', None),
        )
        or []
    )
    requested = str(requested or '').strip()
    if requested:
        exact = next((item for item in available if item.lower() == requested.lower()), None)
        if not exact:
            choices = ', '.join(available[:5]) or 'none reported by ArcGIS'
            raise ValueError(
                f'The selected {label.lower()} geographic transformation {requested!r} is '
                f'not valid for these datasets. Available choices: {choices}.'
            )
        return exact
    if available:
        selected = available[0]
        arcpy.AddMessage(f'{label} will use geographic transformation: {selected}')
        return selected
    if _gcs_code(source_sr) != _gcs_code(target_sr):
        raise ValueError(
            f'ArcGIS could not find a geographic transformation for {label.lower()}. '
            'Confirm the input coordinate system and install any required ArcGIS coordinate-system data.'
        )
    return ''


def _validate_output_folder(output_folder: str, create: bool = False) -> str:
    '''check the release folder and create it only after input preflight'''

    requested = str(output_folder or '').strip()
    if not requested:
        raise ValueError('Choose an output folder for the release files.')
    output_folder = os.path.abspath(requested)
    if os.path.exists(output_folder) and not os.path.isdir(output_folder):
        raise ValueError('Output folder points to a file. Choose a folder instead.')
    if create and not os.path.isdir(output_folder):
        try:
            os.makedirs(output_folder)
        except OSError as error:
            raise ValueError(
                f'Could not create output folder {output_folder}: {error}'
            ) from None
    writable_location = output_folder
    while not os.path.exists(writable_location):
        parent = os.path.dirname(writable_location)
        if parent == writable_location:
            break
        writable_location = parent
    if not os.path.isdir(writable_location) or not os.access(
        writable_location, os.W_OK
    ):
        raise ValueError(
            'Output folder is not writable. Choose a folder where you can save files.'
        )
    return output_folder


def _artifact_paths(output_folder: str, release_date, process_lines, process_points):
    '''build only the output paths needed by the enabled branches'''

    paths = core.release_artifact_paths(output_folder, release_date)
    result = IntegrationResult(qa_csv=paths['qa_csv'])
    if process_lines:
        result.line_output = paths['line_shapefile']
        result.line_zip = paths['line_zip']
    if process_points:
        result.point_output = paths['point_shapefile']
        result.point_geojson = paths['point_geojson']
    return result


def _assert_no_collisions(result: IntegrationResult) -> None:
    '''stop if any dated release file or shapefile sidecar already exists'''

    collisions = []
    for path in (result.line_zip, result.point_geojson, result.qa_csv):
        if path and os.path.exists(path):
            collisions.append(path)
    for shapefile in (result.line_output, result.point_output):
        if shapefile:
            base = os.path.splitext(shapefile)[0]
            collisions.extend(
                path for path in glob.glob(base + '.*') if os.path.exists(path)
            )
    if collisions:
        names = ', '.join(sorted({os.path.basename(path) for path in collisions}))
        raise ValueError(
            f'This release already exists ({names}). Choose a different release date or '
            'output folder. Existing releases are never overwritten.'
        )


def _validate_branch(branch: _Branch) -> None:
    '''validate one complete target and its selected new features'''

    target_description = arcpy.Describe(branch.target_input)
    source_description = arcpy.Describe(branch.new_input)

    # make sure both inputs are the right geometry type and do not contain joins
    if getattr(target_description, 'shapeType', None) != branch.shape_type:
        raise ValueError(
            f'{branch.label} target must contain {branch.shape_type.lower()} features.'
        )
    if getattr(source_description, 'shapeType', None) != branch.shape_type:
        raise ValueError(
            f'New {branch.label.lower()} must contain '
            f'{branch.shape_type.lower()} features.'
        )
    if any('.' in field.name for field in arcpy.ListFields(branch.new_input)):
        raise ValueError(
            f'New {branch.label.lower()} has a joined table. Remove the join before '
            'running this tool so each source field can be mapped unambiguously.'
        )

    # use the target catalog path so a target selection cannot remove history
    # keep the new input as a layer so its selection or definition query is honored
    branch.target_path = str(
        getattr(target_description, 'catalogPath', branch.target_input)
    )
    if os.path.splitext(branch.target_path)[1].casefold() != '.shp':
        raise ValueError(
            f'{branch.label} target must be the current production shapefile (.shp). '
            'A geodatabase layer can contain longer field names or values that a shapefile '
            'would silently shorten, so it cannot safely define the release schema.'
        )
    if _normalized_path(branch.target_path) == _normalized_path(
        getattr(source_description, 'catalogPath', branch.new_input)
    ):
        raise ValueError(f'{branch.label} target and new features must be different datasets.')
    branch.target_sr = _valid_spatial_reference(
        branch.target_path, f'{branch.label} target'
    )
    branch.source_sr = _valid_spatial_reference(
        branch.new_input, f'New {branch.label.lower()}'
    )
    branch.historical_count = _count(branch.target_path)
    if branch.historical_count <= 0:
        raise ValueError(
            f'{branch.label} target contains no historical features. Select the latest '
            'complete GIUM production shapefile, not an empty schema template.'
        )
    branch.new_count = _count(branch.new_input)
    if branch.new_count <= 0:
        raise ValueError(
            f'New {branch.label.lower()} contains no selected features. Select the intended '
            'features or clear the selection, then run the tool again.'
        )
    branch.transformation = _choose_transformation(
        branch.new_input, branch.source_sr, branch.target_sr,
        branch.requested_transformation, branch.label,
    )

    branch.target_fields = _field_lookup(branch.target_path)
    resolved_target = _resolved_fields(branch.target_path, branch.profile, required=True)
    for role in branch.roles:
        target_field = resolved_target[role]
        core.validate_role_field(role, target_field)
        branch.target_role_fields[role] = target_field.name

    # country is optional on point labels but is mapped when the target has it
    if branch.label == 'Arrowhead points':
        country = resolved_target.get('country')
        if country:
            core.validate_role_field('country', country)
            branch.target_role_fields['country'] = country.name


def _add_field_like(dataset, field) -> str:
    '''add a staging field that matches its production target field'''

    field_type = _ADD_FIELD_TYPES.get(field.type)
    if not field_type:
        raise ValueError(
            f'Field {field.name!r} uses unsupported ArcGIS type {field.type!r}.'
        )
    kwargs = {}
    if field.type == 'String':
        kwargs['field_length'] = field.length
    elif field.type in _NUMERIC_FIELD_TYPES:
        precision = getattr(field, 'precision', None)
        scale = getattr(field, 'scale', None)
        if precision is not None:
            kwargs['field_precision'] = precision
        if scale is not None:
            kwargs['field_scale'] = scale
    arcpy.management.AddField(dataset, field.name, field_type, **kwargs)
    return field.name


def _compatible(source_field, target_field, allow_numeric_conversion=False) -> bool:
    '''check whether ArcGIS can map fields without losing information'''

    if source_field.type == target_field.type:
        return True
    return (
        allow_numeric_conversion
        and source_field.type in _NUMERIC_FIELD_TYPES
        and target_field.type in _NUMERIC_FIELD_TYPES
    )


def _prepare_role_fields(branch: _Branch) -> None:
    '''add missing GIUM fields in staging and fill only blank values'''

    source_fields = _field_lookup(branch.staged_new)
    resolved_source = _resolved_fields(branch.staged_new, branch.profile, required=False)
    roles = list(branch.roles)
    if 'country' in branch.target_role_fields and 'country' not in roles:
        roles.append('country')

    for role in roles:
        target_field = branch.target_fields[branch.target_role_fields[role].lower()]
        source_field = resolved_source.get(role)
        if source_field and not _compatible(
            source_field, target_field, allow_numeric_conversion=role == 'rotation'
        ):
            raise ValueError(
                f'New {branch.label.lower()} field {source_field.name} is type '
                f'{source_field.type}, but target field {target_field.name} is '
                f'{target_field.type}. Correct the source field type before continuing.'
            )
        if not source_field:
            # add the target field to staging so an explicit FieldMap can use it
            _add_field_like(branch.staged_new, target_field)
            source_fields = _field_lookup(branch.staged_new)
            source_field = source_fields[target_field.name.lower()]
        branch.source_role_fields[role] = source_field.name

    cursor_fields = ['OID@'] + [branch.source_role_fields[role] for role in roles]
    branch.filled_counts = {role: 0 for role in roles}
    with arcpy.da.UpdateCursor(branch.staged_new, cursor_fields) as rows:
        for row in rows:
            row = list(row)
            oid = row[0]
            changed = False
            for index, role in enumerate(roles, start=1):
                source_value = row[index]
                fallback = branch.fallbacks.get(role)
                target_field = branch.target_fields[
                    branch.target_role_fields[role].lower()
                ]
                value = core.resolved_role_value(
                    role, source_value, fallback, target_field,
                    required=role in branch.roles,
                    context=f'feature {oid}',
                )
                if core.is_blank(source_value) and not core.is_blank(fallback):
                    branch.filled_counts[role] += 1
                    changed = True
                row[index] = value
            if changed:
                rows.updateRow(row)


def _build_field_mappings(branch: _Branch):
    '''create explicit Append field mappings from new data to the target schema'''

    source_fields = _field_lookup(branch.staged_new)
    role_by_target = {
        name.lower(): role for role, name in branch.target_role_fields.items()
    }
    mappings = arcpy.FieldMappings()
    mapped = []
    extra_text_mappings = []
    for target_field in arcpy.ListFields(branch.target_path):
        if (
            target_field.type in _SYSTEM_FIELD_TYPES
            or getattr(target_field, 'required', False)
            or not getattr(target_field, 'editable', True)
        ):
            continue
        role = role_by_target.get(target_field.name.lower())
        source_name = branch.source_role_fields.get(role) if role else None
        if not source_name:
            same_name = source_fields.get(target_field.name.lower())
            if same_name:
                if not _compatible(same_name, target_field):
                    raise ValueError(
                        f'New {branch.label.lower()} field {same_name.name} is type '
                        f'{same_name.type}, but the same-named target field is '
                        f'{target_field.type}. ArcGIS could coerce or lose values; correct '
                        'the source field type before continuing.'
                    )
                source_name = same_name.name
        if not source_name:
            continue
        field_map = arcpy.FieldMap()
        field_map.addInputField(branch.staged_new, source_name)
        output_field = field_map.outputField
        output_field.name = target_field.name
        output_field.aliasName = getattr(target_field, 'aliasName', target_field.name)
        field_map.outputField = output_field
        mappings.addFieldMap(field_map)
        mapped.append(f'{source_name}->{target_field.name}')
        if role is None and target_field.type == 'String':
            extra_text_mappings.append((source_name, target_field))
    if not mapped:
        raise ValueError(f'No compatible fields could be mapped for {branch.label.lower()}.')
    for source_name, target_field in extra_text_mappings:
        with arcpy.da.SearchCursor(branch.staged_new, ['OID@', source_name]) as rows:
            for oid, value in rows:
                try:
                    core.validate_value_for_field(
                        value, target_field, target_field.name, allow_blank=True
                    )
                except ValueError as error:
                    raise ValueError(f'Feature {oid}: {error}') from None
    arcpy.AddMessage(f'{branch.label} field mapping: {", ".join(mapped)}')
    return mappings, mapped


def _null_geometry_count(dataset) -> int:
    '''count null or empty features in a dataset'''

    count = 0
    with arcpy.da.SearchCursor(dataset, ['SHAPE@']) as rows:
        for (geometry,) in rows:
            if geometry is None or getattr(geometry, 'isEmpty', False):
                count += 1
    return count


def _invalid_geometry_count(dataset, output_table: str) -> int:
    '''count ArcGIS geometry problems, including nonempty invalid geometry'''

    arcpy.management.CheckGeometry(dataset, output_table, 'ESRI')
    return _count(output_table)


def _schema_signature(dataset):
    '''record the production field definitions in their original order'''

    signature = []
    for field in arcpy.ListFields(dataset):
        if field.type in _SYSTEM_FIELD_TYPES or getattr(field, 'required', False):
            continue
        item = [field.name.casefold(), field.type]
        if field.type == 'String':
            item.append(getattr(field, 'length', None))
        elif field.type in _NUMERIC_FIELD_TYPES:
            item.extend([
                getattr(field, 'precision', None),
                getattr(field, 'scale', None),
            ])
        item.append(getattr(field, 'isNullable', None))
        signature.append(tuple(item))
    return signature


def _stage_branch(
    branch: _Branch,
    gdb: str,
    release_dir: str,
    qa_rows: List[core.QARow],
):
    '''stage, append, and validate either the line or point branch'''

    safe = 'lines' if branch.shape_type == 'Polyline' else 'points'
    branch.staged_target = os.path.join(gdb, f'{safe}_historical')
    copied_new = os.path.join(gdb, f'{safe}_new_source')
    branch.staged_new = os.path.join(gdb, f'{safe}_new_projected')
    branch.staged_output = os.path.join(gdb, f'{safe}_complete')

    # copy the complete target but only the selected new records into staging
    arcpy.AddMessage(f'Copying the complete historical {branch.label.lower()} target...')
    arcpy.management.CopyFeatures(branch.target_path, branch.staged_target)
    arcpy.management.CopyFeatures(branch.new_input, copied_new)
    if _count(copied_new) != branch.new_count:
        raise ValueError(f'ArcGIS did not copy all selected new {branch.label.lower()}.')

    # project the new records into the target coordinate system before appending
    if _same_spatial_reference(branch.source_sr, branch.target_sr):
        arcpy.management.CopyFeatures(copied_new, branch.staged_new)
    else:
        arcpy.AddMessage(
            f'Projecting new {branch.label.lower()} to the target coordinate system...'
        )
        kwargs = {
            'in_dataset': copied_new,
            'out_dataset': branch.staged_new,
            'out_coor_system': branch.target_sr,
        }
        if branch.transformation:
            kwargs['transform_method'] = branch.transformation
        arcpy.management.Project(**kwargs)
    if _count(branch.staged_new) != branch.new_count:
        raise ValueError(f'Projection changed the number of new {branch.label.lower()} features.')
    nulls = _null_geometry_count(branch.staged_new)
    if nulls:
        raise ValueError(
            f'Projection produced {nulls} empty {branch.label.lower()} geometries. '
            'Run Check Geometry on the source data and try again.'
        )
    invalids = _invalid_geometry_count(
        branch.staged_new, os.path.join(gdb, f'{safe}_new_geometry_problems')
    )
    if invalids:
        raise ValueError(
            f'ArcGIS found {invalids} geometry problem(s) in the projected new '
            f'{branch.label.lower()}. Repair the source geometry and run the tool again.'
        )

    # fill required fields, map them explicitly, and append to a copy of the target
    _prepare_role_fields(branch)
    mappings, mapped = _build_field_mappings(branch)
    arcpy.management.CopyFeatures(branch.staged_target, branch.staged_output)
    arcpy.AddMessage(f'Appending {branch.new_count:,} new {branch.label.lower()}...')
    arcpy.management.Append(branch.staged_new, branch.staged_output, 'NO_TEST', mappings)
    final_count = _count(branch.staged_output)
    expected = branch.historical_count + branch.new_count
    if final_count != expected:
        raise ValueError(
            f'{branch.label} count check failed: expected {expected:,}, found '
            f'{final_count:,}. No release files were published.'
        )
    if _null_geometry_count(branch.staged_output):
        raise ValueError(
            f'The complete {branch.label.lower()} output contains empty geometry.'
        )
    invalids = _invalid_geometry_count(
        branch.staged_output, os.path.join(gdb, f'{safe}_complete_geometry_problems')
    )
    if invalids:
        raise ValueError(
            f'ArcGIS found {invalids} geometry problem(s) after combining the '
            f'{branch.label.lower()}. No release files were published.'
        )
    output_sr = _valid_spatial_reference(
        branch.staged_output, f'Complete {branch.label.lower()}'
    )
    if not _same_spatial_reference(output_sr, branch.target_sr):
        raise ValueError(
            f'The complete {branch.label.lower()} has the wrong coordinate system.'
        )

    # create the final shapefile in staging and verify that its schema survived
    branch.staged_release = os.path.join(release_dir, branch.output_name)
    arcpy.management.CopyFeatures(branch.staged_output, branch.staged_release)
    release_count = _count(branch.staged_release)
    release_sr = _valid_spatial_reference(
        branch.staged_release, f'Packaged {branch.label.lower()}'
    )
    target_schema = _schema_signature(branch.target_path)
    release_schema = _schema_signature(branch.staged_release)
    if release_count != expected:
        raise ValueError(
            f'Packaged {branch.label.lower()} count check failed: expected {expected:,}, '
            f'found {release_count:,}.'
        )
    if not _same_spatial_reference(release_sr, branch.target_sr):
        raise ValueError(f'Packaged {branch.label.lower()} has the wrong coordinate system.')
    if release_schema != target_schema:
        raise ValueError(
            f'Packaged {branch.label.lower()} schema does not match its production target. '
            'A field name, order, type, length, precision, scale, or nullability changed.'
        )
    invalids = _invalid_geometry_count(
        branch.staged_release, os.path.join(gdb, f'{safe}_release_geometry_problems')
    )
    if invalids:
        raise ValueError(
            f'Packaged {branch.label.lower()} contains {invalids} geometry problem(s).'
        )
    qa_rows.extend([
        _qa(branch.label, 'target_input_path', 'PASS', branch.target_path,
            'Complete historical target; layer selections were ignored.'),
        _qa(branch.label, 'new_input_path', 'PASS',
            str(getattr(arcpy.Describe(branch.new_input), 'catalogPath', branch.new_input)),
            'Selections and definition queries on this layer were honored.'),
        _qa(
            branch.label,
            'historical_count',
            'PASS',
            branch.historical_count,
            branch.target_path,
        ),
        _qa(branch.label, 'new_count', 'PASS', branch.new_count,
            'Selections and definition queries on the new layer were honored.'),
        _qa(branch.label, 'final_count', 'PASS', final_count, f'Expected {expected}.'),
        _qa(branch.label, 'target_spatial_reference', 'PASS',
            getattr(branch.target_sr, 'name', ''), ''),
        _qa(branch.label, 'geographic_transformation', 'PASS',
            branch.transformation or 'None required', ''),
        _qa(branch.label, 'field_mapping', 'PASS', '; '.join(mapped), ''),
        _qa(branch.label, 'packaged_schema', 'PASS',
            '; '.join(item[0] for item in release_schema),
            'Matches the selected production target.'),
    ])
    for role, count in branch.filled_counts.items():
        qa_rows.append(_qa(branch.label, f'{role}_fallback_rows', 'PASS', count, ''))
        qa_rows.append(
            _qa(
                branch.label,
                f'{role}_missing_rows',
                'PASS',
                0,
                'Validated on every selected new feature.',
            )
        )
    if 'rotation' in branch.source_role_fields:
        rotations = []
        with arcpy.da.SearchCursor(
            branch.staged_new, [branch.source_role_fields['rotation']]
        ) as rows:
            rotations = [float(value) for (value,) in rows]
        qa_rows.extend([
            _qa(branch.label, 'rotation_minimum', 'PASS', min(rotations),
                'Selected new arrowheads.'),
            _qa(branch.label, 'rotation_maximum', 'PASS', max(rotations),
                'Selected new arrowheads.'),
        ])


def _qa(section, check, status, value, details):
    '''make one row for the release QA report'''

    return core.QARow(section, check, status, value, details)


def _write_zip(staged_shapefile: str, zip_path: str) -> List[str]:
    '''write the line shapefile and its required sidecars to a ZIP'''

    base = os.path.splitext(staged_shapefile)[0]
    members = core.select_shapefile_zip_members(
        glob.glob(base + '.*'), os.path.basename(staged_shapefile)
    )
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            archive.write(path, os.path.basename(path))
    return [os.path.basename(path) for path in members]


def _write_qa(path: str, rows: Iterable[core.QARow]) -> None:
    '''write the QA report with Excel-friendly UTF-8 encoding'''

    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        stream.write(core.qa_csv_text(rows))


def _copy_release_artifacts(sources: Sequence[str], output_folder: str) -> List[str]:
    '''publish staged files and remove partial copies if any copy fails'''

    created = []
    try:
        for source in sources:
            destination = os.path.join(output_folder, os.path.basename(source))
            if os.path.exists(destination):
                raise ValueError(
                    f'Release artifact appeared during processing: {destination}'
                )
            created.append(destination)
            shutil.copy2(source, destination)
    except Exception:
        for path in reversed(created):
            try:
                os.remove(path)
            except OSError as cleanup_error:
                arcpy.AddWarning(f'Could not remove partial release file {path}: {cleanup_error}')
        raise
    return created


def _as_boolean(value, label: str) -> bool:
    '''normalize ArcPy booleans without treating the text false as true'''

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    text = str(value or '').strip().casefold()
    if text in ('true', '1', 'yes'):
        return True
    if text in ('false', '0', 'no', ''):
        return False
    raise ValueError(f'{label} must be True or False.')


def execute(
    process_lines,
    line_target,
    new_lines,
    line_transformation,
    process_points,
    point_target,
    new_points,
    point_transformation,
    herd_name,
    country,
    season,
    line_class,
    point_type,
    release_date,
    output_folder,
):
    '''build and safely publish a complete GIUM arrow release'''

    # normalize ArcGIS parameter values and calculate the dated output paths
    process_lines = _as_boolean(process_lines, 'Process seasonal arrow lines')
    process_points = _as_boolean(process_points, 'Process arrowhead points')
    if not process_lines and not process_points:
        raise ValueError(
            'Select at least one section: seasonal arrow lines or arrowhead points.'
        )
    output_folder = _validate_output_folder(output_folder)
    stamp = core.release_stamp(release_date)
    result = _artifact_paths(output_folder, release_date, process_lines, process_points)
    _assert_no_collisions(result)

    # keep the shared fallback values together for both enabled branches
    branches = []
    shared = {
        'herd': herd_name,
        'country': country,
        'season': season,
        'class': line_class,
        'type': point_type or 'Arrowhead',
    }
    if process_lines:
        if not line_target or not new_lines:
            raise ValueError(
                'Choose both the existing SeasonalArrows target and new arrow lines.'
            )
        branches.append(
            _Branch(
                'Seasonal arrow lines',
                'Polyline',
                line_target,
                new_lines,
                line_transformation or '',
                _LINE_ROLES,
                core.LINE_ROLE_PROFILE,
                shared,
                os.path.basename(result.line_output),
            )
        )
    if process_points:
        if not point_target or not new_points:
            raise ValueError(
                'Choose both the existing GIUMPointLabels target and new arrowheads.'
            )
        branches.append(
            _Branch(
                'Arrowhead points',
                'Point',
                point_target,
                new_points,
                point_transformation or '',
                _POINT_ROLES,
                core.POINT_ROLE_PROFILE,
                shared,
                os.path.basename(result.point_output),
            )
        )

    # all checks above and below this point are read-only
    arcpy.AddMessage('Checking inputs before creating release files...')
    for branch in branches:
        _validate_branch(branch)

    # make sure an unusual output path cannot replace an input
    final_paths = [path for path in vars(result).values() if path]
    source_paths = [branch.target_path for branch in branches]
    source_paths += [
        str(
            getattr(
                arcpy.Describe(branch.new_input),
                'catalogPath',
                branch.new_input,
            )
        )
        for branch in branches
    ]
    if {_normalized_path(path) for path in final_paths} & {
        _normalized_path(path) for path in source_paths
    }:
        raise ValueError('A release output path cannot replace an input dataset.')

    # create temporary staging only after every enabled branch passes preflight
    output_folder = _validate_output_folder(output_folder, create=True)
    staging_root = tempfile.mkdtemp(prefix='.gium_arrow_staging_', dir=output_folder)
    release_dir = os.path.join(staging_root, 'release')
    os.mkdir(release_dir)
    qa_rows = [
        _qa('Release', 'release_date', 'PASS', stamp, ''),
        _qa(
            'Release',
            'atomic_mode',
            'PASS',
            'Enabled',
            'All enabled sections must pass before files are published.',
        ),
    ]
    published = []
    try:
        gdb_result = arcpy.management.CreateFileGDB(staging_root, 'staging.gdb')
        gdb = os.path.join(staging_root, 'staging.gdb')
        # real ArcPy results are indexable; the fallback also supports the test adapter
        try:
            gdb = str(gdb_result[0])
        except (TypeError, IndexError, KeyError):
            gdb = os.path.join(staging_root, 'staging.gdb')

        for branch in branches:
            _stage_branch(branch, gdb, release_dir, qa_rows)

        # collect complete shapefile sidecars and build the line ZIP
        promotion_sources = []
        if process_lines:
            staged_line = os.path.join(release_dir, os.path.basename(result.line_output))
            staged_zip = os.path.join(release_dir, os.path.basename(result.line_zip))
            members = _write_zip(staged_line, staged_zip)
            promotion_sources.extend(core.select_shapefile_zip_members(
                glob.glob(os.path.splitext(staged_line)[0] + '.*'),
                os.path.basename(staged_line),
            ))
            promotion_sources.append(staged_zip)
            qa_rows.append(
                _qa(
                    'Seasonal arrow lines',
                    'zip_members',
                    'PASS',
                    '; '.join(members),
                    'Files are stored at the ZIP root.',
                )
            )

        # convert the complete point release to the WGS 84 GeoJSON used downstream
        if process_points:
            staged_points = os.path.join(
                release_dir, os.path.basename(result.point_output)
            )
            staged_geojson = os.path.join(
                release_dir, os.path.basename(result.point_geojson)
            )
            arcpy.AddMessage('Creating formatted WGS 84 GeoJSON for Mapbox...')
            arcpy.conversion.FeaturesToJSON(
                staged_points,
                staged_geojson,
                'FORMATTED',
                'NO_Z_VALUES',
                'NO_M_VALUES',
                'GEOJSON',
                'WGS84',
                'USE_FIELD_NAME',
            )
            if not os.path.isfile(staged_geojson) or os.path.getsize(staged_geojson) == 0:
                raise ValueError('ArcGIS did not create a valid point-label GeoJSON file.')
            try:
                with open(staged_geojson, encoding='utf-8') as stream:
                    geojson = json.load(stream)
            except (OSError, ValueError) as error:
                raise ValueError(f'Point-label GeoJSON could not be read: {error}') from None
            expected_points = next(
                branch.historical_count + branch.new_count
                for branch in branches if branch.shape_type == 'Point'
            )
            if (
                geojson.get('type') != 'FeatureCollection'
                or len(geojson.get('features', [])) != expected_points
            ):
                raise ValueError(
                    'Point-label GeoJSON validation failed: its feature count does not '
                    'match the complete point-label release.'
                )
            qa_rows.append(
                _qa(
                    'Arrowhead points',
                    'geojson',
                    'PASS',
                    f'WGS 84 / field names / {expected_points} features',
                    os.path.basename(staged_geojson),
                )
            )
            promotion_sources.extend(core.select_shapefile_zip_members(
                glob.glob(os.path.splitext(staged_points)[0] + '.*'),
                os.path.basename(staged_points),
            ))
            promotion_sources.append(staged_geojson)

        # write the QA report last so it describes every artifact being published
        staged_qa = os.path.join(release_dir, os.path.basename(result.qa_csv))
        for artifact_name, artifact_path in vars(result).items():
            if artifact_path:
                qa_rows.append(
                    _qa(
                        'Release',
                        f'{artifact_name}_path',
                        'PASS',
                        artifact_path,
                        'Final release artifact.',
                    )
                )
        qa_rows.append(
            _qa(
                'Release',
                'overall_result',
                'PASS',
                'Ready for visual review',
                'Historical inputs were not changed.',
            )
        )
        _write_qa(staged_qa, qa_rows)
        promotion_sources.append(staged_qa)
        published = _copy_release_artifacts(promotion_sources, output_folder)
        arcpy.AddMessage('GIUM arrow release created successfully.')
        for path in (
            result.line_output,
            result.line_zip,
            result.point_output,
            result.point_geojson,
            result.qa_csv,
        ):
            if path:
                arcpy.AddMessage(f'Created: {path}')
        arcpy.AddWarning(
            'Complete visual review before publishing to Mapbox: compare the old and new '
            'layers in ArcGIS Pro '
            'and confirm arrow placement, rotation, herd, season, class, and type.'
        )
        return result
    except Exception:
        # remove any final files if a future step fails after publication starts
        for path in reversed(published):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as cleanup_error:
                arcpy.AddWarning(f'Could not remove partial release file {path}: {cleanup_error}')
        raise
    finally:
        try:
            shutil.rmtree(staging_root)
        except OSError as cleanup_error:
            arcpy.AddWarning(
                f'Could not remove temporary staging folder {staging_root}: {cleanup_error}'
            )
