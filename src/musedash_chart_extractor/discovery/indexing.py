"""Phase 6 song, difficulty, and chart relationship recovery."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any

import json5
import UnityPy

from ..scanner import ScannerError, fingerprint_file, validate_game_directory

ALBUM_CONTAINER_PATTERN = re.compile(
    r"^Assets/Static Resources/Data/Configs/others/ALBUM(?P<number>[1-9][0-9]*)\.json$"
)
CHART_ID_PATTERN = re.compile(r"^(?P<stem>.+)_map(?P<slot>[1-9][0-9]*)$")
STAGE_INFO_CLASS = "Assets.Scripts.GameCore.StageInfo"
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


class ChartIndexError(ScannerError):
    """Raised when Phase 6 evidence is missing, ambiguous, or stale."""


def _required_string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ChartIndexError(f"{context} must be a non-empty string")
    return value


def _required_int(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChartIndexError(f"{context} must be an integer")
    return value


def _validated_sha256(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ChartIndexError(f"{context} must be a SHA-256 hex string")
    return value.casefold()


def _resolve_source_path(root: Path, relative_path: str) -> Path:
    portable = PurePosixPath(relative_path)
    if portable.is_absolute() or ".." in portable.parts:
        raise ChartIndexError(f"source path escapes game directory: {relative_path!r}")
    source_path = root.joinpath(*portable.parts).resolve()
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise ChartIndexError(
            f"source path escapes game directory: {relative_path!r}"
        ) from exc
    if not source_path.is_file():
        raise ChartIndexError(f"inventoried source does not exist: {source_path}")
    return source_path


def _object_type_name(obj: Any) -> str:
    value = getattr(getattr(obj, "type", None), "name", None)
    return value if isinstance(value, str) else str(value)


def select_album_sources(
    bundle_reports: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select every exact ALBUM<N>.json TextAsset from the Phase 2 inventory."""

    selected: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for report_index, report in enumerate(bundle_reports):
        if not isinstance(report, Mapping):
            raise ChartIndexError(f"bundle inventory row {report_index} is not an object")
        if report.get("parseable") is not True:
            continue
        entries = report.get("container_entries", [])
        if not isinstance(entries, list):
            raise ChartIndexError(
                f"parseable bundle inventory row {report_index} has invalid container_entries"
            )
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            path = entry.get("path")
            match = ALBUM_CONTAINER_PATTERN.fullmatch(path) if isinstance(path, str) else None
            if match is None:
                continue
            if entry.get("resolved") is not True or entry.get("type") != "TextAsset":
                raise ChartIndexError(f"album container is not a resolved TextAsset: {path}")
            number = int(match.group("number"), 10)
            if number in seen_numbers:
                raise ChartIndexError(f"duplicate ALBUM number in inventory: {number}")
            seen_numbers.add(number)
            source = _required_string(
                report.get("source"), context=f"ALBUM{number} source"
            )
            size = _required_int(
                report.get("size"), context=f"ALBUM{number} source size"
            )
            if size < 0:
                raise ChartIndexError(f"ALBUM{number} source size cannot be negative")
            selected.append(
                {
                    "album_number": number,
                    "source": source,
                    "source_byte_count": size,
                    "source_sha256": _validated_sha256(
                        report.get("sha256"), context=f"ALBUM{number} source SHA-256"
                    ),
                    "container_path": path,
                    "path_id": _required_int(
                        entry.get("path_id"), context=f"ALBUM{number} PathID"
                    ),
                    "object_type": "TextAsset",
                }
            )
    if not selected:
        raise ChartIndexError("found no exact ALBUM<N>.json TextAssets")
    return sorted(selected, key=lambda row: row["album_number"])


