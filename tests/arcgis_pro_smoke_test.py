'''
run from the ArcGIS Pro Python window with

    exec(open(PATH).read())

or from an authorized Python Command Prompt with

    propy tests/arcgis_pro_smoke_test.py

'''

from __future__ import annotations
import csv
import datetime
import json
import pathlib
import sys
import tempfile
import zipfile
import arcpy


PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "toolbox"
sys.path.insert(0, str(PACKAGE))

import arrow_creation_arcpy  # noqa: E402
import arrow_rotation_arcpy  # noqa: E402
import gium_integration_arcpy  # noqa: E402


def _count(dataset: str) -> int:
    '''get an ArcGIS feature count as an integer'''

    return int(arcpy.management.GetCount(dataset)[0])


def _field_names(dataset: str) -> set[str]:
    '''get user field names without ArcGIS geometry fields'''

    return {
        field.name.lower()
        for field in arcpy.ListFields(dataset)
        if not field.required
        and field.name.lower() not in {"shape_length", "shape_leng", "shape_area"}
    }


def _result_path(result, name: str) -> pathlib.Path:
    '''read an integration output from either result format'''

    if isinstance(result, dict):
        value = result[name]
    else:
        value = getattr(result, name)
    return pathlib.Path(value)


def _add_text_fields(dataset: str, fields: list[tuple[str, int]]) -> None:
    '''add several text fields to disposable smoke-test data'''

    for name, length in fields:
        arcpy.management.AddField(dataset, name, "TEXT", field_length=length)


