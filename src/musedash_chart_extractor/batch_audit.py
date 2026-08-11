"""Independently audit a local canonical batch against its manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .diagnostics import write_json

MAX_SAMPLES = 10
BATCH_MANIFEST_SCHEMA_VERSION = 1
CANONICAL_SCHEMA_VERSION = "1.1.0"
EVENT_RAW_KEYS = {"base_raw_record_index", "raw_record_indices"}
RAW_LAYOUT = {
    "strategy": "single-raw-record-table-v1",
    "raw_record_table": "raw.experimental_chart.raw_records",
    "event_record_references": "events[].raw.raw_record_indices",
    "omitted_derived_fields": ["raw.experimental_chart.logical_objects"],
}


class BatchAuditError(RuntimeError):
    """Raised when the batch root or manifest cannot be audited safely."""


def _object(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchAuditError(f"{context} must be an object")
    return value


def _array(value: Any, *, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise BatchAuditError(f"{context} must be an array")
    return value


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _same_json_value(value: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return value is expected
    if _integer(expected):
        return _integer(value) and value == expected
    return value == expected


def _safe_output_path(root: Path, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value:
        raise BatchAuditError("successful manifest row has no output_path")
    portable = PurePosixPath(value)
    if portable.is_absolute() or ".." in portable.parts:
        raise BatchAuditError(f"unsafe manifest output path: {value!r}")
    relative = portable.as_posix()
    destination = (root / Path(*portable.parts)).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise BatchAuditError(
            f"manifest output path escapes batch root: {value!r}"
        ) from exc
    return relative, destination


def _sample(values: list[str]) -> list[str]:
    return values[:MAX_SAMPLES]


def _audit_manifest(
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Recompute the complete M8 manifest invariants from its chart rows."""

    issues: list[str] = []

    def issue(detail: str) -> None:
        issues.append(f"manifest: {detail}")

    expected_values = {
        "schema_version": BATCH_MANIFEST_SCHEMA_VERSION,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "phase": 9,
        "status": "complete-with-classified-outcomes",
        "milestone_status": "M8-achieved",
        "complete": True,
    }
    for field, expected in expected_values.items():
        if not _same_json_value(manifest.get(field), expected):
            issue(f"{field} {manifest.get(field)!r} != {expected!r}")

    candidate_count = manifest.get("candidate_count")
    if not _integer(candidate_count) or candidate_count != len(rows):
        issue(f"candidate_count {candidate_count!r} != chart row count {len(rows)}")

    chart_ids_by_folded: dict[str, str] = {}
    for position, row in enumerate(rows):
        chart_id = row.get("chart_id")
        if not isinstance(chart_id, str) or not chart_id:
            issue(f"charts[{position}].chart_id is not a non-empty string")
            continue
        folded = chart_id.casefold()
        previous = chart_ids_by_folded.get(folded)
        if previous is not None:
            issue(
                "case-insensitive chart_id collision: "
                f"{previous!r}, {chart_id!r}"
            )
        else:
            chart_ids_by_folded[folded] = chart_id

    def row_counter(field: str) -> Counter[str]:
        result: Counter[str] = Counter()
        for position, row in enumerate(rows):
            value = row.get(field)
            if not isinstance(value, str) or not value:
                issue(f"charts[{position}].{field} is not a non-empty string")
                continue
            result[value] += 1
        return result

    statuses = row_counter("status")
    raw_statuses = row_counter("raw_parse_status")
    canonical_statuses = row_counter("canonical_status")
    unknown_statuses = sorted(set(statuses) - {"failed", "success", "uncertain"})
    if unknown_statuses:
        issue(f"charts contain unsupported statuses {unknown_statuses!r}")

    expected_aggregates = {
        "status_counts": dict(sorted(statuses.items())),
        "raw_parse_status_counts": dict(sorted(raw_statuses.items())),
        "canonical_status_counts": dict(sorted(canonical_statuses.items())),
    }
    for field, expected in expected_aggregates.items():
        value = manifest.get(field)
        valid_counts = isinstance(value, Mapping) and all(
            isinstance(key, str) and _integer(count) and count >= 0
            for key, count in value.items()
        )
        if not valid_counts or dict(value) != expected:
            issue(f"{field} {value!r} != row aggregate {expected!r}")

    success_count = statuses.get("success", 0)
    declared_chart_count = manifest.get("chart_file_count")
    if not _integer(declared_chart_count) or declared_chart_count != success_count:
        issue(
            f"chart_file_count {declared_chart_count!r} != success row count "
            f"{success_count}"
        )

    bundles: set[str] = set()
    event_count = 0
    for position, row in enumerate(rows):
        source = row.get("source")
        bundle = source.get("bundle") if isinstance(source, Mapping) else None
        if not isinstance(bundle, str) or not bundle:
            issue(f"charts[{position}].source.bundle is not a non-empty string")
        else:
            bundles.add(bundle)

        row_event_count = row.get("event_count")
        if not _integer(row_event_count) or row_event_count < 0:
            issue(f"charts[{position}].event_count is not a non-negative integer")
        else:
            event_count += row_event_count

    declared_source_count = manifest.get("source_count")
    if not _integer(declared_source_count) or declared_source_count != len(bundles):
        issue(
            f"source_count {declared_source_count!r} != unique row source count "
            f"{len(bundles)}"
        )
    declared_event_count = manifest.get("event_count")
    if not _integer(declared_event_count) or declared_event_count != event_count:
        issue(f"event_count {declared_event_count!r} != row aggregate {event_count}")

    phase_gate = manifest.get("phase_gate")
    if not isinstance(phase_gate, Mapping):
        issue("phase_gate is not an object")
    else:
        expected_gate = {
            "all_candidates_classified": True,
            "all_supported_candidates_extracted": True,
            "allowed_outcomes": ["failed", "success", "uncertain"],
        }
        for field, expected in expected_gate.items():
            if not _same_json_value(phase_gate.get(field), expected):
                issue(f"phase_gate.{field} {phase_gate.get(field)!r} != {expected!r}")

    if statuses.get("failed", 0):
        issue("failed chart rows are incompatible with M8-achieved")
    if success_count == 0:
        issue("M8-achieved requires at least one successful chart row")
    return issues


