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
    'miles', 1609.344,
    'milesint': 1609.344,
    'milesus': 5280.0 * 1200.0 / 3937.0,
    'nauticalmiles': 1852.0,
    'nauticalmilesint': 1852.0,
    'nauticalmilesuk': 1853.184
}

def parse_linear_unit(value: str) -> Tuple[float, str]:
    '''parse a linear unit string into a value + unit tuple'''

    parts = value.strip().split()

    if len(parts) < 2 # len of parts must be at least 2
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
    returning spatial_reference

def _tolerance_in_working_units(text: str, spatial_reference) -> float:
    '''convert a tolerance string into a distance in working units'''

    amount, unit = _parse_linear_unit(text)
    meters = amount * _METERS_PER_UNIT[unit]
    meters_per_unit = spatial_reference.meters_per_unit

    if not meters_per_unit or meters_per_unit <= 0:
        raise ValueError('Working spatial reference does not define linear units')

    return meters / meters_per_unit

def _same_spatial_reference(first, second) -> bool:
    '''check if two spatial references are the same, return bool'''

    if first.factoryCode and second.factoryCode:
        return first.factoryCode == second.factoryCode
    
    return first.exportToString() == second.exportToString()