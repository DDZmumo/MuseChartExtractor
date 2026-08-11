"""Evidence-scoped validation for canonical Muse Dash charts.

The validator deliberately separates structural checks, aggregate semantic
checks, and independent references.  An aggregate combo match is never
reported as an event-by-event comparison.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..scanner import ScannerError, fingerprint_file, validate_game_directory
from .canonicalize import CanonicalizationError, reconstruct_experimental_chart

VALIDATION_SCHEMA_VERSION = "validation-report-v1"
ADD_COMBO_TYPE_IDS = frozenset({1, 3, 4, 5, 8})
DIFFERENCE_CATEGORIES = (
    "matched",
    "missing_offline",
    "extra_offline",
    "timing_delta",
    "type_mismatch",
    "lane_mismatch",
    "duration_delta",
)

_DECIMAL_PATTERN = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)


class ValidationInputError(ScannerError):
    """Raised when the validation request itself is ambiguous or malformed."""


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _strict_decimal(
    value: Any,
    *,
    path: str,
    errors: list[dict[str, str]],
) -> Decimal | None:
    if not isinstance(value, str) or not _DECIMAL_PATTERN.fullmatch(value):
        errors.append(
            _issue(
                "invalid_decimal_text",
                path,
                "expected a finite JSON-style decimal encoded as an exact string",
            )
        )
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        errors.append(_issue("invalid_decimal_text", path, "cannot parse decimal"))
        return None
    if not number.is_finite():
        errors.append(_issue("non_finite_decimal", path, "decimal must be finite"))
        return None
    return number


def _decimal_summary(values: Sequence[Decimal]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "minimum_sec": None, "maximum_sec": None}
    return {
        "count": len(values),
        "minimum_sec": format(min(values), "f"),
        "maximum_sec": format(max(values), "f"),
    }


def _ratio(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return format(Decimal(numerator) / Decimal(denominator), ".6f")


def _type_sort_key(value: str) -> tuple[int, int | str]:
    if value == "null":
        return 2, value
    try:
        return 0, int(value)
    except ValueError:
        return 1, value


def _source_check(
    source: Any,
    game_dir: str | Path | None,
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        errors.append(_issue("invalid_source", "source", "source must be an object"))
        return {"status": "invalid", "verified": False}

    bundle = source.get("bundle")
    expected_sha256 = source.get("bundle_sha256")
    if not isinstance(bundle, str) or not bundle:
        errors.append(
            _issue("invalid_source_bundle", "source.bundle", "bundle must be a path string")
        )
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_sha256
    ):
        errors.append(
            _issue(
                "invalid_source_sha256",
                "source.bundle_sha256",
                "bundle SHA-256 must contain 64 hexadecimal characters",
            )
        )
    if not isinstance(bundle, str) or not bundle or not isinstance(expected_sha256, str):
        return {"status": "invalid", "verified": False}

    if game_dir is None:
        warnings.append(
            _issue(
                "source_not_checked",
                "source.bundle",
                "no game directory was supplied; source existence and hash were not checked",
            )
        )
        return {
            "status": "not_checked",
            "verified": False,
            "bundle": bundle,
            "expected_sha256": expected_sha256,
        }

    root = validate_game_directory(game_dir)
    candidate = (root / Path(bundle)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        errors.append(
            _issue(
                "source_outside_game_directory",
                "source.bundle",
                "resolved source path escapes the game directory",
            )
        )
        return {
            "status": "outside_game_directory",
            "verified": False,
            "bundle": bundle,
            "expected_sha256": expected_sha256,
        }
    if not candidate.is_file():
        errors.append(
            _issue("source_missing", "source.bundle", "source bundle does not exist")
        )
        return {
            "status": "missing",
            "verified": False,
            "bundle": bundle,
            "expected_sha256": expected_sha256,
            "resolved_path": str(candidate),
        }

    size, actual_sha256, _ = fingerprint_file(candidate)
    verified = actual_sha256.casefold() == expected_sha256.casefold()
    if not verified:
        errors.append(
            _issue(
                "source_sha256_mismatch",
                "source.bundle_sha256",
                "source bundle content does not match canonical provenance",
            )
        )
    return {
        "status": "verified" if verified else "sha256_mismatch",
        "verified": verified,
        "bundle": bundle,
        "resolved_path": str(candidate),
        "size_bytes": size,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
    }


def _raw_accounting(
    chart: Mapping[str, Any],
    events: Sequence[Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    raw = chart.get("raw")
    experimental = raw.get("experimental_chart") if isinstance(raw, Mapping) else None
    if not isinstance(experimental, Mapping):
        errors.append(
            _issue(
                "missing_experimental_evidence",
                "raw.experimental_chart",
                "canonical chart has no retained experimental chart",
            )
        )
        return {"status": "invalid", "accounted": False}

    if chart.get("schema_version") == "1.1.0":
        try:
            reconstruct_experimental_chart(raw)
        except CanonicalizationError as exc:
            errors.append(
                _issue(
                    "invalid_canonical_raw_layout",
                    "raw.layout",
                    str(exc),
                )
            )

    expected = experimental.get("raw_record_count")
    raw_rows = experimental.get("raw_records")
    record_groups = experimental.get("record_groups")
    grouping = experimental.get("grouping")
    sentinel = (
        grouping.get("observed_sentinel_count")
        if isinstance(grouping, Mapping)
        else None
    )
    if not _is_integer(expected) or expected < 0:
        errors.append(
            _issue(
                "invalid_raw_record_count",
                "raw.experimental_chart.raw_record_count",
                "raw record count must be a non-negative integer",
            )
        )
    if not _is_integer(sentinel) or sentinel < 0:
        errors.append(
            _issue(
                "invalid_sentinel_count",
                "raw.experimental_chart.grouping.observed_sentinel_count",
                "sentinel count must be a non-negative integer",
            )
        )

    original_indices: list[int] = []
    complete_original_indices = True
    if not isinstance(raw_rows, list):
        complete_original_indices = False
        errors.append(
            _issue(
                "missing_original_raw_records",
                "raw.experimental_chart.raw_records",
                "retained experimental evidence must include the original raw record array",
            )
        )
    else:
        for position, row in enumerate(raw_rows):
            index = row.get("index") if isinstance(row, Mapping) else None
            if not _is_integer(index):
                complete_original_indices = False
                errors.append(
                    _issue(
                        "invalid_original_raw_record_index",
                        f"raw.experimental_chart.raw_records[{position}].index",
                        "original raw record index must be an integer",
                    )
                )
            else:
                original_indices.append(index)
    original_duplicates = len(original_indices) - len(set(original_indices))
    if original_duplicates:
        errors.append(
            _issue(
                "duplicate_original_raw_record_indices",
                "raw.experimental_chart.raw_records[].index",
                f"{original_duplicates} original raw record indices are duplicated",
            )
        )
    if _is_integer(expected) and isinstance(raw_rows, list) and expected != len(raw_rows):
        errors.append(
            _issue(
                "original_raw_record_count_mismatch",
                "raw.experimental_chart.raw_record_count",
                "raw_record_count does not equal the retained raw_records length",
            )
        )

    sentinel_indices: list[int] = []
    sentinel_group_count = 0
    complete_sentinel_indices = True
    gameplay_groups: list[Mapping[str, Any]] = []
    if not isinstance(record_groups, list):
        complete_sentinel_indices = False
        errors.append(
            _issue(
                "missing_record_groups",
                "raw.experimental_chart.record_groups",
                "retained experimental evidence must include record groups",
            )
        )
    else:
        for group_position, group in enumerate(record_groups):
            if not isinstance(group, Mapping):
                complete_sentinel_indices = False
                errors.append(
                    _issue(
                        "invalid_record_group",
                        f"raw.experimental_chart.record_groups[{group_position}]",
                        "record group must be an object",
                    )
                )
                continue
            role = group.get("role_status")
            if role == "logical-gameplay-object":
                gameplay_groups.append(group)
                continue
            if role != "observed-sentinel":
                complete_sentinel_indices = False
                errors.append(
                    _issue(
                        "unknown_record_group_role",
                        (
                            f"raw.experimental_chart.record_groups[{group_position}]"
                            ".role_status"
                        ),
                        f"unknown record-group role: {role!r}",
                    )
                )
                continue
            sentinel_group_count += 1
            indices = group.get("raw_record_indices")
            if not isinstance(indices, list):
                complete_sentinel_indices = False
                errors.append(
                    _issue(
                        "invalid_sentinel_raw_indices",
                        (
                            f"raw.experimental_chart.record_groups[{group_position}]"
                            ".raw_record_indices"
                        ),
                        "sentinel group raw_record_indices must be an array",
                    )
                )
                continue
            for index_position, index in enumerate(indices):
                if not _is_integer(index):
                    complete_sentinel_indices = False
                    errors.append(
                        _issue(
                            "invalid_sentinel_raw_index",
                            (
                                f"raw.experimental_chart.record_groups[{group_position}]"
                                f".raw_record_indices[{index_position}]"
                            ),
                            "sentinel raw record index must be an integer",
                        )
                    )
                else:
                    sentinel_indices.append(index)
    if _is_integer(sentinel) and sentinel_group_count != sentinel:
        errors.append(
            _issue(
                "sentinel_group_count_mismatch",
                "raw.experimental_chart.grouping.observed_sentinel_count",
                "observed sentinel count does not equal sentinel record-group count",
            )
        )
    sentinel_duplicates = len(sentinel_indices) - len(set(sentinel_indices))
    if sentinel_duplicates:
        errors.append(
            _issue(
                "duplicate_sentinel_raw_indices",
                "raw.experimental_chart.record_groups[].raw_record_indices",
                f"{sentinel_duplicates} sentinel raw record indices are duplicated",
            )
        )

    if len(gameplay_groups) != len(events):
        errors.append(
            _issue(
                "event_record_group_count_mismatch",
                "raw.experimental_chart.record_groups",
                "logical gameplay record-group count does not equal event count",
            )
        )

    referenced_count = 0
    referenced_indices: list[int] = []
    complete_event_raw = True
    for event_position, event in enumerate(events):
        event_raw = event.get("raw") if isinstance(event, Mapping) else None
        indices = (
            event_raw.get("raw_record_indices")
            if isinstance(event_raw, Mapping)
            else None
        )
        if not isinstance(indices, list):
            complete_event_raw = False
            errors.append(
                _issue(
                    "missing_event_raw_record_indices",
                    f"events[{event_position}].raw.raw_record_indices",
                    "event must reference its grouped raw MusicData records by index",
                )
            )
            continue
        if "music_data_records" in event_raw or "group" in event_raw:
            complete_event_raw = False
            errors.append(
                _issue(
                    "duplicated_event_raw_payload",
                    f"events[{event_position}].raw",
                    "event raw must reference the top-level evidence tables, not duplicate them",
                )
            )
        referenced_count += len(indices)
        valid_event_indices: list[int] = []
        for index_position, index in enumerate(indices):
            if not _is_integer(index):
                complete_event_raw = False
                errors.append(
                    _issue(
                        "invalid_raw_record_index",
                        (
                            f"events[{event_position}].raw.raw_record_indices"
                            f"[{index_position}]"
                        ),
                        "raw record index must be an integer",
                    )
                )
            else:
                referenced_indices.append(index)
                valid_event_indices.append(index)
        base_index = (
            event_raw.get("base_raw_record_index")
            if isinstance(event_raw, Mapping)
            else None
        )
        if not _is_integer(base_index) or base_index not in valid_event_indices:
            complete_event_raw = False
            errors.append(
                _issue(
                    "invalid_event_base_raw_record_index",
                    f"events[{event_position}].raw.base_raw_record_index",
                    "event base raw record index must be an integer member reference",
                )
            )
        if event_position < len(gameplay_groups):
            expected_group = gameplay_groups[event_position]
            expected_indices = expected_group.get("raw_record_indices")
            expected_base = expected_group.get("base_raw_record_index")
            if indices != expected_indices or base_index != expected_base:
                complete_event_raw = False
                errors.append(
                    _issue(
                        "event_record_group_reference_mismatch",
                        f"events[{event_position}].raw",
                        "event raw references differ from the retained record group",
                    )
                )

    referenced_duplicates = len(referenced_indices) - len(set(referenced_indices))
    if referenced_duplicates:
        errors.append(
            _issue(
                "duplicate_raw_record_indices",
                "events[].raw.raw_record_indices",
                f"{referenced_duplicates} referenced raw record indices are duplicated",
            )
        )
    original_set = set(original_indices)
    referenced_set = set(referenced_indices)
    sentinel_set = set(sentinel_indices)
    accounted_set = referenced_set | sentinel_set
    event_sentinel_overlap = referenced_set & sentinel_set
    missing_indices = sorted(original_set - accounted_set)
    extra_indices = sorted(accounted_set - original_set)
    if event_sentinel_overlap:
        errors.append(
            _issue(
                "event_sentinel_index_overlap",
                "events[].raw.raw_record_indices",
                "event and sentinel evidence claim the same raw record indices",
            )
        )
    if missing_indices or extra_indices:
        errors.append(
            _issue(
                "raw_record_index_set_mismatch",
                "raw.experimental_chart.raw_records[].index",
                "event plus sentinel raw indices do not exactly equal the original raw index set",
            )
        )

    accounted = (
        complete_event_raw
        and complete_original_indices
        and complete_sentinel_indices
        and _is_integer(expected)
        and _is_integer(sentinel)
        and isinstance(raw_rows, list)
        and expected == len(raw_rows)
        and sentinel_group_count == sentinel
        and len(gameplay_groups) == len(events)
        and referenced_count + len(sentinel_indices) == expected
        and referenced_duplicates == 0
        and original_duplicates == 0
        and sentinel_duplicates == 0
        and not event_sentinel_overlap
        and not missing_indices
        and not extra_indices
    )
    if not accounted and _is_integer(expected) and _is_integer(sentinel):
        errors.append(
            _issue(
                "raw_record_accounting_mismatch",
                "raw.experimental_chart.raw_record_count",
                "event references plus sentinel records do not exactly account for raw records",
            )
        )
    return {
        "status": "accounted" if accounted else "invalid",
        "accounted": accounted,
        "expected_raw_record_count": expected,
        "referenced_event_raw_record_count": referenced_count,
        "observed_sentinel_count": sentinel,
        "observed_sentinel_raw_record_count": len(sentinel_indices),
        "duplicate_referenced_index_count": referenced_duplicates,
        "duplicate_original_index_count": original_duplicates,
        "duplicate_sentinel_index_count": sentinel_duplicates,
        "event_sentinel_overlap_count": len(event_sentinel_overlap),
        "missing_index_count": len(missing_indices),
        "missing_index_sample": missing_indices[:10],
        "extra_index_count": len(extra_indices),
        "extra_index_sample": extra_indices[:10],
    }


def _not_compared_differences() -> dict[str, dict[str, Any]]:
    return {
        category: {
            "status": "not_compared",
            "count": None,
            "details": [],
        }
        for category in DIFFERENCE_CATEGORIES
    }


def _reference_check(
    chart_id: str | None,
    projected_combo: int,
    projection_complete: bool,
    reference: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if reference is None:
        return {
            "status": "not_provided",
            "scope": "none",
            "expected_combo": None,
            "projected_combo": projected_combo,
            "source": None,
        }
    reference_chart_id = reference.get("chart_id")
    expected_combo = reference.get("expected_combo")
    if reference_chart_id != chart_id:
        return {
            "status": "invalid_reference",
            "scope": "aggregate_combo_only",
            "expected_combo": expected_combo,
            "projected_combo": projected_combo,
            "source": reference.get("source"),
            "reason": "reference chart_id does not match canonical chart_id",
        }
    if not _is_integer(expected_combo) or expected_combo < 0:
        return {
            "status": "invalid_reference",
            "scope": "aggregate_combo_only",
            "expected_combo": expected_combo,
            "projected_combo": projected_combo,
            "source": reference.get("source"),
            "reason": "expected_combo must be a non-negative integer",
        }
    if not projection_complete:
        return {
            "status": "not_compared",
            "scope": "aggregate_combo_only",
            "expected_combo": expected_combo,
            "projected_combo": projected_combo,
            "source": reference.get("source"),
            "reason": "offline combo projection is incomplete",
        }
    return {
        "status": "matched" if expected_combo == projected_combo else "mismatch",
        "scope": "aggregate_combo_only",
        "expected_combo": expected_combo,
        "projected_combo": projected_combo,
        "delta": projected_combo - expected_combo,
        "source": reference.get("source"),
        "claim_limit": (
            "aggregate combo agreement does not establish event-level timing, type, "
            "lane, or duration accuracy"
        ),
    }


def validate_canonical_chart(
    chart: Mapping[str, Any],
    *,
    game_dir: str | Path | None = None,
    reference: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one canonical chart without mutating it or its game resources."""

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    chart_id_value = chart.get("chart_id")
    chart_id = chart_id_value if isinstance(chart_id_value, str) else None
    if not chart_id:
        errors.append(
            _issue("invalid_chart_id", "chart_id", "chart_id must be a non-empty string")
        )

    events_value = chart.get("events")
    if not isinstance(events_value, list):
        errors.append(_issue("invalid_events", "events", "events must be an array"))
        events: list[Any] = []
    else:
        events = events_value
    declared_count = chart.get("event_count")
    if not _is_integer(declared_count) or declared_count < 0:
        errors.append(
            _issue(
                "invalid_event_count",
                "event_count",
                "event_count must be a non-negative integer",
            )
        )
    elif declared_count != len(events):
        errors.append(
            _issue(
                "event_count_mismatch",
                "event_count",
                "event_count does not equal the events array length",
            )
        )
    if len(events) == 0:
        errors.append(_issue("empty_chart", "events", "chart contains no events"))
    elif len(events) < 10:
        warnings.append(
            _issue(
                "sparse_chart_review_required",
                "event_count",
                "chart has fewer than 10 events; this is legal but merits manual review",
            )
        )

    type_counts: Counter[str] = Counter()
    air_counts: Counter[str] = Counter()
    hold_durations: list[Decimal] = []
    multi_durations: list[Decimal] = []
    duration_missing_by_type: Counter[str] = Counter()
    unknown_type_count = 0
    projected_combo = 0
    projected_long_press_ends = 0
    projection_complete = True
    previous_time: Decimal | None = None

    for position, event_value in enumerate(events):
        prefix = f"events[{position}]"
        if not isinstance(event_value, Mapping):
            errors.append(_issue("invalid_event", prefix, "event must be an object"))
            projection_complete = False
            continue
        event = event_value
        index = event.get("index")
        if not _is_integer(index) or index != position:
            errors.append(
                _issue(
                    "event_index_mismatch",
                    f"{prefix}.index",
                    f"expected contiguous index {position}",
                )
            )

        time = _strict_decimal(event.get("time_sec"), path=f"{prefix}.time_sec", errors=errors)
        duration_value = event.get("duration_sec")
        end_value = event.get("end_time_sec")
        duration = (
            None
            if duration_value is None
            else _strict_decimal(duration_value, path=f"{prefix}.duration_sec", errors=errors)
        )
        end = (
            None
            if end_value is None
            else _strict_decimal(end_value, path=f"{prefix}.end_time_sec", errors=errors)
        )
        if time is not None:
            if time < 0:
                warnings.append(
                    _issue(
                        "negative_raw_time_preserved",
                        f"{prefix}.time_sec",
                        (
                            "negative finite raw time is preserved; the game timing "
                            "origin/offset has not been generalized"
                        ),
                    )
                )
            if previous_time is not None and time < previous_time:
                errors.append(
                    _issue(
                        "event_order_descends",
                        f"{prefix}.time_sec",
                        "event time is earlier than the preceding event",
                    )
                )
            previous_time = time
        if duration is not None and duration < 0:
            errors.append(
                _issue(
                    "negative_duration",
                    f"{prefix}.duration_sec",
                    "duration must be non-negative",
                )
            )
        if (duration_value is None) != (end_value is None):
            errors.append(
                _issue(
                    "incomplete_duration_pair",
                    prefix,
                    "duration_sec and end_time_sec must either both be null or both be present",
                )
            )
        if time is not None and end is not None:
            if end < time:
                errors.append(
                    _issue(
                        "end_before_start",
                        f"{prefix}.end_time_sec",
                        "end time must not precede event time",
                    )
                )
            if duration is not None and end != time + duration:
                errors.append(
                    _issue(
                        "duration_end_mismatch",
                        f"{prefix}.end_time_sec",
                        "end time must exactly equal time plus duration",
                    )
                )

        type_id = event.get("type_id")
        if type_id is not None and not _is_integer(type_id):
            errors.append(
                _issue("invalid_type_id", f"{prefix}.type_id", "type_id must be integer or null")
            )
            type_key = "invalid"
            projection_complete = False
        else:
            type_key = "null" if type_id is None else str(type_id)
        type_counts[type_key] += 1
        type_status = event.get("type_status")
        if type_status == "unknown" or type_id is None:
            unknown_type_count += 1
        elif type_status != "known":
            warnings.append(
                _issue(
                    "unrecognized_type_status",
                    f"{prefix}.type_status",
                    "type_status is neither known nor unknown",
                )
            )

        is_air = event.get("is_air")
        if is_air is None:
            air_counts["unknown"] += 1
        elif isinstance(is_air, bool):
            air_counts["air" if is_air else "ground"] += 1
        else:
            errors.append(
                _issue("invalid_is_air", f"{prefix}.is_air", "is_air must be boolean or null")
            )
            air_counts["invalid"] += 1

        if type_id in {3, 8}:
            if duration is None:
                duration_missing_by_type[str(type_id)] += 1
            elif type_id == 3:
                hold_durations.append(duration)
            else:
                multi_durations.append(duration)
        if type_id in ADD_COMBO_TYPE_IDS:
            projected_combo += 1
        if type_id == 3:
            extra = event.get("extra")
            end_count = (
                extra.get("long_press_end_record_count")
                if isinstance(extra, Mapping)
                else None
            )
            if not _is_integer(end_count) or end_count < 0:
                projection_complete = False
                warnings.append(
                    _issue(
                        "invalid_long_press_end_count",
                        f"{prefix}.extra.long_press_end_record_count",
                        "combo projection is incomplete without a non-negative integer end count",
                    )
                )
            else:
                projected_long_press_ends += end_count
                projected_combo += end_count

    if unknown_type_count:
        warnings.append(
            _issue(
                "unknown_types_preserved",
                "events[].type_status",
                f"{unknown_type_count} events retain unknown type semantics",
            )
        )
    if air_counts["unknown"]:
        warnings.append(
            _issue(
                "air_ground_not_interpreted",
                "events[].is_air",
                f"{air_counts['unknown']} events have no interpreted air/ground value",
            )
        )
    for type_id, label in ((3, "hold"), (8, "multi")):
        missing = duration_missing_by_type[str(type_id)]
        if missing:
            warnings.append(
                _issue(
                    f"{label}_duration_missing",
                    "events[].duration_sec",
                    f"{missing} type {type_id} events have no interpreted duration",
                )
            )

    source_check = _source_check(chart.get("source"), game_dir, errors, warnings)
    accounting = _raw_accounting(chart, events, errors)
    reference_check = _reference_check(
        chart_id,
        projected_combo,
        projection_complete,
        reference,
    )
    if reference_check["status"] in {"mismatch", "invalid_reference"}:
        warnings.append(
            _issue(
                "reference_not_matched",
                "reference.expected_combo",
                "aggregate reference did not match the offline combo projection",
            )
        )

    structural_valid = not errors
    if not structural_valid:
        status = "structurally-invalid"
    elif reference_check["status"] == "matched":
        status = "partially-validated-aggregate-combo-match"
    elif reference_check["status"] == "mismatch":
        status = "structurally-valid-aggregate-combo-mismatch"
    else:
        status = "structurally-valid-reference-incomplete"

    return {
        "chart_id": chart_id,
        "status": status,
        "structural": {
            "valid": structural_valid,
            "event_count": {
                "declared": declared_count,
                "actual": len(events),
                "non_empty": bool(events),
                "upper_limit_applied": False,
            },
            "source": source_check,
            "raw_accounting": accounting,
            "errors": errors,
        },
        "semantic": {
            "type_distribution": dict(
                sorted(type_counts.items(), key=lambda item: _type_sort_key(item[0]))
            ),
            "air_ground_distribution": {
                key: air_counts[key] for key in ("air", "ground", "unknown", "invalid")
            },
            "unknown_type_count": unknown_type_count,
            "unknown_type_ratio": _ratio(unknown_type_count, len(events)),
            "hold_duration": {
                **_decimal_summary(hold_durations),
                "missing_count": duration_missing_by_type["3"],
            },
            "multi_duration": {
                **_decimal_summary(multi_durations),
                "missing_count": duration_missing_by_type["8"],
            },
            "add_combo_projection": {
                "status": "complete" if projection_complete else "incomplete",
                "projected_combo": projected_combo,
                "base_add_combo_event_count": projected_combo - projected_long_press_ends,
                "long_press_end_count": projected_long_press_ends,
                "add_combo_type_ids": sorted(ADD_COMBO_TYPE_IDS),
                "formula": (
                    "count(type_id in [1,3,4,5,8]) + "
                    "sum(type3.extra.long_press_end_record_count)"
                ),
                "evidence_scope": "pinned static IL2CPP and NoteData evidence",
            },
            "warnings": warnings,
        },
        "reference": reference_check,
        "differences": _not_compared_differences(),
        "comparison_scope": {
            "aggregate_combo": reference_check["status"],
            "event_level": "not_compared",
            "claim": (
                "No event-level reference was supplied; timing, type, lane, air/ground, "
                "and duration accuracy remain unverified by this report."
            ),
        },
    }


