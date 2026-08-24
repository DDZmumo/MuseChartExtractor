from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.charts.validator import (
    DIFFERENCE_CATEGORIES,
    ValidationInputError,
    render_validation_markdown,
    validate_canonical_chart,
    validate_canonical_charts,
)


def _event(
    index: int,
    time: str,
    type_id: int,
    *,
    duration: str | None = None,
    end: str | None = None,
    type_status: str = "known",
    long_press_ends: int = 0,
) -> dict:
    return {
        "index": index,
        "time_sec": time,
        "duration_sec": duration,
        "end_time_sec": end,
        "type_id": type_id,
        "type_name": None if type_status == "unknown" else f"Type{type_id}",
        "type_status": type_status,
        "is_air": None,
        "extra": {"long_press_end_record_count": long_press_ends},
        "raw": {
            "base_raw_record_index": index + 1,
            "raw_record_indices": [index + 1],
        },
    }


def _chart(chart_id: str, bundle_sha256: str, bundle: str = "data/chart.bundle") -> dict:
    events = [
        _event(0, "0.25", 1),
        _event(1, "1.0", 3, duration="2.5", end="3.5", long_press_ends=1),
        _event(2, "4", 99, type_status="unknown"),
    ]
    return {
        "schema_version": "1.1.0",
        "chart_id": chart_id,
        "song": {"song_id": f"song-{chart_id}"},
        "difficulty": {"difficulty_id": 1},
        "source": {
            "bundle": bundle,
            "bundle_sha256": bundle_sha256,
            "container_path": f"Assets/{chart_id}.asset",
            "path_id": 42,
            "object_type": "MonoBehaviour",
            "payload_sha256": "b" * 64,
            "game_fingerprint": "sha256:fixture",
            "catalog_sha256": "c" * 64,
            "raw": {},
        },
        "timing": {"unit": "seconds"},
        "event_count": len(events),
        "events": events,
        "validation_status": "unvalidated",
        "canonicalization_status": "canonicalized-with-raw-evidence",
        "warnings": [],
        "raw": {
            "layout": {
                "strategy": "single-raw-record-table-v1",
                "raw_record_table": "raw.experimental_chart.raw_records",
                "event_record_references": "events[].raw.raw_record_indices",
                "omitted_derived_fields": [
                    "raw.experimental_chart.logical_objects"
                ],
            },
            "experimental_chart": {
                "raw_record_count": 4,
                "raw_records": [{"index": index} for index in range(4)],
                "record_groups": [
                    {
                        "role_status": "observed-sentinel",
                        "raw_record_indices": [0],
                    },
                    *[
                        {
                            "role_status": "logical-gameplay-object",
                            "base_raw_record_index": index,
                            "raw_record_indices": [index],
                        }
                        for index in range(1, 4)
                    ],
                ],
                "logical_object_count": 3,
                "grouping": {"observed_sentinel_count": 1},
            }
        },
    }


def _issue_codes(report: dict) -> set[str]:
    return {row["code"] for row in report["structural"]["errors"]}


