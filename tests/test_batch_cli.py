from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from musedash_chart_extractor.cli import _build_parser, run


class BatchCliTests(unittest.TestCase):
    def test_extract_all_defaults_are_project_local_artifacts(self) -> None:
        arguments = _build_parser().parse_args(
            ["extract-all", "--game-dir", "fixture-game"]
        )

        self.assertEqual(arguments.output, Path("extracted"))
        self.assertEqual(
            arguments.candidate_file,
            Path("diagnostics/chart_candidates.jsonl"),
        )
        self.assertEqual(
            arguments.song_index,
            Path("diagnostics/song_chart_index.json"),
        )
        self.assertEqual(
            arguments.bundle_inventory,
            Path("diagnostics/bundle_inventory.jsonl"),
        )
        self.assertEqual(
            arguments.grouping_census_summary,
            Path("diagnostics/grouping_census_summary.json"),
        )
        self.assertEqual(arguments.progress_every, 50)
        self.assertFalse(arguments.allow_unsupported_research)

        research_arguments = _build_parser().parse_args(
            [
                "extract-all",
                "--game-dir",
                "fixture-game",
                "--allow-unsupported-research",
            ]
        )
        self.assertTrue(research_arguments.allow_unsupported_research)

    def test_extract_all_loads_inputs_once_and_reports_manifest_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_dir = root / "game"
            game_dir.mkdir()
            output_dir = root / "extracted"
            candidates_path = root / "chart_candidates.jsonl"
            bundle_inventory_path = root / "bundle_inventory.jsonl"
            song_index_path = root / "song_chart_index.json"
            census_path = root / "grouping_census_summary.json"
            candidates_path.write_text("{}\n", encoding="utf-8")
            bundle_inventory_path.write_text("{}\n", encoding="utf-8")
            song_index_path.write_text("{}", encoding="utf-8")
            census_path.write_text("{}", encoding="utf-8")
            manifest = {
                "status": "complete-with-classified-outcomes",
                "milestone_status": "M8-achieved",
                "candidate_count": 1,
                "source_count": 1,
                "chart_file_count": 1,
                "event_count": 2,
                "status_counts": {"success": 1},
                "raw_parse_status_counts": {"parsed": 1},
                "canonical_status_counts": {
                    "canonicalized-with-raw-evidence": 1
                },
            }
            stdout = io.StringIO()
            stderr = io.StringIO()

            def fake_extract(**kwargs):
                self.assertEqual(kwargs["output_dir"], output_dir.resolve())
                self.assertEqual(kwargs["candidate_file"], candidates_path)
                self.assertEqual(kwargs["song_index_file"], song_index_path)
                self.assertEqual(kwargs["bundle_inventory_file"], bundle_inventory_path)
                self.assertEqual(kwargs["grouping_census_summary_file"], census_path)
                self.assertFalse(kwargs["allow_unsupported_research"])
                kwargs["progress"](1, 1, "data/chart.bundle")
                return SimpleNamespace(manifest=manifest)

            fake_installation = SimpleNamespace(extract_charts=fake_extract)
            with patch(
                "musedash_chart_extractor.installation.MuseDashInstallation.open",
                return_value=fake_installation,
            ) as open_installation:
                status = run(
                    [
                        "extract-all",
                        "--game-dir",
                        str(game_dir),
                        "--output",
                        str(output_dir),
                        "--candidate-file",
                        str(candidates_path),
                        "--song-index",
                        str(song_index_path),
                        "--bundle-inventory",
                        str(bundle_inventory_path),
                        "--grouping-census-summary",
                        str(census_path),
                        "--progress-every",
                        "1",
                    ],
                    stdout=stdout,
                    stderr=stderr,
                )

            self.assertEqual(status, 0)
            open_installation.assert_called_once_with(game_dir.resolve())
            self.assertEqual(
                stderr.getvalue(),
                "extract all: 1/1 bundles (data/chart.bundle)\n",
            )
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["milestone_status"], "M8-achieved")
            self.assertTrue(summary["formal_support"])
            self.assertEqual(summary["status_counts"], {"success": 1})
            self.assertEqual(
                summary["batch_manifest"],
                str((output_dir / "manifest.json").resolve()),
            )

    def test_extract_all_refuses_output_inside_game_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "game"
            game_dir.mkdir()
            forbidden = game_dir / "extracted"
            stderr = io.StringIO()

            with patch(
                "musedash_chart_extractor.installation.MuseDashInstallation.open"
            ) as open_installation:
                status = run(
                    [
                        "extract-all",
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
            open_installation.assert_not_called()

    def test_extract_all_research_summary_is_explicitly_nonformal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_dir = root / "game"
            game_dir.mkdir()
            manifest = {
                "status": "complete-with-classified-outcomes",
                "milestone_status": "M8-achieved",
                "candidate_count": 1,
                "source_count": 1,
                "chart_file_count": 1,
                "event_count": 2,
                "status_counts": {"success": 1},
                "raw_parse_status_counts": {"parsed": 1},
                "canonical_status_counts": {
                    "canonicalized-with-raw-evidence": 1
                },
                "profile_support": {
                    "formal_support": False,
                    "status": "unsupported-fingerprint-research",
                },
            }
            installation = SimpleNamespace()

            def fake_extract(**kwargs):
                self.assertTrue(kwargs["allow_unsupported_research"])
                return SimpleNamespace(manifest=manifest)

            installation.extract_charts = fake_extract
            stdout = io.StringIO()
            with patch(
                "musedash_chart_extractor.installation.MuseDashInstallation.open",
                return_value=installation,
            ):
                status = run(
                    [
                        "extract-all",
                        "--game-dir",
                        str(game_dir),
                        "--output",
                        str(root / "output"),
                        "--allow-unsupported-research",
                    ],
                    stdout=stdout,
                    stderr=io.StringIO(),
                )

            self.assertEqual(status, 0)
            self.assertFalse(json.loads(stdout.getvalue())["formal_support"])

    def test_extract_all_rejects_negative_progress_interval_before_input_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_dir = root / "game"
            game_dir.mkdir()
            stderr = io.StringIO()

            status = run(
                [
                    "extract-all",
                    "--game-dir",
                    str(game_dir),
                    "--output",
                    str(root / "extracted"),
                    "--progress-every",
                    "-1",
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(status, 2)
            self.assertIn("progress interval cannot be negative", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
