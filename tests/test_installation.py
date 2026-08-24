from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from musedash_chart_extractor.installation import (
    CURRENT_GAME_FINGERPRINT,
    ExtractedChartCollection,
    MuseDashInstallation,
    STEAM_DEPOT_241392741196033182_FINGERPRINT,
    SUPPORTED_RESOURCE_PROFILES,
    UnknownGameVersionError,
)
from musedash_chart_extractor.scanner import ResourceRecord, ScannerError


def _record() -> ResourceRecord:
    return ResourceRecord(
        relative_path="fixture.bin",
        size=7,
        suffix=".bin",
        magic="unknown",
        sha256=hashlib.sha256(b"fixture").hexdigest(),
        category="unknown",
    )


def _write_extraction_artifacts(
    diagnostics: Path,
    fingerprint: str,
) -> tuple[dict, dict, dict, dict]:
    candidate = {
        "inventory_fingerprint": fingerprint,
        "chart_id": "fixture_map1",
    }
    bundle = {"source": "notedata.bundle"}
    song_index = {"inventory_fingerprint": fingerprint, "songs": []}
    census = {"inventory_fingerprint": fingerprint, "candidate_count": 1}
    (diagnostics / "chart_candidates.jsonl").write_text(
        json.dumps(candidate) + "\n", encoding="utf-8"
    )
    (diagnostics / "bundle_inventory.jsonl").write_text(
        json.dumps(bundle) + "\n", encoding="utf-8"
    )
    (diagnostics / "song_chart_index.json").write_text(
        json.dumps(song_index), encoding="utf-8"
    )
    (diagnostics / "grouping_census_summary.json").write_text(
        json.dumps(census), encoding="utf-8"
    )
    return candidate, bundle, song_index, census


