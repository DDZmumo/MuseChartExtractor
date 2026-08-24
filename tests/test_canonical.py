from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.charts.canonicalize import (
    CanonicalizationError,
    canonicalize_chart,
    reconstruct_experimental_chart,
)
from musedash_chart_extractor.cli import run
from musedash_chart_extractor.discovery.first_chart import build_experimental_chart
from musedash_chart_extractor.unity.odin import parse_stage_info_payload
from test_odin import _payload


def _experimental(type_id: int) -> dict:
    payload = _payload()
    parsed = parse_stage_info_payload(payload)
    candidate = {
        "rank": 1,
        "inventory_fingerprint": "sha256:fixture",
        "source": "fixture.bundle",
        "source_size": 123,
        "source_sha256": "a" * 64,
        "container_path": "Assets/Static Resources/Data/Configs/StageInfos/fixture_map1.asset",
        "path_id": 42,
        "object_type": "MonoBehaviour",
        "metadata": {
            "asset_name": "fixture_map1",
            "difficulty_raw": 1,
            "music": "fixture_music",
            "bpm_raw": 120.0,
        },
    }
    return build_experimental_chart(
        candidate,
        parsed,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        stage_info_raw={
            "serializationData": {"SerializedBytes": list(payload)},
            "unknownStageInfoField": {"preserved": True},
        },
        note_configs_by_uid={
            "fixture-note": [
                {
                    "uid": "fixture-note",
                    "type": str(type_id),
                    "unknownNoteConfigField": [1, 2, 3],
                }
            ]
        },
        note_data_provenance={"source": "fixture-note-data.bundle"},
    )


def _index() -> dict:
    return {
        "catalog": {
            "source": {"catalog_sha256": "b" * 64},
        },
        "songs": [
            {
                "song_id": "fixture-song",
                "metadata": {
                    "title_raw": "Fixture Song",
                    "artist_raw": "Fixture Artist",
                    "bpm_raw": "120",
                    "raw": {"unknownAlbumField": {"preserved": True}},
                },
                "source": {"container_path": "ALBUM1.json"},
                "charts": [
                    {
                        "chart_id": "fixture_map1",
                        "difficulty_id": 1,
                        "difficulty_key": "difficulty1",
                        "difficulty_level_raw": "4",
                        "difficulty_raw": 1,
                        "relationship_status": "exact-note-json",
                        "relationship_evidence": ["synthetic exact relationship"],
                        "warnings": ["synthetic-warning"],
                        "source": {"bundle": "fixture.bundle"},
                        "addressables": {"entry_index": 7},
                    }
                ],
            }
        ],
    }


def _validation(experimental: dict) -> dict:
    source = experimental["source"]
    return {
        "status": "validated-first-chart",
        "milestone_status": "M4-achieved",
        "chart": {
            "bundle": source["bundle"],
            "bundle_sha256": source["bundle_sha256"],
            "path_id": source["path_id"],
            "payload_sha256": source["payload_sha256"],
        },
        "unknownValidationField": {"preserved": True},
    }


