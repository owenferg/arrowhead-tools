'''
arcpy adapter for creating arrowheads from line endpoints
'''

from __future__ import annotations
import math
import os
from typing import Dict, Iterator, List, Optional, Tuple
import arcpy
from arrow_rotation_core import clockwise_angle_from_east, endpoints_from_part


_NUMERIC_FIELD_TYPES = {'Double', 'Single', 'Integer', 'SmallInteger', 'BigInteger'}
_CUSTOM_FIELD_TYPES = {'SmallInteger', 'Integer', 'BigInteger', 'String'}
_SKIPPED_FIELD_TYPES = {'OID', 'Geometry', 'Blob', 'Raster', 'GlobalID'}
_ADD_FIELD_TYPES = {
    'SmallInteger': 'SHORT',
    'Integer': 'LONG',
    'BigInteger': 'BIGINTEGER',
    'Single': 'FLOAT',
    'Double': 'DOUBLE',
    'String': 'TEXT',
    'Date': 'DATE',
    'DateOnly': 'DATEONLY',
    'TimeOnly': 'TIMEONLY',
    'TimestampOffset': 'TIMESTAMPOFFSET',
    'Guid': 'GUID',
    'GUID': 'GUID',
}


def _same_spatial_reference(first, second) -> bool:
    '''check if two spatial references are the same'''

    if first.factoryCode and second.factoryCode:
        return first.factoryCode == second.factoryCode
    return first.exportToString() == second.exportToString()


def _working_spatial_reference(line_layer):
    '''get a projected spatial reference for measuring screen direction'''

    spatial_reference = arcpy.Describe(line_layer).spatialReference

    if not spatial_reference or spatial_reference.name == 'Unknown':
        raise ValueError('Lines must have a defined spatial reference')

    if spatial_reference.type == 'Geographic':
        arcpy.AddWarning(
            'Lines use a geographic coordinate system; screen direction will be '
            'calculated in WGS 1984 Web Mercator Auxiliary Sphere.'
        )
        return spatial_reference, arcpy.SpatialReference(3857)
    return spatial_reference, spatial_reference


def _projection_for_layer(layer, target_spatial_reference):
    '''get the transformation for a layer to a target spatial reference'''

    description = arcpy.Describe(layer)
    source = description.spatialReference

    if _same_spatial_reference(source, target_spatial_reference):
        return None

    transformations = arcpy.ListTransformations(
        source, target_spatial_reference, description.extent
    )
    return transformations[0] if transformations else ''


def _project_if_needed(geometry, spatial_reference, transformation=None):
    '''project a geometry to a target spatial reference if needed'''

    if _same_spatial_reference(geometry.spatialReference, spatial_reference):
        return geometry
    if transformation:
        return geometry.projectAs(spatial_reference, transformation)
    return geometry.projectAs(spatial_reference)


def _parts(geometry) -> Iterator[List[object]]:
    '''iterate over nonempty geometry parts'''

    for part in geometry:
        points = [point for point in part if point is not None]
        if points:
            yield points


def _parse_placement(value: str) -> str:
    '''validate and normalize the requested endpoint placement'''

    placement = str(value or '').strip().upper()
    if placement not in {'START', 'END', 'BOTH', 'CUSTOM'}:
        raise ValueError('Arrowhead placement must be START, END, BOTH, or CUSTOM')
    return placement


def _parse_custom_value(value) -> bool:
    '''interpret one strict Boolean-compatible custom placement value'''

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == 'true':
            return True
        if normalized == 'false':
            return False
    raise ValueError(f'{value!r} is not a supported Boolean value')


def _parse_rotation_buffer(value) -> float:
    '''validate the clockwise rotation buffer'''

    try:
        rotation_buffer = float(value)
    except (TypeError, ValueError):
        raise ValueError('Rotation buffer must be a number') from None
    if not math.isfinite(rotation_buffer):
        raise ValueError('Rotation buffer must be a finite number')
    return rotation_buffer


def _normalized_path(value) -> str:
    '''normalize a catalog path for safe comparisons'''

    return os.path.normcase(os.path.normpath(str(value))).replace('\\', '/').lower()


