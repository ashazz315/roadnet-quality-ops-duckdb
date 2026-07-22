from __future__ import annotations

import unittest

import pandas as pd

from quality_mvp.validation import validate_records


class ValidationTests(unittest.TestCase):
    def test_aliases_and_valid_types_are_normalized(self) -> None:
        frame = pd.DataFrame(
            [{"id": " A1 ", "lon": "116.4", "lat": "39.9", "severity": "HIGH", "reported_at": "2026-07-22"}]
        )
        result = validate_records(frame)
        self.assertEqual(result.missing_columns, ())
        self.assertTrue(result.errors.empty)
        self.assertEqual(result.valid.loc[0, "record_id"], "A1")
        self.assertAlmostEqual(result.valid.loc[0, "longitude"], 116.4)
        self.assertEqual(result.valid.loc[0, "severity"], "high")

    def test_invalid_rows_are_separated_with_all_reasons(self) -> None:
        frame = pd.DataFrame(
            [
                {"record_id": "OK", "longitude": 116.4, "latitude": 39.9, "severity": "low"},
                {"record_id": "BAD", "longitude": "abc", "latitude": 95, "severity": "urgent"},
                {"record_id": "DUP", "longitude": 116.4, "latitude": 39.9},
                {"record_id": "DUP", "longitude": 116.5, "latitude": 39.8},
            ]
        )
        result = validate_records(frame)
        self.assertEqual(list(result.valid["record_id"]), ["OK"])
        self.assertEqual(len(result.errors), 3)
        bad = result.errors.loc[result.errors["record_id"] == "BAD"].iloc[0]
        self.assertIn("invalid_longitude", bad["error_codes"])
        self.assertIn("out_of_range_latitude", bad["error_codes"])
        self.assertIn("invalid_severity", bad["error_codes"])
        self.assertEqual(set(result.errors.loc[result.errors["record_id"] == "DUP", "source_row"]), {4, 5})

    def test_missing_required_columns_are_reported(self) -> None:
        result = validate_records(pd.DataFrame([{"record_id": "A1"}]))
        self.assertEqual(result.missing_columns, ("longitude", "latitude"))
        self.assertTrue(result.valid.empty)
        self.assertTrue(result.errors.empty)

    def test_alias_collision_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "重复"):
            validate_records(pd.DataFrame(columns=["record_id", "longitude", "lon", "latitude"]))


if __name__ == "__main__":
    unittest.main()
