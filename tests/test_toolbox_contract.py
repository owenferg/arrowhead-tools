'''
toolbox parameter contract tests without requiring an ArcGIS Pro license
'''

import importlib.machinery
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest


PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "toolbox"
sys.path.insert(0, str(PACKAGE))


class Filter:
    '''small arcpy filter stand-in for value lists and geometry filters'''

    def __init__(self):
        self.type = None
        self.list = []


class Parameter:
    '''small arcpy parameter stand-in for loading the python toolbox'''

    def __init__(self, **values):
        self.displayName = None
        self.name = None
        self.datatype = None
        self.parameterType = None
        self.direction = None
        self.category = None
        self.multiValue = False
        self.filter = Filter()
        self.schema = types.SimpleNamespace(clone=False, geometryType=None)
        self.value = None
        self.values = None
        self.altered = False
        self.enabled = True
        self.error = None
        self.warning = None
        self.parameterDependencies = []
        self._columns = []
        self.filters = []
        self.__dict__.update(values)
        if not isinstance(getattr(self, "filter", None), Filter):
            self.filter = Filter()

    @property
    def columns(self):
        return self._columns

    @columns.setter
    def columns(self, value):
        self._columns = value
        self.filters = [Filter() for _unused in value]

    @property
    def valueAsText(self):
        return None if self.value is None else str(self.value)

    def setErrorMessage(self, message):
        self.error = message

    def setWarningMessage(self, message):
        self.warning = message