def _validate_output(line_layer, output: str) -> Tuple[str, str]:
    '''validate the output path without changing anything'''

    output = str(output or '').strip()
    workspace, name = os.path.split(output)

    if not workspace or not name:
        raise ValueError('Output arrowheads must include a workspace and feature class name')

    input_path = arcpy.Describe(line_layer).catalogPath
    if _normalized_path(input_path) == _normalized_path(output):
        raise ValueError('Output arrowheads cannot replace the input lines')
    return workspace, name


def _unique_field_name(preferred: str, used: set, workspace: str) -> str:
    '''make a valid field name that does not collide with another output field'''

    for number in range(10000):
        candidate = preferred if number == 0 else f'{preferred}_{number}'
        valid = arcpy.ValidateFieldName(candidate, workspace)
        if valid and valid.lower() not in used:
            used.add(valid.lower())
            return valid
    raise ValueError(f'Could not create a unique field name for {preferred}')


def _source_fields(line_layer) -> List[object]:
    '''get editable attributes that can be copied to the output'''

    fields = []
    for field in arcpy.ListFields(line_layer):
        if (
            field.type not in _SKIPPED_FIELD_TYPES
            and getattr(field, 'editable', True)
            and not getattr(field, 'required', False)
        ):
            if field.type not in _ADD_FIELD_TYPES:
                arcpy.AddWarning(f'Skipped unsupported source field {field.name} ({field.type})')
                continue
            fields.append(field)
    return fields


def _validate_line_layer(line_layer) -> None:
    '''make sure the input can be traced and copied safely'''

    description = arcpy.Describe(line_layer)
    if getattr(description, 'shapeType', None) != 'Polyline':
        raise ValueError('Input lines must be a polyline layer')
    if not getattr(description, 'hasOID', True):
        raise ValueError('Input lines must have an Object ID field')
    if any('.' in field.name for field in arcpy.ListFields(line_layer)):
        raise ValueError('Joined line layers are not supported; remove the join and run the tool again')


def _validate_custom_field(line_layer, placement: str, custom_field) -> Optional[str]:
    '''validate the custom placement field and all selected/input values'''

    if placement != 'CUSTOM':
        return None

    requested = str(custom_field or '').strip()
    if not requested:
        raise ValueError('Custom placement field is required for CUSTOM placement')

    field = next(
        (
            field
            for field in arcpy.ListFields(line_layer)
            if field.name.lower() == requested.lower()
        ),
        None,
    )
    if field is None:
        raise ValueError(f'Custom placement field {requested!r} was not found in the input lines')
    if field.type not in _CUSTOM_FIELD_TYPES:
        raise ValueError(
            f'Custom placement field {field.name!r} must be a Short, Long, Big Integer, '
            'or Text field'
        )

    with arcpy.da.SearchCursor(line_layer, ['OID@', field.name]) as rows:
        for source_oid, value in rows:
            try:
                _parse_custom_value(value)
            except ValueError:
                raise ValueError(
                    f'Custom placement field {field.name!r} has invalid Boolean value '
                    f'{value!r} for source Object ID {source_oid}'
                ) from None
    return field.name


def _validate_rotation_field(line_layer, output: str, rotation_field: str) -> str:
    '''validate the requested field before an existing output is deleted'''

    workspace, _ = _validate_output(line_layer, output)
    requested = str(rotation_field or '').strip()

    if not requested:
        raise ValueError('Rotation field name cannot be blank')
    valid = arcpy.ValidateFieldName(requested, workspace)
    if valid.lower() != requested.lower():
        raise ValueError('Rotation field name is not valid for the output workspace')

    matching_input_field = next(
        (field for field in arcpy.ListFields(line_layer) if field.name.lower() == valid.lower()),
        None,
    )
    if matching_input_field and (
        matching_input_field.type in _SKIPPED_FIELD_TYPES
        or not getattr(matching_input_field, 'editable', True)
        or getattr(matching_input_field, 'required', False)
    ):
        raise ValueError('Rotation field name cannot use a system or noneditable field')

    source_rotation = next(
        (field for field in _source_fields(line_layer) if field.name.lower() == valid.lower()),
        None,
    )
    if source_rotation and source_rotation.type not in _NUMERIC_FIELD_TYPES:
        raise ValueError('Existing rotation field must be numeric')
    return valid


