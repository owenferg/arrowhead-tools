'''
unit tests for arrow_creation_arcpy.py without requiring an ArcGIS Pro license
'''

import importlib
import os
import pathlib
import sys
import types
import unittest


PACKAGE = pathlib.Path(__file__).resolve().parents[1] / 'toolbox'
sys.path.insert(0, str(PACKAGE))


class SpatialReference:
    def __init__(self, code, name=None, sr_type='Projected', meters_per_unit=1.0):
        self.factoryCode = code
        self.name = name or f'SR {code}'
        self.type = sr_type
        self.metersPerUnit = meters_per_unit

    def exportToString(self):
        return f'{self.factoryCode}:{self.name}:{self.type}'


class Point:
    def __init__(self, x, y, z=None, m=None):
        self.X = x
        self.Y = y
        self.Z = z
        self.M = m


class Geometry:
    def __init__(self, spatial_reference, parts=None, has_curves=False):
        self.spatialReference = spatial_reference
        self._parts = [
            [Point(*coordinates) for coordinates in part]
            for part in (parts or [])
        ]
        self.pointCount = sum(map(len, self._parts))
        self.hasCurves = has_curves

    def __iter__(self):
        return iter(self._parts)

    def projectAs(self, spatial_reference, transformation=None):
        projected = Geometry(spatial_reference)
        projected._parts = self._parts
        projected.pointCount = self.pointCount
        projected.hasCurves = self.hasCurves
        return projected

    def densify(self, method, distance, deviation):
        self.hasCurves = False
        return self


class PointGeometry:
    def __init__(self, point, spatial_reference, has_z, has_m):
        self.firstPoint = point
        self.spatialReference = spatial_reference
        self.hasZ = has_z
        self.hasM = has_m


class Field:
    def __init__(
        self, name, field_type='String', editable=True, required=False,
        length=80, precision=0, scale=0,
    ):
        self.name = name
        self.aliasName = name
        self.type = field_type
        self.editable = editable
        self.required = required
        self.length = length
        self.precision = precision
        self.scale = scale


class Dataset:
    def __init__(
        self, path, spatial_reference, rows, fields, shape_type='Polyline',
        has_oid=True, has_oid64=False, has_z=False, has_m=False,
    ):
        self.path = path
        self.spatial_reference = spatial_reference
        self.rows = rows
        self.fields = fields
        self.shape_type = shape_type
        self.extent = object()
        self.has_oid = has_oid
        self.has_oid64 = has_oid64
        self.has_z = has_z
        self.has_m = has_m


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


class InsertCursor(CursorBase):
    def __init__(self, arcpy, layer, fields):
        self.dataset = arcpy.datasets[layer]
        self.fields = fields

    def insertRow(self, values):
        self.dataset.rows.append(dict(zip(self.fields, values)))


def _field_value(row, field):
    if field == 'OID@':
        return row['oid']
    if field == 'SHAPE@':
        return row['geometry']
    return row.get(field)


def build_fake_arcpy():
    module = types.ModuleType('arcpy')
    module.datasets = {}
    module.messages = []
    module.warnings = []
    module.env = types.SimpleNamespace(overwriteOutput=True)
    module.SpatialReference = lambda code: SpatialReference(code)
    module.PointGeometry = PointGeometry
    module.AddMessage = module.messages.append
    module.AddWarning = module.warnings.append
    module.Exists = lambda path: path in module.datasets
    module.ValidateFieldName = lambda name, workspace: str(name).replace(' ', '_')
    module.ListTransformations = lambda source, target, extent: ['TEST_TRANSFORM']
    module.ListFields = lambda layer: module.datasets[layer].fields

    def describe(layer):
        dataset = module.datasets[layer]
        return types.SimpleNamespace(
            spatialReference=dataset.spatial_reference,
            catalogPath=dataset.path,
            extent=dataset.extent,
            hasOID=dataset.has_oid,
            hasOID64=dataset.has_oid64,
            hasZ=dataset.has_z,
            hasM=dataset.has_m,
            shapeType=dataset.shape_type,
        )

    module.Describe = describe
    module.da = types.SimpleNamespace(
        SearchCursor=lambda layer, fields: SearchCursor(module, layer, fields),
        InsertCursor=lambda layer, fields: InsertCursor(module, layer, fields),
    )

    def create_featureclass(
        workspace, name, geometry_type, template=None, has_m='DISABLED',
        has_z='DISABLED', spatial_reference=None,
    ):
        path = os.path.join(workspace, name)
        module.datasets[path] = Dataset(
            path,
            spatial_reference,
            [],
            [Field('OBJECTID', 'OID', False, True), Field('Shape', 'Geometry', False, True)],
            'Point',
            has_z=has_z == 'ENABLED',
            has_m=has_m == 'ENABLED',
        )

    type_names = {
        'SHORT': 'SmallInteger', 'LONG': 'Integer', 'BIGINTEGER': 'BigInteger',
        'FLOAT': 'Single', 'DOUBLE': 'Double', 'TEXT': 'String', 'DATE': 'Date',
        'GUID': 'Guid',
    }

    def add_field(layer, name, field_type, **kwargs):
        field = Field(
            name,
            type_names.get(field_type, field_type.title()),
            length=kwargs.get('field_length', 80),
            precision=kwargs.get('field_precision', 0),
            scale=kwargs.get('field_scale', 0),
        )
        module.datasets[layer].fields.append(field)

    module.management = types.SimpleNamespace(
        CreateFeatureclass=create_featureclass,
        AddField=add_field,
        Delete=lambda path: module.datasets.pop(path, None),
    )
    return module


