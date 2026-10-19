'''
shared helpers used by every tool in the toolbox

arcpy is imported inside the functions that need it instead of at the top of
the file, so the tests can swap in a fake arcpy without reloading this module.
'''

from __future__ import annotations

import math
import os
from typing import List


# field types that are never copied, mapped, or written by the tools
SYSTEM_FIELD_TYPES = frozenset({'OID', 'Geometry', 'Blob', 'Raster', 'GlobalID'})

INTEGER_FIELD_TYPES = frozenset({'SmallInteger', 'Integer', 'BigInteger'})
NUMERIC_FIELD_TYPES = INTEGER_FIELD_TYPES | {'Single', 'Double'}

# ListFields type names mapped to the keywords AddField expects
ADD_FIELD_TYPES = {
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


def same_spatial_reference(first, second) -> bool:
    '''check if two spatial references are the same'''

    if not first or not second:
        return False
    first_code = getattr(first, 'factoryCode', 0)
    second_code = getattr(second, 'factoryCode', 0)
    if first_code and second_code:
        return first_code == second_code
    return first.exportToString() == second.exportToString()


def is_unknown_spatial_reference(spatial_reference) -> bool:
    '''check for a missing or undefined coordinate system'''

    return not spatial_reference or getattr(spatial_reference, 'name', 'Unknown') == 'Unknown'


def normalized_path(value) -> str:
    '''normalize a catalog path so inputs and outputs can be compared safely'''

    return os.path.normcase(os.path.abspath(str(value))).replace('\\', '/').lower()


def parse_rotation_buffer(value) -> float:
    '''validate the clockwise rotation buffer'''

    try:
        rotation_buffer = float(value)
    except (TypeError, ValueError):
        raise ValueError('Rotation buffer must be a number') from None
    if not math.isfinite(rotation_buffer):
        raise ValueError('Rotation buffer must be a finite number')
    return rotation_buffer


def joined_field_names(dataset) -> List[str]:
    '''list qualified field names, which only show up when a layer has a join'''

    import arcpy  # pyright: ignore[reportMissingImports]

    return [field.name for field in arcpy.ListFields(dataset) if '.' in field.name]


def projection_for_layer(layer, target_spatial_reference):
    '''get the transformation for a layer to a target spatial reference

    returns None when no projection is needed, and an empty string when the
    layer has to be projected but ArcGIS has no transformation to offer.
    '''

    import arcpy  # pyright: ignore[reportMissingImports]

    description = arcpy.Describe(layer)
    source = description.spatialReference
    if is_unknown_spatial_reference(source):
        raise ValueError('All input layers must have a defined spatial reference')
    if same_spatial_reference(source, target_spatial_reference):
        return None

    transformations = arcpy.ListTransformations(
        source, target_spatial_reference, description.extent
    )
    return transformations[0] if transformations else ''


def project_if_needed(geometry, spatial_reference, transformation=None):
    '''project a geometry to a target spatial reference if needed'''

    source = geometry.spatialReference
    if is_unknown_spatial_reference(source):
        raise ValueError('All input geometries must have a defined spatial reference')
    if same_spatial_reference(source, spatial_reference):
        return geometry
    if transformation:
        return geometry.projectAs(spatial_reference, transformation)
    return geometry.projectAs(spatial_reference)
