from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from musedash_chart_extractor.cli import _build_parser, run


class StoreCliTests(unittest.TestCase):
    def test_store_command_defaults(self) -> None:
        extract = _build_parser().parse_args(
            ["extract-store", "--game-dir", "fixture-game"]
        )
        self.assertEqual(extract.output, Path("MuseDashChartStore"))
        self.assertEqual(extract.candidate_file, Path("diagnostics/chart_candidates.jsonl"))
        self.assertEqual(extract.song_index, Path("diagnostics/song_chart_index.json"))
        self.assertEqual(
            extract.grouping_census_summary,
            Path("diagnostics/grouping_census_summary.json"),
        )
        self.assertFalse(hasattr(extract, "allow_unsupported_research"))

        audit = _build_parser().parse_args(
            ["audit-store", "--store", "MuseDashChartStore"]
        )
        self.assertIsNone(audit.game_dir)
        self.assertEqual(audit.report, Path("diagnostics/store_audit.json"))

    def test_extract_store_uses_formal_installation_gate_and_reports_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            output = root / "store"
            manifest = {
                "status": "complete-with-classified-outcomes",
                "complete": True,
                "candidate_count": 2,
                "source_count": 1,
                "payload_count": 1,
                "payload_byte_count": 100,
                "raw_record_count": 4,
                "logical_event_count": 2,
                "status_counts": {"success": 1, "uncertain": 1},
                "logical_store_digest": "a" * 64,
                "phase_gate": {"no_failed_charts": True},
            }
            installation = SimpleNamespace(
                extract_store=lambda **kwargs: (
                    kwargs["progress"](1, 1, "data/fixture.bundle") or manifest
                )
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch(
                "musedash_chart_extractor.installation.MuseDashInstallation.open",
                return_value=installation,
            ) as opened:
                status = run(
                    [
                        "extract-store",
                        "--game-dir",
                        str(game),
                        "--output",
                        str(output),
                        "--progress-every",
                        "1",
                    ],
                    stdout=stdout,
                    stderr=stderr,
                )
        self.assertEqual(status, 0)
        opened.assert_called_once_with(game.resolve())
        self.assertIn("extract store: 1/1", stderr.getvalue())
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["payload_count"], 1)
        self.assertEqual(summary["logical_store_digest"], "a" * 64)
        self.assertEqual(summary["store_manifest"], str((output / "store.json").resolve()))

    def test_extract_store_rejects_a_symbolic_link_output_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            output = root / "store-link"
            original_is_symlink = Path.is_symlink

            def is_symlink(path: Path) -> bool:
                return path == output or original_is_symlink(path)

            with (
                patch.object(Path, "is_symlink", autospec=True, side_effect=is_symlink),
                patch(
                    "musedash_chart_extractor.installation.MuseDashInstallation.open"
                ) as opened,
            ):
                status = run(
                    [
                        "extract-store",
                        "--game-dir",
                        str(game),
                        "--output",
                        str(output),
                    ],
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

            self.assertEqual(status, 2)
            opened.assert_not_called()

    def test_audit_store_writes_report_and_returns_one_for_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = root / "store"
            store.mkdir()
            report_path = root / "report.json"
            report = {
                "status": "failed",
                "mismatch_counts": {"payload_set_mismatches": 1},
                "counts": {"chart_count": 1, "payload_count": 1},
            }
            stdout = io.StringIO()
            with patch(
                "musedash_chart_extractor.store.audit.audit_chart_store",
                return_value=report,
            ):
                status = run(
                    [
                        "audit-store",
                        "--store",
                        str(store),
                        "--report",
                        str(report_path),
                    ],
                    stdout=stdout,
                    stderr=io.StringIO(),
                )
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")

    def test_audit_store_report_cannot_overwrite_store_content(self) -> None:
        for relative in (
            "store.json",
            "index.sqlite3",
            "payloads/sha256/aa/report.json",
            ".staging/report.json",
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = root / "store"
                store.mkdir()
                report_path = store.joinpath(*relative.split("/"))
                with patch(
                    "musedash_chart_extractor.store.audit.audit_chart_store"
                ) as audit:
                    status = run(
                        [
                            "audit-store",
                            "--store",
                            str(store),
                            "--report",
                            str(report_path),
                        ],
                        stdout=io.StringIO(),
                        stderr=io.StringIO(),
                    )
                self.assertEqual(status, 2)
                audit.assert_not_called()

    def test_audit_store_report_rejects_a_junction_inside_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = root / "store"
            store.mkdir()
            junction = store / "audit"
            report_path = junction / "store.json"
            original_is_junction = getattr(Path, "is_junction", lambda _path: False)

            def is_junction(path: Path) -> bool:
                return path == junction or original_is_junction(path)

            with (
                patch.object(
                    Path,
                    "is_junction",
                    autospec=True,
                    side_effect=is_junction,
                    create=True,
                ),
                patch(
                    "musedash_chart_extractor.store.audit.audit_chart_store"
                ) as audit,
            ):
                status = run(
                    [
                        "audit-store",
                        "--store",
                        str(store),
                        "--report",
                        str(report_path),
                    ],
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

            self.assertEqual(status, 2)
            audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
