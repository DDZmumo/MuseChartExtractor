from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from musedash_chart_extractor.discovery.note_data import (
    NOTE_DATA_CONTAINER_PATH,
    NoteDataError,
    resolve_note_data,
    select_note_data_source,
)


class FakeTextAssetObject:
    def __init__(self, path_id: int, content: str, type_name: str = "TextAsset") -> None:
        self.path_id = path_id
        self.type = SimpleNamespace(name=type_name)
        self._content = content

    def read(self) -> SimpleNamespace:
        return SimpleNamespace(m_Script=self._content)


def _report(source: str, payload: bytes, *, path_id: int = 42) -> dict:
    return {
        "source": source,
        "parseable": True,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "container_entries": [
            {
                "path": NOTE_DATA_CONTAINER_PATH,
                "path_id": path_id,
                "type": "TextAsset",
                "resolved": True,
            }
        ],
    }


class NoteDataTests(unittest.TestCase):
    def test_resolves_exact_text_asset_and_returns_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"UnityFS synthetic note data"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            rows = [
                {"uid": "note-a", "type": 1, "unknown": {"value": 7}},
                {"uid": "note-b", "type": 9, "extra": [1, 2]},
            ]
            content = json.dumps(rows, ensure_ascii=False)
            target = FakeTextAssetObject(42, content)

            by_uid, provenance = resolve_note_data(
                game_dir,
                [_report("fixture.bundle", source_payload)],
                loader=lambda _: SimpleNamespace(objects=[target]),
            )

            self.assertEqual(by_uid, {"note-a": [rows[0]], "note-b": [rows[1]]})
            self.assertEqual(by_uid["note-a"][0]["unknown"], {"value": 7})
            self.assertEqual(provenance["source"], "fixture.bundle")
            self.assertEqual(provenance["container_path"], NOTE_DATA_CONTAINER_PATH)
            self.assertEqual(provenance["path_id"], 42)
            self.assertEqual(provenance["source_byte_count"], len(source_payload))
            self.assertEqual(
                provenance["source_sha256"], hashlib.sha256(source_payload).hexdigest()
            )
            content_bytes = content.encode("utf-8")
            self.assertEqual(provenance["content_byte_count"], len(content_bytes))
            self.assertEqual(
                provenance["content_sha256"], hashlib.sha256(content_bytes).hexdigest()
            )
            self.assertEqual(provenance["row_count"], 2)
            self.assertEqual(provenance["uid_count"], 2)

    def test_duplicate_uids_are_retained_in_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"source"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            rows = [
                {"uid": "same", "value": "first"},
                {"uid": "same", "value": "second"},
            ]

            by_uid, provenance = resolve_note_data(
                game_dir,
                [_report("fixture.bundle", source_payload)],
                loader=lambda _: SimpleNamespace(
                    objects=[FakeTextAssetObject(42, json.dumps(rows))]
                ),
            )

            self.assertEqual(by_uid["same"], rows)
            self.assertEqual(provenance["row_count"], 2)
            self.assertEqual(provenance["uid_count"], 1)

    def test_stale_source_fails_before_unity_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            (game_dir / "fixture.bundle").write_bytes(b"current")

            with self.assertRaisesRegex(NoteDataError, "fingerprint is stale"):
                resolve_note_data(
                    game_dir,
                    [_report("fixture.bundle", b"stale!")],
                    loader=lambda _: self.fail("stale source must not be loaded"),
                )

    def test_source_escape_and_missing_source_fail_before_unity_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            game_dir = base / "game"
            game_dir.mkdir()
            outside_payload = b"outside"
            (base / "outside.bundle").write_bytes(outside_payload)

            with self.assertRaisesRegex(NoteDataError, "escapes game directory"):
                resolve_note_data(
                    game_dir,
                    [_report("../outside.bundle", outside_payload)],
                    loader=lambda _: self.fail("escaped source must not be loaded"),
                )
            with self.assertRaisesRegex(NoteDataError, "does not exist"):
                resolve_note_data(
                    game_dir,
                    [_report("missing.bundle", b"missing")],
                    loader=lambda _: self.fail("missing source must not be loaded"),
                )

    def test_missing_and_multiple_inventory_matches_fail_loudly(self) -> None:
        unrelated = {
            "source": "unrelated.bundle",
            "parseable": True,
            "size": 1,
            "sha256": "0" * 64,
            "container_entries": [
                {
                    "path": NOTE_DATA_CONTAINER_PATH.upper(),
                    "path_id": 1,
                    "type": "TextAsset",
                    "resolved": True,
                }
            ],
        }
        with self.assertRaisesRegex(NoteDataError, "found 0"):
            select_note_data_source([unrelated])

        first = _report("first.bundle", b"a", path_id=1)
        second = _report("second.bundle", b"b", path_id=2)
        with self.assertRaisesRegex(NoteDataError, "found 2"):
            select_note_data_source([first, second])

    def test_invalid_json_and_non_list_root_fail_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"source"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            report = _report("fixture.bundle", source_payload)

            for content, message in (("[", "invalid note-data JSON"), ("{}", "root must be a list")):
                with self.subTest(content=content):
                    with self.assertRaisesRegex(NoteDataError, message):
                        resolve_note_data(
                            game_dir,
                            [report],
                            loader=lambda _, value=content: SimpleNamespace(
                                objects=[FakeTextAssetObject(42, value)]
                            ),
                        )

    def test_invalid_rows_and_uids_fail_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"source"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            report = _report("fixture.bundle", source_payload)
            cases = (
                ([1], "row 0 must be an object"),
                ([{}], "row 0 has no non-empty string uid"),
                ([{"uid": "   "}], "row 0 has no non-empty string uid"),
                ([{"uid": 7}], "row 0 has no non-empty string uid"),
            )

            for rows, message in cases:
                with self.subTest(rows=rows):
                    with self.assertRaisesRegex(NoteDataError, message):
                        resolve_note_data(
                            game_dir,
                            [report],
                            loader=lambda _, value=json.dumps(rows): SimpleNamespace(
                                objects=[FakeTextAssetObject(42, value)]
                            ),
                        )


if __name__ == "__main__":
    unittest.main()
