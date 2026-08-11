"""Neutral exporter contract for canonical chart consumers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..charts.models import CanonicalChart

ChartLike = CanonicalChart | Mapping[str, Any]


def chart_mapping(chart: ChartLike) -> Mapping[str, Any]:
    """Return a JSON-compatible canonical mapping without guessing fields."""

    if isinstance(chart, CanonicalChart):
        return chart.to_dict()
    if isinstance(chart, Mapping):
        return chart
    raise TypeError("chart must be a CanonicalChart or mapping")


@runtime_checkable
class ChartExporter(Protocol):
    """Composable destination-oriented chart exporter."""

    def export(self, chart: ChartLike, destination: str | Path) -> Path:
        """Export one chart and return the destination path."""
