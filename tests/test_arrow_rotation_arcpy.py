'''
unit tests for arrow_rotation_arcpy.py without requiring an ArcGIS Pro license
'''

import importlib
import pathlib
import sys
import types
import unittest


PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "toolbox"
sys.path.insert(0, str(PACKAGE))


class SpatialReference:
    def __init__(self, code, name=None, sr_type="Projected", meters_per_unit=1.0):
        self.factoryCode = code
        self.name = name or f"SR {code}"
        self.type = sr_type
        self.metersPerUnit = meters_per_unit

    def exportToString(self):
        return f"{self.factoryCode}:{self.name}:{self.type}"


class Point:
    def __init__(self, x, y):
        self.X = x
        self.Y = y


class Geometry:
    def __init__(self, spatial_reference, parts=None, point=None, has_curves=False):
        self.spatialReference = spatial_reference
        self._parts = [[Point(x, y) for x, y in part] for part in (parts or [])]
        self.firstPoint = Point(*point) if point is not None else None
        self.pointCount = sum(map(len, self._parts)) + (1 if self.firstPoint else 0)
        self.extent = object()
        self.hasCurves = has_curves
        self.densified = False

    def __iter__(self):
        return iter(self._parts)

    def projectAs(self, spatial_reference, transformation=None):
        projected = Geometry(spatial_reference)
        projected._parts = self._parts
        projected.firstPoint = self.firstPoint
        projected.pointCount = self.pointCount
        projected.hasCurves = self.hasCurves
        projected.extent = self.extent
        return projected

    def densify(self, method, distance, deviation):
        self.densified = True
        self.hasCurves = False
        return self


class Field:
    def __init__(self, name, field_type="Double", editable=True):
        self.name = name
        self.type = field_type
        self.editable = editable


class Dataset:
    def __init__(self, path, spatial_reference, rows, fields, shape_type, has_oid64=False):
        self.path = path
        self.spatial_reference = spatial_reference
        self.rows = rows
        self.fields = fields
        self.shape_type = shape_type
        self.extent = object()
        self.has_oid64 = has_oid64


class CursorBase:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SearchCursor(CursorBase):
    def __init__(self, arcpy, layer, fields):
        self.dataset = arcpy.datasets[layer]
        self.fields = fields

    def __iter__(self):
        for row in self.dataset.rows:
            yield tuple(_field_value(row, field) for field in self.fields)


class UpdateCursor(SearchCursor):
    def __iter__(self):
        for row in self.dataset.rows:
            self.current = row
            yield tuple(_field_value(row, field) for field in self.fields)

    def updateRow(self, values):
        for field, value in zip(self.fields, values):
            if field not in {"OID@", "SHAPE@"}:
                self.current[field] = value


class InsertCursor(CursorBase):
    def __init__(self, arcpy, table, fields):
        self.dataset = arcpy.datasets[table]
        self.fields = fields

    def insertRow(self, values):
        self.dataset.rows.append(dict(zip(self.fields, values)))


def _field_value(row, field):
    if field == "OID@":
        return row["oid"]
    if field == "SHAPE@":
        return row["geometry"]
    return row.get(field)


def build_fake_arcpy():
    module = types.ModuleType("arcpy")
    module.datasets = {}
    module.messages = []
    module.warnings = []
    module.env = types.SimpleNamespace(overwriteOutput=True)
    module.SpatialReference = lambda code: SpatialReference(code)
    module.AddMessage = module.messages.append
    module.AddWarning = module.warnings.append
    module.AddError = module.warnings.append
    module.Exists = lambda path: path in module.datasets
    module.TestSchemaLock = lambda path: True
    module.ValidateFieldName = lambda name, workspace: name
    module.ListTransformations = lambda source, target, extent: ["TEST_TRANSFORM"]
    module.ListFields = lambda layer: module.datasets[layer].fields

    def describe(layer):
        dataset = module.datasets[layer]
        return types.SimpleNamespace(
            spatialReference=dataset.spatial_reference,
            catalogPath=dataset.path,
            extent=dataset.extent,
            hasOID=True,
            hasOID64=dataset.has_oid64,
            shapeType=dataset.shape_type,
        )

    module.Describe = describe
    module.da = types.SimpleNamespace(
        SearchCursor=lambda layer, fields: SearchCursor(module, layer, fields),
        UpdateCursor=lambda layer, fields: UpdateCursor(module, layer, fields),
        InsertCursor=lambda layer, fields: InsertCursor(module, layer, fields),
    )

    def add_field(layer, name, field_type, field_length=None):
        dataset = module.datasets[layer]
        dataset.fields.append(Field(name, "Double" if field_type == "DOUBLE" else field_type.title()))
        for row in dataset.rows:
            row[name] = None

    def create_table(workspace, name):
        path = str(pathlib.PureWindowsPath(workspace) / name)
        module.datasets[path] = Dataset(path, None, [], [], "Table")

    module.management = types.SimpleNamespace(
        AddField=add_field,
        CreateTable=create_table,
        Delete=lambda path: module.datasets.pop(path, None),
    )
    return module


