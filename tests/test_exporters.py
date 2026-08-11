from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.exporters import (
    CSV_COLUMNS,
    ChartExporter,
    CsvExporter,
    JsonExporter,
)


def _chart() -> dict:
    return {
        "schema_version": "1.0.0",
        "chart_id": "fixture_map1",
        "unknown_top_level": {"preserved": True},
        "events": [
            {
                "index": 0,
                "time_sec": "-0.482",
                "end_time_sec": None,
                "duration_sec": None,
                "type_id": 99,
                "type_name": None,
                "is_air": None,
                "raw": {"unknown": 7},
            },
            {
                "index": 1,
                "time_sec": "3.116883116883116883116883117",
                "end_time_sec": "4.675324675324675324675324675",
                "duration_sec": "1.558441558441558441558441558",
                "type_id": 3,
                "type_name": "Press",
                "is_air": False,
                "raw": {},
            },
        ],
    }


class ExporterTests(unittest.TestCase):
    def test_json_exporter_preserves_the_complete_mapping_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "chart.json"
            exporter = JsonExporter(indent=None)

            first = exporter.export(_chart(), output).read_bytes()
            second = exporter.export(_chart(), output).read_bytes()

            self.assertIsInstance(exporter, ChartExporter)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(first), _chart())
            self.assertTrue(json.loads(first)["unknown_top_level"]["preserved"])

    def test_csv_exporter_uses_exact_decimal_milliseconds_and_null_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "chart.csv"
            exporter = CsvExporter()
            exporter.export(_chart(), output)

            with output.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
            self.assertEqual(rows[0]["time_ms"], "-482.000")
            self.assertEqual(rows[0]["type_id"], "99")
            self.assertEqual(rows[0]["type_name"], "")
            self.assertEqual(rows[0]["is_air"], "")
            self.assertEqual(
                rows[1]["time_ms"], "3116.883116883116883116883117"
            )
            self.assertEqual(rows[1]["is_air"], "false")

    def test_exporters_fail_loudly_on_invalid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(TypeError, "CanonicalChart or mapping"):
                JsonExporter().export(object(), root / "bad.json")
            with self.assertRaisesRegex(ValueError, "events must be an array"):
                CsvExporter().export({"events": None}, root / "bad.csv")
            with self.assertRaisesRegex(ValueError, "valid decimal"):
                CsvExporter().export(
                    {
                        "events": [
                            {
                                "index": 0,
                                "time_sec": "not-a-number",
                                "end_time_sec": None,
                                "duration_sec": None,
                            }
                        ]
                    },
                    root / "bad-time.csv",
                )

    def test_csv_milliseconds_do_not_round_a_29_digit_decimal(self) -> None:
        chart = _chart()
        chart["events"][0]["time_sec"] = "79228162514264337593543950.335"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "chart.csv"
            CsvExporter().export(chart, output)
            with output.open("r", encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))

        self.assertEqual(row["time_ms"], "79228162514264337593543950335.000")


if __name__ == "__main__":
    unittest.main()
