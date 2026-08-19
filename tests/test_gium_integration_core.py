import csv
import datetime
import io
import math
import os
import pathlib
import sys
import unittest


PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "toolbox"
sys.path.insert(0, str(PACKAGE))

from gium_integration_core import (  # noqa: E402
    FieldDefinition,
    LINE_ROLE_PROFILE,
    POINT_ROLE_PROFILE,
    QA_COLUMNS,
    QARow,
    coalesce_value,
    ensure_no_artifact_collisions,
    field_definition,
    find_artifact_collisions,
    is_blank,
    qa_csv_text,
    release_artifact_names,
    release_artifact_paths,
    resolve_role_fields,
    resolved_role_value,
    select_shapefile_zip_members,
    validate_required_value,
    validate_required_values,
    validate_role_field,
    validate_rotation,
    validate_value_for_field,
)


def text_field(name, length=80):
    return FieldDefinition(name, "String", length)


class RoleResolutionTests(unittest.TestCase):
    def test_line_roles_resolve_case_insensitively(self):
        resolved = resolve_role_fields(
            [
                text_field("HERD_NAME"),
                text_field("country"),
                text_field("SEASON"),
                text_field("CLASS"),
            ],
            LINE_ROLE_PROFILE,
        )
        self.assertEqual(resolved["herd"].name, "HERD_NAME")
        self.assertEqual(resolved["country"].name, "country")
        self.assertEqual(resolved["season"].name, "SEASON")
        self.assertEqual(resolved["class"].name, "CLASS")

    def test_point_country_is_optional(self):
        resolved = resolve_role_fields(
            [
                text_field("HerdName"),
                text_field("Season"),
                text_field("TYPE"),
                FieldDefinition("Rotation", "Double"),
            ],
            POINT_ROLE_PROFILE,
        )
        self.assertIsNone(resolved["country"])

    def test_source_can_have_no_known_fields_when_required_roles_disabled(self):
        resolved = resolve_role_fields(
            [text_field("Unrelated")], POINT_ROLE_PROFILE, require_profile_roles=False
        )
        self.assertTrue(all(field is None for field in resolved.values()))

    def test_missing_required_target_role_fails_with_aliases(self):
        with self.assertRaisesRegex(ValueError, "required class.*class, Class"):
            resolve_role_fields(
                [
                    text_field("HerdName"),
                    text_field("Country"),
                    text_field("Season"),
                ],
                LINE_ROLE_PROFILE,
            )

    def test_two_accepted_aliases_are_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous.*herd"):
            resolve_role_fields(
                [
                    text_field("HerdName"),
                    text_field("Herd_Name"),
                    text_field("Country"),
                    text_field("Season"),
                    text_field("Class"),
                ],
                LINE_ROLE_PROFILE,
            )

    def test_case_only_duplicate_is_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous.*type"):
            resolve_role_fields(
                [text_field("Type"), text_field("TYPE")],
                POINT_ROLE_PROFILE,
                require_profile_roles=False,
            )

    def test_field_like_arcgis_object_is_copied(self):
        field = type("Field", (), {"name": "Season", "type": "String", "length": 25})()
        self.assertEqual(field_definition(field), FieldDefinition("Season", "String", 25))

    def test_invalid_field_like_object_fails(self):
        with self.assertRaisesRegex(ValueError, "name and ArcGIS field type"):
            field_definition(object())


