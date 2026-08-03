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


class Parameter:
    '''small arcpy parameter stand-in for loading the python toolbox'''

    def __init__(self, **values):
        self.__dict__.update(values)
        self.filter = types.SimpleNamespace(list=[])
        self.schema = types.SimpleNamespace(clone=False)
        self.value = None
        self.altered = False
        self.enabled = True
        self.error = None

    @property
    def valueAsText(self):
        return None if self.value is None else str(self.value)

    def setErrorMessage(self, message):
        self.error = message


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
            return types.SimpleNamespace(
                baseName=path.stem,
                catalogPath=str(value),
                path=str(path.parent),
                dataType="Workspace" if is_geodatabase else "Folder",
                workspaceType="LocalDatabase" if is_geodatabase else "FileSystem",
            )

        arcpy.Describe = describe
        arcpy.Exists = lambda value: pathlib.Path(str(value)).exists()
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
        self.assertEqual(parameters[4].value, 3)
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


if __name__ == "__main__":
    unittest.main()
