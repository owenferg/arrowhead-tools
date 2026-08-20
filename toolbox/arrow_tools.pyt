'''
portable ArcGIS Pro Python toolbox to help automate creating and rotating arrowheads
created by Owen Ferguson
'''

import importlib
import datetime
import os
import arcpy  # pyright: ignore[reportMissingImports]
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
            IntegrateGIUMData,
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


class IntegrateGIUMData:
    '''GIUM release workflow for any number of existing production layers'''

    _SPELLCHECK_ROLES = ('class', 'type')
    _VALUE_LIST_LIMIT = 12

    def __init__(self):
        self.label = "Integrate Data into Existing GIUM Layers"
        self.description = (
            "Creates safe, dated copies of existing GIUM production shapefiles, "
            "projects and appends only the new features, fills missing metadata, "
            "validates the release, and creates the Mapbox-ready ZIP and GeoJSON "
            "packages. Existing production datasets are never changed."
        )
        self.canRunInBackground = False
        self._generated_output_folder = None
        self._value_cache = {}

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
        datasets = self._parameter(
            "Datasets to add",
            "datasets",
            "GPValueTable",
            "Required",
            "Input",
            "1. Datasets",
        )
        datasets.columns = [
            ["GPString", "Layer type"],
            ["GPFeatureLayer", "Existing production shapefile (.shp)"],
            ["GPFeatureLayer", "New data"],
            ["GPString", "Class"],
            ["GPString", "Type"],
            ["GPString", "Season"],
            ["GPString", "Package as"],
            ["GPString", "Geographic transformation"],
        ]
        datasets.filters[0].type = "ValueList"
        datasets.filters[0].list = list(gium_integration_core.LAYER_PROFILE_NAMES)
        datasets.filters[6].type = "ValueList"
        datasets.filters[6].list = list(gium_integration_core.PACKAGE_CHOICES)

        herd_name = self._parameter(
            "Herd name (fills blanks only)",
            "herd_name",
            "GPString",
            "Optional",
            "Input",
            "2. Shared metadata",
        )
        country = self._parameter(
            "Country (fills blanks when the target has Country)",
            "country",
            "GPString",
            "Optional",
            "Input",
            "2. Shared metadata",
        )
        release_date = self._parameter(
            "Release date",
            "release_date",
            "GPDate",
            "Required",
            "Input",
            "2. Shared metadata",
        )
        release_date.value = datetime.datetime.now()
        output_folder = self._parameter(
            "Release output folder",
            "output_folder",
            "DEFolder",
            "Required",
            "Input",
            "2. Shared metadata",
        )

        created = self._parameter(
            "Created release files",
            "created_files",
            "DEFile",
            "Derived",
            "Output",
            "3. Created release",
        )
        created.multiValue = True
        qa_csv = self._parameter(
            "GIUM release QA report",
            "qa_csv",
            "DEFile",
            "Derived",
            "Output",
            "3. Created release",
        )

        return [
            datasets,
            herd_name,
            country,
            release_date,
            output_folder,
            created,
            qa_csv,
        ]

    def isLicensed(self):
        return True

    @staticmethod
    def _table_rows(parameter):
        rows = getattr(parameter, "values", None)
        if rows is None:
            rows = parameter.value
        return list(rows or [])

    @staticmethod
    def _cell_text(value):
        if value is None:
            return ""
        return str(value).strip()

    def _role_field_name(self, dataset, profile, role):
        aliases = {alias.casefold() for alias in profile.aliases.get(role, ())}
        if not aliases:
            return None
        try:
            matches = [
                field.name
                for field in arcpy.ListFields(dataset)
                if field.name.casefold() in aliases
            ]
        except Exception:
            return None
        return matches[0] if len(matches) == 1 else None

    def _distinct_field_values(self, dataset, field_name):
        try:
            catalog = getattr(arcpy.Describe(dataset), "catalogPath", dataset)
            path = str(catalog)
            mtime = os.path.getmtime(path) if os.path.exists(path) else None
            size = os.path.getsize(path) if os.path.exists(path) else None
        except Exception:
            path, mtime, size = str(dataset), None, None
        cache_key = (path, field_name.casefold())
        cached = self._value_cache.get(cache_key)
        if cached and cached[0] == mtime and cached[1] == size:
            return cached[2]
        values = []
        seen = set()
        try:
            with arcpy.da.SearchCursor(dataset, [field_name]) as rows:
                for (value,) in rows:
                    text = "" if value is None else str(value).strip()
                    if text and text not in seen:
                        seen.add(text)
                        values.append(text)
        except Exception:
            values = []
        self._value_cache[cache_key] = (mtime, size, values)
        return values

    def _spellcheck_warning(self, row_number, role, typed, existing):
        if typed in existing:
            return None
        listed = existing[: self._VALUE_LIST_LIMIT]
        extra = len(existing) - len(listed)
        choices = ", ".join(listed)
        if extra > 0:
            choices = f"{choices}, and {extra} more"
        return (
            f"Row {row_number} {role} {typed!r} is not an existing value. "
            f"Existing values are: {choices}. If this is a genuinely new {role}, "
            "you can ignore this."
        )

    def updateParameters(self, parameters):
        output = parameters[4]
        if not output.altered or output.valueAsText == self._generated_output_folder:
            targets = []
            for row in self._table_rows(parameters[0]):
                if row and len(row) > 1 and row[1]:
                    targets.append(row[1])
            suggestion = _default_release_folder(*targets)
            if suggestion:
                output.value = suggestion
                self._generated_output_folder = suggestion

    def updateMessages(self, parameters):
        rows = self._table_rows(parameters[0])
        errors = []
        warnings = []
        if not rows:
            errors.append("Add at least one dataset to the table.")
        for index, row in enumerate(rows, start=1):
            cells = list(row) + [None] * (8 - len(row))
            layer_type = self._cell_text(cells[0])
            target = cells[1]
            new_data = cells[2]
            package = self._cell_text(cells[6])
            transformation = self._cell_text(cells[7])
            if not layer_type:
                errors.append(f"Row {index} needs a layer type.")
                continue
            try:
                profile = gium_integration_core.layer_profile(layer_type)
            except ValueError as error:
                errors.append(f"Row {index}: {error}")
                continue
            if not target:
                errors.append(f"Row {index} needs an existing production shapefile.")
            elif not _is_shapefile_layer(target):
                errors.append(
                    f"Row {index}: the target must be the complete production .shp file, "
                    "not a geodatabase feature class."
                )
            if not new_data:
                errors.append(f"Row {index} needs the new data to add.")
            if package:
                try:
                    gium_integration_core.resolve_package_formats(package, profile)
                except ValueError as error:
                    errors.append(f"Row {index}: {error}")
            if target and new_data:
                expected = profile.shape_type
                try:
                    target_shape = getattr(arcpy.Describe(target), "shapeType", None)
                    source_shape = getattr(arcpy.Describe(new_data), "shapeType", None)
                except Exception:
                    target_shape = source_shape = None
                if expected and target_shape and target_shape != expected:
                    errors.append(
                        f"Row {index}: {profile.name} needs {expected.lower()} features."
                    )
                elif expected and source_shape and source_shape != expected:
                    errors.append(
                        f"Row {index}: the new data must contain {expected.lower()} features."
                    )
                available = _available_transformations(new_data, target)
                if transformation and available and transformation not in available:
                    warnings.append(
                        f"Row {index}: geographic transformation {transformation!r} is "
                        f"not in ArcGIS Pro's list. Available choices: "
                        f"{', '.join(available[:5])}."
                    )
                elif transformation and not available:
                    warnings.append(
                        f"Row {index}: ArcGIS Pro did not find a geographic transformation "
                        "for these layers. Leave this cell blank, or run the Project tool "
                        "first if the datums differ."
                    )
            if target:
                typed = {
                    "class": self._cell_text(cells[3]),
                    "type": self._cell_text(cells[4]),
                }
                for role in self._SPELLCHECK_ROLES:
                    value = typed[role]
                    if not value:
                        continue
                    field_name = self._role_field_name(target, profile, role)
                    if not field_name:
                        continue
                    existing = self._distinct_field_values(target, field_name)
                    if not existing:
                        continue
                    warning = self._spellcheck_warning(index, role, value, existing)
                    if warning:
                        warnings.append(warning)
        if errors:
            parameters[0].setErrorMessage("\n".join(errors))
        if warnings:
            parameters[0].setWarningMessage("\n".join(warnings))
        if not parameters[3].value:
            parameters[3].setErrorMessage("Choose a release date.")
        if not parameters[4].valueAsText:
            parameters[4].setErrorMessage("Choose a release output folder.")

    @staticmethod
    def _result_value(result, name):
        if isinstance(result, dict):
            return result.get(name)
        return getattr(result, name, None)

    def execute(self, parameters, messages):
        try:
            rows = self._table_rows(parameters[0])
            result = gium_integration_arcpy.execute(
                rows,
                parameters[1].valueAsText,
                parameters[2].valueAsText,
                parameters[3].value,
                parameters[4].valueAsText,
            )
            created = self._result_value(result, "created")
            if created:
                parameters[5].value = created
            qa_csv = self._result_value(result, "qa_csv")
            if qa_csv:
                parameters[6].value = qa_csv
        except Exception as exc:
            arcpy.AddError(str(exc))
            raise