class MetadataPolicyTests(unittest.TestCase):
    def test_blank_semantics_preserve_zero_and_false(self):
        for value in (None, "", " \t\n"):
            self.assertTrue(is_blank(value))
        for value in (0, 0.0, False, "0", " no "):
            self.assertFalse(is_blank(value))

    def test_source_value_wins_and_blank_source_uses_fallback(self):
        self.assertEqual(coalesce_value("Fall migration", "Spring migration"), "Fall migration")
        self.assertEqual(coalesce_value("  ", "Spring migration"), "Spring migration")
        self.assertEqual(coalesce_value(0, 90), 0)
        self.assertIs(coalesce_value(False, True), False)

    def test_required_helpers_report_one_or_all_missing_roles(self):
        with self.assertRaisesRegex(ValueError, "Row 7 is missing required herd"):
            validate_required_value("herd", " ", "row 7")
        with self.assertRaisesRegex(ValueError, "herd, season"):
            validate_required_values(
                {"herd": None, "season": "", "type": "Arrowhead"},
                ("herd", "season", "type"),
                "row 7",
            )

    def test_text_role_and_numeric_rotation_field_contracts(self):
        self.assertEqual(validate_role_field("herd", text_field("HerdName")).type, "String")
        self.assertEqual(
            validate_role_field("rotation", FieldDefinition("Rotation", "Double")).type,
            "Double",
        )
        with self.assertRaisesRegex(ValueError, "Herd.*must be Text"):
            validate_role_field("herd", FieldDefinition("HerdName", "Integer"))
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            validate_role_field("rotation", text_field("Rotation"))

    def test_text_length_and_type_are_not_silently_coerced(self):
        field = text_field("Season", 6)
        self.assertEqual(validate_value_for_field("Spring", field, "season"), "Spring")
        with self.assertRaisesRegex(ValueError, "7 characters.*allows 6"):
            validate_value_for_field("Spring!", field, "season")
        with self.assertRaisesRegex(ValueError, "must be text"):
            validate_value_for_field(2026, field, "season")

    def test_numeric_types_reject_bool_fractional_integer_and_nonfinite_float(self):
        integer = FieldDefinition("Count", "Integer")
        floating = FieldDefinition("Rotation", "Double")
        self.assertEqual(validate_value_for_field(4, integer), 4)
        self.assertEqual(validate_value_for_field(4.5, floating), 4.5)
        for value in (True, 4.5):
            with self.assertRaises(ValueError):
                validate_value_for_field(value, integer)
        for value in (False, math.inf, math.nan):
            with self.assertRaises(ValueError):
                validate_value_for_field(value, floating)

    def test_blank_field_value_can_be_allowed_or_rejected(self):
        field = text_field("Country")
        self.assertIsNone(validate_value_for_field(None, field))
        with self.assertRaisesRegex(ValueError, "Country cannot be blank"):
            validate_value_for_field(None, field, allow_blank=False)

    def test_rotation_range(self):
        self.assertEqual(validate_rotation(0), 0.0)
        self.assertEqual(validate_rotation(359.999), 359.999)
        for invalid in (-0.01, 360, math.inf, math.nan, True, "90"):
            with self.assertRaises(ValueError, msg=repr(invalid)):
                validate_rotation(invalid)

    def test_resolved_role_value_applies_precedence_and_validation(self):
        target = text_field("Herd_Name", 40)
        self.assertEqual(
            resolved_role_value("herd", "Red Desert", "Fallback", target),
            "Red Desert",
        )
        self.assertEqual(
            resolved_role_value("herd", " ", "Red Desert", target),
            "Red Desert",
        )
        with self.assertRaisesRegex(ValueError, "missing required herd"):
            resolved_role_value("herd", None, "", target, context="row 12")

    def test_resolved_rotation_requires_target_type_and_range(self):
        field = FieldDefinition("Rotation", "Double")
        self.assertEqual(resolved_role_value("rotation", 0, 15, field), 0)
        with self.assertRaisesRegex(ValueError, "less than 360"):
            resolved_role_value("rotation", None, 360, field)


