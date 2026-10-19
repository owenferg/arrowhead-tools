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
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import arcpy  # pyright: ignore[reportMissingImports]
import arcpy_common as common
import gium_integration_core as core


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
    new_catalog_path: str = ''
    target_sr: object = None
    source_sr: object = None
    transformation: str = ''
    historical_count: int = 0
    new_count: int = 0
    original_new_count: int = 0
    multipart_count: int = 0
    missing_values: List[str] = field(default_factory=list)
    source_field_names: Tuple[str, ...] = ()
    source_stand_ins: Dict[str, str] = field(default_factory=dict)
    tool_fields: Set[str] = field(default_factory=set)
    target_fields: Dict[str, object] = field(default_factory=dict)
    source_role_fields: Dict[str, str] = field(default_factory=dict)
    target_role_fields: Dict[str, str] = field(default_factory=dict)
    filled_counts: Dict[str, int] = field(default_factory=dict)
    rotation_range: Optional[Tuple[float, float]] = None
    staged_new: str = ''
    staged_release: str = ''


@dataclass
class _FieldPlan:
    '''how the staged new fields map onto one production target'''

    mappings: object
    mapped: List[str]
    extra_text: List[Tuple[str, object]]
    unmapped_new: List[str]
    unfilled_target: List[str]


def _qa(section, check, status, value, details):
    '''make one row for the release QA report'''

    return core.QARow(section, check, status, value, details)


def _warn(qa_rows: List[core.QARow], section, check, value, message: str) -> None:
    '''record a problem that does not block the release, in the QA report and the messages'''

    arcpy.AddWarning(message)
    qa_rows.append(_qa(section, check, core.QA_WARN, value, message))


def _valid_spatial_reference(dataset, label: str):
    '''get a defined spatial reference or show a useful input error'''

    spatial_reference = getattr(arcpy.Describe(dataset), 'spatialReference', None)
    if common.is_unknown_spatial_reference(spatial_reference):
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


def _is_user_field(field) -> bool:
    '''check whether a field holds attribute values the tool can map'''

    return (
        field.type not in common.SYSTEM_FIELD_TYPES
        and not getattr(field, 'required', False)
        and getattr(field, 'editable', True)
    )


def _resolved_fields(dataset, profile, required=True, fields=None):
    '''match the pure python field rules back to ArcGIS field objects'''

    fields = list(arcpy.ListFields(dataset) if fields is None else fields)
    definitions = core.resolve_role_fields(fields, profile, required)
    by_name = {field.name.casefold(): field for field in fields}
    return {
        role: by_name[definition.name.casefold()] if definition else None
        for role, definition in definitions.items()
    }


def _resolved_source_fields(dataset, profile, fields=None):
    '''match roles on the new data, allowing the profile's stand-in fields

    returns the ArcGIS field for each role and {role: stand-in field name}
    '''

    fields = list(arcpy.ListFields(dataset) if fields is None else fields)
    definitions, stand_ins = core.resolve_source_role_fields(fields, profile)
    by_name = {field.name.casefold(): field for field in fields}
    resolved = {
        role: by_name[definition.name.casefold()] if definition else None
        for role, definition in definitions.items()
    }
    return resolved, stand_ins


def _gcs_code(spatial_reference):
    '''get a stable code or name for the geographic coordinate system'''

    gcs = getattr(spatial_reference, 'GCS', None)
    return getattr(gcs, 'factoryCode', None) or getattr(gcs, 'name', None)