def _read_album_text(
    root: Path,
    source: Mapping[str, Any],
    *,
    loader: Callable[[str], Any],
) -> tuple[str, dict[str, Any]]:
    source_path = _resolve_source_path(root, str(source["source"]))
    actual_size, actual_sha256, _prefix = fingerprint_file(source_path)
    if actual_size != source["source_byte_count"] or actual_sha256 != source["source_sha256"]:
        raise ChartIndexError(
            f"ALBUM{source['album_number']} source fingerprint is stale: {source['source']}"
        )

    environment = loader(str(source_path))
    matching = [
        obj
        for obj in environment.objects
        if getattr(obj, "path_id", None) == source["path_id"]
    ]
    if len(matching) != 1:
        raise ChartIndexError(
            f"ALBUM{source['album_number']} PathID {source['path_id']} resolved to "
            f"{len(matching)} objects"
        )
    obj = matching[0]
    if _object_type_name(obj) != "TextAsset":
        raise ChartIndexError(
            f"ALBUM{source['album_number']} object type changed: {_object_type_name(obj)}"
        )
    data = obj.read()
    content = getattr(data, "m_Script", None)
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ChartIndexError(
                f"ALBUM{source['album_number']} is not valid UTF-8"
            ) from exc
    if not isinstance(content, str):
        raise ChartIndexError(
            f"ALBUM{source['album_number']} TextAsset has no string m_Script"
        )
    encoded = content.encode("utf-8")
    provenance = {
        **dict(source),
        "content_byte_count": len(encoded),
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    return content, provenance


def read_album_rows(
    game_dir: str | Path,
    album_sources: Sequence[Mapping[str, Any]],
    *,
    loader: Callable[[str], Any] = UnityPy.load,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read JSON5 album rows while preserving every source field and provenance."""

    root = validate_game_directory(game_dir)
    rows: list[dict[str, Any]] = []
    provenances: list[dict[str, Any]] = []
    seen_uids: dict[str, str] = {}
    for source in album_sources:
        content, provenance = _read_album_text(root, source, loader=loader)
        number = provenance["album_number"]
        try:
            parsed = json5.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise ChartIndexError(f"invalid ALBUM{number} JSON5: {exc}") from exc
        if not isinstance(parsed, list):
            raise ChartIndexError(f"ALBUM{number} root must be a list")
        provenance["row_count"] = len(parsed)
        provenances.append(provenance)
        for row_index, value in enumerate(parsed):
            context = f"ALBUM{number} row {row_index}"
            if not isinstance(value, Mapping):
                raise ChartIndexError(f"{context} must be an object")
            raw = dict(value)
            uid = _required_string(raw.get("uid"), context=f"{context} uid")
            music = _required_string(raw.get("music"), context=f"{context} music")
            note_json = _required_string(
                raw.get("noteJson"), context=f"{context} noteJson"
            )
            if uid in seen_uids:
                raise ChartIndexError(
                    f"duplicate song uid {uid!r} in {context}; first seen in {seen_uids[uid]}"
                )
            seen_uids[uid] = context
            rows.append(
                {
                    "song_id": uid,
                    "music_raw": music,
                    "note_json_raw": note_json,
                    "raw": raw,
                    "source": {
                        "album_number": number,
                        "row_index": row_index,
                        **{
                            key: provenance[key]
                            for key in (
                                "source",
                                "source_byte_count",
                                "source_sha256",
                                "container_path",
                                "path_id",
                                "object_type",
                                "content_byte_count",
                                "content_sha256",
                            )
                        },
                    },
                }
            )
    return rows, provenances


def _catalog_stage_entries(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries = catalog.get("entries")
    internal_ids = catalog.get("internal_ids")
    resource_types = catalog.get("resource_types")
    if not isinstance(entries, list) or not isinstance(internal_ids, list):
        raise ChartIndexError("Addressables index has invalid entries/internal_ids")
    if not isinstance(resource_types, list):
        raise ChartIndexError("Addressables index has invalid resource_types")

    stage_type_indices = {
        index
        for index, value in enumerate(resource_types)
        if isinstance(value, Mapping) and value.get("m_ClassName") == STAGE_INFO_CLASS
    }
    if len(stage_type_indices) != 1:
        raise ChartIndexError(
            f"Addressables index has {len(stage_type_indices)} StageInfo resource types"
        )
    stage_type_index = next(iter(stage_type_indices))
    result: dict[str, dict[str, Any]] = {}
    for list_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ChartIndexError(f"Addressables entry {list_index} is not an object")
        if entry.get("resource_type_index") != stage_type_index:
            continue
        entry_index = _required_int(
            entry.get("entry_index"), context=f"Addressables entry {list_index} index"
        )
        if entry_index != list_index:
            raise ChartIndexError(
                f"Addressables entry index mismatch: list {list_index}, stored {entry_index}"
            )
        chart_id = _required_string(
            entry.get("primary_key"), context=f"Addressables StageInfo {entry_index} key"
        )
        if chart_id in result:
            raise ChartIndexError(f"duplicate Addressables StageInfo key: {chart_id!r}")
        dependencies = entry.get("dependency_entry_indices")
        if not isinstance(dependencies, list) or len(dependencies) != 1:
            raise ChartIndexError(
                f"StageInfo {chart_id!r} has {len(dependencies) if isinstance(dependencies, list) else 'invalid'} bundle dependencies"
            )
        dependency_index = _required_int(
            dependencies[0], context=f"StageInfo {chart_id!r} dependency index"
        )
        if not 0 <= dependency_index < len(entries):
            raise ChartIndexError(
                f"StageInfo {chart_id!r} dependency index is out of range"
            )
        dependency = entries[dependency_index]
        if not isinstance(dependency, Mapping):
            raise ChartIndexError(
                f"StageInfo {chart_id!r} dependency entry is not an object"
            )
        internal_id_index = _required_int(
            dependency.get("internal_id_index"),
            context=f"StageInfo {chart_id!r} dependency internal id",
        )
        if not 0 <= internal_id_index < len(internal_ids):
            raise ChartIndexError(
                f"StageInfo {chart_id!r} dependency internal id is out of range"
            )
        internal_id = internal_ids[internal_id_index]
        if not isinstance(internal_id, Mapping):
            raise ChartIndexError(
                f"StageInfo {chart_id!r} dependency internal id is not an object"
            )
        local_path = _required_string(
            internal_id.get("local_path"),
            context=f"StageInfo {chart_id!r} dependency local path",
        )
        result[chart_id] = {
            "entry_index": entry_index,
            "primary_key": chart_id,
            "resource_type_index": stage_type_index,
            "resource_type": STAGE_INFO_CLASS,
            "dependency_entry_index": dependency_index,
            "dependency_primary_key": dependency.get("primary_key"),
            "dependency_local_path": local_path,
        }
    if not result:
        raise ChartIndexError("Addressables index contains no StageInfo entries")
    return result


def _decimal_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def _candidate_chart(
    candidate: Mapping[str, Any],
    *,
    addressables: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], int]:
    metadata = candidate.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ChartIndexError("candidate has invalid metadata")
    chart_id = _required_string(
        metadata.get("asset_name"), context="candidate asset_name"
    )
    match = CHART_ID_PATTERN.fullmatch(chart_id)
    if match is None:
        raise ChartIndexError(f"candidate chart id has no terminal _mapN slot: {chart_id!r}")
    slot = int(match.group("slot"), 10)
    container_path = _required_string(
        candidate.get("container_path"), context=f"candidate {chart_id} container"
    )
    if PurePosixPath(container_path).stem != chart_id:
        raise ChartIndexError(
            f"candidate {chart_id!r} does not match container basename {container_path!r}"
        )
    catalog_entry = addressables.get(chart_id)
    if catalog_entry is None:
        raise ChartIndexError(f"candidate {chart_id!r} has no Addressables StageInfo entry")
    source = _required_string(
        candidate.get("source"), context=f"candidate {chart_id} source"
    )
    if catalog_entry["dependency_local_path"] != source:
        raise ChartIndexError(
            f"candidate {chart_id!r} source differs from Addressables dependency: "
            f"{source!r} != {catalog_entry['dependency_local_path']!r}"
        )
    return (
        {
            "chart_id": chart_id,
            "difficulty_id": slot,
            "difficulty_key": f"difficulty{slot}",
            "song_id": None,
            "relationship_status": "unresolved",
            "relationship_evidence": [],
            "warnings": [],
            "source": {
                "bundle": source,
                "bundle_byte_count": _required_int(
                    candidate.get("source_size"),
                    context=f"candidate {chart_id} source size",
                ),
                "bundle_sha256": _validated_sha256(
                    candidate.get("source_sha256"),
                    context=f"candidate {chart_id} source SHA-256",
                ),
                "container_path": container_path,
                "path_id": _required_int(
                    candidate.get("path_id"), context=f"candidate {chart_id} PathID"
                ),
                "object_type": candidate.get("object_type"),
                "stage_info_md5_raw": metadata.get("md5"),
            },
            "addressables": dict(catalog_entry),
            "stage_info_raw": dict(metadata),
            "difficulty_raw": metadata.get("difficulty_raw"),
            "difficulty_level_raw": None,
        },
        slot,
    )


def _crosscheck_warnings(
    chart: Mapping[str, Any], album: Mapping[str, Any]
) -> list[str]:
    warnings: list[str] = []
    stage = chart["stage_info_raw"]
    raw = album["raw"]
    if stage.get("music") != raw.get("music"):
        warnings.append("stage_info_music_differs_from_album")
    if stage.get("scene") != raw.get("scene"):
        warnings.append("stage_info_scene_differs_from_album")
    if not _decimal_equal(stage.get("bpm_raw"), raw.get("bpm")):
        warnings.append("stage_info_bpm_differs_from_album")
    if stage.get("difficulty_raw") != chart["difficulty_id"]:
        warnings.append("stage_info_difficulty_raw_differs_from_map_slot")
    map_name = stage.get("map_name_raw")
    if isinstance(map_name, str) and (":\\" in map_name or map_name.startswith("/")):
        warnings.append("stage_info_map_name_is_absolute_development_path")
    return warnings


def build_song_chart_index(
    candidates: Sequence[Mapping[str, Any]],
    album_rows: Sequence[Mapping[str, Any]],
    album_provenance: Sequence[Mapping[str, Any]],
    addressables_index: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the deterministic Phase 6 index without hand-written PathID maps."""

    stage_entries = _catalog_stage_entries(addressables_index)
    exact_rows_by_chart: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    rows_by_music: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    rows_by_uid: dict[str, Mapping[str, Any]] = {}
    for album in album_rows:
        uid = _required_string(album.get("song_id"), context="album song_id")
        if uid in rows_by_uid:
            raise ChartIndexError(f"duplicate normalized album song id: {uid!r}")
        rows_by_uid[uid] = album
        music = _required_string(album.get("music_raw"), context=f"song {uid} music")
        note_json = _required_string(
            album.get("note_json_raw"), context=f"song {uid} noteJson"
        )
        rows_by_music[music].append(album)
        raw = album.get("raw")
        if not isinstance(raw, Mapping):
            raise ChartIndexError(f"song {uid} raw metadata is invalid")
        for key in raw:
            match = re.fullmatch(r"difficulty([1-9][0-9]*)", str(key))
            if match is not None:
                exact_rows_by_chart[f"{note_json}{match.group(1)}"].append(album)

    normalized_charts: list[dict[str, Any]] = []
    inventory_fingerprints: set[str] = set()
    seen_chart_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ChartIndexError("candidate row is not an object")
        if candidate.get("status") != "unvalidated_candidate":
            raise ChartIndexError(
                f"candidate row has unsupported status: {candidate.get('status')!r}"
            )
        inventory_fingerprints.add(
            _required_string(
                candidate.get("inventory_fingerprint"),
                context="candidate inventory fingerprint",
            )
        )
        chart, slot = _candidate_chart(candidate, addressables=stage_entries)
        chart_id = chart["chart_id"]
        if chart_id in seen_chart_ids:
            raise ChartIndexError(f"duplicate candidate chart id: {chart_id!r}")
        seen_chart_ids.add(chart_id)
        exact = exact_rows_by_chart.get(chart_id, [])
        if len(exact) == 1:
            album = exact[0]
            chart["song_id"] = album["song_id"]
            chart["relationship_status"] = "exact-note-json"
            chart["relationship_evidence"] = [
                "Addressables primary key equals StageInfo container basename",
                f"album.noteJson + {slot} equals chart_id",
                f"album contains difficulty{slot}",
            ]
        elif len(exact) > 1:
            chart["unresolved_reason"] = "ambiguous exact album noteJson relationship"
            normalized_charts.append(chart)
            continue
        else:
            stage_music = chart["stage_info_raw"].get("music")
            fallback = rows_by_music.get(stage_music, []) if isinstance(stage_music, str) else []
            if len(fallback) == 1:
                album = fallback[0]
                chart["song_id"] = album["song_id"]
                chart["relationship_status"] = "unique-music-fallback"
                chart["relationship_evidence"] = [
                    "no album.noteJson relationship",
                    "StageInfo music matches exactly one album row",
                ]
                chart["warnings"].append("song_link_uses_music_fallback")
            else:
                chart["unresolved_reason"] = (
                    "no exact album noteJson relationship and music fallback count is "
                    f"{len(fallback)}"
                )
                normalized_charts.append(chart)
                continue

        chart["difficulty_level_raw"] = album["raw"].get(chart["difficulty_key"])
        chart["warnings"].extend(_crosscheck_warnings(chart, album))
        normalized_charts.append(chart)

    if len(inventory_fingerprints) != 1:
        raise ChartIndexError(
            f"candidate rows contain {len(inventory_fingerprints)} inventory fingerprints"
        )
    if len(stage_entries) != len(normalized_charts):
        missing = sorted(set(stage_entries) - seen_chart_ids)
        extra = sorted(seen_chart_ids - set(stage_entries))
        raise ChartIndexError(
            "candidate/Addressables StageInfo census differs: "
            f"catalog={len(stage_entries)}, candidates={len(normalized_charts)}, "
            f"catalog_only={missing[:5]}, candidate_only={extra[:5]}"
        )

    charts_by_song: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    relationship_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    for chart in normalized_charts:
        relationship_counts[chart["relationship_status"]] += 1
        warning_counts.update(chart["warnings"])
        if chart["song_id"] is None:
            unresolved.append(chart)
        else:
            charts_by_song[chart["song_id"]].append(chart)

    songs: list[dict[str, Any]] = []
    for song_id in sorted(rows_by_uid, key=lambda value: (value.casefold(), value)):
        album = rows_by_uid[song_id]
        charts = sorted(
            charts_by_song.get(song_id, []),
            key=lambda chart: (chart["difficulty_id"], chart["chart_id"]),
        )
        songs.append(
            {
                "song_id": song_id,
                "metadata": {
                    "title_raw": album["raw"].get("name"),
                    "artist_raw": album["raw"].get("author"),
                    "bpm_raw": album["raw"].get("bpm"),
                    "music_raw": album["music_raw"],
                    "note_json_raw": album["note_json_raw"],
                    "scene_raw": album["raw"].get("scene"),
                    "raw": dict(album["raw"]),
                },
                "source": dict(album["source"]),
                "chart_count": len(charts),
                "charts": charts,
            }
        )

    source = addressables_index.get("source")
    if not isinstance(source, Mapping):
        raise ChartIndexError("Addressables index has invalid source provenance")
    counts = {
        "album_source_count": len(album_provenance),
        "album_row_count": len(album_rows),
        "song_count": len(songs),
        "candidate_chart_count": len(normalized_charts),
        "indexed_chart_count": len(normalized_charts) - len(unresolved),
        "unresolved_chart_count": len(unresolved),
        "songs_with_at_least_two_indexed_charts": sum(
            song["chart_count"] >= 2 for song in songs
        ),
        "relationship_status_counts": dict(sorted(relationship_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
    }
    phase_gate_passed = counts["songs_with_at_least_two_indexed_charts"] >= 3
    return {
        "schema_version": 1,
        "phase": 6,
        "status": "indexed-with-unresolved" if unresolved else "indexed",
        "milestone_status": "M5-achieved" if phase_gate_passed else "M5-not-achieved",
        "inventory_fingerprint": next(iter(inventory_fingerprints)),
        "catalog": {
            "source": dict(source),
            "addressables_version": addressables_index.get("addressables_version"),
            "build_result_hash": addressables_index.get("build_result_hash"),
            "stage_info_resource_type": STAGE_INFO_CLASS,
            "stage_info_entry_count": len(stage_entries),
        },
        "id_rules": {
            "song_id": "ALBUM row uid",
            "chart_id": "Addressables StageInfo primary key, cross-checked with container basename",
            "difficulty_id": "integer terminal map slot from chart_id _mapN",
            "stage_info_md5": "content identifier only, not the logical chart id",
            "stage_info_difficulty_raw": "retained raw metadata, not the difficulty id",
        },
        "counts": counts,
        "phase_gate": {
            "required_songs": 3,
            "required_charts_per_song": 2,
            "passed": phase_gate_passed,
        },
        "album_sources": [dict(value) for value in album_provenance],
        "songs": songs,
        "unresolved_charts": sorted(
            unresolved, key=lambda chart: (chart["chart_id"].casefold(), chart["chart_id"])
        ),
    }
