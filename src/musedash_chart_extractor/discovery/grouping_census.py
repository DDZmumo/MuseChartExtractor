"""Phase 9 metadata-only census of raw parsing and grouping families."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import UnityPy

from ..scanner import ScannerError, fingerprint_file, validate_game_directory
from ..unity.odin import OdinParseError, parse_stage_info_payload
from .first_chart import FirstChartProjectionError, _group_records

GROUPING_CENSUS_SCHEMA_VERSION = 1
GROUPING_RULE_VERSION = "composite-neutral-base-negative-id-singleton-v2"


class GroupingCensusError(ScannerError):
    """Raised when census inputs are stale, ambiguous, or unsafe."""


def _source_path(root: Path, relative_path: str) -> Path:
    portable = PurePosixPath(relative_path)
    if portable.is_absolute() or ".." in portable.parts:
        raise GroupingCensusError(f"candidate source escapes game directory: {relative_path}")
    resolved = root.joinpath(*portable.parts).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise GroupingCensusError(
            f"candidate source escapes game directory: {relative_path}"
        ) from exc
    if not resolved.is_file():
        raise GroupingCensusError(f"candidate source does not exist: {resolved}")
    return resolved


def _candidate_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    structure = candidate.get("structure")
    if not isinstance(metadata, Mapping) or not isinstance(structure, Mapping):
        raise GroupingCensusError("candidate lacks metadata or structural fingerprint")
    return {
        "chart_id": metadata.get("asset_name"),
        "source": candidate.get("source"),
        "source_sha256": candidate.get("source_sha256"),
        "container_path": candidate.get("container_path"),
        "path_id": candidate.get("path_id"),
        "object_type": candidate.get("object_type"),
        "payload_byte_count": structure.get("serialized_payload_byte_count"),
        "payload_sha256": structure.get("serialized_payload_sha256"),
    }


def _profile_parsed_records(parsed: Mapping[str, Any]) -> dict[str, Any]:
    records = parsed.get("records")
    if not isinstance(records, list):
        raise GroupingCensusError("parsed payload has no records array")
    groups, logical, grouping = _group_records(records, {})
    config_ids = [
        record["fields"]["configData"]["fields"]["id"]["value"]
        for record in records
    ]
    id_counts = Counter(config_ids)
    nonzero_base_end_index_count = 0
    for row in groups:
        base_index = row["base_raw_record_index"]
        base = records[base_index]
        if base["fields"]["endIndex"]["value"] != 0:
            nonzero_base_end_index_count += 1
    return {
        "status": "grouped",
        "grouping_rule_version": GROUPING_RULE_VERSION,
        "raw_record_count": len(records),
        "record_group_count": len(groups),
        "logical_object_count": len(logical),
        "observed_sentinel_count": grouping["observed_sentinel_count"],
        "expanded_raw_record_count": grouping["expanded_raw_record_count"],
        "negative_config_id_record_count": sum(value < 0 for value in config_ids),
        "repeated_config_id_value_count": sum(count > 1 for count in id_counts.values()),
        "nonzero_neutral_base_end_index_count": nonzero_base_end_index_count,
        "logical_time_descent_count": grouping["logical_sequence_time_descent_count"],
    }


def census_stage_info_grouping(
    game_dir: str | Path,
    candidates: Sequence[Mapping[str, Any]],
    *,
    progress: Callable[[int, int, str], None] | None = None,
    loader: Callable[[str], Any] = UnityPy.load,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Classify every candidate without emitting chart events or payload bytes."""

    root = validate_game_directory(game_dir)
    if not candidates:
        raise GroupingCensusError("candidate census is empty")
    by_source: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    fingerprints: set[str] = set()
    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise GroupingCensusError(f"candidate {position} is not an object")
        if candidate.get("status") != "unvalidated_candidate":
            raise GroupingCensusError(
                f"candidate {position} has unsupported status {candidate.get('status')!r}"
            )
        source = candidate.get("source")
        fingerprint = candidate.get("inventory_fingerprint")
        if not isinstance(source, str) or not source:
            raise GroupingCensusError(f"candidate {position} has no source")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise GroupingCensusError(f"candidate {position} has no inventory fingerprint")
        fingerprints.add(fingerprint)
        by_source[source].append(candidate)
    if len(fingerprints) != 1:
        raise GroupingCensusError(
            f"candidates contain {len(fingerprints)} inventory fingerprints"
        )

    rows: list[dict[str, Any]] = []
    ordered_sources = sorted(by_source, key=lambda value: (value.casefold(), value))
    for source_position, source in enumerate(ordered_sources, start=1):
        source_candidates = sorted(
            by_source[source],
            key=lambda row: (str(row.get("container_path", "")).casefold(), int(row["path_id"])),
        )
        expected_sizes = {candidate.get("source_size") for candidate in source_candidates}
        expected_hashes = {candidate.get("source_sha256") for candidate in source_candidates}
        if len(expected_sizes) != 1 or len(expected_hashes) != 1:
            raise GroupingCensusError(f"candidate source fingerprints disagree: {source}")
        source_path = _source_path(root, source)
        size, sha256, _prefix = fingerprint_file(source_path)
        if size != next(iter(expected_sizes)) or sha256 != next(iter(expected_hashes)):
            raise GroupingCensusError(f"candidate source fingerprint is stale: {source}")

        try:
            environment = loader(str(source_path))
            objects = {int(obj.path_id): obj for obj in environment.objects}
        except Exception as exc:
            for candidate in source_candidates:
                rows.append(
                    {
                        "schema_version": GROUPING_CENSUS_SCHEMA_VERSION,
                        "phase": 9,
                        **_candidate_identity(candidate),
                        "raw_parse_status": "failed",
                        "grouping_status": "not-attempted",
                        "error_type": type(exc).__name__,
                        "error": f"bundle load failed: {exc}",
                    }
                )
        else:
            for candidate in source_candidates:
                identity = _candidate_identity(candidate)
                try:
                    obj = objects[int(candidate["path_id"])]
                    data = obj.parse_as_dict()
                    serialization = data["serializationData"]
                    if serialization["SerializedFormat"] != 0:
                        raise GroupingCensusError(
                            f"unsupported SerializedFormat {serialization['SerializedFormat']!r}"
                        )
                    payload = bytes(serialization["SerializedBytes"])
                    payload_sha256 = hashlib.sha256(payload).hexdigest()
                    if (
                        len(payload) != identity["payload_byte_count"]
                        or payload_sha256 != identity["payload_sha256"]
                    ):
                        raise GroupingCensusError("serialized payload fingerprint is stale")
                    parsed = parse_stage_info_payload(payload)
                except OdinParseError as exc:
                    rows.append(
                        {
                            "schema_version": GROUPING_CENSUS_SCHEMA_VERSION,
                            "phase": 9,
                            **identity,
                            "raw_parse_status": "unsupported",
                            "grouping_status": "not-attempted",
                            "error_type": type(exc).__name__,
                            "error": exc.to_dict(),
                        }
                    )
                except Exception as exc:
                    rows.append(
                        {
                            "schema_version": GROUPING_CENSUS_SCHEMA_VERSION,
                            "phase": 9,
                            **identity,
                            "raw_parse_status": "failed",
                            "grouping_status": "not-attempted",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                else:
                    try:
                        profile = _profile_parsed_records(parsed)
                    except FirstChartProjectionError as exc:
                        rows.append(
                            {
                                "schema_version": GROUPING_CENSUS_SCHEMA_VERSION,
                                "phase": 9,
                                **identity,
                                "raw_parse_status": "parsed",
                                "grouping_status": "uncertain",
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        )
                    else:
                        rows.append(
                            {
                                "schema_version": GROUPING_CENSUS_SCHEMA_VERSION,
                                "phase": 9,
                                **identity,
                                "raw_parse_status": "parsed",
                                "grouping_status": "grouped",
                                "profile": profile,
                            }
                        )
        if progress is not None:
            progress(source_position, len(ordered_sources), source)

    rows.sort(key=lambda row: (str(row.get("chart_id", "")).casefold(), str(row.get("chart_id", ""))))
    raw_statuses = Counter(row["raw_parse_status"] for row in rows)
    grouping_statuses = Counter(row["grouping_status"] for row in rows)
    profiles = [row["profile"] for row in rows if isinstance(row.get("profile"), Mapping)]
    summary = {
        "schema_version": GROUPING_CENSUS_SCHEMA_VERSION,
        "phase": 9,
        "status": "census-complete",
        "inventory_fingerprint": next(iter(fingerprints)),
        "grouping_rule_version": GROUPING_RULE_VERSION,
        "source_count": len(ordered_sources),
        "candidate_count": len(rows),
        "raw_parse_status_counts": dict(sorted(raw_statuses.items())),
        "grouping_status_counts": dict(sorted(grouping_statuses.items())),
        "parsed_raw_record_count": sum(row["raw_record_count"] for row in profiles),
        "grouped_logical_object_count": sum(row["logical_object_count"] for row in profiles),
        "charts_with_observed_sentinel": sum(
            row["observed_sentinel_count"] > 0 for row in profiles
        ),
        "negative_config_id_record_count": sum(
            row["negative_config_id_record_count"] for row in profiles
        ),
        "nonzero_neutral_base_end_index_count": sum(
            row["nonzero_neutral_base_end_index_count"] for row in profiles
        ),
        "complete": len(rows) == len(candidates),
        "copyright_boundary": "metadata-only; no payload bytes or chart events",
    }
    return rows, summary
