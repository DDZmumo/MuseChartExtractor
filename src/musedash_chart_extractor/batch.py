"""Deterministic Phase 9 batch extraction from verified local resources."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import UnityPy

from . import __version__
from .charts.canonicalize import CanonicalizationError, canonicalize_chart
from .charts.models import CANONICAL_SCHEMA_VERSION
from .diagnostics import write_compact_json
from .discovery.first_chart import FirstChartProjectionError, build_experimental_chart
from .discovery.grouping_census import GROUPING_RULE_VERSION
from .scanner import ScannerError, fingerprint_file, validate_game_directory
from .unity.odin import OdinParseError, parse_stage_info_payload

BATCH_MANIFEST_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_WINDOWS_RESERVED_STEMS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class BatchExtractionError(ScannerError):
    """Raised when batch inputs or destinations cannot be used safely."""


def safe_path_component(value: str) -> str:
    """Encode one logical identifier as a separator-free portable component.

    Percent encoding is deterministic and avoids lossy character replacement.
    Windows device names and the two relative-path components need an explicit
    byte encoding because RFC 3986 always leaves ASCII dots unescaped.
    """

    if not isinstance(value, str) or not value:
        raise BatchExtractionError("output identifier must be a non-empty string")
    encoded = quote(value, safe="-_.~")
    stem = encoded.rstrip(" .").split(".", 1)[0].casefold()
    if encoded in {".", ".."} or stem in _WINDOWS_RESERVED_STEMS or encoded.endswith("."):
        encoded = "".join(f"%{byte:02X}" for byte in value.encode("utf-8"))
    if not encoded or encoded in {".", ".."} or "/" in encoded or "\\" in encoded:
        raise BatchExtractionError(f"identifier cannot form a safe path component: {value!r}")
    return encoded


def _required_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchExtractionError(f"{context} must be an object")
    return value


def _required_sequence(value: Any, *, context: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BatchExtractionError(f"{context} must be an array")
    return value


def _required_string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise BatchExtractionError(f"{context} must be a non-empty string")
    return value


def _required_integer(value: Any, *, context: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BatchExtractionError(f"{context} must be an integer")
    if minimum is not None and value < minimum:
        raise BatchExtractionError(f"{context} must be at least {minimum}")
    return value


def _required_sha256(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise BatchExtractionError(f"{context} must be a lowercase SHA-256")
    return value


def _source_path(root: Path, relative_path: str) -> Path:
    if "\\" in relative_path:
        raise BatchExtractionError(
            f"candidate source must use portable forward slashes: {relative_path}"
        )
    portable = PurePosixPath(relative_path)
    if portable.is_absolute() or ".." in portable.parts:
        raise BatchExtractionError(f"candidate source escapes game directory: {relative_path}")
    resolved = root.joinpath(*portable.parts).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BatchExtractionError(
            f"candidate source escapes game directory: {relative_path}"
        ) from exc
    if not resolved.is_file():
        raise BatchExtractionError(f"candidate source does not exist: {relative_path}")
    if root.joinpath(*portable.parts).is_symlink():
        raise BatchExtractionError(f"refusing symbolic-link candidate source: {relative_path}")
    return resolved


def _output_root(game_root: Path, output_dir: str | Path) -> Path:
    output = Path(output_dir).expanduser().resolve(strict=False)
    try:
        output.relative_to(game_root)
    except ValueError:
        pass
    else:
        raise BatchExtractionError("output directory must not be inside the game directory")
    return output


def _candidate_identity(candidate: Mapping[str, Any], *, position: int) -> dict[str, Any]:
    if candidate.get("status") != "unvalidated_candidate":
        raise BatchExtractionError(
            f"candidate {position} has unsupported status {candidate.get('status')!r}"
        )
    metadata = _required_mapping(candidate.get("metadata"), context=f"candidate {position} metadata")
    structure = _required_mapping(
        candidate.get("structure"), context=f"candidate {position} structure"
    )
    chart_id = _required_string(
        metadata.get("asset_name"), context=f"candidate {position} asset name"
    )
    source = _required_string(candidate.get("source"), context=f"candidate {chart_id} source")
    container_path = _required_string(
        candidate.get("container_path"), context=f"candidate {chart_id} container"
    )
    if PurePosixPath(container_path).stem != chart_id:
        raise BatchExtractionError(
            f"candidate {chart_id!r} does not match container basename {container_path!r}"
        )
    serialized_format = structure.get("serialized_format")
    if serialized_format != 0:
        raise BatchExtractionError(
            f"candidate {chart_id} has unsupported serialized format {serialized_format!r}"
        )
    return {
        "chart_id": chart_id,
        "inventory_fingerprint": _required_string(
            candidate.get("inventory_fingerprint"),
            context=f"candidate {chart_id} inventory fingerprint",
        ),
        "source": source,
        "source_size": _required_integer(
            candidate.get("source_size"), context=f"candidate {chart_id} source size", minimum=0
        ),
        "source_sha256": _required_sha256(
            candidate.get("source_sha256"), context=f"candidate {chart_id} source SHA-256"
        ),
        "container_path": container_path,
        "path_id": _required_integer(
            candidate.get("path_id"), context=f"candidate {chart_id} PathID"
        ),
        "object_type": _required_string(
            candidate.get("object_type"), context=f"candidate {chart_id} object type"
        ),
        "payload_byte_count": _required_integer(
            structure.get("serialized_payload_byte_count"),
            context=f"candidate {chart_id} payload byte count",
            minimum=0,
        ),
        "payload_sha256": _required_sha256(
            structure.get("serialized_payload_sha256"),
            context=f"candidate {chart_id} payload SHA-256",
        ),
    }


def _index_entries(
    song_chart_index: Mapping[str, Any],
) -> tuple[dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]], dict[str, Mapping[str, Any]]]:
    indexed: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    unresolved: dict[str, Mapping[str, Any]] = {}
    for song_value in _required_sequence(song_chart_index.get("songs"), context="song index songs"):
        song = _required_mapping(song_value, context="song index song")
        song_id = _required_string(song.get("song_id"), context="indexed song id")
        for chart_value in _required_sequence(song.get("charts"), context=f"song {song_id} charts"):
            chart = _required_mapping(chart_value, context=f"song {song_id} chart")
            chart_id = _required_string(chart.get("chart_id"), context=f"song {song_id} chart id")
            if chart_id in indexed or chart_id in unresolved:
                raise BatchExtractionError(f"duplicate chart id in song index: {chart_id!r}")
            indexed[chart_id] = (song, chart)
    for chart_value in _required_sequence(
        song_chart_index.get("unresolved_charts", ()), context="song index unresolved charts"
    ):
        chart = _required_mapping(chart_value, context="unresolved chart")
        chart_id = _required_string(chart.get("chart_id"), context="unresolved chart id")
        if chart_id in indexed or chart_id in unresolved:
            raise BatchExtractionError(f"duplicate chart id in song index: {chart_id!r}")
        unresolved[chart_id] = chart
    return indexed, unresolved


def _validated_grouping_census(
    value: Mapping[str, Any],
    *,
    inventory_fingerprint: str,
    candidate_count: int,
    source_count: int,
) -> dict[str, Any]:
    """Require the completed metadata-only gate that authorized batching."""

    summary = _required_mapping(value, context="grouping census summary")
    expected_scalars = {
        "schema_version": 1,
        "phase": 9,
        "status": "census-complete",
        "complete": True,
        "inventory_fingerprint": inventory_fingerprint,
        "grouping_rule_version": GROUPING_RULE_VERSION,
        "candidate_count": candidate_count,
        "source_count": source_count,
    }
    for field, expected in expected_scalars.items():
        actual = summary.get(field)
        if actual != expected:
            raise BatchExtractionError(
                f"grouping census {field} mismatch: expected {expected!r}, "
                f"found {actual!r}"
            )
    expected_status_count = {"parsed": candidate_count}
    if summary.get("raw_parse_status_counts") != expected_status_count:
        raise BatchExtractionError(
            "grouping census does not show every candidate as strictly parsed"
        )
    expected_group_count = {"grouped": candidate_count}
    if summary.get("grouping_status_counts") != expected_group_count:
        raise BatchExtractionError(
            "grouping census does not show every candidate as grouped"
        )
    return {
        "schema_version": 1,
        "status": "census-complete",
        "complete": True,
        "inventory_fingerprint": inventory_fingerprint,
        "grouping_rule_version": GROUPING_RULE_VERSION,
        "candidate_count": candidate_count,
        "source_count": source_count,
        "raw_parse_status_counts": expected_status_count,
        "grouping_status_counts": expected_group_count,
        "parsed_raw_record_count": _required_integer(
            summary.get("parsed_raw_record_count"),
            context="grouping census parsed raw record count",
            minimum=0,
        ),
        "grouped_logical_object_count": _required_integer(
            summary.get("grouped_logical_object_count"),
            context="grouping census logical object count",
            minimum=0,
        ),
    }


def _payload_bytes(value: Any) -> bytes:
    if not isinstance(value, list):
        raise ValueError("serializationData.SerializedBytes is not a list")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255
        for item in value
    ):
        raise ValueError("serializationData.SerializedBytes contains a non-byte value")
    return bytes(value)


def _object_type_name(obj: Any) -> str:
    value = getattr(getattr(obj, "type", None), "name", None)
    return str(value) if value is not None else ""


def _source_manifest(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bundle": identity["source"],
        "bundle_size": identity["source_size"],
        "bundle_sha256": identity["source_sha256"],
        "container_path": identity["container_path"],
        "path_id": identity["path_id"],
        "object_type": identity["object_type"],
        "payload_byte_count": identity["payload_byte_count"],
        "payload_sha256": identity["payload_sha256"],
    }


def _difficulty(index_row: Mapping[str, Any] | None, candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _required_mapping(candidate.get("metadata"), context="candidate metadata")
    if index_row is None:
        return {
            "id": None,
            "level_raw": None,
            "stage_info_raw": metadata.get("difficulty_raw"),
        }
    return {
        "id": index_row.get("difficulty_id"),
        "level_raw": index_row.get("difficulty_level_raw"),
        "stage_info_raw": index_row.get("difficulty_raw", metadata.get("difficulty_raw")),
    }


def _base_row(
    candidate: Mapping[str, Any],
    identity: Mapping[str, Any],
    index_row: Mapping[str, Any] | None,
    song_id: str | None,
) -> dict[str, Any]:
    warnings = [] if index_row is None else [str(value) for value in index_row.get("warnings", ())]
    return {
        "schema_version": BATCH_MANIFEST_SCHEMA_VERSION,
        "chart_id": identity["chart_id"],
        "song_id": song_id,
        "difficulty": _difficulty(index_row, candidate),
        "event_count": None,
        "status": "failed",
        "reason": None,
        "warnings": sorted(set(warnings), key=lambda value: (value.casefold(), value)),
        "source": _source_manifest(identity),
        "raw_parse_status": "not-attempted",
        "grouping_status": "not-attempted",
        "canonical_status": "not-attempted",
        "validation_status": str(candidate.get("validation_status", "unvalidated")),
        "output_path": None,
        "output_byte_count": None,
        "output_sha256": None,
    }


def _failure(row: dict[str, Any], *, reason: str, stage: str, exc: Exception | None = None) -> dict[str, Any]:
    row["status"] = "failed"
    row["reason"] = reason
    row["failure_stage"] = stage
    if exc is not None:
        row["error_type"] = type(exc).__name__
    return row


def _safe_chart_paths(
    candidates: Sequence[Mapping[str, Any]],
    indexed: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> dict[str, PurePosixPath]:
    result: dict[str, PurePosixPath] = {}
    occupied: dict[str, str] = {}
    for position, candidate in enumerate(candidates):
        identity = _candidate_identity(candidate, position=position)
        chart_id = identity["chart_id"]
        match = indexed.get(chart_id)
        if match is None:
            continue
        song_id = _required_string(match[0].get("song_id"), context=f"chart {chart_id} song id")
        relative = PurePosixPath(
            "charts", safe_path_component(song_id), f"{safe_path_component(chart_id)}.json"
        )
        folded = relative.as_posix().casefold()
        previous = occupied.get(folded)
        if previous is not None and previous != chart_id:
            raise BatchExtractionError(
                f"case-insensitive output collision between {previous!r} and {chart_id!r}"
            )
        occupied[folded] = chart_id
        result[chart_id] = relative
    return result


def _validate_index_candidate_provenance(
    identities: Mapping[str, Mapping[str, Any]],
    indexed: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
    unresolved: Mapping[str, Mapping[str, Any]],
) -> None:
    """Cross-check the independently recovered index against Phase 3 evidence."""

    for chart_id, identity in identities.items():
        index_row = indexed[chart_id][1] if chart_id in indexed else unresolved[chart_id]
        source = _required_mapping(
            index_row.get("source"), context=f"indexed chart {chart_id} source"
        )
        expected_source = {
            "bundle": identity["source"],
            "bundle_byte_count": identity["source_size"],
            "bundle_sha256": identity["source_sha256"],
            "container_path": identity["container_path"],
            "path_id": identity["path_id"],
            "object_type": identity["object_type"],
        }
        for field, expected in expected_source.items():
            if source.get(field) != expected:
                raise BatchExtractionError(
                    f"indexed chart {chart_id} source {field} differs from candidate evidence"
                )
        addressables = _required_mapping(
            index_row.get("addressables"),
            context=f"indexed chart {chart_id} Addressables evidence",
        )
        if addressables.get("primary_key") != chart_id:
            raise BatchExtractionError(
                f"indexed chart {chart_id} Addressables primary key differs"
            )
        if addressables.get("dependency_local_path") != identity["source"]:
            raise BatchExtractionError(
                f"indexed chart {chart_id} Addressables dependency differs from candidate source"
            )


def _relative_output_files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    if root.is_symlink() or not root.is_dir():
        raise BatchExtractionError(f"chart output root is not a regular directory: {root}")
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BatchExtractionError(f"refusing symbolic link in chart output tree: {path}")
        if path.is_file():
            files.add(path.relative_to(root.parent).as_posix())
    return files


def _chart_destination(output_root: Path, relative: PurePosixPath) -> Path:
    destination = output_root.joinpath(*relative.parts).resolve(strict=False)
    try:
        destination.relative_to(output_root)
    except ValueError as exc:
        raise BatchExtractionError(f"chart output escapes destination: {relative.as_posix()}") from exc
    return destination


def extract_all_charts(
    game_dir: str | Path,
    output_dir: str | Path,
    candidates: Sequence[Mapping[str, Any]],
    song_chart_index: Mapping[str, Any],
    *,
    grouping_census_summary: Mapping[str, Any],
    note_configs_by_uid: Mapping[str, Sequence[Mapping[str, Any]]],
    note_data_provenance: Mapping[str, Any],
    validation_reports: Mapping[str, Mapping[str, Any]] | None = None,
    expected_candidate_count: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    loader: Callable[[str], Any] = UnityPy.load,
    parser: Callable[[bytes], Mapping[str, Any]] = parse_stage_info_payload,
    experimental_builder: Callable[..., dict[str, Any]] = build_experimental_chart,
    canonicalizer: Callable[..., Any] = canonicalize_chart,
    chart_writer: Callable[[str | Path, Mapping[str, Any]], Path] = write_compact_json,
    extractor_version: str = __version__,
) -> dict[str, Any]:
    """Extract and classify every supplied candidate, then write manifest last.

    A source bundle is fingerprinted and loaded once per invocation. Failures
    after that boundary are retained as chart rows and do not abort siblings.
    Top-level evidence contradictions remain fatal because no trustworthy full
    manifest can be produced from an ambiguous candidate/index set.
    """

    root = validate_game_directory(game_dir)
    output = _output_root(root, output_dir)
    if not candidates:
        raise BatchExtractionError("batch candidate set is empty")
    if expected_candidate_count is not None and len(candidates) != expected_candidate_count:
        raise BatchExtractionError(
            f"expected {expected_candidate_count} candidates, found {len(candidates)}"
        )

    index_fingerprint = _required_string(
        song_chart_index.get("inventory_fingerprint"), context="song index inventory fingerprint"
    )
    identities: dict[str, dict[str, Any]] = {}
    candidate_by_id: dict[str, Mapping[str, Any]] = {}
    casefold_chart_ids: dict[str, str] = {}
    by_source: dict[str, list[str]] = defaultdict(list)
    source_casefolds: dict[str, str] = {}
    for position, candidate_value in enumerate(candidates):
        candidate = _required_mapping(candidate_value, context=f"candidate {position}")
        identity = _candidate_identity(candidate, position=position)
        chart_id = identity["chart_id"]
        if identity["inventory_fingerprint"] != index_fingerprint:
            raise BatchExtractionError(
                f"candidate {chart_id} inventory fingerprint differs from song index"
            )
        if chart_id in identities:
            raise BatchExtractionError(f"duplicate candidate chart id: {chart_id!r}")
        folded_chart = chart_id.casefold()
        if folded_chart in casefold_chart_ids:
            raise BatchExtractionError(
                f"case-insensitive candidate chart id collision: "
                f"{casefold_chart_ids[folded_chart]!r}, {chart_id!r}"
            )
        casefold_chart_ids[folded_chart] = chart_id
        folded_source = identity["source"].casefold()
        previous_source = source_casefolds.get(folded_source)
        if previous_source is not None and previous_source != identity["source"]:
            raise BatchExtractionError(
                f"case-insensitive candidate source collision: "
                f"{previous_source!r}, {identity['source']!r}"
            )
        source_casefolds[folded_source] = identity["source"]
        identities[chart_id] = identity
        candidate_by_id[chart_id] = candidate
        by_source[identity["source"]].append(chart_id)

    census_evidence = _validated_grouping_census(
        grouping_census_summary,
        inventory_fingerprint=index_fingerprint,
        candidate_count=len(candidates),
        source_count=len(by_source),
    )
    indexed, unresolved = _index_entries(song_chart_index)
    candidate_ids = set(identities)
    index_ids = set(indexed) | set(unresolved)
    if candidate_ids != index_ids:
        missing = sorted(candidate_ids - index_ids, key=lambda item: (item.casefold(), item))
        extra = sorted(index_ids - candidate_ids, key=lambda item: (item.casefold(), item))
        raise BatchExtractionError(
            "candidate and song-index chart ID sets differ: "
            f"missing_from_index={missing[:10]!r}, extra_in_index={extra[:10]!r}"
        )
    index_counts = _required_mapping(song_chart_index.get("counts"), context="song index counts")
    expected_index_counts = {
        "candidate_chart_count": len(candidates),
        "indexed_chart_count": len(indexed),
        "unresolved_chart_count": len(unresolved),
    }
    for field, expected in expected_index_counts.items():
        if index_counts.get(field) != expected:
            raise BatchExtractionError(
                f"song index {field} mismatch: expected {expected}, "
                f"found {index_counts.get(field)!r}"
            )
    _validate_index_candidate_provenance(identities, indexed, unresolved)
    safe_paths = _safe_chart_paths(candidates, indexed)
    reports = validation_reports or {}
    output.mkdir(parents=True, exist_ok=True)
    planned_output_paths = {path.as_posix() for path in safe_paths.values()}
    existing_output_paths = _relative_output_files(output / "charts")
    unexpected_existing = sorted(
        existing_output_paths - planned_output_paths,
        key=lambda item: (item.casefold(), item),
    )
    if unexpected_existing:
        raise BatchExtractionError(
            "chart output tree contains files outside the current complete plan: "
            f"{unexpected_existing[:10]!r}"
        )

    rows: list[dict[str, Any]] = []
    ordered_sources = sorted(by_source, key=lambda value: (value.casefold(), value))
    for source_number, source in enumerate(ordered_sources, start=1):
        chart_ids = sorted(by_source[source], key=lambda value: (value.casefold(), value))
        source_identities = [identities[chart_id] for chart_id in chart_ids]
        expected_sizes = {identity["source_size"] for identity in source_identities}
        expected_hashes = {identity["source_sha256"] for identity in source_identities}
        if len(expected_sizes) != 1 or len(expected_hashes) != 1:
            raise BatchExtractionError(f"candidate source fingerprints disagree: {source}")

        source_path: Path | None = None
        source_failure: tuple[str, str, Exception | None] | None = None
        try:
            source_path = _source_path(root, source)
            actual_size, actual_sha256, _prefix = fingerprint_file(source_path)
        except Exception as exc:
            source_failure = ("source-unavailable", "source-verification", exc)
        else:
            if actual_size != next(iter(expected_sizes)) or actual_sha256 != next(iter(expected_hashes)):
                source_failure = ("stale-source-fingerprint", "source-verification", None)

        objects: dict[int, Any] = {}
        if source_failure is None and source_path is not None:
            try:
                environment = loader(str(source_path))
                for obj in environment.objects:
                    path_id = int(obj.path_id)
                    if path_id in objects:
                        raise ValueError(f"duplicate PathID {path_id}")
                    objects[path_id] = obj
            except Exception as exc:
                source_failure = ("bundle-load-failed", "bundle-load", exc)

        for chart_id in chart_ids:
            candidate = candidate_by_id[chart_id]
            identity = identities[chart_id]
            indexed_match = indexed.get(chart_id)
            unresolved_row = unresolved.get(chart_id)
            song = indexed_match[0] if indexed_match is not None else None
            index_row = indexed_match[1] if indexed_match is not None else unresolved_row
            song_id = str(song["song_id"]) if song is not None else None
            row = _base_row(candidate, identity, index_row, song_id)
            if source_failure is not None:
                reason, stage, exc = source_failure
                rows.append(_failure(row, reason=reason, stage=stage, exc=exc))
                continue

            try:
                obj = objects[identity["path_id"]]
                actual_type = _object_type_name(obj)
                if actual_type != identity["object_type"]:
                    raise ValueError(
                        f"object type differs: expected {identity['object_type']!r}, got {actual_type!r}"
                    )
                if "object_byte_size" in candidate:
                    actual_object_size = int(obj.byte_size)
                    expected_object_size = _required_integer(
                        candidate.get("object_byte_size"),
                        context=f"candidate {chart_id} object byte size",
                        minimum=0,
                    )
                    if actual_object_size != expected_object_size:
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
                rows.append(_failure(row, reason="object-evidence-mismatch", stage="object-read", exc=exc))
                continue

            try:
                parsed = parser(payload)
            except OdinParseError as exc:
                row["raw_parse_status"] = "unsupported"
                rows.append(
                    _failure(
                        row,
                        reason="unsupported-serialized-structure",
                        stage="raw-parse",
                        exc=exc,
                    )
                )
                continue
            except Exception as exc:
                row["raw_parse_status"] = "failed"
                rows.append(_failure(row, reason="raw-parse-failed", stage="raw-parse", exc=exc))
                continue
            row["raw_parse_status"] = "parsed"

            try:
                experimental = experimental_builder(
                    candidate,
                    parsed,
                    payload_sha256=payload_sha256,
                    stage_info_raw=data,
                    note_configs_by_uid=note_configs_by_uid,
                    note_data_provenance=note_data_provenance,
                )
            except FirstChartProjectionError as exc:
                row["status"] = "uncertain"
                row["reason"] = "grouping-invariants-not-satisfied"
                row["failure_stage"] = "grouping"
                row["error_type"] = type(exc).__name__
                row["grouping_status"] = "uncertain"
                rows.append(row)
                continue
            except Exception as exc:
                row["grouping_status"] = "failed"
                rows.append(_failure(row, reason="grouping-failed", stage="grouping", exc=exc))
                continue

            row["grouping_status"] = "grouped"
            row["event_count"] = experimental.get("logical_object_count")
            row["raw_record_count"] = experimental.get("raw_record_count")
            if indexed_match is None:
                row["status"] = "uncertain"
                row["reason"] = (
                    "song-identity-unresolved"
                    if unresolved_row is not None
                    else "song-index-entry-missing"
                )
                row["canonical_status"] = "not-attempted"
                rows.append(row)
                continue

            mini_index = {
                "catalog": song_chart_index.get("catalog"),
                "songs": [song],
            }
            try:
                canonical = canonicalizer(experimental, mini_index, reports.get(chart_id))
                rendered = canonical.to_dict() if hasattr(canonical, "to_dict") else canonical
                if not isinstance(rendered, Mapping):
                    raise CanonicalizationError("canonicalizer result is not an object")
                rendered = dict(rendered)
                unknown_types = sum(
                    isinstance(event, Mapping) and event.get("type_status") == "unknown"
                    for event in rendered.get("events", ())
                )
                warnings = set(row["warnings"])
                warnings.update(str(value) for value in rendered.get("warnings", ()))
                if unknown_types:
                    warnings.add("unknown_event_types_preserved")
                row["warnings"] = sorted(warnings, key=lambda value: (value.casefold(), value))
                relative = safe_paths[chart_id]
                destination = _chart_destination(output, relative)
                chart_writer(destination, rendered)
                output_size, output_sha256, _prefix = fingerprint_file(destination)
            except Exception as exc:
                row["canonical_status"] = "failed"
                rows.append(
                    _failure(row, reason="canonical-output-failed", stage="canonical-output", exc=exc)
                )
                continue

            row["status"] = "success"
            row["reason"] = None
            row["canonical_status"] = str(
                rendered.get("canonicalization_status", "canonicalized-with-raw-evidence")
            )
            row["validation_status"] = str(rendered.get("validation_status", "unvalidated"))
            row["event_count"] = rendered.get("event_count", row["event_count"])
            row["output_path"] = relative.as_posix()
            row["output_byte_count"] = output_size
            row["output_sha256"] = output_sha256
            rows.append(row)

        if progress is not None:
            progress(source_number, len(ordered_sources), source)

    rows.sort(key=lambda value: (str(value["chart_id"]).casefold(), str(value["chart_id"])))
    statuses = Counter(str(row["status"]) for row in rows)
    raw_statuses = Counter(str(row["raw_parse_status"]) for row in rows)
    canonical_statuses = Counter(str(row["canonical_status"]) for row in rows)
    success_output_paths = {
        str(row["output_path"])
        for row in rows
        if row.get("status") == "success" and isinstance(row.get("output_path"), str)
    }
    actual_output_paths = _relative_output_files(output / "charts")
    if actual_output_paths != success_output_paths:
        stale = sorted(
            actual_output_paths - success_output_paths,
            key=lambda item: (item.casefold(), item),
        )
        missing = sorted(
            success_output_paths - actual_output_paths,
            key=lambda item: (item.casefold(), item),
        )
        raise BatchExtractionError(
            "chart output tree does not match this run; manifest was not replaced: "
            f"stale={stale[:10]!r}, missing={missing[:10]!r}"
        )
    complete = len(rows) == len(candidates)
    no_failed = statuses.get("failed", 0) == 0
    manifest = {
        "schema_version": BATCH_MANIFEST_SCHEMA_VERSION,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "phase": 9,
        "status": "complete-with-classified-outcomes" if complete else "incomplete",
        "milestone_status": (
            "M8-achieved"
            if complete and no_failed and statuses.get("success", 0) > 0
            else "M8-not-achieved"
        ),
        "game_fingerprint": index_fingerprint,
        "extractor_version": extractor_version,
        "grouping_census": census_evidence,
        "note_data_provenance": dict(note_data_provenance),
        "candidate_count": len(candidates),
        "source_count": len(ordered_sources),
        "chart_file_count": statuses.get("success", 0),
        "event_count": sum(
            row["event_count"] for row in rows if isinstance(row.get("event_count"), int)
        ),
        "status_counts": dict(sorted(statuses.items())),
        "raw_parse_status_counts": dict(sorted(raw_statuses.items())),
        "canonical_status_counts": dict(sorted(canonical_statuses.items())),
        "complete": complete,
        "phase_gate": {
            "all_candidates_classified": complete,
            "all_supported_candidates_extracted": no_failed,
            "allowed_outcomes": ["failed", "success", "uncertain"],
        },
        "ordering": "chart_id case-insensitive ascending, then exact chart_id",
        "copyright_boundary": (
            "local-only output from user-owned resources; do not commit or redistribute"
        ),
        "charts": rows,
    }
    write_compact_json(output / "manifest.json", manifest)
    return manifest
