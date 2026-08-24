from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from musedash_chart_extractor import (
    ChartStore,
    ChartStoreError,
    CsvExporter,
    JsonExporter,
    UnresolvedChartError,
)
from musedash_chart_extractor.store import extract_chart_store
from musedash_chart_extractor.store.schema import contained_path


def _string(value: str, *, width: int = 1) -> bytes:
    encoded = value.encode("latin-1" if width == 0 else "utf-16-le")
    return bytes([width]) + struct.pack("<i", len(value)) + encoded


def _type_name(type_id: int, value: str) -> bytes:
    return b"\x2f" + struct.pack("<i", type_id) + _string(value)


def _decimal_raw(text: str) -> bytes:
    negative = text.startswith("-")
    unsigned = text[1:] if negative else text
    whole, separator, fraction = unsigned.partition(".")
    scale = len(fraction) if separator else 0
    coefficient = int(f"{whole}{fraction}" if separator else whole)
    flags = (scale << 16) | (0x80000000 if negative else 0)
    return struct.pack(
        "<IIII",
        flags,
        (coefficient >> 64) & 0xFFFFFFFF,
        coefficient & 0xFFFFFFFF,
        (coefficient >> 32) & 0xFFFFFFFF,
    )


def _named(tag: int, name: str, body: bytes = b"") -> bytes:
    return bytes([tag]) + _string(name) + body


def _record(index: int, *, first: bool, note_uid_value: str = "fixture-note") -> bytes:
    record_type = (
        _type_name(1, "GameLogic.MusicData, Assembly-CSharp")
        if first
        else b"\x30" + struct.pack("<i", 1)
    )
    config_type = (
        _type_name(2, "GameLogic.MusicConfigData, Assembly-CSharp")
        if first
        else b"\x30" + struct.pack("<i", 2)
    )
    time = "0" if first else "3.125"
    note_uid = (
        _named(0x2D, "note_uid")
        if first
        else _named(0x27, "note_uid", _string(note_uid_value, width=0))
    )
    return b"".join(
        [
            b"\x04",
            record_type,
            _named(0x13, "objId", struct.pack("<h", index)),
            _named(0x23, "tick", _decimal_raw(time)),
            _named(0x03, "configData", config_type),
            _named(0x17, "id", struct.pack("<i", index)),
            _named(0x23, "time", _decimal_raw(time)),
            note_uid,
            _named(0x23, "length", _decimal_raw("1.5" if index else "0")),
            _named(0x2B, "blood", b"\x00"),
            _named(0x17, "pathway", struct.pack("<i", index)),
            b"\x05",
            _named(0x2B, "isLongPressing", b"\x00"),
            _named(0x17, "doubleIdx", struct.pack("<i", -1)),
            _named(0x2D, "sameTickNoteIdx"),
            _named(0x2B, "isDouble", b"\x00"),
            _named(0x2B, "isLongPressEnd", b"\x00"),
            _named(0x23, "longPressPTick", _decimal_raw("0")),
            _named(0x17, "endIndex", struct.pack("<i", 0)),
            _named(0x23, "dt", _decimal_raw("-1.25" if index else "0")),
            _named(0x17, "longPressNum", struct.pack("<i", 0)),
            _named(0x23, "showTick", _decimal_raw(time)),
            b"\x05",
        ]
    )


def _payload(*, note_uid_value: str = "fixture-note") -> bytes:
    return b"".join(
        [
            b"\x01",
            _string("musicDatas"),
            _type_name(
                0,
                "System.Collections.Generic.List`1[[GameLogic.MusicData, Assembly-CSharp]], mscorlib",
            ),
            struct.pack("<i", 0),
            b"\x06",
            struct.pack("<q", 2),
            _record(0, first=True),
            _record(1, first=False, note_uid_value=note_uid_value),
            b"\x07\x05",
            _named(0x23, "delay", _decimal_raw("0")),
            _named(0x2D, "dialogEvents"),
        ]
    )


