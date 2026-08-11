from __future__ import annotations

import hashlib
import json
from copy import deepcopy
import unittest

from musedash_chart_extractor.discovery.first_chart import (
    _group_records,
    build_experimental_chart,
)
from musedash_chart_extractor.unity.odin import parse_stage_info_payload
from test_odin import _payload, _record


class ExperimentalFirstChartTests(unittest.TestCase):
    def test_repeated_negative_config_id_records_remain_independent(self) -> None:
        payload = _payload(
            declared_count=4,
            records=[
                _record(0, first=True),
                *[
                    _record(
                        index,
                        first=False,
                        is_long_pressing=False,
                        is_long_press_end=False,
                    )
                    for index in (1, 2, 3)
                ],
            ],
        )
        parsed = deepcopy(parse_stage_info_payload(payload))
        for record in parsed["records"][1:]:
            config = record["fields"]["configData"]["fields"]
            config["id"]["value"] = -1
            config["time"]["value"]["text"] = "1"
            config["pathway"]["value"] = 0

        groups, logical, summary = _group_records(
            parsed["records"],
            {"fixture-note": [{"uid": "fixture-note", "type": "1"}]},
        )

        self.assertEqual(len(groups), 4)
        self.assertEqual(len(logical), 3)
        self.assertEqual([row["config_id_raw"] for row in logical], [-1, -1, -1])
        self.assertEqual(
            [row["time_raw"]["text"] for row in logical], ["1", "1", "1"]
        )
        self.assertEqual(summary["observed_sentinel_count"], 1)

    def test_neutral_base_does_not_require_zero_end_index(self) -> None:
        payload = _payload(
            declared_count=4,
            records=[
                _record(0, first=True),
                *[
                    _record(
                        index,
                        first=False,
                        is_long_pressing=False,
                        is_long_press_end=False,
                    )
                    for index in (1, 2, 3)
                ],
            ],
        )
        parsed = deepcopy(parse_stage_info_payload(payload))
        for record in parsed["records"][1:]:
            fields = record["fields"]
            config = fields["configData"]["fields"]
            config["id"]["value"] = 34
            config["time"]["value"]["text"] = "10"
            config["pathway"]["value"] = 1
            fields["endIndex"]["value"] = 8980
        base, pressing, ending = parsed["records"][1:]
        base["fields"]["endIndex"]["value"] = 875
        pressing["fields"]["isLongPressing"]["value"] = True
        ending["fields"]["isLongPressEnd"]["value"] = True

        _groups, logical, summary = _group_records(
            parsed["records"],
            {"fixture-note": [{"uid": "fixture-note", "type": "3"}]},
        )

        self.assertEqual(len(logical), 1)
        self.assertEqual(logical[0]["base_raw_record_index"], 1)
        self.assertEqual(logical[0]["long_pressing_raw_record_indices"], [2])
        self.assertEqual(logical[0]["long_press_end_raw_record_indices"], [3])
        self.assertEqual(summary["expanded_raw_record_count"], 2)

    def test_projection_preserves_raw_records_and_unknown_type(self) -> None:
        payload = _payload()
        parsed = parse_stage_info_payload(payload)
        candidate = {
            "rank": 1,
            "inventory_fingerprint": "sha256:fixture",
            "source": "fixture.bundle",
            "source_size": 123,
            "source_sha256": "a" * 64,
            "container_path": "Assets/Data/StageInfos/fixture.asset",
            "path_id": 42,
            "object_type": "MonoBehaviour",
            "metadata": {
                "asset_name": "fixture_map1",
                "map_name_raw": r"D:\developer\fixture.bms",
                "music": "fixture_music",
                "scene": "fixture_scene",
                "difficulty_raw": 4,
                "md5": "synthetic",
                "bpm_raw": 0.0,
                "scene_event_count": 0,
            },
        }

        chart = build_experimental_chart(
            candidate,
            parsed,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            stage_info_raw={
                "m_Enabled": 1,
                "serializationData": {
                    "SerializedFormat": 0,
                    "SerializedBytes": list(payload),
                },
                "sceneEvents": [{"uid": "synthetic-scene-event"}],
                "unknownFutureField": {"value": 17},
            },
            note_configs_by_uid={
                "fixture-note": [
                    {
                        "uid": "fixture-note",
                        "type": "9",
                        "future_config_field": "preserved",
                    }
                ]
            },
            note_data_provenance={
                "source": "synthetic-notedata.bundle",
                "content_sha256": "b" * 64,
            },
        )

        self.assertEqual(chart["status"], "raw-extracted")
        self.assertEqual(chart["validation_status"], "unvalidated")
        self.assertFalse(chart["canonicalized"])
        self.assertEqual(chart["raw_record_count"], 2)
        self.assertNotIn("events", chart)
        self.assertEqual(chart["raw_records"][1]["raw_type"]["id"], 9)
        self.assertIsNone(chart["raw_records"][1]["raw_type"]["name"])
        self.assertEqual(
            chart["raw_records"][1]["raw_type"]["status"],
            "resolved-id-name-unknown",
        )
        self.assertEqual(
            chart["raw_records"][1]["raw"]["fields"]["configData"]["fields"]["note_uid"]["value"],
            "fixture-note",
        )
        self.assertEqual(chart["candidate_metadata"]["difficulty_raw"], 4)
        self.assertEqual(
            chart["stage_info_raw"]["sceneEvents"],
            [{"uid": "synthetic-scene-event"}],
        )
        self.assertEqual(
            chart["stage_info_raw"]["unknownFutureField"],
            {"value": 17},
        )
        self.assertEqual(
            chart["stage_info_raw"]["serializationData"]["SerializedBytes"],
            list(payload),
        )
        self.assertEqual(chart["record_group_count"], 2)
        self.assertEqual(chart["logical_object_count"], 1)
        self.assertEqual(
            chart["record_groups"][0]["role_status"],
            "observed-sentinel",
        )
        self.assertEqual(
            chart["logical_objects"][0]["raw_record_indices"],
            [1],
        )
        self.assertEqual(
            chart["note_data"]["configs_by_uid_raw"]["fixture-note"][0][
                "future_config_field"
            ],
            "preserved",
        )
        self.assertEqual(chart["note_data"]["unmapped_note_uids"], [])
        self.assertIn(
            "the enum names for numeric notedata type identifiers",
            chart["unknowns"],
        )
        json.dumps(chart)

    def test_grouping_uses_config_id_and_keeps_long_press_subrecords(self) -> None:
        payload = _payload(
            declared_count=3,
            records=[
                _record(0, first=True),
                _record(
                    1,
                    first=False,
                    is_long_pressing=False,
                    is_long_press_end=False,
                ),
                _record(
                    2,
                    first=False,
                    is_long_pressing=False,
                    is_long_press_end=False,
                ),
            ],
        )
        parsed = parse_stage_info_payload(payload)
        parsed = deepcopy(parsed)
        extra = parsed["records"][2]
        extra["fields"]["configData"]["fields"]["id"]["value"] = 1
        extra["fields"]["configData"]["fields"]["pathway"]["value"] = 1
        extra["fields"]["isLongPressing"]["value"] = True
        extra["fields"]["isLongPressEnd"]["value"] = False
        extra["fields"]["endIndex"]["value"] = 123
        candidate = {
            "rank": 1,
            "inventory_fingerprint": "sha256:fixture",
            "source": "fixture.bundle",
            "source_size": 123,
            "source_sha256": "a" * 64,
            "container_path": "Assets/Data/StageInfos/fixture.asset",
            "path_id": 42,
            "object_type": "MonoBehaviour",
            "metadata": {"asset_name": "fixture_map1"},
        }

        chart = build_experimental_chart(
            candidate,
            parsed,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            stage_info_raw={"serializationData": {"SerializedBytes": list(payload)}},
            note_configs_by_uid={
                "fixture-note": [{"uid": "fixture-note", "type": "3"}]
            },
            note_data_provenance={"source": "synthetic-notedata.bundle"},
        )

        self.assertEqual(chart["raw_record_count"], 3)
        self.assertEqual(chart["record_group_count"], 2)
        self.assertEqual(chart["logical_object_count"], 1)
        logical = chart["logical_objects"][0]
        self.assertEqual(logical["config_id_raw"], 1)
        self.assertEqual(logical["raw_record_indices"], [1, 2])
        self.assertEqual(logical["long_pressing_raw_record_indices"], [2])
        self.assertEqual(logical["long_press_end_raw_record_indices"], [])
        self.assertEqual(logical["raw_type"]["id"], 3)
        self.assertEqual(chart["grouping"]["expanded_raw_record_count"], 1)

    def test_logical_sequence_uses_config_id_order_not_raw_first_occurrence(self) -> None:
        payload = _payload(
            declared_count=3,
            records=[
                _record(0, first=True),
                _record(
                    2,
                    first=False,
                    is_long_pressing=False,
                    is_long_press_end=False,
                ),
                _record(
                    1,
                    first=False,
                    is_long_pressing=False,
                    is_long_press_end=False,
                ),
            ],
        )
        parsed = parse_stage_info_payload(payload)
        parsed = deepcopy(parsed)
        parsed["records"][1]["fields"]["configData"]["fields"]["time"]["value"][
            "text"
        ] = "2"
        parsed["records"][2]["fields"]["configData"]["fields"]["time"]["value"][
            "text"
        ] = "1"
        candidate = {
            "rank": 1,
            "inventory_fingerprint": "sha256:fixture",
            "source": "fixture.bundle",
            "source_size": 123,
            "source_sha256": "a" * 64,
            "container_path": "Assets/Data/StageInfos/fixture.asset",
            "path_id": 42,
            "object_type": "MonoBehaviour",
            "metadata": {"asset_name": "fixture_map1"},
        }

        chart = build_experimental_chart(
            candidate,
            parsed,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            stage_info_raw={"serializationData": {"SerializedBytes": list(payload)}},
            note_configs_by_uid={
                "fixture-note": [{"uid": "fixture-note", "type": "1"}]
            },
            note_data_provenance={"source": "synthetic-notedata.bundle"},
        )

        self.assertEqual(
            [row["index"] for row in chart["raw_records"]],
            [0, 1, 2],
        )
        self.assertEqual(
            [row["config_id_raw"] for row in chart["logical_objects"]],
            [1, 2],
        )
        self.assertEqual(
            [row["base_raw_record_index"] for row in chart["logical_objects"]],
            [2, 1],
        )
        self.assertEqual(
            chart["grouping"]["logical_sequence_order"],
            (
                "configData.time-ascending, configData.id-ascending, "
                "base-raw-index-ascending"
            ),
        )
        self.assertEqual(chart["grouping"]["logical_sequence_time_descent_count"], 0)


if __name__ == "__main__":
    unittest.main()