class ArrowToolTests(unittest.TestCase):
    def setUp(self):
        self.arcpy = build_fake_arcpy()
        sys.modules["arcpy"] = self.arcpy
        sys.modules.pop("arrow_rotation_arcpy", None)
        self.tool = importlib.import_module("arrow_rotation_arcpy")

        sr = SpatialReference(32633, "UTM 33N")
        self.point_rows = [
            {"oid": 1, "geometry": Geometry(sr, point=(10, 0)), "rotation_deg": 33},
            {"oid": 2, "geometry": Geometry(sr, point=(0, 0)), "rotation_deg": 44},
            {"oid": 3, "geometry": Geometry(sr, point=(50, 50)), "rotation_deg": 77},
        ]
        self.arcpy.datasets["points"] = Dataset(
            r"C:\test.gdb\points", sr, self.point_rows,
            [Field("OBJECTID", "OID", False), Field("rotation_deg")], "Point",
        )
        self.arcpy.datasets["lines"] = Dataset(
            r"C:\test.gdb\lines", sr,
            [{"oid": 9, "geometry": Geometry(sr, parts=[[(0, 0), (10, 0)]])}],
            [Field("OBJECTID", "OID", False)], "Polyline",
        )

    def test_execute_updates_matches_and_preserves_unmatched(self):
        audit = r"C:\test.gdb\arrow_audit"
        self.tool.execute("points", "lines", "2 Meters", "rotation_deg", audit)

        self.assertEqual(self.point_rows[0]["rotation_deg"], 3)
        self.assertEqual(self.point_rows[1]["rotation_deg"], 183)
        self.assertEqual(self.point_rows[2]["rotation_deg"], 77)
        statuses = [row["STATUS"] for row in self.arcpy.datasets[audit].rows]
        self.assertEqual(statuses, ["MATCHED", "MATCHED", "UNMATCHED"])
        rotations = [row["ROTATION"] for row in self.arcpy.datasets[audit].rows]
        self.assertEqual(rotations, [3, 183, None])
        self.assertTrue(any("Updated 2" in message for message in self.arcpy.messages))

    def test_existing_audit_is_preserved_when_overwrite_is_disabled(self):
        audit = r"C:\test.gdb\arrow_audit"
        self.arcpy.datasets[audit] = Dataset(audit, None, [{"keep": True}], [], "Table")
        self.arcpy.env.overwriteOutput = False

        with self.assertRaisesRegex(ValueError, "overwrite output is disabled"):
            self.tool.execute("points", "lines", "2 Meters", "rotation_deg", audit)

        self.assertEqual(self.arcpy.datasets[audit].rows, [{"keep": True}])

    def test_audit_uses_big_integer_for_64_bit_point_ids(self):
        audit = r"C:\test.gdb\arrow_audit"
        self.arcpy.datasets["points"].has_oid64 = True

        self.tool.execute("points", "lines", "2 Meters", "rotation_deg", audit)

        point_oid = next(
            field for field in self.arcpy.datasets[audit].fields
            if field.name == "POINT_OID"
        )
        self.assertEqual(point_oid.type, "Biginteger")

    def test_new_rotation_field_is_created(self):
        self.arcpy.datasets["points"].fields.pop()
        for row in self.point_rows:
            row.pop("rotation_deg")
        self.tool.execute("points", "lines", "2 Meters", "new_angle", None)
        self.assertEqual(self.point_rows[0]["new_angle"], 3)
        self.assertEqual(self.point_rows[2]["new_angle"], None)

    def test_rotation_buffer_is_modifiable_and_wraps(self):
        self.tool.execute(
            "points", "lines", "2 Meters", "rotation_deg", None, "-5"
        )
        self.assertEqual(self.point_rows[0]["rotation_deg"], 355)
        self.assertEqual(self.point_rows[1]["rotation_deg"], 175)

    def test_invalid_rotation_buffer_is_rejected_before_update(self):
        for value, message in (("nan", "finite number"), ("abc", "must be a number")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                self.tool.execute(
                    "points", "lines", "2 Meters", "rotation_deg", None, value
                )
        self.assertEqual(self.point_rows[0]["rotation_deg"], 33)

    def test_non_numeric_rotation_field_is_rejected_before_update(self):
        self.arcpy.datasets["points"].fields[-1].type = "String"
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            self.tool.execute("points", "lines", "2 Meters", "rotation_deg", None)
        self.assertEqual(self.point_rows[0]["rotation_deg"], 33)

    def test_joined_layer_is_rejected(self):
        self.arcpy.datasets["points"].fields.append(Field("joined.value"))
        with self.assertRaisesRegex(ValueError, "Joined"):
            self.tool.execute("points", "lines", "2 Meters", "rotation_deg", None)

    def test_common_arcgis_linear_units_are_supported(self):
        value, unit = self.tool._parse_linear_unit("10 Feet US")
        self.assertEqual((value, unit), (10, "feetus"))
        self.assertAlmostEqual(self.tool._METERS_PER_UNIT[unit], 1200.0 / 3937.0)

    def test_non_finite_match_distance_is_rejected(self):
        for value in ("nan Meters", "inf Meters"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "positive"):
                self.tool._parse_linear_unit(value)


if __name__ == "__main__":
    unittest.main()
