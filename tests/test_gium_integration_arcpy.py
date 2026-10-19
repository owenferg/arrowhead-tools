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
    def __init__(self, empty=False, part_count=1, problem=None, shapefile_problem=None):
        self.isEmpty = empty
        self.partCount = part_count
        # problem is reported by Check Geometry anywhere, shapefile_problem only
        # once the feature is in a shapefile, like a precision change on Append
        self.problem = problem
        self.shapefile_problem = shapefile_problem


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
    module.calls = []
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
    module.env = types.SimpleNamespace(scratchFolder=None)

    def describe(dataset):
        item = module.datasets[str(dataset)]
        return types.SimpleNamespace(
            catalogPath=item.catalog_path,
            shapeType=item.shape_type,
            spatialReference=item.spatial_reference,
            extent=item.extent,
        )

    module.Describe = describe
    def search_cursor(dataset, fields):
        module.calls.append(('SearchCursor', str(dataset)))
        return Cursor(module, dataset, fields)

    def update_cursor(dataset, fields):
        module.calls.append(('UpdateCursor', str(dataset)))
        return Cursor(module, dataset, fields, True)

    module.da = types.SimpleNamespace(
        SearchCursor=search_cursor,
        UpdateCursor=update_cursor,
    )

    def first_oid(path):
        # shapefiles number records from 0, geodatabases from 1
        return 0 if str(path).lower().endswith('.shp') else 1

    def copy_features(source, output):
        source = str(source)
        output = str(output)
        module.calls.append(('CopyFeatures', source, output))
        item = module.datasets[source]
        rows = copy.deepcopy(item.rows)
        for oid, row in enumerate(rows, start=first_oid(output)):
            row['oid'] = oid
        module.datasets[output] = Dataset(
            output, item.shape_type, item.spatial_reference,
            copy.deepcopy(item.fields), rows, output,
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
        module.calls.append(('Project', str(in_dataset), transform_method))
        copy_features(in_dataset, out_dataset)
        module.datasets[str(out_dataset)].spatial_reference = out_coor_system
        return [out_dataset]

    def multipart_to_singlepart(in_features, out_features):
        module.calls.append(('MultipartToSinglepart', str(in_features)))
        source = module.datasets[str(in_features)]
        rows = []
        for row in source.rows:
            geometry = row.get('geometry')
            parts = int(getattr(geometry, 'partCount', 1) or 1)
            for unused_part in range(parts):
                part = copy.deepcopy(row)
                part['ORIG_FID'] = row.get('oid')
                if geometry is not None:
                    part['geometry'] = Geometry(
                        geometry.isEmpty, 1, geometry.problem, geometry.shapefile_problem
                    )
                rows.append(part)
        for oid, row in enumerate(rows, start=1):
            row['oid'] = oid
        module.datasets[str(out_features)] = Dataset(
            str(out_features), source.shape_type, source.spatial_reference,
            copy.deepcopy(source.fields) + [Field('ORIG_FID', 'Integer')], rows,
        )
        return [out_features]

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
        next_oid = len(destination.rows) + first_oid(target)
        for source_row in source.rows:
            row = {'oid': next_oid, 'geometry': source_row.get('geometry')}
            for mapping in field_mapping.maps:
                row[mapping.outputField.name] = source_row.get(mapping.source_field)
            destination.rows.append(row)
            next_oid += 1

    def check_geometry(dataset, output_table, validation_method='ESRI'):
        module.calls.append(('CheckGeometry', str(dataset)))
        in_shapefile = str(dataset).lower().endswith('.shp')
        # a dataset-level condition, like a missing spatial index, never blocks
        problems = [{'FEATURE_ID': -1, 'PROBLEM': 'bad dataset extent'}]
        for row in module.datasets[str(dataset)].rows:
            geometry = row.get('geometry')
            problem = None
            if geometry is None:
                problem = 'null geometry'
            elif getattr(geometry, 'isEmpty', False):
                problem = 'empty geometry'
            elif geometry.problem:
                problem = geometry.problem
            elif in_shapefile and geometry.shapefile_problem:
                problem = geometry.shapefile_problem
            if problem:
                problems.append({'FEATURE_ID': row.get('oid'), 'PROBLEM': problem})
        module.datasets[str(output_table)] = Dataset(
            str(output_table), 'Table', None,
            [Field('FEATURE_ID', 'Integer'), Field('PROBLEM')], problems,
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
        MultipartToSinglepart=multipart_to_singlepart,
    )

    def features_to_json(source, output, *options):
        module.calls.append(('FeaturesToJSON', str(source)) + tuple(options))
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
            # GeoJSON is requested explicitly so these tests still cover that package
            'GIUM point labels', self.point_target, self.point_layer,
            '', 'Arrowhead', 'Spring migration', 'GeoJSON', '',
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

    def read_qa(self, path):
        with open(path, encoding='utf-8-sig') as stream:
            return list(csv.DictReader(stream))

    def qa_row(self, rows, check, section=None):
        for row in rows:
            if row['check'] == check and (section is None or row['section'] == section):
                return row
        self.fail('%s not in QA report' % check)

    def failure_report(self):
        return self.release / 'GIUMIntegration_August4_2026_QA_FAILED.csv'

    def release_files(self):
        # everything in the output folder except the failure report
        return [
            path for path in self.release.glob('*')
            if path.name != self.failure_report().name
        ]

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
        self.assertFalse(self.release_files())
        self.assertEqual(len(self.arcpy.datasets[self.point_target].rows), 1)

    def test_append_failure_rolls_back_every_release_artifact(self):
        def fail_append(*unused_args, **unused_kwargs):
            raise RuntimeError('simulated append failure')

        self.arcpy.management.Append = fail_append
        with self.assertRaisesRegex(RuntimeError, 'simulated append failure'):
            self.run_tool()

        self.assertTrue(self.release.is_dir())
        self.assertEqual(list(self.release.iterdir()), [self.failure_report()])
        self.assertEqual(len(self.arcpy.datasets[self.line_target].rows), 1)
        self.assertEqual(len(self.arcpy.datasets[self.point_target].rows), 1)

    def test_geojson_failure_rolls_back_staged_line_and_point_outputs(self):
        def fail_geojson(*unused_args, **unused_kwargs):
            raise RuntimeError('simulated GeoJSON failure')

        self.arcpy.conversion.FeaturesToJSON = fail_geojson
        with self.assertRaisesRegex(RuntimeError, 'simulated GeoJSON failure'):
            self.run_tool()

        self.assertTrue(self.release.is_dir())
        self.assertEqual(list(self.release.iterdir()), [self.failure_report()])

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
        with self.assertRaisesRegex(ValueError, 'multipart.*Split multipart features'):
            self.run_tool()
        self.assertFalse(self.release.exists() and any(self.release.iterdir()))
        self.assertNotIn('MultipartToSinglepart', {call[0] for call in self.arcpy.calls})

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


    def polygon_dataset(self):
        root = pathlib.Path(self.temp.name)
        poly_target = str(root / 'poly_features_old.shp')
        poly_source = str(root / 'source_polys')
        historical = {
            'oid': 1, 'geometry': Geometry(), 'Type': 'Migration land', 'Class': 'Range',
            'Herd_Name': 'Old', 'Country': 'Canada',
        }
        selected = {
            'oid': 2, 'geometry': Geometry(), 'Type': '', 'Class': '',
            'Herd_Name': '', 'Country': '',
        }
        self.arcpy.datasets[poly_target] = Dataset(
            poly_target, 'Polygon', self.sr, production_fields('Polygon'),
            [historical, dict(historical, oid=2, Class='Calving ground')],
        )
        self.arcpy.datasets[poly_source] = Dataset(
            poly_source, 'Polygon', self.source_sr, production_fields('Polygon'), [selected]
        )
        return poly_target, poly_source

    def staged_release(self, target):
        for call in self.arcpy.calls:
            if call[0] == 'CopyFeatures' and call[1] == target:
                return self.arcpy.datasets[call[2]]
        self.fail('%s was never copied' % target)

    def use_scratch_folder(self):
        scratch = pathlib.Path(self.temp.name) / 'scratch'
        scratch.mkdir(exist_ok=True)
        self.arcpy.env.scratchFolder = str(scratch)
        return scratch

    def use_different_datum(self):
        self.source_sr = SpatialReference(4269, gcs=4269)
        for name in (self.line_layer, self.line_source):
            self.arcpy.datasets[name].spatial_reference = self.source_sr

    def test_clean_release_reports_a_plain_pass(self):
        result = self.run_tool()
        rows = self.read_qa(result.qa_csv)
        self.assertEqual(self.qa_row(rows, 'overall_result')['value'], 'PASS')
        self.assertEqual([row for row in rows if row['status'] != 'PASS'], [])
        self.assertEqual(self.arcpy.warnings, [])
        self.assertTrue(any('created successfully' in message for message in self.arcpy.messages))

    def test_staging_happens_in_the_scratch_folder_and_is_removed(self):
        scratch = self.use_scratch_folder()
        with mock.patch.object(
            self.tool.tempfile, 'mkdtemp', wraps=tempfile.mkdtemp
        ) as mkdtemp:
            self.run_tool()
        self.assertEqual(mkdtemp.call_count, 1)
        self.assertEqual(mkdtemp.call_args.kwargs['dir'], str(scratch))
        self.assertEqual(list(scratch.iterdir()), [])
        self.assertFalse([path for path in self.release.iterdir() if path.is_dir()])

    def test_staging_falls_back_to_the_system_temp_folder(self):
        self.arcpy.env.scratchFolder = str(pathlib.Path(self.temp.name) / 'missing')
        with mock.patch.object(
            self.tool.tempfile, 'mkdtemp', wraps=tempfile.mkdtemp
        ) as mkdtemp:
            self.run_tool(datasets=[self.point_row()])
        self.assertEqual(mkdtemp.call_args.kwargs['dir'], tempfile.gettempdir())

    def test_history_is_copied_once_straight_to_the_release_shapefile(self):
        self.use_scratch_folder()
        self.run_tool()
        for target, name in (
            (self.line_target, 'SeasonalArrowsMerged_August4_2026.shp'),
            (self.point_target, 'GIUMPointLabelsMerged_August4_2026.shp'),
        ):
            copies = [
                call for call in self.arcpy.calls
                if call[0] == 'CopyFeatures' and call[1] == target
            ]
            self.assertEqual(len(copies), 1)
            self.assertEqual(os.path.basename(copies[0][2]), name)
            self.assertFalse(copies[0][2].startswith(str(self.release)))
            # nothing copies the combined output a second time
            self.assertFalse([
                call for call in self.arcpy.calls
                if call[0] == 'CopyFeatures' and call[1] == copies[0][2]
            ])
        checked = [
            os.path.basename(call[1]) for call in self.arcpy.calls
            if call[0] == 'CheckGeometry'
        ]
        self.assertEqual(checked, [
            'row1_new_projected', 'SeasonalArrowsMerged_August4_2026.shp',
            'row2_new_projected', 'GIUMPointLabelsMerged_August4_2026.shp',
        ])

    def test_new_data_is_read_once_in_preflight_and_twice_in_staging(self):
        self.arcpy.datasets[self.line_target].fields.extend([Field('Notes'), Field('Area')])
        self.arcpy.datasets[self.line_layer].fields.extend([Field('Notes'), Field('Area')])
        self.run_tool(datasets=[self.line_row()])
        layer_reads = [
            call for call in self.arcpy.calls
            if call[0].endswith('Cursor') and call[1] == self.line_layer
        ]
        self.assertEqual(layer_reads, [('SearchCursor', self.line_layer)])
        staged_reads = sorted(
            call[0] for call in self.arcpy.calls
            if call[0].endswith('Cursor') and call[1].endswith('row1_new_projected')
        )
        self.assertEqual(staged_reads, ['SearchCursor', 'UpdateCursor'])

    def test_geojson_is_written_unformatted(self):
        self.run_tool(datasets=[self.point_row()])
        calls = [call for call in self.arcpy.calls if call[0] == 'FeaturesToJSON']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], 'NOT_FORMATTED')
        self.assertFalse(any('formatted' in message.lower() for message in self.arcpy.messages))

    def test_historical_geometry_problem_is_a_warning(self):
        self.arcpy.datasets[self.line_target].rows[0]['geometry'] = Geometry(
            problem='self intersections'
        )
        result = self.run_tool()
        rows = self.read_qa(result.qa_csv)
        row = self.qa_row(rows, 'historical_geometry_problems')
        self.assertEqual(row['status'], 'WARN')
        self.assertEqual(row['section'], 'Row 1 Seasonal arrows')
        self.assertEqual(row['value'], '1')
        self.assertIn('feature 0: self intersections', row['details'])
        self.assertEqual(self.qa_row(rows, 'overall_result')['value'], 'PASS with 1 warning')
        self.assertTrue(any(
            'already has 1 geometry problem' in warning for warning in self.arcpy.warnings
        ))
        self.assertTrue(any('1 warning' in message for message in self.arcpy.messages))
        self.assertEqual(len(self.arcpy.datasets[self.line_target].rows), 1)

    def test_new_geometry_problem_blocks_before_history_is_copied(self):
        self.arcpy.datasets[self.line_layer].rows[0]['geometry'] = Geometry(
            problem='self intersections'
        )
        with self.assertRaisesRegex(ValueError, 'projected new.*self intersections'):
            self.run_tool()
        self.assertFalse([
            call for call in self.arcpy.calls
            if call[0] == 'CopyFeatures' and call[1] == self.line_target
        ])
        self.assertFalse(self.release_files())

    def test_new_geometry_problem_in_the_release_blocks(self):
        self.arcpy.datasets[self.line_layer].rows[0]['geometry'] = Geometry(
            shapefile_problem='short segment'
        )
        with self.assertRaisesRegex(
            ValueError, 'on the new .* features after combining.*feature 1: short segment'
        ):
            self.run_tool()
        self.assertFalse(self.release_files())

    def test_historical_problem_blocks_when_new_features_cannot_be_identified(self):
        self.arcpy.datasets[self.line_target].rows[0]['geometry'] = Geometry(
            problem='self intersections'
        )
        original_append = self.arcpy.management.Append

        def append_out_of_order(inputs, target, *args):
            original_append(inputs, target, *args)
            self.arcpy.datasets[str(target)].rows.reverse()

        self.arcpy.management.Append = append_out_of_order
        with self.assertRaisesRegex(ValueError, 'after combining.*feature 0'):
            self.run_tool(datasets=[self.line_row()])
        self.assertTrue(any('Could not confirm' in warning for warning in self.arcpy.warnings))

    def test_split_option_splits_only_the_staged_copy(self):
        # a production ORIG_FID must not be filled from the staging split
        self.arcpy.datasets[self.line_target].fields.append(Field('ORIG_FID', 'Integer'))
        self.arcpy.datasets[self.line_layer].rows[0]['geometry'] = Geometry(part_count=2)
        result = self.run_tool(datasets=[self.line_row()], split_multipart=True)

        splits = [call for call in self.arcpy.calls if call[0] == 'MultipartToSinglepart']
        self.assertEqual(len(splits), 1)
        self.assertTrue(splits[0][1].endswith('row1_new_source'))
        self.assertEqual(len(self.arcpy.datasets[self.line_layer].rows), 1)
        self.assertEqual(self.arcpy.datasets[self.line_layer].rows[0]['geometry'].partCount, 2)

        release = self.staged_release(self.line_target)
        self.assertEqual(len(release.rows), 3)
        self.assertEqual([row.get('ORIG_FID') for row in release.rows[1:]], [None, None])
        self.assertEqual(
            [field.name for field in release.fields],
            [field.name for field in self.arcpy.datasets[self.line_target].fields],
        )
        rows = self.read_qa(result.qa_csv)
        self.assertEqual(self.qa_row(rows, 'new_count_before_split')['value'], '1')
        self.assertEqual(self.qa_row(rows, 'new_count')['value'], '2')
        self.assertEqual(self.qa_row(rows, 'final_count')['value'], '3')
        split_row = self.qa_row(rows, 'multipart_split')
        self.assertEqual((split_row['status'], split_row['value']), ('WARN', '2'))
        unfilled = self.qa_row(rows, 'unfilled_target_fields')
        self.assertEqual(unfilled['value'], 'ORIG_FID')
        self.assertEqual(self.qa_row(rows, 'split_multipart', 'Release')['value'], 'Yes')

    def test_split_option_is_skipped_when_nothing_is_multipart(self):
        self.run_tool(split_multipart=True)
        self.assertNotIn('MultipartToSinglepart', {call[0] for call in self.arcpy.calls})

    def test_missing_datum_transformation_fails_and_mentions_the_option(self):
        self.use_different_datum()
        with self.assertRaisesRegex(
            ValueError,
            'no geographic transformation.*Project without a datum transformation',
        ):
            self.run_tool(datasets=[self.line_row()])
        self.assertFalse(self.release.exists())

    def test_project_without_transformation_option_warns(self):
        self.use_different_datum()
        result = self.run_tool(
            datasets=[self.line_row()], project_without_transformation=True
        )
        row = self.qa_row(self.read_qa(result.qa_csv), 'geographic_transformation')
        self.assertEqual((row['status'], row['value']), ('WARN', 'None available'))
        self.assertIn('Positions may shift', row['details'])
        projects = [call for call in self.arcpy.calls if call[0] == 'Project']
        self.assertEqual(projects[0][2], '')
        self.assertTrue(any('Positions may shift' in warning for warning in self.arcpy.warnings))

    def test_automatic_transformation_is_a_warning(self):
        self.arcpy.ListTransformations = lambda source, target, extent: ['BEST', 'SECOND']
        result = self.run_tool(datasets=[self.line_row()])
        row = self.qa_row(self.read_qa(result.qa_csv), 'geographic_transformation')
        self.assertEqual((row['status'], row['value']), ('WARN', 'BEST'))
        projects = [call for call in self.arcpy.calls if call[0] == 'Project']
        self.assertEqual(projects[0][2], 'BEST')

    def test_requested_transformation_passes(self):
        self.arcpy.ListTransformations = lambda source, target, extent: ['BEST', 'SECOND']
        result = self.run_tool(datasets=[self.line_row(transformation='second')])
        row = self.qa_row(self.read_qa(result.qa_csv), 'geographic_transformation')
        self.assertEqual((row['status'], row['value']), ('PASS', 'SECOND'))

    def test_typed_class_and_type_are_spellchecked_with_one_target_read(self):
        poly_target, poly_source = self.polygon_dataset()
        result = self.run_tool(datasets=[[
            'Polygon features', poly_target, poly_source,
            'Rnage', 'Migration lands', '', '', '',
        ]])
        rows = self.read_qa(result.qa_csv)
        class_row = self.qa_row(rows, 'class_spellcheck')
        self.assertEqual((class_row['status'], class_row['value']), ('WARN', 'Rnage'))
        self.assertIn('Existing values are: Range, Calving ground', class_row['details'])
        type_row = self.qa_row(rows, 'type_spellcheck')
        self.assertEqual(type_row['value'], 'Migration lands')
        self.assertEqual(self.qa_row(rows, 'overall_result')['value'], 'PASS with 2 warnings')
        target_reads = [
            call for call in self.arcpy.calls if call == ('SearchCursor', poly_target)
        ]
        self.assertEqual(len(target_reads), 1)
        self.assertTrue(any("'Rnage'" in warning for warning in self.arcpy.warnings))

    def test_arrow_season_comes_from_type_when_new_data_has_no_season(self):
        # new arrow lines that keep their season in Type, like the LeafRiver lines
        fields = [field for field in production_fields('Polyline') if field.name != 'Season']
        fields.append(Field('Type'))
        for name in (self.line_source, self.line_layer):
            dataset = self.arcpy.datasets[name]
            dataset.fields = list(fields)
            for row in dataset.rows:
                row.pop('Season', None)
                row['Type'] = 'Fall migration'
        result = self.run_tool(datasets=[self.line_row(season='')])
        rows = self.read_qa(result.qa_csv)
        stand_in = self.qa_row(rows, 'season_source_field')
        self.assertEqual((stand_in['status'], stand_in['value']), ('PASS', 'Type'))
        release = next(
            dataset for path, dataset in self.arcpy.datasets.items()
            if os.path.basename(path) == 'SeasonalArrowsMerged_August4_2026.shp'
        )
        self.assertEqual([row['Season'] for row in release.rows], ['Winter', 'Fall migration'])
        checks = {row['check'] for row in rows}
        self.assertNotIn('unmapped_new_fields', checks)

    def test_point_labels_default_to_a_zip(self):
        result = self.run_tool(datasets=[self.point_row(package='')])
        self.created_named(result, 'GIUMPointLabelsMerged_August4_2026.zip')
        self.assertFalse(any(path.endswith('.geojson') for path in result.created))

    def test_blank_class_fills_from_shared_production_value(self):
        result = self.run_tool(datasets=[self.line_row(**{'class': ''})])
        rows = self.read_qa(result.qa_csv)
        autofill = self.qa_row(rows, 'class_autofill')
        self.assertEqual((autofill['status'], autofill['value']), ('PASS', 'Migration'))
        # the fake arcpy keeps the release under its staging path
        release = next(
            dataset for path, dataset in self.arcpy.datasets.items()
            if os.path.basename(path) == 'SeasonalArrowsMerged_August4_2026.shp'
        )
        self.assertEqual([row['Class'] for row in release.rows], ['Migration', 'Migration'])
        self.assertEqual(self.qa_row(rows, 'overall_result')['value'], 'PASS')

    def test_blank_class_is_not_filled_when_production_values_differ(self):
        historical = self.arcpy.datasets[self.line_target].rows[0]
        self.arcpy.datasets[self.line_target].rows.append(
            dict(historical, oid=2, Class='Stopover')
        )
        with self.assertRaisesRegex(ValueError, 'blank Class and no class value'):
            self.run_tool(datasets=[self.line_row(**{'class': ''})])

    def test_known_class_is_not_flagged(self):
        poly_target, poly_source = self.polygon_dataset()
        result = self.run_tool(datasets=[[
            'Polygon features', poly_target, poly_source,
            'Range', 'Migration land', '', '', '',
        ]])
        checks = {row['check'] for row in self.read_qa(result.qa_csv)}
        self.assertFalse({'class_spellcheck', 'type_spellcheck'} & checks)

    def test_unmapped_and_unfilled_fields_are_reported(self):
        self.arcpy.datasets[self.line_layer].fields.append(Field('Observer'))
        self.arcpy.datasets[self.line_target].fields.append(Field('Region'))
        result = self.run_tool(datasets=[self.line_row()])
        rows = self.read_qa(result.qa_csv)
        unmapped = self.qa_row(rows, 'unmapped_new_fields')
        self.assertEqual((unmapped['status'], unmapped['value']), ('WARN', 'Observer'))
        unfilled = self.qa_row(rows, 'unfilled_target_fields')
        self.assertEqual((unfilled['status'], unfilled['value']), ('WARN', 'Region'))
        self.assertEqual(self.qa_row(rows, 'overall_result')['value'], 'PASS with 2 warnings')

    def test_failure_report_lists_the_error_and_rows_so_far(self):
        self.release.mkdir()
        self.arcpy.ListTransformations = lambda source, target, extent: ['BEST']
        self.arcpy.datasets[self.point_layer].rows[0]['Rotation'] = 360.0
        with self.assertRaisesRegex(ValueError, 'less than 360'):
            self.run_tool()
        self.assertFalse(self.release_files())
        rows = self.read_qa(self.failure_report())
        final = self.qa_row(rows, 'overall_result')
        self.assertEqual(final['status'], 'FAIL')
        self.assertIn('less than 360', final['value'])
        self.assertEqual(
            self.qa_row(rows, 'geographic_transformation', 'Row 2 GIUM point labels')['status'],
            'WARN',
        )
        self.qa_row(rows, 'final_count', 'Row 1 Seasonal arrows')
        self.assertTrue(any(
            self.failure_report().name in message for message in self.arcpy.messages
        ))

    def test_failed_preflight_does_not_create_the_output_folder(self):
        self.arcpy.datasets[self.line_layer].rows[0]['geometry'] = Geometry(part_count=2)
        with self.assertRaisesRegex(ValueError, 'multipart'):
            self.run_tool()
        self.assertFalse(self.release.exists())

    def test_failure_report_does_not_block_or_outlive_a_successful_run(self):
        self.release.mkdir()
        self.arcpy.datasets[self.point_layer].rows[0]['Rotation'] = 360.0
        with self.assertRaisesRegex(ValueError, 'less than 360'):
            self.run_tool()
        self.assertTrue(self.failure_report().exists())
        # a second failure replaces the report instead of stopping on it
        with self.assertRaisesRegex(ValueError, 'less than 360'):
            self.run_tool()

        self.arcpy.datasets[self.point_layer].rows[0]['Rotation'] = 32.0
        result = self.run_tool()
        self.assertFalse(self.failure_report().exists())
        self.assertTrue(os.path.isfile(result.qa_csv))

    def test_existing_release_is_not_given_a_failure_report(self):
        self.run_tool()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_tool()
        self.assertFalse(self.failure_report().exists())

    def test_failure_report_problems_never_hide_the_original_error(self):
        self.release.mkdir()
        self.arcpy.datasets[self.point_layer].rows[0]['Rotation'] = 360.0
        with mock.patch.object(self.tool, '_write_qa', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError, 'less than 360'):
                self.run_tool()
        self.assertTrue(any(
            'Could not write the failure QA report' in warning
            for warning in self.arcpy.warnings
        ))

    def test_invalid_release_date_writes_no_failure_report(self):
        self.release.mkdir()
        with self.assertRaisesRegex(ValueError, 'Release date'):
            self.run_tool(release_date='not a date')
        self.assertEqual(list(self.release.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
