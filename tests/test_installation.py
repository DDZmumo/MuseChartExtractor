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


class InstallationApiTests(unittest.TestCase):
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
            candidate = {
                "inventory_fingerprint": CURRENT_GAME_FINGERPRINT,
                "chart_id": "fixture_map1",
            }
            bundle = {"source": "notedata.bundle"}
            (diagnostics / "chart_candidates.jsonl").write_text(
                json.dumps(candidate) + "\n", encoding="utf-8"
            )
            (diagnostics / "bundle_inventory.jsonl").write_text(
                json.dumps(bundle) + "\n", encoding="utf-8"
            )
            song_index = {
                "inventory_fingerprint": CURRENT_GAME_FINGERPRINT,
                "songs": [],
            }
            census = {
                "inventory_fingerprint": CURRENT_GAME_FINGERPRINT,
                "candidate_count": 1,
            }
            (diagnostics / "song_chart_index.json").write_text(
                json.dumps(song_index), encoding="utf-8"
            )
            (diagnostics / "grouping_census_summary.json").write_text(
                json.dumps(census), encoding="utf-8"
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


if __name__ == "__main__":
    unittest.main()