class ArrowCreationTests(unittest.TestCase):
    def setUp(self):
        self.arcpy = build_fake_arcpy()
        sys.modules['arcpy'] = self.arcpy
        sys.modules.pop('arrow_creation_arcpy', None)
        self.tool = importlib.import_module('arrow_creation_arcpy')
        self.sr = SpatialReference(32633, 'UTM 33N')
        self.lines = '/test.gdb/lines'
        self.output = '/test.gdb/lines_Arrowheads'
        self.arcpy.datasets[self.lines] = Dataset(
            self.lines,
            self.sr,
            [{
                'oid': 9,
                'geometry': Geometry(
                    self.sr,
                    parts=[[(0, 0), (10, 0)], [(5, 5), (5, 15)]],
                ),
                'RoadName': 'Main',
            }],
            [
                Field('OBJECTID', 'OID', False, True),
                Field('Shape', 'Geometry', False, True),
                Field('RoadName', 'String', length=40),
            ],
        )

    def test_end_creates_one_outward_arrowhead_per_part(self):
        self.tool.execute(self.lines, 'END', 'Rotation', '3', self.output)

        rows = self.arcpy.datasets[self.output].rows
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['Rotation'] for row in rows], [3, 273])
        self.assertEqual([row['ENDPOINT'] for row in rows], ['END', 'END'])
        self.assertEqual([row['SOURCE_PART'] for row in rows], [0, 1])
        self.assertEqual([row['RoadName'] for row in rows], ['Main', 'Main'])
        self.assertEqual(
            [(row['SHAPE@'].firstPoint.X, row['SHAPE@'].firstPoint.Y) for row in rows],
            [(10, 0), (5, 15)],
        )

    def test_both_uses_distinct_terminal_segments_and_wraps_buffer(self):
        self.arcpy.datasets[self.lines].rows[0]['geometry'] = Geometry(
            self.sr, parts=[[(0, 0), (0, 0), (10, 0), (10, 0)]]
        )

        self.tool.execute(self.lines, 'BOTH', 'Angle', '-5', self.output)

        rows = self.arcpy.datasets[self.output].rows
        self.assertEqual([row['ENDPOINT'] for row in rows], ['START', 'END'])
        self.assertEqual([row['Angle'] for row in rows], [175, 355])

    def test_custom_uses_boolean_values_for_each_line_and_part(self):
        dataset = self.arcpy.datasets[self.lines]
        dataset.fields.append(Field('BothEnds', 'SmallInteger'))
        dataset.rows[0]['BothEnds'] = True
        dataset.rows.append({
            'oid': 10,
            'geometry': Geometry(self.sr, parts=[[(20, 0), (30, 0)]]),
            'RoadName': 'Second',
            'BothEnds': 0,
        })

        self.tool.execute(
            self.lines, 'CUSTOM', 'Rotation', 3, self.output,
            custom_field='bothends',
        )

        rows = self.arcpy.datasets[self.output].rows
        self.assertEqual(
            [(row['SOURCE_OID'], row['SOURCE_PART'], row['ENDPOINT']) for row in rows],
            [
                (9, 0, 'START'), (9, 0, 'END'),
                (9, 1, 'START'), (9, 1, 'END'),
                (10, 0, 'END'),
            ],
        )
        self.assertEqual([row['BothEnds'] for row in rows], [True, True, True, True, 0])

    def test_custom_accepts_case_insensitive_text_booleans(self):
        dataset = self.arcpy.datasets[self.lines]
        dataset.fields.append(Field('BothEnds', 'String'))
        dataset.rows[0]['BothEnds'] = ' TrUe '
        dataset.rows.append({
            'oid': 10,
            'geometry': Geometry(self.sr, parts=[[(20, 0), (30, 0)]]),
            'RoadName': 'Second',
            'BothEnds': 'FALSE',
        })

        self.tool.execute(
            self.lines, 'CUSTOM', 'Rotation', 3, self.output,
            custom_field='BothEnds',
        )

        rows = self.arcpy.datasets[self.output].rows
        self.assertEqual(
            [(row['SOURCE_OID'], row['ENDPOINT']) for row in rows],
            [(9, 'START'), (9, 'END'), (9, 'START'), (9, 'END'), (10, 'END')],
        )

    def test_invalid_custom_field_or_value_preserves_existing_output(self):
        dataset = self.arcpy.datasets[self.lines]
        existing = Dataset(self.output, self.sr, [{'keep': True}], [], 'Point')
        self.arcpy.datasets[self.output] = existing

        with self.assertRaisesRegex(ValueError, 'is required for CUSTOM'):
            self.tool.execute(self.lines, 'CUSTOM', 'Rotation', 3, self.output)
        self.assertIs(self.arcpy.datasets[self.output], existing)

        with self.assertRaisesRegex(ValueError, 'was not found'):
            self.tool.execute(
                self.lines, 'CUSTOM', 'Rotation', 3, self.output,
                custom_field='Missing',
            )
        self.assertIs(self.arcpy.datasets[self.output], existing)

        dataset.fields.append(Field('BothEnds', 'Double'))
        dataset.rows[0]['BothEnds'] = 1.0
        with self.assertRaisesRegex(ValueError, 'must be a Short, Long, Big Integer, or Text'):
            self.tool.execute(
                self.lines, 'CUSTOM', 'Rotation', 3, self.output,
                custom_field='BothEnds',
            )
        self.assertIs(self.arcpy.datasets[self.output], existing)

        dataset.fields[-1].type = 'SmallInteger'
        for invalid_value in (None, 2, '1'):
            dataset.rows[0]['BothEnds'] = invalid_value
            with self.subTest(value=invalid_value):
                with self.assertRaisesRegex(
                    ValueError, "BothEnds.*invalid Boolean value.*Object ID 9"
                ):
                    self.tool.execute(
                        self.lines, 'CUSTOM', 'Rotation', 3, self.output,
                        custom_field='BothEnds',
                    )
                self.assertIs(self.arcpy.datasets[self.output], existing)

    def test_closed_and_degenerate_parts_are_skipped_and_partial_output_is_removed(self):
        self.arcpy.datasets[self.lines].rows[0]['geometry'] = Geometry(
            self.sr, parts=[[(0, 0), (1, 0), (0, 0)], [(2, 2), (2, 2)]]
        )

        with self.assertRaisesRegex(ValueError, 'No usable line endpoints'):
            self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        self.assertNotIn(self.output, self.arcpy.datasets)

    def test_metadata_collisions_get_unique_names_and_rotation_is_reused(self):
        dataset = self.arcpy.datasets[self.lines]
        dataset.has_oid64 = True
        dataset.fields.extend([
            Field('SOURCE_OID', 'Integer'),
            Field('SOURCE_PART', 'Integer'),
            Field('ENDPOINT', 'String'),
            Field('Rotation', 'Double'),
        ])
        dataset.rows[0].update(
            SOURCE_OID=77, SOURCE_PART=88, ENDPOINT='old', Rotation=999
        )

        self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        fields = {field.name: field for field in self.arcpy.datasets[self.output].fields}
        self.assertIn('SOURCE_OID_1', fields)
        self.assertEqual(fields['SOURCE_OID_1'].type, 'BigInteger')
        self.assertIn('SOURCE_PART_1', fields)
        self.assertIn('ENDPOINT_1', fields)
        row = self.arcpy.datasets[self.output].rows[0]
        self.assertEqual(row['SOURCE_OID'], 77)
        self.assertEqual(row['SOURCE_OID_1'], 9)
        self.assertEqual(row['Rotation'], 3)

    def test_invalid_rotation_collision_does_not_delete_existing_output(self):
        dataset = self.arcpy.datasets[self.lines]
        dataset.fields.append(Field('Rotation', 'String'))
        dataset.rows[0]['Rotation'] = 'old'
        existing = Dataset(self.output, self.sr, [{'keep': True}], [], 'Point')
        self.arcpy.datasets[self.output] = existing

        with self.assertRaisesRegex(ValueError, 'must be numeric'):
            self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        self.assertIs(self.arcpy.datasets[self.output], existing)

    def test_overwrite_setting_is_honored(self):
        self.arcpy.datasets[self.output] = Dataset(self.output, self.sr, [], [], 'Point')
        self.arcpy.env.overwriteOutput = False

        with self.assertRaisesRegex(ValueError, 'overwrite output is disabled'):
            self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

    def test_z_and_m_are_preserved_on_output_points(self):
        dataset = self.arcpy.datasets[self.lines]
        dataset.has_z = True
        dataset.has_m = True
        dataset.rows[0]['geometry'] = Geometry(self.sr, parts=[[(0, 0, 7, 2), (10, 0, 8, 3)]])

        self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        output = self.arcpy.datasets[self.output]
        point_geometry = output.rows[0]['SHAPE@']
        self.assertTrue(output.has_z)
        self.assertTrue(output.has_m)
        self.assertEqual((point_geometry.firstPoint.Z, point_geometry.firstPoint.M), (8, 3))

    def test_geographic_lines_use_projected_direction_but_source_geometry(self):
        geographic = SpatialReference(4326, 'WGS 84', 'Geographic', 111319.49)
        dataset = self.arcpy.datasets[self.lines]
        dataset.spatial_reference = geographic
        dataset.rows[0]['geometry'] = Geometry(geographic, parts=[[(-120, 35), (-119, 35)]])

        self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        row = self.arcpy.datasets[self.output].rows[0]
        self.assertIs(row['SHAPE@'].spatialReference, geographic)
        self.assertTrue(any('Web Mercator' in warning for warning in self.arcpy.warnings))

    def test_joined_or_oidless_input_is_rejected(self):
        dataset = self.arcpy.datasets[self.lines]
        dataset.fields.append(Field('joined.value'))
        with self.assertRaisesRegex(ValueError, 'Joined'):
            self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        dataset.fields.pop()
        dataset.has_oid = False
        with self.assertRaisesRegex(ValueError, 'Object ID'):
            self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

    def test_invalid_inputs_do_not_create_an_output(self):
        for placement, buffer, field, message in (
            ('MIDDLE', 3, 'Rotation', 'START, END, BOTH, or CUSTOM'),
            ('END', 'nan', 'Rotation', 'finite number'),
            ('END', 3, 'bad name', 'not valid'),
        ):
            with self.subTest(placement=placement, buffer=buffer, field=field):
                with self.assertRaisesRegex(ValueError, message):
                    self.tool.execute(self.lines, placement, field, buffer, self.output)
                self.assertNotIn(self.output, self.arcpy.datasets)

    def test_system_field_cannot_be_used_for_rotation(self):
        existing = Dataset(self.output, self.sr, [{'keep': True}], [], 'Point')
        self.arcpy.datasets[self.output] = existing

        with self.assertRaisesRegex(ValueError, 'system or noneditable'):
            self.tool.execute(self.lines, 'END', 'OBJECTID', 3, self.output)

        self.assertIs(self.arcpy.datasets[self.output], existing)

    def test_guid_source_attribute_is_copied(self):
        dataset = self.arcpy.datasets[self.lines]
        dataset.fields.append(Field('AssetGuid', 'Guid'))
        dataset.rows[0]['AssetGuid'] = '{ABC}'

        self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        fields = {field.name: field.type for field in self.arcpy.datasets[self.output].fields}
        self.assertEqual(fields['AssetGuid'], 'Guid')
        self.assertEqual(self.arcpy.datasets[self.output].rows[0]['AssetGuid'], '{ABC}')

    def test_invalid_working_linear_units_are_rejected_before_overwrite(self):
        self.sr.metersPerUnit = 0
        existing = Dataset(self.output, self.sr, [{'keep': True}], [], 'Point')
        self.arcpy.datasets[self.output] = existing

        with self.assertRaisesRegex(ValueError, 'valid linear units'):
            self.tool.execute(self.lines, 'END', 'Rotation', 3, self.output)

        self.assertIs(self.arcpy.datasets[self.output], existing)


if __name__ == '__main__':
    unittest.main()
