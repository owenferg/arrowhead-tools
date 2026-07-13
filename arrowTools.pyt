"""
Portable ArcGIS Pro Python toolbox to help automate rotating arrowheads.
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
        points = arcpy.Parameter(
            displayName="Arrowhead points",
            name="arrowhead_points",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input",
        )
        points.filter.list = ["Point"]

        lines = arcpy.Parameter(
            displayName="Lines",
            name="lines",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input",
        )
        lines.filter.list = ["Polyline"]

        tolerance = arcpy.Parameter(
            displayName="Maximum endpoint match distance",
            name="match_distance",
            datatype="GPLinearUnit",
            parameterType="Required",
            direction="Input",
        )
        tolerance.value = "25 Meters"

        field_name = arcpy.Parameter(
            displayName="Rotation field name",
            name="rotation_field",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        field_name.value = "rotation_deg"

        audit = arcpy.Parameter(
            displayName="Audit output table",
            name="audit_table",
            datatype="DETable",
            parameterType="Optional",
            direction="Output",
        )
        derived = arcpy.Parameter(
            displayName="Updated arrowhead layer",
            name="updated_arrowheads",
            datatype="GPFeatureLayer",
            parameterType="Derived",
            direction="Output",
        )
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
            arrow_tool.execute(
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
