from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from musedash_chart_extractor.discovery.candidates import (
    CandidateDiscoveryError,
    discover_stage_info_candidates,
    extract_utf16le_ascii_runs,
    select_stage_info_sources,
)
from musedash_chart_extractor.scanner import ResourceRecord


class FakeObject:
    def __init__(self, path_id: int, type_name: str, byte_size: int, data: dict) -> None:
        self.path_id = path_id
        self.type = SimpleNamespace(name=type_name)
        self.byte_size = byte_size
        self._data = data

    def parse_as_dict(self) -> dict:
        return self._data


def _payload(extra_bytes: int = 0) -> bytes:
    strings = [
        "System.Collections.Generic.List`1[[GameLogic.MusicData, Assembly-CSharp]]",
        "tick",
        "time",
        "length",
        "tick",
        "time",
        "length",
    ]
    encoded = b"\xff".join(value.encode("utf-16-le") for value in strings)
    return encoded + (b"\x00" * (5000 + extra_bytes))


def _stage(path_id: int, difficulty: int, extra_bytes: int = 0) -> FakeObject:
    payload = _payload(extra_bytes)
    return FakeObject(
        path_id,
        "MonoBehaviour",
        len(payload) + 100,
        {
            "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
            "m_Enabled": 1,
            "m_Name": f"fixture_map{difficulty}",
            "m_Script": {"m_FileID": 0, "m_PathID": 99},
            "serializationData": {
                "SerializedFormat": 0,
                "SerializedBytes": list(payload),
                "ReferencedUnityObjects": [],
                "SerializationNodes": [],
            },
            "mapName": f"fixture_map{difficulty}",
            "music": "fixture_music",
            "scene": "fixture_scene",
            "difficulty": difficulty,
            "md5": "synthetic",
            "bpm": 120.0,
            "sceneEvents": [],
        },
    )


class CandidateDiscoveryTests(unittest.TestCase):
    def test_utf16_probe_is_bounded_and_reports_offsets(self) -> None:
        payload = b"\x01\x02" + "tick".encode("utf-16-le") + b"\xff" + "time".encode(
            "utf-16-le"
        )
        self.assertEqual(
            extract_utf16le_ascii_runs(payload),
            [(2, "tick"), (11, "time")],
        )
        self.assertEqual(extract_utf16le_ascii_runs(payload, limit=10), [(2, "tick")])

    def test_selection_uses_resolved_stageinfo_container_entries(self) -> None:
        reports = [
            {
                "source": "music.bundle",
                "parseable": True,
                "size": 10,
                "sha256": "a" * 64,
                "container_entries": [
                    {
                        "path": "Assets/Data/StageInfos/example.asset",
                        "path_id": 7,
                        "type": "MonoBehaviour",
                        "resolved": True,
                    },
                    {
                        "path": "Assets/Data/Songs/example.asset",
                        "path_id": 8,
                        "type": "MonoBehaviour",
                        "resolved": True,
                    },
                ],
            }
        ]
        selected = select_stage_info_sources(reports)
        self.assertEqual(len(selected), 1)
        self.assertEqual([entry["path_id"] for entry in selected[0]["entries"]], [7])

    def test_real_discovery_path_scores_and_never_emits_payload_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            source_path = game_dir / "music.bundle"
            source_payload = b"UnityFS synthetic"
            source_path.write_bytes(source_payload)
            digest = hashlib.sha256(source_payload).hexdigest()
            report = {
                "source": "music.bundle",
                "parseable": True,
                "size": len(source_payload),
                "sha256": digest,
                "container_entries": [
                    {
                        "path": f"Assets/Data/StageInfos/fixture_map{difficulty}.asset",
                        "path_id": difficulty,
                        "type": "MonoBehaviour",
                        "byte_size": 6000 + difficulty,
                        "resolved": True,
                    }
                    for difficulty in (1, 2)
                ],
            }
            record = ResourceRecord(
                relative_path="music.bundle",
                size=len(source_payload),
                suffix=".bundle",
                magic="UnityFS",
                sha256=digest,
                category="unity_bundle_candidate",
            )
            script = FakeObject(
                99,
                "MonoScript",
                10,
                {
                    "m_Name": "StageInfo",
                    "m_ClassName": "StageInfo",
                    "m_Namespace": "Assets.Scripts.GameCore",
                    "m_AssemblyName": "Assembly-CSharp.dll",
                },
            )
            stages = [_stage(1, 1), _stage(2, 2, 200)]
            stages[1]._data["mapName"] = r"D:\developer\fixture_map4.bms"
            stages[1]._data["difficulty"] = 4
            stages[1]._data["bpm"] = 0.0
            environment = SimpleNamespace(objects=[*stages, script])

            candidates, summary = discover_stage_info_candidates(
                game_dir,
                [report],
                [record],
                inventory_fingerprint="sha256:fixture",
                loader=lambda _: environment,
            )

            self.assertEqual(summary["candidate_count"], 2)
            self.assertEqual(summary["failed_candidate_count"], 0)
            self.assertEqual(summary["score_version"], "stageinfo-signals-v1")
            self.assertEqual([row["score"] for row in candidates], [1.0, 1.0])
            self.assertTrue(
                all(row["status"] == "unvalidated_candidate" for row in candidates)
            )
            self.assertEqual(candidates[0]["metadata"]["difficulty_raw"], 4)
            self.assertEqual(candidates[0]["metadata"]["bpm_raw"], 0.0)
            self.assertTrue(candidates[0]["metadata"]["map_name_raw"].endswith(".bms"))
            self.assertIn("music_data_list_type_descriptor", {
                item["signal"] for item in candidates[0]["score_components"]
            })
            rendered = json.dumps(candidates)
            self.assertNotIn(str(list(_payload())[:100]), rendered)
            self.assertNotIn('"serialized_payload":', rendered)

    def test_stale_phase_two_fingerprint_fails_before_unity_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            (game_dir / "music.bundle").write_bytes(b"new")
            report = {
                "source": "music.bundle",
                "parseable": True,
                "size": 3,
                "sha256": hashlib.sha256(b"old").hexdigest(),
                "container_entries": [
                    {
                        "path": "Assets/Data/StageInfos/example.asset",
                        "path_id": 1,
                        "type": "MonoBehaviour",
                        "resolved": True,
                    }
                ],
            }
            record = ResourceRecord(
                relative_path="music.bundle",
                size=3,
                suffix=".bundle",
                magic="UnityFS",
                sha256=hashlib.sha256(b"new").hexdigest(),
                category="unity_bundle_candidate",
            )

            with self.assertRaisesRegex(CandidateDiscoveryError, "stale"):
                discover_stage_info_candidates(
                    game_dir,
                    [report],
                    [record],
                    inventory_fingerprint="sha256:fixture",
                    loader=lambda _: self.fail("stale source must not be loaded"),
                )


if __name__ == "__main__":
    unittest.main()
