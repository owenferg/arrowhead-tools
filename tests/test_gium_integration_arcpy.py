'''
unit tests for gium_integration_arcpy.py without requiring an ArcGIS Pro license
'''

import copy
import csv
import importlib
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock


TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / 'toolbox'
sys.path.insert(0, str(TOOLBOX))


class SpatialReference:
    def __init__(self, code, name=None, gcs=4326):
        self.factoryCode = code
        self.name = name or f'SR {code}'
        self.GCS = types.SimpleNamespace(factoryCode=gcs, name=f'GCS {gcs}')

    def exportToString(self):
        return f'{self.factoryCode}:{self.name}'


class Field:
    def __init__(self, name, field_type='String', length=80, required=False, editable=True):
        self.name = name
        self.aliasName = name
        self.type = field_type
        self.length = length
        self.required = required
        self.editable = editable
        self.precision = 0
        self.scale = 0


class Geometry:
    def __init__(self, empty=False, part_count=1):
        self.isEmpty = empty
        self.partCount = part_count


class Dataset:
    def __init__(self, path, shape_type, spatial_reference, fields, rows=None, catalog_path=None):
        self.path = path
        self.shape_type = shape_type
        self.spatial_reference = spatial_reference
        self.fields = fields
        self.rows = rows or []
        self.catalog_path = catalog_path or path
        self.extent = object()


class Cursor:
    def __init__(self, module, dataset, fields, update=False):
        self.dataset = module.datasets[str(dataset)]
        self.fields = fields
        self.update = update
        self.index = -1

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def __iter__(self):
        for index, row in enumerate(self.dataset.rows):
            self.index = index
            yield tuple(
                row.get('oid') if name == 'OID@'
                else row.get('geometry') if name == 'SHAPE@'
                else row.get(name)
                for name in self.fields
            )

    def updateRow(self, values):
        row = self.dataset.rows[self.index]
        for name, value in zip(self.fields, values):
            if name not in ('OID@', 'SHAPE@'):
                row[name] = value


class FieldMap:
    def __init__(self):
        self.source = None
        self.source_field = None
        self.outputField = Field('placeholder')

    def addInputField(self, dataset, field):
        self.source = str(dataset)
        self.source_field = field
        self.outputField = copy.copy(next(
            item for item in FAKE.datasets[self.source].fields if item.name == field
        ))


class FieldMappings:
    def __init__(self):
        self.maps = []

    def addFieldMap(self, field_map):
        self.maps.append(field_map)


FAKE = None