def _audit_raw_accounting(
    relative: str,
    chart: Mapping[str, Any],
    events: list[Any],
) -> tuple[int, dict[str, list[str]]]:
    """Validate the schema 1.1 raw table and its complete reference closure."""

    mismatches = {
        "raw_layout_mismatches": [],
        "raw_record_table_mismatches": [],
        "record_group_mismatches": [],
        "event_reference_mismatches": [],
        "raw_accounting_mismatches": [],
        "duplicated_payload_mismatches": [],
    }

    def issue(category: str, detail: str) -> None:
        mismatches[category].append(f"{relative}: {detail}")

    raw = chart.get("raw")
    if not isinstance(raw, Mapping):
        issue("raw_layout_mismatches", "raw is not an object")
        return 0, mismatches
    layout = raw.get("layout")
    if not isinstance(layout, Mapping) or dict(layout) != RAW_LAYOUT:
        issue("raw_layout_mismatches", "raw.layout is not the schema 1.1 layout")

    experimental = raw.get("experimental_chart")
    if not isinstance(experimental, Mapping):
        issue("raw_record_table_mismatches", "experimental_chart is not an object")
        return 0, mismatches
    if "logical_objects" in experimental:
        issue(
            "duplicated_payload_mismatches",
            "experimental_chart still contains derived logical_objects",
        )

    raw_rows = experimental.get("raw_records")
    declared_raw_count = experimental.get("raw_record_count")
    original_indices: list[int] = []
    if not isinstance(raw_rows, list):
        issue("raw_record_table_mismatches", "raw_records is not an array")
        raw_rows = []
    else:
        for position, row in enumerate(raw_rows):
            index = row.get("index") if isinstance(row, Mapping) else None
            if not _integer(index):
                issue(
                    "raw_record_table_mismatches",
                    f"raw_records[{position}].index is not an integer",
                )
            else:
                original_indices.append(index)
    if not _integer(declared_raw_count) or declared_raw_count != len(raw_rows):
        issue(
            "raw_record_table_mismatches",
            f"raw_record_count {declared_raw_count!r} != raw_records length {len(raw_rows)}",
        )
    original_duplicate_count = len(original_indices) - len(set(original_indices))
    if original_duplicate_count:
        issue(
            "raw_record_table_mismatches",
            f"raw_records contains {original_duplicate_count} duplicate indices",
        )

    groups_value = experimental.get("record_groups")
    groups = groups_value if isinstance(groups_value, list) else []
    if not isinstance(groups_value, list):
        issue("record_group_mismatches", "record_groups is not an array")
    declared_group_count = experimental.get("record_group_count")
    if not _integer(declared_group_count) or declared_group_count != len(groups):
        issue(
            "record_group_mismatches",
            f"record_group_count {declared_group_count!r} != record_groups length {len(groups)}",
        )

    gameplay_groups: list[Mapping[str, Any]] = []
    sentinel_indices: list[int] = []
    group_indices: list[int] = []
    sentinel_group_count = 0
    for position, group_value in enumerate(groups):
        if not isinstance(group_value, Mapping):
            issue("record_group_mismatches", f"record_groups[{position}] is not an object")
            continue
        group = group_value
        role = group.get("role_status")
        if role == "logical-gameplay-object":
            gameplay_groups.append(group)
        elif role == "observed-sentinel":
            sentinel_group_count += 1
        else:
            issue(
                "record_group_mismatches",
                f"record_groups[{position}] has unknown role {role!r}",
            )
            continue

        members = group.get("raw_record_indices")
        if not isinstance(members, list) or any(not _integer(value) for value in members):
            issue(
                "record_group_mismatches",
                f"record_groups[{position}].raw_record_indices is not an integer array",
            )
            continue
        if len(members) != len(set(members)):
            issue(
                "record_group_mismatches",
                f"record_groups[{position}] contains duplicate raw indices",
            )
        base = group.get("base_raw_record_index")
        if not _integer(base) or base not in members:
            issue(
                "record_group_mismatches",
                f"record_groups[{position}] base index is not a member reference",
            )
        group_indices.extend(members)
        if role == "observed-sentinel":
            sentinel_indices.extend(members)

    group_duplicate_count = len(group_indices) - len(set(group_indices))
    if group_duplicate_count:
        issue(
            "record_group_mismatches",
            f"record_groups reuse {group_duplicate_count} raw indices",
        )
    original_set = set(original_indices)
    group_set = set(group_indices)
    if group_set != original_set:
        issue(
            "record_group_mismatches",
            "record_groups do not exactly cover the raw_records index set",
        )

    declared_logical_count = experimental.get("logical_object_count")
    if not _integer(declared_logical_count) or declared_logical_count != len(
        gameplay_groups
    ):
        issue(
            "record_group_mismatches",
            (
                f"logical_object_count {declared_logical_count!r} != "
                f"gameplay group count {len(gameplay_groups)}"
            ),
        )
    if len(gameplay_groups) != len(events):
        issue(
            "record_group_mismatches",
            f"gameplay group count {len(gameplay_groups)} != event count {len(events)}",
        )

    grouping = experimental.get("grouping")
    if not isinstance(grouping, Mapping):
        issue("record_group_mismatches", "grouping is not an object")
    else:
        expected_grouping_counts = {
            "raw_record_count": len(raw_rows),
            "record_group_count": len(groups),
            "logical_object_count": len(gameplay_groups),
            "observed_sentinel_count": sentinel_group_count,
        }
        for field, expected in expected_grouping_counts.items():
            if not _same_json_value(grouping.get(field), expected):
                issue(
                    "record_group_mismatches",
                    f"grouping.{field} {grouping.get(field)!r} != {expected}",
                )

    event_indices: list[int] = []
    for position, event_value in enumerate(events):
        if not isinstance(event_value, Mapping):
            issue("event_reference_mismatches", f"events[{position}] is not an object")
            continue
        event_raw = event_value.get("raw")
        if not isinstance(event_raw, Mapping) or set(event_raw) != EVENT_RAW_KEYS:
            issue(
                "event_reference_mismatches",
                f"events[{position}].raw is not index-only",
            )
            continue
        base = event_raw.get("base_raw_record_index")
        members = event_raw.get("raw_record_indices")
        if not isinstance(members, list) or any(not _integer(value) for value in members):
            issue(
                "event_reference_mismatches",
                f"events[{position}].raw_record_indices is not an integer array",
            )
            continue
        if len(members) != len(set(members)):
            issue(
                "event_reference_mismatches",
                f"events[{position}] contains duplicate raw indices",
            )
        if not _integer(base) or base not in members:
            issue(
                "event_reference_mismatches",
                f"events[{position}] base index is not a member reference",
            )
        event_indices.extend(members)
        if position < len(gameplay_groups):
            group = gameplay_groups[position]
            if (
                members != group.get("raw_record_indices")
                or base != group.get("base_raw_record_index")
            ):
                issue(
                    "event_reference_mismatches",
                    f"events[{position}] references differ from its gameplay group",
                )

    event_duplicate_count = len(event_indices) - len(set(event_indices))
    sentinel_duplicate_count = len(sentinel_indices) - len(set(sentinel_indices))
    event_set = set(event_indices)
    sentinel_set = set(sentinel_indices)
    overlap = event_set & sentinel_set
    accounted_set = event_set | sentinel_set
    if event_duplicate_count:
        issue(
            "raw_accounting_mismatches",
            f"events reuse {event_duplicate_count} raw indices",
        )
    if sentinel_duplicate_count:
        issue(
            "raw_accounting_mismatches",
            f"sentinel groups reuse {sentinel_duplicate_count} raw indices",
        )
    if overlap:
        issue(
            "raw_accounting_mismatches",
            f"event and sentinel references overlap at {sorted(overlap)[:MAX_SAMPLES]!r}",
        )
    missing = sorted(original_set - accounted_set)
    extra = sorted(accounted_set - original_set)
    if missing or extra:
        issue(
            "raw_accounting_mismatches",
            (
                "event plus sentinel references differ from raw_records: "
                f"missing={missing[:MAX_SAMPLES]!r}, "
                f"extra={extra[:MAX_SAMPLES]!r}"
            ),
        )
    if len(event_indices) + len(sentinel_indices) != len(raw_rows):
        issue(
            "raw_accounting_mismatches",
            "event plus sentinel reference count differs from raw_records length",
        )

    return len(raw_rows), mismatches


