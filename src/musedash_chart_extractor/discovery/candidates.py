"""Discover and rank StageInfo candidates without decoding chart events.

The implementation is deliberately narrow: Phase 2 established that resolved
``StageInfos/*.asset`` container entries are the highest-value objects in the
current installation.  This module verifies those sources against a fresh
resource inventory, reads only those MonoBehaviours, and retains structural
metadata instead of exporting their serialized payloads.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import UnityPy

from ..scanner import ResourceRecord, ScannerError, validate_game_directory

STAGE_INFO_PATH_FRAGMENT = "/stageinfos/"
STRUCTURAL_SCAN_LIMIT = 64 * 1024
MAX_STRING_SAMPLE = 32
MIN_LARGE_PAYLOAD_BYTES = 4 * 1024
SCORE_VERSION = "stageinfo-signals-v1"
STRUCTURAL_STRING_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.+`\[\], -]{3,159}")
EXPECTED_STAGE_INFO_FIELDS = {
    "m_GameObject",
    "m_Enabled",
    "m_Script",
    "m_Name",
    "serializationData",
    "mapName",
    "music",
    "scene",
    "difficulty",
    "md5",
    "bpm",
    "sceneEvents",
}
EVENT_FIELD_TERMS = {
    "tick",
    "time",
    "type",
    "length",
    "duration",
    "isair",
    "ismul",
    "islongpressing",
    "islongpressend",
}


class CandidateDiscoveryError(ScannerError):
    """Raised when Phase 3 inputs are missing, stale, or malformed."""


def load_bundle_inventory(path: str | Path) -> list[dict[str, Any]]:
    """Load the Phase 2 JSONL artifact with line-specific parse failures."""

    source = Path(path)
    if not source.is_file():
        raise CandidateDiscoveryError(f"bundle inventory does not exist: {source}")

    reports: list[dict[str, Any]] = []
    try:
        with source.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CandidateDiscoveryError(
                        f"invalid bundle inventory JSON at {source}:{line_number}: {exc}"
                    ) from exc
                if not isinstance(value, dict):
                    raise CandidateDiscoveryError(
                        f"bundle inventory row is not an object at {source}:{line_number}"
                    )
                reports.append(value)
    except OSError as exc:
        raise CandidateDiscoveryError(f"cannot read bundle inventory {source}: {exc}") from exc
    return reports


def select_stage_info_sources(
    reports: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select candidates from observed container paths, never filename guesses."""

    selected: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for report in reports:
        if report.get("parseable") is not True:
            continue
        source = report.get("source")
        if not isinstance(source, str) or not source:
            raise CandidateDiscoveryError("parseable bundle inventory row has no source")

        entries = []
        for entry in report.get("container_entries", []):
            if not isinstance(entry, Mapping):
                continue
            path = entry.get("path")
            if (
                entry.get("resolved") is True
                and entry.get("type") == "MonoBehaviour"
                and isinstance(path, str)
                and STAGE_INFO_PATH_FRAGMENT in f"/{path.casefold()}"
            ):
                entries.append(dict(entry))
        if not entries:
            continue
        if source in seen_sources:
            raise CandidateDiscoveryError(f"duplicate source in bundle inventory: {source}")
        seen_sources.add(source)
        entries.sort(key=lambda item: (str(item["path"]).casefold(), str(item["path"])))
        selected.append(
            {
                "source": source,
                "size": report.get("size"),
                "sha256": report.get("sha256"),
                "entries": entries,
            }
        )

    selected.sort(key=lambda item: (item["source"].casefold(), item["source"]))
    return selected


