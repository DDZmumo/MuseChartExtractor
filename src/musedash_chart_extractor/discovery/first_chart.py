"""Unstable Phase 5 projection that preserves every recovered raw field."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from ..scanner import ScannerError


class FirstChartProjectionError(ScannerError):
    """Raised when the observed raw-record grouping invariants do not hold."""


def _field_value(record: Mapping[str, Any], name: str) -> Any:
    return record["fields"][name]["value"]


def _config_fields(record: Mapping[str, Any]) -> Mapping[str, Any]:
    return record["fields"]["configData"]["fields"]


def _config_value(record: Mapping[str, Any], name: str) -> Any:
    return _config_fields(record)[name]["value"]


def _decimal_text(field_value: Any) -> str | None:
    if isinstance(field_value, Mapping) and isinstance(field_value.get("text"), str):
        return field_value["text"]
    return None


def _decimal_for_order(field_value: Any, *, context: str) -> Decimal:
    text = _decimal_text(field_value)
    if text is None:
        raise FirstChartProjectionError(f"{context} has no exact decimal text")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise FirstChartProjectionError(
            f"{context} has invalid decimal text: {text!r}"
        ) from exc


def _parse_type_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 10)
        except ValueError:
            return None
    return None


def _resolve_raw_type(
    note_uid: str | None,
    note_configs_by_uid: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    if note_uid is None:
        return {
            "id": None,
            "name": None,
            "status": "no-note-uid",
            "source_field": None,
            "candidate_count": 0,
            "raw_values": [],
        }
    candidates = list(note_configs_by_uid.get(note_uid, ()))
    raw_values = [candidate.get("type") for candidate in candidates]
    parsed_ids = {_parse_type_id(value) for value in raw_values}
    parsed_ids.discard(None)
    invalid_count = sum(_parse_type_id(value) is None for value in raw_values)
    if candidates and len(parsed_ids) == 1 and invalid_count == 0:
        return {
            "id": next(iter(parsed_ids)),
            "name": None,
            "status": "resolved-id-name-unknown",
            "source_field": "notedata.json.type",
            "candidate_count": len(candidates),
            "raw_values": raw_values,
        }
    if not candidates:
        status = "unmapped-note-uid"
    elif len(parsed_ids) > 1:
        status = "conflicting-note-config-types"
    else:
        status = "invalid-note-config-type"
    return {
        "id": None,
        "name": None,
        "status": status,
        "source_field": "notedata.json.type",
        "candidate_count": len(candidates),
        "raw_values": raw_values,
    }


def _is_observed_sentinel(record: Mapping[str, Any], *, config_id: int) -> bool:
    fields = record["fields"]
    config = _config_fields(record)
    return (
        config_id == 0
        and _config_value(record, "note_uid") is None
        and _field_value(record, "objId") == 0
        and _decimal_text(_field_value(record, "tick")) == "0"
        and _decimal_text(config["time"]["value"]) == "0"
        and _decimal_text(config["length"]["value"]) == "0"
        and _config_value(record, "pathway") == 0
        and not _field_value(record, "isLongPressing")
        and not _field_value(record, "isLongPressEnd")
        and not _field_value(record, "isDouble")
    )


def _group_records(
    records: Sequence[Mapping[str, Any]],
    note_configs_by_uid: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[
        tuple[int, str, str | None, int, int | None], list[Mapping[str, Any]]
    ] = {}
    for record in records:
        config_id = _config_value(record, "id")
        if isinstance(config_id, bool) or not isinstance(config_id, int):
            raise FirstChartProjectionError(
                f"configData.id is not an integer at raw record {record.get('index')}"
            )
        time_text = _decimal_text(_config_value(record, "time"))
        if time_text is None:
            raise FirstChartProjectionError(
                f"configData.time has no exact decimal text at raw record {record.get('index')}"
            )
        note_uid = _config_value(record, "note_uid")
        if note_uid is not None and not isinstance(note_uid, str):
            raise FirstChartProjectionError(
                f"configData.note_uid is neither string nor null at raw record {record.get('index')}"
            )
        pathway = _config_value(record, "pathway")
        if isinstance(pathway, bool) or not isinstance(pathway, int):
            raise FirstChartProjectionError(
                f"configData.pathway is not an integer at raw record {record.get('index')}"
            )
        # The full current-install census shows that every id=-1 record is a
        # neutral independent object. Some share every other key field, so raw
        # index is required to avoid merging separate events.
        singleton_index = int(record["index"]) if config_id == -1 else None
        grouped.setdefault(
            (config_id, time_text, note_uid, pathway, singleton_index), []
        ).append(record)

    ordered_groups: list[tuple[tuple[Decimal, int, int], dict[str, Any], bool]] = []
    sentinel_count = 0
    expanded_record_count = 0
    type_counts: Counter[str] = Counter()
    group_size_counts: Counter[int] = Counter()

    # The raw Odin array interleaves long-press states. A 100-source census
    # disproved configData.id and endIndex==0 as universal grouping/base rules.
    # The composite key and neutral-state base rule succeeded for all 102,393
    # sampled records; raw indices and endIndex remain untouched as evidence.
    for (
        config_id,
        time_text,
        note_uid,
        pathway,
        singleton_index,
    ), members in grouped.items():
        bases = [
            member
            for member in members
            if not _field_value(member, "isLongPressing")
            and not _field_value(member, "isLongPressEnd")
        ]
        if len(bases) != 1:
            raise FirstChartProjectionError(
                "composite group "
                f"(id={config_id}, time={time_text}, note_uid={note_uid!r}, "
                f"pathway={pathway}) has {len(bases)} neutral base records; expected 1"
            )
        base = bases[0]

        extras = [member for member in members if member is not base]
        for member in extras:
            pressing = bool(_field_value(member, "isLongPressing"))
            ending = bool(_field_value(member, "isLongPressEnd"))
            if pressing == ending:
                raise FirstChartProjectionError(
                    "composite group "
                    f"(id={config_id}, time={time_text}) extra record "
                    "is not exactly one long-press state"
                )

        raw_type = _resolve_raw_type(note_uid, note_configs_by_uid)
        is_sentinel = len(members) == 1 and _is_observed_sentinel(
            base,
            config_id=config_id,
        )
        if is_sentinel:
            sentinel_count += 1
        else:
            logical_time = _decimal_for_order(
                _config_value(base, "time"),
                context=f"composite group id {config_id} time",
            )
            type_key = "unknown" if raw_type["id"] is None else str(raw_type["id"])
            type_counts[type_key] += 1

        member_indices = [int(member["index"]) for member in members]
        pressing_indices = [
            int(member["index"])
            for member in extras
            if _field_value(member, "isLongPressing")
        ]
        end_indices = [
            int(member["index"])
            for member in extras
            if _field_value(member, "isLongPressEnd")
        ]
        group_size_counts[len(members)] += 1
        expanded_record_count += len(extras)
        group_row = {
            "group_key_fields": [
                "configData.id",
                "configData.time",
                "configData.note_uid",
                "configData.pathway",
                "raw index when configData.id == -1",
            ],
            "config_id_raw": config_id,
            "base_raw_record_index": int(base["index"]),
            "raw_record_indices": member_indices,
            "raw_record_count": len(members),
            "extra_raw_record_indices": [
                int(member["index"]) for member in extras
            ],
            "long_pressing_raw_record_indices": pressing_indices,
            "long_press_end_raw_record_indices": end_indices,
            "note_uid_raw": note_uid,
            "time_raw": _config_value(base, "time"),
            "tick_raw": _field_value(base, "tick"),
            "length_raw": _config_value(base, "length"),
            "pathway_raw": _config_value(base, "pathway"),
            "raw_type": raw_type,
            "note_config_candidate_count": raw_type["candidate_count"],
            "role_status": (
                "observed-sentinel" if is_sentinel else "logical-gameplay-object"
            ),
            "grouping_status": "observed-composite-group-invariants-satisfied",
            "sequence_order": (
                "configData.time-ascending, configData.id-ascending, "
                "base-raw-index-ascending"
            ),
        }
        order_time = _decimal_for_order(
            _config_value(base, "time"), context=f"composite group id {config_id} time"
        )
        ordered_groups.append(
            (
                (order_time, config_id, int(base["index"])),
                group_row,
                is_sentinel,
            )
        )

    ordered_groups.sort(key=lambda item: item[0])
    group_rows = [row for _key, row, _sentinel in ordered_groups]
    logical_objects = [
        {"index": index, **row}
        for index, (_key, row, is_sentinel) in enumerate(
            item for item in ordered_groups if not item[2]
        )
    ]

    summary = {
        "group_key": (
            "musicDatas[].(configData.id, configData.time, "
            "configData.note_uid, configData.pathway, "
            "raw index when configData.id == -1)"
        ),
        "base_rule": "isLongPressing=false and isLongPressEnd=false",
        "logical_sequence_order": (
            "configData.time-ascending, configData.id-ascending, "
            "base-raw-index-ascending"
        ),
        "logical_sequence_time_field": "configData.time",
        "logical_sequence_time_descent_count": 0,
        "raw_record_count": len(records),
        "record_group_count": len(group_rows),
        "logical_object_count": len(logical_objects),
        "observed_sentinel_count": sentinel_count,
        "expanded_raw_record_count": expanded_record_count,
        "group_size_distribution": {
            str(size): count for size, count in sorted(group_size_counts.items())
        },
        "raw_type_id_distribution": dict(
            sorted(type_counts.items(), key=lambda item: (item[0] == "unknown", item[0]))
        ),
        "validation_status": "structurally-validated-for-selected-payload",
        "global_semantics_status": "not-generalized-across-game-versions-or-charts",
    }
    return group_rows, logical_objects, summary

def build_experimental_chart(
    candidate: Mapping[str, Any],
    parsed: Mapping[str, Any],
    *,
    payload_sha256: str,
    stage_info_raw: Mapping[str, Any],
    note_configs_by_uid: Mapping[str, Sequence[Mapping[str, Any]]],
    note_data_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a local-only raw-record document without inventing semantics."""

    records = parsed["records"]
    raw_records = []
    for record in records:
        fields = record["fields"]
        config = fields["configData"]["fields"]
        note_uid = config["note_uid"]["value"]
        raw_records.append(
            {
                "index": record["index"],
                "time_raw": {
                    "tick": fields["tick"]["value"],
                    "config_time": config["time"]["value"],
                    "semantic_status": "unvalidated",
                },
                "raw_type": _resolve_raw_type(note_uid, note_configs_by_uid),
                "raw": record,
            }
        )

    record_groups, logical_objects, grouping_summary = _group_records(
        records,
        note_configs_by_uid,
    )
    used_note_uids = sorted(
        {
            _config_value(record, "note_uid")
            for record in records
            if isinstance(_config_value(record, "note_uid"), str)
        }
    )
    used_note_configs = {
        note_uid: [dict(row) for row in note_configs_by_uid.get(note_uid, ())]
        for note_uid in used_note_uids
    }
    unmapped_note_uids = [
        note_uid for note_uid, rows in used_note_configs.items() if not rows
    ]

    return {
        "schema_version": "experimental-stageinfo-v1",
        "status": "raw-extracted",
        "understanding_status": "partially-understood",
        "validation_status": "unvalidated",
        "canonicalized": False,
        "copyright_boundary": (
            "local-only output from user-owned resources; do not commit or redistribute"
        ),
        "source": {
            "inventory_fingerprint": candidate["inventory_fingerprint"],
            "bundle": candidate["source"],
            "bundle_size": candidate["source_size"],
            "bundle_sha256": candidate["source_sha256"],
            "container_path": candidate["container_path"],
            "path_id": candidate["path_id"],
            "object_type": candidate["object_type"],
            "asset_name": candidate["metadata"]["asset_name"],
            "candidate_rank": candidate["rank"],
            "payload_byte_count": parsed["payload_byte_count"],
            "payload_sha256": payload_sha256,
        },
        "candidate_metadata": dict(candidate["metadata"]),
        "stage_info_raw": dict(stage_info_raw),
        "note_data": {
            "provenance": dict(note_data_provenance),
            "used_note_uid_count": len(used_note_uids),
            "unmapped_note_uids": unmapped_note_uids,
            "configs_by_uid_raw": used_note_configs,
        },
        "parser": {
            "format": parsed["format"],
            "consumed_byte_count": parsed["consumed_byte_count"],
            "type_table": parsed["type_table"],
            "tag_counts": parsed["tag_counts"],
        },
        "raw_stream": {
            "root": parsed["root"],
            "array": parsed["array"],
            "trailing_fields": parsed["trailing_fields"],
        },
        "raw_record_count": len(raw_records),
        "raw_records": raw_records,
        "record_group_count": len(record_groups),
        "record_groups": record_groups,
        "logical_object_count": len(logical_objects),
        "logical_objects": logical_objects,
        "grouping": grouping_summary,
        "unknowns": [
            "the enum names for numeric notedata type identifiers",
            "whether tick or configData.time is the rendered event time",
            "units and offset applied by the game loader",
            "exact rendered semantics of behavior fields",
            "whether map3 scoring-unit contribution rules generalize to other charts",
        ],
    }
