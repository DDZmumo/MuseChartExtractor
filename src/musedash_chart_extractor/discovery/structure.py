"""Reproducible, bounded structure recovery for one ranked StageInfo candidate."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import UnityPy

from ..scanner import ScannerError, fingerprint_file, validate_game_directory
from ..unity.odin import OdinParseError, parse_stage_info_payload

MAX_SAMPLE_RECORDS = 10


class StructureRecoveryError(ScannerError):
    """Raised when a selected candidate cannot be verified or recovered."""


def load_ranked_candidate(
    path: str | Path,
    *,
    source: str,
    path_id: int,
) -> dict[str, Any]:
    """Load exactly one source/PathID pair from ``chart_candidates.jsonl``."""

    candidate_path = Path(path)
    if not candidate_path.is_file():
        raise StructureRecoveryError(f"chart candidate file does not exist: {candidate_path}")
    matches: list[dict[str, Any]] = []
    try:
        with candidate_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StructureRecoveryError(
                        f"invalid chart candidate JSON at {candidate_path}:{line_number}: {exc}"
                    ) from exc
                if not isinstance(row, dict):
                    raise StructureRecoveryError(
                        f"chart candidate row is not an object at {candidate_path}:{line_number}"
                    )
                if row.get("source") == source and row.get("path_id") == path_id:
                    matches.append(row)
    except OSError as exc:
        raise StructureRecoveryError(f"cannot read chart candidates {candidate_path}: {exc}") from exc

    if len(matches) != 1:
        raise StructureRecoveryError(
            f"expected exactly one ranked candidate for {source} PathID {path_id}, "
            f"found {len(matches)}"
        )
    candidate = matches[0]
    if candidate.get("status") != "unvalidated_candidate":
        raise StructureRecoveryError(
            f"ranked candidate is not readable: status={candidate.get('status')!r}"
        )
    return candidate


def _decimal_value(field: Mapping[str, Any]) -> Decimal:
    value = field.get("value")
    if not isinstance(value, Mapping) or not isinstance(value.get("text"), str):
        raise StructureRecoveryError("parsed decimal field has no exact text value")
    return Decimal(value["text"])


def _decimal_stats(records: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, Any]:
    values = []
    for record in records:
        current: Any = record
        for component in path:
            current = current[component]
        values.append(_decimal_value(current))
    if not values:
        raise StructureRecoveryError(
            "cannot build time-field hypotheses for an empty musicDatas array"
        )
    comparisons = len(values) - 1
    nondecreasing = sum(left <= right for left, right in zip(values, values[1:]))
    decreases = [
        {
            "index": index,
            "previous": str(values[index - 1]),
            "current": str(values[index]),
        }
        for index in range(1, len(values))
        if values[index] < values[index - 1]
    ]
    return {
        "count": len(values),
        "minimum": str(min(values)),
        "maximum": str(max(values)),
        "first_values": [str(value) for value in values[:3]],
        "last_values": [str(value) for value in values[-3:]],
        "adjacent_comparison_count": comparisons,
        "nondecreasing_adjacent_count": nondecreasing,
        "nondecreasing_adjacent_ratio": round(
            nondecreasing / comparisons if comparisons else 1.0,
            6,
        ),
        "decrease_count": len(decreases),
        "decrease_samples": decreases[:10],
    }


def _boolean_distribution(records: list[dict[str, Any]], field_name: str) -> dict[str, int]:
    counts = Counter(bool(record["fields"][field_name]["value"]) for record in records)
    return {"false": counts[False], "true": counts[True]}


def _record_sample(record: Mapping[str, Any]) -> dict[str, Any]:
    fields = record["fields"]
    config = fields["configData"]["fields"]
    return {
        "index": record["index"],
        "offset": record["offset"],
        "end_offset": record["end_offset"],
        "objId": fields["objId"],
        "tick": fields["tick"],
        "configData": {
            "id": config["id"],
            "time": config["time"],
            "note_uid": config["note_uid"],
            "length": config["length"],
            "blood": config["blood"],
            "pathway": config["pathway"],
        },
        "isLongPressing": fields["isLongPressing"],
        "doubleIdx": fields["doubleIdx"],
        "sameTickNoteIdx": fields["sameTickNoteIdx"],
        "isDouble": fields["isDouble"],
        "isLongPressEnd": fields["isLongPressEnd"],
        "longPressPTick": fields["longPressPTick"],
        "endIndex": fields["endIndex"],
        "dt": fields["dt"],
        "longPressNum": fields["longPressNum"],
        "showTick": fields["showTick"],
    }


def _build_hypotheses(
    *,
    candidate: Mapping[str, Any],
    parsed: Mapping[str, Any],
    payload_sha256: str,
    sample_records: int,
) -> list[dict[str, Any]]:
    records = parsed["records"]
    provenance = {
        "inventory_fingerprint": candidate["inventory_fingerprint"],
        "source": candidate["source"],
        "source_size": candidate["source_size"],
        "source_sha256": candidate["source_sha256"],
        "container_path": candidate["container_path"],
        "path_id": candidate["path_id"],
        "object_type": candidate["object_type"],
        "asset_name": candidate["metadata"]["asset_name"],
        "payload_byte_count": parsed["payload_byte_count"],
        "payload_sha256": payload_sha256,
    }
    common = {
        "schema_version": 1,
        "phase": 4,
        "understanding_status": "partially-understood",
        "validation_status": "unvalidated",
        "provenance": provenance,
    }

    tick_stats = _decimal_stats(records, ("fields", "tick"))
    time_stats = _decimal_stats(
        records,
        ("fields", "configData", "fields", "time"),
    )
    behavior = {
        name: _boolean_distribution(records, name)
        for name in ("isLongPressing", "isDouble", "isLongPressEnd")
    }
    return [
        {
            **common,
            "hypothesis_id": "odin-binary-format",
            "field_path": "serializationData.SerializedBytes",
            "hypothesis": "payload uses the observed Sirenix/Odin Binary wire format",
            "confidence": "high",
            "evidence": [
                f"strict parser consumed {parsed['consumed_byte_count']} of {parsed['payload_byte_count']} bytes",
                f"all entries used the supported observed tag set: {', '.join(parsed['tag_counts'])}",
                f"resolved type table: {parsed['type_table']}",
            ],
            "counter_evidence": [
                "the exact Odin Serializer build used by this game has not been identified"
            ],
            "tag_counts": parsed["tag_counts"],
            "type_table": parsed["type_table"],
        },
        {
            **common,
            "hypothesis_id": "music-data-event-array",
            "field_path": "musicDatas[]",
            "hypothesis": "musicDatas is a serialized array of repeated MusicData records",
            "confidence": "high",
            "evidence": [
                f"root type is {parsed['root']['type']['name']}",
                f"array declared {parsed['array']['declared_count']} records and exactly {parsed['array']['parsed_count']} were parsed",
                "every record matched the same MusicData and nested MusicConfigData field layout",
                "array, root node, trailing delay/dialogEvents, and payload EOF all closed exactly",
            ],
            "counter_evidence": [
                "record correspondence with rendered game objects is not yet validated"
            ],
            "array": parsed["array"],
            "record_field_names": list(records[0]["fields"]) if records else [],
            "config_field_names": list(records[0]["fields"]["configData"]["fields"])
            if records
            else [],
            "record_samples": [
                _record_sample(record) for record in records[:sample_records]
            ],
        },
        {
            **common,
            "hypothesis_id": "tick-time-related",
            "field_path": "musicDatas[].tick",
            "hypothesis": "tick is a time-related decimal field",
            "confidence": "high",
            "evidence": [
                "the serialized field name is tick",
                f"values span {tick_stats['minimum']} to {tick_stats['maximum']}",
                f"{tick_stats['nondecreasing_adjacent_count']}/{tick_stats['adjacent_comparison_count']} adjacent pairs are nondecreasing",
            ],
            "counter_evidence": [
                f"{tick_stats['decrease_count']} adjacent decreases exist, so storage order is not strictly chronological",
                "seconds/beats/ticks semantics have not yet been checked against game footage",
            ],
            "statistics": tick_stats,
        },
        {
            **common,
            "hypothesis_id": "config-time-related",
            "field_path": "musicDatas[].configData.time",
            "hypothesis": "configData.time is a time-related decimal field",
            "confidence": "high",
            "evidence": [
                "the serialized field name is time",
                f"values span {time_stats['minimum']} to {time_stats['maximum']}",
                f"{time_stats['nondecreasing_adjacent_count']}/{time_stats['adjacent_comparison_count']} adjacent pairs are nondecreasing",
            ],
            "counter_evidence": [
                f"{time_stats['decrease_count']} adjacent decreases exist, so storage order is not strictly chronological",
                "its precise relationship to tick and rendered event timing is not yet validated",
            ],
            "statistics": time_stats,
        },
        {
            **common,
            "hypothesis_id": "behavior-flags",
            "field_path": "musicDatas[].{isLongPressing,isDouble,isLongPressEnd}",
            "hypothesis": "named boolean fields encode event behavior/state",
            "confidence": "high for stored boolean structure; unvalidated for game semantics",
            "evidence": [
                f"isLongPressing distribution: {behavior['isLongPressing']}",
                f"isDouble distribution: {behavior['isDouble']}",
                f"isLongPressEnd distribution: {behavior['isLongPressEnd']}",
            ],
            "counter_evidence": [
                "the meanings suggested by field names have not been compared with game footage",
                "no event-type enum field has yet been identified; configData.id must not be guessed as one",
            ],
            "distributions": behavior,
        },
    ]


def recover_stage_info_candidate(
    game_dir: str | Path,
    candidate: Mapping[str, Any],
    *,
    loader: Callable[[str], Any] = UnityPy.load,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Verify fingerprints and recover records plus the complete TypeTree envelope."""

    root = validate_game_directory(game_dir)
    relative_path = candidate.get("source")
    if not isinstance(relative_path, str) or not relative_path:
        raise StructureRecoveryError("candidate source is missing")
    source_path = (root / Path(relative_path)).resolve(strict=True)
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise StructureRecoveryError(
            f"candidate source escapes game directory: {relative_path}"
        ) from exc

    size, source_sha256, _ = fingerprint_file(source_path)
    if size != candidate.get("source_size") or source_sha256 != candidate.get("source_sha256"):
        raise StructureRecoveryError(
            f"candidate source fingerprint is stale for {relative_path}"
        )

    try:
        environment = loader(str(source_path))
        target = next(
            obj
            for obj in environment.objects
            if int(obj.path_id) == int(candidate["path_id"])
        )
    except StopIteration as exc:
        raise StructureRecoveryError(
            f"candidate PathID is absent from source: {candidate['path_id']}"
        ) from exc
    except Exception as exc:
        raise StructureRecoveryError(
            f"cannot load candidate source {relative_path}: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        data = target.parse_as_dict()
        if not isinstance(data, Mapping):
            raise StructureRecoveryError("StageInfo TypeTree result is not an object")
        serialization = data["serializationData"]
        if not isinstance(serialization, Mapping):
            raise StructureRecoveryError("StageInfo serializationData is not an object")
        if serialization["SerializedFormat"] != 0:
            raise StructureRecoveryError(
                f"unsupported StageInfo SerializedFormat: {serialization['SerializedFormat']}"
            )
        payload = bytes(serialization["SerializedBytes"])
    except StructureRecoveryError:
        raise
    except Exception as exc:
        raise StructureRecoveryError(
            f"cannot read StageInfo serializationData: {type(exc).__name__}: {exc}"
        ) from exc

    payload_sha256 = hashlib.sha256(payload).hexdigest()
    expected_structure = candidate.get("structure")
    if not isinstance(expected_structure, Mapping):
        raise StructureRecoveryError("candidate has no structural fingerprint")
    if (
        len(payload) != expected_structure.get("serialized_payload_byte_count")
        or payload_sha256 != expected_structure.get("serialized_payload_sha256")
    ):
        raise StructureRecoveryError("candidate serialized payload fingerprint is stale")

    try:
        parsed = parse_stage_info_payload(payload)
    except OdinParseError as exc:
        detail = exc.to_dict()
        raise StructureRecoveryError(
            f"Odin parse failed for {relative_path} PathID {candidate['path_id']}: {detail}"
        ) from exc
    return parsed, payload_sha256, dict(data)


def inspect_stage_info_candidate(
    game_dir: str | Path,
    candidate: Mapping[str, Any],
    *,
    sample_records: int = 2,
    loader: Callable[[str], Any] = UnityPy.load,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify, parse, and summarize one candidate without emitting all records."""

    if not 0 <= sample_records <= MAX_SAMPLE_RECORDS:
        raise StructureRecoveryError(
            f"sample records must be between 0 and {MAX_SAMPLE_RECORDS}"
        )
    parsed, payload_sha256, _stage_info_envelope = recover_stage_info_candidate(
        game_dir,
        candidate,
        loader=loader,
    )

    hypotheses = _build_hypotheses(
        candidate=candidate,
        parsed=parsed,
        payload_sha256=payload_sha256,
        sample_records=sample_records,
    )
    summary = {
        "schema_version": 1,
        "phase": 4,
        "source": candidate["source"],
        "path_id": candidate["path_id"],
        "candidate_rank": candidate["rank"],
        "payload_byte_count": parsed["payload_byte_count"],
        "payload_sha256": payload_sha256,
        "declared_record_count": parsed["array"]["declared_count"],
        "parsed_record_count": parsed["array"]["parsed_count"],
        "consumed_byte_count": parsed["consumed_byte_count"],
        "hypothesis_count": len(hypotheses),
        "sample_record_count": sample_records,
    }
    return hypotheses, summary