def extract_utf16le_ascii_runs(
    payload: bytes,
    *,
    limit: int = STRUCTURAL_SCAN_LIMIT,
    minimum_characters: int = 4,
) -> list[tuple[int, str]]:
    """Extract bounded printable UTF-16LE runs with byte offsets.

    This is a format-agnostic probe.  It does not claim that every printable
    run is a field name or that the surrounding payload has been decoded.
    """

    window = payload[:limit]
    runs: list[tuple[int, str]] = []
    index = 0
    while index + 1 < len(window):
        if 0x20 <= window[index] <= 0x7E and window[index + 1] == 0:
            start = index
            characters: list[str] = []
            while (
                index + 1 < len(window)
                and 0x20 <= window[index] <= 0x7E
                and window[index + 1] == 0
            ):
                characters.append(chr(window[index]))
                index += 2
            if len(characters) >= minimum_characters:
                runs.append((start, "".join(characters)))
            continue
        index += 1
    return runs


def _payload_entropy(payload: bytes) -> float:
    if not payload:
        return 0.0
    counts = Counter(payload)
    size = len(payload)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def _field_types(value: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): type(field_value).__name__ for key, field_value in value.items()}


def _as_byte_payload(value: Any) -> bytes:
    if not isinstance(value, list):
        raise ValueError("serializationData.SerializedBytes is not a list")
    if any(not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= 255 for item in value):
        raise ValueError("serializationData.SerializedBytes contains a non-byte value")
    return bytes(value)


