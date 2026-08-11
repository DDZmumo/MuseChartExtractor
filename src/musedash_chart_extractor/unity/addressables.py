"""Strict reader for Addressables 1.21 compact JSON content catalogs.

The byte layout is independently implemented from the documented behavior of
Unity Addressables 1.21.20.  No Unity source code or binaries are redistributed.
Unknown object tags and inconsistent offsets fail with source context instead
of being guessed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from ..scanner import ResourceRecord, ScannerError, validate_game_directory

RUNTIME_PATH_TOKEN = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
ENCODED_FIELDS = {
    "m_KeyDataString",
    "m_BucketDataString",
    "m_EntryDataString",
    "m_ExtraDataString",
}
NORMALIZED_TABLE_FIELDS = {
    "m_InternalIds",
    "m_ProviderIds",
    "m_resourceTypes",
    "m_InternalIdPrefixes",
}
OBJECT_TYPE_NAMES = {
    0: "AsciiString",
    1: "UnicodeString",
    2: "UInt16",
    3: "UInt32",
    4: "Int32",
    5: "Hash128",
    6: "Type",
    7: "JsonObject",
}


class AddressablesCatalogError(ScannerError):
    """Raised when compact catalog bytes violate their declared structure."""


@dataclass(frozen=True, slots=True)
class DecodedCatalogObject:
    tag: int
    type_name: str
    value: Any
    start: int
    end: int

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type_name, "value": self.value}


@dataclass(frozen=True, slots=True)
class _Bucket:
    key_data_offset: int
    entry_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _RawEntry:
    internal_id_index: int
    provider_index: int
    dependency_key_index: int
    dependency_hash: int
    data_offset: int
    primary_key_index: int
    resource_type_index: int


def _fail(message: str) -> AddressablesCatalogError:
    return AddressablesCatalogError(f"invalid Addressables compact catalog: {message}")


def _require_range(data: bytes, offset: int, length: int, context: str) -> None:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise _fail(
            f"{context} exceeds stream bounds "
            f"(offset={offset}, length={length}, stream={len(data)})"
        )


def _read_i32(data: bytes, offset: int, context: str) -> int:
    _require_range(data, offset, 4, context)
    return struct.unpack_from("<i", data, offset)[0]


def _read_u16(data: bytes, offset: int, context: str) -> int:
    _require_range(data, offset, 2, context)
    return struct.unpack_from("<H", data, offset)[0]


def _read_u32(data: bytes, offset: int, context: str) -> int:
    _require_range(data, offset, 4, context)
    return struct.unpack_from("<I", data, offset)[0]


def _decode_text(payload: bytes, encoding: str, context: str) -> str:
    try:
        return payload.decode(encoding)
    except UnicodeDecodeError as exc:
        raise _fail(f"{context} is not valid {encoding}: {exc}") from exc


def decode_compact_object(data: bytes, offset: int) -> DecodedCatalogObject:
    """Decode one typed SerializationUtilities object at an absolute offset."""

    _require_range(data, offset, 1, "object tag")
    start = offset
    tag = data[offset]
    offset += 1
    type_name = OBJECT_TYPE_NAMES.get(tag)
    if type_name is None:
        raise _fail(f"unknown object tag {tag} at offset {start}")

    if tag in {0, 1}:
        byte_length = _read_i32(data, offset, f"{type_name} length")
        offset += 4
        if byte_length < 0:
            raise _fail(f"negative {type_name} byte length {byte_length} at {start}")
        if tag == 1 and byte_length % 2:
            raise _fail(f"odd UTF-16LE byte length {byte_length} at {start}")
        _require_range(data, offset, byte_length, type_name)
        encoding = "ascii" if tag == 0 else "utf-16-le"
        value = _decode_text(data[offset : offset + byte_length], encoding, type_name)
        offset += byte_length
    elif tag == 2:
        value = _read_u16(data, offset, type_name)
        offset += 2
    elif tag == 3:
        value = _read_u32(data, offset, type_name)
        offset += 4
    elif tag == 4:
        value = _read_i32(data, offset, type_name)
        offset += 4
    elif tag in {5, 6}:
        _require_range(data, offset, 1, f"{type_name} length")
        byte_length = data[offset]
        offset += 1
        _require_range(data, offset, byte_length, type_name)
        payload = data[offset : offset + byte_length]
        offset += byte_length
        if tag == 5:
            value = _decode_text(payload, "ascii", type_name)
        else:
            # Addressables 1.21.20's Type writer/reader disagree about this
            # payload. Preserve bytes until a real catalog fixture establishes
            # semantics instead of coercing them to a GUID.
            value = {
                "raw_base64": base64.b64encode(payload).decode("ascii"),
                "ascii_preview": (
                    payload.decode("ascii") if payload.isascii() else None
                ),
            }
    else:
        _require_range(data, offset, 1, "JsonObject assembly length")
        assembly_length = data[offset]
        offset += 1
        _require_range(data, offset, assembly_length, "JsonObject assembly")
        assembly_name = _decode_text(
            data[offset : offset + assembly_length],
            "ascii",
            "JsonObject assembly",
        )
        offset += assembly_length

        _require_range(data, offset, 1, "JsonObject class length")
        class_length = data[offset]
        offset += 1
        _require_range(data, offset, class_length, "JsonObject class")
        class_name = _decode_text(
            data[offset : offset + class_length],
            "ascii",
            "JsonObject class",
        )
        offset += class_length

        json_length = _read_i32(data, offset, "JsonObject JSON length")
        offset += 4
        if json_length < 0 or json_length % 2:
            raise _fail(f"invalid JsonObject UTF-16LE byte length {json_length} at {start}")
        _require_range(data, offset, json_length, "JsonObject JSON")
        raw_json = _decode_text(
            data[offset : offset + json_length],
            "utf-16-le",
            "JsonObject JSON",
        )
        offset += json_length
        try:
            parsed_json = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise _fail(f"JsonObject at {start} contains invalid JSON: {exc}") from exc
        value = {
            "assembly_name": assembly_name,
            "class_name": class_name,
            "json": parsed_json,
            "raw_json": raw_json,
        }

    return DecodedCatalogObject(tag, type_name, value, start, offset)


def _decode_base64_field(catalog: dict[str, Any], name: str) -> bytes:
    value = catalog.get(name)
    if not isinstance(value, str):
        raise _fail(f"{name} is missing or is not a string")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise _fail(f"{name} is not strict Base64: {exc}") from exc


def _parse_buckets(data: bytes) -> list[_Bucket]:
    count = _read_i32(data, 0, "bucket count")
    if count < 0:
        raise _fail(f"negative bucket count {count}")
    offset = 4
    buckets = []
    for index in range(count):
        key_offset = _read_i32(data, offset, f"bucket {index} key offset")
        offset += 4
        entry_count = _read_i32(data, offset, f"bucket {index} entry count")
        offset += 4
        if entry_count < 0:
            raise _fail(f"negative entry count in bucket {index}: {entry_count}")
        byte_length = entry_count * 4
        _require_range(data, offset, byte_length, f"bucket {index} entries")
        entries = struct.unpack_from(f"<{entry_count}i", data, offset) if entry_count else ()
        offset += byte_length
        buckets.append(_Bucket(key_offset, tuple(entries)))
    if offset != len(data):
        raise _fail(f"bucket stream has {len(data) - offset} trailing bytes")
    return buckets


def _parse_keys(data: bytes, buckets: list[_Bucket]) -> list[DecodedCatalogObject]:
    count = _read_i32(data, 0, "key count")
    if count != len(buckets):
        raise _fail(f"key count {count} does not match bucket count {len(buckets)}")
    offsets = [bucket.key_data_offset for bucket in buckets]
    if offsets and offsets[0] != 4:
        raise _fail(f"first key starts at {offsets[0]}, expected 4")
    if offsets != sorted(set(offsets)):
        raise _fail("key offsets are not unique and strictly increasing")

    keys = []
    for index, key_offset in enumerate(offsets):
        key = decode_compact_object(data, key_offset)
        expected_end = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        if key.end != expected_end:
            raise _fail(
                f"key {index} ends at {key.end}, next boundary is {expected_end}"
            )
        keys.append(key)
    if not offsets and len(data) != 4:
        raise _fail(f"empty key stream has {len(data) - 4} trailing bytes")
    return keys


def _parse_entries(data: bytes) -> list[_RawEntry]:
    count = _read_i32(data, 0, "entry count")
    if count < 0:
        raise _fail(f"negative entry count {count}")
    expected_length = 4 + count * 28
    if len(data) != expected_length:
        raise _fail(
            f"entry stream length {len(data)} does not equal 4 + {count} * 28"
        )
    entries = []
    offset = 4
    for _ in range(count):
        entries.append(_RawEntry(*struct.unpack_from("<7i", data, offset)))
        offset += 28
    return entries


def _validate_index(index: int, length: int, context: str) -> None:
    if index < 0 or index >= length:
        raise _fail(f"{context} index {index} is outside 0..{length - 1}")


def _parse_extra_objects(
    data: bytes,
    entries: list[_RawEntry],
) -> dict[int, DecodedCatalogObject]:
    offsets = sorted({entry.data_offset for entry in entries if entry.data_offset >= 0})
    if offsets and offsets[0] != 0:
        raise _fail(f"first extra object starts at {offsets[0]}, expected 0")
    if not offsets and data:
        raise _fail(f"extra stream has {len(data)} unreferenced bytes")
    objects = {}
    for index, offset in enumerate(offsets):
        decoded = decode_compact_object(data, offset)
        expected_end = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        if decoded.end != expected_end:
            raise _fail(
                f"extra object at {offset} ends at {decoded.end}, "
                f"next boundary is {expected_end}"
            )
        objects[offset] = decoded
    return objects


def _expand_internal_id(raw: str, prefixes: list[str]) -> str:
    if not prefixes:
        return raw
    separator = raw.rfind("#")
    if separator < 0:
        return raw
    try:
        prefix_index = int(raw[:separator])
    except ValueError:
        return raw
    _validate_index(prefix_index, len(prefixes), "internal ID prefix")
    return prefixes[prefix_index] + raw[separator + 1 :]


def _local_runtime_path(
    internal_id: str,
    *,
    catalog_dir: Path,
    game_dir: Path,
) -> str | None:
    if not internal_id.startswith(RUNTIME_PATH_TOKEN):
        return None
    suffix = internal_id[len(RUNTIME_PATH_TOKEN) :].lstrip("/\\")
    normalized = suffix.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise _fail(f"unsafe RuntimePath suffix: {suffix!r}")
    candidate = (catalog_dir / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(game_dir)
    except ValueError as exc:
        raise _fail(f"RuntimePath escapes game directory: {internal_id!r}") from exc
    if not candidate.is_file():
        return None
    return candidate.relative_to(game_dir).as_posix()


def _key_text(key: DecodedCatalogObject) -> str:
    if isinstance(key.value, (str, int)):
        return str(key.value)
    if key.type_name in {"Hash128", "Type"}:
        return str(key.value)
    return json.dumps(key.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_json_file(path: Path, context: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AddressablesCatalogError(f"cannot read {context} {path}: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AddressablesCatalogError(f"cannot parse {context} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AddressablesCatalogError(f"{context} root is not a JSON object: {path}")
    return raw, value


def parse_addressables_catalog(
    game_dir: str | Path,
    catalog_path: str | Path,
    settings_path: str | Path,
    inventory: Iterable[ResourceRecord] = (),
) -> dict[str, Any]:
    """Decode and validate a compact JSON catalog into queryable locations."""

    root = validate_game_directory(game_dir)
    catalog_file = Path(catalog_path).resolve(strict=True)
    settings_file = Path(settings_path).resolve(strict=True)
    for path, label in ((catalog_file, "catalog"), (settings_file, "settings")):
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AddressablesCatalogError(f"{label} is outside game directory: {path}") from exc

    catalog_raw, catalog = _read_json_file(catalog_file, "catalog")
    settings_raw, settings = _read_json_file(settings_file, "settings")
    internal_ids = catalog.get("m_InternalIds")
    providers = catalog.get("m_ProviderIds")
    resource_types = catalog.get("m_resourceTypes")
    prefixes = catalog.get("m_InternalIdPrefixes") or []
    if not isinstance(internal_ids, list) or not all(isinstance(item, str) for item in internal_ids):
        raise _fail("m_InternalIds is not a string array")
    if not isinstance(providers, list) or not all(isinstance(item, str) for item in providers):
        raise _fail("m_ProviderIds is not a string array")
    if not isinstance(resource_types, list) or not all(isinstance(item, dict) for item in resource_types):
        raise _fail("m_resourceTypes is not an object array")
    if not isinstance(prefixes, list) or not all(isinstance(item, str) for item in prefixes):
        raise _fail("m_InternalIdPrefixes is not a string array")

    key_data = _decode_base64_field(catalog, "m_KeyDataString")
    bucket_data = _decode_base64_field(catalog, "m_BucketDataString")
    entry_data = _decode_base64_field(catalog, "m_EntryDataString")
    extra_data = _decode_base64_field(catalog, "m_ExtraDataString")
    buckets = _parse_buckets(bucket_data)
    keys = _parse_keys(key_data, buckets)
    raw_entries = _parse_entries(entry_data)

    for bucket_index, bucket in enumerate(buckets):
        for entry_index in bucket.entry_indices:
            _validate_index(entry_index, len(raw_entries), f"bucket {bucket_index} entry")
    extra_objects = _parse_extra_objects(extra_data, raw_entries)

    entry_key_indices: list[list[int]] = [[] for _ in raw_entries]
    for key_index, bucket in enumerate(buckets):
        for entry_index in bucket.entry_indices:
            entry_key_indices[entry_index].append(key_index)

    internal_id_rows = []
    for internal_id_index, internal_id_raw in enumerate(internal_ids):
        internal_id_expanded = _expand_internal_id(internal_id_raw, prefixes)
        internal_id_rows.append(
            {
                "internal_id_index": internal_id_index,
                "raw": internal_id_raw,
                "expanded": internal_id_expanded,
                "local_path": _local_runtime_path(
                    internal_id_expanded,
                    catalog_dir=catalog_file.parent,
                    game_dir=root,
                ),
            }
        )

    entries: list[dict[str, Any]] = []
    for entry_index, raw_entry in enumerate(raw_entries):
        _validate_index(raw_entry.internal_id_index, len(internal_ids), f"entry {entry_index} internal ID")
        _validate_index(raw_entry.provider_index, len(providers), f"entry {entry_index} provider")
        _validate_index(raw_entry.primary_key_index, len(keys), f"entry {entry_index} primary key")
        _validate_index(raw_entry.resource_type_index, len(resource_types), f"entry {entry_index} resource type")
        if raw_entry.dependency_key_index < -1:
            raise _fail(
                f"entry {entry_index} has invalid dependency key index "
                f"{raw_entry.dependency_key_index}"
            )
        if raw_entry.dependency_key_index >= 0:
            _validate_index(raw_entry.dependency_key_index, len(keys), f"entry {entry_index} dependency key")
        if raw_entry.data_offset < -1:
            raise _fail(f"entry {entry_index} has invalid data offset {raw_entry.data_offset}")
        if raw_entry.data_offset >= 0 and raw_entry.data_offset not in extra_objects:
            raise _fail(f"entry {entry_index} references unknown data offset {raw_entry.data_offset}")

        dependency_indices = (
            list(buckets[raw_entry.dependency_key_index].entry_indices)
            if raw_entry.dependency_key_index >= 0
            else []
        )
        entries.append(
            {
                "entry_index": entry_index,
                "internal_id_index": raw_entry.internal_id_index,
                "provider_index": raw_entry.provider_index,
                "dependency_key_index": (
                    raw_entry.dependency_key_index
                    if raw_entry.dependency_key_index >= 0
                    else None
                ),
                "dependency_entry_indices": dependency_indices,
                "dependency_hash": raw_entry.dependency_hash,
                "data_offset": raw_entry.data_offset if raw_entry.data_offset >= 0 else None,
                "primary_key_index": raw_entry.primary_key_index,
                "primary_key": _key_text(keys[raw_entry.primary_key_index]),
                "resource_type_index": raw_entry.resource_type_index,
                "key_indices": entry_key_indices[entry_index],
            }
        )

    key_tag_counts = Counter(key.type_name for key in keys)
    extra_tag_counts = Counter(obj.type_name for obj in extra_objects.values())
    key_buckets = [
        {
            "key_index": index,
            "key_data_offset": bucket.key_data_offset,
            "key": keys[index].to_json(),
            "entry_indices": list(bucket.entry_indices),
        }
        for index, bucket in enumerate(buckets)
    ]

    catalog_bundle_paths = sorted(
        {
            str(row["local_path"])
            for row in internal_id_rows
            if row["local_path"] is not None
            and str(row["local_path"]).casefold().endswith(".bundle")
        },
        key=lambda value: (value.casefold(), value),
    )
    inventory_bundle_paths = sorted(
        {
            record.relative_path
            for record in inventory
            if record.category == "unity_bundle_candidate"
        },
        key=lambda value: (value.casefold(), value),
    )
    catalog_folded = {value.casefold(): value for value in catalog_bundle_paths}
    inventory_folded = {value.casefold(): value for value in inventory_bundle_paths}

    return {
        "schema_version": 1,
        "catalog_format": "Addressables compact JSON",
        "addressables_version": settings.get("m_AddressablesVersion"),
        "source": {
            "catalog": catalog_file.relative_to(root).as_posix(),
            "catalog_size": len(catalog_raw),
            "catalog_sha256": hashlib.sha256(catalog_raw).hexdigest(),
            "settings": settings_file.relative_to(root).as_posix(),
            "settings_size": len(settings_raw),
            "settings_sha256": hashlib.sha256(settings_raw).hexdigest(),
        },
        "locator_id": catalog.get("m_LocatorId"),
        "build_result_hash": catalog.get("m_BuildResultHash"),
        "settings_data": settings,
        "catalog_metadata": {
            key: value
            for key, value in catalog.items()
            if key not in ENCODED_FIELDS and key not in NORMALIZED_TABLE_FIELDS
        },
        "counts": {
            "key_count": len(keys),
            "bucket_count": len(buckets),
            "bucket_entry_reference_count": sum(
                len(bucket.entry_indices) for bucket in buckets
            ),
            "entry_count": len(entries),
            "internal_id_count": len(internal_ids),
            "provider_count": len(providers),
            "resource_type_count": len(resource_types),
            "internal_id_prefix_count": len(prefixes),
            "extra_object_count": len(extra_objects),
            "local_file_internal_id_count": sum(
                row["local_path"] is not None for row in internal_id_rows
            ),
            "local_file_entry_count": sum(
                internal_id_rows[entry["internal_id_index"]]["local_path"] is not None
                for entry in entries
            ),
        },
        "stream_sizes": {
            "key_data_bytes": len(key_data),
            "bucket_data_bytes": len(bucket_data),
            "entry_data_bytes": len(entry_data),
            "extra_data_bytes": len(extra_data),
        },
        "object_tag_counts": {
            "keys": dict(sorted(key_tag_counts.items())),
            "extra": dict(sorted(extra_tag_counts.items())),
        },
        "providers": providers,
        "resource_types": resource_types,
        "internal_id_prefixes": prefixes,
        "internal_ids": internal_id_rows,
        "key_buckets": key_buckets,
        "extra_objects": [
            {
                "data_offset": offset,
                "object": extra_objects[offset].to_json(),
            }
            for offset in sorted(extra_objects)
        ],
        "entries": entries,
        "bundle_path_crosscheck": {
            "catalog_bundle_path_count": len(catalog_bundle_paths),
            "inventory_bundle_path_count": len(inventory_bundle_paths),
            "matched_count": len(set(catalog_folded) & set(inventory_folded)),
            "catalog_only": [
                catalog_folded[key]
                for key in sorted(set(catalog_folded) - set(inventory_folded))
            ],
            "inventory_only": [
                inventory_folded[key]
                for key in sorted(set(inventory_folded) - set(catalog_folded))
            ],
        },
    }
