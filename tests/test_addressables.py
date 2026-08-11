from __future__ import annotations

import base64
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from musedash_chart_extractor.scanner import ResourceRecord
from musedash_chart_extractor.unity.addressables import (
    AddressablesCatalogError,
    parse_addressables_catalog,
)


def _string_object(value: str) -> bytes:
    payload = value.encode("ascii")
    return bytes([0]) + struct.pack("<i", len(payload)) + payload


def _json_object(value: dict[str, object]) -> bytes:
    assembly = b"Fixture.Assembly"
    class_name = b"Fixture.Options"
    raw_json = json.dumps(value, separators=(",", ":")).encode("utf-16-le")
    return (
        bytes([7, len(assembly)])
        + assembly
        + bytes([len(class_name)])
        + class_name
        + struct.pack("<i", len(raw_json))
        + raw_json
    )


def _fixture_catalog(*, bad_provider_index: bool = False, unknown_key_tag: bool = False) -> dict[str, object]:
    key_values = ["bundle.bundle", "fixture-asset"]
    key_data = bytearray(struct.pack("<i", len(key_values)))
    key_offsets = []
    for index, value in enumerate(key_values):
        key_offsets.append(len(key_data))
        if unknown_key_tag and index == 0:
            key_data.extend(b"\x63")
        else:
            key_data.extend(_string_object(value))

    bucket_data = bytearray(struct.pack("<i", 2))
    bucket_data.extend(struct.pack("<iii", key_offsets[0], 1, 0))
    bucket_data.extend(struct.pack("<iii", key_offsets[1], 1, 1))

    extra_data = _json_object({"m_Crc": 42, "future_field": "preserved"})
    provider_for_asset = 9 if bad_provider_index else 1
    entries = [
        (0, 0, -1, 0, 0, 0, 0),
        (1, provider_for_asset, 0, 12345, -1, 1, 1),
    ]
    entry_data = struct.pack("<i", len(entries)) + b"".join(
        struct.pack("<7i", *entry) for entry in entries
    )

    return {
        "m_LocatorId": "FixtureLocator",
        "m_BuildResultHash": "fixture-build-hash",
        "m_ProviderIds": ["BundleProvider", "AssetProvider"],
        "m_InternalIds": [
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\StandaloneWindows64\\bundle.bundle",
            "Assets/Fixture/asset.asset",
        ],
        "m_KeyDataString": base64.b64encode(key_data).decode("ascii"),
        "m_BucketDataString": base64.b64encode(bucket_data).decode("ascii"),
        "m_EntryDataString": base64.b64encode(entry_data).decode("ascii"),
        "m_ExtraDataString": base64.b64encode(extra_data).decode("ascii"),
        "m_resourceTypes": [
            {"m_AssemblyName": "Fixture", "m_ClassName": "BundleResource"},
            {"m_AssemblyName": "Fixture", "m_ClassName": "FixtureAsset"},
        ],
        "m_InternalIdPrefixes": [],
        "future_catalog_field": {"preserved": True},
    }


class AddressablesCatalogTests(unittest.TestCase):
    def _write_fixture(
        self,
        root: Path,
        catalog: dict[str, object],
    ) -> tuple[Path, Path, ResourceRecord]:
        aa_dir = root / "MuseDash_Data" / "StreamingAssets" / "aa"
        bundle = aa_dir / "StandaloneWindows64" / "bundle.bundle"
        bundle.parent.mkdir(parents=True)
        payload = b"UnityFS synthetic fixture"
        bundle.write_bytes(payload)
        catalog_path = aa_dir / "catalog.json"
        settings_path = aa_dir / "settings.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        settings_path.write_text(
            json.dumps(
                {
                    "m_AddressablesVersion": "1.21.20",
                    "m_buildTarget": "StandaloneWindows64",
                }
            ),
            encoding="utf-8",
        )
        record = ResourceRecord(
            relative_path="MuseDash_Data/StreamingAssets/aa/StandaloneWindows64/bundle.bundle",
            size=len(payload),
            suffix=".bundle",
            magic="UnityFS",
            sha256=hashlib.sha256(payload).hexdigest(),
            category="unity_bundle_candidate",
        )
        return catalog_path, settings_path, record

    def test_compact_catalog_recovers_keys_dependencies_and_local_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_path, settings_path, record = self._write_fixture(
                root, _fixture_catalog()
            )

            result = parse_addressables_catalog(
                root,
                catalog_path,
                settings_path,
                [record],
            )

            self.assertEqual(result["addressables_version"], "1.21.20")
            self.assertEqual(result["counts"]["entry_count"], 2)
            self.assertEqual(result["counts"]["extra_object_count"], 1)
            self.assertEqual(result["object_tag_counts"]["keys"], {"AsciiString": 2})
            bundle_entry, asset_entry = result["entries"]
            self.assertEqual(
                result["internal_ids"][bundle_entry["internal_id_index"]]["local_path"],
                record.relative_path,
            )
            self.assertEqual(
                result["extra_objects"][0]["object"]["value"]["json"][
                    "future_field"
                ],
                "preserved",
            )
            self.assertEqual(asset_entry["dependency_entry_indices"], [0])
            self.assertEqual(asset_entry["dependency_hash"], 12345)
            self.assertEqual(
                result["bundle_path_crosscheck"],
                {
                    "catalog_bundle_path_count": 1,
                    "inventory_bundle_path_count": 1,
                    "matched_count": 1,
                    "catalog_only": [],
                    "inventory_only": [],
                },
            )
            self.assertTrue(
                result["catalog_metadata"]["future_catalog_field"]["preserved"]
            )

    def test_unknown_object_tag_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_path, settings_path, record = self._write_fixture(
                root, _fixture_catalog(unknown_key_tag=True)
            )

            with self.assertRaisesRegex(AddressablesCatalogError, "unknown object tag 99"):
                parse_addressables_catalog(root, catalog_path, settings_path, [record])

    def test_invalid_entry_index_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_path, settings_path, record = self._write_fixture(
                root, _fixture_catalog(bad_provider_index=True)
            )

            with self.assertRaisesRegex(AddressablesCatalogError, "provider index 9"):
                parse_addressables_catalog(root, catalog_path, settings_path, [record])


if __name__ == "__main__":
    unittest.main()
