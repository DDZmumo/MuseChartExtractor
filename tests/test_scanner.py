from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.diagnostics import (
    write_compact_json,
    write_resource_inventory,
)
from musedash_chart_extractor.scanner import (
    GameDirectoryError,
    ResourceRecord,
    build_inventory_fingerprint,
    build_resource_summary,
    classify_resource,
    detect_magic,
    scan_game_directory,
    validate_game_directory,
)


class MagicDetectionTests(unittest.TestCase):
    def test_supported_signatures(self) -> None:
        cases = {
            b"UnityFS\x00payload": "UnityFS",
            b"UnityWeb\x00payload": "UnityWeb",
            b"UnityRaw\x00payload": "UnityRaw",
            b"MZ\x90\x00": "PE",
            b"  \r\n{\"key\": true}": "JSON",
            b"\xef\xbb\xbf[1, 2]": "JSON",
            b"\x00\x01\x02": "unknown",
        }
        for prefix, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(detect_magic(prefix), expected)

    def test_suffix_and_name_categories_remain_explicit(self) -> None:
        self.assertEqual(
            classify_resource("aa/example.bundle", "unknown"),
            "unity_bundle_candidate",
        )
        self.assertEqual(
            classify_resource("aa/catalog.json", "JSON"),
            "addressables_catalog",
        )
        self.assertEqual(
            classify_resource("Metadata/global-metadata.dat", "unknown"),
            "il2cpp_metadata",
        )


class ResourceScannerTests(unittest.TestCase):
    def test_invalid_game_directory_is_a_domain_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with self.assertRaisesRegex(GameDirectoryError, "does not exist"):
                validate_game_directory(missing)

    def test_inventory_is_sorted_hashed_and_summarized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "game"
            (game_dir / "MuseDash_Data" / "StreamingAssets" / "aa").mkdir(
                parents=True
            )
            bundle = game_dir / "MuseDash_Data" / "StreamingAssets" / "aa" / "z.bundle"
            bundle_payload = b"UnityFS\x00synthetic-fixture"
            bundle.write_bytes(bundle_payload)
            (game_dir / "catalog.json").write_text(
                '{"synthetic":true}', encoding="utf-8"
            )
            (game_dir / "a.bin").write_bytes(b"0123456789")

            records = scan_game_directory(game_dir)

            self.assertEqual(
                [record.relative_path for record in records],
                [
                    "a.bin",
                    "catalog.json",
                    "MuseDash_Data/StreamingAssets/aa/z.bundle",
                ],
            )
            self.assertEqual(records[0].sha256, hashlib.sha256(b"0123456789").hexdigest())
            self.assertEqual(records[2].magic, "UnityFS")
            self.assertEqual(records[2].category, "unity_bundle_candidate")

            summary = build_resource_summary(
                game_dir,
                records,
                large_file_threshold=10,
            )
            self.assertEqual(summary["file_count"], 3)
            self.assertEqual(summary["bundle_count"], 1)
            self.assertEqual(summary["unityfs_count"], 1)
            self.assertEqual(summary["catalog_count"], 1)
            self.assertEqual(summary["unknown_large_file_count"], 1)
            self.assertRegex(summary["inventory_fingerprint"], r"^sha256:[0-9a-f]{64}$")

    def test_inventory_fingerprint_is_order_independent_and_content_sensitive(self) -> None:
        first = ResourceRecord(
            relative_path="a.bundle",
            size=1,
            suffix=".bundle",
            magic="UnityFS",
            sha256=hashlib.sha256(b"a").hexdigest(),
            category="unity_bundle_candidate",
        )
        second = ResourceRecord(
            relative_path="b.bin",
            size=1,
            suffix=".bin",
            magic="unknown",
            sha256=hashlib.sha256(b"b").hexdigest(),
            category="unknown",
        )
        changed = ResourceRecord(
            relative_path="b.bin",
            size=1,
            suffix=".bin",
            magic="unknown",
            sha256=hashlib.sha256(b"c").hexdigest(),
            category="unknown",
        )

        expected = build_inventory_fingerprint([first, second])
        self.assertEqual(expected, build_inventory_fingerprint([second, first]))
        self.assertNotEqual(expected, build_inventory_fingerprint([first, changed]))

    def test_diagnostic_outputs_are_valid_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "diagnostics"
            rows = [
                {
                    "relative_path": "fixture.bundle",
                    "size": 7,
                    "magic": "UnityFS",
                }
            ]
            summary = {"schema_version": 1, "file_count": 1}

            inventory_path, summary_path = write_resource_inventory(
                output_dir, rows, summary
            )

            inventory_text = inventory_path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(inventory_text), rows[0])
            self.assertTrue(inventory_text.endswith("\n"))
            self.assertEqual(
                json.loads(summary_path.read_text(encoding="utf-8")), summary
            )

    def test_compact_json_is_stable_and_has_no_indentation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_compact_json(
                Path(temporary) / "index.json",
                {"z": [1, 2], "a": "value"},
            )

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '{"a":"value","z":[1,2]}\n',
            )


if __name__ == "__main__":
    unittest.main()
