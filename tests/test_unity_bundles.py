from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from musedash_chart_extractor.scanner import ResourceRecord
from musedash_chart_extractor.unity.bundles import (
    build_object_type_summary,
    probe_unity_source,
)


class FakeObject:
    def __init__(
        self,
        path_id: int,
        type_name: str,
        byte_size: int,
        name: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.path_id = path_id
        self.type = SimpleNamespace(name=type_name)
        self.byte_size = byte_size
        self._name = name
        self._error = error

    def peek_name(self) -> str | None:
        if self._error is not None:
            raise self._error
        return self._name


class FakePointer:
    def __init__(self, obj: FakeObject) -> None:
        self.path_id = obj.path_id
        self.file_id = 0
        self._obj = obj

    def deref(self) -> FakeObject:
        return self._obj


class UnityBundleProbeTests(unittest.TestCase):
    def _resource(self, payload: bytes, relative_path: str = "fixture.bundle") -> ResourceRecord:
        return ResourceRecord(
            relative_path=relative_path,
            size=len(payload),
            suffix=Path(relative_path).suffix,
            magic="UnityFS",
            sha256=hashlib.sha256(payload).hexdigest(),
            category="unity_bundle_candidate",
        )

    def test_probe_records_metadata_without_parsing_object_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            payload = b"synthetic"
            (game_dir / "fixture.bundle").write_bytes(payload)
            stage = FakeObject(7, "MonoBehaviour", 123, "demo_map1")
            script = FakeObject(8, "MonoScript", 42, "StageInfo")
            environment = SimpleNamespace(
                objects=[stage, script],
                assets=[
                    SimpleNamespace(
                        name="CAB-fixture",
                        unity_version="2019.4.41f1",
                        target_platform=19,
                        objects={7: stage, 8: script},
                    )
                ],
                container={
                    "Assets/Data/StageInfos/demo_map1.asset": FakePointer(stage),
                },
            )

            report = probe_unity_source(
                game_dir,
                self._resource(payload),
                loader=lambda _: environment,
            )

            self.assertTrue(report["parseable"])
            self.assertEqual(report["object_types"], {"MonoBehaviour": 1, "MonoScript": 1})
            self.assertEqual(report["container_entries"][0]["path_id"], 7)
            self.assertTrue(report["container_entries"][0]["resolved"])
            self.assertEqual(report["named_objects"][0]["name"], "demo_map1")
            self.assertEqual(report["name_search_hits"], ["stage", "map"])

    def test_probe_retains_name_and_load_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary)
            payload = b"synthetic"
            (game_dir / "fixture.bundle").write_bytes(payload)
            broken_name = FakeObject(
                9,
                "TextAsset",
                5,
                error=ValueError("missing type tree"),
            )
            environment = SimpleNamespace(
                objects=[broken_name],
                assets=[],
                container={},
            )
            resource = self._resource(payload)

            name_report = probe_unity_source(
                game_dir,
                resource,
                loader=lambda _: environment,
            )
            load_report = probe_unity_source(
                game_dir,
                resource,
                loader=lambda _: (_ for _ in ()).throw(ValueError("bad bundle")),
            )

            self.assertTrue(name_report["parseable"])
            self.assertEqual(name_report["name_read_errors"][0]["path_id"], 9)
            self.assertFalse(load_report["parseable"])
            self.assertEqual(load_report["error_type"], "ValueError")
            self.assertEqual(load_report["error"], "bad bundle")

    def test_summary_distinguishes_complete_and_failed_probes(self) -> None:
        reports = [
            {
                "source": "one.bundle",
                "source_category": "unity_bundle_candidate",
                "parseable": True,
                "object_count": 2,
                "container_count": 1,
                "object_types": {"MonoBehaviour": 1, "AssetBundle": 1},
                "asset_files": [{"unity_version": "2019.4.41f1"}],
                "container_entries": [{"resolved": False}],
                "name_read_errors": [{"error_type": "ValueError"}],
                "warnings": [{"category": "UserWarning", "message": "fixture"}],
                "name_search_hits": ["stage", "map"],
            },
            {
                "source": "two.bundle",
                "source_category": "unity_bundle_candidate",
                "parseable": False,
                "error_type": "ValueError",
                "error": "bad bundle",
            },
        ]

        summary = build_object_type_summary(
            reports,
            inventory_fingerprint="sha256:fixture",
            candidate_source_count=3,
        )

        self.assertFalse(summary["probe_complete"])
        self.assertEqual(summary["parseable_source_count"], 1)
        self.assertEqual(summary["failed_source_count"], 1)
        self.assertEqual(summary["failure_types"], {"ValueError": 1})
        self.assertEqual(summary["high_value_source_count"], 1)
        self.assertEqual(summary["unresolved_container_count"], 1)
        self.assertEqual(summary["name_read_error_count"], 1)
        self.assertEqual(summary["warning_count"], 1)


if __name__ == "__main__":
    unittest.main()