class InstallationApiTests(unittest.TestCase):
    def test_supported_profile_registry_is_self_consistent(self) -> None:
        self.assertEqual(
            set(SUPPORTED_RESOURCE_PROFILES),
            {
                CURRENT_GAME_FINGERPRINT,
                STEAM_DEPOT_241392741196033182_FINGERPRINT,
            },
        )
        for fingerprint, profile in SUPPORTED_RESOURCE_PROFILES.items():
            self.assertEqual(profile.fingerprint, fingerprint)

        second = SUPPORTED_RESOURCE_PROFILES[
            STEAM_DEPOT_241392741196033182_FINGERPRINT
        ]
        self.assertEqual(second.addressables_version, "1.21.20")
        self.assertEqual(
            second.build_result_hash,
            "f4759f2e039525793e62c59c15df44c6",
        )
        self.assertEqual(
            second.parser_family,
            "sirenix-odin-binary-observed-stageinfo-subset",
        )
        self.assertEqual(
            second.grouping_rule_version,
            "composite-neutral-base-negative-id-singleton-v2",
        )
        self.assertEqual(
            second.evidence_status,
            "M8-achieved-on-steam-depot-manifest-241392741196033182",
        )

    def test_open_gates_unknown_fingerprints_to_probe_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = MuseDashInstallation.open(
                root,
                scanner=lambda _root: [_record()],
            )

            self.assertFalse(installation.supported)
            with self.assertRaisesRegex(UnknownGameVersionError, "scan/probe"):
                installation.require_supported()

    def test_known_profile_and_extract_charts_delegate_to_batch_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            diagnostics = root / "diagnostics"
            output = root / "output"
            game.mkdir()
            diagnostics.mkdir()
            _candidate, bundle, _song_index, census = _write_extraction_artifacts(
                diagnostics,
                CURRENT_GAME_FINGERPRINT,
            )
            installation = MuseDashInstallation(
                root=game.resolve(),
                inventory_fingerprint=CURRENT_GAME_FINGERPRINT,
                profile=SUPPORTED_RESOURCE_PROFILES[CURRENT_GAME_FINGERPRINT],
            )
            manifest = {
                "chart_file_count": 0,
                "charts": [
                    {
                        "chart_id": "unresolved_map1",
                        "status": "uncertain",
                        "song_id": None,
                    }
                ],
            }

            with (
                patch(
                    "musedash_chart_extractor.installation.resolve_note_data",
                    return_value=({"note": []}, {"source": "fixture"}),
                ) as resolve_note_data,
                patch(
                    "musedash_chart_extractor.installation.extract_all_charts",
                    return_value=manifest,
                ) as extract_all,
            ):
                collection = installation.extract_charts(
                    output_dir=output,
                    diagnostics_dir=diagnostics,
                )

            self.assertIsInstance(collection, ExtractedChartCollection)
            self.assertEqual(len(collection), 0)
            self.assertEqual(collection.uncertain[0]["chart_id"], "unresolved_map1")
            resolve_note_data.assert_called_once_with(game.resolve(), [bundle])
            kwargs = extract_all.call_args.kwargs
            self.assertEqual(kwargs["grouping_census_summary"], census)
            self.assertEqual(kwargs["expected_candidate_count"], 1)
            self.assertFalse(kwargs["research_mode"])

            census["inventory_fingerprint"] = [CURRENT_GAME_FINGERPRINT]
            (diagnostics / "grouping_census_summary.json").write_text(
                json.dumps(census), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ScannerError,
                "grouping census inventory_fingerprint must be a non-empty string",
            ):
                installation.extract_charts(
                    output_dir=output,
                    diagnostics_dir=diagnostics,
                )

    def test_collection_iterates_success_files_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            chart_path = output / "charts" / "song" / "fixture_map1.json"
            chart_path.parent.mkdir(parents=True)
            chart_path.write_text(
                json.dumps({"chart_id": "fixture_map1", "events": []}),
                encoding="utf-8",
            )
            collection = ExtractedChartCollection(
                output_dir=output,
                manifest={
                    "chart_file_count": 1,
                    "charts": [
                        {
                            "status": "success",
                            "output_path": "charts/song/fixture_map1.json",
                        }
                    ],
                },
            )

            self.assertEqual([chart["chart_id"] for chart in collection], ["fixture_map1"])

    def test_explicit_research_batch_keeps_unknown_version_fail_closed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            diagnostics = root / "diagnostics"
            output = root / "output"
            game.mkdir()
            diagnostics.mkdir()
            fingerprint = "sha256:" + "a" * 64
            _write_extraction_artifacts(diagnostics, fingerprint)
            installation = MuseDashInstallation(
                root=game.resolve(),
                inventory_fingerprint=fingerprint,
                profile=None,
            )

            with self.assertRaises(UnknownGameVersionError):
                installation.extract_charts(
                    output_dir=output,
                    diagnostics_dir=diagnostics,
                )

            manifest = {"chart_file_count": 0, "charts": []}
            with (
                patch(
                    "musedash_chart_extractor.installation.resolve_note_data",
                    return_value=({}, {"source": "fixture"}),
                ),
                patch(
                    "musedash_chart_extractor.installation.extract_all_charts",
                    return_value=manifest,
                ) as extract_all,
            ):
                collection = installation.extract_charts(
                    output_dir=output,
                    diagnostics_dir=diagnostics,
                    allow_unsupported_research=True,
                )

            self.assertEqual(collection.manifest, manifest)
            extract_all.assert_called_once()
            self.assertTrue(extract_all.call_args.kwargs["research_mode"])

            with self.assertRaisesRegex(
                ScannerError, "allow_unsupported_research must be a boolean"
            ):
                installation.extract_charts(
                    output_dir=output,
                    diagnostics_dir=diagnostics,
                    allow_unsupported_research=1,  # type: ignore[arg-type]
                )

    def test_research_flag_does_not_downgrade_a_supported_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            diagnostics = root / "diagnostics"
            game.mkdir()
            diagnostics.mkdir()
            _write_extraction_artifacts(diagnostics, CURRENT_GAME_FINGERPRINT)
            installation = MuseDashInstallation(
                root=game.resolve(),
                inventory_fingerprint=CURRENT_GAME_FINGERPRINT,
                profile=SUPPORTED_RESOURCE_PROFILES[CURRENT_GAME_FINGERPRINT],
            )

            with (
                patch(
                    "musedash_chart_extractor.installation.resolve_note_data",
                    return_value=({}, {"source": "fixture"}),
                ),
                patch(
                    "musedash_chart_extractor.installation.extract_all_charts",
                    return_value={"chart_file_count": 0, "charts": []},
                ) as extract_all,
            ):
                installation.extract_charts(
                    diagnostics_dir=diagnostics,
                    output_dir=root / "output",
                    allow_unsupported_research=True,
                )

            self.assertFalse(extract_all.call_args.kwargs["research_mode"])

    def test_extract_store_rejects_a_symbolic_link_output_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            output = root / "store-link"
            installation = MuseDashInstallation(
                root=game.resolve(),
                inventory_fingerprint=CURRENT_GAME_FINGERPRINT,
                profile=SUPPORTED_RESOURCE_PROFILES[CURRENT_GAME_FINGERPRINT],
            )
            original_is_symlink = Path.is_symlink

            def is_symlink(path: Path) -> bool:
                return path == output or original_is_symlink(path)

            with patch.object(
                Path, "is_symlink", autospec=True, side_effect=is_symlink
            ):
                with self.assertRaisesRegex(ScannerError, "symbolic link"):
                    installation.extract_store(
                        output_dir=output,
                        diagnostics_dir=root / "missing-diagnostics",
                    )


if __name__ == "__main__":
    unittest.main()
