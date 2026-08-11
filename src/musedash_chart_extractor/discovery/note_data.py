"""Read-only resolver for the exact Addressables ``notedata.json`` asset.

The Phase 2 bundle inventory is the authority for locating this asset.  This
module deliberately does not guess a bundle filename or pin a PathID: it
requires one resolved ``TextAsset`` entry at the observed container path,
verifies the source fingerprint, and then reads only that object with UnityPy.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import UnityPy

from ..scanner import ScannerError, fingerprint_file, validate_game_directory

NOTE_DATA_CONTAINER_PATH = "Assets/Static Resources/Data/Configs/others/notedata.json"
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


class NoteDataError(ScannerError):
    """Raised when the inventoried note-data asset cannot be verified or read."""


def _validated_match(
    report: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    source = report.get("source")
    if not isinstance(source, str) or not source:
        raise NoteDataError("matching bundle inventory row has no source")

    source_size = report.get("size")
    if (
        not isinstance(source_size, int)
        or isinstance(source_size, bool)
        or source_size < 0
    ):
        raise NoteDataError(
            f"matching bundle inventory row has invalid source size: {source!r}"
        )

    source_sha256 = report.get("sha256")
    if not isinstance(source_sha256, str) or _SHA256_PATTERN.fullmatch(source_sha256) is None:
        raise NoteDataError(
            f"matching bundle inventory row has invalid source SHA-256: {source!r}"
        )

    path_id = entry.get("path_id")
    if not isinstance(path_id, int) or isinstance(path_id, bool):
        raise NoteDataError(
            f"matching note-data container has invalid PathID in source {source!r}"
        )

    return {
        "source": source,
        "source_byte_count": source_size,
        "source_sha256": source_sha256.casefold(),
        "container_path": NOTE_DATA_CONTAINER_PATH,
        "path_id": path_id,
        "object_type": "TextAsset",
    }


def select_note_data_source(
    bundle_reports: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Select exactly one resolved TextAsset at the exact note-data container.

    Unparseable Phase 2 rows and unrelated container entries are ignored.  A
    duplicate match remains an error even when both entries name the same
    bundle: silently choosing one would discard ambiguity in the evidence.
    """

    matches: list[dict[str, Any]] = []
    for report in bundle_reports:
        if not isinstance(report, Mapping):
            raise NoteDataError("bundle inventory report is not an object")
        if report.get("parseable") is not True:
            continue

        entries = report.get("container_entries", [])
        if not isinstance(entries, list):
            raise NoteDataError("parseable bundle inventory row has invalid container_entries")
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if (
                entry.get("path") == NOTE_DATA_CONTAINER_PATH
                and entry.get("resolved") is True
                and entry.get("type") == "TextAsset"
            ):
                matches.append(_validated_match(report, entry))

    if len(matches) != 1:
        raise NoteDataError(
            "expected exactly one resolved TextAsset at container "
            f"{NOTE_DATA_CONTAINER_PATH!r}, found {len(matches)}"
        )
    return matches[0]


def _resolve_source_path(root: Path, relative_path: str) -> Path:
    candidate = (root / Path(relative_path)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NoteDataError(
            f"bundle inventory source escapes game directory: {relative_path}"
        ) from exc

    try:
        source_path = candidate.resolve(strict=True)
    except OSError as exc:
        raise NoteDataError(
            f"note-data source does not exist or cannot be resolved: {relative_path}: {exc}"
        ) from exc
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise NoteDataError(
            f"bundle inventory source escapes game directory: {relative_path}"
        ) from exc
    if not source_path.is_file():
        raise NoteDataError(f"note-data source is not a file: {relative_path}")
    return source_path


def _object_type_name(obj: Any) -> str:
    type_value = getattr(obj, "type", None)
    return str(getattr(type_value, "name", type_value))


def _read_text_asset(
    source_path: Path,
    *,
    relative_path: str,
    path_id: int,
    loader: Callable[[str], Any],
) -> str:
    try:
        environment = loader(str(source_path))
        targets = [
            obj for obj in environment.objects if int(obj.path_id) == path_id
        ]
    except Exception as exc:
        raise NoteDataError(
            f"cannot load note-data source {relative_path}: {type(exc).__name__}: {exc}"
        ) from exc

    if len(targets) != 1:
        raise NoteDataError(
            f"expected exactly one object for note-data PathID {path_id} in "
            f"{relative_path}, found {len(targets)}"
        )
    target = targets[0]
    object_type = _object_type_name(target)
    if object_type != "TextAsset":
        raise NoteDataError(
            f"note-data PathID {path_id} is not a TextAsset in {relative_path}: "
            f"found {object_type!r}"
        )

    try:
        content = target.read().m_Script
    except Exception as exc:
        raise NoteDataError(
            f"cannot read note-data TextAsset {relative_path} PathID {path_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(content, str):
        raise NoteDataError(
            f"note-data TextAsset content is not text: {relative_path} PathID {path_id}"
        )
    return content


def _index_rows(content: str) -> tuple[dict[str, list[dict[str, Any]]], int]:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise NoteDataError(
            f"invalid note-data JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(decoded, list):
        raise NoteDataError(
            f"note-data JSON root must be a list, found {type(decoded).__name__}"
        )

    by_uid: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(decoded):
        if not isinstance(row, dict):
            raise NoteDataError(
                f"note-data row {index} must be an object, found {type(row).__name__}"
            )
        uid = row.get("uid")
        if not isinstance(uid, str) or not uid.strip():
            raise NoteDataError(f"note-data row {index} has no non-empty string uid")
        by_uid.setdefault(uid, []).append(row)
    return by_uid, len(decoded)


def resolve_note_data(
    game_dir: str | Path,
    bundle_reports: Iterable[Mapping[str, Any]],
    *,
    loader: Callable[[str], Any] = UnityPy.load,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Verify and read the inventoried note-data TextAsset.

    Returns an index from each UID to *all* raw JSON rows carrying that UID and
    a provenance record.  Duplicate UIDs are intentional and never overwrite
    an earlier row.
    """

    root = validate_game_directory(game_dir)
    selected = select_note_data_source(bundle_reports)
    relative_path = selected["source"]
    source_path = _resolve_source_path(root, relative_path)

    source_size, source_sha256, _prefix = fingerprint_file(source_path)
    if (
        source_size != selected["source_byte_count"]
        or source_sha256 != selected["source_sha256"]
    ):
        raise NoteDataError(
            f"Phase 2 note-data source fingerprint is stale for {relative_path}: "
            f"expected {selected['source_sha256']} "
            f"({selected['source_byte_count']} bytes), found {source_sha256} "
            f"({source_size} bytes)"
        )

    content = _read_text_asset(
        source_path,
        relative_path=relative_path,
        path_id=selected["path_id"],
        loader=loader,
    )
    content_bytes = content.encode("utf-8")
    by_uid, row_count = _index_rows(content)
    provenance = {
        "schema_version": 1,
        **selected,
        "content_byte_count": len(content_bytes),
        "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
        "row_count": row_count,
        "uid_count": len(by_uid),
    }
    return by_uid, provenance