class CanonicalValidatorTests(unittest.TestCase):
    def test_multiple_charts_require_verified_sources_and_matching_aggregate_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            bundle = game_dir / "data" / "chart.bundle"
            bundle.parent.mkdir()
            bundle.write_bytes(b"synthetic-unity-bundle-placeholder")
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            charts = [_chart("fixture_map1", digest), _chart("fixture_map2", digest)]
            references = [
                {
                    "chart_id": "fixture_map1",
                    "expected_combo": 3,
                    "source": {"kind": "synthetic-independent-count"},
                },
                {
                    "chart_id": "fixture_map2",
                    "expected_combo": 3,
                    "source": {"kind": "synthetic-independent-count"},
                },
            ]

            report = validate_canonical_charts(
                charts,
                game_dir=game_dir,
                references=references,
            )

        self.assertEqual(report["status"], "partially-validated-multiple-charts")
        self.assertEqual(report["milestone_status"], "M7-achieved")
        self.assertEqual(report["summary"]["chart_count"], 2)
        self.assertEqual(report["summary"]["source_verified_count"], 2)
        self.assertEqual(report["summary"]["reference_matched_count"], 2)
        for chart_report in report["charts"]:
            self.assertEqual(chart_report["reference"]["scope"], "aggregate_combo_only")
            self.assertEqual(
                chart_report["semantic"]["add_combo_projection"]["projected_combo"],
                3,
            )
            self.assertEqual(
                set(chart_report["differences"]), set(DIFFERENCE_CATEGORIES)
            )
            self.assertTrue(
                all(
                    difference["status"] == "not_compared"
                    for difference in chart_report["differences"].values()
                )
            )

    def test_decimal_time_duration_end_order_and_index_are_strict(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["events"][0]["time_sec"] = 0.25
        chart["events"][1]["index"] = 7
        chart["events"][1]["time_sec"] = "-1"
        chart["events"][1]["duration_sec"] = "-2"
        chart["events"][1]["end_time_sec"] = "-4"
        chart["events"][2]["time_sec"] = "NaN"

        report = validate_canonical_chart(chart)
        codes = _issue_codes(report)

        self.assertFalse(report["structural"]["valid"])
        self.assertIn("invalid_decimal_text", codes)
        self.assertIn("event_index_mismatch", codes)
        self.assertIn("negative_duration", codes)
        self.assertIn("end_before_start", codes)
        self.assertIn("duration_end_mismatch", codes)
        warning_codes = {
            row["code"] for row in report["semantic"]["warnings"]
        }
        self.assertIn("negative_raw_time_preserved", warning_codes)

    def test_negative_finite_raw_time_is_preserved_as_a_warning(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["events"][0]["time_sec"] = "-0.482"

        report = validate_canonical_chart(chart)
        warning_codes = {
            row["code"] for row in report["semantic"]["warnings"]
        }

        self.assertTrue(report["structural"]["valid"])
        self.assertIn("negative_raw_time_preserved", warning_codes)

    def test_descending_time_and_incomplete_duration_pair_are_rejected(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["events"][1]["time_sec"] = "5"
        chart["events"][1]["duration_sec"] = None
        chart["events"][1]["end_time_sec"] = "6"
        chart["events"][2]["time_sec"] = "4"

        report = validate_canonical_chart(chart)
        codes = _issue_codes(report)

        self.assertIn("incomplete_duration_pair", codes)
        self.assertIn("event_order_descends", codes)

    def test_unknown_types_are_legal_and_reported_as_warnings(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)

        report = validate_canonical_chart(chart)
        warning_codes = {
            row["code"] for row in report["semantic"]["warnings"]
        }

        self.assertTrue(report["structural"]["valid"])
        self.assertEqual(report["semantic"]["unknown_type_count"], 1)
        self.assertEqual(report["semantic"]["unknown_type_ratio"], "0.333333")
        self.assertIn("unknown_types_preserved", warning_codes)
        self.assertEqual(report["semantic"]["type_distribution"]["99"], 1)

    def test_raw_accounting_mismatch_is_a_structural_failure(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["raw"]["experimental_chart"]["raw_record_count"] = 99

        report = validate_canonical_chart(chart)

        self.assertFalse(report["structural"]["raw_accounting"]["accounted"])
        self.assertIn("raw_record_accounting_mismatch", _issue_codes(report))

    def test_raw_accounting_requires_exact_index_set_not_only_equal_counts(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["events"][2]["raw"]["raw_record_indices"][0] = 4

        report = validate_canonical_chart(chart)
        accounting = report["structural"]["raw_accounting"]

        self.assertFalse(accounting["accounted"])
        self.assertEqual(accounting["referenced_event_raw_record_count"], 3)
        self.assertEqual(accounting["observed_sentinel_raw_record_count"], 1)
        self.assertEqual(accounting["missing_index_count"], 1)
        self.assertEqual(accounting["missing_index_sample"], [3])
        self.assertEqual(accounting["extra_index_count"], 1)
        self.assertEqual(accounting["extra_index_sample"], [4])
        self.assertIn("raw_record_index_set_mismatch", _issue_codes(report))
        self.assertIn("event_record_group_reference_mismatch", _issue_codes(report))

    def test_event_payload_duplication_is_a_structural_failure(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["events"][0]["raw"]["music_data_records"] = [
            {"index": 1, "fields": {}}
        ]

        report = validate_canonical_chart(chart)

        self.assertFalse(report["structural"]["raw_accounting"]["accounted"])
        self.assertIn("duplicated_event_raw_payload", _issue_codes(report))

    def test_source_hash_mismatch_is_reported_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            bundle = game_dir / "data" / "chart.bundle"
            bundle.parent.mkdir()
            original = b"unchanged-source"
            bundle.write_bytes(original)
            chart = _chart("fixture_map1", "0" * 64)

            report = validate_canonical_chart(chart, game_dir=game_dir)

            self.assertEqual(bundle.read_bytes(), original)
        self.assertEqual(
            report["structural"]["source"]["status"], "sha256_mismatch"
        )
        self.assertIn("source_sha256_mismatch", _issue_codes(report))

    def test_reference_mismatch_does_not_claim_event_level_comparison(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)

        chart_report = validate_canonical_chart(
            chart,
            reference={"chart_id": "fixture_map1", "expected_combo": 999},
        )
        batch_report = validate_canonical_charts(
            [chart],
            references=[{"chart_id": "fixture_map1", "expected_combo": 999}],
        )
        markdown = render_validation_markdown(batch_report)

        self.assertEqual(chart_report["reference"]["status"], "mismatch")
        self.assertEqual(chart_report["reference"]["delta"], -996)
        self.assertEqual(chart_report["comparison_scope"]["event_level"], "not_compared")
        self.assertEqual(batch_report["milestone_status"], "M7-not-achieved")
        for category in DIFFERENCE_CATEGORIES:
            self.assertIn(f"| fixture_map1 | {category} | not_compared |", markdown)
        self.assertIn("no event-level reference was compared", markdown)

    def test_complete_indexed_event_reference_compares_explicit_fields(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["events"][0]["is_air"] = False
        chart["events"][1]["is_air"] = True
        chart["events"][2]["is_air"] = False
        reference = {
            "chart_id": "fixture_map1",
            "expected_combo": 3,
            "event_reference": {
                "schema_version": "event-reference-v1",
                "scope": "complete-indexed-sequence",
                "source": {"kind": "synthetic-independent-event-stream"},
                "time_tolerance_sec": "0.01",
                "duration_tolerance_sec": "0.001",
                "events": [
                    {
                        "index": 0,
                        "time_sec": "0.251",
                        "type_id": 1,
                        "is_air": False,
                        "duration_sec": None,
                    },
                    {
                        "index": 1,
                        "time_sec": "1.0",
                        "type_id": 3,
                        "is_air": True,
                        "duration_sec": "2.5005",
                    },
                    {
                        "index": 2,
                        "time_sec": "4",
                        "type_id": 99,
                        "is_air": False,
                        "duration_sec": None,
                    },
                ],
            },
        }

        report = validate_canonical_chart(chart, reference=reference)

        self.assertEqual(report["event_reference"]["status"], "matched")
        self.assertEqual(
            report["event_reference"]["compared_fields"],
            ["time_sec", "type_id", "is_air", "duration_sec"],
        )
        self.assertEqual(report["differences"]["matched"]["count"], 3)
        for category in DIFFERENCE_CATEGORIES[1:]:
            self.assertEqual(report["differences"][category]["status"], "compared")
            self.assertEqual(report["differences"][category]["count"], 0)
        self.assertEqual(report["comparison_scope"]["event_level"], "matched")

    def test_event_reference_reports_each_difference_without_greedy_alignment(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        chart["events"][0]["is_air"] = False
        chart["events"][1]["is_air"] = True
        chart["events"][2]["is_air"] = False
        reference = {
            "chart_id": "fixture_map1",
            "event_reference": {
                "schema_version": "event-reference-v1",
                "scope": "complete-indexed-sequence",
                "source": {"kind": "synthetic-independent-event-stream"},
                "time_tolerance_sec": "0.01",
                "duration_tolerance_sec": "0.01",
                "events": [
                    {
                        "index": 0,
                        "time_sec": "0.5",
                        "type_id": 2,
                        "is_air": True,
                        "duration_sec": None,
                    },
                    {
                        "index": 1,
                        "time_sec": "1.0",
                        "type_id": 3,
                        "is_air": True,
                        "duration_sec": "3.0",
                    },
                    {"index": 2, "time_sec": "4"},
                    {"index": 3, "time_sec": "5"},
                ],
            },
        }

        report = validate_canonical_chart(chart, reference=reference)
        differences = report["differences"]

        self.assertEqual(report["event_reference"]["status"], "mismatch")
        self.assertEqual(differences["matched"]["count"], 1)
        self.assertEqual(differences["missing_offline"]["count"], 1)
        self.assertEqual(differences["extra_offline"]["count"], 0)
        self.assertEqual(differences["timing_delta"]["count"], 1)
        self.assertEqual(differences["type_mismatch"]["count"], 1)
        self.assertEqual(differences["lane_mismatch"]["count"], 1)
        self.assertEqual(differences["duration_delta"]["count"], 1)
        self.assertEqual(
            report["status"], "structurally-valid-event-reference-mismatch"
        )

    def test_shorter_event_reference_reports_extra_offline_events(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        reference = {
            "chart_id": "fixture_map1",
            "event_reference": {
                "schema_version": "event-reference-v1",
                "scope": "complete-indexed-sequence",
                "source": {"kind": "synthetic-independent-event-stream"},
                "events": [
                    {"index": 0, "time_sec": "0.25"},
                    {"index": 1, "time_sec": "1.0"},
                ],
            },
        }

        report = validate_canonical_chart(chart, reference=reference)

        self.assertEqual(report["differences"]["extra_offline"]["count"], 1)
        self.assertEqual(report["differences"]["missing_offline"]["count"], 0)

    def test_event_reference_requires_provenance_and_contiguous_indices(self) -> None:
        chart = _chart("fixture_map1", "a" * 64)
        reference = {
            "chart_id": "fixture_map1",
            "event_reference": {
                "schema_version": "event-reference-v1",
                "scope": "complete-indexed-sequence",
                "source": {},
                "events": [{"index": 1, "time_sec": "0.25"}],
            },
        }

        with self.assertRaisesRegex(ValidationInputError, "source"):
            validate_canonical_chart(chart, reference=reference)
        reference["event_reference"]["source"] = {"kind": "synthetic"}
        with self.assertRaisesRegex(ValidationInputError, "contiguous"):
            validate_canonical_chart(chart, reference=reference)

    def test_no_game_directory_keeps_m7_incomplete_even_when_counts_match(self) -> None:
        digest = "a" * 64
        charts = [_chart("fixture_map1", digest), _chart("fixture_map2", digest)]
        references = {
            "fixture_map1": {"expected_combo": 3},
            "fixture_map2": {"expected_combo": 3},
        }

        report = validate_canonical_charts(charts, references=references)

        self.assertEqual(report["summary"]["reference_matched_count"], 2)
        self.assertEqual(report["summary"]["source_verified_count"], 0)
        self.assertEqual(report["milestone_status"], "M7-not-achieved")
        self.assertEqual(report["status"], "validation-incomplete")


if __name__ == "__main__":
    unittest.main()