def _run_gium_integration_smoke(folder: str, geodatabase: str) -> None:
    '''exercise the complete GIUM release workflow with disposable data'''

    target_sr = arcpy.SpatialReference(3857)
    source_sr = arcpy.SpatialReference(4326)

    target_folder = pathlib.Path(folder) / "gium_targets"
    target_folder.mkdir()
    line_target = str(target_folder / "seasonal_target.shp")
    new_lines = str(pathlib.Path(geodatabase) / "new_seasonal_lines")
    point_target = str(target_folder / "point_label_target.shp")
    new_points = str(pathlib.Path(geodatabase) / "new_arrowheads")

    arcpy.management.CreateFeatureclass(
        str(target_folder), "seasonal_target.shp", "POLYLINE", spatial_reference=target_sr
    )
    _add_text_fields(
        line_target,
        [("HerdName", 80), ("Country", 60), ("Season", 50), ("Class", 30)],
    )
    arcpy.management.AddField(line_target, "ExistingID", "LONG")

    arcpy.management.CreateFeatureclass(
        geodatabase, "new_seasonal_lines", "POLYLINE", spatial_reference=source_sr
    )
    _add_text_fields(
        new_lines,
        [("Herd_Name", 80), ("Country", 60), ("Season", 50), ("class", 30)],
    )
    arcpy.management.AddField(new_lines, "NewID", "LONG")

    arcpy.management.CreateFeatureclass(
        str(target_folder), "point_label_target.shp", "POINT", spatial_reference=target_sr
    )
    _add_text_fields(
        point_target,
        [("Herd_Name", 80), ("Season", 50), ("Type", 30)],
    )
    arcpy.management.AddField(point_target, "Rotation", "DOUBLE")
    arcpy.management.AddField(point_target, "ExistingID", "LONG")

    arcpy.management.CreateFeatureclass(
        geodatabase, "new_arrowheads", "POINT", spatial_reference=source_sr
    )
    _add_text_fields(
        new_points,
        [("HerdName", 80), ("Season", 50), ("TYPE", 30)],
    )
    arcpy.management.AddField(new_points, "Rotation", "DOUBLE")
    arcpy.management.AddField(new_points, "NewID", "LONG")
    # Part 1 tracing fields should not leak into a target that does not have them
    arcpy.management.AddField(new_points, "SOURCE_OID", "LONG")
    arcpy.management.AddField(new_points, "SOURCE_PART", "LONG")
    arcpy.management.AddField(new_points, "ENDPOINT", "TEXT", field_length=10)

    target_line = arcpy.Polyline(
        arcpy.Array([arcpy.Point(0.0, 0.0), arcpy.Point(100.0, 0.0)]),
        target_sr,
    )
    with arcpy.da.InsertCursor(
        line_target,
        ["SHAPE@", "HerdName", "Country", "Season", "Class", "ExistingID"],
    ) as rows:
        rows.insertRow(
            (target_line, "Historic herd", "Mongolia", "Winter", "Migration", 1)
        )
        rows.insertRow(
            (target_line, "Second historic herd", "Canada", "Summer", "Migration", 2)
        )

    with arcpy.da.InsertCursor(
        point_target,
        ["SHAPE@XY", "Herd_Name", "Season", "Type", "Rotation", "ExistingID"],
    ) as rows:
        rows.insertRow(
            ((0.0, 0.0), "Historic herd", "Winter", "Arrowhead", 90.0, 1)
        )
        rows.insertRow(
            ((100.0, 100.0), "Second historic herd", "Summer", "Arrowhead", 180.0, 2)
        )

    with arcpy.da.InsertCursor(
        new_lines,
        ["SHAPE@", "Herd_Name", "Country", "Season", "class", "NewID"],
    ) as rows:
        rows.insertRow(
            (
                arcpy.Polyline(
                    arcpy.Array(
                        [arcpy.Point(10.0, 45.0), arcpy.Point(10.01, 45.01)]
                    ),
                    source_sr,
                ),
                "",
                "",
                "Spring migration",
                "",
                1,
            )
        )
        rows.insertRow(
            (
                arcpy.Polyline(
                    arcpy.Array(
                        [arcpy.Point(11.0, 46.0), arcpy.Point(11.01, 46.01)]
                    ),
                    source_sr,
                ),
                "Unselected herd",
                "Canada",
                "Fall migration",
                "Migration",
                2,
            )
        )

    with arcpy.da.InsertCursor(
        new_points,
        [
            "SHAPE@XY",
            "HerdName",
            "Season",
            "TYPE",
            "Rotation",
            "NewID",
            "SOURCE_OID",
            "SOURCE_PART",
            "ENDPOINT",
        ],
    ) as rows:
        rows.insertRow(
            ((10.01, 45.01), "", "Spring migration", "", 32.0, 1, 1, 0, "END")
        )
        rows.insertRow(
            ((11.01, 46.01), "Unselected herd", "Fall migration", "Arrowhead", 48.0, 2, 2, 0, "END")
        )

    line_layer = arcpy.management.MakeFeatureLayer(
        new_lines, "gium_smoke_selected_lines", "NewID = 1"
    )[0]
    point_layer = arcpy.management.MakeFeatureLayer(
        new_points, "gium_smoke_selected_points", "NewID = 1"
    )[0]
    # select one target row to prove that all historical records are still retained
    line_target_layer = arcpy.management.MakeFeatureLayer(
        line_target, "gium_smoke_selected_line_target", "ExistingID = 1"
    )[0]
    point_target_layer = arcpy.management.MakeFeatureLayer(
        point_target, "gium_smoke_selected_point_target", "ExistingID = 1"
    )[0]

    before_line_count = _count(line_target)
    before_point_count = _count(point_target)
    before_line_fields = _field_names(line_target)
    before_point_fields = _field_names(point_target)
    release_date = datetime.datetime(2026, 1, 2)
    release_folder = str(pathlib.Path(folder) / "gium_release")
    pathlib.Path(release_folder).mkdir()

    result = gium_integration_arcpy.execute(
        True,
        line_target_layer,
        line_layer,
        "",
        True,
        point_target_layer,
        point_layer,
        "",
        "Smoke Test Herd",
        "Kazakhstan",
        "Deliberately different fallback season",
        "Migration",
        "Arrowhead",
        release_date,
        release_folder,
    )

    line_output = _result_path(result, "line_output")
    line_zip = _result_path(result, "line_zip")
    point_output = _result_path(result, "point_output")
    point_geojson = _result_path(result, "point_geojson")
    qa_csv = _result_path(result, "qa_csv")

    assert line_output.name == "SeasonalArrowsMerged_January2_2026.shp", line_output
    assert point_output.name == "GIUMPointLabelsMerged_January2_2026.shp", point_output
    assert line_zip.name == "SeasonalArrowsMerged_January2_2026.zip", line_zip
    assert point_geojson.name == "GIUMPointLabelsMerged_January2_2026.geojson", point_geojson
    assert qa_csv.name == "GIUMArrowIntegration_January2_2026_QA.csv", qa_csv
    for artifact in (line_output, line_zip, point_output, point_geojson, qa_csv):
        assert artifact.exists(), artifact

    # historical inputs stay unchanged and each selected new input adds one row
    assert _count(line_target) == before_line_count == 2
    assert _count(point_target) == before_point_count == 2
    assert _field_names(line_target) == before_line_fields
    assert _field_names(point_target) == before_point_fields
    assert _count(str(line_output)) == 3
    assert _count(str(point_output)) == 3
    assert _field_names(str(line_output)) == before_line_fields
    assert _field_names(str(point_output)) == before_point_fields
    assert arcpy.Describe(str(line_output)).spatialReference.factoryCode == 3857
    assert arcpy.Describe(str(point_output)).spatialReference.factoryCode == 3857

    line_values = list(
        arcpy.da.SearchCursor(
            str(line_output), ["HerdName", "Country", "Season", "Class"]
        )
    )
    assert (
        "Smoke Test Herd",
        "Kazakhstan",
        "Spring migration",
        "Migration",
    ) in line_values, line_values
    point_values = list(
        arcpy.da.SearchCursor(
            str(point_output), ["Herd_Name", "Season", "Type", "Rotation"]
        )
    )
    assert (
        "Smoke Test Herd",
        "Spring migration",
        "Arrowhead",
        32.0,
    ) in point_values, point_values
    expected_projected = arcpy.PointGeometry(
        arcpy.Point(10.01, 45.01), source_sr
    ).projectAs(target_sr).firstPoint
    new_point_rows = list(
        arcpy.da.SearchCursor(str(point_output), ["Herd_Name", "SHAPE@XY"])
    )
    projected_xy = next(xy for herd, xy in new_point_rows if herd == "Smoke Test Herd")
    assert abs(projected_xy[0] - expected_projected.X) < 0.01, projected_xy
    assert abs(projected_xy[1] - expected_projected.Y) < 0.01, projected_xy
    assert not {"source_oid", "source_part", "endpoint"} & _field_names(
        str(point_output)
    )

    with zipfile.ZipFile(line_zip) as archive:
        members = {pathlib.PurePosixPath(name).name.lower() for name in archive.namelist()}
    assert {
        "seasonalarrowsmerged_january2_2026.shp",
        "seasonalarrowsmerged_january2_2026.shx",
        "seasonalarrowsmerged_january2_2026.dbf",
        "seasonalarrowsmerged_january2_2026.prj",
    } <= members, members

    with point_geojson.open(encoding="utf-8") as stream:
        geojson = json.load(stream)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 3
    for feature in geojson["features"]:
        x, y = feature["geometry"]["coordinates"][:2]
        assert -180.0 <= x <= 180.0 and -90.0 <= y <= 90.0, (x, y)
    new_geojson_feature = next(
        feature for feature in geojson["features"]
        if any(
            str(key).lower() in {"herd_name", "herdname"}
            and value == "Smoke Test Herd"
            for key, value in feature["properties"].items()
        )
    )
    longitude, latitude = new_geojson_feature["geometry"]["coordinates"][:2]
    assert abs(longitude - 10.01) < 0.000001, (longitude, latitude)
    assert abs(latitude - 45.01) < 0.000001, (longitude, latitude)

    with qa_csv.open(newline="", encoding="utf-8-sig") as stream:
        qa_rows = list(csv.DictReader(stream))
    assert qa_rows, "QA report is empty"
    assert {"section", "check", "status", "value", "details"} <= set(qa_rows[0])
    assert any(row["check"] == "overall_result" for row in qa_rows), qa_rows

    # a point validation failure after line staging should publish neither branch
    with arcpy.da.UpdateCursor(new_points, ["NewID", "Rotation"]) as rows:
        for row in rows:
            if row[0] == 2:
                row[1] = 360.0
                rows.updateRow(row)
    failed_point_layer = arcpy.management.MakeFeatureLayer(
        new_points, "gium_smoke_invalid_points", "NewID = 2"
    )[0]
    failed_release_folder = pathlib.Path(folder) / "gium_failed_release"
    failed_release_folder.mkdir()
    try:
        gium_integration_arcpy.execute(
            True,
            line_target,
            line_layer,
            "",
            True,
            point_target,
            failed_point_layer,
            "",
            "Smoke Test Herd",
            "Kazakhstan",
            "Spring migration",
            "Migration",
            "Arrowhead",
            datetime.datetime(2026, 1, 3),
            str(failed_release_folder),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid rotation unexpectedly produced a GIUM release")
    assert not list(failed_release_folder.glob("SeasonalArrowsMerged_January3_2026.*"))
    assert not list(failed_release_folder.glob("GIUMPointLabelsMerged_January3_2026.*"))
    assert not list(
        failed_release_folder.glob("GIUMArrowIntegration_January3_2026_QA.csv")
    )
    assert _count(line_target) == before_line_count
    assert _count(point_target) == before_point_count

    arcpy.management.Delete(line_layer)
    arcpy.management.Delete(point_layer)
    arcpy.management.Delete(line_target_layer)
    arcpy.management.Delete(point_target_layer)
    arcpy.management.Delete(failed_point_layer)

def run() -> None:
    spatial_reference = arcpy.SpatialReference(3857)
    with tempfile.TemporaryDirectory(prefix="arrow_test_") as folder:
        geodatabase = str(pathlib.Path(folder) / "smoke.gdb")
        arcpy.management.CreateFileGDB(folder, "smoke.gdb")
        points = str(pathlib.Path(geodatabase) / "arrowheads")
        lines = str(pathlib.Path(geodatabase) / "lines")
        audit = str(pathlib.Path(geodatabase) / "arrow_audit")
        created_ends = str(pathlib.Path(geodatabase) / "created_ends")
        created_both = str(pathlib.Path(geodatabase) / "created_both")
        created_custom_end = str(pathlib.Path(geodatabase) / "created_custom_end")
        created_custom_both = str(pathlib.Path(geodatabase) / "created_custom_both")
        arcpy.management.CreateFeatureclass(
            geodatabase, "arrowheads", "POINT", spatial_reference=spatial_reference
        )
        arcpy.management.CreateFeatureclass(
            geodatabase, "lines", "POLYLINE", spatial_reference=spatial_reference
        )
        arcpy.management.AddField(points, "rotation_deg", "DOUBLE")
        arcpy.management.AddField(lines, "LINE_NAME", "TEXT", field_length=40)
        arcpy.management.AddField(lines, "BOTH_ENDS", "SHORT")

        with arcpy.da.InsertCursor(points, ["SHAPE@XY", "rotation_deg"]) as rows:
            rows.insertRow(((10.0, 0.0), 33.0))
            rows.insertRow(((0.0, 0.0), 44.0))
            rows.insertRow(((50.0, 50.0), 77.0))
        line = arcpy.Polyline(
            arcpy.Array([arcpy.Point(0.0, 0.0), arcpy.Point(10.0, 0.0)]),
            spatial_reference,
        )
        with arcpy.da.InsertCursor(lines, ["SHAPE@", "LINE_NAME", "BOTH_ENDS"]) as rows:
            rows.insertRow((line, "Smoke test line", 0))

        arrow_rotation_arcpy.execute(
            points, lines, "2 Meters", "rotation_deg", audit
        )
        rotations = [
            value for (value,) in arcpy.da.SearchCursor(
                points, ["rotation_deg"], sql_clause=(None, "ORDER BY OBJECTID")
            )
        ]
        assert rotations == [0.0, 180.0, 77.0], rotations
        statuses = sorted(
            status for (status,) in arcpy.da.SearchCursor(audit, ["STATUS"])
        )
        assert statuses == ["MATCHED", "MATCHED", "UNMATCHED"], statuses

        arrow_creation_arcpy.execute(
            lines, "END", "Rotation", "3", created_ends
        )
        end_rows = list(
            arcpy.da.SearchCursor(
                created_ends,
                ["SHAPE@XY", "Rotation", "ENDPOINT", "SOURCE_PART", "LINE_NAME"],
            )
        )
        assert end_rows == [
            ((10.0, 0.0), 3.0, "END", 0, "Smoke test line")
        ], end_rows

        arrow_creation_arcpy.execute(
            lines, "BOTH", "Rotation", "3", created_both
        )
        both_rows = sorted(
            arcpy.da.SearchCursor(
                created_both, ["SHAPE@XY", "Rotation", "ENDPOINT"]
            ),
            key=lambda row: row[2],
        )
        assert both_rows == [
            ((10.0, 0.0), 3.0, "END"),
            ((0.0, 0.0), 183.0, "START"),
        ], both_rows

        arrow_creation_arcpy.execute(
            lines, "CUSTOM", "Rotation", "3", created_custom_end,
            custom_field="BOTH_ENDS",
        )
        custom_end_rows = list(
            arcpy.da.SearchCursor(
                created_custom_end, ["SHAPE@XY", "Rotation", "ENDPOINT", "BOTH_ENDS"]
            )
        )
        assert custom_end_rows == [
            ((10.0, 0.0), 3.0, "END", 0)
        ], custom_end_rows

        with arcpy.da.UpdateCursor(lines, ["BOTH_ENDS"]) as rows:
            for row in rows:
                row[0] = 1
                rows.updateRow(row)

        arrow_creation_arcpy.execute(
            lines, "CUSTOM", "Rotation", "3", created_custom_both,
            custom_field="BOTH_ENDS",
        )
        custom_both_rows = sorted(
            arcpy.da.SearchCursor(
                created_custom_both, ["SHAPE@XY", "Rotation", "ENDPOINT", "BOTH_ENDS"]
            ),
            key=lambda row: row[2],
        )
        assert custom_both_rows == [
            ((10.0, 0.0), 3.0, "END", 1),
            ((0.0, 0.0), 183.0, "START", 1),
        ], custom_both_rows

        _run_gium_integration_smoke(folder, geodatabase)

        arcpy.management.ClearWorkspaceCache()
        print("ArcGIS Pro smoke test passed")


if __name__ == "__main__":
    run()
