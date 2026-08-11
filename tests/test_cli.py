from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from musedash_chart_extractor.cli import _PROFILE_GATED_COMMANDS, _build_parser, run
from musedash_chart_extractor.installation import UnknownGameVersionError


class CliTests(unittest.TestCase):
    def test_parser_commands_default_to_supported_fingerprint_gate(self) -> None:
        command_arguments = {
            "candidates": [],
            "inspect-stageinfo": ["--source", "fixture.bundle", "--path-id", "42"],
            "extract": ["--source", "fixture.bundle", "--path-id", "42"],
            "index": [],
            "grouping-census": [],
        }
        self.assertEqual(set(command_arguments), set(_PROFILE_GATED_COMMANDS))

        for command, extra in command_arguments.items():
            with self.subTest(command=command), patch(
                "musedash_chart_extractor.installation.MuseDashInstallation.open"
            ) as open_installation:
                installation = open_installation.return_value
                installation.require_supported.side_effect = UnknownGameVersionError(
                    "unsupported fixture fingerprint"
                )
                stderr = io.StringIO()
                status = run(
                    [command, "--game-dir", "fixture-game", *extra],
                    stdout=io.StringIO(),
                    stderr=stderr,
                )

                self.assertEqual(status, 2)
                self.assertIn("unsupported fixture fingerprint", stderr.getvalue())
                open_installation.assert_called_once_with(Path("fixture-game"))
                installation.require_supported.assert_called_once_with()

    def test_unsupported_research_override_is_explicit_and_skips_profile_gate(self) -> None:
        parser = _build_parser()
        arguments = parser.parse_args(
            [
                "candidates",
                "--game-dir",
                "fixture-game",
                "--allow-unsupported-research",
            ]
        )
        self.assertTrue(arguments.allow_unsupported_research)

        with patch(
            "musedash_chart_extractor.installation.MuseDashInstallation.open"
        ) as open_installation:
            status = run(
                [
                    "candidates",
                    "--game-dir",
                    "definitely-not-a-real-directory",
                    "--allow-unsupported-research",
                ],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        self.assertEqual(status, 2)
        open_installation.assert_not_called()

    def test_scan_missing_directory_fails_without_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        status = run(
            ["scan", "--game-dir", "definitely-not-a-real-directory"],
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("game directory does not exist", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_scan_writes_phase_one_artifacts_outside_game_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_dir = root / "game"
            game_dir.mkdir()
            (game_dir / "fixture.bundle").write_bytes(b"UnityFS\x00fixture")
            output_dir = root / "output"
            stdout = io.StringIO()
            stderr = io.StringIO()

            status = run(
                [
                    "scan",
                    "--game-dir",
                    str(game_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(status, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ok")
            self.assertTrue((output_dir / "resource_inventory.jsonl").is_file())
            self.assertTrue((output_dir / "resource_summary.json").is_file())

    def test_scan_refuses_output_inside_game_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "game"
            game_dir.mkdir()
            stderr = io.StringIO()

            status = run(
                [
                    "scan",
                    "--game-dir",
                    str(game_dir),
                    "--output-dir",
                    str(game_dir / "diagnostics"),
                ],
                stdout=io.StringIO(),
                stderr=stderr,
            )

            self.assertEqual(status, 2)
            self.assertIn("must not be inside", stderr.getvalue())
            self.assertFalse((game_dir / "diagnostics").exists())

    def test_phase_three_to_six_commands_refuse_game_directory_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "game"
            game_dir.mkdir()
            cases = [
                (
                    [
                        "candidates",
                        "--game-dir",
                        str(game_dir),
                        "--output-dir",
                        str(game_dir / "candidate-diagnostics"),
                    ],
                    game_dir / "candidate-diagnostics",
                ),
                (
                    [
                        "inspect-stageinfo",
                        "--game-dir",
                        str(game_dir),
                        "--source",
                        "fixture.bundle",
                        "--path-id",
                        "42",
                        "--output-dir",
                        str(game_dir / "structure-diagnostics"),
                    ],
                    game_dir / "structure-diagnostics",
                ),
                (
                    [
                        "extract",
                        "--game-dir",
                        str(game_dir),
                        "--source",
                        "fixture.bundle",
                        "--path-id",
                        "42",
                        "--output",
                        str(game_dir / "first-chart.json"),
                    ],
                    game_dir / "first-chart.json",
                ),
                (
                    [
                        "index",
                        "--game-dir",
                        str(game_dir),
                        "--output",
                        str(game_dir / "song-chart-index.json"),
                    ],
                    game_dir / "song-chart-index.json",
                ),
            ]

            for arguments, forbidden_output in cases:
                with self.subTest(command=arguments[0]):
                    arguments.append("--allow-unsupported-research")
                    stderr = io.StringIO()
                    status = run(
                        arguments,
                        stdout=io.StringIO(),
                        stderr=stderr,
                    )

                    self.assertEqual(status, 2)
                    self.assertIn("must not be inside", stderr.getvalue())
                    self.assertFalse(forbidden_output.exists())


if __name__ == "__main__":
    unittest.main()
