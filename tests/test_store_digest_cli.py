from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.cli import run
from test_store_canonical_digest import _build_store


class StoreDigestCliTests(unittest.TestCase):
    def test_digest_store_writes_only_metadata_report_and_checks_expected_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = _build_store(root)
            report_path = root / "diagnostics" / "digest.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            status = run(
                [
                    "digest-store",
                    "--store",
                    str(store),
                    "--report",
                    str(report_path),
                    "--progress-every",
                    "1",
                    "--expected-chart-count",
                    "2",
                    "--expected-raw-record-count",
                    "4",
                    "--expected-event-count",
                    "2",
                    "--expected-sentinel-count",
                    "2",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(status, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(summary["resolved_chart_count"], 2)
            self.assertEqual(summary["uncertain_count"], 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertNotIn('"events":', json.dumps(report))
            self.assertIn("digest store: 2/2 charts", stderr.getvalue())

    def test_digest_store_returns_one_for_expected_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = _build_store(root, resolved_count=1, uncertain_count=0)
            report_path = root / "digest.json"

            status = run(
                [
                    "digest-store",
                    "--store",
                    str(store),
                    "--report",
                    str(report_path),
                    "--expected-corpus-digest",
                    "0" * 64,
                ],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

            self.assertEqual(status, 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["mismatch_counts"]["expected_mismatches"], 1)

    def test_digest_store_rejects_report_inside_store_and_negative_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = _build_store(root, resolved_count=1, uncertain_count=0)
            for extra, expected_message in (
                (["--report", str(store / "report.json")], "outside the Store"),
                (["--progress-every", "-1"], "progress interval"),
                (["--failure-sample-limit", "-1"], "failure sample limit"),
            ):
                with self.subTest(extra=extra):
                    stderr = io.StringIO()
                    status = run(
                        ["digest-store", "--store", str(store), *extra],
                        stdout=io.StringIO(),
                        stderr=stderr,
                    )
                    self.assertEqual(status, 2)
                    self.assertIn(expected_message, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
