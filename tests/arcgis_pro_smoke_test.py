'''
run from the ArcGIS Pro Python window with

    exec(open(PATH).read())

or from an authorized Python Command Prompt with

    propy tests/arcgis_pro_smoke_test.py

'''

from __future__ import annotations
import pathlib
import sys
import tempfile
import arcpy


PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "toolbox"
sys.path.insert(0, str(PACKAGE))

import arrow_creation_arcpy  # noqa: E402
import arrow_rotation_arcpy  # noqa: E402

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
        arcpy.management.CreateFeatureclass(
            geodatabase, "arrowheads", "POINT", spatial_reference=spatial_reference
        )
        arcpy.management.CreateFeatureclass(
            geodatabase, "lines", "POLYLINE", spatial_reference=spatial_reference
        )
        arcpy.management.AddField(points, "rotation_deg", "DOUBLE")
        arcpy.management.AddField(lines, "LINE_NAME", "TEXT", field_length=40)

        with arcpy.da.InsertCursor(points, ["SHAPE@XY", "rotation_deg"]) as rows:
            rows.insertRow(((10.0, 0.0), 33.0))
            rows.insertRow(((0.0, 0.0), 44.0))
            rows.insertRow(((50.0, 50.0), 77.0))
        line = arcpy.Polyline(
            arcpy.Array([arcpy.Point(0.0, 0.0), arcpy.Point(10.0, 0.0)]),
            spatial_reference,
        )
        with arcpy.da.InsertCursor(lines, ["SHAPE@", "LINE_NAME"]) as rows:
            rows.insertRow((line, "Smoke test line"))

        arrow_rotation_arcpy.execute(
            points, lines, "2 Meters", "rotation_deg", audit
        )
        rotations = [
            value for (value,) in arcpy.da.SearchCursor(
                points, ["rotation_deg"], sql_clause=(None, "ORDER BY OBJECTID")
            )
        ]
        assert rotations == [3.0, 183.0, 77.0], rotations
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
        arcpy.management.ClearWorkspaceCache()
        print("ArcGIS Pro smoke test passed")


if __name__ == "__main__":
    run()