def _choose_transformation(
    dataset: _Dataset,
    extent,
    qa_rows: List[core.QARow],
    allow_without_transformation: bool = False,
) -> str:
    '''validate or choose the best ArcGIS geographic transformation

    the choice is recorded in the QA report, as a warning when the tool picked
    it or when the new data has to be projected without one.
    '''

    label = dataset.label
    requested = str(dataset.requested_transformation or '').strip()
    if common.same_spatial_reference(dataset.source_sr, dataset.target_sr):
        if requested:
            arcpy.AddWarning(
                f'{label} already uses the target coordinate system; its transformation '
                'selection will be ignored.'
            )
        qa_rows.append(_qa(label, 'geographic_transformation', core.QA_PASS,
                           'None required', 'New data already uses the target coordinate system.'))
        return ''
    available = list(
        arcpy.ListTransformations(dataset.source_sr, dataset.target_sr, extent) or []
    )
    if requested:
        exact = next((item for item in available if item.lower() == requested.lower()), None)
        if not exact:
            choices = ', '.join(available[:5]) or 'none reported by ArcGIS'
            raise ValueError(
                f'The selected {label} geographic transformation {requested!r} is '
                f'not valid for these datasets. Available choices: {choices}.'
            )
        qa_rows.append(_qa(label, 'geographic_transformation', core.QA_PASS,
                           exact, 'Chosen in the tool.'))
        return exact
    if available:
        selected = available[0]
        _warn(
            qa_rows, label, 'geographic_transformation', selected,
            f'{label} will use geographic transformation {selected}, which ArcGIS Pro '
            'recommends first for this area. If a different one is correct for this '
            'data, choose it in the Geographic transformation column.',
        )
        return selected
    if _gcs_code(dataset.source_sr) != _gcs_code(dataset.target_sr):
        source_gcs = getattr(getattr(dataset.source_sr, 'GCS', None), 'name', 'unknown')
        target_gcs = getattr(getattr(dataset.target_sr, 'GCS', None), 'name', 'unknown')
        if not allow_without_transformation:
            target_name = getattr(dataset.target_sr, 'name', 'the target coordinate system')
            raise ValueError(
                f'ArcGIS has no geographic transformation from {source_gcs} to '
                f'{target_gcs} covering the area of these {label} features, so this tool '
                f'cannot project them safely. Run the ArcGIS Pro Project tool on the new '
                f'data to convert them to {target_name} first, then choose the '
                'projected layer here and run this tool again. To project them anyway, '
                'check "Project without a datum transformation when ArcGIS has none" '
                'under Options; positions may then shift.'
            )
        _warn(
            qa_rows, label, 'geographic_transformation', 'None available',
            f'ArcGIS has no geographic transformation from {source_gcs} to {target_gcs} '
            f'for the {label} features, so they will be projected without one. '
            'Positions may shift, typically by a few meters to a few hundred meters '
            'depending on the datums. Check their placement against the production '
            'layer during visual review.',
        )
        return ''
    qa_rows.append(_qa(label, 'geographic_transformation', core.QA_PASS,
                       'None required', 'Both coordinate systems use the same datum.'))
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


def _is_multipart(geometry) -> bool:
    '''check whether a geometry has more than one part, which Mapbox cannot import'''

    if geometry is None:
        return False
    try:
        return int(getattr(geometry, 'partCount', 1) or 1) > 1
    except (TypeError, ValueError):
        return False


def _scan_new_data(dataset: _Dataset, source_fields) -> None:
    '''count multipart features and blank required values in one read of the new data

    runs before any copying so a blank metadata box or a multipart feature fails
    in seconds rather than after the production data has been staged.
    '''

    resolved, dataset.source_stand_ins = _resolved_source_fields(
        dataset.new_input, dataset.profile, fields=source_fields
    )
    problems: List[str] = []
    countable = []
    for role in dataset.roles:
        # an entered value fills every blank, so the source data cannot fall short
        if not core.is_blank(dataset.fallbacks.get(role)):
            continue
        source_field = resolved.get(role)
        if source_field is None:
            aliases = ', '.join(
                dataset.profile.aliases.get(role, ())
                + dataset.profile.source_fallbacks.get(role, ())
            )
            problems.append(
                f'{dataset.label}: no {role} field (accepted names: {aliases}) and no '
                f'{role} value entered, so every feature would be missing it.'
            )
            continue
        countable.append((role, source_field.name))

    blanks = {role: 0 for role, _ in countable}
    multipart = 0
    total = 0
    with arcpy.da.SearchCursor(
        dataset.new_input, ['SHAPE@'] + [name for _, name in countable]
    ) as rows:
        for row in rows:
            total += 1
            if _is_multipart(row[0]):
                multipart += 1
            for index, (role, _) in enumerate(countable, start=1):
                if core.is_blank(row[index]):
                    blanks[role] += 1

    for role, field_name in countable:
        if blanks[role]:
            problems.append(
                f'{dataset.label}: {blanks[role]} of {total} features have a blank '
                f'{field_name} and no {role} value was entered.'
            )
    dataset.multipart_count = multipart
    dataset.missing_values = problems


