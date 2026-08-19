'''
portable ArcGIS Pro Python toolbox to help automate creating and rotating arrowheads
created by Owen Ferguson
'''

import importlib
import datetime
import os
import arcpy
import arrow_rotation_core
import arrow_creation_arcpy
import arrow_rotation_arcpy
import gium_integration_core
import gium_integration_arcpy

# reload the scripts when the toolbox is refreshed so ArcGIS does not use older cached versions
arrow_rotation_core = importlib.reload(arrow_rotation_core)
arrow_creation_arcpy = importlib.reload(arrow_creation_arcpy)
arrow_rotation_arcpy = importlib.reload(arrow_rotation_arcpy)
gium_integration_core = importlib.reload(gium_integration_core)
gium_integration_arcpy = importlib.reload(gium_integration_arcpy)


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


def _default_release_folder(*datasets):
    '''suggest a normal folder beside the first selected production dataset'''

    for dataset in datasets:
        if not dataset:
            continue
        try:
            description = arcpy.Describe(dataset)
            path = getattr(description, "path", None)
            catalog_path = getattr(description, "catalogPath", None) or str(dataset)
            candidate = path or os.path.dirname(catalog_path)
            if str(candidate).lower().endswith((".gdb", ".sde")):
                candidate = os.path.dirname(candidate)
            if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue
    return None


def _available_transformations(source_layer, target_layer):
    '''return ArcGIS Pro's recommended datum transformations in preferred order'''

    if not source_layer or not target_layer:
        return []
    try:
        source_description = arcpy.Describe(source_layer)
        target_description = arcpy.Describe(target_layer)
        source_sr = source_description.spatialReference
        target_sr = target_description.spatialReference
        if not source_sr or not target_sr or source_sr.name == "Unknown" or target_sr.name == "Unknown":
            return []
        if (
            source_sr.factoryCode and target_sr.factoryCode
            and source_sr.factoryCode == target_sr.factoryCode
        ):
            return []
        return list(arcpy.ListTransformations(
            source_sr, target_sr, source_description.extent
        ) or [])
    except Exception:
        return []


def _is_shapefile_layer(layer):
    '''check whether a selected production target resolves to a shapefile'''

    if not layer:
        return False
    try:
        description = arcpy.Describe(layer)
        catalog_path = getattr(description, "catalogPath", None) or str(layer)
        return os.path.splitext(str(catalog_path))[1].lower() == ".shp"
    except Exception:
        return False

class Toolbox:
    def __init__(self):
        self.label = "Arrow Tools"
        self.alias = "arrows"
        self.tools = [
            CreateArrowheadsFromLineEndpoints,
            RotateArrowheads,
            IntegrateGIUMArrowData,
        ]