class CanonicalChartTests(unittest.TestCase):
    def test_unknown_type_remains_legal_and_all_raw_evidence_is_retained(self) -> None:
        experimental = _experimental(99)
        song_index = _index()

        model = canonicalize_chart(experimental, song_index)
        rendered = model.to_dict()

        self.assertEqual(rendered["schema_version"], "1.1.0")
        self.assertEqual(rendered["source"]["extractor_version"], "0.1.0")
        self.assertEqual(rendered["chart_id"], "fixture_map1")
        self.assertEqual(rendered["song"]["song_id"], "fixture-song")
        self.assertEqual(rendered["difficulty"]["difficulty_id"], 1)
        self.assertEqual(rendered["validation_status"], "unvalidated")
        self.assertEqual(rendered["event_count"], 1)
        event = rendered["events"][0]
        self.assertEqual(event["time_sec"], "3.117")
        self.assertEqual(event["type_id"], 99)
        self.assertIsNone(event["type_name"])
        self.assertEqual(event["type_status"], "unknown")
        self.assertIsNone(event["is_air"])
        self.assertIsNone(event["duration_sec"])
        self.assertNotIn(
            "logical_objects",
            rendered["raw"]["experimental_chart"],
        )
        self.assertEqual(
            reconstruct_experimental_chart(rendered["raw"]),
            experimental,
        )
        self.assertEqual(
            rendered["raw"]["indexed_song"],
            song_index["songs"][0],
        )
        self.assertEqual(
            set(event["raw"]),
            {"base_raw_record_index", "raw_record_indices"},
        )
        self.assertNotIn("music_data_records", event["raw"])
        self.assertNotIn("group", event["raw"])
        raw_by_index = {
            row["index"]: row
            for row in rendered["raw"]["experimental_chart"]["raw_records"]
        }
        base = raw_by_index[event["raw"]["base_raw_record_index"]]["raw"]
        self.assertEqual(
            base["fields"]["configData"]["fields"]["note_uid"]["value"],
            "fixture-note",
        )
        self.assertEqual(
            rendered["raw"]["experimental_chart"]["stage_info_raw"][
                "unknownStageInfoField"
            ],
            {"preserved": True},
        )
        json.dumps(rendered)

    def test_known_press_type_has_exact_duration_and_end_time(self) -> None:
        experimental = _experimental(3)
        validation = _validation(experimental)
        model = canonicalize_chart(experimental, _index(), validation)
        event = model.events[0]

        self.assertEqual(event.type_id, 3)
        self.assertEqual(event.type_name, "Press")
        self.assertEqual(event.type_status, "known")
        self.assertEqual(event.duration_sec, "1.5")
        self.assertEqual(event.end_time_sec, "4.617")
        self.assertEqual(model.validation_status, "validated-first-chart")
        self.assertEqual(
            model.raw["validation_report"]["unknownValidationField"],
            {"preserved": True},
        )
        self.assertIn("is_air_not_yet_canonicalized", model.warnings)

    def test_explicit_extractor_version_preserves_build_time_provenance(self) -> None:
        model = canonicalize_chart(
            _experimental(1),
            _index(),
            extractor_version="store-builder-1.2.3",
        )

        self.assertEqual(model.source.extractor_version, "store-builder-1.2.3")

    def test_invalid_explicit_extractor_version_fails_loudly(self) -> None:
        with self.assertRaisesRegex(CanonicalizationError, "extractor version"):
            canonicalize_chart(
                _experimental(1),
                _index(),
                extractor_version="",
            )

    def test_mismatched_validation_report_fails_loudly(self) -> None:
        experimental = _experimental(1)
        validation = _validation(experimental)
        validation["chart"]["path_id"] = 999

        with self.assertRaisesRegex(CanonicalizationError, "path_id does not match"):
            canonicalize_chart(experimental, _index(), validation)

    def test_missing_index_relationship_fails_loudly(self) -> None:
        index = _index()
        index["songs"][0]["charts"] = []

        with self.assertRaisesRegex(CanonicalizationError, "resolved to 0"):
            canonicalize_chart(_experimental(1), index)

    def test_derived_logical_objects_must_be_exactly_reconstructible(self) -> None:
        experimental = _experimental(1)
        experimental["logical_objects"][0]["grouping_status"] = "tampered"

        with self.assertRaisesRegex(
            CanonicalizationError,
            "cannot reconstruct the Phase 5 input",
        ):
            canonicalize_chart(experimental, _index())

    def test_canonicalize_cli_writes_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_path = root / "raw.json"
            index_path = root / "index.json"
            output_path = root / "canonical.json"
            report_path = root / "canonical-report.json"
            raw_path.write_text(json.dumps(_experimental(3)), encoding="utf-8")
            index_path.write_text(json.dumps(_index()), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()

            status = run(
                [
                    "canonicalize",
                    "--raw-chart",
                    str(raw_path),
                    "--song-index",
                    str(index_path),
                    "--output",
                    str(output_path),
                    "--report",
                    str(report_path),
                ],
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(status, 0)
            self.assertEqual(stderr.getvalue(), "")
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["status"], "canonicalized-with-raw-evidence")
            self.assertEqual(summary["event_count"], 1)
            self.assertEqual(summary["report"], str(report_path.resolve()))
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["schema_version"], "1.1.0")
            self.assertEqual(written["events"][0]["type_name"], "Press")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["milestone_status"], "M6-achieved")
            self.assertTrue(
                report["lossless_checks"][
                    "experimental_chart_reconstructed_equal"
                ]
            )
            self.assertTrue(
                report["lossless_checks"][
                    "raw_record_references_accounted_with_sentinel"
                ]
            )
            self.assertTrue(
                report["lossless_checks"]["event_payloads_not_duplicated"]
            )


if __name__ == "__main__":
    unittest.main()