class _Object:
    def __init__(self, payload: bytes, *, chart_id: str = "fixture_map1", path_id: int = 101) -> None:
        self.path_id = path_id
        self.byte_size = len(payload) + 128
        self.type = SimpleNamespace(name="MonoBehaviour")
        self._payload = payload
        self._chart_id = chart_id

    def parse_as_dict(self) -> dict:
        return {
            "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
            "m_Enabled": 1,
            "m_Script": {"m_FileID": 0, "m_PathID": 7},
            "m_Name": self._chart_id,
            "serializationData": {
                "SerializedFormat": 0,
                "SerializedBytes": list(self._payload),
                "SerializedBytesString": "",
                "ReferencedUnityObjects": [],
                "Prefab": None,
                "PrefabModificationsReferencedUnityObjects": [],
                "PrefabModifications": [],
                "SerializationNodes": [],
            },
            "mapName": self._chart_id,
            "music": "fixture_music",
            "scene": "fixture_scene",
            "difficulty": 1,
            "md5": "fixture-md5",
            "bpm": 120,
            "sceneEvents": [{"uid": "synthetic-scene"}],
            "syntheticUnknown": {"preserve": True},
        }


def _candidate(
    bundle: bytes,
    payload: bytes,
    *,
    chart_id: str = "fixture_map1",
    path_id: int = 101,
) -> dict:
    return {
        "schema_version": 1,
        "phase": 3,
        "status": "unvalidated_candidate",
        "validation_status": "unvalidated",
        "inventory_fingerprint": "sha256:" + "1" * 64,
        "source": "data/fixture.bundle",
        "source_size": len(bundle),
        "source_sha256": hashlib.sha256(bundle).hexdigest(),
        "container_path": f"Assets/Static Resources/Data/Configs/StageInfos/{chart_id}.asset",
        "path_id": path_id,
        "object_type": "MonoBehaviour",
        "object_byte_size": len(payload) + 128,
        "rank": 1,
        "metadata": {
            "asset_name": chart_id,
            "map_name_raw": chart_id,
            "music": "fixture_music",
            "scene": "fixture_scene",
            "difficulty_raw": 1,
            "md5": "fixture-md5",
            "bpm_raw": 120,
            "scene_event_count": 1,
        },
        "structure": {
            "serialized_format": 0,
            "serialized_payload_byte_count": len(payload),
            "serialized_payload_sha256": hashlib.sha256(payload).hexdigest(),
        },
    }


def _song_index(candidate: dict) -> dict:
    chart = {
        "chart_id": "fixture_map1",
        "song_id": "fixture-song",
        "difficulty_id": 1,
        "difficulty_key": "difficulty1",
        "difficulty_raw": 1,
        "difficulty_level_raw": 4,
        "relationship_status": "exact-note-json",
        "relationship_evidence": ["synthetic exact relationship"],
        "warnings": [],
        "source": {
            "bundle": candidate["source"],
            "bundle_byte_count": candidate["source_size"],
            "bundle_sha256": candidate["source_sha256"],
            "container_path": candidate["container_path"],
            "path_id": candidate["path_id"],
            "object_type": candidate["object_type"],
        },
        "addressables": {
            "primary_key": "fixture_map1",
            "dependency_local_path": candidate["source"],
        },
        "stage_info_raw": dict(candidate["metadata"]),
    }
    return {
        "schema_version": 1,
        "inventory_fingerprint": candidate["inventory_fingerprint"],
        "catalog": {
            "addressables_version": "1.21.20",
            "build_result_hash": "synthetic-build",
            "source": {
                "catalog": "aa/catalog.json",
                "catalog_sha256": "2" * 64,
                "catalog_size": 10,
                "settings": "aa/settings.json",
                "settings_sha256": "3" * 64,
                "settings_size": 11,
            },
        },
        "counts": {
            "candidate_chart_count": 1,
            "indexed_chart_count": 1,
            "unresolved_chart_count": 0,
        },
        "songs": [
            {
                "song_id": "fixture-song",
                "metadata": {
                    "title_raw": "Fixture Song",
                    "artist_raw": "Synthetic",
                    "bpm_raw": 120,
                    "music_raw": "fixture_music",
                    "note_json_raw": "fixture_map",
                    "scene_raw": "fixture_scene",
                    "raw": {"unknownAlbumField": "preserve"},
                },
                "source": {"album_number": 1, "row_index": 0},
                "chart_count": 1,
                "charts": [chart],
            }
        ],
        "unresolved_charts": [],
    }


