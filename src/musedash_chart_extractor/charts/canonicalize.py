"""Lossless projection from the Phase 5/6 evidence documents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from .. import __version__
from ..scanner import ScannerError
from .models import (
    CANONICAL_SCHEMA_VERSION,
    CanonicalChart,
    ChartEvent,
    DifficultyIdentity,
    SongIdentity,
    SourceProvenance,
    TimingIdentity,
)

NOTE_TYPE_NAMES = {
    0: "None",
    1: "Monster",
    2: "Block",
    3: "Press",
    4: "Hide",
    5: "Boss",
    6: "Hp",
    7: "Music",
    8: "Mul",
    9: "SceneChange",
    10: "AutoOn",
    11: "AutoOff",
    12: "DisappearOn",
    13: "DisappearOff",
    14: "DisappearBossOn",
    15: "DisappearBossOff",
    16: "SceneHideOn",
    17: "SceneHideOff",
}
_DURATION_TYPES = {3, 8}
CANONICAL_RAW_LAYOUT_STRATEGY = "single-raw-record-table-v1"
_OMITTED_DERIVED_FIELDS = ["raw.experimental_chart.logical_objects"]


class CanonicalizationError(ScannerError):
    """Raised when Phase 5/6 evidence cannot be projected without guessing."""


def _logical_objects_from_record_groups(
    experimental_chart: Mapping[str, Any],
) -> list[dict[str, Any]]:
    groups = _sequence(
        experimental_chart.get("record_groups"),
        context="experimental record_groups",
    )
    logical_objects: list[dict[str, Any]] = []
    for position, value in enumerate(groups):
        group = _mapping(value, context=f"experimental record group {position}")
        role = group.get("role_status")
        if role == "observed-sentinel":
            continue
        if role != "logical-gameplay-object":
            raise CanonicalizationError(
                f"experimental record group {position} has unknown role {role!r}"
            )
        logical_objects.append(
            {"index": len(logical_objects), **deepcopy(dict(group))}
        )
    expected = _integer(
        experimental_chart.get("logical_object_count"),
        context="experimental logical_object_count",
    )
    if len(logical_objects) != expected:
        raise CanonicalizationError(
            "reconstructed logical object count differs from experimental evidence"
        )
    return logical_objects


def reconstruct_experimental_chart(
    canonical_raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconstruct the exact Phase 5 mapping from deduplicated canonical raw data."""

    raw = _mapping(canonical_raw, context="canonical raw")
    layout = _mapping(raw.get("layout"), context="canonical raw layout")
    if layout.get("strategy") != CANONICAL_RAW_LAYOUT_STRATEGY:
        raise CanonicalizationError(
            f"unsupported canonical raw layout strategy: {layout.get('strategy')!r}"
        )
    if layout.get("omitted_derived_fields") != _OMITTED_DERIVED_FIELDS:
        raise CanonicalizationError("canonical raw layout omitted fields differ")
    experimental = deepcopy(
        dict(_mapping(raw.get("experimental_chart"), context="experimental chart"))
    )
    if "logical_objects" in experimental:
        raise CanonicalizationError(
            "deduplicated experimental evidence still embeds logical_objects"
        )
    experimental["logical_objects"] = _logical_objects_from_record_groups(
        experimental
    )
    return experimental


def _mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanonicalizationError(f"{context} must be an object")
    return value


