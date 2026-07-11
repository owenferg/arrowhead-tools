"""
Portable ArcGIS Pro Python toolbox to help automate rotating arrowheads.
Intended for the GIUM fact sheet data pipeline
Created by Owen Ferguson
"""

import arcpy
import arrow_tool

class Toolbox:
    def __init__(self):
        self.label = "Arrow Tools"
        self.alias = "arrows"
        self.tools = [RotateArrowheads]

class RotateArrowheads:
    def __init__(self):
        self.label = "Calculate Arrowhead Rotations"
        self.description = (
            "Matches arrowhead points to nearby line endpoints and writes "
            "clockwise-from-east rotation values. Uncertain matches are skipped."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        """Parameters for the arrowhead rotation tool"""
        points = arcpy.Parameter("Arrowhead points", "arrowhead_points", "GPFeatureLayer", "Required", "Input")
        points.filter.list = ["Point"]

        lines = arcpy.Parameter("Lines", "lines", "GPFeatureLayer", "Required", "Input")
        lines.filter.list = ["Polyline"]

        tolerance = arcpy.Parameter("Maximum endpoint match distance", "match_distance", "GPLinearUnit", "Required", "Input")
        tolerance.value = "25 Meters"

        field_name = arcpy.Parameter("Rotation field name", "rotation_field", "GPString", "Required", "Input")
        field_name.value = "rotation_deg"

        audit = arcpy.Parameter("Audit output table", "audit_table", "DETable", "Optional", "Output")
        derived = arcpy.Parameter("Updated arrowhead layer", "updated_arrowheads", "GPFeatureLayer", "Derived", "Output")
        derived.parameterDependencies = [points.name]
        derived.schema.clone = True

        return [points, lines, tolerance, field_name, audit, derived]

    def isLicensed(self):
        return True

    def updateMessages(self, parameters):
        # if the rotation field name is altered and is blank, set an error message
        if parameters[3].altered and not str(parameters[3].valueAsText or "").strip():
            parameters[3].setErrorMessage("Rotation field name cannot be blank")

    def execute(self, parameters, messages):
        try:
            migration_arrow_tool.execute(
                parameters[0].valueAsText, # arrowhead points  
                parameters[1].valueAsText, # lines
                parameters[2].valueAsText, # maximum endpoint match distance
                parameters[3].valueAsText, # rotation field name
                parameters[4].valueAsText, # audit output table
            )
            parameters[5].value = parameters[0].value # updated arrowhead layer
        
        except Exception as exc:
            arcpy.AddError(str(exc))
            raise