def _unresolved_index(resolved: dict, unresolved: dict) -> dict:
    result = deepcopy(_song_index(resolved))
    unresolved_chart = deepcopy(result["songs"][0]["charts"][0])
    unresolved_chart["chart_id"] = unresolved["metadata"]["asset_name"]
    unresolved_chart["song_id"] = None
    unresolved_chart["source"] = {
        "bundle": unresolved["source"],
        "bundle_byte_count": unresolved["source_size"],
        "bundle_sha256": unresolved["source_sha256"],
        "container_path": unresolved["container_path"],
        "path_id": unresolved["path_id"],
        "object_type": unresolved["object_type"],
    }
    unresolved_chart["addressables"] = {
        "primary_key": unresolved["metadata"]["asset_name"],
        "dependency_local_path": unresolved["source"],
    }
    unresolved_chart["stage_info_raw"] = deepcopy(unresolved["metadata"])
    result["counts"] = {
        "candidate_chart_count": 2,
        "indexed_chart_count": 1,
        "unresolved_chart_count": 1,
    }
    result["unresolved_charts"] = [unresolved_chart]
    return result


def _census(candidate: dict, *, candidate_count: int = 1, source_count: int = 1) -> dict:
    return {
        "schema_version": 1,
        "phase": 9,
        "status": "census-complete",
        "complete": True,
        "inventory_fingerprint": candidate["inventory_fingerprint"],
        "grouping_rule_version": "composite-neutral-base-negative-id-singleton-v2",
        "candidate_count": candidate_count,
        "source_count": source_count,
        "raw_parse_status_counts": {"parsed": candidate_count},
        "grouping_status_counts": {"grouped": candidate_count},
        "parsed_raw_record_count": candidate_count * 2,
        "grouped_logical_object_count": candidate_count,
    }


def _extract(
    root: Path,
    candidates: list[dict],
    song_index: dict,
    objects: list[_Object],
    *,
    note_configs: dict[str, list[dict]] | None = None,
    census: dict | None = None,
    loader=None,
) -> tuple[Path, dict]:
    bundle = b"synthetic bundle"
    game = root / "game"
    source = game / "data" / "fixture.bundle"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(bundle)
    output = root / "MuseDashChartStore"
    environment = SimpleNamespace(objects=objects)
    manifest = extract_chart_store(
        game,
        output,
        candidates,
        song_index,
        grouping_census_summary=census
        or _census(candidates[0], candidate_count=len(candidates), source_count=1),
        note_configs_by_uid=note_configs
        if note_configs is not None
        else {"fixture-note": [{"uid": "fixture-note", "type": 3, "unknown": "preserve"}]},
        note_data_provenance={"row_count": 1, "uid_count": 1},
        parser_family="sirenix-odin-binary-observed-stageinfo-subset",
        parser_version="strict-stageinfo-v1",
        loader=loader or (lambda _path: environment),
    )
    return output, manifest