def audit_extracted_batch(output_dir: str | Path) -> dict[str, Any]:
    """Hash and inspect every successful canonical file named by one manifest."""

    root = Path(output_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest_text = manifest_bytes.decode("utf-8")
        manifest = _object(json.loads(manifest_text), context="batch manifest")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatchAuditError(f"cannot read batch manifest {manifest_path}: {exc}") from exc

    rows = _array(manifest.get("charts"), context="batch manifest charts")
    expected: dict[str, tuple[Mapping[str, Any], Path]] = {}
    manifest_rows: list[Mapping[str, Any]] = []
    duplicate_paths: list[str] = []
    for position, value in enumerate(rows):
        row = _object(value, context=f"manifest chart row {position}")
        manifest_rows.append(row)
        if row.get("status") != "success":
            continue
        relative, destination = _safe_output_path(root, row.get("output_path"))
        if relative in expected:
            duplicate_paths.append(relative)
        expected[relative] = (row, destination)

    charts_root = root / "charts"
    actual = (
        {
            path.relative_to(root).as_posix()
            for path in charts_root.rglob("*")
            if path.is_file()
        }
        if charts_root.exists()
        else set()
    )
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))

    mismatch_lists: dict[str, list[str]] = {
        "manifest_mismatches": _audit_manifest(manifest, manifest_rows),
        "duplicate_manifest_paths": sorted(set(duplicate_paths)),
        "missing_files": missing,
        "extra_files": extra,
        "size_mismatches": [],
        "sha256_mismatches": [],
        "json_mismatches": [],
        "schema_mismatches": [],
        "identity_mismatches": [],
        "event_count_mismatches": [],
        "raw_layout_mismatches": [],
        "raw_record_table_mismatches": [],
        "record_group_mismatches": [],
        "event_reference_mismatches": [],
        "raw_accounting_mismatches": [],
        "duplicated_payload_mismatches": [],
    }
    audited_bytes = 0
    audited_events = 0
    audited_raw_records = 0
    chart_sizes: list[tuple[int, str]] = []

    for relative in sorted(set(expected) & actual):
        row, destination = expected[relative]
        try:
            payload = destination.read_bytes()
        except OSError as exc:
            mismatch_lists["json_mismatches"].append(
                f"{relative}: read failed: {exc}"
            )
            continue
        audited_bytes += len(payload)
        chart_sizes.append((len(payload), relative))
        expected_size = row.get("output_byte_count")
        if expected_size != len(payload):
            mismatch_lists["size_mismatches"].append(
                f"{relative}: expected {expected_size!r}, found {len(payload)}"
            )
        digest = hashlib.sha256(payload).hexdigest()
        if row.get("output_sha256") != digest:
            mismatch_lists["sha256_mismatches"].append(relative)
        try:
            chart_text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            mismatch_lists["json_mismatches"].append(
                f"{relative}: UTF-8 decode failed: {exc}"
            )
            continue
        try:
            chart = _object(
                json.loads(chart_text), context=f"canonical chart {relative}"
            )
        except (json.JSONDecodeError, BatchAuditError) as exc:
            mismatch_lists["json_mismatches"].append(f"{relative}: {exc}")
            continue

        if chart.get("schema_version") != "1.1.0":
            mismatch_lists["schema_mismatches"].append(relative)
        if chart.get("chart_id") != row.get("chart_id"):
            mismatch_lists["identity_mismatches"].append(relative)
        events_value = chart.get("events")
        if not isinstance(events_value, list):
            mismatch_lists["event_count_mismatches"].append(
                f"{relative}: events is not an array"
            )
            continue
        events = events_value
        audited_events += len(events)
        if not _same_json_value(
            chart.get("event_count"), len(events)
        ) or not _same_json_value(row.get("event_count"), len(events)):
            mismatch_lists["event_count_mismatches"].append(relative)

        raw_record_count, raw_mismatches = _audit_raw_accounting(
            relative, chart, events
        )
        audited_raw_records += raw_record_count
        for category, values in raw_mismatches.items():
            mismatch_lists[category].extend(values)

    mismatch_counts = {
        name: len(values) for name, values in mismatch_lists.items()
    }
    declared_count = manifest.get("chart_file_count")
    count_mismatch = declared_count != len(expected)
    passed = not count_mismatch and not any(mismatch_counts.values())
    support_value = manifest.get("profile_support")
    support = dict(support_value) if isinstance(support_value, Mapping) else None
    ordered_sizes = sorted(chart_sizes)
    smallest = ordered_sizes[0] if ordered_sizes else None
    largest = ordered_sizes[-1] if ordered_sizes else None
    return {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "game_fingerprint": manifest.get("game_fingerprint"),
        "canonical_schema_version": manifest.get("canonical_schema_version"),
        "profile_support": support,
        "manifest": {
            "byte_count": len(manifest_bytes),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "declared_chart_file_count": declared_count,
            "successful_row_count": len(expected),
            "count_matches": not count_mismatch,
        },
        "audited_chart_file_count": len(set(expected) & actual),
        "audited_chart_byte_count": audited_bytes,
        "audited_event_count": audited_events,
        "audited_raw_record_count": audited_raw_records,
        "chart_size_bytes": {
            "minimum": smallest[0] if smallest else None,
            "maximum": largest[0] if largest else None,
            "average": audited_bytes // len(chart_sizes) if chart_sizes else None,
            "largest_path": largest[1] if largest else None,
        },
        "mismatch_counts": mismatch_counts,
        "mismatch_samples": {
            name: _sample(values) for name, values in mismatch_lists.items()
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = audit_extracted_batch(arguments.output_dir)
        destination = write_json(arguments.report, report)
    except (BatchAuditError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "chart_file_count": report["audited_chart_file_count"],
                "chart_byte_count": report["audited_chart_byte_count"],
                "event_count": report["audited_event_count"],
                "raw_record_count": report["audited_raw_record_count"],
                "mismatch_counts": report["mismatch_counts"],
                "report": str(destination.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