class ReleaseArtifactTests(unittest.TestCase):
    def test_names_use_merged_month_day_year_stamps(self):
        expected = (
            "SeasonalArrowsMerged_April9_2026.shp",
            "SeasonalArrowsMerged_April9_2026.zip",
            "GIUMPointLabelsMerged_April9_2026.shp",
            "GIUMPointLabelsMerged_April9_2026.geojson",
            "GIUMArrowIntegration_April9_2026_QA.csv",
        )
        self.assertEqual(release_artifact_names(datetime.date(2026, 4, 9)).all(), expected)
        self.assertEqual(release_artifact_names("2026-04-09").all(), expected)
        self.assertEqual(release_artifact_names("20260409").all(), expected)
        self.assertEqual(
            release_artifact_names(datetime.date(2026, 6, 10)).point_shapefile,
            "GIUMPointLabelsMerged_June10_2026.shp",
        )

    def test_invalid_calendar_date_or_format_fails(self):
        for value in ("2026-02-30", "04/09/2026", "", None):
            with self.assertRaisesRegex(ValueError, "Release date"):
                release_artifact_names(value)

    def test_paths_join_to_selected_folder(self):
        paths = release_artifact_paths("/release folder", "20260409")
        self.assertEqual(
            paths["line_zip"],
            os.path.join("/release folder", "SeasonalArrowsMerged_April9_2026.zip"),
        )
        self.assertEqual(
            paths["point_geojson"],
            os.path.join("/release folder", "GIUMPointLabelsMerged_April9_2026.geojson"),
        )
        with self.assertRaisesRegex(ValueError, "Output folder"):
            release_artifact_paths(" ", "20260409")

    def test_collision_helpers_do_not_write_or_normalize_paths(self):
        existing = {"A/SeasonalArrows.shp", "A/report.csv"}
        paths = ["A/SeasonalArrows.shp", "A/new.zip", "A/report.csv"]
        exists = lambda path: str(path) in existing
        self.assertEqual(
            find_artifact_collisions(paths, exists),
            ("A/SeasonalArrows.shp", "A/report.csv"),
        )
        with self.assertRaisesRegex(ValueError, "SeasonalArrows.*report"):
            ensure_no_artifact_collisions(paths, exists)
        ensure_no_artifact_collisions(["A/new.zip"], exists)


class QAReportTests(unittest.TestCase):
    def test_csv_has_stable_columns_and_escapes_values(self):
        text = qa_csv_text(
            [
                QARow("counts", "new features", "PASS", 12),
                QARow("fields", "herd", "PASS", None, "Herd_Name, source"),
            ]
        )
        reader = csv.DictReader(io.StringIO(text))
        self.assertEqual(tuple(reader.fieldnames), QA_COLUMNS)
        rows = list(reader)
        self.assertEqual(rows[0]["value"], "12")
        self.assertEqual(rows[1]["value"], "")
        self.assertEqual(rows[1]["details"], "Herd_Name, source")
        self.assertNotIn("\r\n", text)

    def test_csv_rejects_unstructured_rows(self):
        with self.assertRaisesRegex(TypeError, "QARow"):
            qa_csv_text([{"section": "counts"}])


class ShapefilePackageTests(unittest.TestCase):
    def test_required_and_optional_sidecars_are_selected_in_stable_order(self):
        paths = [
            "/out/unrelated.dbf",
            "/out/SeasonalArrowsMerged_April9_2026.cpg",
            "/out/SeasonalArrowsMerged_April9_2026.shp.xml",
            "/out/SeasonalArrowsMerged_April9_2026.dbf",
            "/out/SeasonalArrowsMerged_April9_2026.prj",
            "/out/SeasonalArrowsMerged_April9_2026.shp",
            "/out/SeasonalArrowsMerged_April9_2026.shx",
            "/out/SeasonalArrowsMerged_April9_2026.sbx",
        ]
        selected = select_shapefile_zip_members(
            paths, "SeasonalArrowsMerged_April9_2026.shp"
        )
        self.assertEqual(
            [pathlib.Path(path).suffix for path in selected[:5]],
            [".shp", ".shx", ".dbf", ".prj", ".cpg"],
        )
        self.assertTrue(selected[-2].endswith(".sbx"))
        self.assertTrue(selected[-1].endswith(".shp.xml"))

    def test_case_insensitive_names_are_supported(self):
        selected = select_shapefile_zip_members(
            ["/out/A.SHP", "/out/A.SHX", "/out/A.DBF", "/out/A.PRJ"],
            "a.shp",
        )
        self.assertEqual(len(selected), 4)

    def test_missing_required_sidecar_fails(self):
        with self.assertRaisesRegex(ValueError, "A.prj"):
            select_shapefile_zip_members(
                ["A.shp", "A.shx", "A.dbf"],
                "A.shp",
            )

    def test_duplicate_sidecar_fails(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            select_shapefile_zip_members(
                ["/one/A.shp", "/two/A.shp", "A.shx", "A.dbf", "A.prj"],
                "A.shp",
            )

    def test_non_shapefile_name_fails(self):
        with self.assertRaisesRegex(ValueError, "end in .shp"):
            select_shapefile_zip_members([], "A.geojson")


if __name__ == "__main__":
    unittest.main()
