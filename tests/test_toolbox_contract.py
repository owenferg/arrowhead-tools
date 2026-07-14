'''
toolbox parameter contract tests without requiring an ArcGIS Pro license
'''

import importlib.machinery
import importlib.util
import pathlib
import sys
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
        self.error = None

    @property
    def valueAsText(self):
        return None if self.value is None else str(self.value)

    def setErrorMessage(self, message):
        self.error = message


class ToolboxContractTests(unittest.TestCase):
    def setUp(self):
        # load the pyt file with only the arcpy surface used by the toolbox definition
        arcpy = types.ModuleType("arcpy")
        arcpy.Parameter = Parameter
        arcpy.AddError = lambda message: None
        sys.modules["arcpy"] = arcpy
        sys.modules.pop("arrow_rotation_arcpy", None)

        loader = importlib.machinery.SourceFileLoader(
            "arrow_tools_contract", str(PACKAGE / "arrow_tools.pyt")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.toolbox = importlib.util.module_from_spec(spec)
        loader.exec_module(self.toolbox)

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