class CreateArrowheadsFromLineEndpoints:
    def __init__(self):
        self.label = "Create Arrowheads from Line Endpoints"
        self.description = (
            "Creates new arrowhead points from line starts, ends, both, or a custom "
            "per-line field and writes clockwise-from-east rotation values facing away "
            "from each line. No existing arrowhead point layer is needed."
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
        placement.filter.list = ["START", "END", "BOTH", "CUSTOM"]
        placement.value = "END"

        custom_placement_field = arcpy.Parameter(
            displayName="Custom placement field",
            name="custom_placement_field",
            datatype="Field",
            parameterType="Optional",
            direction="Input",
        )
        custom_placement_field.parameterDependencies = [lines.name]
        custom_placement_field.filter.list = ["Short", "Long", "BigInteger", "Text"]
        custom_placement_field.enabled = False

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
        rotation_buffer.value = 0

        output = arcpy.Parameter(
            displayName="Output arrowheads",
            name="output_arrowheads",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Output",
        )
        output.schema.geometryType = "Point"

        return [
            lines, placement, custom_placement_field, field_name, rotation_buffer, output,
        ]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        # only show the custom field selector when custom placement is requested
        parameters[2].enabled = parameters[1].valueAsText == "CUSTOM"

        # update the suggested name when the line layer changes, but keep names entered by the user
        lines = parameters[0].valueAsText
        output = parameters[5]
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
        # custom placement requires a field from the input line layer
        if (
            parameters[1].valueAsText == "CUSTOM"
            and not str(parameters[2].valueAsText or "").strip()
        ):
            parameters[2].setErrorMessage(
                "Custom placement field is required for CUSTOM placement"
            )

        # if the rotation field name is altered and is blank, set an error message
        if parameters[3].altered and not str(parameters[3].valueAsText or "").strip():
            parameters[3].setErrorMessage("Rotation field name cannot be blank")

    def execute(self, parameters, messages):
        try:
            arrow_creation_arcpy.execute(
                parameters[0].valueAsText, # lines
                parameters[1].valueAsText, # arrowhead placement
                parameters[3].valueAsText, # rotation field name
                parameters[4].valueAsText, # rotation buffer
                parameters[5].valueAsText, # output arrowheads
                custom_field=parameters[2].valueAsText,
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
        rotation_buffer.value = 0

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


class IntegrateGIUMArrowData:
    '''GIUM release workflow for seasonal lines and point-label arrowheads'''

    def __init__(self):
        self.label = "Integrate Data into Existing GIUM Layers"
        self.description = (
            "Creates safe, dated copies of the GIUM SeasonalArrows and GIUMPointLabels "
            "datasets, projects and appends only the new features, fills missing GIUM "
            "metadata, validates the release, and creates the Mapbox-ready ZIP and "
            "GeoJSON packages. Existing production datasets are never changed."
        )
        self.canRunInBackground = False
        self._generated_output_folder = None
        self._generated_line_transformation = None
        self._generated_point_transformation = None

    @staticmethod
    def _parameter(
        display_name, name, datatype, parameter_type, direction, category
    ):
        parameter = arcpy.Parameter(
            displayName=display_name,
            name=name,
            datatype=datatype,
            parameterType=parameter_type,
            direction=direction,
        )
        parameter.category = category
        return parameter

    def getParameterInfo(self):
        process_lines = self._parameter(
            "Process seasonal arrow lines",
            "process_lines",
            "GPBoolean",
            "Required",
            "Input",
            "1. Seasonal arrow lines",
        )
        process_lines.value = True

        line_target = self._parameter(
            "Existing SeasonalArrows production shapefile (.shp; complete latest)",
            "line_target",
            "GPFeatureLayer",
            "Optional",
            "Input",
            "1. Seasonal arrow lines",
        )
        line_target.filter.list = ["Polyline"]

        new_lines = self._parameter(
            "New seasonal arrow lines",
            "new_lines",
            "GPFeatureLayer",
            "Optional",
            "Input",
            "1. Seasonal arrow lines",
        )
        new_lines.filter.list = ["Polyline"]

        line_transformation = self._parameter(
            "Line geographic transformation",
            "line_transformation",
            "GPString",
            "Optional",
            "Input",
            "1. Seasonal arrow lines",
        )
        line_transformation.filter.type = "ValueList"

        process_points = self._parameter(
            "Process arrowhead points",
            "process_points",
            "GPBoolean",
            "Required",
            "Input",
            "2. Arrowhead points",
        )
        process_points.value = True

        point_target = self._parameter(
            "Existing GIUMPointLabels production shapefile (.shp; complete latest)",
            "point_target",
            "GPFeatureLayer",
            "Optional",
            "Input",
            "2. Arrowhead points",
        )
        point_target.filter.list = ["Point"]

        new_points = self._parameter(
            "New arrowhead points from Part 1",
            "new_points",
            "GPFeatureLayer",
            "Optional",
            "Input",
            "2. Arrowhead points",
        )
        new_points.filter.list = ["Point"]

        point_transformation = self._parameter(
            "Point geographic transformation",
            "point_transformation",
            "GPString",
            "Optional",
            "Input",
            "2. Arrowhead points",
        )
        point_transformation.filter.type = "ValueList"

        herd_name = self._parameter(
            "Herd name (fills blanks only)",
            "herd_name",
            "GPString",
            "Optional",
            "Input",
            "3. GIUM metadata",
        )
        country = self._parameter(
            "Country (fills blanks when the target has Country)",
            "country",
            "GPString",
            "Optional",
            "Input",
            "3. GIUM metadata",
        )
        season = self._parameter(
            "Season (fills blanks only)",
            "season",
            "GPString",
            "Optional",
            "Input",
            "3. GIUM metadata",
        )
        line_class = self._parameter(
            "Line class (fills blanks only)",
            "line_class",
            "GPString",
            "Optional",
            "Input",
            "3. GIUM metadata",
        )
        line_class.value = "Seasonal Movement"

        point_type = self._parameter(
            "Point type (fills blanks only)",
            "point_type",
            "GPString",
            "Required",
            "Input",
            "3. GIUM metadata",
        )
        point_type.value = "Arrowhead"

        release_date = self._parameter(
            "Release date",
            "release_date",
            "GPDate",
            "Required",
            "Input",
            "4. Release outputs",
        )
        release_date.value = datetime.datetime.now()

        output_folder = self._parameter(
            "Release output folder",
            "output_folder",
            "DEFolder",
            "Required",
            "Input",
            "4. Release outputs",
        )

        line_output = self._parameter(
            "New SeasonalArrows shapefile",
            "line_output",
            "DEFeatureClass",
            "Derived",
            "Output",
            "5. Created release",
        )
        line_output.schema.geometryType = "Polyline"
        line_zip = self._parameter(
            "SeasonalArrows ZIP",
            "line_zip",
            "DEFile",
            "Derived",
            "Output",
            "5. Created release",
        )
        point_output = self._parameter(
            "New GIUMPointLabels shapefile",
            "point_output",
            "DEFeatureClass",
            "Derived",
            "Output",
            "5. Created release",
        )
        point_output.schema.geometryType = "Point"
        point_geojson = self._parameter(
            "GIUMPointLabels GeoJSON",
            "point_geojson",
            "DEFile",
            "Derived",
            "Output",
            "5. Created release",
        )
        qa_csv = self._parameter(
            "GIUM release QA report",
            "qa_csv",
            "DEFile",
            "Derived",
            "Output",
            "5. Created release",
        )

        return [
            process_lines,
            line_target,
            new_lines,
            line_transformation,
            process_points,
            point_target,
            new_points,
            point_transformation,
            herd_name,
            country,
            season,
            line_class,
            point_type,
            release_date,
            output_folder,
            line_output,
            line_zip,
            point_output,
            point_geojson,
            qa_csv,
        ]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        process_lines = parameters[0].value is not False
        process_points = parameters[4].value is not False

        # keep inputs for disabled branches out of the users way
        for index in (1, 2, 3):
            parameters[index].enabled = process_lines
        for index in (5, 6, 7):
            parameters[index].enabled = process_points

        parameters[9].enabled = process_lines or process_points
        parameters[11].enabled = process_lines
        parameters[12].enabled = process_points

        # refresh the recommended line transformation when either line layer changes
        line_transformations = (
            _available_transformations(
                parameters[2].valueAsText, parameters[1].valueAsText
            )
            if process_lines
            else []
        )
        parameters[3].filter.list = line_transformations
        current_line_transformation = parameters[3].valueAsText
        if (
            current_line_transformation
            and current_line_transformation not in line_transformations
        ):
            parameters[3].value = None
            current_line_transformation = None
            self._generated_line_transformation = None
        if line_transformations and (
            not parameters[3].altered
            or not current_line_transformation
            or current_line_transformation == self._generated_line_transformation
        ):
            parameters[3].value = line_transformations[0]
            self._generated_line_transformation = line_transformations[0]
        elif (
            not line_transformations
            and current_line_transformation == self._generated_line_transformation
        ):
            parameters[3].value = None
            self._generated_line_transformation = None

        # do the same transformation check for the arrowhead point layers
        point_transformations = (
            _available_transformations(
                parameters[6].valueAsText, parameters[5].valueAsText
            )
            if process_points
            else []
        )
        parameters[7].filter.list = point_transformations
        current_point_transformation = parameters[7].valueAsText
        if (
            current_point_transformation
            and current_point_transformation not in point_transformations
        ):
            parameters[7].value = None
            current_point_transformation = None
            self._generated_point_transformation = None
        if point_transformations and (
            not parameters[7].altered
            or not current_point_transformation
            or current_point_transformation == self._generated_point_transformation
        ):
            parameters[7].value = point_transformations[0]
            self._generated_point_transformation = point_transformations[0]
        elif (
            not point_transformations
            and current_point_transformation == self._generated_point_transformation
        ):
            parameters[7].value = None
            self._generated_point_transformation = None

        output = parameters[14]
        # suggest a nearby release folder until the user enters their own folder
        if not output.altered or output.valueAsText == self._generated_output_folder:
            suggestion = _default_release_folder(
                parameters[1].valueAsText if process_lines else None,
                parameters[5].valueAsText if process_points else None,
            )
            if suggestion:
                output.value = suggestion
                self._generated_output_folder = suggestion

    def updateMessages(self, parameters):
        process_lines = parameters[0].value is not False
        process_points = parameters[4].value is not False

        # show input problems in the tool form before the user presses Run
        if not process_lines and not process_points:
            parameters[0].setErrorMessage(
                "Select at least one branch: seasonal arrow lines or arrowhead points."
            )
        if process_lines:
            if not parameters[1].valueAsText:
                parameters[1].setErrorMessage(
                    "Choose the complete latest SeasonalArrows layer."
                )
            elif not _is_shapefile_layer(parameters[1].valueAsText):
                parameters[1].setErrorMessage(
                    "The SeasonalArrows target must be the complete production .shp file, "
                    "not a geodatabase feature class."
                )
            if not parameters[2].valueAsText:
                parameters[2].setErrorMessage("Choose the new seasonal arrow lines.")
        if process_points:
            if not parameters[5].valueAsText:
                parameters[5].setErrorMessage(
                    "Choose the complete latest GIUMPointLabels layer."
                )
            elif not _is_shapefile_layer(parameters[5].valueAsText):
                parameters[5].setErrorMessage(
                    "The GIUMPointLabels target must be the complete production .shp file, "
                    "not a geodatabase feature class."
                )
            if not parameters[6].valueAsText:
                parameters[6].setErrorMessage(
                    "Choose the new arrowheads created by Part 1."
                )
            if not str(parameters[12].valueAsText or "").strip():
                parameters[12].setErrorMessage("Point type cannot be blank.")
        if not parameters[13].value:
            parameters[13].setErrorMessage("Choose a release date.")
        if not parameters[14].valueAsText:
            parameters[14].setErrorMessage("Choose a release output folder.")

    @staticmethod
    def _result_value(result, name):
        if isinstance(result, dict):
            return result.get(name)
        return getattr(result, name, None)

    def execute(self, parameters, messages):
        try:
            # pass the ArcGIS form values to the integration adapter
            result = gium_integration_arcpy.execute(
                parameters[0].value,
                parameters[1].valueAsText,
                parameters[2].valueAsText,
                parameters[3].valueAsText,
                parameters[4].value,
                parameters[5].valueAsText,
                parameters[6].valueAsText,
                parameters[7].valueAsText,
                parameters[8].valueAsText,
                parameters[9].valueAsText,
                parameters[10].valueAsText,
                parameters[11].valueAsText,
                parameters[12].valueAsText,
                parameters[13].value,
                parameters[14].valueAsText,
            )
            # send created paths back to ArcGIS as derived outputs
            for index, name in zip(
                (15, 16, 17, 18, 19),
                ("line_output", "line_zip", "point_output", "point_geojson", "qa_csv"),
            ):
                value = self._result_value(result, name)
                if value:
                    parameters[index].value = value
        except Exception as exc:
            arcpy.AddError(str(exc))
            raise
