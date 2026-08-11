from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.batch import (
    BatchExtractionError,
    extract_all_charts,
    safe_path_component,
)
from test_odin import _payload


class _Type:
    name = "MonoBehaviour"


class _Object:
    type = _Type()

    def __init__(self, path_id: int, payload: bytes, chart_id: str) -> None:
        self.path_id = path_id
        self.byte_size = len(payload) + 100
        self._payload = payload
        self._chart_id = chart_id

    def parse_as_dict(self) -> dict:
        return {
            "m_Name": self._chart_id,
            "unknownStageInfoField": {"preserved": True},
            "serializationData": {
                "SerializedFormat": 0,
                "SerializedBytes": list(self._payload),
                "unknownSerializationField": [1, 2, 3],
            },
        }


class _Environment:
    def __init__(self, objects: list[_Object]) -> None:
        self.objects = objects


def _candidate(
    *,
    chart_id: str,
    path_id: int,
    payload: bytes,
    bundle_bytes: bytes,
    inventory_fingerprint: str = "sha256:fixture",
) -> dict:
    return {
        "status": "unvalidated_candidate",
        "validation_status": "unvalidated",
        "rank": path_id,
        "inventory_fingerprint": inventory_fingerprint,
        "source": "data/fixture.bundle",
        "source_size": len(bundle_bytes),
        "source_sha256": hashlib.sha256(bundle_bytes).hexdigest(),
        "container_path": f"Assets/Static Resources/Data/Configs/StageInfos/{chart_id}.asset",
        "path_id": path_id,
        "object_type": "MonoBehaviour",
        "object_byte_size": len(payload) + 100,
        "metadata": {
            "asset_name": chart_id,
            "difficulty_raw": path_id,
            "music": "fixture_music",
            "bpm_raw": 120.0,
        },
        "structure": {
            "serialized_format": 0,
            "serialized_payload_byte_count": len(payload),
            "serialized_payload_sha256": hashlib.sha256(payload).hexdigest(),
        },
    }


def _index(
    *,
    include_unresolved: bool = True,
    bundle_bytes: bytes = b"synthetic-bundle",
) -> dict:
    bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
    def source(chart_id: str, path_id: int) -> dict:
        return {
            "bundle": "data/fixture.bundle",
            "bundle_byte_count": len(bundle_bytes),
            "bundle_sha256": bundle_sha256,
            "container_path": (
                "Assets/Static Resources/Data/Configs/StageInfos/"
                f"{chart_id}.asset"
            ),
            "path_id": path_id,
            "object_type": "MonoBehaviour",
        }

    def addressables(chart_id: str) -> dict:
        return {
            "primary_key": chart_id,
            "dependency_local_path": "data/fixture.bundle",
        }

    unresolved = (
        [
            {
                "chart_id": "tutorial_v2_map1",
                "song_id": None,
                "difficulty_id": 1,
                "difficulty_raw": 1,
                "difficulty_level_raw": None,
                "relationship_status": "unresolved",
                "unresolved_reason": "no ALBUM identity",
                "warnings": ["album-identity-unresolved"],
                "source": source("tutorial_v2_map1", 2),
                "addressables": addressables("tutorial_v2_map1"),
            }
        ]
        if include_unresolved
        else []
    )
    indexed_count = 1
    unresolved_count = len(unresolved)
    return {
        "inventory_fingerprint": "sha256:fixture",
        "counts": {
            "candidate_chart_count": indexed_count + unresolved_count,
            "indexed_chart_count": indexed_count,
            "unresolved_chart_count": unresolved_count,
        },
        "catalog": {"source": {"catalog_sha256": "b" * 64}},
        "songs": [
            {
                "song_id": "fixture-song",
                "metadata": {
                    "title_raw": "Fixture Song",
                    "artist_raw": "Fixture Artist",
                    "bpm_raw": "120",
                },
                "source": {"container_path": "ALBUM1.json"},
                "charts": [
                    {
                        "chart_id": "fixture_map1",
                        "song_id": "fixture-song",
                        "difficulty_id": 1,
                        "difficulty_key": "difficulty1",
                        "difficulty_level_raw": "4",
                        "difficulty_raw": 1,
                        "relationship_status": "exact-note-json",
                        "relationship_evidence": ["synthetic exact relationship"],
                        "warnings": ["synthetic-warning"],
                        "source": source("fixture_map1", 1),
                        "addressables": {
                            **addressables("fixture_map1"),
                            "entry_index": 7,
                        },
                    }
                ],
            }
        ],
        "unresolved_charts": unresolved,
    }


