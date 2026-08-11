"""Neutral, JSON-serializable canonical chart data model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

CANONICAL_SCHEMA_VERSION = "1.1.0"


@dataclass(frozen=True, slots=True)
class SongIdentity:
    song_id: str
    title: str | None
    artist: str | None
    bpm_raw: Any
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "song_id": self.song_id,
            "title": self.title,
            "artist": self.artist,
            "bpm_raw": deepcopy(self.bpm_raw),
            "raw": deepcopy(self.raw),
        }


@dataclass(frozen=True, slots=True)
class DifficultyIdentity:
    difficulty_id: int
    level_raw: Any
    stage_info_difficulty_raw: Any
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "difficulty_id": self.difficulty_id,
            "level_raw": deepcopy(self.level_raw),
            "stage_info_difficulty_raw": deepcopy(self.stage_info_difficulty_raw),
            "raw": deepcopy(self.raw),
        }


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    extractor_version: str
    bundle: str
    bundle_sha256: str
    container_path: str
    path_id: int
    object_type: str
    payload_sha256: str
    game_fingerprint: str
    catalog_sha256: str | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor_version": self.extractor_version,
            "bundle": self.bundle,
            "bundle_sha256": self.bundle_sha256,
            "container_path": self.container_path,
            "path_id": self.path_id,
            "object_type": self.object_type,
            "payload_sha256": self.payload_sha256,
            "game_fingerprint": self.game_fingerprint,
            "catalog_sha256": self.catalog_sha256,
            "raw": deepcopy(self.raw),
        }


@dataclass(frozen=True, slots=True)
class TimingIdentity:
    time_field: str
    unit: str
    offset_sec: str | None
    bpm_raw: Any
    delay_raw: Any
    status: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_field": self.time_field,
            "unit": self.unit,
            "offset_sec": self.offset_sec,
            "bpm_raw": deepcopy(self.bpm_raw),
            "delay_raw": deepcopy(self.delay_raw),
            "status": self.status,
            "raw": deepcopy(self.raw),
        }


@dataclass(frozen=True, slots=True)
class ChartEvent:
    index: int
    time_sec: str
    end_time_sec: str | None
    duration_sec: str | None
    type_id: int | None
    type_name: str | None
    type_status: str
    is_air: bool | None
    extra: dict[str, Any]
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "time_sec": self.time_sec,
            "end_time_sec": self.end_time_sec,
            "duration_sec": self.duration_sec,
            "type_id": self.type_id,
            "type_name": self.type_name,
            "type_status": self.type_status,
            "is_air": self.is_air,
            "extra": deepcopy(self.extra),
            "raw": deepcopy(self.raw),
        }


@dataclass(frozen=True, slots=True)
class CanonicalChart:
    schema_version: str
    chart_id: str
    song: SongIdentity
    difficulty: DifficultyIdentity
    source: SourceProvenance
    timing: TimingIdentity
    events: tuple[ChartEvent, ...]
    validation_status: str
    canonicalization_status: str
    warnings: tuple[str, ...]
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chart_id": self.chart_id,
            "song": self.song.to_dict(),
            "difficulty": self.difficulty.to_dict(),
            "source": self.source.to_dict(),
            "timing": self.timing.to_dict(),
            "event_count": len(self.events),
            "events": [event.to_dict() for event in self.events],
            "validation_status": self.validation_status,
            "canonicalization_status": self.canonicalization_status,
            "warnings": list(self.warnings),
            "raw": deepcopy(self.raw),
        }
