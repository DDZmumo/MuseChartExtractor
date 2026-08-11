from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from musedash_chart_extractor.discovery.indexing import (
    STAGE_INFO_CLASS,
    ChartIndexError,
    build_song_chart_index,
    read_album_rows,
    select_album_sources,
)


class FakeTextAsset:
    def __init__(self, path_id: int, content: str) -> None:
        self.path_id = path_id
        self.type = SimpleNamespace(name="TextAsset")
        self._content = content

    def read(self) -> SimpleNamespace:
        return SimpleNamespace(m_Script=self._content)


def _album_report(source: str, payload: bytes, number: int, path_id: int) -> dict:
    return {
        "source": source,
        "parseable": True,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "container_entries": [
            {
                "path": f"Assets/Static Resources/Data/Configs/others/ALBUM{number}.json",
                "path_id": path_id,
                "type": "TextAsset",
                "resolved": True,
            }
        ],
    }


def _candidate(
    chart_id: str,
    source: str,
    *,
    music: str,
    difficulty_raw: int,
    path_id: int,
    map_name: str | None = None,
) -> dict:
    return {
        "status": "unvalidated_candidate",
        "inventory_fingerprint": "sha256:fixture",
        "source": source,
        "source_size": 123,
        "source_sha256": "a" * 64,
        "container_path": (
            "Assets/Static Resources/Data/Configs/StageInfos/" f"{chart_id}.asset"
        ),
        "path_id": path_id,
        "object_type": "MonoBehaviour",
        "metadata": {
            "asset_name": chart_id,
            "music": music,
            "scene": "scene_fixture",
            "bpm_raw": 120.0,
            "difficulty_raw": difficulty_raw,
            "map_name_raw": map_name if map_name is not None else chart_id,
            "md5": f"{path_id:032x}",
        },
    }


def _album(song_number: int, *, slots: tuple[int, ...] = (1, 2)) -> dict:
    raw = {
        "uid": f"fixture-{song_number}",
        "name": f"Fixture Song {song_number}",
        "author": "Fixture Artist",
        "bpm": "120",
        "music": f"song_{song_number}_music",
        "noteJson": f"song_{song_number}_map",
        "scene": "scene_fixture",
        "unknownFutureField": {"kept": True},
    }
    for slot in slots:
        raw[f"difficulty{slot}"] = str(slot + 2)
    return {
        "song_id": raw["uid"],
        "music_raw": raw["music"],
        "note_json_raw": raw["noteJson"],
        "raw": raw,
        "source": {
            "album_number": song_number,
            "row_index": 0,
            "source": f"album{song_number}.bundle",
            "source_sha256": "b" * 64,
            "path_id": song_number,
        },
    }


def _catalog(candidates: list[dict]) -> dict:
    sources = sorted({candidate["source"] for candidate in candidates})
    entries: list[dict] = []
    internal_ids: list[dict] = []
    dependency_by_source: dict[str, int] = {}
    for source in sources:
        dependency_by_source[source] = len(entries)
        internal_id_index = len(internal_ids)
        internal_ids.append({"local_path": source})
        entries.append(
            {
                "entry_index": len(entries),
                "primary_key": f"fixture-{internal_id_index}.bundle",
                "resource_type_index": 0,
                "internal_id_index": internal_id_index,
                "dependency_entry_indices": [],
            }
        )
    for candidate in candidates:
        entries.append(
            {
                "entry_index": len(entries),
                "primary_key": candidate["metadata"]["asset_name"],
                "resource_type_index": 1,
                "internal_id_index": 0,
                "dependency_entry_indices": [dependency_by_source[candidate["source"]]],
            }
        )
    return {
        "source": {
            "catalog": "fixture/catalog.json",
            "catalog_sha256": "c" * 64,
        },
        "addressables_version": "fixture",
        "build_result_hash": "fixture-build",
        "entries": entries,
        "internal_ids": internal_ids,
        "resource_types": [
            {"m_ClassName": "FixtureBundle"},
            {"m_ClassName": STAGE_INFO_CLASS},
        ],
    }


class AlbumReaderTests(unittest.TestCase):
    def test_reads_comments_and_trailing_commas_with_json5(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_payload = b"synthetic UnityFS"
            (root / "album1.bundle").write_bytes(source_payload)
            content = """
            [
              // A real ALBUM file contains this form of line comment.
              {
                uid: 'fixture-1',
                music: 'https://example.invalid//music',
                noteJson: 'fixture_map',
                difficulty1: 'E',
              },
            ]
            """
            report = _album_report("album1.bundle", source_payload, 1, 42)

            sources = select_album_sources([report])
            rows, provenance = read_album_rows(
                root,
                sources,
                loader=lambda _: SimpleNamespace(
                    objects=[FakeTextAsset(42, content)]
                ),
            )

            self.assertEqual(rows[0]["song_id"], "fixture-1")
            self.assertEqual(rows[0]["music_raw"], "https://example.invalid//music")
            self.assertEqual(rows[0]["raw"]["difficulty1"], "E")
            self.assertEqual(provenance[0]["row_count"], 1)
            self.assertEqual(
                provenance[0]["content_sha256"],
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )

    def test_album_source_fingerprint_and_duplicate_uid_fail_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "album1.bundle").write_bytes(b"current")
            stale = _album_report("album1.bundle", b"stale!", 1, 42)
            with self.assertRaisesRegex(ChartIndexError, "fingerprint is stale"):
                read_album_rows(
                    root,
                    select_album_sources([stale]),
                    loader=lambda _: self.fail("stale source must not load"),
                )

            current = _album_report("album1.bundle", b"current", 1, 42)
            duplicate = """[
              {uid:'same', music:'one', noteJson:'one_map'},
              {uid:'same', music:'two', noteJson:'two_map'},
            ]"""
            with self.assertRaisesRegex(ChartIndexError, "duplicate song uid"):
                read_album_rows(
                    root,
                    select_album_sources([current]),
                    loader=lambda _: SimpleNamespace(
                        objects=[FakeTextAsset(42, duplicate)]
                    ),
                )


