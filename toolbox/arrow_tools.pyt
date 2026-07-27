'''
portable ArcGIS Pro Python toolbox to help automate creating and rotating arrowheads
created by Owen Ferguson
'''

import importlib
import os
import arcpy
import arrow_rotation_core
import arrow_creation_arcpy
import arrow_rotation_arcpy

# reload the scripts when the toolbox is refreshed so ArcGIS does not use older cached versions
arrow_rotation_core = importlib.reload(arrow_rotation_core)
arrow_creation_arcpy = importlib.reload(arrow_creation_arcpy)
arrow_rotation_arcpy = importlib.reload(arrow_rotation_arcpy)


def _input_name_and_workspace(lines):
    '''find a useful output name and workspace from the selected line layer'''
    description = arcpy.Describe(lines)
    catalog_path = getattr(description, "catalogPath", None) or str(lines)
    base_name = getattr(description, "baseName", None)
    input_name = base_name or os.path.basename(catalog_path.rstrip("/\\"))
    if input_name.lower().endswith(".shp"):
        input_name = input_name[:-4]
    workspace = getattr(description, "path", None) or os.path.dirname(catalog_path)
    return input_name, workspace


def _workspace_is_writable(workspace):
    '''check local workspaces without trying to create anything during validation'''
    workspace_text = str(workspace or "")
    if not workspace_text or "://" in workspace_text or workspace_text.lower().endswith(".sde"):
        return False

    try:
        if not arcpy.Exists(workspace):
            return False
        workspace_type = getattr(arcpy.Describe(workspace), "workspaceType", None)
        if str(workspace_type or "").lower() == "remotedatabase":
            return False
    except Exception:
        return False

    path_to_check = os.path.abspath(workspace_text)
    while path_to_check and not os.path.exists(path_to_check):
        parent = os.path.dirname(path_to_check)
        if parent == path_to_check:
            return False
        path_to_check = parent
    return bool(path_to_check and os.access(path_to_check, os.W_OK))


def _workspace_needs_shapefile_extension(workspace):
    '''folder workspaces store feature classes as shapefiles'''
    try:
        return str(arcpy.Describe(workspace).dataType).lower() == "folder"
    except Exception:
        workspace_text = str(workspace).lower()
        return ".gdb" not in workspace_text and not workspace_text.endswith(".sde")


def _default_output_path(lines):
    '''build the default output path while leaving final validation to ArcGIS Pro'''
    input_name, input_workspace = _input_name_and_workspace(lines)

    workspace = input_workspace if _workspace_is_writable(input_workspace) else None
    if not workspace:
        try:
            workspace = arcpy.mp.ArcGISProject("CURRENT").defaultGeodatabase
        except Exception:
            workspace = None
    if not workspace:
        workspace = arcpy.env.scratchGDB

    output_name = arcpy.ValidateTableName(f"{input_name}_Arrowheads", workspace)
    if _workspace_needs_shapefile_extension(workspace) and not output_name.lower().endswith(".shp"):
        output_name += ".shp"
    return os.path.join(workspace, output_name)

class Toolbox:
    def __init__(self):
        self.label = "Arrow Tools"
        self.alias = "arrows"
        self.tools = [CreateArrowheadsFromLineEndpoints, RotateArrowheads]


class CreateArrowheadsFromLineEndpoints:
    def __init__(self):
        self.label = "Create Arrowheads from Line Endpoints"
        self.description = (
            "Creates new arrowhead points from line starts, ends, or both and writes "
            "clockwise-from-east rotation values facing away from each line. No existing "
            "arrowhead point layer is needed."
        )
        self.canRunInBackground = False
        self._generated_output_default = None

    def getParameterInfo(self):
        '''parameters for creating arrowheads from line endpoints'''
        lines = arcpy.Parameter(
            displayName="Lines",
            name="lines",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input",
        )
        lines.filter.list = ["Polyline"]

        placement = arcpy.Parameter(
            displayName="Arrowhead placement",
            name="placement",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        placement.filter.type = "ValueList"
        placement.filter.list = ["START", "END", "BOTH"]
        placement.value = "END"

        field_name = arcpy.Parameter(
            displayName="Rotation field name",
            name="rotation_field",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        field_name.value = "Rotation"

        rotation_buffer = arcpy.Parameter(
            displayName="Clockwise rotation buffer (degrees)",
            name="rotation_buffer",
            datatype="GPDouble",
            parameterType="Required",
            direction="Input",
        )
        rotation_buffer.value = 3

        output = arcpy.Parameter(
            displayName="Output arrowheads",
            name="output_arrowheads",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Output",
        )
        output.schema.geometryType = "Point"

        return [lines, placement, field_name, rotation_buffer, output]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        # update the suggested name when the line layer changes, but keep names entered by the user
        lines = parameters[0].valueAsText
        output = parameters[4]
        if not lines:
            return

        current_output = output.valueAsText
        if output.altered and current_output != self._generated_output_default:
            return

        try:
            suggested_output = _default_output_path(lines)
        except Exception:
            return

        output.value = suggested_output
        self._generated_output_default = suggested_output

    def updateMessages(self, parameters):
        # if the rotation field name is altered and is blank, set an error message
        if parameters[2].altered and not str(parameters[2].valueAsText or "").strip():
            parameters[2].setErrorMessage("Rotation field name cannot be blank")

    def execute(self, parameters, messages):
        try:
            arrow_creation_arcpy.execute(
                parameters[0].valueAsText, # lines
                parameters[1].valueAsText, # arrowhead placement
                parameters[2].valueAsText, # rotation field name
                parameters[3].valueAsText, # rotation buffer
                parameters[4].valueAsText, # output arrowheads
            )
        except Exception as exc:
            arcpy.AddError(str(exc))
            raise

class RotateArrowheads:
    def __init__(self):
        self.label = "Update Existing Arrowhead Rotations"
        self.description = (
            "Matches arrowhead points to nearby line endpoints and writes "
            "clockwise-from-east rotation values with a configurable degree buffer. "
            "An existing arrowhead point layer is required. Uncertain matches are skipped."
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