class ToolboxContractTests(unittest.TestCase):
    def setUp(self):
        # load the pyt file with only the arcpy surface used by the toolbox definition
        self.temp_directory = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self.temp_directory.name) / "source.gdb"
        self.workspace.mkdir()
        self.project_workspace = pathlib.Path(self.temp_directory.name) / "project.gdb"
        self.project_workspace.mkdir()

        arcpy = types.ModuleType("arcpy")
        arcpy.Parameter = Parameter
        arcpy.AddError = lambda message: None
        def describe(value):
            path = pathlib.Path(str(value))
            is_geodatabase = str(path).lower().endswith(".gdb")
            suffix = path.suffix.lower()
            shape_type = None
            if suffix == ".shp":
                name = path.stem.lower()
                if "point" in name:
                    shape_type = "Point"
                elif "poly" in name or "area" in name:
                    shape_type = "Polygon"
                else:
                    shape_type = "Polyline"
            return types.SimpleNamespace(
                baseName=path.stem,
                catalogPath=str(value),
                path=str(path.parent),
                dataType="Workspace" if is_geodatabase else "Folder",
                workspaceType="LocalDatabase" if is_geodatabase else "FileSystem",
                shapeType=shape_type,
            )

        arcpy.Describe = describe
        arcpy.Exists = lambda value: pathlib.Path(str(value)).exists()
        arcpy.ListFields = lambda dataset: []
        arcpy.da = types.SimpleNamespace(
            SearchCursor=lambda dataset, fields: iter(()),
        )
        arcpy.ValidateTableName = lambda name, workspace: name.replace(" ", "_")
        arcpy.mp = types.SimpleNamespace(
            ArcGISProject=lambda project: types.SimpleNamespace(
                defaultGeodatabase=str(self.project_workspace)
            )
        )
        arcpy.env = types.SimpleNamespace(scratchGDB=str(self.project_workspace))
        sys.modules["arcpy"] = arcpy
        sys.modules.pop("arrow_creation_arcpy", None)
        sys.modules.pop("arrow_rotation_arcpy", None)

        loader = importlib.machinery.SourceFileLoader(
            "arrow_tools_contract", str(PACKAGE / "arrow_tools.pyt")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.toolbox = importlib.util.module_from_spec(spec)
        loader.exec_module(self.toolbox)

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_tool_registration_and_distinct_display_names(self):
        tools = self.toolbox.Toolbox().tools
        self.assertEqual(
            tools,
            [
                self.toolbox.CreateArrowheadsFromLineEndpoints,
                self.toolbox.RotateArrowheads,
                self.toolbox.IntegrateGIUMData,
            ],
        )
        self.assertEqual(
            self.toolbox.CreateArrowheadsFromLineEndpoints().label,
            "Create Arrowheads from Line Endpoints",
        )
        self.assertEqual(
            self.toolbox.RotateArrowheads().label,
            "Update Existing Arrowhead Rotations",
        )
        self.assertEqual(
            self.toolbox.IntegrateGIUMData().label,
            "Integrate Data into Existing GIUM Layers",
        )

    def test_creation_parameter_defaults_and_execute_forwarding(self):
        parameters = self.toolbox.CreateArrowheadsFromLineEndpoints().getParameterInfo()
        self.assertEqual(
            [parameter.name for parameter in parameters],
            [
                "lines",
                "placement",
                "custom_placement_field",
                "rotation_field",
                "rotation_buffer",
                "output_arrowheads",
            ],
        )
        self.assertEqual(parameters[0].filter.list, ["Polyline"])
        self.assertEqual(
            parameters[1].filter.list,
            ["START", "END", "BOTH", "CUSTOM"],
        )
        self.assertEqual(parameters[1].value, "END")
        self.assertEqual(parameters[2].parameterDependencies, ["lines"])
        self.assertEqual(
            parameters[2].filter.list,
            ["Short", "Long", "BigInteger", "Text"],
        )
        self.assertFalse(parameters[2].enabled)
        self.assertEqual(parameters[3].value, "Rotation")
        self.assertEqual(parameters[4].value, 0)
        self.assertEqual(parameters[5].schema.geometryType, "Point")

        values = [
            "lines", "CUSTOM", "BothEnds", "MapRotation", "-4", "new_arrowheads",
        ]
        for parameter, value in zip(parameters, values):
            parameter.value = value

        forwarded = []
        self.toolbox.arrow_creation_arcpy.execute = (
            lambda *args, **kwargs: forwarded.append((args, kwargs))
        )
        self.toolbox.CreateArrowheadsFromLineEndpoints().execute(parameters, None)

        self.assertEqual(
            forwarded,
            [(
                ("lines", "CUSTOM", "MapRotation", "-4", "new_arrowheads"),
                {"custom_field": "BothEnds"},
            )],
        )

    def test_custom_creation_field_is_enabled_and_required_only_for_custom(self):
        tool = self.toolbox.CreateArrowheadsFromLineEndpoints()
        parameters = tool.getParameterInfo()

        parameters[1].value = "CUSTOM"
        tool.updateParameters(parameters)
        tool.updateMessages(parameters)
        self.assertTrue(parameters[2].enabled)
        self.assertEqual(
            parameters[2].error,
            "Custom placement field is required for CUSTOM placement",
        )

        parameters[2].value = "BothEnds"
        tool.updateMessages(parameters)
        parameters[1].value = "END"
        tool.updateParameters(parameters)
        self.assertFalse(parameters[2].enabled)
        self.assertEqual(parameters[2].value, "BothEnds")

    def test_creation_output_default_tracks_input_until_user_edits_it(self):
        tool = self.toolbox.CreateArrowheadsFromLineEndpoints()
        parameters = tool.getParameterInfo()

        roads = self.workspace / "Roads"
        parameters[0].value = str(roads)
        tool.updateParameters(parameters)
        self.assertEqual(
            parameters[5].value,
            str(self.workspace / "Roads_Arrowheads"),
        )

        trails = self.workspace / "Trail Lines"
        # ArcGIS can mark values assigned during validation as altered
        parameters[5].altered = True
        parameters[0].value = str(trails)
        tool.updateParameters(parameters)
        self.assertEqual(
            parameters[5].value,
            str(self.workspace / "Trail_Lines_Arrowheads"),
        )

        parameters[5].value = str(self.workspace / "My_Custom_Output")
        parameters[5].altered = True
        parameters[0].value = str(self.workspace / "Streams")
        tool.updateParameters(parameters)
        self.assertEqual(
            parameters[5].value,
            str(self.workspace / "My_Custom_Output"),
        )

    def test_creation_output_default_uses_shapefile_for_folder_workspace(self):
        tool = self.toolbox.CreateArrowheadsFromLineEndpoints()
        parameters = tool.getParameterInfo()
        parameters[0].value = str(pathlib.Path(self.temp_directory.name) / "Roads.shp")

        tool.updateParameters(parameters)

        self.assertEqual(
            parameters[5].value,
            str(pathlib.Path(self.temp_directory.name) / "Roads_Arrowheads.shp"),
        )

    def test_creation_output_default_avoids_enterprise_workspace(self):
        enterprise = pathlib.Path(self.temp_directory.name) / "enterprise.sde"
        enterprise.touch()
        tool = self.toolbox.CreateArrowheadsFromLineEndpoints()
        parameters = tool.getParameterInfo()
        parameters[0].value = str(enterprise / "Roads")

        tool.updateParameters(parameters)

        self.assertEqual(
            parameters[5].value,
            str(self.project_workspace / "Roads_Arrowheads"),
        )

    def test_parameter_order_and_execute_forwarding(self):
        parameters = self.toolbox.RotateArrowheads().getParameterInfo()
        self.assertEqual(
            [parameter.name for parameter in parameters],
            [
                "arrowhead_points",
                "lines",
                "match_distance",
                "rotation_field",
                "rotation_buffer",
                "audit_table",
                "updated_arrowheads",
            ],
        )

        values = ["points", "lines", "2 Meters", "Rotation", "-4", "audit"]
        for parameter, value in zip(parameters, values):
            parameter.value = value

        forwarded = []
        self.toolbox.arrow_rotation_arcpy.execute = lambda *args: forwarded.append(args)
        self.toolbox.RotateArrowheads().execute(parameters, None)

        self.assertEqual(
            forwarded,
            [("points", "lines", "2 Meters", "Rotation", "audit", "-4")],
        )
        self.assertEqual(parameters[6].value, "points")

    def test_gium_parameter_contract_and_forwarding(self):
        tool = self.toolbox.IntegrateGIUMData()
        parameters = tool.getParameterInfo()
        self.assertEqual(
            [parameter.name for parameter in parameters],
            [
                "datasets",
                "herd_name",
                "country",
                "release_date",
                "output_folder",
                "created_files",
                "qa_csv",
            ],
        )
        self.assertEqual(parameters[0].datatype, "GPValueTable")
        self.assertEqual(
            [column[1] for column in parameters[0].columns],
            [
                "Layer type",
                "Existing production shapefile (.shp)",
                "New data",
                "Class",
                "Type",
                "Season",
                "Package as",
                "Geographic transformation",
            ],
        )
        self.assertEqual(parameters[0].filters[0].type, "ValueList")
        self.assertIn("Seasonal arrows", parameters[0].filters[0].list)
        self.assertIn("Other", parameters[0].filters[0].list)
        self.assertEqual(parameters[0].filters[6].list, [
            "Zipped shapefile", "GeoJSON", "Both",
        ])
        self.assertTrue(parameters[5].multiValue)

        rows = [[
            "Seasonal arrows",
            "old_lines.shp",
            "new_lines",
            "Migration",
            "",
            "Spring migration",
            "",
            "LINE_TRANSFORM",
        ]]
        parameters[0].values = rows
        parameters[1].value = "Test Herd"
        parameters[2].value = "Test Country"
        parameters[3].value = "2026-08-04"
        parameters[4].value = "/release"

        forwarded = []
        self.toolbox.gium_integration_arcpy.execute = lambda *args: (
            forwarded.append(args)
            or {
                "created": ["/release/lines.shp", "/release/lines.zip"],
                "qa_csv": "/release/qa.csv",
            }
        )
        tool.execute(parameters, None)

        self.assertEqual(
            forwarded,
            [(rows, "Test Herd", "Test Country", "2026-08-04", "/release")],
        )
        self.assertEqual(parameters[5].value, ["/release/lines.shp", "/release/lines.zip"])
        self.assertEqual(parameters[6].value, "/release/qa.csv")

    def test_gium_messages_require_table_rows(self):
        tool = self.toolbox.IntegrateGIUMData()
        parameters = tool.getParameterInfo()
        parameters[4].value = str(pathlib.Path(self.temp_directory.name))

        tool.updateMessages(parameters)

        self.assertEqual(parameters[0].error, "Add at least one dataset to the table.")

    def test_gium_messages_reject_non_shapefile_targets_before_run(self):
        tool = self.toolbox.IntegrateGIUMData()
        parameters = tool.getParameterInfo()
        parameters[0].values = [[
            "Seasonal arrows",
            str(self.workspace / "SeasonalArrows"),
            "new_lines",
            "", "", "", "", "",
        ]]
        parameters[3].value = "2026-08-04"
        parameters[4].value = str(pathlib.Path(self.temp_directory.name))

        tool.updateMessages(parameters)

        self.assertIn("production .shp file", parameters[0].error)

    def test_gium_output_folder_defaults_from_the_first_target(self):
        tool = self.toolbox.IntegrateGIUMData()
        parameters = tool.getParameterInfo()
        parameters[0].values = [[
            "GIUM point labels",
            "/current/point/target.shp",
            "new_points",
            "", "Arrowhead", "", "", "",
        ]]
        captured = []
        self.toolbox._default_release_folder = lambda *datasets: (
            captured.append(datasets) or "/current/point"
        )

        tool.updateParameters(parameters)

        self.assertEqual(captured, [("/current/point/target.shp",)])
        self.assertEqual(parameters[4].value, "/current/point")

    def test_gium_spellcheck_warns_on_unknown_class_values(self):
        tool = self.toolbox.IntegrateGIUMData()
        parameters = tool.getParameterInfo()
        target = str(pathlib.Path(self.temp_directory.name) / "linear_barriers.shp")
        pathlib.Path(target).write_text("placeholder")
        parameters[0].values = [[
            "Linear barriers",
            target,
            "new_lines",
            "fence",
            "", "", "", "",
        ]]
        parameters[3].value = "2026-08-04"
        parameters[4].value = str(pathlib.Path(self.temp_directory.name))
        self.toolbox.arcpy.ListFields = lambda dataset: [
            types.SimpleNamespace(name="Class", type="String")
        ]

        class DistinctCursor:
            def __enter__(self):
                return iter([("Fence",), ("Railway",)])

            def __exit__(self, *unused):
                return False

        self.toolbox.arcpy.da.SearchCursor = lambda dataset, fields: DistinctCursor()

        tool.updateMessages(parameters)

        self.assertIn("fence", parameters[0].warning)
        self.assertIn("Fence, Railway", parameters[0].warning)

    def test_gium_spellcheck_does_not_warn_on_new_season(self):
        tool = self.toolbox.IntegrateGIUMData()
        parameters = tool.getParameterInfo()
        target = str(pathlib.Path(self.temp_directory.name) / "SeasonalArrows.shp")
        pathlib.Path(target).write_text("placeholder")
        parameters[0].values = [[
            "Seasonal arrows",
            target,
            "new_lines",
            "",
            "",
            "Calving",
            "",
            "",
        ]]
        parameters[3].value = "2026-08-04"
        parameters[4].value = str(pathlib.Path(self.temp_directory.name))
        self.toolbox.arcpy.ListFields = lambda dataset: [
            types.SimpleNamespace(name="Season", type="String")
        ]

        class DistinctCursor:
            def __enter__(self):
                return iter([("Spring migration",), ("Fall migration",)])

            def __exit__(self, *unused):
                return False

        self.toolbox.arcpy.da.SearchCursor = lambda dataset, fields: DistinctCursor()

        tool.updateMessages(parameters)

        self.assertIsNone(parameters[0].warning)

    def test_available_transformations_uses_arcgis_recommendation_order(self):
        source_sr = types.SimpleNamespace(name="WGS 1984", factoryCode=4326)
        target_sr = types.SimpleNamespace(name="Web Mercator", factoryCode=3857)
        descriptions = {
            "source": types.SimpleNamespace(
                spatialReference=source_sr, extent="source extent"
            ),
            "target": types.SimpleNamespace(
                spatialReference=target_sr, extent="target extent"
            ),
        }
        self.toolbox.arcpy.Describe = lambda value: descriptions[value]
        calls = []
        self.toolbox.arcpy.ListTransformations = lambda source, target, extent: (
            calls.append((source, target, extent)) or ["BEST", "SECOND"]
        )

        available = self.toolbox._available_transformations("source", "target")

        self.assertEqual(available, ["BEST", "SECOND"])
        self.assertEqual(calls, [(source_sr, target_sr, "source extent")])


if __name__ == "__main__":
    unittest.main()