def _reference_map(
    references: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if references is None:
        return {}
    rows: Sequence[Any]
    if isinstance(references, Mapping):
        embedded = references.get("references")
        if isinstance(embedded, list):
            rows = embedded
        else:
            keyed: dict[str, Mapping[str, Any]] = {}
            for chart_id, reference in references.items():
                if not isinstance(chart_id, str) or not isinstance(reference, Mapping):
                    raise ValidationInputError(
                        "reference mapping must map chart ids to reference objects"
                    )
                row = dict(reference)
                row.setdefault("chart_id", chart_id)
                keyed[chart_id] = row
            return keyed
    elif isinstance(references, Sequence) and not isinstance(references, (str, bytes)):
        rows = references
    else:
        raise ValidationInputError("references must be an array or object")

    indexed: dict[str, Mapping[str, Any]] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValidationInputError(f"reference {position} must be an object")
        chart_id = row.get("chart_id")
        if not isinstance(chart_id, str) or not chart_id:
            raise ValidationInputError(f"reference {position} has no chart_id")
        if chart_id in indexed:
            raise ValidationInputError(f"duplicate reference chart_id: {chart_id}")
        indexed[chart_id] = row
    return indexed


def validate_canonical_charts(
    charts: Sequence[Mapping[str, Any]],
    *,
    game_dir: str | Path | None = None,
    references: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate multiple canonical charts and conservatively evaluate M7."""

    reference_by_chart = _reference_map(references)
    chart_reports: list[dict[str, Any]] = []
    seen_chart_ids: set[str] = set()
    duplicate_chart_ids: list[str] = []
    for position, chart in enumerate(charts):
        if not isinstance(chart, Mapping):
            raise ValidationInputError(f"chart {position} must be an object")
        chart_id = chart.get("chart_id")
        reference = reference_by_chart.get(chart_id) if isinstance(chart_id, str) else None
        report = validate_canonical_chart(chart, game_dir=game_dir, reference=reference)
        chart_reports.append(report)
        if isinstance(chart_id, str):
            if chart_id in seen_chart_ids:
                duplicate_chart_ids.append(chart_id)
            seen_chart_ids.add(chart_id)

    structural_valid_count = sum(
        report["structural"]["valid"] for report in chart_reports
    )
    source_verified_count = sum(
        report["structural"]["source"].get("verified") is True
        for report in chart_reports
    )
    reference_matched_count = sum(
        report["reference"]["status"] == "matched" for report in chart_reports
    )
    reference_mismatch_count = sum(
        report["reference"]["status"] in {"mismatch", "invalid_reference"}
        for report in chart_reports
    )
    unreferenced_ids = sorted(reference_by_chart.keys() - seen_chart_ids)
    milestone_achieved = (
        len(chart_reports) >= 2
        and not duplicate_chart_ids
        and not unreferenced_ids
        and structural_valid_count == len(chart_reports)
        and source_verified_count == len(chart_reports)
        and reference_matched_count == len(chart_reports)
    )
    if milestone_achieved:
        status = "partially-validated-multiple-charts"
    elif structural_valid_count != len(chart_reports) or reference_mismatch_count:
        status = "validation-failed"
    else:
        status = "validation-incomplete"

    explanations = []
    for report in chart_reports:
        accounting = report["structural"]["raw_accounting"]
        combo = report["semantic"]["add_combo_projection"]
        explanations.append(
            {
                "chart_id": report["chart_id"],
                "raw_to_logical": (
                    "raw records are grouped by retained logical objects; observed sentinels "
                    "are accounted separately"
                ),
                "raw_record_count": accounting.get("expected_raw_record_count"),
                "logical_event_count": report["structural"]["event_count"]["actual"],
                "observed_sentinel_count": accounting.get("observed_sentinel_count"),
                "logical_to_combo": combo["formula"],
                "projected_combo": combo["projected_combo"],
            }
        )

    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "status": status,
        "milestone_status": "M7-achieved" if milestone_achieved else "M7-not-achieved",
        "validation_scope": {
            "structural": "canonical structure, exact decimals, provenance, and raw accounting",
            "semantic": "distributions and static addCombo aggregate projection",
            "reference": "aggregate combo only",
            "event_level_reference": "not_compared",
            "accuracy_claim": "partial; this report does not claim 100% event accuracy",
        },
        "summary": {
            "chart_count": len(chart_reports),
            "structural_valid_count": structural_valid_count,
            "source_verified_count": source_verified_count,
            "reference_matched_count": reference_matched_count,
            "reference_mismatch_count": reference_mismatch_count,
            "duplicate_chart_ids": sorted(set(duplicate_chart_ids)),
            "references_without_chart": unreferenced_ids,
        },
        "major_difference_explanations": explanations,
        "charts": chart_reports,
    }


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "n/a"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_validation_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact human-readable companion to a JSON validation report."""

    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# Chart validation report",
        "",
        f"- Status: `{_markdown_cell(report.get('status'))}`",
        f"- Milestone: `{_markdown_cell(report.get('milestone_status'))}`",
        f"- Charts: {_markdown_cell(summary.get('chart_count'))}",
        f"- Structurally valid: {_markdown_cell(summary.get('structural_valid_count'))}",
        f"- Source verified: {_markdown_cell(summary.get('source_verified_count'))}",
        f"- Aggregate references matched: {_markdown_cell(summary.get('reference_matched_count'))}",
        "- Accuracy claim: partial; no event-level reference was compared.",
        "",
        "| Chart | Structural | Source | Projected combo | Reference | Status |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    charts = report.get("charts")
    if not isinstance(charts, list):
        charts = []
    for chart in charts:
        if not isinstance(chart, Mapping):
            continue
        structural = chart.get("structural")
        semantic = chart.get("semantic")
        reference = chart.get("reference")
        source = structural.get("source", {}) if isinstance(structural, Mapping) else {}
        projection = (
            semantic.get("add_combo_projection", {})
            if isinstance(semantic, Mapping)
            else {}
        )
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    chart.get("chart_id"),
                    structural.get("valid") if isinstance(structural, Mapping) else None,
                    source.get("status") if isinstance(source, Mapping) else None,
                    projection.get("projected_combo")
                    if isinstance(projection, Mapping)
                    else None,
                    reference.get("status") if isinstance(reference, Mapping) else None,
                    chart.get("status"),
                )
            )
            + " |"
        )

    lines.extend(["", "## Difference categories", ""])
    lines.append(
        "The supplied references contain aggregate combo counts only. Every event-level "
        "difference category therefore remains `not_compared`."
    )
    lines.extend(["", "| Chart | Category | Status |", "| --- | --- | --- |"])
    for chart in charts:
        if not isinstance(chart, Mapping):
            continue
        differences = chart.get("differences")
        if not isinstance(differences, Mapping):
            continue
        for category in DIFFERENCE_CATEGORIES:
            detail = differences.get(category)
            status = detail.get("status") if isinstance(detail, Mapping) else None
            lines.append(
                f"| {_markdown_cell(chart.get('chart_id'))} | {category} | "
                f"{_markdown_cell(status)} |"
            )

    issue_rows: list[tuple[Any, str, Mapping[str, Any]]] = []
    for chart in charts:
        if not isinstance(chart, Mapping):
            continue
        structural = chart.get("structural")
        semantic = chart.get("semantic")
        errors = structural.get("errors", []) if isinstance(structural, Mapping) else []
        warnings = semantic.get("warnings", []) if isinstance(semantic, Mapping) else []
        for level, issues in (("error", errors), ("warning", warnings)):
            if isinstance(issues, list):
                issue_rows.extend(
                    (chart.get("chart_id"), level, issue)
                    for issue in issues
                    if isinstance(issue, Mapping)
                )
    if issue_rows:
        lines.extend(
            [
                "",
                "## Issues",
                "",
                "| Chart | Level | Code | Path | Message |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for chart_id, level, issue in issue_rows:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        chart_id,
                        level,
                        issue.get("code"),
                        issue.get("path"),
                        issue.get("message"),
                    )
                )
                + " |"
            )
    return "\n".join(lines) + "\n"
