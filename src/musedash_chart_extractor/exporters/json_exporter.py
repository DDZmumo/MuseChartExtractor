"""Deterministic canonical JSON exporter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..diagnostics import write_text
from .base import ChartLike, chart_mapping


@dataclass(frozen=True, slots=True)
class JsonExporter:
    """Write the complete canonical model as stable UTF-8 JSON."""

    indent: int | None = 2

    def export(self, chart: ChartLike, destination: str | Path) -> Path:
        if self.indent is not None and self.indent < 0:
            raise ValueError("JSON indentation cannot be negative")
        rendered = chart_mapping(chart)
        separators = (",", ":") if self.indent is None else None
        content = json.dumps(
            rendered,
            ensure_ascii=False,
            indent=self.indent,
            separators=separators,
            sort_keys=True,
        )
        return write_text(destination, f"{content}\n")