def _fill_and_spellcheck_values(dataset: _Dataset, qa_rows: List[core.QARow]) -> None:
    '''fill a blank class or type from production, or spell-check a typed one

    when the cell was left blank and every production feature shares one value,
    that value fills the blanks on the new features so nobody has to guess it.
    '''

    checks = []
    for role in core.SPELLCHECK_ROLES:
        field_name = dataset.target_role_fields.get(role)
        if field_name:
            checks.append((role, field_name))
    if not checks:
        return

    # one read of the production target covers every checked field
    columns: List[List[object]] = [[] for _ in checks]
    with arcpy.da.SearchCursor(
        dataset.target_path, [field_name for _, field_name in checks]
    ) as rows:
        for row in rows:
            for index, value in enumerate(row):
                columns[index].append(value)

    for (role, field_name), column in zip(checks, columns):
        existing = core.distinct_text_values(column)
        typed = dataset.fallbacks.get(role)
        if core.is_blank(typed):
            shared = core.shared_value(existing)
            if shared is None:
                continue
            dataset.fallbacks[role] = shared
            arcpy.AddMessage(
                f'{dataset.label}: every production feature has {field_name} '
                f'{shared!r}, so it will fill blank {role} values on the new features.'
            )
            qa_rows.append(_qa(
                dataset.label, f'{role}_autofill', core.QA_PASS, shared,
                f'{role.capitalize()} was left blank and every production feature '
                'uses this value, so it filled blanks on the new features.',
            ))
            continue
        message = core.spellcheck_warning(
            dataset.label, role, str(typed).strip(), existing
        )
        if message:
            _warn(qa_rows, dataset.label, f'{role}_spellcheck', str(typed).strip(), message)


def _validate_dataset(
    dataset: _Dataset,
    qa_rows: List[core.QARow],
    split_multipart: bool = False,
    project_without_transformation: bool = False,
) -> None:
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

    if common.joined_field_names(dataset.new_input):
        raise ValueError(
            f'The {dataset.label} new data has a joined table. Remove the join before '
            'running this tool so each source field can be mapped unambiguously.'
        )

    # use the target catalog path so a target selection cannot remove history
    # keep the new input as a layer so its selection or definition query is honored
    dataset.target_path = str(
        getattr(target_description, 'catalogPath', dataset.target_input)
    )
    dataset.new_catalog_path = str(
        getattr(source_description, 'catalogPath', dataset.new_input)
    )
    if os.path.splitext(dataset.target_path)[1].casefold() != '.shp':
        raise ValueError(
            f'The {dataset.label} target must be the current production shapefile (.shp). '
            'A geodatabase layer can contain longer field names or values that a shapefile '
            'would silently shorten, so it cannot safely define the release schema.'
        )
    if common.normalized_path(dataset.target_path) == common.normalized_path(
        dataset.new_catalog_path
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

    source_fields = list(arcpy.ListFields(dataset.new_input))
    dataset.source_field_names = tuple(
        field.name for field in source_fields if _is_user_field(field)
    )
    # fill blank class/type from production first, so the blank-value check sees it
    _fill_and_spellcheck_values(dataset, qa_rows)
    _scan_new_data(dataset, source_fields)
    for role, stand_in in dataset.source_stand_ins.items():
        target_name = dataset.target_role_fields.get(role, role)
        arcpy.AddMessage(
            f'{dataset.label}: the new data has no {target_name} field, so {role} '
            f'values will come from its {stand_in} field.'
        )
        qa_rows.append(_qa(
            dataset.label, f'{role}_source_field', core.QA_PASS, stand_in,
            f'The new data has no {target_name} field, so {role} was taken from '
            f'{stand_in}.',
        ))
    if dataset.multipart_count and not split_multipart:
        raise ValueError(
            f'{dataset.label}: {dataset.multipart_count} of {dataset.new_count} new '
            'features are multipart. Mapbox cannot import multipart data. Check '
            '"Split multipart features into single parts" under Options to split them '
            'automatically, or run the Multipart To Singlepart tool on the new data, '
            'then choose that result and run this tool again.'
        )
    dataset.transformation = _choose_transformation(
        dataset,
        getattr(source_description, 'extent', None),
        qa_rows,
        project_without_transformation,
    )


def _add_field_like(dataset, field) -> str:
    '''add a staging field that matches its production target field'''

    field_type = common.ADD_FIELD_TYPES.get(field.type)
    if not field_type:
        raise ValueError(
            f'Field {field.name!r} uses unsupported ArcGIS type {field.type!r}.'
        )
    kwargs = {}
    if field.type == 'String':
        kwargs['field_length'] = field.length
    elif field.type in common.NUMERIC_FIELD_TYPES:
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
        and source_field.type in common.NUMERIC_FIELD_TYPES
        and target_field.type in common.NUMERIC_FIELD_TYPES
    )


def _resolve_role_sources(dataset: _Dataset) -> None:
    '''match every GIUM role to a staged field, adding the target field when missing'''

    resolved_source, _ = _resolved_source_fields(dataset.staged_new, dataset.profile)
    for role in dataset.managed_roles:
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
            source_field = _field_lookup(dataset.staged_new)[target_field.name.lower()]
        dataset.source_role_fields[role] = source_field.name


