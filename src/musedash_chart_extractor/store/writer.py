"""Transactional writer for content-addressed Odin chart stores."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

import UnityPy

from .. import __version__
from ..batch import (
    _candidate_identity,
    _index_entries,
    _object_type_name,
    _output_root,
    _payload_bytes,
    _required_integer,
    _required_mapping,
    _required_sequence,
    _required_string,
    _source_path,
    _validate_index_candidate_provenance,
    _validated_grouping_census,
)
from ..charts.models import CANONICAL_SCHEMA_VERSION
from ..discovery.first_chart import FirstChartProjectionError, build_experimental_chart
from ..scanner import fingerprint_file, validate_game_directory
from ..unity.odin import OdinParseError, parse_stage_info_payload
from .schema import (
    STORE_INDEX_NAME,
    STORE_MANIFEST_NAME,
    STORE_PARSER_FAMILY,
    STORE_PARSER_VERSION,
    STORE_SCHEMA_VERSION,
    ChartStoreError,
    atomic_write_bytes,
    atomic_write_json,
    compute_logical_digest,
    contained_path,
    create_schema,
    metadata_rows,
    path_is_link,
    payload_relative_path,
    sha256_bytes,
    stable_json,
)


def _mapping_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(value))


def _stripped_stage_info(value: Mapping[str, Any], *, chart_id: str) -> dict[str, Any]:
    serialization_value = value.get("serializationData")
    if not isinstance(serialization_value, Mapping):
        raise ChartStoreError(f"chart {chart_id} has no serializationData envelope")
    serialization = {
        key: deepcopy(item)
        for key, item in serialization_value.items()
        if key != "SerializedBytes"
    }
    if serialization.get("SerializedFormat") != 0:
        raise ChartStoreError(f"chart {chart_id} has unsupported SerializedFormat")
    if "SerializedBytes" not in serialization_value:
        raise ChartStoreError(f"chart {chart_id} envelope has no SerializedBytes")
    envelope = {
        key: deepcopy(item)
        for key, item in value.items()
        if key != "serializationData"
    }
    envelope["serializationData"] = serialization
    return envelope


def _existing_payload_files(root: Path) -> set[str]:
    payload_root = root / "payloads"
    if not payload_root.exists():
        return set()
    if path_is_link(payload_root) or not payload_root.is_dir():
        raise ChartStoreError("store payload root is not a regular directory")
    result: set[str] = set()
    for current, directory_names, file_names in os.walk(
        payload_root, followlinks=False
    ):
        current_path = Path(current)
        for name in list(directory_names):
            child = current_path / name
            if path_is_link(child):
                raise ChartStoreError(
                    "store payload tree contains a symbolic link: "
                    f"{child.relative_to(root).as_posix()}"
                )
        for name in file_names:
            child = current_path / name
            if path_is_link(child):
                raise ChartStoreError(
                    "store payload tree contains a symbolic link: "
                    f"{child.relative_to(root).as_posix()}"
                )
            result.add(child.relative_to(root).as_posix())
    return result


def _remove_staging_tree(staging: Path) -> None:
    if not staging.exists():
        return
    if path_is_link(staging) or not staging.is_dir():
        raise ChartStoreError(f"invalid store staging directory: {staging}")
    try:
        for current, directory_names, file_names in os.walk(
            staging, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            for name in (*directory_names, *file_names):
                child = current_path / name
                if path_is_link(child):
                    relative = child.relative_to(staging.parent).as_posix()
                    raise ChartStoreError(
                        "store staging tree contains a symbolic link or junction: "
                        f"{relative}"
                    )
        shutil.rmtree(staging)
    except ChartStoreError:
        raise
    except OSError as exc:
        raise ChartStoreError(f"cannot remove store staging directory: {staging}: {exc}") from exc


def _atomic_payload(stage_root: Path, relative: str, payload: bytes) -> Path:
    destination = contained_path(stage_root, relative, context="staged payload path")
    atomic_write_bytes(destination, payload)
    return destination


def _verify_existing_payload(path: Path, *, expected_sha256: str, expected_size: int) -> None:
    if not path.is_file() or path_is_link(path):
        raise ChartStoreError(f"existing content-addressed payload is not a regular file: {path}")
    actual_size, actual_sha256, _prefix = fingerprint_file(path)
    if actual_size != expected_size or actual_sha256 != expected_sha256:
        raise ChartStoreError(
            f"existing content-addressed payload does not match its name: {path}"
        )


def _index_row_json(
    chart_id: str,
    indexed: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
    unresolved: Mapping[str, Mapping[str, Any]],
) -> tuple[str | None, Mapping[str, Any]]:
    match = indexed.get(chart_id)
    if match is not None:
        song, chart = match
        return _required_string(song.get("song_id"), context=f"chart {chart_id} song id"), chart
    return None, unresolved[chart_id]


def _open_build_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    create_schema(connection)
    return connection


def _insert_song_rows(
    connection: sqlite3.Connection,
    song_chart_index: Mapping[str, Any],
) -> None:
    seen_casefold: dict[str, str] = {}
    rows: list[tuple[str, str, str, str]] = []
    for position, value in enumerate(
        _required_sequence(song_chart_index.get("songs"), context="song index songs")
    ):
        song = _required_mapping(value, context=f"song index song {position}")
        song_id = _required_string(song.get("song_id"), context=f"song {position} id")
        folded = song_id.casefold()
        previous = seen_casefold.get(folded)
        if previous is not None:
            raise ChartStoreError(
                f"case-insensitive song ID collision: {previous!r}, {song_id!r}"
            )
        seen_casefold[folded] = song_id
        rendered = stable_json(song)
        rows.append((song_id, folded, rendered, sha256_bytes(rendered.encode("utf-8"))))
    connection.executemany(
        "INSERT INTO songs(song_id, song_id_casefold, song_json, song_sha256) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def _insert_note_config_rows(
    connection: sqlite3.Connection,
    note_configs_by_uid: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[int, int]:
    rows: list[tuple[str, str, int]] = []
    total_rows = 0
    seen_casefold: dict[str, str] = {}
    for uid in sorted(note_configs_by_uid, key=lambda value: (value.casefold(), value)):
        if not isinstance(uid, str) or not uid:
            raise ChartStoreError("note config UID must be a non-empty string")
        folded = uid.casefold()
        previous = seen_casefold.get(folded)
        if previous is not None and previous != uid:
            raise ChartStoreError(
                f"case-insensitive note UID collision: {previous!r}, {uid!r}"
            )
        seen_casefold[folded] = uid
        values = note_configs_by_uid[uid]
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ChartStoreError(f"note config {uid!r} rows must be an array")
        copied = []
        for position, value in enumerate(values):
            if not isinstance(value, Mapping):
                raise ChartStoreError(f"note config {uid!r} row {position} is not an object")
            copied.append(_mapping_copy(value))
        total_rows += len(copied)
        rows.append((uid, stable_json(copied), len(copied)))
    connection.executemany(
        "INSERT INTO note_configs(uid, rows_json, row_count) VALUES (?, ?, ?)", rows
    )
    return len(rows), total_rows


def extract_chart_store(
    game_dir: str | Path,
    output_dir: str | Path,
    candidates: Sequence[Mapping[str, Any]],
    song_chart_index: Mapping[str, Any],
    *,
    grouping_census_summary: Mapping[str, Any],
    note_configs_by_uid: Mapping[str, Sequence[Mapping[str, Any]]],
    note_data_provenance: Mapping[str, Any],
    parser_family: str,
    parser_version: str,
    validation_reports: Mapping[str, Mapping[str, Any]] | None = None,
    expected_candidate_count: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    loader: Callable[[str], Any] = UnityPy.load,
    parser: Callable[[bytes], Mapping[str, Any]] = parse_stage_info_payload,
    experimental_builder: Callable[..., dict[str, Any]] = build_experimental_chart,
    extractor_version: str = __version__,
) -> dict[str, Any]:
    """Build a complete compact store and publish ``store.json`` last."""

    root = validate_game_directory(game_dir)
    requested_output = Path(output_dir).expanduser()
    if path_is_link(requested_output):
        raise ChartStoreError(
            f"store output is a symbolic link or junction: {requested_output}"
        )
    output = _output_root(root, output_dir)
    if not candidates:
        raise ChartStoreError("chart store candidate set is empty")
    if expected_candidate_count is not None and len(candidates) != expected_candidate_count:
        raise ChartStoreError(
            f"expected {expected_candidate_count} candidates, found {len(candidates)}"
        )
    parser_family = _required_string(parser_family, context="store parser family")
    parser_version = _required_string(parser_version, context="store parser version")
    if parser_family != STORE_PARSER_FAMILY:
        raise ChartStoreError(f"unsupported store parser family: {parser_family!r}")
    if parser_version != STORE_PARSER_VERSION:
        raise ChartStoreError(f"unsupported store parser version: {parser_version!r}")
    extractor_version = _required_string(extractor_version, context="store extractor version")
    if output.exists() and (path_is_link(output) or not output.is_dir()):
        raise ChartStoreError(f"store output is not a regular directory: {output}")
    output.mkdir(parents=True, exist_ok=True)

    index_fingerprint = _required_string(
        song_chart_index.get("inventory_fingerprint"),
        context="song index inventory fingerprint",
    )
    identities: dict[str, dict[str, Any]] = {}
    candidate_by_id: dict[str, Mapping[str, Any]] = {}
    by_source: dict[str, list[str]] = defaultdict(list)
    chart_casefolds: dict[str, str] = {}
    source_casefolds: dict[str, str] = {}
    for position, candidate_value in enumerate(candidates):
        candidate = _required_mapping(candidate_value, context=f"candidate {position}")
        identity = _candidate_identity(candidate, position=position)
        chart_id = identity["chart_id"]
        if identity["inventory_fingerprint"] != index_fingerprint:
            raise ChartStoreError(
                f"candidate {chart_id} inventory fingerprint differs from song index"
            )
        if chart_id in identities:
            raise ChartStoreError(f"duplicate candidate chart ID: {chart_id!r}")
        folded_chart = chart_id.casefold()
        if folded_chart in chart_casefolds:
            raise ChartStoreError(
                f"case-insensitive candidate chart ID collision: "
                f"{chart_casefolds[folded_chart]!r}, {chart_id!r}"
            )
        chart_casefolds[folded_chart] = chart_id
        folded_source = identity["source"].casefold()
        previous_source = source_casefolds.get(folded_source)
        if previous_source is not None and previous_source != identity["source"]:
            raise ChartStoreError(
                f"case-insensitive candidate source collision: "
                f"{previous_source!r}, {identity['source']!r}"
            )
        source_casefolds[folded_source] = identity["source"]
        identities[chart_id] = identity
        candidate_by_id[chart_id] = candidate
        by_source[identity["source"]].append(chart_id)

    census = _validated_grouping_census(
        grouping_census_summary,
        inventory_fingerprint=index_fingerprint,
        candidate_count=len(candidates),
        source_count=len(by_source),
    )
    indexed, unresolved = _index_entries(song_chart_index)
    if set(identities) != set(indexed) | set(unresolved):
        raise ChartStoreError("candidate and song-index chart ID sets differ")
    index_counts = _required_mapping(song_chart_index.get("counts"), context="song index counts")
    expected_counts = {
        "candidate_chart_count": len(candidates),
        "indexed_chart_count": len(indexed),
        "unresolved_chart_count": len(unresolved),
    }
    for field, expected in expected_counts.items():
        if index_counts.get(field) != expected:
            raise ChartStoreError(
                f"song index {field} mismatch: expected {expected}, "
                f"found {index_counts.get(field)!r}"
            )
    _validate_index_candidate_provenance(identities, indexed, unresolved)

    staging = output / ".staging"
    building_marker = output / ".building"
    _remove_staging_tree(staging)
    staging.mkdir(parents=True)
    atomic_write_bytes(building_marker, b"incomplete\n")
    database_path = staging / STORE_INDEX_NAME
    rows: list[dict[str, Any]] = []
    stage_rows: dict[str, tuple[int, str, str]] = {}
    payload_rows: dict[str, tuple[int, str]] = {}
    note_uid_refs: dict[str, list[str]] = {}
    reports = validation_reports or {}

    ordered_sources = sorted(by_source, key=lambda value: (value.casefold(), value))
    for source_number, source in enumerate(ordered_sources, start=1):
        chart_ids = sorted(by_source[source], key=lambda value: (value.casefold(), value))
        source_identities = [identities[chart_id] for chart_id in chart_ids]
        expected_sizes = {identity["source_size"] for identity in source_identities}
        expected_hashes = {identity["source_sha256"] for identity in source_identities}
        if len(expected_sizes) != 1 or len(expected_hashes) != 1:
            raise ChartStoreError(f"candidate source fingerprints disagree: {source}")
        try:
            source_path = _source_path(root, source)
        except Exception as exc:
            raise ChartStoreError(str(exc)) from exc
        actual_size, actual_sha256, _prefix = fingerprint_file(source_path)
        if actual_size != next(iter(expected_sizes)) or actual_sha256 != next(iter(expected_hashes)):
            raise ChartStoreError(f"candidate source fingerprint is stale: {source}")
        try:
            environment = loader(str(source_path))
            objects: dict[int, Any] = {}
            for obj in environment.objects:
                path_id = int(obj.path_id)
                if path_id in objects:
                    raise ChartStoreError(f"duplicate PathID {path_id} in {source}")
                objects[path_id] = obj
        except ChartStoreError:
            raise
        except Exception as exc:
            raise ChartStoreError(f"cannot load candidate source {source}: {exc}") from exc

        for chart_id in chart_ids:
            candidate = candidate_by_id[chart_id]
            identity = identities[chart_id]
            song_id, index_row = _index_row_json(chart_id, indexed, unresolved)
            status = "failed"
            reason: str | None = None
            raw_parse_status = "not-attempted"
            grouping_status = "not-attempted"
            canonical_status = "not-attempted"
            raw_count: int | None = None
            group_count: int | None = None
            event_count: int | None = None
            sentinel_count: int | None = None
            payload_sha256: str | None = None
            try:
                obj = objects[identity["path_id"]]
                if _object_type_name(obj) != identity["object_type"]:
                    raise ValueError("object type differs from candidate evidence")
                if "object_byte_size" in candidate and int(obj.byte_size) != _required_integer(
                    candidate.get("object_byte_size"),
                    context=f"candidate {chart_id} object byte size",
                    minimum=0,
                ):
                    raise ValueError("object byte size differs from candidate evidence")
                data = obj.parse_as_dict()
                if not isinstance(data, Mapping):
                    raise ValueError("MonoBehaviour TypeTree result is not an object")
                if data.get("m_Name") != chart_id:
                    raise ValueError("MonoBehaviour m_Name differs from candidate asset name")
                serialization = data.get("serializationData")
                if not isinstance(serialization, Mapping):
                    raise ValueError("MonoBehaviour has no serializationData object")
                if serialization.get("SerializedFormat") != 0:
                    raise ValueError("MonoBehaviour SerializedFormat is not zero")
                payload = _payload_bytes(serialization.get("SerializedBytes"))
                payload_sha256 = hashlib.sha256(payload).hexdigest()
                if (
                    len(payload) != identity["payload_byte_count"]
                    or payload_sha256 != identity["payload_sha256"]
                ):
                    raise ValueError("serialized payload fingerprint differs from candidate evidence")
            except Exception as exc:
                reason = "object-evidence-mismatch"
                rows.append(
                    {
                        "chart_id": chart_id,
                        "song_id": song_id,
                        "difficulty_id": index_row.get("difficulty_id"),
                        "status": status,
                        "reason": reason,
                        "payload_sha256": None,
                        "source_path": source,
                        "container_path": identity["container_path"],
                        "path_id": identity["path_id"],
                        "object_type": identity["object_type"],
                        "index_row": _mapping_copy(index_row),
                        "raw_parse_status": raw_parse_status,
                        "grouping_status": grouping_status,
                        "canonical_status": canonical_status,
                        "raw_record_count": None,
                        "record_group_count": None,
                        "logical_event_count": None,
                        "sentinel_count": None,
                        "validation": _mapping_copy(reports[chart_id]) if chart_id in reports else None,
                        "error_type": type(exc).__name__,
                    }
                )
                continue

            relative = payload_relative_path(payload_sha256).as_posix()
            final_payload = contained_path(output, relative, context="payload path")
            if final_payload.exists():
                _verify_existing_payload(
                    final_payload,
                    expected_sha256=payload_sha256,
                    expected_size=len(payload),
                )
            elif payload_sha256 not in payload_rows:
                _atomic_payload(staging, relative, payload)
            previous_payload = payload_rows.get(payload_sha256)
            if previous_payload is not None and previous_payload != (len(payload), relative):
                raise ChartStoreError(f"payload SHA collision for {payload_sha256}")
            payload_rows[payload_sha256] = (len(payload), relative)
            envelope = _stripped_stage_info(data, chart_id=chart_id)
            envelope_json = stable_json(envelope)
            stage_rows[chart_id] = (
                0,
                envelope_json,
                sha256_bytes(envelope_json.encode("utf-8")),
            )

            try:
                parsed = parser(payload)
                raw_parse_status = "parsed"
            except OdinParseError as exc:
                reason = "unsupported-serialized-structure"
                raw_parse_status = "unsupported"
                rows.append(
                    {
                        "chart_id": chart_id,
                        "song_id": song_id,
                        "difficulty_id": index_row.get("difficulty_id"),
                        "status": "failed",
                        "reason": reason,
                        "payload_sha256": payload_sha256,
                        "source_path": source,
                        "container_path": identity["container_path"],
                        "path_id": identity["path_id"],
                        "object_type": identity["object_type"],
                        "index_row": _mapping_copy(index_row),
                        "raw_parse_status": raw_parse_status,
                        "grouping_status": grouping_status,
                        "canonical_status": canonical_status,
                        "raw_record_count": None,
                        "record_group_count": None,
                        "logical_event_count": None,
                        "sentinel_count": None,
                        "validation": _mapping_copy(reports[chart_id]) if chart_id in reports else None,
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            except Exception as exc:
                raise ChartStoreError(f"chart {chart_id} Odin parse failed: {exc}") from exc

            try:
                experimental = experimental_builder(
                    candidate,
                    parsed,
                    payload_sha256=payload_sha256,
                    stage_info_raw=data,
                    note_configs_by_uid=note_configs_by_uid,
                    note_data_provenance=note_data_provenance,
                )
                grouping_status = "grouped"
            except FirstChartProjectionError as exc:
                reason = "grouping-invariants-not-satisfied"
                grouping_status = "uncertain"
                status = "failed"
                experimental = None
                error_type = type(exc).__name__
            except Exception as exc:
                raise ChartStoreError(f"chart {chart_id} grouping failed: {exc}") from exc
            else:
                raw_count = _required_integer(
                    experimental.get("raw_record_count"),
                    context=f"chart {chart_id} raw record count",
                    minimum=0,
                )
                group_count = _required_integer(
                    experimental.get("record_group_count"),
                    context=f"chart {chart_id} record group count",
                    minimum=0,
                )
                event_count = _required_integer(
                    experimental.get("logical_object_count"),
                    context=f"chart {chart_id} logical event count",
                    minimum=0,
                )
                grouping = _required_mapping(
                    experimental.get("grouping"), context=f"chart {chart_id} grouping"
                )
                sentinel_count = _required_integer(
                    grouping.get("observed_sentinel_count"),
                    context=f"chart {chart_id} sentinel count",
                    minimum=0,
                )
                note_data = _required_mapping(
                    experimental.get("note_data"), context=f"chart {chart_id} note data"
                )
                configs = _required_mapping(
                    note_data.get("configs_by_uid_raw"),
                    context=f"chart {chart_id} used note configs",
                )
                note_uid_refs[chart_id] = sorted(
                    (str(uid) for uid in configs), key=lambda value: (value.casefold(), value)
                )
                if song_id is None:
                    status = "uncertain"
                    reason = "song-identity-unresolved"
                    canonical_status = "not-attempted"
                else:
                    status = "success"
                    reason = None
                    canonical_status = "lazy-canonicalization-available"
                error_type = None

            row = {
                "chart_id": chart_id,
                "song_id": song_id,
                "difficulty_id": index_row.get("difficulty_id"),
                "status": status,
                "reason": reason,
                "payload_sha256": payload_sha256,
                "source_path": source,
                "container_path": identity["container_path"],
                "path_id": identity["path_id"],
                "object_type": identity["object_type"],
                "index_row": _mapping_copy(index_row),
                "raw_parse_status": raw_parse_status,
                "grouping_status": grouping_status,
                "canonical_status": canonical_status,
                "raw_record_count": raw_count,
                "record_group_count": group_count,
                "logical_event_count": event_count,
                "sentinel_count": sentinel_count,
                "validation": _mapping_copy(reports[chart_id]) if chart_id in reports else None,
            }
            if error_type is not None:
                row["error_type"] = error_type
            rows.append(row)

        if progress is not None:
            progress(source_number, len(ordered_sources), source)

    rows.sort(key=lambda value: (value["chart_id"].casefold(), value["chart_id"]))
    if len(rows) != len(candidates) or {row["chart_id"] for row in rows} != set(identities):
        raise ChartStoreError("not every candidate was classified exactly once")

    raw_total = sum(
        int(row["raw_record_count"])
        for row in rows
        if isinstance(row.get("raw_record_count"), int)
    )
    event_total = sum(
        int(row["logical_event_count"])
        for row in rows
        if isinstance(row.get("logical_event_count"), int)
    )
    sentinel_total = sum(
        int(row["sentinel_count"])
        for row in rows
        if isinstance(row.get("sentinel_count"), int)
    )
    if raw_total != census["parsed_raw_record_count"]:
        raise ChartStoreError(
            "grouping census raw record count differs from the strict current parse"
        )
    if event_total != census["grouped_logical_object_count"]:
        raise ChartStoreError(
            "grouping census logical object count differs from the strict current parse"
        )

    expected_payload_paths = {relative for _size, relative in payload_rows.values()}
    extra_payload_paths = _existing_payload_files(output) - expected_payload_paths
    if extra_payload_paths:
        sample = sorted(extra_payload_paths, key=lambda value: (value.casefold(), value))[0]
        raise ChartStoreError(
            "store contains an unindexed payload from another resource set: "
            f"{sample}; run audit-store and use a clean Store directory"
        )

    connection = _open_build_database(database_path)
    try:
        with connection:
            catalog = _required_mapping(song_chart_index.get("catalog"), context="song index catalog")
            catalog_source = _required_mapping(
                catalog.get("source"), context="song index catalog source"
            )
            metadata = {
                "store_schema_version": STORE_SCHEMA_VERSION,
                "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
                "extractor_version": extractor_version,
                "parser_family": parser_family,
                "parser_version": parser_version,
                "inventory_fingerprint": index_fingerprint,
                "addressables_version": catalog.get("addressables_version"),
                "build_result_hash": catalog.get("build_result_hash"),
                "catalog_sha256": catalog_source.get("catalog_sha256"),
                "settings_sha256": catalog_source.get("settings_sha256"),
                "catalog": _mapping_copy(catalog),
                "note_data_provenance": _mapping_copy(note_data_provenance),
                "grouping_census": _mapping_copy(census),
            }
            connection.executemany(
                "INSERT INTO metadata(key, value_json) VALUES (?, ?)", metadata_rows(metadata)
            )
            for source in ordered_sources:
                identity = identities[by_source[source][0]]
                connection.execute(
                    "INSERT INTO sources(relative_path, path_casefold, byte_count, sha256) "
                    "VALUES (?, ?, ?, ?)",
                    (source, source.casefold(), identity["source_size"], identity["source_sha256"]),
                )
            for payload_sha256 in sorted(payload_rows):
                byte_count, relative = payload_rows[payload_sha256]
                connection.execute(
                    "INSERT INTO payloads(sha256, byte_count, relative_path, path_casefold) "
                    "VALUES (?, ?, ?, ?)",
                    (payload_sha256, byte_count, relative, relative.casefold()),
                )
            for chart_id in sorted(identities, key=lambda value: (value.casefold(), value)):
                rendered = stable_json(candidate_by_id[chart_id])
                connection.execute(
                    "INSERT INTO candidates(chart_id, chart_id_casefold, candidate_json, "
                    "candidate_sha256) VALUES (?, ?, ?, ?)",
                    (chart_id, chart_id.casefold(), rendered, sha256_bytes(rendered.encode("utf-8"))),
                )
            _insert_song_rows(connection, song_chart_index)
            stored_note_configs: dict[str, Sequence[Mapping[str, Any]]] = dict(
                note_configs_by_uid
            )
            for referenced_uids in note_uid_refs.values():
                for uid in referenced_uids:
                    stored_note_configs.setdefault(uid, ())
            note_uid_count, note_config_row_count = _insert_note_config_rows(
                connection, stored_note_configs
            )
            for row in rows:
                connection.execute(
                    "INSERT INTO charts(chart_id, song_id, difficulty_id, status, reason, "
                    "payload_sha256, source_path, container_path, path_id, object_type, "
                    "index_row_json, raw_parse_status, grouping_status, canonical_status, "
                    "raw_record_count, record_group_count, logical_event_count, sentinel_count, "
                    "validation_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["chart_id"], row["song_id"], row["difficulty_id"], row["status"],
                        row["reason"], row["payload_sha256"], row["source_path"],
                        row["container_path"], row["path_id"], row["object_type"],
                        stable_json(row["index_row"]), row["raw_parse_status"],
                        row["grouping_status"], row["canonical_status"],
                        row["raw_record_count"], row["record_group_count"],
                        row["logical_event_count"], row["sentinel_count"],
                        stable_json(row["validation"]) if row["validation"] is not None else None,
                    ),
                )
                stage_row = stage_rows.get(row["chart_id"])
                if stage_row is not None:
                    connection.execute(
                        "INSERT INTO stage_info(chart_id, serialized_format, envelope_json, "
                        "envelope_sha256) VALUES (?, ?, ?, ?)",
                        (row["chart_id"], *stage_row),
                    )
                for uid in note_uid_refs.get(row["chart_id"], ()):
                    connection.execute(
                        "INSERT INTO chart_note_uids(chart_id, uid) VALUES (?, ?)",
                        (row["chart_id"], uid),
                    )
            logical_digest = compute_logical_digest(connection)
            connection.execute(
                "INSERT INTO metadata(key, value_json) VALUES (?, ?)",
                ("logical_store_digest", stable_json(logical_digest)),
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise ChartStoreError(f"new store SQLite integrity check failed: {integrity!r}")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ChartStoreError("new store SQLite foreign key check failed")
    finally:
        connection.close()

    status_counts = Counter(str(row["status"]) for row in rows)
    # Invalidate any older manifest before changing its referenced payload/index set.
    manifest_path = output / STORE_MANIFEST_NAME
    if manifest_path.exists():
        if path_is_link(manifest_path) or not manifest_path.is_file():
            raise ChartStoreError(f"existing store manifest is unsafe: {manifest_path}")
        manifest_path.unlink()
    for payload_sha256, (byte_count, relative) in sorted(payload_rows.items()):
        final_path = contained_path(output, relative, context="payload path")
        if final_path.exists():
            _verify_existing_payload(
                final_path, expected_sha256=payload_sha256, expected_size=byte_count
            )
            continue
        staged_path = contained_path(staging, relative, context="staged payload path")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, final_path)
    final_index = output / STORE_INDEX_NAME
    os.replace(database_path, final_index)
    index_size, index_sha256, _prefix = fingerprint_file(final_index)
    manifest = {
        "store_schema_version": STORE_SCHEMA_VERSION,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "status": "complete-with-classified-outcomes",
        "complete": True,
        "inventory_fingerprint": index_fingerprint,
        "extractor_version": extractor_version,
        "parser_family": parser_family,
        "parser_version": parser_version,
        "addressables": {
            "version": song_chart_index["catalog"].get("addressables_version"),
            "build_result_hash": song_chart_index["catalog"].get("build_result_hash"),
            "catalog_sha256": song_chart_index["catalog"]["source"].get("catalog_sha256"),
            "settings_sha256": song_chart_index["catalog"]["source"].get("settings_sha256"),
        },
        "logical_store_digest": logical_digest,
        "index": {
            "relative_path": STORE_INDEX_NAME,
            "byte_count": index_size,
            "sha256": index_sha256,
        },
        "candidate_count": len(candidates),
        "source_count": len(ordered_sources),
        "payload_count": len(payload_rows),
        "payload_byte_count": sum(value[0] for value in payload_rows.values()),
        "song_count": len(song_chart_index.get("songs", ())),
        "note_uid_count": note_uid_count,
        "note_config_row_count": note_config_row_count,
        "raw_record_count": raw_total,
        "logical_event_count": event_total,
        "sentinel_count": sentinel_total,
        "status_counts": dict(sorted(status_counts.items())),
        "charts": [
            {
                "chart_id": row["chart_id"],
                "song_id": row["song_id"],
                "difficulty_id": row["difficulty_id"],
                "status": row["status"],
                "reason": row["reason"],
                "payload_sha256": row["payload_sha256"],
                "raw_record_count": row["raw_record_count"],
                "logical_event_count": row["logical_event_count"],
                "sentinel_count": row["sentinel_count"],
            }
            for row in rows
        ],
        "phase_gate": {
            "all_candidates_classified": len(rows) == len(candidates),
            "all_candidate_payloads_stored": all(
                row["payload_sha256"] is not None for row in rows
            ),
            "all_payloads_strictly_parsed": all(
                row["raw_parse_status"] == "parsed" for row in rows
            ),
            "all_payloads_grouped": all(row["grouping_status"] == "grouped" for row in rows),
            "no_failed_charts": status_counts.get("failed", 0) == 0,
        },
        "copyright_boundary": (
            "local-only index and raw payloads from user-owned resources; "
            "do not commit or redistribute"
        ),
    }
    atomic_write_json(manifest_path, manifest)
    if building_marker.exists():
        building_marker.unlink()
    _remove_staging_tree(staging)
    return manifest


__all__ = ["extract_chart_store"]
