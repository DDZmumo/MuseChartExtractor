"""Lazy, read-only access to compact Odin chart stores."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..charts.canonicalize import canonicalize_chart
from ..discovery.first_chart import build_experimental_chart
from ..scanner import fingerprint_file
from ..unity.odin import parse_stage_info_payload
from .schema import (
    STORE_INDEX_NAME,
    STORE_MANIFEST_NAME,
    STORE_SCHEMA_VERSION,
    ChartNotFoundError,
    ChartStoreError,
    UnresolvedChartError,
    compute_logical_digest,
    contained_path,
    parse_json_object,
    path_is_link,
    payload_relative_path,
    read_metadata,
    reject_symlink_path,
    require_metadata_versions,
    require_sha256,
)


@dataclass(frozen=True, slots=True)
class ChartRef:
    """Metadata-only chart reference returned without parsing an Odin payload."""

    chart_id: str
    song_id: str | None
    difficulty_id: int | None
    status: str
    reason: str | None
    payload_sha256: str | None
    payload_byte_count: int | None
    source_bundle: str
    container_path: str
    path_id: int
    object_type: str
    raw_record_count: int | None
    logical_event_count: int | None
    sentinel_count: int | None


def _load_json_file(path: Path, *, context: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        value = json.loads(text)
    except UnicodeDecodeError as exc:
        raise ChartStoreError(f"{context} is not UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ChartStoreError(f"{context} is invalid JSON: {path}: {exc}") from exc
    except OSError as exc:
        raise ChartStoreError(f"cannot read {context}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ChartStoreError(f"{context} root must be an object: {path}")
    return value


def _json_array(value: str, *, context: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ChartStoreError(f"invalid {context} JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ChartStoreError(f"{context} JSON must be an array")
    return parsed


class ChartStore:
    """Read-only Store facade; payloads are parsed only by ``load_chart``."""

    def __init__(
        self,
        root: Path,
        connection: sqlite3.Connection,
        manifest: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> None:
        self.root = root
        self._connection = connection
        self.manifest = dict(manifest)
        self.metadata = dict(metadata)
        self._closed = False

    @classmethod
    def open(cls, path: str | Path) -> ChartStore:
        requested = Path(path).expanduser()
        if path_is_link(requested):
            raise ChartStoreError(
                f"store root is a symbolic link or junction: {requested}"
            )
        root = requested.resolve(strict=False)
        if not root.is_dir() or path_is_link(root):
            raise ChartStoreError(f"store root is not a regular directory: {root}")
        building = root / ".building"
        if building.exists():
            raise ChartStoreError(f"store build is incomplete: {root}")
        manifest_path = root / STORE_MANIFEST_NAME
        reject_symlink_path(root, manifest_path, context="store manifest")
        if not manifest_path.is_file():
            raise ChartStoreError(f"store manifest is missing: {manifest_path}")
        manifest = _load_json_file(manifest_path, context="store manifest")
        if manifest.get("store_schema_version") != STORE_SCHEMA_VERSION:
            raise ChartStoreError(
                f"unsupported store schema: {manifest.get('store_schema_version')!r}"
            )
        if manifest.get("complete") is not True:
            raise ChartStoreError("store manifest is not complete")
        index_value = manifest.get("index")
        if not isinstance(index_value, Mapping):
            raise ChartStoreError("store manifest has no index object")
        if index_value.get("relative_path") != STORE_INDEX_NAME:
            raise ChartStoreError("store manifest index path is not canonical")
        index_path = contained_path(root, STORE_INDEX_NAME, context="store index path")
        reject_symlink_path(root, index_path, context="store index")
        if not index_path.is_file():
            raise ChartStoreError(f"store index is missing: {index_path}")
        expected_size = index_value.get("byte_count")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int):
            raise ChartStoreError("store manifest index byte count is invalid")
        expected_sha256 = require_sha256(
            index_value.get("sha256"), context="store manifest index SHA-256"
        )
        actual_size, actual_sha256, _prefix = fingerprint_file(index_path)
        if actual_size != expected_size or actual_sha256 != expected_sha256:
            raise ChartStoreError("store index fingerprint differs from store manifest")
        try:
            connection = sqlite3.connect(f"{index_path.as_uri()}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise ChartStoreError(f"store SQLite integrity check failed: {integrity!r}")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise ChartStoreError("store SQLite foreign key check failed")
            metadata = read_metadata(connection)
            require_metadata_versions(metadata)
            logical_digest = compute_logical_digest(connection)
            expected_digest = require_sha256(
                manifest.get("logical_store_digest"),
                context="store manifest logical digest",
            )
            if metadata.get("logical_store_digest") != expected_digest:
                raise ChartStoreError("store metadata logical digest differs from manifest")
            if logical_digest != expected_digest:
                raise ChartStoreError("store logical rows differ from recorded digest")
            scalar_matches = {
                "canonical_schema_version": metadata.get(
                    "canonical_schema_version"
                ),
                "inventory_fingerprint": metadata.get("inventory_fingerprint"),
                "extractor_version": metadata.get("extractor_version"),
                "parser_family": metadata.get("parser_family"),
                "parser_version": metadata.get("parser_version"),
            }
            for field, expected in scalar_matches.items():
                if manifest.get(field) != expected:
                    raise ChartStoreError(
                        f"store manifest {field} differs from SQLite metadata"
                    )
            unsupported_statuses = connection.execute(
                "SELECT DISTINCT status FROM charts "
                "WHERE status NOT IN ('success', 'uncertain', 'failed')"
            ).fetchall()
            if unsupported_statuses:
                raise ChartStoreError("store contains an unsupported chart status")
        except Exception:
            if "connection" in locals():
                connection.close()
            raise
        return cls(root, connection, manifest, metadata)

    def __enter__(self) -> ChartStore:
        self._require_open()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise ChartStoreError("chart store is closed")

    def iter_charts(self) -> Iterator[ChartRef]:
        """Yield metadata-only references without opening or parsing payload files."""

        self._require_open()
        query = """
            SELECT c.chart_id, c.song_id, c.difficulty_id, c.status, c.reason,
                   c.payload_sha256, p.byte_count, c.source_path, c.container_path,
                   c.path_id, c.object_type, c.raw_record_count,
                   c.logical_event_count, c.sentinel_count
              FROM charts AS c
              LEFT JOIN payloads AS p ON p.sha256 = c.payload_sha256
             ORDER BY c.chart_id COLLATE NOCASE, c.chart_id
        """
        for row in self._connection.execute(query):
            yield ChartRef(*row)

    def _chart_row(self, chart_id: str) -> tuple[Any, ...]:
        self._require_open()
        if not isinstance(chart_id, str) or not chart_id:
            raise ChartStoreError("chart ID must be a non-empty string")
        row = self._connection.execute(
            """
            SELECT chart_id, song_id, difficulty_id, status, reason, payload_sha256,
                   source_path, container_path, path_id, object_type, index_row_json,
                   raw_parse_status, grouping_status, canonical_status,
                   raw_record_count, record_group_count, logical_event_count,
                   sentinel_count, validation_json
              FROM charts WHERE chart_id = ?
            """,
            (chart_id,),
        ).fetchone()
        if row is None:
            raise ChartNotFoundError(chart_id)
        return row

    def read_payload(self, chart_id: str) -> bytes:
        """Read exact Odin bytes after verifying canonical path, size, and SHA-256."""

        row = self._chart_row(chart_id)
        payload_sha256 = row[5]
        if not isinstance(payload_sha256, str):
            raise ChartStoreError(f"chart {chart_id!r} has no stored payload")
        payload_row = self._connection.execute(
            "SELECT byte_count, relative_path FROM payloads WHERE sha256 = ?",
            (payload_sha256,),
        ).fetchone()
        if payload_row is None:
            raise ChartStoreError(f"chart {chart_id!r} references a missing payload row")
        byte_count, relative_path = payload_row
        expected_relative = payload_relative_path(payload_sha256).as_posix()
        if relative_path != expected_relative:
            raise ChartStoreError(f"chart {chart_id!r} payload path is not canonical")
        destination = contained_path(self.root, relative_path, context="payload path")
        reject_symlink_path(self.root, destination, context="payload")
        if not destination.is_file():
            raise ChartStoreError(f"chart {chart_id!r} payload is missing")
        try:
            payload = destination.read_bytes()
        except OSError as exc:
            raise ChartStoreError(f"cannot read chart {chart_id!r} payload: {exc}") from exc
        if len(payload) != byte_count or require_sha256(
            payload_sha256, context=f"chart {chart_id} payload SHA-256"
        ) != hashlib.sha256(payload).hexdigest():
            raise ChartStoreError(f"chart {chart_id!r} payload fingerprint mismatch")
        return payload

    def load_chart(self, chart_id: str) -> dict[str, Any]:
        """Strictly parse one payload and lazily reconstruct Canonical schema 1.1."""

        row = self._chart_row(chart_id)
        song_id = row[1]
        status = row[3]
        if song_id is None or status == "uncertain":
            raise UnresolvedChartError(
                f"chart {chart_id!r} is stored but has no resolved song identity"
            )
        if status != "success":
            raise ChartStoreError(
                f"chart {chart_id!r} cannot be canonicalized from status {status!r}"
            )
        payload = self.read_payload(chart_id)
        try:
            parsed = parse_stage_info_payload(payload)
        except Exception as exc:
            raise ChartStoreError(f"chart {chart_id!r} Odin payload is invalid: {exc}") from exc
        candidate_row = self._connection.execute(
            "SELECT candidate_json FROM candidates WHERE chart_id = ?", (chart_id,)
        ).fetchone()
        stage_row = self._connection.execute(
            "SELECT envelope_json, envelope_sha256 FROM stage_info WHERE chart_id = ?",
            (chart_id,),
        ).fetchone()
        song_row = self._connection.execute(
            "SELECT song_json FROM songs WHERE song_id = ?", (song_id,)
        ).fetchone()
        if candidate_row is None or stage_row is None or song_row is None:
            raise ChartStoreError(f"chart {chart_id!r} reconstruction rows are incomplete")
        candidate = parse_json_object(candidate_row[0], context=f"chart {chart_id} candidate")
        envelope = parse_json_object(stage_row[0], context=f"chart {chart_id} StageInfo")
        if hashlib.sha256(stage_row[0].encode("utf-8")).hexdigest() != stage_row[1]:
            raise ChartStoreError(f"chart {chart_id!r} StageInfo envelope hash mismatch")
        serialization_value = envelope.get("serializationData")
        if not isinstance(serialization_value, Mapping):
            raise ChartStoreError(f"chart {chart_id!r} StageInfo has no serializationData")
        if "SerializedBytes" in serialization_value:
            raise ChartStoreError(
                f"chart {chart_id!r} StageInfo duplicates SerializedBytes in SQLite"
            )
        serialization = deepcopy(dict(serialization_value))
        serialization["SerializedBytes"] = list(payload)
        envelope["serializationData"] = serialization
        note_configs: dict[str, list[Mapping[str, Any]]] = {}
        for (uid,) in self._connection.execute(
            "SELECT uid FROM chart_note_uids WHERE chart_id = ? ORDER BY uid COLLATE NOCASE, uid",
            (chart_id,),
        ):
            note_row = self._connection.execute(
                "SELECT rows_json FROM note_configs WHERE uid = ?", (uid,)
            ).fetchone()
            if note_row is None:
                raise ChartStoreError(
                    f"chart {chart_id!r} references missing note config UID {uid!r}"
                )
            note_configs[uid] = _json_array(note_row[0], context=f"note config {uid}")
        experimental = build_experimental_chart(
            candidate,
            parsed,
            payload_sha256=row[5],
            stage_info_raw=envelope,
            note_configs_by_uid=note_configs,
            note_data_provenance=deepcopy(self.metadata["note_data_provenance"]),
        )
        song = parse_json_object(song_row[0], context=f"song {song_id}")
        validation = None
        if row[18] is not None:
            validation = parse_json_object(row[18], context=f"chart {chart_id} validation")
        canonical = canonicalize_chart(
            experimental,
            {"catalog": deepcopy(self.metadata["catalog"]), "songs": [song]},
            validation,
            extractor_version=str(self.metadata["extractor_version"]),
        )
        return canonical.to_dict()


__all__ = ["ChartRef", "ChartStore"]
