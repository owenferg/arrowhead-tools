"""
ArcPy adapter for the arrowhead rotation tool
"""

from __future__ import annotations
import os
from typing import Dict, Iterator, List, Optional, Tuple
import arcpy
from arrow_rotation_core import Endpoint, EndpointIndex, Match, endpoints_from_part

_METERS_PER_UNIT = {
    "millimeters": 0.001,
    'centimeters': 0.01,
    'decimeters': 0.1,
    'meters': 1.0,
    'kilometers': 1000.0,
    'inches': 0.0254,
    'feet': 0.3048,
    'feetint': 0.3048,
    'feetus': 1200.0 / 3937.0,
    'yards': 0.9144,
    'miles': 1609.344,
    'milesint': 1609.344,
    'milesus': 5280.0 * 1200.0 / 3937.0,
    'nauticalmiles': 1852.0,
    'nauticalmilesint': 1852.0,
    'nauticalmilesuk': 1853.184
}

def _parse_linear_unit(value: str) -> Tuple[float, str]:
    '''parse a linear unit string into a value + unit tuple'''

    parts = value.strip().split()

    if len(parts) < 2:  # len of parts must be at least 2
        raise ValueError('Match distance must include a value and unit, such as "25 meters"')

    amount = float(parts[0])
    unit = "".join(parts[1:]).lower().replace("_", "")

    if amount <= 0 or unit not in _METERS_PER_UNIT:
        raise ValueError("Match distance must be positive and use a supported linear unit")

    return amount, unit

def _working_spatial_reference(point_layer) -> arcpy.SpatialReference:
    '''get the spatial reference for the point layer, converting to meters if necessary'''

    spatial_reference = arcpy.Describe(point_layer).spatialReference

    if not spatial_reference or spatial_reference.name == "Unknown":
        raise ValueError('Arrowhead points must have a defined spatial reference')

    if spatial_reference.type == "Geographic":
        arcpy.AddWarning(
            'Arrowheads use a geographic coordinate system; calculations will use '
            'WGS 1984 Web Mercator Auxiliary Sphere for distance and screen direction.'
        )
        return arcpy.SpatialReference(3857)
    return spatial_reference

def _tolerance_in_working_units(text: str, spatial_reference) -> float:
    '''convert a tolerance string into a distance in working units'''

    amount, unit = _parse_linear_unit(text)
    meters = amount * _METERS_PER_UNIT[unit]
    meters_per_unit = spatial_reference.metersPerUnit

    if not meters_per_unit or meters_per_unit <= 0:
        raise ValueError('Working spatial reference does not define linear units')

    return meters / meters_per_unit

def _same_spatial_reference(first, second) -> bool:
    '''check if two spatial references are the same, return bool'''

    if first.factoryCode and second.factoryCode:
        return first.factoryCode == second.factoryCode
    
    return first.exportToString() == second.exportToString()

def _projection_for_layer(layer, target_spatial_reference):
    '''get the projection for a layer to a target spatial reference'''

    description = arcpy.Describe(layer) # get layer desc from arcpy
    source = description.spatialReference 

    if not source or source.name == 'Unknown':
        # if not a defined spatial ref
        raise ValueError('All input layers have a defined spatial reference')
    
    if _same_spatial_reference(source, target_spatial_reference):
        return None # if same spatial ref, no projection needed

    # list all transformations between source and target
    transformations = arcpy.ListTransformations(
        source, target_spatial_reference, description.extent
    )

    # return first transformation if it exists 
    return transformations[0] if transformations else ''

def _project_if_needed(geometry, spatial_reference, transformation=None):
    '''project a geometry to a target spatial reference if needed'''

    source = geometry.spatialReference

    if not source or source.name == 'Unknown':
        raise ValueError('All input geometries must have a defined spatial reference')

    if _same_spatial_reference(source, spatial_reference):
        # if same spatial ref, no projection needed
        return geometry

    if transformation:
        # if transformation is provided, use it
        return geometry.projectAs(spatial_reference, transformation)

    return geometry.projectAs(spatial_reference)

def _parts(geometry) -> Iterator[List[Tuple[float, float]]]:
    '''iterate over the parts of a geometry and return the points'''

    for part in geometry:
        points = [(point.X, point.Y) for point in part if point is not None]
        # yield points if they are not None
        if points:
            yield points

def _read_endpoints(line_layer, spatial_reference, tangent_distance: float) -> List[Endpoint]:
    '''read the endpoints from a line layer'''

    endpoints: List[Endpoint] = [] 
    # get the transformation for the line layer
    transformation = _projection_for_layer(line_layer, spatial_reference)

    with arcpy.da.SearchCursor(line_layer, ["OID@", "SHAPE@"]) as rows:
        # iter over each row in cursor, get the oid and geometry
        for line_oid, geometry in rows:
            if geometry is None or geometry.pointCount == 0:
                continue

            # project the geometry if needed
            geometry = _project_if_needed(geometry, spatial_reference, transformation)

            if getattr(geometry, 'hasCurves', False):
                # densify the geometry if it has curves
                geometry = geometry.densify('DISTANCE', tangent_distance, 0.0)
            
            for part_index, points in enumerate(_parts(geometry)):
                endpoints.extend(endpoints_from_part(line_oid, part_index, points))

    return endpoints

def _calculate_matches(point_layer, spatial_reference, index: EndpointIndex) -> Dict[int, Match]:
    '''calculate the matches for a point layer'''

    matches: Dict[int, Match] = {}
    transformation = _projection_for_layer(point_layer, spatial_reference)

    # iter over each row in cursor, get the oid and geometry
    with arcpy.da.SearchCursor(point_layer, ["OID@", "SHAPE@"]) as rows:
        for point_oid, geometry in rows:
            if geometry is None or geometry.pointCount == 0:
                # if the geometry is empty, add a match for the empty geometry
                matches[point_oid] = Match("EMPTY_GEOMETRY")
                continue

            geometry = _project_if_needed(geometry, spatial_reference, transformation)
            point = geometry.firstPoint # get the first point of the geometry

            # add the match to the matches dictionary
            matches[point_oid] = index.match(point.X, point.Y)
    return matches

