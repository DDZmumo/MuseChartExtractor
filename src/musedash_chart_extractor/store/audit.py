"""Independent, metadata-only integrity audit for compact chart stores."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import UnityPy

from ..batch import _object_type_name, _payload_bytes
from ..discovery.first_chart import build_experimental_chart
from ..scanner import fingerprint_file, validate_game_directory
from ..unity.odin import parse_stage_info_payload
from .schema import (
    STORE_INDEX_NAME,
    STORE_MANIFEST_NAME,
    STORE_SCHEMA_VERSION,
    ChartStoreError,
    compute_logical_digest,
    contained_path,
    payload_relative_path,
    path_is_link,
    read_metadata,
    reject_symlink_path,
    require_metadata_versions,
    require_sha256,
    sha256_bytes,
    stable_json,
)

_MISMATCH_CATEGORIES = (
    "manifest_mismatches",
    "index_mismatches",
    "sqlite_integrity_mismatches",
    "foreign_key_mismatches",
    "logical_digest_mismatches",
    "id_set_mismatches",
    "row_hash_mismatches",
    "payload_set_mismatches",
    "payload_fingerprint_mismatches",
    "payload_parse_mismatches",
    "grouping_count_mismatches",
    "envelope_mismatches",
    "source_mismatches",
)
_MAX_SAMPLES = 10


class _Mismatches:
    def __init__(self) -> None:
        self.counts = {category: 0 for category in _MISMATCH_CATEGORIES}
        self.samples: dict[str, list[str]] = {
            category: [] for category in _MISMATCH_CATEGORIES
        }

    def add(self, category: str, message: str) -> None:
        self.counts[category] += 1
        if len(self.samples[category]) < _MAX_SAMPLES:
            self.samples[category].append(message)

    @property
    def failed(self) -> bool:
        return any(self.counts.values())


def _base_report(root: Path, mismatches: _Mismatches) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "store_schema_version": None,
        "canonical_schema_version": None,
        "inventory_fingerprint": None,
        "status": "failed",
        "store": {
            "root_name": root.name,
            "manifest": STORE_MANIFEST_NAME,
            "index": STORE_INDEX_NAME,
            "logical_store_digest": None,
            "index_byte_count": None,
            "index_sha256": None,
        },
        "counts": {
            "candidate_count": 0,
            "chart_count": 0,
            "source_count": 0,
            "payload_count": 0,
            "payload_byte_count": 0,
            "song_count": 0,
            "note_uid_count": 0,
            "note_config_row_count": 0,
            "raw_record_count": 0,
            "logical_event_count": 0,
            "sentinel_count": 0,
            "status_counts": {},
        },
        "sqlite": {
            "integrity_check": "not-run",
            "foreign_key_violation_count": 0,
        },
        "source_verification": {
            "requested": False,
            "verified_source_count": 0,
            "verified_chart_count": 0,
        },
        "mismatch_counts": mismatches.counts,
        "mismatch_samples": mismatches.samples,
        "copyright_boundary": (
            "metadata, hashes, counts, and bounded mismatch descriptions only; "
            "no chart events or payload bytes"
        ),
    }


def _finish(report: dict[str, Any], mismatches: _Mismatches) -> dict[str, Any]:
    report["status"] = "failed" if mismatches.failed else "passed"
    return report


def _load_manifest(path: Path, mismatches: _Mismatches) -> dict[str, Any] | None:
    if path_is_link(path):
        mismatches.add(
            "manifest_mismatches", "store manifest is a symbolic link or junction"
        )
        return None
    if not path.is_file():
        mismatches.add("manifest_mismatches", "store manifest is missing")
        return None
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        mismatches.add(
            "manifest_mismatches",
            f"store manifest cannot be decoded: {type(exc).__name__}",
        )
        return None
    if not isinstance(value, dict):
        mismatches.add("manifest_mismatches", "store manifest root is not an object")
        return None
    return value


def _parse_object(
    value: Any,
    *,
    category: str,
    context: str,
    mismatches: _Mismatches,
) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        mismatches.add(category, f"{context} is invalid JSON")
        return None
    if not isinstance(parsed, dict):
        mismatches.add(category, f"{context} is not a JSON object")
        return None
    return parsed


def _parse_array(
    value: Any,
    *,
    context: str,
    mismatches: _Mismatches,
) -> list[Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        mismatches.add("row_hash_mismatches", f"{context} is invalid JSON")
        return None
    if not isinstance(parsed, list):
        mismatches.add("row_hash_mismatches", f"{context} is not a JSON array")
        return None
    return parsed


def _integer_total(rows: list[sqlite3.Row], field: str) -> int:
    return sum(
        int(row[field])
        for row in rows
        if isinstance(row[field], int) and not isinstance(row[field], bool)
    )


def _compare_manifest(
    manifest: Mapping[str, Any],
    metadata: Mapping[str, Any],
    counts: Mapping[str, Any],
    chart_summaries: list[dict[str, Any]],
    phase_gate: Mapping[str, bool],
    mismatches: _Mismatches,
) -> None:
    expected_scalars = {
        "store_schema_version": STORE_SCHEMA_VERSION,
        "canonical_schema_version": metadata.get("canonical_schema_version"),
        "inventory_fingerprint": metadata.get("inventory_fingerprint"),
        "extractor_version": metadata.get("extractor_version"),
        "parser_family": metadata.get("parser_family"),
        "parser_version": metadata.get("parser_version"),
        "candidate_count": counts["candidate_count"],
        "source_count": counts["source_count"],
        "payload_count": counts["payload_count"],
        "payload_byte_count": counts["payload_byte_count"],
        "song_count": counts["song_count"],
        "note_uid_count": counts["note_uid_count"],
        "note_config_row_count": counts["note_config_row_count"],
        "raw_record_count": counts["raw_record_count"],
        "logical_event_count": counts["logical_event_count"],
        "sentinel_count": counts["sentinel_count"],
        "status_counts": counts["status_counts"],
    }
    for field, expected in expected_scalars.items():
        if manifest.get(field) != expected:
            mismatches.add(
                "manifest_mismatches",
                f"manifest {field} differs from audited value",
            )
    expected_addressables = {
        "version": metadata.get("addressables_version"),
        "build_result_hash": metadata.get("build_result_hash"),
        "catalog_sha256": metadata.get("catalog_sha256"),
        "settings_sha256": metadata.get("settings_sha256"),
    }
    if manifest.get("addressables") != expected_addressables:
        mismatches.add(
            "manifest_mismatches",
            "manifest addressables differs from SQLite metadata",
        )
    if manifest.get("complete") is not True:
        mismatches.add("manifest_mismatches", "manifest complete is not true")
    if manifest.get("status") != "complete-with-classified-outcomes":
        mismatches.add("manifest_mismatches", "manifest status is not complete")
    manifest_charts = manifest.get("charts")
    if not isinstance(manifest_charts, list):
        mismatches.add("manifest_mismatches", "manifest charts is not an array")
    else:
        expected_ids = [row["chart_id"] for row in chart_summaries]
        actual_ids = [
            row.get("chart_id") if isinstance(row, Mapping) else None
            for row in manifest_charts
        ]
        if Counter(actual_ids) != Counter(expected_ids):
            mismatches.add(
                "manifest_mismatches",
                "manifest charts ID collection differs from SQLite",
            )
        elif actual_ids != expected_ids:
            mismatches.add(
                "manifest_mismatches", "manifest charts order differs from SQLite"
            )
        elif manifest_charts != chart_summaries:
            mismatches.add(
                "manifest_mismatches", "manifest charts values differ from SQLite"
            )
    manifest_gate = manifest.get("phase_gate")
    if not isinstance(manifest_gate, Mapping):
        mismatches.add("manifest_mismatches", "manifest phase_gate is not an object")
    else:
        for field, expected in phase_gate.items():
            if manifest_gate.get(field) is not expected:
                mismatches.add(
                    "manifest_mismatches",
                    f"manifest phase_gate {field} differs from audited value",
                )
            if expected is not True:
                mismatches.add(
                    "manifest_mismatches",
                    f"audited phase_gate {field} is false",
                )


def _payload_files(root: Path, mismatches: _Mismatches) -> set[str]:
    payload_root = root / "payloads"
    if not payload_root.exists():
        return set()
    if path_is_link(payload_root) or not payload_root.is_dir():
        mismatches.add(
            "payload_set_mismatches", "payload root is not a regular directory"
        )
        return set()
    result: set[str] = set()
    for current, directory_names, file_names in os.walk(
        payload_root, followlinks=False
    ):
        current_path = Path(current)
        for name in list(directory_names):
            child = current_path / name
            if path_is_link(child):
                mismatches.add(
                    "payload_set_mismatches",
                    "payload tree contains symbolic link "
                    f"{child.relative_to(root).as_posix()}",
                )
                directory_names.remove(name)
        for name in file_names:
            child = current_path / name
            relative = child.relative_to(root).as_posix()
            if path_is_link(child):
                mismatches.add(
                    "payload_set_mismatches",
                    f"payload file is a symbolic link: {relative}",
                )
            result.add(relative)
    return result


def _note_configs_for_chart(
    connection: sqlite3.Connection,
    chart_id: str,
    mismatches: _Mismatches,
) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    for (uid,) in connection.execute(
        "SELECT uid FROM chart_note_uids WHERE chart_id = ? ORDER BY uid", (chart_id,)
    ):
        row = connection.execute(
            "SELECT rows_json FROM note_configs WHERE uid = ?", (uid,)
        ).fetchone()
        if row is None:
            mismatches.add(
                "foreign_key_mismatches",
                f"chart {chart_id!r} references missing note config UID {uid!r}",
            )
            result[str(uid)] = []
            continue
        values = _parse_array(
            row[0], context=f"note config {uid}", mismatches=mismatches
        )
        if values is None:
            result[str(uid)] = []
            continue
        mapped: list[Mapping[str, Any]] = []
        for position, value in enumerate(values):
            if not isinstance(value, Mapping):
                mismatches.add(
                    "row_hash_mismatches",
                    f"note config {uid} row {position} is not an object",
                )
            else:
                mapped.append(value)
        result[str(uid)] = mapped
    return result


def _audit_source_game(
    connection: sqlite3.Connection,
    chart_rows: list[sqlite3.Row],
    stage_by_chart: Mapping[str, tuple[str, str]],
    payload_paths: Mapping[str, Path],
    game_dir: str | Path,
    loader: Callable[[str], Any],
    report: dict[str, Any],
    mismatches: _Mismatches,
) -> None:
    verification = report["source_verification"]
    verification["requested"] = True
    try:
        game_root = validate_game_directory(game_dir)
    except Exception as exc:
        mismatches.add(
            "source_mismatches", f"game directory is invalid: {type(exc).__name__}"
        )
        return

    charts_by_source: dict[str, list[sqlite3.Row]] = {}
    for chart in chart_rows:
        charts_by_source.setdefault(str(chart["source_path"]), []).append(chart)
    source_rows = {
        str(row["relative_path"]): row
        for row in connection.execute(
            "SELECT relative_path, byte_count, sha256 FROM sources"
        ).fetchall()
    }
    for source in sorted(charts_by_source, key=lambda value: (value.casefold(), value)):
        source_row = source_rows.get(source)
        if source_row is None:
            continue
        try:
            source_path = contained_path(
                game_root, source, context="source bundle path"
            )
            if path_is_link(source_path) or not source_path.is_file():
                raise ChartStoreError("source bundle is not a regular file")
            size, digest, _prefix = fingerprint_file(source_path)
            if size != source_row["byte_count"] or digest != source_row["sha256"]:
                raise ChartStoreError("source bundle fingerprint mismatch")
            environment = loader(str(source_path))
            objects: dict[int, Any] = {}
            for obj in environment.objects:
                path_id = int(obj.path_id)
                if path_id in objects:
                    raise ChartStoreError(f"duplicate PathID {path_id}")
                objects[path_id] = obj
        except Exception as exc:
            mismatches.add(
                "source_mismatches",
                f"source {source!r} cannot be verified: {type(exc).__name__}",
            )
            continue
        verification["verified_source_count"] += 1

        for chart in charts_by_source[source]:
            chart_id = str(chart["chart_id"])
            try:
                obj = objects[int(chart["path_id"])]
                if _object_type_name(obj) != chart["object_type"]:
                    raise ChartStoreError("object type mismatch")
                data = obj.parse_as_dict()
                if not isinstance(data, Mapping) or data.get("m_Name") != chart_id:
                    raise ChartStoreError("StageInfo identity mismatch")
                serialization_value = data.get("serializationData")
                if not isinstance(serialization_value, Mapping):
                    raise ChartStoreError("serializationData is missing")
                serialization = deepcopy(dict(serialization_value))
                payload = _payload_bytes(serialization.pop("SerializedBytes", None))
                payload_sha = chart["payload_sha256"]
                if (
                    not isinstance(payload_sha, str)
                    or sha256_bytes(payload) != payload_sha
                ):
                    raise ChartStoreError("StageInfo payload SHA-256 mismatch")
                if payload_sha not in payload_paths:
                    raise ChartStoreError("StageInfo payload is not available in Store")
                envelope = deepcopy(dict(data))
                envelope["serializationData"] = serialization
                stage_row = stage_by_chart.get(chart_id)
                if stage_row is None or stable_json(envelope) != stage_row[0]:
                    raise ChartStoreError("StageInfo envelope differs from Store")
            except Exception as exc:
                mismatches.add(
                    "source_mismatches",
                    "chart "
                    f"{chart_id!r} source evidence mismatch: {type(exc).__name__}",
                )
                continue
            verification["verified_chart_count"] += 1


def audit_chart_store(
    store_dir: str | Path,
    *,
    game_dir: str | Path | None = None,
    loader: Callable[[str], Any] = UnityPy.load,
) -> dict[str, Any]:
    """Audit one Store without retaining chart events or payload bytes in the report."""

    requested = Path(store_dir).expanduser()
    if path_is_link(requested):
        raise ChartStoreError(
            f"store root is a symbolic link or junction: {requested}"
        )
    root = requested.resolve(strict=False)
    if not root.is_dir() or path_is_link(root):
        raise ChartStoreError(f"store root is not a regular directory: {root}")
    mismatches = _Mismatches()
    report = _base_report(root, mismatches)
    if (root / ".building").exists():
        mismatches.add("manifest_mismatches", "store build is incomplete")

    manifest = _load_manifest(root / STORE_MANIFEST_NAME, mismatches)
    if manifest is None:
        return _finish(report, mismatches)
    report["store_schema_version"] = manifest.get("store_schema_version")
    report["canonical_schema_version"] = manifest.get("canonical_schema_version")
    report["inventory_fingerprint"] = manifest.get("inventory_fingerprint")
    report["store"]["logical_store_digest"] = manifest.get("logical_store_digest")

    index_value = manifest.get("index")
    if not isinstance(index_value, Mapping):
        mismatches.add("index_mismatches", "manifest index is not an object")
        return _finish(report, mismatches)
    if index_value.get("relative_path") != STORE_INDEX_NAME:
        mismatches.add("index_mismatches", "manifest index path is not canonical")
    index_path = contained_path(root, STORE_INDEX_NAME, context="store index path")
    if path_is_link(index_path) or not index_path.is_file():
        mismatches.add("index_mismatches", "store index is not a regular file")
        return _finish(report, mismatches)
    actual_index_size, actual_index_sha, _prefix = fingerprint_file(index_path)
    report["store"]["index_byte_count"] = actual_index_size
    report["store"]["index_sha256"] = actual_index_sha
    if (
        index_value.get("byte_count") != actual_index_size
        or index_value.get("sha256") != actual_index_sha
    ):
        mismatches.add("index_mismatches", "index fingerprint differs from manifest")

    try:
        connection = sqlite3.connect(f"{index_path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as exc:
        mismatches.add(
            "sqlite_integrity_mismatches",
            f"cannot open Store SQLite index: {type(exc).__name__}",
        )
        return _finish(report, mismatches)

    try:
        try:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_text = [str(row[0]) for row in integrity_rows]
            if integrity_text == ["ok"]:
                report["sqlite"]["integrity_check"] = "ok"
            else:
                report["sqlite"]["integrity_check"] = "failed"
                for value in integrity_text or ["no result"]:
                    mismatches.add("sqlite_integrity_mismatches", value)
        except sqlite3.Error as exc:
            report["sqlite"]["integrity_check"] = "failed"
            mismatches.add(
                "sqlite_integrity_mismatches",
                f"SQLite integrity_check failed: {type(exc).__name__}",
            )

        try:
            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            report["sqlite"]["foreign_key_violation_count"] = len(foreign_key_rows)
            for row in foreign_key_rows:
                mismatches.add(
                    "foreign_key_mismatches",
                    f"foreign key violation in table {row[0]!r} row {row[1]!r}",
                )
        except sqlite3.Error as exc:
            mismatches.add(
                "foreign_key_mismatches",
                f"foreign_key_check failed: {type(exc).__name__}",
            )
        try:
            note_reference_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(chart_note_uids)"
            ).fetchall()
            has_note_uid_foreign_key = any(
                row["table"] == "note_configs"
                and row["from"] == "uid"
                and row["to"] == "uid"
                for row in note_reference_foreign_keys
            )
            if not has_note_uid_foreign_key:
                mismatches.add(
                    "foreign_key_mismatches",
                    "chart_note_uids.uid has no note_configs.uid foreign key",
                )
        except sqlite3.Error as exc:
            mismatches.add(
                "foreign_key_mismatches",
                f"cannot inspect chart note UID foreign key: {type(exc).__name__}",
            )

        try:
            metadata = read_metadata(connection)
            require_metadata_versions(metadata)
            actual_logical_digest = compute_logical_digest(connection)
        except Exception as exc:
            mismatches.add(
                "logical_digest_mismatches",
                f"cannot verify logical Store rows: {type(exc).__name__}",
            )
            return _finish(report, mismatches)
        recorded_digest = manifest.get("logical_store_digest")
        if metadata.get("logical_store_digest") != recorded_digest:
            mismatches.add(
                "logical_digest_mismatches",
                "SQLite logical digest differs from manifest",
            )
        if actual_logical_digest != recorded_digest:
            mismatches.add(
                "logical_digest_mismatches", "logical Store rows differ from digest"
            )

        try:
            candidate_rows = connection.execute(
                "SELECT chart_id, candidate_json, candidate_sha256 FROM candidates"
            ).fetchall()
            chart_rows = connection.execute("SELECT * FROM charts").fetchall()
            source_rows = connection.execute("SELECT * FROM sources").fetchall()
            payload_rows = connection.execute("SELECT * FROM payloads").fetchall()
            song_rows = connection.execute(
                "SELECT song_id, song_json, song_sha256 FROM songs"
            ).fetchall()
            stage_rows = connection.execute(
                "SELECT chart_id, serialized_format, envelope_json, envelope_sha256 "
                "FROM stage_info"
            ).fetchall()
            note_rows = connection.execute(
                "SELECT uid, rows_json, row_count FROM note_configs"
            ).fetchall()
        except sqlite3.Error as exc:
            mismatches.add(
                "sqlite_integrity_mismatches",
                f"required Store table cannot be read: {type(exc).__name__}",
            )
            return _finish(report, mismatches)

        candidate_ids = {str(row["chart_id"]) for row in candidate_rows}
        chart_ids = {str(row["chart_id"]) for row in chart_rows}
        payload_ids = {str(row["sha256"]) for row in payload_rows}
        chart_payload_ids = {
            str(row["payload_sha256"])
            for row in chart_rows
            if isinstance(row["payload_sha256"], str)
        }
        source_ids = {str(row["relative_path"]) for row in source_rows}
        chart_source_ids = {str(row["source_path"]) for row in chart_rows}
        source_by_path = {
            str(row["relative_path"]): row for row in source_rows
        }
        candidate_sources: set[str] = set()
        candidate_by_chart: dict[str, dict[str, Any]] = {}
        for row in candidate_rows:
            chart_id = str(row["chart_id"])
            candidate = _parse_object(
                row["candidate_json"],
                category="row_hash_mismatches",
                context=f"candidate {chart_id}",
                mismatches=mismatches,
            )
            if sha256_bytes(str(row["candidate_json"]).encode("utf-8")) != row[
                "candidate_sha256"
            ]:
                mismatches.add(
                    "row_hash_mismatches", f"candidate {chart_id!r} hash mismatch"
                )
            if candidate is not None:
                candidate_by_chart[chart_id] = candidate
                if isinstance(candidate.get("source"), str):
                    source = candidate["source"]
                    candidate_sources.add(source)
                    source_row = source_by_path.get(source)
                    if source_row is None or (
                        candidate.get("source_size") != source_row["byte_count"]
                        or candidate.get("source_sha256") != source_row["sha256"]
                    ):
                        mismatches.add(
                            "source_mismatches",
                            f"candidate {chart_id!r} source fingerprint differs "
                            "from sources row",
                        )
        if candidate_ids != chart_ids:
            mismatches.add(
                "id_set_mismatches", "candidate and chart ID sets differ"
            )
        if payload_ids != chart_payload_ids:
            mismatches.add(
                "id_set_mismatches", "payload and chart payload-reference sets differ"
            )
        if source_ids != chart_source_ids or source_ids != candidate_sources:
            mismatches.add(
                "id_set_mismatches", "candidate, chart, and source ID sets differ"
            )

        stage_by_chart: dict[str, tuple[str, str]] = {}
        stage_format_by_chart: dict[str, Any] = {}
        stage_ids: set[str] = set()
        for row in stage_rows:
            chart_id = str(row["chart_id"])
            stage_ids.add(chart_id)
            envelope_json = str(row["envelope_json"])
            envelope_hash = str(row["envelope_sha256"])
            stage_by_chart[chart_id] = (envelope_json, envelope_hash)
            stage_format_by_chart[chart_id] = row["serialized_format"]
            if sha256_bytes(envelope_json.encode("utf-8")) != envelope_hash:
                mismatches.add(
                    "row_hash_mismatches",
                    f"chart {chart_id!r} StageInfo envelope hash mismatch",
                )
        payload_chart_ids = {
            str(row["chart_id"])
            for row in chart_rows
            if isinstance(row["payload_sha256"], str)
        }
        if stage_ids != payload_chart_ids:
            mismatches.add(
                "id_set_mismatches",
                "StageInfo and payload-bearing chart ID sets differ",
            )

        for row in song_rows:
            if (
                sha256_bytes(str(row["song_json"]).encode("utf-8"))
                != row["song_sha256"]
            ):
                mismatches.add(
                    "row_hash_mismatches", f"song {row['song_id']!r} hash mismatch"
                )
            _parse_object(
                row["song_json"],
                category="row_hash_mismatches",
                context=f"song {row['song_id']}",
                mismatches=mismatches,
            )

        note_config_row_count = 0
        for row in note_rows:
            values = _parse_array(
                row["rows_json"],
                context=f"note config {row['uid']}",
                mismatches=mismatches,
            )
            if values is not None:
                note_config_row_count += len(values)
                if row["row_count"] != len(values):
                    mismatches.add(
                        "row_hash_mismatches",
                        f"note config {row['uid']!r} row count mismatch",
                    )

        statuses = Counter(str(row["status"]) for row in chart_rows)
        unsupported_statuses = sorted(
            set(statuses) - {"success", "uncertain", "failed"}
        )
        for status in unsupported_statuses:
            mismatches.add(
                "manifest_mismatches",
                f"unsupported chart status in SQLite: {status!r}",
            )
        counts = {
            "candidate_count": len(candidate_rows),
            "chart_count": len(chart_rows),
            "source_count": len(source_rows),
            "payload_count": len(payload_rows),
            "payload_byte_count": _integer_total(payload_rows, "byte_count"),
            "song_count": len(song_rows),
            "note_uid_count": len(note_rows),
            "note_config_row_count": note_config_row_count,
            "raw_record_count": _integer_total(chart_rows, "raw_record_count"),
            "logical_event_count": _integer_total(chart_rows, "logical_event_count"),
            "sentinel_count": _integer_total(chart_rows, "sentinel_count"),
            "status_counts": dict(sorted(statuses.items())),
        }
        report["counts"] = counts
        census = metadata.get("grouping_census")
        if not isinstance(census, Mapping):
            mismatches.add(
                "grouping_count_mismatches",
                "grouping census metadata is not an object",
            )
        else:
            expected_census_counts = {
                "parsed_raw_record_count": counts["raw_record_count"],
                "grouped_logical_object_count": counts["logical_event_count"],
            }
            for field, expected in expected_census_counts.items():
                if census.get(field) != expected:
                    mismatches.add(
                        "grouping_count_mismatches",
                        f"grouping census {field} differs from audited chart totals",
                    )
        audited_phase_gate = {
            "all_candidates_classified": candidate_ids == chart_ids,
            "all_candidate_payloads_stored": all(
                isinstance(row["payload_sha256"], str) for row in chart_rows
            ),
            "all_payloads_strictly_parsed": all(
                row["raw_parse_status"] == "parsed" for row in chart_rows
            ),
            "all_payloads_grouped": all(
                row["grouping_status"] == "grouped" for row in chart_rows
            ),
            "no_failed_charts": statuses.get("failed", 0) == 0,
        }
        chart_summaries = [
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
            for row in sorted(
                chart_rows,
                key=lambda value: (
                    str(value["chart_id"]).casefold(),
                    str(value["chart_id"]),
                ),
            )
        ]
        _compare_manifest(
            manifest,
            metadata,
            counts,
            chart_summaries,
            audited_phase_gate,
            mismatches,
        )

        note_provenance = metadata.get("note_data_provenance")
        if not isinstance(note_provenance, Mapping):
            mismatches.add(
                "row_hash_mismatches", "note_data_provenance metadata is not an object"
            )
            note_provenance = {}

        envelope_by_chart: dict[str, dict[str, Any]] = {}
        for chart in chart_rows:
            chart_id = str(chart["chart_id"])
            stage_row = stage_by_chart.get(chart_id)
            if stage_row is None:
                continue
            envelope = _parse_object(
                stage_row[0],
                category="envelope_mismatches",
                context=f"chart {chart_id} StageInfo envelope",
                mismatches=mismatches,
            )
            if envelope is None:
                continue
            serialization = envelope.get("serializationData")
            if not isinstance(serialization, Mapping):
                mismatches.add(
                    "envelope_mismatches",
                    f"chart {chart_id!r} envelope has no serializationData",
                )
                continue
            if "SerializedBytes" in serialization:
                mismatches.add(
                    "envelope_mismatches",
                    f"chart {chart_id!r} envelope duplicates SerializedBytes",
                )
            if serialization.get("SerializedFormat") != 0:
                mismatches.add(
                    "envelope_mismatches",
                    f"chart {chart_id!r} envelope SerializedFormat is not zero",
                )
            if serialization.get("SerializedFormat") != stage_format_by_chart.get(
                chart_id
            ):
                mismatches.add(
                    "envelope_mismatches",
                    f"chart {chart_id!r} serialized format column differs "
                    "from envelope",
                )
            if "sceneEvents" not in envelope or not isinstance(
                envelope["sceneEvents"], list
            ):
                mismatches.add(
                    "envelope_mismatches",
                    f"chart {chart_id!r} envelope has no sceneEvents array",
                )
            envelope_by_chart[chart_id] = envelope

        payload_row_by_sha = {
            str(row["sha256"]): row for row in payload_rows
        }
        charts_by_payload: dict[str, list[sqlite3.Row]] = {}
        for chart in chart_rows:
            chart_id = str(chart["chart_id"])
            payload_sha = chart["payload_sha256"]
            if not isinstance(payload_sha, str):
                continue
            charts_by_payload.setdefault(payload_sha, []).append(chart)
            candidate = candidate_by_chart.get(chart_id)
            structure = candidate.get("structure") if candidate is not None else None
            payload_row = payload_row_by_sha.get(payload_sha)
            if not isinstance(structure, Mapping) or payload_row is None or (
                structure.get("serialized_payload_byte_count")
                != payload_row["byte_count"]
                or structure.get("serialized_payload_sha256") != payload_sha
            ):
                mismatches.add(
                    "payload_fingerprint_mismatches",
                    f"chart {chart_id!r} payload differs from candidate evidence",
                )

        expected_payload_paths: set[str] = set()
        payload_paths: dict[str, Path] = {}
        for row in payload_rows:
            payload_sha = row["sha256"]
            relative = str(row["relative_path"])
            try:
                digest = require_sha256(payload_sha, context="payload row SHA-256")
                canonical_relative = payload_relative_path(digest).as_posix()
            except ChartStoreError as exc:
                mismatches.add(
                    "payload_fingerprint_mismatches", str(exc)
                )
                continue
            expected_payload_paths.add(relative)
            if (
                relative != canonical_relative
                or row["path_casefold"] != relative.casefold()
            ):
                mismatches.add(
                    "payload_fingerprint_mismatches",
                    f"payload {digest} path is not canonical",
                )
            try:
                destination = contained_path(root, relative, context="payload path")
                reject_symlink_path(
                    root,
                    root.joinpath(*relative.split("/")),
                    context="payload path",
                )
            except ChartStoreError as exc:
                mismatches.add("payload_set_mismatches", str(exc))
                continue
            if path_is_link(destination) or not destination.is_file():
                mismatches.add(
                    "payload_set_mismatches", f"payload {digest} is missing or unsafe"
                )
                continue
            try:
                content = destination.read_bytes()
            except OSError as exc:
                mismatches.add(
                    "payload_set_mismatches",
                    f"payload {digest} cannot be read: {type(exc).__name__}",
                )
                continue
            if len(content) != row["byte_count"] or sha256_bytes(content) != digest:
                mismatches.add(
                    "payload_fingerprint_mismatches",
                    f"payload {digest} size or SHA-256 mismatch",
                )
            else:
                payload_paths[digest] = destination
            try:
                parsed = parse_stage_info_payload(content)
                if parsed.get("consumed_byte_count") != len(content):
                    raise ChartStoreError("parser did not consume payload to EOF")
            except Exception as exc:
                mismatches.add(
                    "payload_parse_mismatches",
                    f"payload {digest} strict parse failed: {type(exc).__name__}",
                )
                continue
            for chart in charts_by_payload.get(digest, ()):
                chart_id = str(chart["chart_id"])
                candidate = candidate_by_chart.get(chart_id)
                envelope = envelope_by_chart.get(chart_id)
                if candidate is None or envelope is None:
                    continue
                try:
                    experimental = build_experimental_chart(
                        candidate,
                        parsed,
                        payload_sha256=digest,
                        stage_info_raw=envelope,
                        note_configs_by_uid=_note_configs_for_chart(
                            connection, chart_id, mismatches
                        ),
                        note_data_provenance=note_provenance,
                    )
                except Exception as exc:
                    mismatches.add(
                        "grouping_count_mismatches",
                        f"chart {chart_id!r} cannot be grouped: {type(exc).__name__}",
                    )
                    continue
                grouping = experimental.get("grouping")
                observed_sentinel = (
                    grouping.get("observed_sentinel_count")
                    if isinstance(grouping, Mapping)
                    else None
                )
                expected_chart_counts = {
                    "raw_record_count": experimental.get("raw_record_count"),
                    "record_group_count": experimental.get("record_group_count"),
                    "logical_event_count": experimental.get("logical_object_count"),
                    "sentinel_count": observed_sentinel,
                }
                for field, expected in expected_chart_counts.items():
                    if chart[field] != expected:
                        mismatches.add(
                            "grouping_count_mismatches",
                            f"chart {chart_id!r} {field} differs from strict parse",
                        )
                del experimental
            del parsed
            del content

        actual_payload_paths = _payload_files(root, mismatches)
        for relative in sorted(expected_payload_paths - actual_payload_paths):
            mismatches.add(
                "payload_set_mismatches", f"indexed payload file is missing: {relative}"
            )
        for relative in sorted(actual_payload_paths - expected_payload_paths):
            mismatches.add(
                "payload_set_mismatches",
                f"extra payload file is not indexed: {relative}",
            )

        if game_dir is not None:
            _audit_source_game(
                connection,
                chart_rows,
                stage_by_chart,
                payload_paths,
                game_dir,
                loader,
                report,
                mismatches,
            )
    finally:
        connection.close()

    return _finish(report, mismatches)


__all__ = ["audit_chart_store"]
