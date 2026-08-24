from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from musedash_chart_extractor import ChartStoreError, digest_chart_store
from test_store import (
    _Object,
    _candidate,
    _extract,
    _payload,
    _song_index,
)


def _index(candidates: list[dict], *, resolved_count: int) -> dict:
    result = deepcopy(_song_index(candidates[0]))
    template = result["songs"][0]["charts"][0]
    resolved: list[dict] = []
    uncertain: list[dict] = []
    for position, candidate in enumerate(candidates):
        chart = deepcopy(template)
        chart_id = candidate["metadata"]["asset_name"]
        chart["chart_id"] = chart_id
        chart["difficulty_id"] = position + 1
        chart["difficulty_key"] = f"difficulty{position + 1}"
        chart["source"] = {
            "bundle": candidate["source"],
            "bundle_byte_count": candidate["source_size"],
            "bundle_sha256": candidate["source_sha256"],
            "container_path": candidate["container_path"],
            "path_id": candidate["path_id"],
            "object_type": candidate["object_type"],
        }
        chart["addressables"] = {
            "primary_key": chart_id,
            "dependency_local_path": candidate["source"],
        }
        chart["stage_info_raw"] = deepcopy(candidate["metadata"])
        if position < resolved_count:
            chart["song_id"] = "fixture-song"
            resolved.append(chart)
        else:
            chart["song_id"] = None
            chart["relationship_status"] = "unresolved"
            chart["unresolved_reason"] = "synthetic missing song identity"
            uncertain.append(chart)
    result["songs"][0]["charts"] = resolved
    result["songs"][0]["chart_count"] = len(resolved)
    result["unresolved_charts"] = uncertain
    result["counts"] = {
        "candidate_chart_count": len(candidates),
        "indexed_chart_count": len(resolved),
        "unresolved_chart_count": len(uncertain),
    }
    return result


def _build_store(
    root: Path,
    *,
    resolved_count: int = 2,
    uncertain_count: int = 1,
) -> Path:
    bundle = b"synthetic bundle"
    candidates: list[dict] = []
    objects: list[_Object] = []
    note_configs: dict[str, list[dict]] = {}
    total = resolved_count + uncertain_count
    for position in range(total):
        uid = f"fixture-note-{position}"
        payload = _payload(note_uid_value=uid)
        chart_id = f"fixture_map{position + 1}"
        path_id = 101 + position
        candidates.append(
            _candidate(
                bundle,
                payload,
                chart_id=chart_id,
                path_id=path_id,
            )
        )
        objects.append(_Object(payload, chart_id=chart_id, path_id=path_id))
        note_configs[uid] = [{"uid": uid, "type": 3}]
    output, _manifest = _extract(
        root,
        candidates,
        _index(candidates, resolved_count=resolved_count),
        objects,
        note_configs=note_configs,
    )
    return output


class StoreCanonicalDigestTests(unittest.TestCase):
    def test_streams_multiple_charts_and_uncertain_without_writing_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = _build_store(root)
            files_before = sorted(
                path.relative_to(store).as_posix()
                for path in store.rglob("*")
                if path.is_file()
            )

            first = digest_chart_store(store)
            second = digest_chart_store(store)
            files_after = sorted(
                path.relative_to(store).as_posix()
                for path in store.rglob("*")
                if path.is_file()
            )

        self.assertEqual(first["status"], "passed")
        self.assertEqual(first, second)
        self.assertEqual(files_before, files_after)
        self.assertEqual(first["status_counts"], {"success": 2, "uncertain": 1})
        self.assertEqual(first["canonical"]["resolved_chart_count"], 2)
        self.assertEqual(first["canonical"]["raw_record_count"], 4)
        self.assertEqual(first["canonical"]["logical_event_count"], 2)
        self.assertEqual(first["canonical"]["sentinel_count"], 2)
        self.assertGreater(first["canonical"]["semantic_byte_count"], 0)
        self.assertEqual(first["id_sets"]["uncertain"]["count"], 1)
        self.assertEqual(len(first["canonical"]["corpus_digest"]), 64)
        self.assertNotIn('"events":', json.dumps(first))

    def test_expected_digest_and_count_mismatches_fail_without_throwing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = _build_store(Path(temporary), resolved_count=1, uncertain_count=0)
            baseline = digest_chart_store(store)
            expected = {
                "inventory_fingerprint": baseline["inventory_fingerprint"],
                "canonical_corpus_digest": baseline["canonical"]["corpus_digest"],
                "resolved_id_set_digest": baseline["id_sets"]["resolved"]["digest"],
                "uncertain_id_set_digest": baseline["id_sets"]["uncertain"]["digest"],
                "resolved_chart_count": 999,
                "resolved_raw_record_count": baseline["canonical"]["raw_record_count"],
                "resolved_event_count": baseline["canonical"]["logical_event_count"],
                "resolved_sentinel_count": baseline["canonical"]["sentinel_count"],
                "semantic_byte_count": baseline["canonical"]["semantic_byte_count"],
            }
            expected["canonical_corpus_digest"] = "0" * 64

            report = digest_chart_store(store, expected=expected)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["mismatch_counts"]["expected_mismatches"], 2)
        self.assertEqual(len(report["failure_samples"]), 2)

    def test_corrupt_payload_is_a_bounded_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = _build_store(Path(temporary), resolved_count=12, uncertain_count=0)
            payloads = list((store / "payloads" / "sha256").rglob("*.odin"))
            self.assertEqual(len(payloads), 12)
            for payload in payloads:
                payload.write_bytes(payload.read_bytes() + b"corrupt")

            report = digest_chart_store(store, failure_sample_limit=10)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(
            report["mismatch_counts"]["canonical_load_mismatches"], 12
        )
        self.assertEqual(len(report["failure_samples"]), 10)
        self.assertEqual(report["canonical"]["resolved_chart_count"], 0)

    def test_missing_song_row_fails_closed_when_store_opens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = _build_store(Path(temporary), resolved_count=1, uncertain_count=0)
            index = store / "index.sqlite3"
            connection = sqlite3.connect(index)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DELETE FROM songs")
                connection.commit()
            finally:
                connection.close()
            manifest_path = store / "store.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            content = index.read_bytes()
            manifest["index"]["byte_count"] = len(content)
            manifest["index"]["sha256"] = hashlib.sha256(content).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ChartStoreError, "foreign key"):
                digest_chart_store(store)

    def test_invalid_expected_baseline_fails_before_opening_store(self) -> None:
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            digest_chart_store(
                "does-not-exist",
                expected={"canonical_corpus_digest": "NOT-A-DIGEST"},
            )


if __name__ == "__main__":
    unittest.main()
