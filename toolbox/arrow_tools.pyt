'''
portable ArcGIS Pro Python toolbox to help automate rotating arrowheads
created by Owen Ferguson
'''

import importlib
import arcpy
import arrow_rotation_core
import arrow_rotation_arcpy

# reload the scripts when the toolbox is refreshed so ArcGIS does not use older cached versions
arrow_rotation_core = importlib.reload(arrow_rotation_core)
arrow_rotation_arcpy = importlib.reload(arrow_rotation_arcpy)

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
            "clockwise-from-east rotation values with a configurable degree buffer. "
            "Uncertain matches are skipped."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        '''parameters for the arrowhead rotation tool'''
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
        tolerance.value = "5 Meters"

        field_name = arcpy.Parameter(
            displayName="Rotation field name",
            name="rotation_field",
            datatype="Field",
            parameterType="Required",
            direction="Input",
        )
        field_name.parameterDependencies = [points.name]
        field_name.value = "Rotation"

        rotation_buffer = arcpy.Parameter(
            displayName="Clockwise rotation buffer (degrees)",
            name="rotation_buffer",
            datatype="GPDouble",
            parameterType="Required",
            direction="Input",
        )
        rotation_buffer.value = 3

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

        return [points, lines, tolerance, field_name, rotation_buffer, audit, derived]

    def isLicensed(self):
        return True

    def updateMessages(self, parameters):
        # if the rotation field name is altered and is blank, set an error message
        if parameters[3].altered and not str(parameters[3].valueAsText or "").strip():
            parameters[3].setErrorMessage("Rotation field name cannot be blank")

    def execute(self, parameters, messages):
        try:
            arrow_rotation_arcpy.execute(
                parameters[0].valueAsText, # arrowhead points
                parameters[1].valueAsText, # lines
                parameters[2].valueAsText, # maximum endpoint match distance
                parameters[3].valueAsText, # rotation field name
                parameters[5].valueAsText, # audit output table
                parameters[4].valueAsText, # rotation buffer
            )
            parameters[6].value = parameters[0].value # updated arrowhead layer
        
        except Exception as exc:
            arcpy.AddError(str(exc))
            raise
