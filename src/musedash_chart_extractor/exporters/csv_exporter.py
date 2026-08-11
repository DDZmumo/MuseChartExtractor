"""Flat, explicitly lossy CSV view of canonical chart events."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from ..diagnostics import write_text
from .base import ChartLike, chart_mapping

CSV_COLUMNS = (
    "index",
    "time_sec",
    "time_ms",
    "end_time_sec",
    "end_time_ms",
    "duration_sec",
    "duration_ms",
    "type_id",
    "type_name",
    "is_air",
)


def _milliseconds(value: Any, *, context: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{context} must be an exact decimal string or null")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{context} is not a valid decimal: {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{context} must be finite")
    with localcontext() as context:
        context.prec = max(50, len(number.as_tuple().digits) + 3)
        milliseconds = number * Decimal(1000)
    rendered = format(milliseconds, "f")
    if "." not in rendered:
        return f"{rendered}.000"
    rendered = rendered.rstrip("0").rstrip(".")
    if "." not in rendered:
        return f"{rendered}.000"
    return rendered


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


@dataclass(frozen=True, slots=True)
class CsvExporter:
    """Write core event columns while leaving the canonical source untouched."""

    def export(self, chart: ChartLike, destination: str | Path) -> Path:
        rendered = chart_mapping(chart)
        events = rendered.get("events")
        if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
            raise ValueError("canonical chart events must be an array")

        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for position, event_value in enumerate(events):
            if not isinstance(event_value, Mapping):
                raise ValueError(f"canonical event {position} must be an object")
            time_sec = event_value.get("time_sec")
            end_time_sec = event_value.get("end_time_sec")
            duration_sec = event_value.get("duration_sec")
            writer.writerow(
                {
                    "index": _cell(event_value.get("index")),
                    "time_sec": _cell(time_sec),
                    "time_ms": _milliseconds(time_sec, context=f"event {position} time_sec"),
                    "end_time_sec": _cell(end_time_sec),
                    "end_time_ms": _milliseconds(
                        end_time_sec, context=f"event {position} end_time_sec"
                    ),
                    "duration_sec": _cell(duration_sec),
                    "duration_ms": _milliseconds(
                        duration_sec, context=f"event {position} duration_sec"
                    ),
                    "type_id": _cell(event_value.get("type_id")),
                    "type_name": _cell(event_value.get("type_name")),
                    "is_air": _cell(event_value.get("is_air")),
                }
            )
        return write_text(destination, stream.getvalue())
