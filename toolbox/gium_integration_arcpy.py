'''
arcpy adapter for integrating new data into GIUM release datasets

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
import time
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import arcpy  # pyright: ignore[reportMissingImports]
import gium_integration_core as core


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

    created: List[str] = field(default_factory=list)
    qa_csv: Optional[str] = None


@dataclass
class _Dataset:
    '''inputs and working paths for one row of a multi-dataset release'''

    index: int
    profile: object
    label: str
    shape_type: str
    target_input: object
    new_input: object
    requested_transformation: str
    package_choice: object
    package_formats: Tuple[str, ...]
    fallbacks: Dict[str, object]
    roles: Tuple[str, ...] = ()
    managed_roles: Tuple[str, ...] = ()
    output_name: str = ''
    shapefile_path: str = ''
    zip_path: Optional[str] = None
    geojson_path: Optional[str] = None
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
                f'The selected {label} geographic transformation {requested!r} is '
                f'not valid for these datasets. Available choices: {choices}.'
            )
        return exact
    if available:
        selected = available[0]
        arcpy.AddMessage(f'{label} will use geographic transformation: {selected}')
        return selected
    if _gcs_code(source_sr) != _gcs_code(target_sr):
        source_gcs = getattr(getattr(source_sr, 'GCS', None), 'name', 'unknown')
        target_gcs = getattr(getattr(target_sr, 'GCS', None), 'name', 'unknown')
        target_name = getattr(target_sr, 'name', 'the target coordinate system')
        raise ValueError(
            f'ArcGIS has no geographic transformation from {source_gcs} to '
            f'{target_gcs} covering the area of these {label} features, so this tool '
            f'cannot project them safely. Run the ArcGIS Pro Project tool on the new '
            f'data to convert them to {target_name} first, then choose the '
            'projected layer here and run this tool again.'
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


def _assign_artifact_paths(dataset: _Dataset, output_folder: str, release_date) -> None:
    '''fill the dated output paths for one validated dataset'''

    names = core.release_artifact_names(
        dataset.profile,
        release_date,
        dataset.package_choice,
        dataset.target_path,
    )
    dataset.output_name = names.shapefile
    dataset.shapefile_path = os.path.join(output_folder, names.shapefile)
    dataset.zip_path = os.path.join(output_folder, names.zip) if names.zip else None
    dataset.geojson_path = (
        os.path.join(output_folder, names.geojson) if names.geojson else None
    )


def _planned_paths(datasets: Sequence[_Dataset], qa_csv: str) -> List[str]:
    '''list every dated file this run intends to publish'''

    paths = []
    for dataset in datasets:
        paths.append(dataset.shapefile_path)
        if dataset.zip_path:
            paths.append(dataset.zip_path)
        if dataset.geojson_path:
            paths.append(dataset.geojson_path)
    paths.append(qa_csv)
    return paths


def _assert_no_collisions(paths: Sequence[str]) -> None:
    '''stop if any dated release file or shapefile sidecar already exists'''

    collisions = []
    for path in paths:
        if not path:
            continue
        if str(path).lower().endswith('.shp'):
            base = os.path.splitext(path)[0]
            collisions.extend(
                item for item in glob.glob(base + '.*') if os.path.exists(item)
            )
        elif os.path.exists(path):
            collisions.append(path)
    if collisions:
        names = ', '.join(sorted({os.path.basename(path) for path in collisions}))
        raise ValueError(
            f'This release already exists ({names}). Choose a different release date or '
            'output folder. Existing releases are never overwritten.'
        )


def _multipart_count(dataset) -> int:
    '''count new features Mapbox cannot import because they have more than one part'''

    count = 0
    with arcpy.da.SearchCursor(dataset, ['SHAPE@']) as rows:
        for (geometry,) in rows:
            if geometry is None:
                continue
            try:
                if int(getattr(geometry, 'partCount', 1) or 1) > 1:
                    count += 1
            except (TypeError, ValueError):
                continue
    return count


def _validate_dataset(dataset: _Dataset) -> None:
    '''validate one complete target and its selected new features'''

    target_description = arcpy.Describe(dataset.target_input)
    source_description = arcpy.Describe(dataset.new_input)
    target_shape = getattr(target_description, 'shapeType', None)
    source_shape = getattr(source_description, 'shapeType', None)
    expected = dataset.profile.shape_type

    if expected:
        if target_shape != expected:
            raise ValueError(
                f'The {dataset.label} target must contain {expected.lower()} features.'
            )
        if source_shape != expected:
            raise ValueError(
                f'The {dataset.label} new data must contain {expected.lower()} features.'
            )
        dataset.shape_type = expected
    else:
        if target_shape not in core.SUPPORTED_SHAPE_TYPES:
            raise ValueError(
                f'The {dataset.label} target must contain point, polyline, or polygon '
                'features.'
            )
        if source_shape != target_shape:
            raise ValueError(
                f'The {dataset.label} new data must be the same geometry type as the '
                f'target ({str(target_shape).lower()}).'
            )
        dataset.shape_type = target_shape

    if any('.' in field.name for field in arcpy.ListFields(dataset.new_input)):
        raise ValueError(
            f'The {dataset.label} new data has a joined table. Remove the join before '
            'running this tool so each source field can be mapped unambiguously.'
        )

    # use the target catalog path so a target selection cannot remove history
    # keep the new input as a layer so its selection or definition query is honored
    dataset.target_path = str(
        getattr(target_description, 'catalogPath', dataset.target_input)
    )
    if os.path.splitext(dataset.target_path)[1].casefold() != '.shp':
        raise ValueError(
            f'The {dataset.label} target must be the current production shapefile (.shp). '
            'A geodatabase layer can contain longer field names or values that a shapefile '
            'would silently shorten, so it cannot safely define the release schema.'
        )
    if _normalized_path(dataset.target_path) == _normalized_path(
        getattr(source_description, 'catalogPath', dataset.new_input)
    ):
        raise ValueError(
            f'The {dataset.label} target and new data must be different datasets.'
        )
    dataset.target_sr = _valid_spatial_reference(
        dataset.target_path, f'{dataset.label} target'
    )
    dataset.source_sr = _valid_spatial_reference(
        dataset.new_input, f'{dataset.label} new data'
    )
    dataset.historical_count = _count(dataset.target_path)
    if dataset.historical_count <= 0:
        raise ValueError(
            f'The {dataset.label} target contains no historical features. Select the '
            'latest complete GIUM production shapefile, not an empty schema template.'
        )
    dataset.new_count = _count(dataset.new_input)
    if dataset.new_count <= 0:
        raise ValueError(
            f'The {dataset.label} new data contains no selected features. Select the '
            'intended features or clear the selection, then run the tool again.'
        )
    multipart = _multipart_count(dataset.new_input)
    if multipart:
        raise ValueError(
            f'{dataset.label}: {multipart} of {dataset.new_count} new features are '
            'multipart. Mapbox cannot import multipart data. Run the Multipart To '
            'Singlepart tool on the new data, then choose that result and run this '
            'tool again.'
        )
    dataset.transformation = _choose_transformation(
        dataset.new_input, dataset.source_sr, dataset.target_sr,
        dataset.requested_transformation, dataset.label,
    )

    dataset.target_fields = _field_lookup(dataset.target_path)
    resolved_target = _resolved_fields(
        dataset.target_path, dataset.profile, required=False
    )
    dataset.managed_roles = core.present_roles(resolved_target)
    dataset.roles = core.enforced_roles(dataset.profile, resolved_target)
    for role in dataset.managed_roles:
        target_field = resolved_target[role]
        core.validate_role_field(role, target_field)
        dataset.target_role_fields[role] = target_field.name


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


def _missing_required_values(dataset: _Dataset) -> List[str]:
    '''report every required GIUM value the release cannot supply yet

    checked before any copying so a blank metadata box fails in seconds rather
    than after the historical target has been copied and projected.
    '''

    resolved = _resolved_fields(dataset.new_input, dataset.profile, required=False)
    problems: List[str] = []
    countable = []
    for role in dataset.roles:
        # an entered value fills every blank, so the source data cannot fall short
        if not core.is_blank(dataset.fallbacks.get(role)):
            continue
        source_field = resolved.get(role)
        if source_field is None:
            aliases = ', '.join(dataset.profile.aliases.get(role, ()))
            problems.append(
                f'{dataset.label}: no {role} field (accepted names: {aliases}) and no '
                f'{role} value entered, so every feature would be missing it.'
            )
            continue
        countable.append((role, source_field.name))

    if not countable:
        return problems

    blanks = {role: 0 for role, _ in countable}
    total = 0
    with arcpy.da.SearchCursor(
        dataset.new_input, [name for _, name in countable]
    ) as rows:
        for row in rows:
            total += 1
            for index, (role, _) in enumerate(countable):
                if core.is_blank(row[index]):
                    blanks[role] += 1

    for role, field_name in countable:
        if blanks[role]:
            problems.append(
                f'{dataset.label}: {blanks[role]} of {total} features have a blank '
                f'{field_name} and no {role} value was entered.'
            )
    return problems


def _prepare_role_fields(dataset: _Dataset) -> None:
    '''add missing GIUM fields in staging and fill only blank values'''

    source_fields = _field_lookup(dataset.staged_new)
    resolved_source = _resolved_fields(dataset.staged_new, dataset.profile, required=False)
    roles = list(dataset.managed_roles)
    if not roles:
        dataset.filled_counts = {}
        return

    for role in roles:
        target_field = dataset.target_fields[dataset.target_role_fields[role].lower()]
        source_field = resolved_source.get(role)
        if source_field and not _compatible(
            source_field, target_field, allow_numeric_conversion=role == 'rotation'
        ):
            raise ValueError(
                f'The {dataset.label} field {source_field.name} is type '
                f'{source_field.type}, but target field {target_field.name} is '
                f'{target_field.type}. Correct the source field type before continuing.'
            )
        if not source_field:
            # add the target field to staging so an explicit FieldMap can use it
            _add_field_like(dataset.staged_new, target_field)
            source_fields = _field_lookup(dataset.staged_new)
            source_field = source_fields[target_field.name.lower()]
        dataset.source_role_fields[role] = source_field.name

    cursor_fields = ['OID@'] + [dataset.source_role_fields[role] for role in roles]
    dataset.filled_counts = {role: 0 for role in roles}
    with arcpy.da.UpdateCursor(dataset.staged_new, cursor_fields) as rows:
        for row in rows:
            row = list(row)
            oid = row[0]
            changed = False
            for index, role in enumerate(roles, start=1):
                source_value = row[index]
                fallback = dataset.fallbacks.get(role)
                target_field = dataset.target_fields[
                    dataset.target_role_fields[role].lower()
                ]
                value = core.resolved_role_value(
                    role, source_value, fallback, target_field,
                    required=role in dataset.roles,
                    context=f'feature {oid}',
                )
                if core.is_blank(source_value) and not core.is_blank(fallback):
                    dataset.filled_counts[role] += 1
                    changed = True
                row[index] = value
            if changed:
                rows.updateRow(row)


def _build_field_mappings(dataset: _Dataset):
    '''create explicit Append field mappings from new data to the target schema'''

    source_fields = _field_lookup(dataset.staged_new)
    role_by_target = {
        name.lower(): role for role, name in dataset.target_role_fields.items()
    }
    mappings = arcpy.FieldMappings()
    mapped = []
    extra_text_mappings = []
    for target_field in arcpy.ListFields(dataset.target_path):
        if (
            target_field.type in _SYSTEM_FIELD_TYPES
            or getattr(target_field, 'required', False)
            or not getattr(target_field, 'editable', True)
        ):
            continue
        role = role_by_target.get(target_field.name.lower())
        source_name = dataset.source_role_fields.get(role) if role else None
        if not source_name:
            same_name = source_fields.get(target_field.name.lower())
            if same_name:
                if not _compatible(same_name, target_field):
                    raise ValueError(
                        f'The {dataset.label} field {same_name.name} is type '
                        f'{same_name.type}, but the same-named target field is '
                        f'{target_field.type}. ArcGIS could coerce or lose values; correct '
                        'the source field type before continuing.'
                    )
                source_name = same_name.name
        if not source_name:
            continue
        field_map = arcpy.FieldMap()
        field_map.addInputField(dataset.staged_new, source_name)
        output_field = field_map.outputField
        output_field.name = target_field.name
        output_field.aliasName = getattr(target_field, 'aliasName', target_field.name)
        field_map.outputField = output_field
        mappings.addFieldMap(field_map)
        mapped.append(f'{source_name}->{target_field.name}')
        if role is None and target_field.type == 'String':
            extra_text_mappings.append((source_name, target_field))
    if not mapped:
        raise ValueError(f'No compatible fields could be mapped for {dataset.label}.')
    for source_name, target_field in extra_text_mappings:
        with arcpy.da.SearchCursor(dataset.staged_new, ['OID@', source_name]) as rows:
            for oid, value in rows:
                try:
                    core.validate_value_for_field(
                        value, target_field, target_field.name, allow_blank=True
                    )
                except ValueError as error:
                    raise ValueError(f'Feature {oid}: {error}') from None
    arcpy.AddMessage(f'{dataset.label} field mapping: {", ".join(mapped)}')
    return mappings, mapped


def _null_geometry_count(dataset) -> int:
    '''count null or empty features in a dataset'''

    count = 0
    with arcpy.da.SearchCursor(dataset, ['SHAPE@']) as rows:
        for (geometry,) in rows:
            if geometry is None or getattr(geometry, 'isEmpty', False):
                count += 1
    return count


def _remove_staging_root(staging_root: str) -> None:
    '''delete the temporary staging folder, waiting for ArcGIS to drop its locks

    ArcGIS releases file geodatabase locks a moment after the last tool finishes,
    and over a network share that lag is long enough that a single attempt leaves
    a hidden staging folder behind on every run.
    '''

    for attempt in range(5):
        try:
            shutil.rmtree(staging_root)
            return
        except OSError as cleanup_error:
            last_error = cleanup_error
            time.sleep(0.5 * (attempt + 1))
    arcpy.AddWarning(
        f'Could not remove temporary staging folder {staging_root}: {last_error}. '
        'It holds no release data and can be deleted by hand.'
    )


def _geometry_problems(dataset, output_table: str) -> Tuple[int, str]:
    '''count real geometry defects and describe them

    CheckGeometry also reports dataset-level conditions with a feature id of -1,
    such as the missing spatial index on a freshly copied staging shapefile.
    Those are not geometry defects and must not block a release.
    '''

    arcpy.management.CheckGeometry(dataset, output_table, 'ESRI')
    total = _count(output_table)
    if not total:
        return 0, ''
    try:
        names = {field.name.upper() for field in arcpy.ListFields(output_table)}
    except Exception:
        names = set()
    wanted = [name for name in ('FEATURE_ID', 'PROBLEM') if name in names]
    if 'FEATURE_ID' not in wanted:
        return total, ''

    defects = []
    with arcpy.da.SearchCursor(output_table, wanted) as rows:
        for row in rows:
            values = dict(zip(wanted, row))
            feature_id = values.get('FEATURE_ID')
            try:
                dataset_level = int(feature_id) < 0
            except (TypeError, ValueError):
                dataset_level = False
            if dataset_level:
                continue
            defects.append('feature %s: %s' % (
                feature_id, values.get('PROBLEM', 'unspecified problem')
            ))
    return len(defects), '; '.join(defects[:10])


def _field_properties(field) -> Dict[str, object]:
    '''describe the field properties a packaged release has to preserve'''

    properties: Dict[str, object] = {'type': field.type}
    if field.type == 'String':
        properties['length'] = getattr(field, 'length', None)
    elif field.type in _NUMERIC_FIELD_TYPES:
        properties['precision'] = getattr(field, 'precision', None)
        properties['scale'] = getattr(field, 'scale', None)
    properties['nullable'] = getattr(field, 'isNullable', None)
    return properties


def _schema_signature(dataset):
    '''record the production field definitions in their original order'''

    signature = []
    for field in arcpy.ListFields(dataset):
        if field.type in _SYSTEM_FIELD_TYPES or getattr(field, 'required', False):
            continue
        signature.append((field.name.casefold(), _field_properties(field)))
    return signature


def _schema_differences(target_schema, release_schema) -> List[str]:
    '''explain exactly how a packaged schema drifted from its production target'''

    target_names = [name for name, _ in target_schema]
    release_names = [name for name, _ in release_schema]
    differences = []

    missing = [name for name in target_names if name not in release_names]
    if missing:
        differences.append('missing field(s) ' + ', '.join(missing))
    added = [name for name in release_names if name not in target_names]
    if added:
        differences.append('unexpected field(s) ' + ', '.join(added))

    shared = [name for name in target_names if name in release_names]
    release_order = [name for name in release_names if name in shared]
    if release_order != shared:
        differences.append(
            'field order changed from %s to %s'
            % (', '.join(shared), ', '.join(release_order))
        )

    target_lookup = dict(target_schema)
    release_lookup = dict(release_schema)
    for name in shared:
        for prop, expected in target_lookup[name].items():
            actual = release_lookup[name].get(prop)
            if actual != expected:
                differences.append(
                    f'{name} {prop} changed from {expected!r} to {actual!r}'
                )
    return differences


def _stage_dataset(
    dataset: _Dataset,
    gdb: str,
    release_dir: str,
    qa_rows: List[core.QARow],
):
    '''stage, append, and validate one dataset in the release'''

    safe = f'row{dataset.index}'
    copied_new = os.path.join(gdb, f'{safe}_new_source')
    dataset.staged_new = os.path.join(gdb, f'{safe}_new_projected')

    # the historical copy and the combined output stay shapefiles: a file
    # geodatabase round trip adds Shape_Length and widens dBASE numeric fields,
    # which would publish a schema the production target does not have
    work_dir = os.path.join(os.path.dirname(gdb), 'work')
    os.makedirs(work_dir, exist_ok=True)
    dataset.staged_target = os.path.join(work_dir, f'{safe}_historical.shp')
    dataset.staged_output = os.path.join(work_dir, f'{safe}_complete.shp')

    # copy the complete target but only the selected new records into staging
    arcpy.AddMessage(f'Copying the complete historical {dataset.label} target...')
    arcpy.management.CopyFeatures(dataset.target_path, dataset.staged_target)
    arcpy.management.CopyFeatures(dataset.new_input, copied_new)
    if _count(copied_new) != dataset.new_count:
        raise ValueError(f'ArcGIS did not copy all selected new {dataset.label} features.')

    # project the new records into the target coordinate system before appending
    if _same_spatial_reference(dataset.source_sr, dataset.target_sr):
        arcpy.management.CopyFeatures(copied_new, dataset.staged_new)
    else:
        arcpy.AddMessage(
            f'Projecting new {dataset.label} features to the target coordinate system...'
        )
        kwargs = {
            'in_dataset': copied_new,
            'out_dataset': dataset.staged_new,
            'out_coor_system': dataset.target_sr,
        }
        if dataset.transformation:
            kwargs['transform_method'] = dataset.transformation
        arcpy.management.Project(**kwargs)
    if _count(dataset.staged_new) != dataset.new_count:
        raise ValueError(
            f'Projection changed the number of new {dataset.label} features.'
        )
    nulls = _null_geometry_count(dataset.staged_new)
    if nulls:
        raise ValueError(
            f'Projection produced {nulls} empty {dataset.label} geometries. '
            'Run Check Geometry on the source data and try again.'
        )
    problem_table = os.path.join(gdb, f'{safe}_new_geometry_problems')
    invalids, detail = _geometry_problems(dataset.staged_new, problem_table)
    if invalids:
        raise ValueError(
            f'ArcGIS found {invalids} geometry problem(s) in the projected new '
            f'{dataset.label} data. Repair the source geometry and run the tool again.'
            + (f' Reported: {detail}.' if detail else '')
        )

    # fill required fields, map them explicitly, and append to a copy of the target
    _prepare_role_fields(dataset)
    mappings, mapped = _build_field_mappings(dataset)
    arcpy.management.CopyFeatures(dataset.staged_target, dataset.staged_output)
    arcpy.AddMessage(f'Appending {dataset.new_count:,} new {dataset.label} features...')
    arcpy.management.Append(dataset.staged_new, dataset.staged_output, 'NO_TEST', mappings)
    final_count = _count(dataset.staged_output)
    expected = dataset.historical_count + dataset.new_count
    if final_count != expected:
        raise ValueError(
            f'{dataset.label} count check failed: expected {expected:,}, found '
            f'{final_count:,}. No release files were published.'
        )
    if _null_geometry_count(dataset.staged_output):
        raise ValueError(
            f'The complete {dataset.label} output contains empty geometry.'
        )
    problem_table = os.path.join(gdb, f'{safe}_complete_geometry_problems')
    invalids, detail = _geometry_problems(dataset.staged_output, problem_table)
    if invalids:
        raise ValueError(
            f'ArcGIS found {invalids} geometry problem(s) after combining the '
            f'{dataset.label} data. No release files were published.'
            + (f' Reported: {detail}.' if detail else '')
        )
    output_sr = _valid_spatial_reference(
        dataset.staged_output, f'Complete {dataset.label}'
    )
    if not _same_spatial_reference(output_sr, dataset.target_sr):
        raise ValueError(
            f'The complete {dataset.label} data has the wrong coordinate system.'
        )

    # create the final shapefile in staging and verify that its schema survived
    dataset.staged_release = os.path.join(release_dir, dataset.output_name)
    arcpy.management.CopyFeatures(dataset.staged_output, dataset.staged_release)
    release_count = _count(dataset.staged_release)
    release_sr = _valid_spatial_reference(
        dataset.staged_release, f'Packaged {dataset.label}'
    )
    target_schema = _schema_signature(dataset.target_path)
    release_schema = _schema_signature(dataset.staged_release)
    if release_count != expected:
        raise ValueError(
            f'Packaged {dataset.label} count check failed: expected {expected:,}, '
            f'found {release_count:,}.'
        )
    if not _same_spatial_reference(release_sr, dataset.target_sr):
        raise ValueError(f'Packaged {dataset.label} data has the wrong coordinate system.')
    if release_schema != target_schema:
        differences = _schema_differences(target_schema, release_schema)
        detail = '; '.join(differences) or 'no individual difference could be isolated'
        raise ValueError(
            f'Packaged {dataset.label} schema does not match its production '
            f'target: {detail}.'
        )
    problem_table = os.path.join(gdb, f'{safe}_release_geometry_problems')
    invalids, detail = _geometry_problems(dataset.staged_release, problem_table)
    if invalids:
        raise ValueError(
            f'Packaged {dataset.label} data contains {invalids} geometry problem(s).'
            + (f' Reported: {detail}.' if detail else '')
        )
    qa_rows.extend([
        _qa(dataset.label, 'target_input_path', 'PASS', dataset.target_path,
            'Complete historical target; layer selections were ignored.'),
        _qa(dataset.label, 'new_input_path', 'PASS',
            str(getattr(arcpy.Describe(dataset.new_input), 'catalogPath', dataset.new_input)),
            'Selections and definition queries on this layer were honored.'),
        _qa(
            dataset.label,
            'historical_count',
            'PASS',
            dataset.historical_count,
            dataset.target_path,
        ),
        _qa(dataset.label, 'new_count', 'PASS', dataset.new_count,
            'Selections and definition queries on the new layer were honored.'),
        _qa(dataset.label, 'final_count', 'PASS', final_count, f'Expected {expected}.'),
        _qa(dataset.label, 'target_spatial_reference', 'PASS',
            getattr(dataset.target_sr, 'name', ''), ''),
        _qa(dataset.label, 'geographic_transformation', 'PASS',
            dataset.transformation or 'None required', ''),
        _qa(dataset.label, 'field_mapping', 'PASS', '; '.join(mapped), ''),
        _qa(dataset.label, 'packaged_schema', 'PASS',
            '; '.join(item[0] for item in release_schema),
            'Matches the selected production target.'),
        _qa(dataset.label, 'enforced_roles', 'PASS',
            ', '.join(dataset.roles) or 'none',
            'Required roles that exist on the production target.'),
    ])
    for role, count in dataset.filled_counts.items():
        qa_rows.append(_qa(dataset.label, f'{role}_fallback_rows', 'PASS', count, ''))
        qa_rows.append(
            _qa(
                dataset.label,
                f'{role}_missing_rows',
                'PASS',
                0,
                'Validated on every selected new feature.',
            )
        )
    if 'rotation' in dataset.source_role_fields:
        rotations = []
        with arcpy.da.SearchCursor(
            dataset.staged_new, [dataset.source_role_fields['rotation']]
        ) as rows:
            rotations = [float(value) for (value,) in rows]
        qa_rows.extend([
            _qa(dataset.label, 'rotation_minimum', 'PASS', min(rotations),
                'Selected new features.'),
            _qa(dataset.label, 'rotation_maximum', 'PASS', max(rotations),
                'Selected new features.'),
        ])


def _qa(section, check, status, value, details):
    '''make one row for the release QA report'''

    return core.QARow(section, check, status, value, details)


def _write_zip(staged_shapefile: str, zip_path: str) -> List[str]:
    '''write a shapefile and its required sidecars to a ZIP'''

    base = os.path.splitext(staged_shapefile)[0]
    members = core.select_shapefile_zip_members(
        glob.glob(base + '.*'), os.path.basename(staged_shapefile)
    )
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in members:
            archive.write(path, os.path.basename(path))
    return [os.path.basename(path) for path in members]


def _write_geojson(dataset: _Dataset, shapefile: str, geojson_path: str) -> None:
    '''write the WGS 84 GeoJSON Mapbox uses and confirm its feature count'''

    arcpy.AddMessage(f'Creating formatted WGS 84 GeoJSON for {dataset.label}...')
    arcpy.conversion.FeaturesToJSON(
        shapefile,
        geojson_path,
        'FORMATTED',
        'NO_Z_VALUES',
        'NO_M_VALUES',
        'GEOJSON',
        'WGS84',
        'USE_FIELD_NAME',
    )
    if not os.path.isfile(geojson_path) or os.path.getsize(geojson_path) == 0:
        raise ValueError(f'ArcGIS did not create a valid GeoJSON file for {dataset.label}.')
    try:
        with open(geojson_path, encoding='utf-8') as stream:
            geojson = json.load(stream)
    except (OSError, ValueError) as error:
        raise ValueError(f'GeoJSON for {dataset.label} could not be read: {error}') from None
    expected = dataset.historical_count + dataset.new_count
    if (
        geojson.get('type') != 'FeatureCollection'
        or len(geojson.get('features', [])) != expected
    ):
        raise ValueError(
            f'GeoJSON validation failed for {dataset.label}: its feature count does not '
            'match the complete release.'
        )


def _package_dataset(
    dataset: _Dataset,
    release_dir: str,
    qa_rows: List[core.QARow],
) -> List[str]:
    '''collect the shapefile package and optional ZIP or GeoJSON for one dataset'''

    staged = dataset.staged_release
    sources = list(core.select_shapefile_zip_members(
        glob.glob(os.path.splitext(staged)[0] + '.*'),
        os.path.basename(staged),
    ))
    if 'zip' in dataset.package_formats:
        staged_zip = os.path.join(release_dir, os.path.basename(dataset.zip_path))
        members = _write_zip(staged, staged_zip)
        sources.append(staged_zip)
        qa_rows.append(
            _qa(
                dataset.label,
                'zip_members',
                'PASS',
                '; '.join(members),
                'Files are stored at the ZIP root.',
            )
        )
    if 'geojson' in dataset.package_formats:
        staged_geojson = os.path.join(release_dir, os.path.basename(dataset.geojson_path))
        _write_geojson(dataset, staged, staged_geojson)
        sources.append(staged_geojson)
        qa_rows.append(
            _qa(
                dataset.label,
                'geojson',
                'PASS',
                f'WGS 84 / field names / {dataset.historical_count + dataset.new_count} features',
                os.path.basename(staged_geojson),
            )
        )
    return sources


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


def execute(
    datasets,
    herd_name,
    country,
    release_date,
    output_folder,
):
    '''build and safely publish a complete GIUM release from one or more datasets'''

    rows = core.parse_dataset_rows(datasets)
    if not rows:
        raise ValueError('Add at least one dataset to the table.')
    output_folder = _validate_output_folder(output_folder)
    items = []
    for index, row in enumerate(rows, start=1):
        profile = core.layer_profile(row['layer_type'])
        items.append(
            _Dataset(
                index=index,
                profile=profile,
                label=f'Row {index} {profile.name}',
                shape_type=profile.shape_type or '',
                target_input=row['target'],
                new_input=row['new_data'],
                requested_transformation=str(row.get('transformation') or '').strip(),
                package_choice=row.get('package'),
                package_formats=core.resolve_package_formats(row.get('package'), profile),
                fallbacks={
                    'herd': herd_name,
                    'country': country,
                    'class': row.get('class'),
                    'type': row.get('type'),
                    'season': row.get('season'),
                },
            )
        )

    # all checks above and below this point are read-only
    arcpy.AddMessage('Checking inputs before creating release files...')
    for item in items:
        _validate_dataset(item)
        _assign_artifact_paths(item, output_folder, release_date)

    folded_names = [item.output_name.casefold() for item in items]
    if len(folded_names) != len(set(folded_names)):
        raise ValueError(
            'Two datasets in this run would create the same output filename. '
            'Choose a different layer type, or run them as separate releases.'
        )

    qa_csv = core.qa_report_path(output_folder, release_date)
    planned = _planned_paths(items, qa_csv)
    _assert_no_collisions(planned)

    missing_values = []
    for item in items:
        missing_values.extend(_missing_required_values(item))
    if missing_values:
        raise ValueError(
            'Required GIUM values are missing. Enter the value in the tool to fill '
            'blanks, or populate the field on the new data, then run again:\n- '
            + '\n- '.join(missing_values)
        )

    # make sure an unusual output path cannot replace an input
    source_paths = [item.target_path for item in items]
    source_paths += [
        str(
            getattr(
                arcpy.Describe(item.new_input),
                'catalogPath',
                item.new_input,
            )
        )
        for item in items
    ]
    if {_normalized_path(path) for path in planned} & {
        _normalized_path(path) for path in source_paths
    }:
        raise ValueError('A release output path cannot replace an input dataset.')

    # create temporary staging only after every dataset passes preflight
    output_folder = _validate_output_folder(output_folder, create=True)
    staging_root = tempfile.mkdtemp(prefix='.gium_arrow_staging_', dir=output_folder)
    release_dir = os.path.join(staging_root, 'release')
    os.mkdir(release_dir)
    stamp = core.release_stamp(release_date)
    qa_rows = [
        _qa('Release', 'release_date', 'PASS', stamp, ''),
        _qa(
            'Release',
            'atomic_mode',
            'PASS',
            'Enabled',
            'All datasets must pass before files are published.',
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

        for item in items:
            _stage_dataset(item, gdb, release_dir, qa_rows)

        promotion_sources = []
        for item in items:
            promotion_sources.extend(_package_dataset(item, release_dir, qa_rows))

        result = IntegrationResult(
            created=[
                path
                for item in items
                for path in (item.shapefile_path, item.zip_path, item.geojson_path)
                if path
            ],
            qa_csv=qa_csv,
        )
        staged_qa = os.path.join(release_dir, os.path.basename(qa_csv))
        for path in result.created + [result.qa_csv]:
            qa_rows.append(
                _qa(
                    'Release',
                    'artifact_path',
                    'PASS',
                    path,
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
        arcpy.AddMessage('GIUM release created successfully.')
        for path in result.created + [result.qa_csv]:
            arcpy.AddMessage(f'Created: {path}')
        arcpy.AddMessage(
            'Next step: complete visual review before publishing to Mapbox. Compare the '
            'old and new layers in ArcGIS Pro and confirm placement, attributes, and '
            'feature counts.'
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
        _remove_staging_root(staging_root)