def _add_source_field(output, output_name: str, field) -> None:
    '''add one source attribute to the output schema'''

    kwargs = {}
    if field.type == 'String' and getattr(field, 'length', None):
        kwargs['field_length'] = field.length
    if field.type in _NUMERIC_FIELD_TYPES:
        if getattr(field, 'precision', None):
            kwargs['field_precision'] = field.precision
        if getattr(field, 'scale', None):
            kwargs['field_scale'] = field.scale
    alias = getattr(field, 'aliasName', None)
    if alias:
        kwargs['field_alias'] = alias
    arcpy.management.AddField(output, output_name, _ADD_FIELD_TYPES[field.type], **kwargs)


def _create_schema(line_layer, output: str, rotation_field: str):
    '''create the point feature class and return its field mapping'''

    description = arcpy.Describe(line_layer)
    workspace, name = _validate_output(line_layer, output)
    valid_rotation = _validate_rotation_field(line_layer, output, rotation_field)

    source_fields = _source_fields(line_layer)
    source_rotation = next(
        (field for field in source_fields if field.name.lower() == valid_rotation.lower()),
        None,
    )
    arcpy.management.CreateFeatureclass(
        workspace,
        name,
        'POINT',
        has_m='ENABLED' if getattr(description, 'hasM', False) else 'DISABLED',
        has_z='ENABLED' if getattr(description, 'hasZ', False) else 'DISABLED',
        spatial_reference=description.spatialReference,
    )

    # create safe output names in source-field order so copied attributes stay familiar
    used = {
        field.name.lower()
        for field in arcpy.ListFields(output)
    }
    field_map: List[Tuple[object, str]] = []
    for field in source_fields:
        output_name = _unique_field_name(field.name, used, workspace)
        _add_source_field(output, output_name, field)
        field_map.append((field, output_name))
        if output_name.lower() != field.name.lower():
            arcpy.AddMessage(f'Copied {field.name} to output field {output_name}')

    rotation_output = next(
        (output_name for field, output_name in field_map if field is source_rotation),
        None,
    )
    if rotation_output is None:
        rotation_output = _unique_field_name(valid_rotation, used, workspace)
        arcpy.management.AddField(output, rotation_output, 'DOUBLE')

    oid_field = _unique_field_name('SOURCE_OID', used, workspace)
    part_field = _unique_field_name('SOURCE_PART', used, workspace)
    endpoint_field = _unique_field_name('ENDPOINT', used, workspace)
    oid_type = 'BIGINTEGER' if getattr(description, 'hasOID64', False) else 'LONG'
    arcpy.management.AddField(output, oid_field, oid_type)
    arcpy.management.AddField(output, part_field, 'LONG')
    arcpy.management.AddField(output, endpoint_field, 'TEXT', field_length=5)

    if oid_field != 'SOURCE_OID':
        arcpy.AddMessage(f'Source ID metadata will be stored in {oid_field}')
    if part_field != 'SOURCE_PART':
        arcpy.AddMessage(f'Source part metadata will be stored in {part_field}')
    if endpoint_field != 'ENDPOINT':
        arcpy.AddMessage(f'Endpoint metadata will be stored in {endpoint_field}')

    return field_map, rotation_output, oid_field, part_field, endpoint_field


def _point_geometry(point, spatial_reference, has_z: bool, has_m: bool):
    '''create a point geometry while keeping z and m support'''

    return arcpy.PointGeometry(point, spatial_reference, has_z, has_m)


