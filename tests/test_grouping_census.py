from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.discovery.grouping_census import (
    GroupingCensusError,
    census_stage_info_grouping,
)
from test_odin import _payload


class _Type:
    name = "MonoBehaviour"


class _Object:
    type = _Type()

    def __init__(self, path_id: int, payload: bytes) -> None:
        self.path_id = path_id
        self._payload = payload

    def parse_as_dict(self) -> dict:
        return {
            "serializationData": {
                "SerializedFormat": 0,
                "SerializedBytes": list(self._payload),
            }
        }


class _Environment:
    def __init__(self, objects: list[_Object]) -> None:
        self.objects = objects


def _candidate(
    *,
    source_size: int,
    source_sha256: str,
    path_id: int,
    chart_id: str,
    payload: bytes,
) -> dict:
    return {
        "status": "unvalidated_candidate",
        "inventory_fingerprint": "sha256:fixture",
        "source": "data/fixture.bundle",
        "source_size": source_size,
        "source_sha256": source_sha256,
        "container_path": f"Assets/StageInfos/{chart_id}.asset",
        "path_id": path_id,
        "object_type": "MonoBehaviour",
        "metadata": {"asset_name": chart_id},
        "structure": {
            "serialized_payload_byte_count": len(payload),
            "serialized_payload_sha256": hashlib.sha256(payload).hexdigest(),
        },
    }


class GroupingCensusTests(unittest.TestCase):
    def test_census_retains_success_and_unsupported_rows_without_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "data" / "fixture.bundle"
            bundle.parent.mkdir()
            bundle_bytes = b"synthetic-bundle"
            bundle.write_bytes(bundle_bytes)
            source_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
            good_payload = _payload()
            unsupported_payload = b"\x34synthetic-unsupported"
            candidates = [
                _candidate(
                    source_size=len(bundle_bytes),
                    source_sha256=source_sha256,
                    path_id=1,
                    chart_id="fixture_map1",
                    payload=good_payload,
                ),
                _candidate(
                    source_size=len(bundle_bytes),
                    source_sha256=source_sha256,
                    path_id=2,
                    chart_id="fixture_map2",
                    payload=unsupported_payload,
                ),
            ]
            environment = _Environment(
                [_Object(1, good_payload), _Object(2, unsupported_payload)]
            )

            rows, summary = census_stage_info_grouping(
                root,
                candidates,
                loader=lambda _path: environment,
            )

        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["source_count"], 1)
        self.assertEqual(summary["raw_parse_status_counts"], {"parsed": 1, "unsupported": 1})
        self.assertEqual(summary["grouping_status_counts"], {"grouped": 1, "not-attempted": 1})
        by_id = {row["chart_id"]: row for row in rows}
        self.assertEqual(by_id["fixture_map1"]["profile"]["logical_object_count"], 1)
        self.assertEqual(by_id["fixture_map2"]["error"]["tag"], 0x34)
        rendered = json.dumps(rows)
        self.assertNotIn("SerializedBytes", rendered)
        self.assertNotIn("synthetic-unsupported", rendered)

    def test_stale_source_fails_the_census_before_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "data" / "fixture.bundle"
            bundle.parent.mkdir()
            bundle.write_bytes(b"current")
            payload = _payload()
            candidate = _candidate(
                source_size=7,
                source_sha256="0" * 64,
                path_id=1,
                chart_id="fixture_map1",
                payload=payload,
            )

            with self.assertRaisesRegex(GroupingCensusError, "fingerprint is stale"):
                census_stage_info_grouping(
                    root,
                    [candidate],
                    loader=lambda _path: self.fail("loader must not run"),
                )


if __name__ == "__main__":
    unittest.main()
