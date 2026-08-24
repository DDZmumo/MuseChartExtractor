"""Physical schema and deterministic helpers for compact chart stores."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from ..charts.models import CANONICAL_SCHEMA_VERSION
from ..scanner import ScannerError

STORE_SCHEMA_VERSION = "1.0.0"
STORE_PARSER_FAMILY = "sirenix-odin-binary-observed-stageinfo-subset"
STORE_PARSER_VERSION = "strict-stageinfo-v1"
STORE_MANIFEST_NAME = "store.json"
STORE_INDEX_NAME = "index.sqlite3"
PAYLOAD_ROOT = PurePosixPath("payloads", "sha256")

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LOGICAL_TABLES = (
    ("metadata", "key"),
    ("sources", "relative_path"),
    ("payloads", "sha256"),
    ("candidates", "chart_id"),
    ("songs", "song_id"),
    ("charts", "chart_id"),
    ("stage_info", "chart_id"),
    ("note_configs", "uid"),
    ("chart_note_uids", "chart_id, uid"),
)


class ChartStoreError(ScannerError):
    """Raised when a physical chart store is unsafe or inconsistent."""


class ChartNotFoundError(ChartStoreError, KeyError):
    """Raised when a requested chart ID is absent from the store."""


class UnresolvedChartError(ChartStoreError):
    """Raised when a stored chart has no canonical song identity."""


def path_is_link(path: Path) -> bool:
    """Return true for symbolic links and Windows directory junctions."""

    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction is not None and is_junction())


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json_object(value: str, *, context: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ChartStoreError(f"invalid {context} JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ChartStoreError(f"{context} JSON must be an object")
    return parsed


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_sha256(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ChartStoreError(f"{context} must be a lowercase SHA-256")
    return value


def payload_relative_path(payload_sha256: str) -> PurePosixPath:
    digest = require_sha256(payload_sha256, context="payload SHA-256")
    return PAYLOAD_ROOT / digest[:2] / f"{digest}.odin"


def contained_path(root: Path, relative_path: str, *, context: str) -> Path:
    if "\\" in relative_path:
        raise ChartStoreError(f"{context} must use portable forward slashes")
    portable = PurePosixPath(relative_path)
    if portable.is_absolute() or not portable.parts or ".." in portable.parts:
        raise ChartStoreError(f"{context} escapes the store: {relative_path!r}")
    unresolved = root.joinpath(*portable.parts)
    reject_symlink_path(root, unresolved, context=context)
    destination = unresolved.resolve(strict=False)
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ChartStoreError(f"{context} escapes the store: {relative_path!r}") from exc
    return destination


def reject_symlink_path(root: Path, path: Path, *, context: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ChartStoreError(f"{context} escapes the store") from exc
    current = root
    if path_is_link(current):
        raise ChartStoreError(f"{context} root is a symbolic link or junction")
    for part in relative.parts:
        current = current / part
        if path_is_link(current):
            raise ChartStoreError(
                f"{context} contains a symbolic link or junction: {current}"
            )


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_bytes(path, (stable_json(value) + "\n").encode("utf-8"))


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        PRAGMA application_id = 0x4d444353;
        PRAGMA user_version = 10000;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE sources (
            relative_path TEXT PRIMARY KEY,
            path_casefold TEXT NOT NULL UNIQUE,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            sha256 TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE payloads (
            sha256 TEXT PRIMARY KEY,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            relative_path TEXT NOT NULL UNIQUE,
            path_casefold TEXT NOT NULL UNIQUE
        ) WITHOUT ROWID;

        CREATE TABLE candidates (
            chart_id TEXT PRIMARY KEY,
            chart_id_casefold TEXT NOT NULL UNIQUE,
            candidate_json TEXT NOT NULL,
            candidate_sha256 TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE songs (
            song_id TEXT PRIMARY KEY,
            song_id_casefold TEXT NOT NULL UNIQUE,
            song_json TEXT NOT NULL,
            song_sha256 TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE charts (
            chart_id TEXT PRIMARY KEY REFERENCES candidates(chart_id),
            song_id TEXT REFERENCES songs(song_id),
            difficulty_id INTEGER,
            status TEXT NOT NULL CHECK (status IN ('success', 'uncertain', 'failed')),
            reason TEXT,
            payload_sha256 TEXT REFERENCES payloads(sha256),
            source_path TEXT NOT NULL REFERENCES sources(relative_path),
            container_path TEXT NOT NULL,
            path_id INTEGER NOT NULL,
            object_type TEXT NOT NULL,
            index_row_json TEXT NOT NULL,
            raw_parse_status TEXT NOT NULL,
            grouping_status TEXT NOT NULL,
            canonical_status TEXT NOT NULL,
            raw_record_count INTEGER,
            record_group_count INTEGER,
            logical_event_count INTEGER,
            sentinel_count INTEGER,
            validation_json TEXT
        ) WITHOUT ROWID;

        CREATE TABLE stage_info (
            chart_id TEXT PRIMARY KEY REFERENCES charts(chart_id),
            serialized_format INTEGER NOT NULL,
            envelope_json TEXT NOT NULL,
            envelope_sha256 TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE note_configs (
            uid TEXT PRIMARY KEY,
            rows_json TEXT NOT NULL,
            row_count INTEGER NOT NULL CHECK (row_count >= 0)
        ) WITHOUT ROWID;

        CREATE TABLE chart_note_uids (
            chart_id TEXT NOT NULL REFERENCES charts(chart_id),
            uid TEXT NOT NULL REFERENCES note_configs(uid),
            PRIMARY KEY (chart_id, uid)
        ) WITHOUT ROWID;

        CREATE INDEX charts_payload_idx ON charts(payload_sha256);
        CREATE INDEX charts_source_idx ON charts(source_path);
        CREATE INDEX charts_song_idx ON charts(song_id);
        """
    )


def metadata_rows(values: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [(key, stable_json(values[key])) for key in sorted(values)]


def read_metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("SELECT key, value_json FROM metadata ORDER BY key").fetchall()
    result: dict[str, Any] = {}
    for key, value_json in rows:
        try:
            result[str(key)] = json.loads(value_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ChartStoreError(f"metadata {key!r} contains invalid JSON") from exc
    return result


def compute_logical_digest(connection: sqlite3.Connection) -> str:
    """Hash stable logical rows, excluding the self-referential digest metadata."""

    digest = hashlib.sha256()
    for table, order_by in _LOGICAL_TABLES:
        digest.update(f"table:{table}\n".encode("ascii"))
        cursor = connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
        column_names = [description[0] for description in cursor.description]
        for row in cursor:
            values = dict(zip(column_names, row, strict=True))
            if table == "metadata" and values.get("key") == "logical_store_digest":
                continue
            digest.update(stable_json(values).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def require_metadata_versions(metadata: Mapping[str, Any]) -> None:
    expected = {
        "store_schema_version": STORE_SCHEMA_VERSION,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "parser_family": STORE_PARSER_FAMILY,
        "parser_version": STORE_PARSER_VERSION,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            label = key.replace("_", " ")
            raise ChartStoreError(
                f"unsupported {label}: expected {value!r}, "
                f"found {metadata.get(key)!r}"
            )


def require_sequence(value: Any, *, context: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ChartStoreError(f"{context} must be an array")
    return value
