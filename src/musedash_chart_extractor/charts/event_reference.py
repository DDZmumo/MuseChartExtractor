"""Strict comparison for provenance-bearing, complete indexed event references."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

EVENT_REFERENCE_SCHEMA_VERSION = "event-reference-v1"
EVENT_REFERENCE_SCOPE = "complete-indexed-sequence"
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


class EventReferenceInputError(ValueError):
    """Raised when an event reference is malformed or ambiguous."""


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _decimal(value: Any, *, path: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_PATTERN.fullmatch(value):
        raise EventReferenceInputError(
            f"{path}: expected a finite JSON-style decimal encoded as an exact string"
        )
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise EventReferenceInputError(f"{path}: cannot parse decimal") from exc
    if not number.is_finite():
        raise EventReferenceInputError(f"{path}: decimal must be finite")
    return number


def _not_compared_differences(reason: str) -> dict[str, dict[str, Any]]:
    return {
        category: {
            "status": "not_compared",
            "count": None,
            "details": [],
            "reason": reason,
        }
        for category in DIFFERENCE_CATEGORIES
    }


def _difference_result(
    *,
    status: str,
    count: int | None,
    details: list[dict[str, Any]] | None = None,
    compared_count: int | None = None,
) -> dict[str, Any]:
    rows = details or []
    result: dict[str, Any] = {
        "status": status,
        "count": count,
        "details": rows[:10],
    }
    if compared_count is not None:
        result["compared_count"] = compared_count
    if count is not None:
        result["details_truncated"] = max(0, count - len(result["details"]))
    return result


def _normalize_reference(payload: Mapping[str, Any]) -> tuple[
    list[dict[str, Any]],
    Mapping[str, Any],
    list[str],
    Decimal,
    Decimal,
]:
    if payload.get("schema_version") != EVENT_REFERENCE_SCHEMA_VERSION:
        raise EventReferenceInputError(
            "event_reference.schema_version must equal "
            f"{EVENT_REFERENCE_SCHEMA_VERSION!r}"
        )
    if payload.get("scope") != EVENT_REFERENCE_SCOPE:
        raise EventReferenceInputError(
            f"event_reference.scope must equal {EVENT_REFERENCE_SCOPE!r}"
        )
    source = payload.get("source")
    if not isinstance(source, Mapping) or not source:
        raise EventReferenceInputError(
            "event_reference.source must be a non-empty provenance object"
        )
    source_kind = source.get("kind")
    if not isinstance(source_kind, str) or not source_kind:
        raise EventReferenceInputError(
            "event_reference.source.kind must be a non-empty string"
        )
    rows = payload.get("events")
    if not isinstance(rows, list):
        raise EventReferenceInputError("event_reference.events must be an array")

    time_tolerance = _decimal(
        payload.get("time_tolerance_sec", "0"),
        path="event_reference.time_tolerance_sec",
    )
    duration_tolerance = _decimal(
        payload.get("duration_tolerance_sec", "0"),
        path="event_reference.duration_tolerance_sec",
    )
    if time_tolerance < 0:
        raise EventReferenceInputError(
            "event_reference.time_tolerance_sec cannot be negative"
        )
    if duration_tolerance < 0:
        raise EventReferenceInputError(
            "event_reference.duration_tolerance_sec cannot be negative"
        )

    normalized: list[dict[str, Any]] = []
    optional_fields = ("type_id", "is_air", "duration_sec")
    supplied_fields = {field: False for field in optional_fields}
    for position, row in enumerate(rows):
        path = f"event_reference.events[{position}]"
        if not isinstance(row, Mapping):
            raise EventReferenceInputError(f"{path} must be an object")
        index = row.get("index")
        if not _is_integer(index) or index != position:
            raise EventReferenceInputError(
                "event_reference event indices must be contiguous integers starting at 0"
            )
        item: dict[str, Any] = {
            "index": index,
            "time_sec": _decimal(row.get("time_sec"), path=f"{path}.time_sec"),
        }
        if "type_id" in row:
            type_id = row.get("type_id")
            if type_id is not None and not _is_integer(type_id):
                raise EventReferenceInputError(
                    f"{path}.type_id must be an integer or null"
                )
            item["type_id"] = type_id
            supplied_fields["type_id"] = True
        if "is_air" in row:
            is_air = row.get("is_air")
            if not isinstance(is_air, bool):
                raise EventReferenceInputError(f"{path}.is_air must be a boolean")
            item["is_air"] = is_air
            supplied_fields["is_air"] = True
        if "duration_sec" in row:
            duration = row.get("duration_sec")
            if duration is None:
                item["duration_sec"] = None
            else:
                parsed_duration = _decimal(duration, path=f"{path}.duration_sec")
                if parsed_duration < 0:
                    raise EventReferenceInputError(
                        f"{path}.duration_sec cannot be negative"
                    )
                item["duration_sec"] = parsed_duration
            supplied_fields["duration_sec"] = True
        normalized.append(item)

    compared_fields = ["time_sec"]
    compared_fields.extend(
        field for field in optional_fields if supplied_fields[field]
    )
    return normalized, source, compared_fields, time_tolerance, duration_tolerance


def compare_event_reference(
    chart_id: str | None,
    offline_events: Sequence[Mapping[str, Any]],
    structural_valid: bool,
    reference: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate and compare one optional complete indexed event reference."""

    if reference is None or "event_reference" not in reference:
        reason = "no event-level reference was supplied"
        return (
            {
                "status": "not_provided",
                "schema_version": None,
                "scope": "none",
                "source": None,
                "reference_event_count": None,
                "offline_event_count": len(offline_events),
                "compared_fields": [],
                "claim_limit": reason,
            },
            _not_compared_differences(reason),
        )

    payload = reference.get("event_reference")
    if not isinstance(payload, Mapping):
        raise EventReferenceInputError("event_reference must be an object")
    if reference.get("chart_id") != chart_id:
        reason = "event reference chart_id does not match canonical chart_id"
        return (
            {
                "status": "invalid_reference",
                "schema_version": payload.get("schema_version"),
                "scope": payload.get("scope"),
                "source": payload.get("source"),
                "reference_event_count": None,
                "offline_event_count": len(offline_events),
                "compared_fields": [],
                "reason": reason,
                "claim_limit": reason,
            },
            _not_compared_differences(reason),
        )

    normalized, source, compared_fields, time_tolerance, duration_tolerance = (
        _normalize_reference(payload)
    )
    base_result = {
        "schema_version": EVENT_REFERENCE_SCHEMA_VERSION,
        "scope": EVENT_REFERENCE_SCOPE,
        "source": dict(source),
        "reference_event_count": len(normalized),
        "offline_event_count": len(offline_events),
        "compared_fields": compared_fields,
        "time_tolerance_sec": format(time_tolerance, "f"),
        "duration_tolerance_sec": format(duration_tolerance, "f"),
    }
    if not structural_valid:
        reason = "canonical chart is structurally invalid"
        return (
            {**base_result, "status": "not_compared", "claim_limit": reason},
            _not_compared_differences(reason),
        )

    common_count = min(len(normalized), len(offline_events))
    missing_details = [
        {"reference_index": index}
        for index in range(len(offline_events), len(normalized))
    ]
    extra_details = [
        {"offline_index": index}
        for index in range(len(normalized), len(offline_events))
    ]
    timing_details: list[dict[str, Any]] = []
    type_details: list[dict[str, Any]] = []
    lane_details: list[dict[str, Any]] = []
    duration_details: list[dict[str, Any]] = []
    type_compared = 0
    lane_compared = 0
    duration_compared = 0
    matched_count = 0

    for index in range(common_count):
        expected = normalized[index]
        offline = offline_events[index]
        row_matched = True
        offline_time = Decimal(str(offline.get("time_sec")))
        time_delta = offline_time - expected["time_sec"]
        if abs(time_delta) > time_tolerance:
            row_matched = False
            timing_details.append(
                {
                    "index": index,
                    "offline_sec": format(offline_time, "f"),
                    "reference_sec": format(expected["time_sec"], "f"),
                    "offline_minus_reference_sec": format(time_delta, "f"),
                }
            )

        if "type_id" in expected:
            type_compared += 1
            if offline.get("type_id") != expected["type_id"]:
                row_matched = False
                type_details.append(
                    {
                        "index": index,
                        "offline_type_id": offline.get("type_id"),
                        "reference_type_id": expected["type_id"],
                    }
                )
        if "is_air" in expected:
            lane_compared += 1
            if offline.get("is_air") is not expected["is_air"]:
                row_matched = False
                lane_details.append(
                    {
                        "index": index,
                        "offline_is_air": offline.get("is_air"),
                        "reference_is_air": expected["is_air"],
                    }
                )
        if "duration_sec" in expected:
            duration_compared += 1
            offline_duration_value = offline.get("duration_sec")
            expected_duration = expected["duration_sec"]
            if offline_duration_value is None or expected_duration is None:
                duration_matches = (
                    offline_duration_value is None and expected_duration is None
                )
                duration_delta = None
            else:
                offline_duration = Decimal(str(offline_duration_value))
                duration_delta = offline_duration - expected_duration
                duration_matches = abs(duration_delta) <= duration_tolerance
            if not duration_matches:
                row_matched = False
                duration_details.append(
                    {
                        "index": index,
                        "offline_sec": offline_duration_value,
                        "reference_sec": (
                            None
                            if expected_duration is None
                            else format(expected_duration, "f")
                        ),
                        "offline_minus_reference_sec": (
                            None if duration_delta is None else format(duration_delta, "f")
                        ),
                    }
                )
        if row_matched:
            matched_count += 1

    supplied_fields = set(compared_fields)
    differences = {
        "matched": _difference_result(
            status="compared", count=matched_count, compared_count=common_count
        ),
        "missing_offline": _difference_result(
            status="compared", count=len(missing_details), details=missing_details
        ),
        "extra_offline": _difference_result(
            status="compared", count=len(extra_details), details=extra_details
        ),
        "timing_delta": _difference_result(
            status="compared",
            count=len(timing_details),
            details=timing_details,
            compared_count=common_count,
        ),
        "type_mismatch": _difference_result(
            status="compared" if "type_id" in supplied_fields else "not_compared",
            count=len(type_details) if "type_id" in supplied_fields else None,
            details=type_details,
            compared_count=type_compared if "type_id" in supplied_fields else None,
        ),
        "lane_mismatch": _difference_result(
            status="compared" if "is_air" in supplied_fields else "not_compared",
            count=len(lane_details) if "is_air" in supplied_fields else None,
            details=lane_details,
            compared_count=lane_compared if "is_air" in supplied_fields else None,
        ),
        "duration_delta": _difference_result(
            status=("compared" if "duration_sec" in supplied_fields else "not_compared"),
            count=(len(duration_details) if "duration_sec" in supplied_fields else None),
            details=duration_details,
            compared_count=(
                duration_compared if "duration_sec" in supplied_fields else None
            ),
        ),
    }
    mismatch_count = sum(
        len(rows)
        for rows in (
            missing_details,
            extra_details,
            timing_details,
            type_details,
            lane_details,
            duration_details,
        )
    )
    status = "matched" if mismatch_count == 0 else "mismatch"
    claim_limit = (
        "The complete indexed sequence was compared only for the explicitly supplied "
        f"fields: {', '.join(compared_fields)}. Omitted fields remain unverified."
    )
    return (
        {
            **base_result,
            "status": status,
            "mismatch_count": mismatch_count,
            "claim_limit": claim_limit,
        },
        differences,
    )


__all__ = [
    "DIFFERENCE_CATEGORIES",
    "EVENT_REFERENCE_SCHEMA_VERSION",
    "EVENT_REFERENCE_SCOPE",
    "EventReferenceInputError",
    "compare_event_reference",
]