def build_fake_arcpy():
    global FAKE
    module = types.ModuleType('arcpy')
    FAKE = module
    module.datasets = {}
    module.messages = []
    module.warnings = []
    module.errors = []
    module.AddMessage = module.messages.append
    module.AddWarning = module.warnings.append
    module.AddError = module.errors.append
    module.FieldMap = FieldMap
    module.FieldMappings = FieldMappings
    module.ListFields = lambda dataset: module.datasets[str(dataset)].fields
    module.ListTransformations = lambda source, target, extent: []

    def describe(dataset):
        item = module.datasets[str(dataset)]
        return types.SimpleNamespace(
            catalogPath=item.catalog_path,
            shapeType=item.shape_type,
            spatialReference=item.spatial_reference,
            extent=item.extent,
        )

    module.Describe = describe
    module.da = types.SimpleNamespace(
        SearchCursor=lambda dataset, fields: Cursor(module, dataset, fields),
        UpdateCursor=lambda dataset, fields: Cursor(module, dataset, fields, True),
    )

    def copy_features(source, output):
        source = str(source)
        output = str(output)
        item = module.datasets[source]
        module.datasets[output] = Dataset(
            output, item.shape_type, item.spatial_reference,
            copy.deepcopy(item.fields), copy.deepcopy(item.rows), output,
        )
        if output.lower().endswith('.shp'):
            stem = os.path.splitext(output)[0]
            for suffix in ('.shp', '.shx', '.dbf', '.prj', '.cpg'):
                pathlib.Path(stem + suffix).write_bytes(b'test')
            # represent an ArcGIS temporary file that must not be published
            pathlib.Path(stem + '.lock').write_bytes(b'temporary')
        return [output]

    def create_file_gdb(folder, name):
        path = os.path.join(folder, name)
        os.mkdir(path)
        return [path]

    def project(in_dataset, out_dataset, out_coor_system, transform_method=''):
        copy_features(in_dataset, out_dataset)
        module.datasets[str(out_dataset)].spatial_reference = out_coor_system
        return [out_dataset]

    def add_field(dataset, name, field_type, **kwargs):
        types_by_gp = {'TEXT': 'String', 'DOUBLE': 'Double', 'LONG': 'Integer'}
        module.datasets[str(dataset)].fields.append(Field(
            name, types_by_gp.get(field_type, field_type), kwargs.get('field_length', 80)
        ))
        for row in module.datasets[str(dataset)].rows:
            row[name] = None

    def append(inputs, target, schema_type, field_mapping):
        source = module.datasets[str(inputs)]
        destination = module.datasets[str(target)]
        next_oid = len(destination.rows) + 1
        for source_row in source.rows:
            row = {'oid': next_oid, 'geometry': source_row.get('geometry')}
            for mapping in field_mapping.maps:
                row[mapping.outputField.name] = source_row.get(mapping.source_field)
            destination.rows.append(row)
            next_oid += 1

    def check_geometry(dataset, output_table, validation_method='ESRI'):
        problems = []
        for index, row in enumerate(module.datasets[str(dataset)].rows, start=1):
            geometry = row.get('geometry')
            if geometry is None or getattr(geometry, 'isEmpty', False):
                problems.append({'oid': index})
        module.datasets[str(output_table)] = Dataset(
            str(output_table), 'Table', None, [], problems
        )
        return [output_table]

    module.management = types.SimpleNamespace(
        GetCount=lambda dataset: [str(len(module.datasets[str(dataset)].rows))],
        CopyFeatures=copy_features,
        CreateFileGDB=create_file_gdb,
        Project=project,
        AddField=add_field,
        Append=append,
        CheckGeometry=check_geometry,
    )

    def features_to_json(source, output, *unused):
        item = module.datasets[str(source)]
        payload = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [0, 0]},
             'properties': {}}
            for unused_row in item.rows
        ]}
        pathlib.Path(output).write_text(json.dumps(payload), encoding='utf-8')

    module.conversion = types.SimpleNamespace(FeaturesToJSON=features_to_json)
    return module


def production_fields(kind):
    base = [Field('FID', 'OID', required=True, editable=False),
            Field('Shape', 'Geometry', required=True, editable=False)]
    if kind == 'Polyline':
        return base + [Field('HerdName'), Field('Country'), Field('Season'), Field('Class')]
    if kind == 'Polygon':
        return base + [Field('Type'), Field('Class'), Field('Herd_Name'), Field('Country')]
    return base + [Field('Herd_Name'), Field('Season'), Field('Type'), Field('Rotation', 'Double')]


