from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from contextlib import redirect_stdout
from pathlib import Path

from musedash_chart_extractor.batch_audit import (
    BatchAuditError,
    audit_extracted_batch,
    main,
)


def _write_fixture(root: Path) -> tuple[Path, Path]:
    output = root / "extracted"
    chart_path = output / "charts" / "song-1" / "chart-1.json"
    chart_path.parent.mkdir(parents=True)
    chart = {
        "schema_version": "1.1.0",
        "chart_id": "chart-1",
        "event_count": 1,
        "events": [
            {
                "index": 0,
                "raw": {
                    "base_raw_record_index": 1,
                    "raw_record_indices": [1, 2],
                },
            }
        ],
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
                "raw_record_count": 3,
                "raw_records": [{"index": 0}, {"index": 1}, {"index": 2}],
                "record_group_count": 2,
                "record_groups": [
                    {
                        "role_status": "observed-sentinel",
                        "base_raw_record_index": 0,
                        "raw_record_indices": [0],
                    },
                    {
                        "role_status": "logical-gameplay-object",
                        "base_raw_record_index": 1,
                        "raw_record_indices": [1, 2],
                    },
                ],
                "logical_object_count": 1,
                "grouping": {
                    "raw_record_count": 3,
                    "record_group_count": 2,
                    "logical_object_count": 1,
                    "observed_sentinel_count": 1,
                },
            }
        },
    }
    payload = (json.dumps(chart, sort_keys=True) + "\n").encode()
    chart_path.write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "game_fingerprint": "sha256:" + "a" * 64,
        "canonical_schema_version": "1.1.0",
        "phase": 9,
        "status": "complete-with-classified-outcomes",
        "milestone_status": "M8-achieved",
        "candidate_count": 1,
        "source_count": 1,
        "chart_file_count": 1,
        "event_count": 1,
        "status_counts": {"success": 1},
        "raw_parse_status_counts": {"parsed": 1},
        "canonical_status_counts": {"canonicalized-with-raw-evidence": 1},
        "complete": True,
        "phase_gate": {
            "all_candidates_classified": True,
            "all_supported_candidates_extracted": True,
            "allowed_outcomes": ["failed", "success", "uncertain"],
        },
        "charts": [
            {
                "status": "success",
                "chart_id": "chart-1",
                "event_count": 1,
                "raw_parse_status": "parsed",
                "canonical_status": "canonicalized-with-raw-evidence",
                "source": {"bundle": "fixture.bundle"},
                "output_path": "charts/song-1/chart-1.json",
                "output_byte_count": len(payload),
                "output_sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output, chart_path


def _rewrite_chart(output: Path, chart_path: Path, chart: dict) -> None:
    payload = (json.dumps(chart, sort_keys=True) + "\n").encode()
    chart_path.write_bytes(payload)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    row = manifest["charts"][0]
    row["output_byte_count"] = len(payload)
    row["output_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )


class ExtractedBatchAuditTests(unittest.TestCase):
    def test_accepts_manifest_hashes_and_index_only_event_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, _chart_path = _write_fixture(root)
            report = audit_extracted_batch(output)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = main(
                    ("--output-dir", str(output), "--report", str(root / "audit.json"))
                )

            self.assertEqual(status, 0)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["audited_chart_file_count"], 1)
            self.assertEqual(report["audited_raw_record_count"], 3)
            self.assertEqual(
                report["chart_size_bytes"]["maximum"],
                report["audited_chart_byte_count"],
            )
            self.assertTrue(report["manifest"]["count_matches"])
            self.assertTrue(all(value == 0 for value in report["mismatch_counts"].values()))
            self.assertEqual(json.loads(stdout.getvalue())["status"], "passed")

    def test_rejects_manifest_that_is_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, _chart_path = _write_fixture(Path(temporary))
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["complete"] = False
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            report = audit_extracted_batch(output)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["mismatch_counts"]["manifest_mismatches"], 1)
            self.assertIn(
                "complete False != True",
                report["mismatch_samples"]["manifest_mismatches"][0],
            )

    def test_wraps_non_utf8_manifest_as_a_batch_audit_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, _chart_path = _write_fixture(Path(temporary))
            (output / "manifest.json").write_bytes(b"\x80")

            with self.assertRaisesRegex(
                BatchAuditError, "cannot read batch manifest"
            ):
                audit_extracted_batch(output)

    def test_reports_non_utf8_chart_as_a_json_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, chart_path = _write_fixture(Path(temporary))
            payload = b"\x80"
            chart_path.write_bytes(payload)
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["charts"][0]["output_byte_count"] = len(payload)
            manifest["charts"][0]["output_sha256"] = hashlib.sha256(
                payload
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            report = audit_extracted_batch(output)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["mismatch_counts"]["json_mismatches"], 1)
            self.assertIn(
                "UTF-8 decode failed",
                report["mismatch_samples"]["json_mismatches"][0],
            )

    def test_rejects_manifest_top_level_and_aggregate_contradictions(self) -> None:
        cases = (
            (
                "manifest schema",
                lambda value: value.__setitem__("schema_version", 2),
                "schema_version",
            ),
            (
                "canonical schema",
                lambda value: value.__setitem__(
                    "canonical_schema_version", "1.0.0"
                ),
                "canonical_schema_version",
            ),
            ("phase", lambda value: value.__setitem__("phase", 8), "phase"),
            (
                "status",
                lambda value: value.__setitem__("status", "incomplete"),
                "status",
            ),
            (
                "milestone",
                lambda value: value.__setitem__(
                    "milestone_status", "M8-not-achieved"
                ),
                "milestone_status",
            ),
            (
                "candidate count",
                lambda value: value.__setitem__("candidate_count", 2),
                "candidate_count",
            ),
            (
                "source count",
                lambda value: value.__setitem__("source_count", 2),
                "source_count",
            ),
            (
                "chart file count",
                lambda value: value.__setitem__("chart_file_count", 2),
                "chart_file_count",
            ),
            (
                "event count",
                lambda value: value.__setitem__("event_count", 2),
                "event_count",
            ),
            (
                "status counts",
                lambda value: value.__setitem__("status_counts", {}),
                "status_counts",
            ),
            (
                "raw parse counts",
                lambda value: value.__setitem__("raw_parse_status_counts", {}),
                "raw_parse_status_counts",
            ),
            (
                "canonical counts",
                lambda value: value.__setitem__("canonical_status_counts", {}),
                "canonical_status_counts",
            ),
            (
                "classified phase gate",
                lambda value: value["phase_gate"].__setitem__(
                    "all_candidates_classified", False
                ),
                "all_candidates_classified",
            ),
            (
                "failure phase gate",
                lambda value: value["phase_gate"].__setitem__(
                    "all_supported_candidates_extracted", False
                ),
                "all_supported_candidates_extracted",
            ),
            (
                "allowed outcomes",
                lambda value: value["phase_gate"].__setitem__(
                    "allowed_outcomes", ["success"]
                ),
                "allowed_outcomes",
            ),
            (
                "boolean manifest schema",
                lambda value: value.__setitem__("schema_version", True),
                "schema_version",
            ),
            (
                "integer complete flag",
                lambda value: value.__setitem__("complete", 1),
                "complete",
            ),
            (
                "boolean aggregate count",
                lambda value: value.__setitem__(
                    "status_counts", {"success": True}
                ),
                "status_counts",
            ),
            (
                "integer phase gate flag",
                lambda value: value["phase_gate"].__setitem__(
                    "all_candidates_classified", 1
                ),
                "all_candidates_classified",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output, _chart_path = _write_fixture(Path(temporary))
            manifest_path = output / "manifest.json"
            valid_manifest = json.loads(manifest_path.read_bytes())

            for name, mutate, expected_fragment in cases:
                with self.subTest(name=name):
                    manifest = deepcopy(valid_manifest)
                    mutate(manifest)
                    manifest_path.write_text(
                        json.dumps(manifest, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

                    report = audit_extracted_batch(output)

                    self.assertEqual(report["status"], "failed")
                    self.assertGreater(
                        report["mismatch_counts"]["manifest_mismatches"], 0
                    )
                    self.assertTrue(
                        any(
                            expected_fragment in sample
                            for sample in report["mismatch_samples"][
                                "manifest_mismatches"
                            ]
                        ),
                        report["mismatch_samples"]["manifest_mismatches"],
                    )

    def test_rejects_missing_and_duplicate_manifest_chart_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, _chart_path = _write_fixture(Path(temporary))
            manifest_path = output / "manifest.json"
            valid_manifest = json.loads(manifest_path.read_bytes())

            missing = deepcopy(valid_manifest)
            missing["charts"][0]["chart_id"] = ""
            manifest_path.write_text(
                json.dumps(missing, sort_keys=True) + "\n", encoding="utf-8"
            )
            missing_report = audit_extracted_batch(output)
            self.assertEqual(missing_report["status"], "failed")
            self.assertTrue(
                any(
                    "chart_id" in sample
                    for sample in missing_report["mismatch_samples"][
                        "manifest_mismatches"
                    ]
                )
            )

            duplicate = deepcopy(valid_manifest)
            uncertain = deepcopy(duplicate["charts"][0])
            uncertain.update(
                {
                    "status": "uncertain",
                    "chart_id": "CHART-1",
                    "output_path": None,
                    "output_byte_count": None,
                    "output_sha256": None,
                }
            )
            duplicate["charts"].append(uncertain)
            duplicate["candidate_count"] = 2
            duplicate["event_count"] = 2
            duplicate["status_counts"] = {"success": 1, "uncertain": 1}
            duplicate["raw_parse_status_counts"] = {"parsed": 2}
            duplicate["canonical_status_counts"] = {
                "canonicalized-with-raw-evidence": 2
            }
            manifest_path.write_text(
                json.dumps(duplicate, sort_keys=True) + "\n", encoding="utf-8"
            )

            duplicate_report = audit_extracted_batch(output)

            self.assertEqual(duplicate_report["status"], "failed")
            self.assertTrue(
                any(
                    "case-insensitive chart_id collision" in sample
                    for sample in duplicate_report["mismatch_samples"][
                        "manifest_mismatches"
                    ]
                )
            )

    def test_reports_tampering_and_duplicated_event_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, chart_path = _write_fixture(Path(temporary))
            chart = json.loads(chart_path.read_text(encoding="utf-8"))
            chart["events"][0]["raw"]["music_data_records"] = [{"index": 1}]
            _rewrite_chart(output, chart_path, chart)

            report = audit_extracted_batch(output)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["mismatch_counts"]["event_reference_mismatches"], 1)

    def test_rejects_missing_raw_index_and_event_group_disagreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, chart_path = _write_fixture(Path(temporary))
            chart = json.loads(chart_path.read_bytes())
            chart["events"][0]["raw"]["raw_record_indices"] = [1, 99]
            _rewrite_chart(output, chart_path, chart)

            report = audit_extracted_batch(output)

            self.assertEqual(report["status"], "failed")
            self.assertGreater(
                report["mismatch_counts"]["event_reference_mismatches"], 0
            )
            self.assertGreater(
                report["mismatch_counts"]["raw_accounting_mismatches"], 0
            )

    def test_rejects_duplicate_raw_indices_and_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, chart_path = _write_fixture(Path(temporary))
            chart = json.loads(chart_path.read_bytes())
            experimental = chart["raw"]["experimental_chart"]
            experimental["raw_records"][2]["index"] = 1
            experimental["raw_record_count"] = 99
            _rewrite_chart(output, chart_path, chart)

            report = audit_extracted_batch(output)

            self.assertEqual(report["status"], "failed")
            self.assertGreater(
                report["mismatch_counts"]["raw_record_table_mismatches"], 0
            )
            self.assertGreater(
                report["mismatch_counts"]["record_group_mismatches"], 0
            )

    def test_rejects_boolean_chart_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, chart_path = _write_fixture(Path(temporary))
            chart = json.loads(chart_path.read_bytes())
            chart["event_count"] = True
            chart["raw"]["experimental_chart"]["grouping"][
                "logical_object_count"
            ] = True
            _rewrite_chart(output, chart_path, chart)

            report = audit_extracted_batch(output)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(
                report["mismatch_counts"]["event_count_mismatches"], 1
            )
            self.assertGreater(
                report["mismatch_counts"]["record_group_mismatches"], 0
            )

    def test_rejects_event_sentinel_overlap_and_logical_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, chart_path = _write_fixture(Path(temporary))
            chart = json.loads(chart_path.read_bytes())
            experimental = chart["raw"]["experimental_chart"]
            experimental["record_groups"][0]["raw_record_indices"] = [0, 1]
            experimental["logical_objects"] = [{"duplicated": True}]
            _rewrite_chart(output, chart_path, chart)

            report = audit_extracted_batch(output)

            self.assertEqual(report["status"], "failed")
            self.assertGreater(
                report["mismatch_counts"]["raw_accounting_mismatches"], 0
            )
            self.assertEqual(
                report["mismatch_counts"]["duplicated_payload_mismatches"], 1
            )

    def test_reports_missing_and_extra_chart_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output, chart_path = _write_fixture(Path(temporary))
            chart_path.unlink()
            extra = output / "charts" / "extra.bin"
            extra.write_text("{}", encoding="utf-8")

            report = audit_extracted_batch(output)

            self.assertEqual(report["mismatch_counts"]["missing_files"], 1)
            self.assertEqual(report["mismatch_counts"]["extra_files"], 1)


if __name__ == "__main__":
    unittest.main()