def _fill_role_values(dataset: _Dataset) -> None:
    '''fill only blank GIUM values in staging and validate every final value

    the rotation range for the QA report is collected in the same pass.
    '''

    roles = list(dataset.managed_roles)
    dataset.filled_counts = {role: 0 for role in roles}
    if not roles:
        return

    target_fields = {
        role: dataset.target_fields[dataset.target_role_fields[role].lower()]
        for role in roles
    }
    cursor_fields = ['OID@'] + [dataset.source_role_fields[role] for role in roles]
    low = high = None
    with arcpy.da.UpdateCursor(dataset.staged_new, cursor_fields) as rows:
        for row in rows:
            row = list(row)
            oid = row[0]
            changed = False
            for index, role in enumerate(roles, start=1):
                source_value = row[index]
                fallback = dataset.fallbacks.get(role)
                value = core.resolved_role_value(
                    role, source_value, fallback, target_fields[role],
                    required=role in dataset.roles,
                    context=f'feature {oid}',
                )
                if core.is_blank(source_value) and not core.is_blank(fallback):
                    dataset.filled_counts[role] += 1
                    changed = True
                if role == 'rotation' and not core.is_blank(value):
                    rotation = float(value)
                    low = rotation if low is None else min(low, rotation)
                    high = rotation if high is None else max(high, rotation)
                row[index] = value
            if changed:
                rows.updateRow(row)
    if low is not None:
        dataset.rotation_range = (low, high)


def _build_field_mappings(dataset: _Dataset) -> _FieldPlan:
    '''create explicit Append field mappings from new data to the target schema

    only production fields get a map, so helper fields a staging tool added,
    such as the ORIG_FID from Multipart To Singlepart, never reach the release.
    '''

    source_fields = _field_lookup(dataset.staged_new)
    role_by_target = {
        name.lower(): role for role, name in dataset.target_role_fields.items()
    }
    mappings = arcpy.FieldMappings()
    mapped = []
    mapped_sources = set()
    extra_text = []
    unfilled = []
    for target_field in dataset.target_fields.values():
        if not _is_user_field(target_field):
            continue
        role = role_by_target.get(target_field.name.lower())
        source_name = dataset.source_role_fields.get(role) if role else None
        if not source_name:
            same_name = source_fields.get(target_field.name.lower())
            if same_name and same_name.name.casefold() not in dataset.tool_fields:
                if not _compatible(same_name, target_field):
                    raise ValueError(
                        f'The {dataset.label} field {same_name.name} is type '
                        f'{same_name.type}, but the same-named target field is '
                        f'{target_field.type}. ArcGIS could coerce or lose values; correct '
                        'the source field type before continuing.'
                    )
                source_name = same_name.name
        if not source_name:
            unfilled.append(target_field.name)
            continue
        field_map = arcpy.FieldMap()
        field_map.addInputField(dataset.staged_new, source_name)
        output_field = field_map.outputField
        output_field.name = target_field.name
        output_field.aliasName = getattr(target_field, 'aliasName', target_field.name)
        field_map.outputField = output_field
        mappings.addFieldMap(field_map)
        mapped.append(f'{source_name}->{target_field.name}')
        mapped_sources.add(source_name.casefold())
        if role is None and target_field.type == 'String':
            extra_text.append((source_name, target_field))
    if not mapped:
        raise ValueError(f'No compatible fields could be mapped for {dataset.label}.')
    unmapped = [
        name for name in dataset.source_field_names
        if name.casefold() not in mapped_sources
    ]
    arcpy.AddMessage(f'{dataset.label} field mapping: {", ".join(mapped)}')
    return _FieldPlan(mappings, mapped, extra_text, unmapped, unfilled)


def _check_new_features(dataset: _Dataset, extra_text) -> None:
    '''check staged geometry and extra text values in one read of the new features

    role values are validated while they are filled, so only the other text
    fields need a length check here.
    '''

    text_fields = [source_name for source_name, _ in extra_text]
    nulls = 0
    with arcpy.da.SearchCursor(
        dataset.staged_new, ['OID@', 'SHAPE@'] + text_fields
    ) as rows:
        for row in rows:
            oid, geometry = row[0], row[1]
            if geometry is None or getattr(geometry, 'isEmpty', False):
                nulls += 1
            for value, (_, target_field) in zip(row[2:], extra_text):
                try:
                    core.validate_value_for_field(
                        value, target_field, target_field.name, allow_blank=True
                    )
                except ValueError as error:
                    raise ValueError(f'Feature {oid}: {error}') from None
    if nulls:
        raise ValueError(
            f'Projection produced {nulls} empty {dataset.label} geometries. '
            'Run Check Geometry on the source data and try again.'
        )