def _ensure_rotation_field(point_layer, requested_name: str) -> str:
    '''ensure a rotation field exists and is numeric and editable'''

    # get the dataset path and validate the field name
    dataset = arcpy.Describe(point_layer).catalogPath
    valid_name = arcpy.ValidateFieldName(requested_name, os.path.dirname(dataset))

    if valid_name.lower() != requested_name.lower():
        # if the field name is not valid, raise an error
        raise ValueError("Rotation field name is not valid for the arrowhead dataset")

    # fields is a dictionary of the fields in the point layer
    fields = {field.name.lower(): field for field in arcpy.ListFields(point_layer)}
    existing = fields.get(valid_name.lower()) # get the existing field

    if existing: # if the field exists
        if existing.type not in {'Double', 'Single', 'Integer', 'SmallInteger'}: 
            # if the field is not numeric, raise an error
            raise ValueError('Existing rotation field must be numeric')
        
        if not existing.editable:
            # if the field is not editable, raise an error
            raise ValueError('Existing rotation field is not editable')

        return existing.name

    # if the field does not exist, check if a schema lock is held
    if not arcpy.TestSchemaLock(dataset):
        raise ValueError('Rotation field cannot be added because another process holds a schema lock')

    # add the field to the point layer
    arcpy.management.AddField(point_layer, valid_name, "DOUBLE")

    return valid_name # return the valid field name

def _validate_editable_layer(point_layer) -> None:
    '''validate the point layer is editable'''

    description = arcpy.Describe(point_layer)

    # check if the layer has an Object ID field
    if not getattr(description, 'hasOID', True):
        raise ValueError('Arrowhead points must have an Object ID field')

    # check if the layer has any qualified fields
    qualified_fields = [field.name for field in arcpy.ListFields(point_layer) if "." in field.name]

    if qualified_fields:
        # if the layer has qualified fields, raise an error
        raise ValueError('Joined arrowhead layers are not supported; remove the join and run the tool again')

    return None # return None if the layer is valid

def _write_rotations(point_layer, field_name: str, matches: Dict[int, Match]) -> int:
    '''write the rotations to the point layer'''

    updated = 0 # init # of updated rows

    with arcpy.da.UpdateCursor(point_layer, ["OID@", field_name]) as rows:
        # iter over each row in cursor, get the oid and current rotation
        for point_oid, current_rotation in rows:
            match = matches[point_oid]

            if match.status == 'MATCHED':  
                # if the match is matched, update the row
                rows.updateRow((point_oid, match.rotation))
                updated += 1

    return updated # return the number of updated rows

def _write_audit_table(output_table: str, matches: Dict[int, Match]) -> None:
    '''write the matches to the audit table'''

    if arcpy.Exists(output_table):
        arcpy.management.Delete(output_table)

    workspace, name = os.path.split(output_table)
    arcpy.management.CreateTable(workspace, name)
    arcpy.management.AddField(output_table, "POINT_OID", "LONG")
    arcpy.management.AddField(output_table, "STATUS", "TEXT", field_length=32)
    arcpy.management.AddField(output_table, "ROTATION", "DOUBLE")

    with arcpy.da.InsertCursor(output_table, ["POINT_OID", "STATUS", "ROTATION"]) as rows:
        for point_oid, match in matches.items():
            rows.insertRow((point_oid, match.status, match.rotation))

def execute(point_layer, line_layer, tolerance_text: str, field_name: str, audit_table: Optional[str]) -> None:
    '''calculate and persist rotations for all selected/input arrowhead points'''

    # validate the point layer is editable
    _validate_editable_layer(point_layer)

    # get the spatial reference for the point layer
    spatial_reference = _working_spatial_reference(point_layer)
    tolerance = _tolerance_in_working_units(tolerance_text, spatial_reference)
    # get the one meter value for the spatial reference
    one_meter = 1.0 / spatial_reference.metersPerUnit
    # get the tangent distance for the line layer
    tangent_distance = max(min(tolerance / 10.0, one_meter), 1e-9)
    # read the endpoints from the line layer
    endpoints = _read_endpoints(line_layer, spatial_reference, tangent_distance)

    if not endpoints:
        # if no endpoints were found, raise an error
        raise ValueError('No usable line endpoints were found')
    
    arcpy.AddMessage(f'Indexed {len(endpoints):,} usable line endpoints')

    # calculate the matches for the point layer
    matches = _calculate_matches(point_layer, spatial_reference, EndpointIndex(endpoints, tolerance))
    # ensure the rotation field exists and is numeric and editable
    rotation_field = _ensure_rotation_field(point_layer, field_name)

    # if audit table is provided, write the matches to the audit table
    if audit_table:
        _write_audit_table(audit_table, matches)

    # write the rotations to the point layer
    updated = _write_rotations(point_layer, rotation_field, matches)

    # count the number of matches by status
    counts: Dict[str, int] = {}

    for match in matches.values():
        # count the number of matches by status
        counts[match.status] = counts.get(match.status, 0) + 1
    
    arcpy.AddMessage(f'Updated {updated:,} arrowhead rotations in {rotation_field}')

    for status, count in sorted(counts.items()):
        # format the message for the status
        message = f'{status}: {count:,}'
        arcpy.AddWarning(message) if status != 'MATCHED' else arcpy.AddMessage(message)