def _sequence(value: Any, *, context: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CanonicalizationError(f"{context} must be an array")
    return value


def _string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise CanonicalizationError(f"{context} must be a non-empty string")
    return value


def _integer(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CanonicalizationError(f"{context} must be an integer")
    return value


def _decimal_text(value: Any, *, context: str) -> tuple[str, Decimal]:
    row = _mapping(value, context=context)
    text = row.get("text")
    if not isinstance(text, str):
        raise CanonicalizationError(f"{context} has no exact decimal text")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise CanonicalizationError(f"{context} has invalid decimal text: {text!r}") from exc
    return text, number


def _find_index_entry(
    index: Mapping[str, Any], chart_id: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for song_value in _sequence(index.get("songs"), context="song index songs"):
        song = _mapping(song_value, context="song index song")
        for chart_value in _sequence(song.get("charts"), context="song index charts"):
            chart = _mapping(chart_value, context="song index chart")
            if chart.get("chart_id") == chart_id:
                matches.append((song, chart))
    if len(matches) != 1:
        raise CanonicalizationError(
            f"chart id {chart_id!r} resolved to {len(matches)} indexed song/chart rows"
        )
    return matches[0]


def _validated_status(
    experimental_chart: Mapping[str, Any],
    validation_report: Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    inherited = experimental_chart.get("validation_status")
    status = inherited if isinstance(inherited, str) else "unvalidated"
    if validation_report is None:
        return status, None
    if validation_report.get("milestone_status") != "M4-achieved":
        raise CanonicalizationError("validation report has not achieved M4")
    report_chart = _mapping(
        validation_report.get("chart"), context="validation report chart"
    )
    source = _mapping(experimental_chart.get("source"), context="chart source")
    checks = (
        ("bundle", source.get("bundle")),
        ("bundle_sha256", source.get("bundle_sha256")),
        ("path_id", source.get("path_id")),
        ("payload_sha256", source.get("payload_sha256")),
    )
    for field, actual in checks:
        if report_chart.get(field) != actual:
            raise CanonicalizationError(
                f"validation report {field} does not match experimental chart"
            )
    report_status = validation_report.get("status")
    if not isinstance(report_status, str) or not report_status:
        raise CanonicalizationError("validation report has no status")
    return report_status, deepcopy(dict(validation_report))


def _field_value(record: Mapping[str, Any], name: str) -> Any:
    fields = _mapping(record.get("fields"), context="MusicData fields")
    field = _mapping(fields.get(name), context=f"MusicData.{name}")
    return field.get("value")


def _config_value(record: Mapping[str, Any], name: str) -> Any:
    fields = _mapping(record.get("fields"), context="MusicData fields")
    config = _mapping(fields.get("configData"), context="MusicData.configData")
    config_fields = _mapping(config.get("fields"), context="MusicData.configData fields")
    field = _mapping(config_fields.get(name), context=f"MusicData.configData.{name}")
    return field.get("value")


def _event_from_group(
    logical: Mapping[str, Any],
    raw_records: Mapping[int, Mapping[str, Any]],
    *,
    expected_index: int,
) -> ChartEvent:
    index = _integer(logical.get("index"), context="logical object index")
    if index != expected_index:
        raise CanonicalizationError(
            f"logical object index mismatch: expected {expected_index}, got {index}"
        )
    base_index = _integer(
        logical.get("base_raw_record_index"), context=f"logical object {index} base index"
    )
    member_values = _sequence(
        logical.get("raw_record_indices"), context=f"logical object {index} raw indices"
    )
    member_indices = [
        _integer(value, context=f"logical object {index} raw index")
        for value in member_values
    ]
    if len(member_indices) != len(set(member_indices)):
        raise CanonicalizationError(
            f"logical object {index} contains duplicate raw record indices"
        )
    if base_index not in member_indices:
        raise CanonicalizationError(
            f"logical object {index} base index is absent from its raw record group"
        )
    try:
        base = raw_records[base_index]
        for value in member_indices:
            raw_records[value]
    except KeyError as exc:
        raise CanonicalizationError(
            f"logical object {index} references missing raw record {exc.args[0]}"
        ) from exc

    time_text, time_value = _decimal_text(
        _config_value(base, "time"), context=f"event {index} configData.time"
    )
    raw_type = _mapping(logical.get("raw_type"), context=f"event {index} raw_type")
    type_id_value = raw_type.get("id")
    type_id = (
        None
        if type_id_value is None
        else _integer(type_id_value, context=f"event {index} type id")
    )
    type_name = NOTE_TYPE_NAMES.get(type_id) if type_id is not None else None
    type_status = "known" if type_name is not None else "unknown"

    duration_text: str | None = None
    end_time_text: str | None = None
    length_text, length_value = _decimal_text(
        _config_value(base, "length"), context=f"event {index} configData.length"
    )
    if type_id in _DURATION_TYPES:
        if length_value < 0:
            raise CanonicalizationError(f"event {index} has negative duration")
        duration_text = length_text
        end_time_text = format(time_value + length_value, "f")

    same_tick = _field_value(base, "sameTickNoteIdx")
    extra = {
        "config_id_raw": logical.get("config_id_raw"),
        "note_uid_raw": _config_value(base, "note_uid"),
        "pathway_raw": _config_value(base, "pathway"),
        "tick_raw": _field_value(base, "tick"),
        "length_raw": _config_value(base, "length"),
        "obj_id_raw": _field_value(base, "objId"),
        "is_double_raw": _field_value(base, "isDouble"),
        "double_index_raw": _field_value(base, "doubleIdx"),
        "same_tick_note_indices_raw": deepcopy(same_tick),
        "long_pressing_record_count": len(
            _sequence(
                logical.get("long_pressing_raw_record_indices"),
                context=f"event {index} pressing indices",
            )
        ),
        "long_press_end_record_count": len(
            _sequence(
                logical.get("long_press_end_raw_record_indices"),
                context=f"event {index} end indices",
            )
        ),
    }
    return ChartEvent(
        index=index,
        time_sec=time_text,
        end_time_sec=end_time_text,
        duration_sec=duration_text,
        type_id=type_id,
        type_name=type_name,
        type_status=type_status,
        is_air=None,
        extra=extra,
        raw={
            "base_raw_record_index": base_index,
            "raw_record_indices": deepcopy(member_indices),
        },
    )


def canonicalize_chart(
    experimental_chart: Mapping[str, Any],
    song_chart_index: Mapping[str, Any],
    validation_report: Mapping[str, Any] | None = None,
) -> CanonicalChart:
    """Convert one indexed Phase 5 chart without discarding recovered evidence."""

    source_raw = _mapping(experimental_chart.get("source"), context="chart source")
    chart_id = _string(source_raw.get("asset_name"), context="chart asset name")
    song_row, chart_row = _find_index_entry(song_chart_index, chart_id)
    relationship_status = chart_row.get("relationship_status")
    if relationship_status not in {"exact-note-json", "unique-music-fallback"}:
        raise CanonicalizationError(
            f"chart {chart_id!r} has unsupported index relationship {relationship_status!r}"
        )

    raw_record_rows = _sequence(
        experimental_chart.get("raw_records"), context="experimental raw_records"
    )
    raw_records: dict[int, Mapping[str, Any]] = {}
    for row_value in raw_record_rows:
        row = _mapping(row_value, context="experimental raw record")
        raw_index = _integer(row.get("index"), context="experimental raw record index")
        if raw_index in raw_records:
            raise CanonicalizationError(f"duplicate raw record index: {raw_index}")
        raw_records[raw_index] = _mapping(
            row.get("raw"), context=f"experimental raw record {raw_index} payload"
        )

    logical_rows = _sequence(
        experimental_chart.get("logical_objects"), context="experimental logical_objects"
    )
    events = tuple(
        _event_from_group(
            _mapping(value, context=f"logical object {index}"),
            raw_records,
            expected_index=index,
        )
        for index, value in enumerate(logical_rows)
    )
    for previous, current in zip(events, events[1:]):
        if Decimal(current.time_sec) < Decimal(previous.time_sec):
            raise CanonicalizationError(
                f"canonical event time descends at {previous.index}->{current.index}"
            )

    song_metadata = _mapping(song_row.get("metadata"), context="indexed song metadata")
    difficulty_id = _integer(
        chart_row.get("difficulty_id"), context="indexed difficulty id"
    )
    index_catalog = _mapping(song_chart_index.get("catalog"), context="index catalog")
    catalog_source = _mapping(index_catalog.get("source"), context="index catalog source")
    object_type = _string(source_raw.get("object_type"), context="chart object type")
    provenance = SourceProvenance(
        extractor_version=__version__,
        bundle=_string(source_raw.get("bundle"), context="source bundle"),
        bundle_sha256=_string(
            source_raw.get("bundle_sha256"), context="source bundle SHA-256"
        ),
        container_path=_string(
            source_raw.get("container_path"), context="source container"
        ),
        path_id=_integer(source_raw.get("path_id"), context="source PathID"),
        object_type=object_type,
        payload_sha256=_string(
            source_raw.get("payload_sha256"), context="source payload SHA-256"
        ),
        game_fingerprint=_string(
            source_raw.get("inventory_fingerprint"), context="game fingerprint"
        ),
        catalog_sha256=(
            catalog_source.get("catalog_sha256")
            if isinstance(catalog_source.get("catalog_sha256"), str)
            else None
        ),
        raw={
            "experimental_source": deepcopy(dict(source_raw)),
            "indexed_source": deepcopy(chart_row.get("source")),
            "addressables": deepcopy(chart_row.get("addressables")),
        },
    )
    trailing_fields = _mapping(
        _mapping(experimental_chart.get("raw_stream"), context="raw stream").get(
            "trailing_fields"
        ),
        context="raw stream trailing fields",
    )
    timing = TimingIdentity(
        time_field="musicDatas[].configData.time",
        unit="seconds",
        offset_sec=None,
        bpm_raw=deepcopy(song_metadata.get("bpm_raw")),
        delay_raw=deepcopy(trailing_fields.get("delay")),
        status="sample-validated-for-selected-chart",
        raw={
            "experimental_parser": deepcopy(experimental_chart.get("parser")),
            "experimental_grouping": deepcopy(experimental_chart.get("grouping")),
            "trailing_fields": deepcopy(dict(trailing_fields)),
        },
    )
    warnings = list(chart_row.get("warnings", []))
    warnings.append("is_air_not_yet_canonicalized")
    warnings.append("duration_only_interpreted_for_static_types_3_and_8")
    validation_status, retained_validation = _validated_status(
        experimental_chart,
        validation_report,
    )
    timing_status = (
        "sample-validated-for-selected-chart"
        if retained_validation is not None
        else "raw-time-field-not-video-validated"
    )
    timing = TimingIdentity(
        time_field=timing.time_field,
        unit=timing.unit,
        offset_sec=timing.offset_sec,
        bpm_raw=timing.bpm_raw,
        delay_raw=timing.delay_raw,
        status=timing_status,
        raw=timing.raw,
    )
    retained_experimental = deepcopy(dict(experimental_chart))
    retained_experimental.pop("logical_objects")
    retained_raw = {
        "layout": {
            "strategy": CANONICAL_RAW_LAYOUT_STRATEGY,
            "raw_record_table": "raw.experimental_chart.raw_records",
            "event_record_references": "events[].raw.raw_record_indices",
            "omitted_derived_fields": deepcopy(_OMITTED_DERIVED_FIELDS),
        },
        "experimental_chart": retained_experimental,
        "indexed_song": deepcopy(dict(song_row)),
        "indexed_chart": deepcopy(dict(chart_row)),
    }
    if retained_validation is not None:
        retained_raw["validation_report"] = retained_validation
    if reconstruct_experimental_chart(retained_raw) != dict(experimental_chart):
        raise CanonicalizationError(
            "deduplicated canonical raw evidence cannot reconstruct the Phase 5 input"
        )
    return CanonicalChart(
        schema_version=CANONICAL_SCHEMA_VERSION,
        chart_id=chart_id,
        song=SongIdentity(
            song_id=_string(song_row.get("song_id"), context="indexed song id"),
            title=(
                song_metadata.get("title_raw")
                if isinstance(song_metadata.get("title_raw"), str)
                else None
            ),
            artist=(
                song_metadata.get("artist_raw")
                if isinstance(song_metadata.get("artist_raw"), str)
                else None
            ),
            bpm_raw=deepcopy(song_metadata.get("bpm_raw")),
            raw=deepcopy(dict(song_metadata)),
        ),
        difficulty=DifficultyIdentity(
            difficulty_id=difficulty_id,
            level_raw=deepcopy(chart_row.get("difficulty_level_raw")),
            stage_info_difficulty_raw=deepcopy(chart_row.get("difficulty_raw")),
            raw={
                "difficulty_key": chart_row.get("difficulty_key"),
                "relationship_status": relationship_status,
                "relationship_evidence": deepcopy(
                    chart_row.get("relationship_evidence")
                ),
            },
        ),
        source=provenance,
        timing=timing,
        events=events,
        validation_status=validation_status,
        canonicalization_status="canonicalized-with-raw-evidence",
        warnings=tuple(dict.fromkeys(str(value) for value in warnings)),
        raw=retained_raw,
    )
