from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from musedash_chart_extractor.discovery.structure import (
    StructureRecoveryError,
    inspect_stage_info_candidate,
    load_ranked_candidate,
    recover_stage_info_candidate,
)
from test_odin import _payload


class FakeStageInfo:
    def __init__(
        self,
        path_id: int,
        payload: bytes,
        serialized_format: int = 0,
        *,
        scene_events: list[dict] | None = None,
    ) -> None:
        self.path_id = path_id
        self._payload = payload
        self._serialized_format = serialized_format
        self._scene_events = scene_events if scene_events is not None else []

    def parse_as_dict(self) -> dict:
        return {
            "m_Enabled": 1,
            "serializationData": {
                "SerializedFormat": self._serialized_format,
                "SerializedBytes": list(self._payload),
                "ReferencedUnityObjects": [],
            },
            "sceneEvents": self._scene_events,
        }


def _candidate(source_payload: bytes, serialized_payload: bytes) -> dict:
    return {
        "status": "unvalidated_candidate",
        "rank": 12,
        "inventory_fingerprint": "sha256:fixture",
        "source": "fixture.bundle",
        "source_size": len(source_payload),
        "source_sha256": hashlib.sha256(source_payload).hexdigest(),
        "container_path": "Assets/Data/StageInfos/fixture.asset",
        "path_id": 42,
        "object_type": "MonoBehaviour",
        "metadata": {"asset_name": "fixture_map1"},
        "structure": {
            "serialized_payload_byte_count": len(serialized_payload),
            "serialized_payload_sha256": hashlib.sha256(serialized_payload).hexdigest(),
        },
    }


class StructureRecoveryTests(unittest.TestCase):
    def test_loads_exact_ranked_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "chart_candidates.jsonl"
            rows = [
                {"source": "other.bundle", "path_id": 1, "status": "failed"},
                {"source": "fixture.bundle", "path_id": 42, "status": "unvalidated_candidate"},
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            selected = load_ranked_candidate(
                path,
                source="fixture.bundle",
                path_id=42,
            )

            self.assertEqual(selected, rows[1])

    def test_inspection_verifies_source_and_emits_only_bounded_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"UnityFS synthetic"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            odin_payload = _payload()
            candidate = _candidate(source_payload, odin_payload)
            target = FakeStageInfo(42, odin_payload)

            hypotheses, summary = inspect_stage_info_candidate(
                game_dir,
                candidate,
                sample_records=1,
                loader=lambda _: SimpleNamespace(objects=[target]),
            )

            self.assertEqual(summary["parsed_record_count"], 2)
            self.assertEqual(summary["consumed_byte_count"], len(odin_payload))
            self.assertEqual(len(hypotheses), 5)
            array_row = next(
                row for row in hypotheses if row["hypothesis_id"] == "music-data-event-array"
            )
            self.assertEqual(len(array_row["record_samples"]), 1)
            rendered = json.dumps(hypotheses)
            self.assertNotIn('"records":', rendered)
            self.assertIn("tick-time-related", rendered)

    def test_stale_source_and_unknown_serialized_format_fail_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"current"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            odin_payload = _payload()
            stale = _candidate(b"stale!!", odin_payload)
            with self.assertRaisesRegex(StructureRecoveryError, "fingerprint is stale"):
                inspect_stage_info_candidate(
                    game_dir,
                    stale,
                    loader=lambda _: self.fail("stale source must not load"),
                )

            current = _candidate(source_payload, odin_payload)
            with self.assertRaisesRegex(StructureRecoveryError, "SerializedFormat"):
                inspect_stage_info_candidate(
                    game_dir,
                    current,
                    loader=lambda _: SimpleNamespace(
                        objects=[FakeStageInfo(42, odin_payload, serialized_format=1)]
                    ),
                )

    def test_recovery_retains_complete_stage_info_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"current"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            odin_payload = _payload()
            candidate = _candidate(source_payload, odin_payload)
            scene_events = [{"uid": "synthetic-event"}]

            _parsed, _sha256, envelope = recover_stage_info_candidate(
                game_dir,
                candidate,
                loader=lambda _: SimpleNamespace(
                    objects=[FakeStageInfo(42, odin_payload, scene_events=scene_events)]
                ),
            )

            self.assertEqual(envelope["sceneEvents"], scene_events)
            self.assertEqual(envelope["m_Enabled"], 1)
            self.assertEqual(
                envelope["serializationData"]["SerializedBytes"],
                list(odin_payload),
            )

    def test_empty_music_data_array_fails_as_domain_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_payload = b"current"
            (game_dir / "fixture.bundle").write_bytes(source_payload)
            odin_payload = _payload(declared_count=0, records=[])
            candidate = _candidate(source_payload, odin_payload)

            with self.assertRaisesRegex(
                StructureRecoveryError,
                "empty musicDatas array",
            ):
                inspect_stage_info_candidate(
                    game_dir,
                    candidate,
                    loader=lambda _: SimpleNamespace(
                        objects=[FakeStageInfo(42, odin_payload)]
                    ),
                )


if __name__ == "__main__":
    unittest.main()
