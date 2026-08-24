from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.cli import run
from test_validator import _chart


class ValidationCliTests(unittest.TestCase):
    def test_validate_writes_json_and_markdown_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_dir = root / "game"
            bundle = game_dir / "data" / "chart.bundle"
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b"synthetic-bundle")
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()

            chart_paths = []
            references = {"schema_version": 1, "references": []}
            for slot in (1, 2):
                chart_id = f"fixture_map{slot}"
                chart_path = root / f"{chart_id}.json"
                chart_path.write_text(
                    json.dumps(_chart(chart_id, digest)), encoding="utf-8"
                )
                chart_paths.append(chart_path)
                references["references"].append(
                    {"chart_id": chart_id, "expected_combo": 3}
                )
            reference_path = root / "references.json"
            reference_path.write_text(json.dumps(references), encoding="utf-8")
            report_path = root / "validation.json"
            markdown_path = root / "validation.md"
            stdout = io.StringIO()
            stderr = io.StringIO()

            status = run(
                [
                    "validate",
                    "--chart",
                    str(chart_paths[0]),
                    "--chart",
                    str(chart_paths[1]),
                    "--game-dir",
                    str(game_dir),
                    "--reference-file",
                    str(reference_path),
                    "--output",
                    str(report_path),
                    "--markdown-output",
                    str(markdown_path),
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(status, 0)
            self.assertEqual(stderr.getvalue(), "")
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["milestone_status"], "M7-achieved")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["source_verified_count"], 2)
            self.assertIn(
                "Accuracy claim: partial",
                markdown_path.read_text(encoding="utf-8"),
            )

    def test_validate_compares_complete_indexed_event_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chart = _chart("fixture_map1", "a" * 64)
            chart_path = root / "chart.json"
            chart_path.write_text(json.dumps(chart), encoding="utf-8")
            reference = {
                "schema_version": 1,
                "references": [
                    {
                        "chart_id": "fixture_map1",
                        "expected_combo": 3,
                        "event_reference": {
                            "schema_version": "event-reference-v1",
                            "scope": "complete-indexed-sequence",
                            "source": {"kind": "synthetic-cli-fixture"},
                            "events": [
                                {
                                    "index": event["index"],
                                    "time_sec": event["time_sec"],
                                    "type_id": event["type_id"],
                                    "duration_sec": event["duration_sec"],
                                }
                                for event in chart["events"]
                            ],
                        },
                    }
                ],
            }
            reference_path = root / "references.json"
            reference_path.write_text(json.dumps(reference), encoding="utf-8")
            report_path = root / "validation.json"
            markdown_path = root / "validation.md"
            stdout = io.StringIO()

            status = run(
                [
                    "validate",
                    "--chart",
                    str(chart_path),
                    "--reference-file",
                    str(reference_path),
                    "--output",
                    str(report_path),
                    "--markdown-output",
                    str(markdown_path),
                ],
                stdout=stdout,
                stderr=io.StringIO(),
            )

            self.assertEqual(status, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["event_reference_compared_count"], 1)
            self.assertEqual(summary["event_reference_matched_count"], 1)
            self.assertEqual(summary["event_reference_mismatch_count"], 0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["charts"][0]["event_reference"]["status"], "matched")
            self.assertIn(
                "scoped to explicitly supplied event-reference fields",
                markdown_path.read_text(encoding="utf-8"),
            )

    def test_validate_refuses_outputs_inside_game_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "game"
            game_dir.mkdir()
            forbidden = game_dir / "validation.json"
            stderr = io.StringIO()

            status = run(
                [
                    "validate",
                    "--chart",
                    str(Path(temporary) / "unused.json"),
                    "--game-dir",
                    str(game_dir),
                    "--output",
                    str(forbidden),
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(status, 2)
            self.assertIn("must not be inside", stderr.getvalue())
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