def _script_identity(data: Mapping[str, Any], objects: Mapping[int, Any]) -> dict[str, Any]:
    pointer = data.get("m_Script")
    if not isinstance(pointer, Mapping):
        return {"resolved": False, "reason": "m_Script is not a pointer object"}
    file_id = pointer.get("m_FileID")
    path_id = pointer.get("m_PathID")
    result: dict[str, Any] = {
        "file_id": file_id,
        "path_id": path_id,
        "resolved": False,
    }
    if file_id != 0 or not isinstance(path_id, int):
        result["reason"] = "m_Script is not a resolvable local pointer"
        return result
    script = objects.get(path_id)
    if script is None:
        result["reason"] = "m_Script PathID is absent from the source"
        return result
    try:
        script_data = script.parse_as_dict()
    except Exception as exc:
        result.update(
            {
                "reason": "MonoScript parse failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return result
    result.update(
        {
            "resolved": True,
            "name": script_data.get("m_Name"),
            "class_name": script_data.get("m_ClassName"),
            "namespace": script_data.get("m_Namespace"),
            "assembly": script_data.get("m_AssemblyName"),
        }
    )
    return result


def _parse_candidate(
    obj: Any,
    *,
    source: Mapping[str, Any],
    entry: Mapping[str, Any],
    objects: Mapping[int, Any],
    inventory_fingerprint: str,
) -> dict[str, Any]:
    data = obj.parse_as_dict()
    if not isinstance(data, Mapping):
        raise ValueError("MonoBehaviour TypeTree result is not an object")
    serialization = data.get("serializationData")
    if not isinstance(serialization, Mapping):
        raise ValueError("MonoBehaviour has no serializationData object")
    payload = _as_byte_payload(serialization.get("SerializedBytes"))

    runs = extract_utf16le_ascii_runs(payload)
    strings = [value for _, value in runs]
    folded_counts = Counter(value.casefold() for value in strings)
    unique_sample: list[dict[str, Any]] = []
    seen: set[str] = set()
    for offset, value in runs:
        folded = value.casefold()
        if (
            folded in seen
            or value != value.strip()
            or STRUCTURAL_STRING_PATTERN.fullmatch(value) is None
        ):
            continue
        seen.add(folded)
        unique_sample.append({"offset": offset, "value": value[:160]})
        if len(unique_sample) == MAX_STRING_SAMPLE:
            break

    scene_events = data.get("sceneEvents")
    metadata = {
        "asset_name": data.get("m_Name"),
        "map_name_raw": data.get("mapName"),
        "music": data.get("music"),
        "scene": data.get("scene"),
        "difficulty_raw": data.get("difficulty"),
        "md5": data.get("md5"),
        "bpm_raw": data.get("bpm"),
        "scene_event_count": len(scene_events) if isinstance(scene_events, list) else None,
    }
    event_field_hits = sorted(EVENT_FIELD_TERMS.intersection(folded_counts))
    repeated_event_field_hits = sorted(
        term for term in event_field_hits if folded_counts[term] >= 2
    )
    music_data_descriptors = sorted(
        {
            value
            for value in strings
            if "gamelogic.musicdata" in value.casefold()
        },
        key=lambda value: (value.casefold(), value),
    )

    return {
        "schema_version": 1,
        "phase": 3,
        "status": "unvalidated_candidate",
        "understanding_status": "candidate-discovered",
        "validation_status": "unvalidated",
        "score_version": SCORE_VERSION,
        "score_interpretation": (
            "fraction of Phase 3 structural signals observed; not a validation probability"
        ),
        "inventory_fingerprint": inventory_fingerprint,
        "source": source["source"],
        "source_size": source["size"],
        "source_sha256": source["sha256"],
        "container_path": entry["path"],
        "path_id": int(obj.path_id),
        "object_type": str(obj.type.name),
        "object_byte_size": int(obj.byte_size),
        "script": _script_identity(data, objects),
        "metadata": metadata,
        "structure": {
            "top_level_field_types": _field_types(data),
            "serialization_field_types": _field_types(serialization),
            "serialized_format": serialization.get("SerializedFormat"),
            "serialized_payload_byte_count": len(payload),
            "serialized_payload_sha256": hashlib.sha256(payload).hexdigest(),
            "serialized_payload_entropy": round(_payload_entropy(payload), 6),
            "structural_scan_byte_count": min(len(payload), STRUCTURAL_SCAN_LIMIT),
            "utf16_run_count_in_scan": len(runs),
            "unique_utf16_string_count_in_scan": len(folded_counts),
            "repeated_utf16_string_count_in_scan": sum(
                count > 1 for count in folded_counts.values()
            ),
            "utf16_string_sample": unique_sample,
            "music_data_type_descriptors": music_data_descriptors[:4],
            "event_field_name_hits": event_field_hits,
            "repeated_event_field_name_hits": repeated_event_field_hits,
            "referenced_unity_object_count": len(
                serialization.get("ReferencedUnityObjects", [])
            )
            if isinstance(serialization.get("ReferencedUnityObjects"), list)
            else None,
            "serialization_node_count": len(serialization.get("SerializationNodes", []))
            if isinstance(serialization.get("SerializationNodes"), list)
            else None,
        },
    }


def _score_candidate(candidate: dict[str, Any], *, sibling_difficulty_count: int) -> None:
    components: list[dict[str, Any]] = []
    counter_evidence: list[str] = []

    def add(signal: str, weight: float, detail: str) -> None:
        components.append({"signal": signal, "weight": weight, "detail": detail})

    path = str(candidate.get("container_path", ""))
    if (
        STAGE_INFO_PATH_FRAGMENT in f"/{path.casefold()}"
        and candidate.get("object_type") == "MonoBehaviour"
    ):
        add(
            "stage_info_container_monobehaviour",
            0.15,
            f"resolved MonoBehaviour under StageInfos: {path}",
        )
    else:
        counter_evidence.append(
            "object is not a resolved MonoBehaviour under a StageInfos directory"
        )

    script = candidate.get("script", {})
    if (
        isinstance(script, Mapping)
        and script.get("resolved") is True
        and script.get("class_name") == "StageInfo"
        and script.get("namespace") == "Assets.Scripts.GameCore"
        and script.get("assembly") == "Assembly-CSharp.dll"
    ):
        add(
            "stage_info_script_identity",
            0.20,
            f"{script.get('namespace')}.StageInfo in {script.get('assembly')}",
        )
    else:
        counter_evidence.append("MonoScript identity was not resolved as StageInfo")

    metadata = candidate.get("metadata", {})
    structure = candidate.get("structure", {})
    top_level_fields = set(structure.get("top_level_field_types", {}))
    if EXPECTED_STAGE_INFO_FIELDS.issubset(top_level_fields):
        add(
            "stage_info_field_shape",
            0.15,
            "TypeTree contains the 12 observed StageInfo top-level fields",
        )
    else:
        counter_evidence.append("TypeTree is missing one or more observed StageInfo fields")

    metadata_valid = (
        isinstance(metadata, Mapping)
        and isinstance(metadata.get("map_name_raw"), str)
        and bool(metadata.get("map_name_raw"))
        and isinstance(metadata.get("music"), str)
        and bool(metadata.get("music"))
        and isinstance(metadata.get("difficulty_raw"), int)
        and not isinstance(metadata.get("difficulty_raw"), bool)
        and isinstance(metadata.get("bpm_raw"), (int, float))
        and not isinstance(metadata.get("bpm_raw"), bool)
        and isinstance(metadata.get("md5"), str)
    )
    if metadata_valid:
        add(
            "chart_metadata_fields",
            0.10,
            "raw mapName/music/difficulty/BPM/MD5 fields have the observed scalar types",
        )
    else:
        counter_evidence.append("one or more chart metadata fields are absent or invalid")

    payload_size = structure.get("serialized_payload_byte_count", 0)
    if isinstance(payload_size, int) and payload_size >= MIN_LARGE_PAYLOAD_BYTES:
        add(
            "large_serialized_payload",
            0.15,
            f"serializationData.SerializedBytes contains {payload_size} bytes",
        )
    else:
        counter_evidence.append(
            f"serialized payload is smaller than {MIN_LARGE_PAYLOAD_BYTES} bytes"
        )

    descriptors = structure.get("music_data_type_descriptors", [])
    if isinstance(descriptors, list) and descriptors:
        add(
            "music_data_list_type_descriptor",
            0.10,
            f"bounded UTF-16 scan contains {descriptors[0]}",
        )
    else:
        counter_evidence.append(
            "bounded UTF-16 scan did not find a GameLogic.MusicData type descriptor"
        )

    repeated_hits = structure.get("repeated_event_field_name_hits", [])
    if isinstance(repeated_hits, list) and len(repeated_hits) >= 2:
        add(
            "repeated_event_field_names",
            0.10,
            f"repeated bounded UTF-16 identifiers: {', '.join(repeated_hits)}",
        )
    else:
        counter_evidence.append(
            "fewer than two event-like identifiers repeat in the bounded UTF-16 scan"
        )

    scene_events_is_list = (
        isinstance(metadata, Mapping)
        and isinstance(metadata.get("scene_event_count"), int)
    )
    if scene_events_is_list:
        add(
            "scene_events_array",
            0.05,
            f"sceneEvents is an array with {metadata.get('scene_event_count')} entries",
        )
    else:
        counter_evidence.append("sceneEvents was not decoded as an array")

    candidate["family_observation"] = {
        "same_music_distinct_difficulty_count": sibling_difficulty_count
    }

    candidate["score"] = round(sum(item["weight"] for item in components), 6)
    candidate["score_components"] = components
    candidate["evidence"] = [item["detail"] for item in components]
    candidate["counter_evidence"] = counter_evidence
    candidate["unverified_signals"] = [
        "serialized event count",
        "monotonic time values",
        "event type value distribution",
        "correspondence with game footage",
    ]


def _failure_candidate(
    *,
    source: Mapping[str, Any],
    entry: Mapping[str, Any],
    inventory_fingerprint: str,
    exc: Exception,
) -> dict[str, Any]:
    candidate = {
        "schema_version": 1,
        "phase": 3,
        "status": "failed",
        "understanding_status": "candidate-unread",
        "validation_status": "unvalidated",
        "score_version": SCORE_VERSION,
        "score_interpretation": (
            "fraction of Phase 3 structural signals observed; not a validation probability"
        ),
        "inventory_fingerprint": inventory_fingerprint,
        "source": source["source"],
        "source_size": source["size"],
        "source_sha256": source["sha256"],
        "container_path": entry["path"],
        "path_id": entry.get("path_id"),
        "object_type": entry.get("type"),
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    _score_candidate(candidate, sibling_difficulty_count=0)
    return candidate


def discover_stage_info_candidates(
    game_dir: str | Path,
    bundle_reports: Iterable[Mapping[str, Any]],
    current_inventory: Iterable[ResourceRecord],
    *,
    inventory_fingerprint: str,
    max_sources: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    loader: Callable[[str], Any] = UnityPy.load,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read only StageInfo objects selected by a verified Phase 2 inventory."""

    root = validate_game_directory(game_dir)
    selected = select_stage_info_sources(bundle_reports)
    available_source_count = len(selected)
    if max_sources is not None:
        selected = selected[:max_sources]

    current_by_path = {record.relative_path: record for record in current_inventory}
    candidates: list[dict[str, Any]] = []
    total = len(selected)
    for index, source in enumerate(selected, start=1):
        relative_path = source["source"]
        current = current_by_path.get(relative_path)
        if current is None:
            raise CandidateDiscoveryError(
                f"Phase 2 source is absent from the current game inventory: {relative_path}"
            )
        if current.size != source["size"] or current.sha256 != source["sha256"]:
            raise CandidateDiscoveryError(
                f"Phase 2 source fingerprint is stale for {relative_path}: "
                f"expected {source['sha256']} ({source['size']} bytes), "
                f"found {current.sha256} ({current.size} bytes)"
            )

        source_path = (root / Path(relative_path)).resolve(strict=True)
        try:
            source_path.relative_to(root)
        except ValueError as exc:
            raise CandidateDiscoveryError(
                f"bundle inventory source escapes game directory: {relative_path}"
            ) from exc

        try:
            environment = loader(str(source_path))
            objects = {int(obj.path_id): obj for obj in environment.objects}
        except Exception as exc:
            candidates.extend(
                _failure_candidate(
                    source=source,
                    entry=entry,
                    inventory_fingerprint=inventory_fingerprint,
                    exc=exc,
                )
                for entry in source["entries"]
            )
        else:
            for entry in source["entries"]:
                try:
                    obj = objects[int(entry["path_id"])]
                    candidates.append(
                        _parse_candidate(
                            obj,
                            source=source,
                            entry=entry,
                            objects=objects,
                            inventory_fingerprint=inventory_fingerprint,
                        )
                    )
                except Exception as exc:
                    candidates.append(
                        _failure_candidate(
                            source=source,
                            entry=entry,
                            inventory_fingerprint=inventory_fingerprint,
                            exc=exc,
                        )
                    )
        if progress is not None:
            progress(index, total, relative_path)

    family_difficulties: dict[str, set[int]] = {}
    for candidate in candidates:
        if candidate["status"] != "unvalidated_candidate":
            continue
        metadata = candidate["metadata"]
        music = metadata.get("music")
        difficulty = metadata.get("difficulty_raw")
        if isinstance(music, str) and isinstance(difficulty, int):
            family_difficulties.setdefault(music, set()).add(difficulty)

    for candidate in candidates:
        metadata = candidate.get("metadata", {})
        music = metadata.get("music") if isinstance(metadata, Mapping) else None
        sibling_count = len(family_difficulties.get(music, set())) if isinstance(music, str) else 0
        _score_candidate(candidate, sibling_difficulty_count=sibling_count)

    candidates.sort(
        key=lambda candidate: (
            -float(candidate["score"]),
            -int(candidate.get("structure", {}).get("serialized_payload_byte_count", 0)),
            str(candidate["source"]).casefold(),
            str(candidate["source"]),
            int(candidate.get("path_id") or 0),
        )
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank

    summary = {
        "schema_version": 1,
        "phase": 3,
        "inventory_fingerprint": inventory_fingerprint,
        "available_source_count": available_source_count,
        "selected_source_count": len(selected),
        "discovery_complete": len(selected) == available_source_count,
        "score_version": SCORE_VERSION,
        "rank_order": [
            "score descending",
            "serialized payload byte count descending",
            "source path ascending",
            "PathID ascending",
        ],
        "candidate_count": sum(
            row["status"] == "unvalidated_candidate" for row in candidates
        ),
        "failed_candidate_count": sum(row["status"] == "failed" for row in candidates),
        "score_distribution": dict(
            sorted(Counter(f"{row['score']:.2f}" for row in candidates).items())
        ),
    }
    return candidates, summary