def _make_staging_root() -> str:
    '''create the temporary staging folder on a local disk

    the output folder is often a network share, so everything is built in the
    ArcGIS scratch folder (or the system temp folder) and only the finished
    release files are copied across.
    '''

    # the scratch folder is not always set outside a geoprocessing session
    scratch = getattr(getattr(arcpy, 'env', None), 'scratchFolder', None)
    if scratch and os.path.isdir(str(scratch)) and os.access(str(scratch), os.W_OK):
        try:
            return tempfile.mkdtemp(prefix='gium_staging_', dir=str(scratch))
        except OSError:
            pass
    return tempfile.mkdtemp(prefix='gium_staging_', dir=tempfile.gettempdir())


def _remove_staging_root(staging_root: str) -> None:
    '''delete the temporary staging folder, waiting for ArcGIS to drop its locks

    ArcGIS releases file geodatabase locks a moment after the last tool finishes,
    and that lag is long enough (especially on a network drive) that a single
    attempt leaves a staging folder behind on some runs.
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


def _geometry_defects(dataset, output_table: str) -> List[Tuple[Optional[int], str]]:
    '''run Check Geometry and list the real defects as (feature id, problem)

    CheckGeometry also reports dataset-level conditions with a feature id of -1,
    such as the missing spatial index on a freshly copied staging shapefile.
    Those are not geometry defects and must not block a release. a defect whose
    feature cannot be identified gets an id of None.
    '''

    arcpy.management.CheckGeometry(dataset, output_table, 'ESRI')
    total = _count(output_table)
    if not total:
        return []
    try:
        names = {field.name.upper() for field in arcpy.ListFields(output_table)}
    except Exception:
        names = set()
    wanted = [name for name in ('FEATURE_ID', 'PROBLEM') if name in names]
    if 'FEATURE_ID' not in wanted:
        return [(None, 'unspecified problem')] * total

    defects = []
    with arcpy.da.SearchCursor(output_table, wanted) as rows:
        for row in rows:
            values = dict(zip(wanted, row))
            try:
                feature_id = int(values.get('FEATURE_ID'))
            except (TypeError, ValueError):
                feature_id = None
            if feature_id is not None and feature_id < 0:
                continue
            defects.append((feature_id, values.get('PROBLEM') or 'unspecified problem'))
    return defects


def _defect_summary(defects) -> str:
    '''describe the first few geometry defects for a message'''

    return '; '.join(
        f'feature {feature_id}: {problem}' if feature_id is not None else problem
        for feature_id, problem in defects[:10]
    )


def _split_release_defects(dataset: _Dataset, defects):
    '''split release geometry defects into (new feature defects, historical defects)

    a shapefile numbers its records 0, 1, 2... in file order, CopyFeatures
    writes the production records first, and Append adds the new ones after
    them. so history should be FID 0 to historical_count - 1 and the new
    features the last new_count FIDs. that layout is confirmed before relying
    on it, and if it does not hold every defect is treated as new so nothing
    slips through.
    '''

    expected = dataset.historical_count + dataset.new_count
    count = 0
    ordered = True
    with arcpy.da.SearchCursor(dataset.staged_release, ['OID@']) as rows:
        for index, (fid,) in enumerate(rows):
            count += 1
            if fid != index:
                ordered = False
                break
    if not ordered or count != expected:
        arcpy.AddWarning(
            f'Could not confirm which {dataset.label} release features are new, so '
            'every geometry problem is treated as blocking.'
        )
        return list(defects), []

    new, historical = [], []
    for defect in defects:
        feature_id = defect[0]
        if feature_id is not None and feature_id < dataset.historical_count:
            historical.append(defect)
        else:
            new.append(defect)
    return new, historical


def _field_properties(field) -> Dict[str, object]:
    '''describe the field properties a packaged release has to preserve'''

    properties: Dict[str, object] = {'type': field.type}
    if field.type == 'String':
        properties['length'] = getattr(field, 'length', None)
    elif field.type in common.NUMERIC_FIELD_TYPES:
        properties['precision'] = getattr(field, 'precision', None)
        properties['scale'] = getattr(field, 'scale', None)
    properties['nullable'] = getattr(field, 'isNullable', None)
    return properties


def _schema_signature(dataset):
    '''record the production field definitions in their original order'''

    signature = []
    for field in arcpy.ListFields(dataset):
        if field.type in common.SYSTEM_FIELD_TYPES or getattr(field, 'required', False):
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


def _split_multipart(
    dataset: _Dataset,
    copied_new: str,
    gdb: str,
    qa_rows: List[core.QARow],
) -> str:
    '''split the staged copy of the new data into single parts

    only the staging copy is split, never the source. every part keeps the
    attributes of the feature it came from.
    '''

    split = os.path.join(gdb, f'row{dataset.index}_new_singlepart')
    before = {field.name.casefold() for field in arcpy.ListFields(copied_new)}
    arcpy.AddMessage(
        f'Splitting {dataset.multipart_count:,} multipart {dataset.label} '
        'features into single parts...'
    )
    arcpy.management.MultipartToSinglepart(copied_new, split)
    # remember fields the tool added, such as ORIG_FID, so they are never mapped
    dataset.tool_fields = {
        field.name.casefold() for field in arcpy.ListFields(split)
    } - before
    dataset.original_new_count = dataset.new_count
    dataset.new_count = _count(split)
    if dataset.new_count < dataset.original_new_count:
        raise ValueError(
            f'Multipart To Singlepart returned fewer {dataset.label} features than it '
            'was given. Check the new data for empty geometry and run the tool again.'
        )
    qa_rows.append(_qa(dataset.label, 'new_count_before_split', core.QA_PASS,
                       dataset.original_new_count, 'Selected new features before splitting.'))
    _warn(
        qa_rows, dataset.label, 'multipart_split', dataset.new_count,
        f'{dataset.label}: {dataset.multipart_count:,} multipart new features were '
        f'split into single parts, so {dataset.original_new_count:,} selected features '
        f'became {dataset.new_count:,}. Each part keeps its original attributes; '
        'check them during visual review.',
    )
    return split


def _verify_release(dataset: _Dataset, gdb: str, qa_rows: List[core.QARow]):
    '''check the staged release shapefile before it can be packaged'''

    label = dataset.label
    release = dataset.staged_release
    expected = dataset.historical_count + dataset.new_count
    final_count = _count(release)
    if final_count != expected:
        raise ValueError(
            f'{label} count check failed: expected {expected:,}, found '
            f'{final_count:,}. No release files were published.'
        )
    release_sr = _valid_spatial_reference(release, f'Packaged {label}')
    if not common.same_spatial_reference(release_sr, dataset.target_sr):
        raise ValueError(f'Packaged {label} data has the wrong coordinate system.')
    target_schema = _schema_signature(dataset.target_path)
    release_schema = _schema_signature(release)
    if release_schema != target_schema:
        differences = _schema_differences(target_schema, release_schema)
        detail = '; '.join(differences) or 'no individual difference could be isolated'
        raise ValueError(
            f'Packaged {label} schema does not match its production '
            f'target: {detail}.'
        )

    # problems already in production are reported but only new ones block
    problem_table = os.path.join(gdb, f'row{dataset.index}_release_geometry_problems')
    defects = _geometry_defects(release, problem_table)
    new_defects, historical_defects = (
        _split_release_defects(dataset, defects) if defects else ([], [])
    )
    if new_defects:
        detail = _defect_summary(new_defects)
        raise ValueError(
            f'ArcGIS found {len(new_defects)} geometry problem(s) on the new {label} '
            'features after combining them with the production data. No release '
            'files were published.'
            + (f' Reported: {detail}.' if detail else '')
        )
    if historical_defects:
        detail = _defect_summary(historical_defects)
        _warn(
            qa_rows, label, 'historical_geometry_problems', len(historical_defects),
            f'{label}: the production target already has {len(historical_defects)} '
            'geometry problem(s). They were copied into the release unchanged; repair '
            'them in production when you can.'
            + (f' Reported: {detail}.' if detail else ''),
        )
    return final_count, release_schema


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
    dataset.staged_release = os.path.join(release_dir, dataset.output_name)

    # copy only the selected new records into staging
    arcpy.management.CopyFeatures(dataset.new_input, copied_new)
    if _count(copied_new) != dataset.new_count:
        raise ValueError(f'ArcGIS did not copy all selected new {dataset.label} features.')
    working_new = copied_new
    if dataset.multipart_count:
        working_new = _split_multipart(dataset, copied_new, gdb, qa_rows)

    # project the new records into the target coordinate system before appending
    if common.same_spatial_reference(dataset.source_sr, dataset.target_sr):
        arcpy.management.CopyFeatures(working_new, dataset.staged_new)
    else:
        arcpy.AddMessage(
            f'Projecting new {dataset.label} features to the target coordinate system...'
        )
        kwargs = {
            'in_dataset': working_new,
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

    # map fields explicitly, then check and fill the new features before
    # anything touches a copy of the production data
    _resolve_role_sources(dataset)
    plan = _build_field_mappings(dataset)
    _check_new_features(dataset, plan.extra_text)
    _fill_role_values(dataset)
    problem_table = os.path.join(gdb, f'{safe}_new_geometry_problems')
    new_defects = _geometry_defects(dataset.staged_new, problem_table)
    if new_defects:
        detail = _defect_summary(new_defects)
        raise ValueError(
            f'ArcGIS found {len(new_defects)} geometry problem(s) in the projected new '
            f'{dataset.label} data. Repair the source geometry and run the tool again.'
            + (f' Reported: {detail}.' if detail else '')
        )
    if plan.unmapped_new:
        names = ', '.join(plan.unmapped_new)
        _warn(
            qa_rows, dataset.label, 'unmapped_new_fields', '; '.join(plan.unmapped_new),
            f'{dataset.label}: these fields on the new data are not in the production '
            f'target, so their values are left out of the release: {names}.',
        )
    if plan.unfilled_target:
        names = ', '.join(plan.unfilled_target)
        _warn(
            qa_rows, dataset.label, 'unfilled_target_fields',
            '; '.join(plan.unfilled_target),
            f'{dataset.label}: these production fields have no matching field on the '
            f'new data, so they are blank for the new features: {names}.',
        )

    # copy history once, straight to the release shapefile, and append to it.
    # the release stays a shapefile the whole way: a file geodatabase round trip
    # adds Shape_Length and widens dBASE numeric fields, which would publish a
    # schema the production target does not have
    arcpy.AddMessage(f'Copying the complete historical {dataset.label} target...')
    arcpy.management.CopyFeatures(dataset.target_path, dataset.staged_release)
    arcpy.AddMessage(f'Appending {dataset.new_count:,} new {dataset.label} features...')
    arcpy.management.Append(
        dataset.staged_new, dataset.staged_release, 'NO_TEST', plan.mappings
    )
    final_count, release_schema = _verify_release(dataset, gdb, qa_rows)

    expected = dataset.historical_count + dataset.new_count
    new_details = 'Selections and definition queries on the new layer were honored.'
    if dataset.original_new_count:
        new_details += ' Counted after splitting multipart features.'
    qa_rows.extend([
        _qa(dataset.label, 'target_input_path', core.QA_PASS, dataset.target_path,
            'Complete historical target; layer selections were ignored.'),
        _qa(dataset.label, 'new_input_path', core.QA_PASS, dataset.new_catalog_path,
            'Selections and definition queries on this layer were honored.'),
        _qa(dataset.label, 'historical_count', core.QA_PASS, dataset.historical_count,
            dataset.target_path),
        _qa(dataset.label, 'new_count', core.QA_PASS, dataset.new_count, new_details),
        _qa(dataset.label, 'final_count', core.QA_PASS, final_count, f'Expected {expected}.'),
        _qa(dataset.label, 'target_spatial_reference', core.QA_PASS,
            getattr(dataset.target_sr, 'name', ''), ''),
        _qa(dataset.label, 'field_mapping', core.QA_PASS, '; '.join(plan.mapped), ''),
        _qa(dataset.label, 'packaged_schema', core.QA_PASS,
            '; '.join(item[0] for item in release_schema),
            'Matches the selected production target.'),
        _qa(dataset.label, 'enforced_roles', core.QA_PASS,
            ', '.join(dataset.roles) or 'none',
            'Required roles that exist on the production target.'),
    ])
    for role, count in dataset.filled_counts.items():
        qa_rows.append(_qa(dataset.label, f'{role}_fallback_rows', core.QA_PASS, count, ''))
        qa_rows.append(
            _qa(
                dataset.label,
                f'{role}_missing_rows',
                core.QA_PASS,
                0,
                'Validated on every selected new feature.',
            )
        )
    if dataset.rotation_range:
        low, high = dataset.rotation_range
        qa_rows.extend([
            _qa(dataset.label, 'rotation_minimum', core.QA_PASS, low,
                'Selected new features.'),
            _qa(dataset.label, 'rotation_maximum', core.QA_PASS, high,
                'Selected new features.'),
        ])


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

    # unformatted JSON is much smaller, which keeps the Mapbox upload quick
    arcpy.AddMessage(f'Creating WGS 84 GeoJSON for {dataset.label}...')
    arcpy.conversion.FeaturesToJSON(
        shapefile,
        geojson_path,
        'NOT_FORMATTED',
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
                core.QA_PASS,
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
                core.QA_PASS,
                f'WGS 84 / field names / {dataset.historical_count + dataset.new_count} features',
                os.path.basename(staged_geojson),
            )
        )
    return sources


def _write_qa(path: str, rows: Iterable[core.QARow]) -> None:
    '''write the QA report with Excel-friendly UTF-8 encoding

    rows are grouped by section so each dataset reads as one block, even though
    its preflight warnings were collected before any dataset was staged.
    '''

    rows = list(rows)
    order: Dict[str, int] = {}
    for row in rows:
        order.setdefault(row.section, len(order))
    rows.sort(key=lambda row: order[row.section])
    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        stream.write(core.qa_csv_text(rows))


def _write_failure_report(
    output_folder,
    release_date,
    qa_rows: Sequence[core.QARow],
    error: BaseException,
) -> None:
    '''leave a QA report explaining a failed run, without ever hiding the error

    it is only written into a folder that already exists, and never beside a
    finished release for the same date.
    '''

    try:
        requested = str(output_folder or '').strip()
        if not requested:
            return
        folder = os.path.abspath(requested)
        if not os.path.isdir(folder):
            return
        if os.path.exists(core.qa_report_path(folder, release_date)):
            return
        path = core.qa_failure_report_path(folder, release_date)
        rows = list(qa_rows) + [
            _qa('Release', 'overall_result', core.QA_FAIL,
                str(error) or type(error).__name__,
                'No release files were published. Historical inputs were not changed.'),
        ]
        _write_qa(path, rows)
        arcpy.AddMessage(f'QA report for the failed run: {path}')
    except Exception as report_error:
        try:
            arcpy.AddWarning(f'Could not write the failure QA report: {report_error}')
        except Exception:
            pass


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


def _remove_old_failure_report(output_folder: str, release_date) -> None:
    '''delete a failure report left by an earlier attempt at this release'''

    path = core.qa_failure_report_path(output_folder, release_date)
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except OSError as error:
        arcpy.AddWarning(f'Could not remove the old failure report {path}: {error}')


def _build_release(
    rows,
    herd_name,
    country,
    release_date,
    output_folder,
    qa_rows: List[core.QARow],
    split_multipart: bool,
    project_without_transformation: bool,
) -> IntegrationResult:
    '''validate every dataset, stage the release, and publish it all at once'''

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
        _validate_dataset(item, qa_rows, split_multipart, project_without_transformation)
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
        missing_values.extend(item.missing_values)
    if missing_values:
        raise ValueError(
            'Required GIUM values are missing. Enter the value in the tool to fill '
            'blanks, or populate the field on the new data, then run again:\n- '
            + '\n- '.join(missing_values)
        )

    # make sure an unusual output path cannot replace an input
    source_paths = [item.target_path for item in items]
    source_paths += [item.new_catalog_path for item in items]
    if {common.normalized_path(path) for path in planned} & {
        common.normalized_path(path) for path in source_paths
    }:
        raise ValueError('A release output path cannot replace an input dataset.')

    # create temporary staging only after every dataset passes preflight
    output_folder = _validate_output_folder(output_folder, create=True)
    staging_root = _make_staging_root()
    release_dir = os.path.join(staging_root, 'release')
    os.mkdir(release_dir)
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
                    core.QA_PASS,
                    path,
                    'Final release artifact.',
                )
            )
        warnings = core.qa_warning_count(qa_rows)
        qa_rows.append(
            _qa(
                'Release',
                'overall_result',
                core.QA_PASS,
                core.qa_overall_result(qa_rows),
                'Ready for visual review. Historical inputs were not changed.',
            )
        )
        _write_qa(staged_qa, qa_rows)
        promotion_sources.append(staged_qa)
        published = _copy_release_artifacts(promotion_sources, output_folder)
        _remove_old_failure_report(output_folder, release_date)
        if warnings:
            arcpy.AddMessage(
                f'GIUM release created with {warnings} warning'
                f'{"" if warnings == 1 else "s"}. Review the WARN rows in the QA report.'
            )
        else:
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


def execute(
    datasets,
    herd_name,
    country,
    release_date,
    output_folder,
    split_multipart=False,
    project_without_transformation=False,
):
    '''build and safely publish a complete GIUM release from one or more datasets

    split_multipart and project_without_transformation turn two preflight
    errors into automatic fixes, each reported as a QA warning. when the run
    fails, a _QA_FAILED.csv report is left in an existing output folder.
    '''

    rows = core.parse_dataset_rows(datasets)
    if not rows:
        raise ValueError('Add at least one dataset to the table.')
    split_multipart = bool(split_multipart)
    project_without_transformation = bool(project_without_transformation)
    stamp = core.release_stamp(release_date)
    qa_rows = [
        _qa('Release', 'release_date', core.QA_PASS, stamp, ''),
        _qa(
            'Release',
            'atomic_mode',
            core.QA_PASS,
            'Enabled',
            'All datasets must pass before files are published.',
        ),
        _qa('Release', 'split_multipart', core.QA_PASS,
            'Yes' if split_multipart else 'No',
            'Split multipart features into single parts.'),
        _qa('Release', 'project_without_transformation', core.QA_PASS,
            'Yes' if project_without_transformation else 'No',
            'Project without a datum transformation when ArcGIS has none.'),
    ]
    try:
        return _build_release(
            rows, herd_name, country, release_date, output_folder, qa_rows,
            split_multipart, project_without_transformation,
        )
    except Exception as error:
        _write_failure_report(output_folder, release_date, qa_rows, error)
        raise