class ChartStoreTests(unittest.TestCase):
    def test_build_read_and_lazily_canonicalize_one_synthetic_chart(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        census = _census(candidate)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            source = game / "data" / "fixture.bundle"
            source.parent.mkdir(parents=True)
            source.write_bytes(bundle)
            output = root / "MuseDashChartStore"
            environment = SimpleNamespace(objects=[_Object(payload)])

            manifest = extract_chart_store(
                game,
                output,
                [candidate],
                _song_index(candidate),
                grouping_census_summary=census,
                note_configs_by_uid={
                    "fixture-note": [
                        {"uid": "fixture-note", "type": 99, "unknown": "preserve"}
                    ]
                },
                note_data_provenance={"row_count": 1, "uid_count": 1},
                parser_family="sirenix-odin-binary-observed-stageinfo-subset",
                parser_version="strict-stageinfo-v1",
                loader=lambda _path: environment,
            )

            self.assertEqual(manifest["store_schema_version"], "1.0.0")
            self.assertEqual(manifest["status_counts"], {"success": 1})
            with ChartStore.open(output) as store:
                refs = list(store.iter_charts())
                self.assertEqual([ref.chart_id for ref in refs], ["fixture_map1"])
                self.assertEqual(store.read_payload("fixture_map1"), payload)
                chart = store.load_chart("fixture_map1")

            self.assertEqual(chart["schema_version"], "1.1.0")
            self.assertEqual(chart["chart_id"], "fixture_map1")
            self.assertEqual(chart["event_count"], 1)
            self.assertEqual(chart["events"][0]["type_id"], 99)
            self.assertIsNone(chart["events"][0]["type_name"])
            self.assertEqual(chart["events"][0]["type_status"], "unknown")
            envelope = chart["raw"]["experimental_chart"]["stage_info_raw"]
            self.assertEqual(envelope["serializationData"]["SerializedBytes"], list(payload))
            self.assertEqual(envelope["syntheticUnknown"], {"preserve": True})
            self.assertEqual(
                chart["raw"]["experimental_chart"]["note_data"]["configs_by_uid_raw"],
                {
                    "fixture-note": [
                        {"uid": "fixture-note", "type": 99, "unknown": "preserve"}
                    ]
                },
            )
            JsonExporter(indent=None).export(chart, root / "chart.json")
            CsvExporter().export(chart, root / "chart.csv")
            self.assertTrue((root / "chart.json").is_file())
            self.assertTrue((root / "chart.csv").is_file())

    def test_duplicate_payload_is_stored_once_and_uncertain_chart_is_retained(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        resolved = _candidate(bundle, payload)
        unresolved = _candidate(bundle, payload, chart_id="tutorial_v2_map1", path_id=202)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, manifest = _extract(
                root,
                [resolved, unresolved],
                _unresolved_index(resolved, unresolved),
                [_Object(payload), _Object(payload, chart_id="tutorial_v2_map1", path_id=202)],
            )

            self.assertEqual(manifest["payload_count"], 1)
            self.assertEqual(manifest["payload_byte_count"], len(payload))
            self.assertEqual(manifest["status_counts"], {"success": 1, "uncertain": 1})
            payload_files = list((output / "payloads" / "sha256").rglob("*.odin"))
            self.assertEqual(len(payload_files), 1)
            with ChartStore.open(output) as store:
                refs = list(store.iter_charts())
                self.assertEqual([ref.status for ref in refs], ["success", "uncertain"])
                self.assertEqual(store.read_payload("tutorial_v2_map1"), payload)
                with self.assertRaises(UnresolvedChartError):
                    store.load_chart("tutorial_v2_map1")

    def test_sqlite_envelope_preserves_unknown_fields_without_serialized_bytes(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            output, _manifest = _extract(
                Path(temporary), [candidate], _song_index(candidate), [_Object(payload)]
            )
            with closing(sqlite3.connect(output / "index.sqlite3")) as connection:
                envelope_json = connection.execute(
                    "SELECT envelope_json FROM stage_info WHERE chart_id = ?",
                    ("fixture_map1",),
                ).fetchone()[0]
            envelope = json.loads(envelope_json)
            self.assertEqual(envelope["syntheticUnknown"], {"preserve": True})
            self.assertEqual(envelope["sceneEvents"], [{"uid": "synthetic-scene"}])
            self.assertEqual(envelope["serializationData"]["SerializedFormat"], 0)
            self.assertNotIn("SerializedBytes", envelope["serializationData"])

    def test_unmapped_note_uid_is_an_explicit_empty_foreign_key_target(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            output, _manifest = _extract(
                Path(temporary),
                [candidate],
                _song_index(candidate),
                [_Object(payload)],
                note_configs={},
            )
            with closing(sqlite3.connect(output / "index.sqlite3")) as connection:
                row = connection.execute(
                    "SELECT rows_json, row_count FROM note_configs WHERE uid = ?",
                    ("fixture-note",),
                ).fetchone()
                foreign_keys = connection.execute(
                    "PRAGMA foreign_key_list(chart_note_uids)"
                ).fetchall()
            self.assertEqual(row, ("[]", 0))
            self.assertTrue(
                any(
                    foreign_key[2] == "note_configs"
                    and foreign_key[3] == "uid"
                    and foreign_key[4] == "uid"
                    for foreign_key in foreign_keys
                )
            )
            with ChartStore.open(output) as store:
                chart = store.load_chart("fixture_map1")
            self.assertEqual(
                chart["raw"]["experimental_chart"]["note_data"]["unmapped_note_uids"],
                ["fixture-note"],
            )

    def test_valid_odin_payload_containing_every_byte_round_trips_exactly(self) -> None:
        note_uid = "".join(chr(value) for value in range(256))
        payload = _payload(note_uid_value=note_uid)
        self.assertIn(bytes(range(256)), payload)
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            output, _manifest = _extract(
                Path(temporary),
                [candidate],
                _song_index(candidate),
                [_Object(payload)],
                note_configs={note_uid: [{"uid": note_uid, "type": 3}]},
            )
            with ChartStore.open(output) as store:
                self.assertEqual(store.read_payload("fixture_map1"), payload)
                self.assertEqual(store.load_chart("fixture_map1")["event_count"], 1)

    def test_corrupt_payload_fails_closed(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            output, _manifest = _extract(
                Path(temporary), [candidate], _song_index(candidate), [_Object(payload)]
            )
            payload_path = next((output / "payloads").rglob("*.odin"))
            payload_path.write_bytes(payload[:-1])
            with ChartStore.open(output) as store:
                with self.assertRaisesRegex(ChartStoreError, "fingerprint mismatch"):
                    store.read_payload("fixture_map1")

    def test_path_traversal_and_casefold_chart_collision_fail_closed(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        first = _candidate(bundle, payload)
        second = _candidate(bundle, payload, chart_id="FIXTURE_MAP1", path_id=202)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ChartStoreError, "case-insensitive"):
                _extract(
                    root,
                    [first, second],
                    _unresolved_index(first, second),
                    [_Object(payload), _Object(payload, chart_id="FIXTURE_MAP1", path_id=202)],
                )

        traversing = deepcopy(first)
        traversing["source"] = "../escape.bundle"
        index = _song_index(traversing)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            with self.assertRaisesRegex(ChartStoreError, "escapes game directory"):
                extract_chart_store(
                    game,
                    root / "store",
                    [traversing],
                    index,
                    grouping_census_summary=_census(traversing),
                    note_configs_by_uid={},
                    note_data_provenance={},
                    parser_family="sirenix-odin-binary-observed-stageinfo-subset",
                    parser_version="strict-stageinfo-v1",
                )

    def test_contained_path_rejects_an_internal_symlink_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            link = root / "payloads"
            with patch.object(
                Path,
                "is_symlink",
                autospec=True,
                side_effect=lambda path: path == link,
            ):
                with self.assertRaisesRegex(ChartStoreError, "symbolic link"):
                    contained_path(
                        root,
                        "payloads/sha256/aa/" + "a" * 64 + ".odin",
                        context="payload path",
                    )

    def test_contained_path_rejects_an_internal_windows_junction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            junction = root / "payloads"
            original_is_junction = getattr(Path, "is_junction", lambda _path: False)

            def is_junction(path: Path) -> bool:
                return path == junction or original_is_junction(path)

            with patch.object(
                Path, "is_junction", autospec=True, side_effect=is_junction, create=True
            ):
                with self.assertRaisesRegex(ChartStoreError, "junction"):
                    contained_path(
                        root,
                        "payloads/sha256/aa/" + "a" * 64 + ".odin",
                        context="payload path",
                    )

    def test_rerun_rejects_a_nested_junction_before_cleaning_staging(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "MuseDashChartStore" / ".staging" / "nested"
            nested.mkdir(parents=True)
            marker = nested / "keep.txt"
            marker.write_text("outside target", encoding="utf-8")
            original_is_junction = getattr(Path, "is_junction", lambda _path: False)

            def is_junction(path: Path) -> bool:
                return path == nested or original_is_junction(path)

            with patch.object(
                Path, "is_junction", autospec=True, side_effect=is_junction, create=True
            ):
                with self.assertRaisesRegex(
                    ChartStoreError, "staging tree contains.*junction"
                ):
                    _extract(
                        root,
                        [candidate],
                        _song_index(candidate),
                        [_Object(payload)],
                    )
            self.assertEqual(marker.read_text(encoding="utf-8"), "outside target")

    def test_interrupted_build_has_no_readable_complete_store(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ChartStoreError, "cannot load candidate source"):
                _extract(
                    root,
                    [candidate],
                    _song_index(candidate),
                    [_Object(payload)],
                    loader=lambda _path: (_ for _ in ()).throw(RuntimeError("interrupted")),
                )
            output = root / "MuseDashChartStore"
            self.assertFalse((output / "store.json").exists())
            self.assertTrue((output / ".building").exists())
            with self.assertRaisesRegex(ChartStoreError, "incomplete"):
                ChartStore.open(output)

    def test_same_directory_rerun_has_same_logical_digest_and_payload_set(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, first = _extract(
                root, [candidate], _song_index(candidate), [_Object(payload)]
            )
            first_payloads = {
                path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (output / "payloads").rglob("*.odin")
            }
            output, second = _extract(
                root, [candidate], _song_index(candidate), [_Object(payload)]
            )
            second_payloads = {
                path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (output / "payloads").rglob("*.odin")
            }
            self.assertEqual(first["logical_store_digest"], second["logical_store_digest"])
            self.assertEqual(first_payloads, second_payloads)

    def test_same_directory_rerun_rejects_a_stale_payload_from_an_old_resource_set(self) -> None:
        first_payload = _payload()
        second_payload = _payload(note_uid_value="replacement-note")
        bundle = b"synthetic bundle"
        first_candidate = _candidate(bundle, first_payload)
        second_candidate = _candidate(bundle, second_payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _extract(
                root,
                [first_candidate],
                _song_index(first_candidate),
                [_Object(first_payload)],
            )

            with self.assertRaisesRegex(ChartStoreError, "unindexed payload"):
                _extract(
                    root,
                    [second_candidate],
                    _song_index(second_candidate),
                    [_Object(second_payload)],
                    note_configs={
                        "replacement-note": [
                            {"uid": "replacement-note", "type": 3}
                        ]
                    },
                )

    def test_grouping_census_totals_must_match_the_strict_current_parse(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        census = _census(candidate)
        census["parsed_raw_record_count"] = 3
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ChartStoreError, "census raw record count"):
                _extract(
                    Path(temporary),
                    [candidate],
                    _song_index(candidate),
                    [_Object(payload)],
                    census=census,
                )

    def test_reader_rejects_an_unsupported_recorded_parser_version(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            output, _manifest = _extract(
                Path(temporary), [candidate], _song_index(candidate), [_Object(payload)]
            )
            index_path = output / "index.sqlite3"
            with closing(sqlite3.connect(index_path)) as connection:
                connection.execute(
                    "UPDATE metadata SET value_json = ? WHERE key = 'parser_version'",
                    (json.dumps("future-parser"),),
                )
                connection.commit()
            manifest_path = output / "store.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["index"]["byte_count"] = index_path.stat().st_size
            manifest["index"]["sha256"] = hashlib.sha256(
                index_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ChartStoreError, "unsupported parser version"):
                ChartStore.open(output)

    def test_reader_rejects_manifest_canonical_schema_disagreement(self) -> None:
        payload = _payload()
        bundle = b"synthetic bundle"
        candidate = _candidate(bundle, payload)
        with tempfile.TemporaryDirectory() as temporary:
            output, _manifest = _extract(
                Path(temporary), [candidate], _song_index(candidate), [_Object(payload)]
            )
            manifest_path = output / "store.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["canonical_schema_version"] = "9.9.9"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                ChartStoreError, "canonical_schema_version differs"
            ):
                ChartStore.open(output)


if __name__ == "__main__":
    unittest.main()