class SongChartIndexTests(unittest.TestCase):
    def test_indexes_three_songs_and_uses_map_slot_as_difficulty_id(self) -> None:
        candidates: list[dict] = []
        albums: list[dict] = []
        for number in range(1, 4):
            slots = (1, 4) if number == 1 else (1, 2)
            albums.append(_album(number, slots=slots))
            for slot in slots:
                candidates.append(
                    _candidate(
                        f"song_{number}_map{slot}",
                        f"song-{number}.bundle",
                        music=f"song_{number}_music",
                        difficulty_raw=3 if slot == 4 else slot,
                        path_id=number * 10 + slot,
                        map_name=(
                            r"D:\developer\song_1_map4.bms"
                            if number == 1 and slot == 4
                            else None
                        ),
                    )
                )

        result = build_song_chart_index(
            candidates,
            albums,
            [{"album_number": number} for number in range(1, 4)],
            _catalog(candidates),
        )

        self.assertEqual(result["milestone_status"], "M5-achieved")
        self.assertTrue(result["phase_gate"]["passed"])
        self.assertEqual(result["counts"]["song_count"], 3)
        self.assertEqual(result["counts"]["indexed_chart_count"], 6)
        self.assertEqual(
            result["counts"]["relationship_status_counts"],
            {"exact-note-json": 6},
        )
        first = next(song for song in result["songs"] if song["song_id"] == "fixture-1")
        hidden = next(chart for chart in first["charts"] if chart["chart_id"].endswith("map4"))
        self.assertEqual(hidden["difficulty_id"], 4)
        self.assertEqual(hidden["difficulty_raw"], 3)
        self.assertIn(
            "stage_info_difficulty_raw_differs_from_map_slot",
            hidden["warnings"],
        )
        self.assertIn(
            "stage_info_map_name_is_absolute_development_path",
            hidden["warnings"],
        )
        self.assertEqual(
            first["metadata"]["raw"]["unknownFutureField"],
            {"kept": True},
        )

    def test_duplicate_music_uses_exact_note_json_and_unresolved_is_retained(self) -> None:
        albums = [_album(1), _album(2)]
        albums[1]["music_raw"] = albums[0]["music_raw"]
        albums[1]["raw"]["music"] = albums[0]["music_raw"]
        candidates = [
            _candidate(
                "song_1_map1",
                "shared.bundle",
                music="song_1_music",
                difficulty_raw=1,
                path_id=1,
            ),
            _candidate(
                "song_2_map1",
                "shared.bundle",
                music="song_1_music",
                difficulty_raw=1,
                path_id=2,
            ),
            _candidate(
                "tutorial_v2_map1",
                "tutorial.bundle",
                music="missing_music",
                difficulty_raw=1,
                path_id=3,
            ),
        ]

        result = build_song_chart_index(
            candidates,
            albums,
            [{"album_number": 1}, {"album_number": 2}],
            _catalog(candidates),
        )

        self.assertEqual(result["counts"]["indexed_chart_count"], 2)
        self.assertEqual(result["counts"]["unresolved_chart_count"], 1)
        self.assertEqual(
            result["unresolved_charts"][0]["chart_id"],
            "tutorial_v2_map1",
        )
        linked = {
            chart["chart_id"]: song["song_id"]
            for song in result["songs"]
            for chart in song["charts"]
        }
        self.assertEqual(linked["song_1_map1"], "fixture-1")
        self.assertEqual(linked["song_2_map1"], "fixture-2")

    def test_addressables_dependency_mismatch_fails_loudly(self) -> None:
        candidate = _candidate(
            "song_1_map1",
            "song-1.bundle",
            music="song_1_music",
            difficulty_raw=1,
            path_id=1,
        )
        catalog = _catalog([candidate])
        catalog["internal_ids"][0]["local_path"] = "other.bundle"

        with self.assertRaisesRegex(ChartIndexError, "source differs"):
            build_song_chart_index(
                [candidate],
                [_album(1)],
                [{"album_number": 1}],
                catalog,
            )


if __name__ == "__main__":
    unittest.main()