def _note_configs() -> dict:
    return {
        "fixture-note": [
            {"uid": "fixture-note", "type": "3", "unknownNoteConfigField": True}
        ]
    }


def _census(*, candidate_count: int, source_count: int = 1) -> dict:
    return {
        "schema_version": 1,
        "phase": 9,
        "status": "census-complete",
        "complete": True,
        "inventory_fingerprint": "sha256:fixture",
        "grouping_rule_version": "composite-neutral-base-negative-id-singleton-v2",
        "candidate_count": candidate_count,
        "source_count": source_count,
        "raw_parse_status_counts": {"parsed": candidate_count},
        "grouping_status_counts": {"grouped": candidate_count},
        "parsed_raw_record_count": candidate_count * 2,
        "grouped_logical_object_count": candidate_count,
    }


class BatchExtractionTests(unittest.TestCase):
    def test_success_and_unresolved_are_complete_stable_and_load_source_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            game = temporary_root / "game"
            output = temporary_root / "output"
            bundle = game / "data" / "fixture.bundle"
            bundle.parent.mkdir(parents=True)
            bundle_bytes = b"synthetic-bundle"
            bundle.write_bytes(bundle_bytes)
            payload = _payload()
            objects = [
                _Object(1, payload, "fixture_map1"),
                _Object(2, payload, "tutorial_v2_map1"),
            ]
            candidates = [
                _candidate(
                    chart_id="tutorial_v2_map1",
                    path_id=2,
                    payload=payload,
                    bundle_bytes=bundle_bytes,
                ),
                _candidate(
                    chart_id="fixture_map1",
                    path_id=1,
                    payload=payload,
                    bundle_bytes=bundle_bytes,
                ),
            ]
            load_calls: list[str] = []

            def loader(path: str) -> _Environment:
                load_calls.append(path)
                return _Environment(objects)

            first = extract_all_charts(
                game,
                output,
                candidates,
                _index(),
                grouping_census_summary=_census(candidate_count=2),
                note_configs_by_uid=_note_configs(),
                note_data_provenance={"source": "synthetic-note-data"},
                expected_candidate_count=2,
                loader=loader,
                extractor_version="test-version",
            )
            first_manifest = (output / "manifest.json").read_bytes()
            first_chart = output / "charts" / "fixture-song" / "fixture_map1.json"
            first_chart_bytes = first_chart.read_bytes()

            second = extract_all_charts(
                game,
                output,
                candidates,
                _index(),
                grouping_census_summary=_census(candidate_count=2),
                note_configs_by_uid=_note_configs(),
                note_data_provenance={"source": "synthetic-note-data"},
                expected_candidate_count=2,
                loader=loader,
                extractor_version="test-version",
            )

            self.assertEqual(len(load_calls), 2, "one source load is allowed per invocation")
            self.assertEqual(first_manifest, (output / "manifest.json").read_bytes())
            self.assertEqual(first_chart_bytes, first_chart.read_bytes())
            self.assertEqual(first, second)
            self.assertEqual(first["status_counts"], {"success": 1, "uncertain": 1})
            self.assertEqual(first["milestone_status"], "M8-achieved")
            self.assertTrue(first["complete"])
            self.assertEqual(
                [row["chart_id"] for row in first["charts"]],
                ["fixture_map1", "tutorial_v2_map1"],
            )
            by_id = {row["chart_id"]: row for row in first["charts"]}
            success = by_id["fixture_map1"]
            self.assertEqual(success["status"], "success")
            self.assertEqual(success["raw_parse_status"], "parsed")
            self.assertEqual(success["grouping_status"], "grouped")
            self.assertEqual(success["output_path"], "charts/fixture-song/fixture_map1.json")
            self.assertEqual(
                success["output_sha256"], hashlib.sha256(first_chart_bytes).hexdigest()
            )
            rendered = json.loads(first_chart_bytes)
            self.assertTrue(
                rendered["raw"]["experimental_chart"]["stage_info_raw"][
                    "unknownStageInfoField"
                ]["preserved"]
            )
            uncertain = by_id["tutorial_v2_map1"]
            self.assertEqual(uncertain["status"], "uncertain")
            self.assertEqual(uncertain["reason"], "song-identity-unresolved")
            self.assertIsNone(uncertain["song_id"])
            self.assertEqual(uncertain["raw_parse_status"], "parsed")
            self.assertEqual(uncertain["grouping_status"], "grouped")
            self.assertIsNone(uncertain["output_path"])

            manifest_text = first_manifest.decode("utf-8")
            self.assertNotIn(str(game.resolve()), manifest_text)
            self.assertNotIn(str(output.resolve()), manifest_text)

            extra = output / "charts" / "unclaimed.json"
            extra.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                BatchExtractionError, "outside the current complete plan"
            ):
                extract_all_charts(
                    game,
                    output,
                    candidates,
                    _index(),
                    grouping_census_summary=_census(candidate_count=2),
                    note_configs_by_uid=_note_configs(),
                    note_data_provenance={"source": "synthetic-note-data"},
                    loader=loader,
                )

    def test_one_object_failure_does_not_abort_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            game = temporary_root / "game"
            output = temporary_root / "output"
            bundle = game / "data" / "fixture.bundle"
            bundle.parent.mkdir(parents=True)
            bundle_bytes = b"synthetic-bundle"
            bundle.write_bytes(bundle_bytes)
            payload = _payload()
            candidates = [
                _candidate(
                    chart_id="fixture_map1",
                    path_id=1,
                    payload=payload,
                    bundle_bytes=bundle_bytes,
                ),
                _candidate(
                    chart_id="tutorial_v2_map1",
                    path_id=2,
                    payload=payload,
                    bundle_bytes=bundle_bytes,
                ),
            ]

            manifest = extract_all_charts(
                game,
                output,
                candidates,
                _index(),
                grouping_census_summary=_census(candidate_count=2),
                note_configs_by_uid=_note_configs(),
                note_data_provenance={},
                loader=lambda _path: _Environment(
                    [_Object(1, payload, "fixture_map1")]
                ),
            )

            by_id = {row["chart_id"]: row for row in manifest["charts"]}
            self.assertEqual(by_id["fixture_map1"]["status"], "success")
            self.assertEqual(by_id["tutorial_v2_map1"]["status"], "failed")
            self.assertEqual(
                by_id["tutorial_v2_map1"]["reason"], "object-evidence-mismatch"
            )
            self.assertEqual(manifest["status_counts"], {"failed": 1, "success": 1})
            self.assertEqual(manifest["milestone_status"], "M8-not-achieved")
            self.assertTrue(manifest["complete"])

    def test_stale_source_is_classified_without_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            game = temporary_root / "game"
            output = temporary_root / "output"
            bundle = game / "data" / "fixture.bundle"
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(b"current")
            payload = _payload()
            candidate = _candidate(
                chart_id="fixture_map1",
                path_id=1,
                payload=payload,
                bundle_bytes=b"stale",
            )

            manifest = extract_all_charts(
                game,
                output,
                [candidate],
                _index(include_unresolved=False, bundle_bytes=b"stale"),
                grouping_census_summary=_census(candidate_count=1),
                note_configs_by_uid=_note_configs(),
                note_data_provenance={},
                loader=lambda _path: self.fail("stale source must not be loaded"),
            )

            self.assertEqual(manifest["charts"][0]["status"], "failed")
            self.assertEqual(manifest["charts"][0]["reason"], "stale-source-fingerprint")
            self.assertEqual(manifest["charts"][0]["raw_parse_status"], "not-attempted")

    def test_failed_rerun_does_not_replace_manifest_while_old_chart_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            output = root / "output"
            bundle = game / "data" / "fixture.bundle"
            bundle.parent.mkdir(parents=True)
            bundle_bytes = b"synthetic-bundle"
            bundle.write_bytes(bundle_bytes)
            payload = _payload()
            candidate = _candidate(
                chart_id="fixture_map1",
                path_id=1,
                payload=payload,
                bundle_bytes=bundle_bytes,
            )
            kwargs = {
                "grouping_census_summary": _census(candidate_count=1),
                "note_configs_by_uid": _note_configs(),
                "note_data_provenance": {},
                "loader": lambda _path: _Environment(
                    [_Object(1, payload, "fixture_map1")]
                ),
            }
            extract_all_charts(
                game,
                output,
                [candidate],
                _index(include_unresolved=False),
                **kwargs,
            )
            previous_manifest = (output / "manifest.json").read_bytes()
            bundle.write_bytes(b"changed-source")

            with self.assertRaisesRegex(
                BatchExtractionError, "manifest was not replaced"
            ):
                extract_all_charts(
                    game,
                    output,
                    [candidate],
                    _index(include_unresolved=False),
                    **kwargs,
                )

            self.assertEqual(previous_manifest, (output / "manifest.json").read_bytes())

    def test_output_safety_and_path_component_encoding(self) -> None:
        self.assertEqual(safe_path_component("song/name"), "song%2Fname")
        self.assertEqual(safe_path_component(".."), "%2E%2E")
        self.assertEqual(safe_path_component("CON"), "%43%4F%4E")

        with tempfile.TemporaryDirectory() as temporary:
            game = Path(temporary) / "game"
            game.mkdir()
            with self.assertRaisesRegex(BatchExtractionError, "must not be inside"):
                extract_all_charts(
                    game,
                    game / "extracted",
                    [{}],
                    {},
                    grouping_census_summary={},
                    note_configs_by_uid={},
                    note_data_provenance={},
                )

    def test_census_and_index_must_cover_the_exact_candidate_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game = Path(temporary) / "game"
            game.mkdir()
            payload = _payload()
            candidate = _candidate(
                chart_id="fixture_map1",
                path_id=1,
                payload=payload,
                bundle_bytes=b"unused",
            )

            with self.assertRaisesRegex(BatchExtractionError, "census candidate_count"):
                extract_all_charts(
                    game,
                    Path(temporary) / "output-a",
                    [candidate],
                    _index(include_unresolved=False),
                    grouping_census_summary=_census(candidate_count=2),
                    note_configs_by_uid={},
                    note_data_provenance={},
                )

            with self.assertRaisesRegex(BatchExtractionError, "chart ID sets differ"):
                extract_all_charts(
                    game,
                    Path(temporary) / "output-b",
                    [candidate],
                    _index(include_unresolved=True),
                    grouping_census_summary=_census(candidate_count=1),
                    note_configs_by_uid={},
                    note_data_provenance={},
                )

            output = Path(temporary) / "output"
            payload = _payload()
            bundle_bytes = b"unused-bundle-evidence"
            first = _candidate(
                chart_id="fixture_map1",
                path_id=1,
                payload=payload,
                bundle_bytes=bundle_bytes,
            )
            second = _candidate(
                chart_id="FIXTURE_map1",
                path_id=2,
                payload=payload,
                bundle_bytes=bundle_bytes,
            )
            with self.assertRaisesRegex(
                BatchExtractionError, "case-insensitive candidate chart id collision"
            ):
                extract_all_charts(
                    game,
                    output,
                    [first, second],
                    _index(include_unresolved=False),
                    grouping_census_summary={},
                    note_configs_by_uid={},
                    note_data_provenance={},
                )


if __name__ == "__main__":
    unittest.main()