def execute(
    line_layer,
    placement: str,
    rotation_field: str,
    rotation_buffer,
    output: str,
    custom_field=None,
) -> None:
    '''create outward-facing arrowhead points from selected/input lines'''

    placement = _parse_placement(placement)
    rotation_buffer = _parse_rotation_buffer(rotation_buffer)
    description = arcpy.Describe(line_layer)

    _validate_line_layer(line_layer)
    custom_field = _validate_custom_field(line_layer, placement, custom_field)

    source_spatial_reference, working_spatial_reference = _working_spatial_reference(line_layer)
    transformation = _projection_for_layer(line_layer, working_spatial_reference)
    _validate_output(line_layer, output)
    _validate_rotation_field(line_layer, output, rotation_field)

    if (
        not getattr(working_spatial_reference, 'metersPerUnit', None)
        or working_spatial_reference.metersPerUnit <= 0
    ):
        raise ValueError('Working spatial reference does not define valid linear units')

    if arcpy.Exists(output):
        if not getattr(getattr(arcpy, 'env', None), 'overwriteOutput', False):
            raise ValueError('Output arrowheads already exist and overwrite output is disabled')
        arcpy.management.Delete(output)

    output_started = False
    try:
        output_started = True
        field_map, rotation_output, oid_field, part_field, endpoint_field = _create_schema(
            line_layer, output, rotation_field
        )

        source_names = [field.name for field, unused in field_map]
        output_names = [output_name for unused, output_name in field_map]
        insert_names = [
            'SHAPE@', *output_names, rotation_output,
            oid_field, part_field, endpoint_field,
        ]
        # a copied rotation field is written only once, with the calculated value
        if rotation_output in output_names:
            insert_names.remove(rotation_output)

        has_z = bool(getattr(description, 'hasZ', False))
        has_m = bool(getattr(description, 'hasM', False))
        tangent_distance = 1.0 / working_spatial_reference.metersPerUnit
        created_count = 0
        closed_count = 0
        degenerate_count = 0

        read_names = ['OID@', 'SHAPE@'] + source_names
        custom_value_index = None
        if custom_field:
            custom_value_index = next(
                (
                    index + 2
                    for index, name in enumerate(source_names)
                    if name.lower() == custom_field.lower()
                ),
                None,
            )
            if custom_value_index is None:
                custom_value_index = len(read_names)
                read_names.append(custom_field)

        with arcpy.da.SearchCursor(line_layer, read_names) as source_rows, arcpy.da.InsertCursor(
            output, insert_names
        ) as output_rows:
            for source_row in source_rows:
                source_oid, geometry = source_row[:2]
                attributes = dict(zip(output_names, source_row[2:2 + len(source_names)]))
                row_placement = placement
                if custom_value_index is not None:
                    row_placement = (
                        'BOTH' if _parse_custom_value(source_row[custom_value_index]) else 'END'
                    )

                if geometry is None or geometry.pointCount == 0:
                    degenerate_count += 1
                    continue

                working_geometry = _project_if_needed(
                    geometry, working_spatial_reference, transformation
                )
                if getattr(working_geometry, 'hasCurves', False):
                    working_geometry = working_geometry.densify(
                        'DISTANCE', tangent_distance, 0.0
                    )

                original_parts = list(_parts(geometry))
                working_parts = list(_parts(working_geometry))

                for part_index, working_points in enumerate(working_parts):
                    xy = [(point.X, point.Y) for point in working_points]
                    if len(xy) < 2 or len(set(xy)) < 2:
                        degenerate_count += 1
                        continue
                    if xy[0] == xy[-1]:
                        closed_count += 1
                        continue
                    if part_index >= len(original_parts):
                        raise ValueError('Could not match projected line parts to source line parts')

                    endpoints = endpoints_from_part(source_oid, part_index, xy)
                    for endpoint in endpoints:
                        if row_placement != 'BOTH' and endpoint.endpoint != row_placement:
                            continue

                        point = (
                            original_parts[part_index][0]
                            if endpoint.endpoint == 'START'
                            else original_parts[part_index][-1]
                        )
                        rotation = (
                            clockwise_angle_from_east(endpoint.dx, endpoint.dy)
                            + rotation_buffer
                        ) % 360.0
                        values: Dict[str, object] = dict(attributes)
                        values[rotation_output] = rotation
                        values[oid_field] = source_oid
                        values[part_field] = part_index
                        values[endpoint_field] = endpoint.endpoint
                        shape = _point_geometry(
                            point, source_spatial_reference, has_z, has_m
                        )
                        output_rows.insertRow(
                            [shape] + [values[field] for field in insert_names[1:]]
                        )
                        created_count += 1

        if not created_count:
            raise ValueError('No usable line endpoints were found')

        arcpy.AddMessage(
            f'Created {created_count:,} {placement.lower()} arrowhead point'
            f'{"s" if created_count != 1 else ""} in {output}'
        )
        arcpy.AddMessage(f'Applied a {rotation_buffer:+g} degree rotation buffer')
        if closed_count:
            arcpy.AddWarning(f'Skipped {closed_count:,} closed line part(s)')
        if degenerate_count:
            arcpy.AddWarning(f'Skipped {degenerate_count:,} empty or degenerate line part(s)')

    except Exception:
        if output_started and arcpy.Exists(output):
            try:
                arcpy.management.Delete(output)
            except Exception as cleanup_error:
                arcpy.AddWarning(
                    f'Could not remove partial output {output}: {cleanup_error}'
                )
        raise