class GiumIntegrationArcPyTests(unittest.TestCase):
    def setUp(self):
        self.arcpy = build_fake_arcpy()
        sys.modules['arcpy'] = self.arcpy
        sys.modules.pop('gium_integration_arcpy', None)
        self.tool = importlib.import_module('gium_integration_arcpy')
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.release = root / 'new_release'
        self.sr = SpatialReference(3857)
        self.source_sr = SpatialReference(4326)
        self.line_target = str(root / 'SeasonalArrows_old.shp')
        self.point_target = str(root / 'GIUMPointLabels_old.shp')
        self.line_source = str(root / 'source_lines')
        self.point_source = str(root / 'source_points')
        self.line_layer = 'selected_line_layer'
        self.point_layer = 'selected_point_layer'

        historical_line = {'oid': 1, 'geometry': Geometry(), 'HerdName': 'Old',
                           'Country': 'Mongolia', 'Season': 'Winter', 'Class': 'Migration'}
        historical_point = {'oid': 1, 'geometry': Geometry(), 'Herd_Name': 'Old',
                            'Season': 'Winter', 'Type': 'Arrowhead', 'Rotation': 90.0}
        self.arcpy.datasets[self.line_target] = Dataset(
            self.line_target, 'Polyline', self.sr, production_fields('Polyline'), [historical_line]
        )
        self.arcpy.datasets[self.point_target] = Dataset(
            self.point_target, 'Point', self.sr, production_fields('Point'), [historical_point]
        )
        line_fields = production_fields('Polyline')
        point_fields = production_fields('Point')
        selected_line = {'oid': 7, 'geometry': Geometry(), 'HerdName': '', 'Country': '',
                         'Season': 'Spring migration', 'Class': ''}
        selected_point = {'oid': 8, 'geometry': Geometry(), 'Herd_Name': '',
                          'Season': 'Spring migration', 'Type': '', 'Rotation': 32.0}
        self.arcpy.datasets[self.line_source] = Dataset(
            self.line_source, 'Polyline', self.source_sr, line_fields,
            [selected_line, dict(selected_line, oid=9, HerdName='Not selected')]
        )
        self.arcpy.datasets[self.point_source] = Dataset(
            self.point_source, 'Point', self.source_sr, point_fields,
            [selected_point, dict(selected_point, oid=10, Herd_Name='Not selected')]
        )
        # layer views contain the selected row but point to the complete source data
        self.arcpy.datasets[self.line_layer] = Dataset(
            self.line_layer, 'Polyline', self.source_sr, line_fields,
            [selected_line], self.line_source,
        )
        self.arcpy.datasets[self.point_layer] = Dataset(
            self.point_layer, 'Point', self.source_sr, point_fields,
            [selected_point], self.point_source,
        )

    def tearDown(self):
        self.temp.cleanup()

    def line_row(self, **changes):
        row = [
            'Seasonal arrows', self.line_target, self.line_layer,
            'Migration', '', 'Spring migration', '', '',
        ]
        keys = (
            'layer_type', 'target', 'new_data', 'class', 'type', 'season',
            'package', 'transformation',
        )
        for key, value in changes.items():
            row[keys.index(key)] = value
        return row

    def point_row(self, **changes):
        row = [
            'GIUM point labels', self.point_target, self.point_layer,
            '', 'Arrowhead', 'Spring migration', '', '',
        ]
        keys = (
            'layer_type', 'target', 'new_data', 'class', 'type', 'season',
            'package', 'transformation',
        )
        for key, value in changes.items():
            row[keys.index(key)] = value
        return row

    def run_tool(self, datasets=None, **changes):
        values = dict(
            datasets=datasets if datasets is not None else [
                self.line_row(), self.point_row(),
            ],
            herd_name='Test Herd',
            country='Kazakhstan',
            release_date='2026-08-04',
            output_folder=str(self.release),
        )
        values.update(changes)
        return self.tool.execute(**values)

    def created_named(self, result, filename):
        for path in result.created:
            if os.path.basename(path) == filename:
                return path
        self.fail('%s not in %s' % (filename, result.created))

    def test_complete_release_is_packaged_without_changing_targets(self):
        result = self.run_tool()

        self.assertEqual(len(self.arcpy.datasets[self.line_target].rows), 1)
        self.assertEqual(len(self.arcpy.datasets[self.point_target].rows), 1)
        line_zip = self.created_named(result, 'SeasonalArrowsMerged_August4_2026.zip')
        point_geojson = self.created_named(
            result, 'GIUMPointLabelsMerged_August4_2026.geojson'
        )
        for path in (line_zip, point_geojson, result.qa_csv):
            self.assertTrue(os.path.isfile(path), path)
        self.assertFalse(list(self.release.glob('*.lock')))
        with zipfile.ZipFile(line_zip) as archive:
            self.assertTrue(
                {
                    'SeasonalArrowsMerged_August4_2026.shp',
                    'SeasonalArrowsMerged_August4_2026.shx',
                    'SeasonalArrowsMerged_August4_2026.dbf',
                    'SeasonalArrowsMerged_August4_2026.prj',
                }
                <= set(archive.namelist())
            )
        with open(result.qa_csv, encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertIn('overall_result', {row['check'] for row in rows})
        self.assertTrue(any(
            row['check'] == 'herd_fallback_rows' and row['value'] == '1'
            for row in rows
        ))
        self.assertTrue(any(
            row['check'] == 'rotation_minimum' and row['value'] == '32.0'
            for row in rows
        ))
        self.assertTrue(any('visual review' in message for message in self.arcpy.messages))

    def test_points_only_release_skips_line_outputs(self):
        result = self.run_tool(datasets=[self.point_row()])
        self.assertFalse(any(path.endswith('.zip') for path in result.created))
        self.assertTrue(os.path.isfile(
            self.created_named(result, 'GIUMPointLabelsMerged_August4_2026.geojson')
        ))

    def test_target_layer_selection_is_ignored_but_new_selection_is_honored(self):
        target_layer = 'selected_historical_line_layer'
        target = self.arcpy.datasets[self.line_target]
        target.rows.append(dict(target.rows[0], oid=2, HerdName='Second historic herd'))
        self.arcpy.datasets[target_layer] = Dataset(
            target_layer, 'Polyline', self.sr, target.fields,
            [target.rows[0]], self.line_target,
        )
        result = self.run_tool(datasets=[self.line_row(target=target_layer)])
        with open(result.qa_csv, encoding='utf-8-sig') as stream:
            qa = {row['check']: row['value'] for row in csv.DictReader(stream)}
        self.assertEqual(qa['historical_count'], '2')
        self.assertEqual(qa['new_count'], '1')
        self.assertEqual(qa['final_count'], '3')

    def test_unavailable_requested_transformation_fails_in_preflight(self):
        with self.assertRaisesRegex(ValueError, 'not valid'):
            self.run_tool(datasets=[self.line_row(transformation='NOT_A_REAL_TRANSFORM')])
        self.assertFalse(self.release.exists() and any(self.release.iterdir()))

    def test_target_must_be_production_shapefile(self):
        self.arcpy.datasets['target_in_gdb'] = copy.deepcopy(
            self.arcpy.datasets[self.line_target]
        )
        self.arcpy.datasets['target_in_gdb'].catalog_path = '/test.gdb/seasonal_target'
        with self.assertRaisesRegex(ValueError, 'production shapefile'):
            self.run_tool(datasets=[self.line_row(target='target_in_gdb')])

    def test_joined_new_layer_is_rejected_with_clear_instruction(self):
        self.arcpy.datasets[self.line_layer].fields.append(Field('lookup.value'))
        with self.assertRaisesRegex(ValueError, 'Remove the join'):
            self.run_tool()

    def test_same_named_numeric_field_cannot_be_silently_coerced(self):
        self.arcpy.datasets[self.line_target].fields.append(Field('Measure', 'Integer'))
        self.arcpy.datasets[self.line_layer].fields.append(Field('Measure', 'Double'))
        self.arcpy.datasets[self.line_layer].rows[0]['Measure'] = 12.5
        with self.assertRaisesRegex(ValueError, 'coerce or lose values'):
            self.run_tool()

    def test_extra_text_field_cannot_be_silently_truncated(self):
        self.arcpy.datasets[self.line_target].fields.append(Field('Notes', length=5))
        self.arcpy.datasets[self.line_layer].fields.append(Field('Notes', length=80))
        self.arcpy.datasets[self.line_layer].rows[0]['Notes'] = 'too long'
        with self.assertRaisesRegex(ValueError, "allows 5"):
            self.run_tool()

    def test_empty_historical_target_is_rejected(self):
        self.arcpy.datasets[self.line_target].rows.clear()
        with self.assertRaisesRegex(ValueError, 'latest complete GIUM production'):
            self.run_tool()

    def test_invalid_rotation_publishes_nothing(self):
        self.arcpy.datasets[self.point_layer].rows[0]['Rotation'] = 360.0
        with self.assertRaisesRegex(ValueError, 'less than 360'):
            self.run_tool()
        self.assertFalse(any(self.release.glob('*')))
        self.assertEqual(len(self.arcpy.datasets[self.point_target].rows), 1)

    def test_append_failure_rolls_back_every_release_artifact(self):
        def fail_append(*unused_args, **unused_kwargs):
            raise RuntimeError('simulated append failure')

        self.arcpy.management.Append = fail_append
        with self.assertRaisesRegex(RuntimeError, 'simulated append failure'):
            self.run_tool()

        self.assertTrue(self.release.is_dir())
        self.assertFalse(list(self.release.iterdir()))
        self.assertEqual(len(self.arcpy.datasets[self.line_target].rows), 1)
        self.assertEqual(len(self.arcpy.datasets[self.point_target].rows), 1)

    def test_geojson_failure_rolls_back_staged_line_and_point_outputs(self):
        def fail_geojson(*unused_args, **unused_kwargs):
            raise RuntimeError('simulated GeoJSON failure')

        self.arcpy.conversion.FeaturesToJSON = fail_geojson
        with self.assertRaisesRegex(RuntimeError, 'simulated GeoJSON failure'):
            self.run_tool()

        self.assertTrue(self.release.is_dir())
        self.assertFalse(list(self.release.iterdir()))

    def test_partial_copy_failure_removes_the_current_destination(self):
        source = pathlib.Path(self.temp.name) / 'source.csv'
        source.write_text('complete')
        self.release.mkdir()

        def partial_copy(unused_source, destination):
            pathlib.Path(destination).write_text('partial')
            raise OSError('simulated disk full')

        with mock.patch.object(self.tool.shutil, 'copy2', side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, 'simulated disk full'):
                self.tool._copy_release_artifacts([str(source)], str(self.release))

        self.assertFalse(list(self.release.iterdir()))

    def test_existing_sidecar_blocks_release_before_staging(self):
        self.release.mkdir()
        orphan = self.release / 'SeasonalArrowsMerged_August4_2026.dbf'
        orphan.write_text('old')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_tool()
        self.assertEqual(orphan.read_text(), 'old')

    def test_multipart_new_data_fails_in_preflight(self):
        self.arcpy.datasets[self.line_layer].rows[0]['geometry'] = Geometry(part_count=2)
        with self.assertRaisesRegex(ValueError, 'multipart'):
            self.run_tool()
        self.assertFalse(self.release.exists() and any(self.release.iterdir()))

    def test_polygon_row_is_packaged_as_a_zip(self):
        root = pathlib.Path(self.temp.name)
        poly_target = str(root / 'poly_features_old.shp')
        poly_source = str(root / 'source_polys')
        historical = {
            'oid': 1, 'geometry': Geometry(), 'Type': 'Range', 'Class': 'Range',
            'Herd_Name': 'Old', 'Country': 'Canada',
        }
        selected = {
            'oid': 2, 'geometry': Geometry(), 'Type': '', 'Class': '',
            'Herd_Name': '', 'Country': '',
        }
        self.arcpy.datasets[poly_target] = Dataset(
            poly_target, 'Polygon', self.sr, production_fields('Polygon'), [historical]
        )
        self.arcpy.datasets[poly_source] = Dataset(
            poly_source, 'Polygon', self.source_sr, production_fields('Polygon'), [selected]
        )
        result = self.run_tool(datasets=[[
            'Polygon features', poly_target, poly_source,
            'Range', 'Migration land', '', '', '',
        ]])
        zip_path = self.created_named(result, 'poly_features_August4_2026.zip')
        self.assertTrue(os.path.isfile(zip_path))
        self.assertFalse(any(path.endswith('.geojson') for path in result.created))

    def test_three_row_release_packages_each_dataset(self):
        root = pathlib.Path(self.temp.name)
        poly_target = str(root / 'ProtectedAreas_old.shp')
        poly_source = str(root / 'source_pa')
        historical = {
            'oid': 1, 'geometry': Geometry(), 'Type': 'Protected Area',
            'Class': '', 'Herd_Name': 'Old', 'Country': 'Canada',
        }
        selected = {
            'oid': 2, 'geometry': Geometry(), 'Type': '',
            'Class': '', 'Herd_Name': '', 'Country': '',
        }
        self.arcpy.datasets[poly_target] = Dataset(
            poly_target, 'Polygon', self.sr, production_fields('Polygon'), [historical]
        )
        self.arcpy.datasets[poly_source] = Dataset(
            poly_source, 'Polygon', self.source_sr, production_fields('Polygon'), [selected]
        )
        result = self.run_tool(datasets=[
            self.line_row(),
            self.point_row(),
            ['Protected areas', poly_target, poly_source, '', 'Protected Area', '', '', ''],
        ])
        self.created_named(result, 'SeasonalArrowsMerged_August4_2026.zip')
        self.created_named(result, 'GIUMPointLabelsMerged_August4_2026.geojson')
        self.created_named(result, 'ProtectedAreas_August4_2026.zip')
        self.assertEqual(os.path.basename(result.qa_csv), 'GIUMIntegration_August4_2026_QA.csv')


if __name__ == '__main__':
    unittest.main()
