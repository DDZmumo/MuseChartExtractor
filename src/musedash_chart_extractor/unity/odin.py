"""Strict reader for the Odin Binary subset observed in Muse Dash StageInfo.

This is not a general-purpose Odin Serializer implementation.  It supports
only the wire entries proven by real StageInfo payloads and validates their
current ``MusicData`` field layout.  Unsupported or changed data fails with an
exact byte offset instead of being guessed or skipped.  Parsed values retain
their source offsets, and decimals additionally retain their exact raw bytes;
this module does not claim that decoded strings can reconstruct the payload
byte-for-byte.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping

TAG_NAMES = {
    0x01: "NamedStartOfReferenceNode",
    0x03: "NamedStartOfStructNode",
    0x04: "UnnamedStartOfStructNode",
    0x05: "EndOfNode",
    0x06: "StartOfArray",
    0x07: "EndOfArray",
    0x13: "NamedShort",
    0x17: "NamedInt",
    0x18: "UnnamedInt",
    0x1D: "NamedULong",
    0x1F: "NamedFloat",
    0x20: "UnnamedFloat",
    0x23: "NamedDecimal",
    0x27: "NamedString",
    0x2B: "NamedBoolean",
    0x2D: "NamedNull",
    0x2E: "UnnamedNull",
    0x2F: "TypeName",
    0x30: "TypeID",
}

MAX_STRING_CHARACTERS = 1_000_000
MAX_ARRAY_ELEMENTS = 1_000_000


class OdinParseError(ValueError):
    """A bounded parse failure with enough context for diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        offset: int,
        context: str,
        tag: int | None = None,
    ) -> None:
        self.message = message
        self.offset = offset
        self.context = context
        self.tag = tag
        tag_text = "" if tag is None else f" tag=0x{tag:02x}"
        super().__init__(f"{message} at offset {offset} ({context}){tag_text}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": type(self).__name__,
            "error": self.message,
            "offset": self.offset,
            "context": self.context,
            "tag": self.tag,
            "tag_hex": None if self.tag is None else f"0x{self.tag:02x}",
        }


@dataclass(frozen=True, slots=True)
class DotNetDecimal:
    """Lossless representation of the observed 16-byte .NET decimal layout."""

    text: str
    raw_hex: str
    flags: int
    high: int
    low: int
    middle: int
    scale: int
    negative: bool
    coefficient: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "raw_hex": self.raw_hex,
            "bits": {
                "flags": self.flags,
                "high": self.high,
                "low": self.low,
                "middle": self.middle,
            },
            "scale": self.scale,
            "negative": self.negative,
            "coefficient": str(self.coefficient),
        }


def decode_dotnet_decimal(raw: bytes, *, offset: int, context: str) -> DotNetDecimal:
    """Decode the little-endian ``flags, high, low, middle`` memory layout."""

    if len(raw) != 16:
        raise OdinParseError(
            f"decimal requires 16 bytes, received {len(raw)}",
            offset=offset,
            context=context,
        )
    flags, high, low, middle = struct.unpack("<IIII", raw)
    reserved = flags & 0x7F00FFFF
    scale = (flags >> 16) & 0xFF
    if reserved:
        raise OdinParseError(
            f"decimal flags contain reserved bits: 0x{flags:08x}",
            offset=offset,
            context=context,
        )
    if scale > 28:
        raise OdinParseError(
            f"decimal scale exceeds 28: {scale}",
            offset=offset,
            context=context,
        )

    coefficient = low | (middle << 32) | (high << 64)
    digits = str(coefficient)
    if scale:
        digits = digits.rjust(scale + 1, "0")
        text = f"{digits[:-scale]}.{digits[-scale:]}"
    else:
        text = digits
    negative = bool(flags & 0x80000000)
    if negative:
        text = f"-{text}"
    return DotNetDecimal(
        text=text,
        raw_hex=raw.hex(),
        flags=flags,
        high=high,
        low=low,
        middle=middle,
        scale=scale,
        negative=negative,
        coefficient=coefficient,
    )


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.types: dict[int, str | None] = {}
        self.tag_counts: Counter[int] = Counter()

    def _take(self, size: int, context: str) -> bytes:
        start = self.offset
        end = start + size
        if size < 0 or end > len(self.payload):
            raise OdinParseError(
                f"need {size} bytes but only {len(self.payload) - start} remain",
                offset=start,
                context=context,
            )
        self.offset = end
        return self.payload[start:end]

    def _read_tag(self, context: str) -> tuple[int, int]:
        start = self.offset
        tag = self._take(1, context)[0]
        self.tag_counts[tag] += 1
        return start, tag

    def expect_tag(self, expected: int, context: str) -> int:
        start, actual = self._read_tag(context)
        if actual != expected:
            expected_name = TAG_NAMES.get(expected, f"0x{expected:02x}")
            actual_name = TAG_NAMES.get(actual, "unknown")
            raise OdinParseError(
                f"expected {expected_name}, found {actual_name}",
                offset=start,
                context=context,
                tag=actual,
            )
        return start

    def read_i16(self, context: str) -> int:
        return struct.unpack("<h", self._take(2, context))[0]

    def read_i32(self, context: str) -> int:
        return struct.unpack("<i", self._take(4, context))[0]

    def read_i64(self, context: str) -> int:
        return struct.unpack("<q", self._take(8, context))[0]

    def read_u64(self, context: str) -> int:
        return struct.unpack("<Q", self._take(8, context))[0]

    def read_string_body(self, context: str) -> str:
        start = self.offset
        width = self._take(1, context)[0]
        length = self.read_i32(context)
        if width not in (0, 1):
            raise OdinParseError(
                f"unsupported Odin string width flag: {width}",
                offset=start,
                context=context,
            )
        if length < 0 or length > MAX_STRING_CHARACTERS:
            raise OdinParseError(
                f"invalid Odin string character count: {length}",
                offset=start + 1,
                context=context,
            )
        byte_count = length if width == 0 else length * 2
        raw = self._take(byte_count, context)
        try:
            return raw.decode("latin-1" if width == 0 else "utf-16-le", errors="strict")
        except UnicodeDecodeError as exc:
            raise OdinParseError(
                f"invalid Odin string encoding: {exc}",
                offset=start + 5,
                context=context,
            ) from exc

    def read_type(self, context: str) -> dict[str, Any]:
        start, tag = self._read_tag(context)
        if tag == 0x2F:
            type_id = self.read_i32(context)
            name = self.read_string_body(context)
            if type_id in self.types:
                raise OdinParseError(
                    f"duplicate Odin TypeName id: {type_id}",
                    offset=start,
                    context=context,
                    tag=tag,
                )
            self.types[type_id] = name
            return {
                "entry": "TypeName",
                "id": type_id,
                "name": name,
                "offset": start,
                "end_offset": self.offset,
            }
        if tag == 0x30:
            type_id = self.read_i32(context)
            if type_id not in self.types:
                raise OdinParseError(
                    f"unknown Odin TypeID: {type_id}",
                    offset=start,
                    context=context,
                    tag=tag,
                )
            return {
                "entry": "TypeID",
                "id": type_id,
                "name": self.types[type_id],
                "offset": start,
                "end_offset": self.offset,
            }
        if tag == 0x2E:
            return {
                "entry": "UnnamedNull",
                "id": None,
                "name": None,
                "offset": start,
                "end_offset": self.offset,
            }
        raise OdinParseError(
            "expected TypeName, TypeID, or UnnamedNull type metadata",
            offset=start,
            context=context,
            tag=tag,
        )

    def _named_prefix(self, tag: int, expected_name: str, context: str) -> tuple[int, str]:
        start = self.expect_tag(tag, context)
        name = self.read_string_body(f"{context}.name")
        if name != expected_name:
            raise OdinParseError(
                f"expected field name {expected_name!r}, found {name!r}",
                offset=start,
                context=context,
                tag=tag,
            )
        return start, name

    @staticmethod
    def _value_row(
        *,
        start: int,
        end: int,
        tag: int,
        name: str,
        kind: str,
        value: Any,
    ) -> dict[str, Any]:
        return {
            "offset": start,
            "end_offset": end,
            "tag": f"0x{tag:02x}",
            "entry_type": TAG_NAMES[tag],
            "name": name,
            "value_type": kind,
            "value": value,
        }

    def named_i16(self, name: str, context: str) -> dict[str, Any]:
        start, actual_name = self._named_prefix(0x13, name, context)
        value = self.read_i16(context)
        return self._value_row(
            start=start,
            end=self.offset,
            tag=0x13,
            name=actual_name,
            kind="int16",
            value=value,
        )

    def named_i32(self, name: str, context: str) -> dict[str, Any]:
        start, actual_name = self._named_prefix(0x17, name, context)
        value = self.read_i32(context)
        return self._value_row(
            start=start,
            end=self.offset,
            tag=0x17,
            name=actual_name,
            kind="int32",
            value=value,
        )

    def unnamed_i32(self, context: str) -> dict[str, Any]:
        start = self.expect_tag(0x18, context)
        value = self.read_i32(context)
        return {
            "offset": start,
            "end_offset": self.offset,
            "tag": "0x18",
            "entry_type": TAG_NAMES[0x18],
            "value_type": "int32",
            "value": value,
        }

    def named_u64(self, name: str, context: str) -> dict[str, Any]:
        start, actual_name = self._named_prefix(0x1D, name, context)
        value = self.read_u64(context)
        return self._value_row(
            start=start,
            end=self.offset,
            tag=0x1D,
            name=actual_name,
            kind="uint64",
            value=value,
        )

    def _float32_value(self, context: str) -> dict[str, Any]:
        value_offset = self.offset
        raw = self._take(4, context)
        return {
            "value": struct.unpack("<f", raw)[0],
            "raw_hex": raw.hex(),
            "value_offset": value_offset,
        }

    def named_float(self, name: str, context: str) -> dict[str, Any]:
        start, actual_name = self._named_prefix(0x1F, name, context)
        value = self._float32_value(context)
        return self._value_row(
            start=start,
            end=self.offset,
            tag=0x1F,
            name=actual_name,
            kind="float32",
            value=value,
        )

    def unnamed_float(self, context: str) -> dict[str, Any]:
        start = self.expect_tag(0x20, context)
        value = self._float32_value(context)
        return {
            "offset": start,
            "end_offset": self.offset,
            "tag": "0x20",
            "entry_type": TAG_NAMES[0x20],
            "value_type": "float32",
            "value": value,
        }

    def named_decimal(self, name: str, context: str) -> dict[str, Any]:
        start, actual_name = self._named_prefix(0x23, name, context)
        value_offset = self.offset
        raw = self._take(16, context)
        value = decode_dotnet_decimal(raw, offset=value_offset, context=context)
        return self._value_row(
            start=start,
            end=self.offset,
            tag=0x23,
            name=actual_name,
            kind="dotnet_decimal",
            value=value.to_dict(),
        )

    def named_bool(self, name: str, context: str) -> dict[str, Any]:
        start, actual_name = self._named_prefix(0x2B, name, context)
        value_offset = self.offset
        raw_value = self._take(1, context)[0]
        if raw_value not in (0, 1):
            raise OdinParseError(
                f"boolean byte must be 0 or 1, found {raw_value}",
                offset=value_offset,
                context=context,
                tag=0x2B,
            )
        return self._value_row(
            start=start,
            end=self.offset,
            tag=0x2B,
            name=actual_name,
            kind="boolean",
            value=bool(raw_value),
        )

    def named_string_or_null(self, name: str, context: str) -> dict[str, Any]:
        if self.offset >= len(self.payload):
            raise OdinParseError(
                "expected named string or null at end of payload",
                offset=self.offset,
                context=context,
            )
        tag = self.payload[self.offset]
        if tag == 0x2D:
            start, actual_name = self._named_prefix(0x2D, name, context)
            return self._value_row(
                start=start,
                end=self.offset,
                tag=0x2D,
                name=actual_name,
                kind="null",
                value=None,
            )
        if tag == 0x27:
            start, actual_name = self._named_prefix(0x27, name, context)
            value = self.read_string_body(f"{context}.value")
            return self._value_row(
                start=start,
                end=self.offset,
                tag=0x27,
                name=actual_name,
                kind="string",
                value=value,
            )
        start, actual = self._read_tag(context)
        raise OdinParseError(
            "expected NamedString or NamedNull",
            offset=start,
            context=context,
            tag=actual,
        )

    def named_null(self, name: str, context: str) -> dict[str, Any]:
        start, actual_name = self._named_prefix(0x2D, name, context)
        return self._value_row(
            start=start,
            end=self.offset,
            tag=0x2D,
            name=actual_name,
            kind="null",
            value=None,
        )


def _parse_config_data(reader: _Reader, record_index: int) -> dict[str, Any]:
    context = f"musicDatas[{record_index}].configData"
    start, name = reader._named_prefix(0x03, "configData", context)
    type_info = reader.read_type(f"{context}.type")
    if type_info["name"] != "GameLogic.MusicConfigData, Assembly-CSharp":
        raise OdinParseError(
            f"unexpected configData type: {type_info['name']!r}",
            offset=type_info["offset"],
            context=f"{context}.type",
        )
    fields = {
        "id": reader.named_i32("id", f"{context}.id"),
        "time": reader.named_decimal("time", f"{context}.time"),
        "note_uid": reader.named_string_or_null("note_uid", f"{context}.note_uid"),
        "length": reader.named_decimal("length", f"{context}.length"),
        "blood": reader.named_bool("blood", f"{context}.blood"),
        "pathway": reader.named_i32("pathway", f"{context}.pathway"),
    }
    reader.expect_tag(0x05, f"{context}.end")
    return {
        "offset": start,
        "end_offset": reader.offset,
        "tag": "0x03",
        "entry_type": TAG_NAMES[0x03],
        "name": name,
        "type": type_info,
        "fields": fields,
    }


def _parse_music_record(reader: _Reader, record_index: int) -> dict[str, Any]:
    context = f"musicDatas[{record_index}]"
    start = reader.expect_tag(0x04, context)
    type_info = reader.read_type(f"{context}.type")
    if type_info["name"] != "GameLogic.MusicData, Assembly-CSharp":
        raise OdinParseError(
            f"unexpected music record type: {type_info['name']!r}",
            offset=type_info["offset"],
            context=f"{context}.type",
        )

    fields = {
        "objId": reader.named_i16("objId", f"{context}.objId"),
        "tick": reader.named_decimal("tick", f"{context}.tick"),
        "configData": _parse_config_data(reader, record_index),
        "isLongPressing": reader.named_bool(
            "isLongPressing", f"{context}.isLongPressing"
        ),
        "doubleIdx": reader.named_i32("doubleIdx", f"{context}.doubleIdx"),
        "sameTickNoteIdx": reader.named_null(
            "sameTickNoteIdx", f"{context}.sameTickNoteIdx"
        ),
        "isDouble": reader.named_bool("isDouble", f"{context}.isDouble"),
        "isLongPressEnd": reader.named_bool(
            "isLongPressEnd", f"{context}.isLongPressEnd"
        ),
        "longPressPTick": reader.named_decimal(
            "longPressPTick", f"{context}.longPressPTick"
        ),
        "endIndex": reader.named_i32("endIndex", f"{context}.endIndex"),
        "dt": reader.named_decimal("dt", f"{context}.dt"),
        "longPressNum": reader.named_i32(
            "longPressNum", f"{context}.longPressNum"
        ),
        "showTick": reader.named_decimal("showTick", f"{context}.showTick"),
    }

    reader.expect_tag(0x05, f"{context}.end")
    return {
        "index": record_index,
        "offset": start,
        "end_offset": reader.offset,
        "tag": "0x04",
        "entry_type": TAG_NAMES[0x04],
        "type": type_info,
        "fields": fields,
    }


_DIALOG_DICTIONARY_TYPE = (
    "System.Collections.Generic.Dictionary`2[[System.String, mscorlib],"
    "[System.Collections.Generic.List`1[[Assets.Scripts.Structs.GameDialogArgs, "
    "Assembly-CSharp]], mscorlib]], mscorlib"
)
_DIALOG_COMPARER_TYPE = (
    "System.Collections.Generic.GenericEqualityComparer`1[[System.String, "
    "mscorlib]], mscorlib"
)
_DIALOG_LIST_TYPE = (
    "System.Collections.Generic.List`1[[Assets.Scripts.Structs.GameDialogArgs, "
    "Assembly-CSharp]], mscorlib"
)
_DIALOG_ARG_TYPE = "Assets.Scripts.Structs.GameDialogArgs, Assembly-CSharp"
_COLOR_TYPE = "UnityEngine.Color, UnityEngine.CoreModule"
_VECTOR2_INT_TYPE = "UnityEngine.Vector2Int, UnityEngine.CoreModule"


def _require_type(type_info: Mapping[str, Any], expected: str | None, context: str) -> None:
    if type_info.get("name") != expected:
        raise OdinParseError(
            f"unexpected type: {type_info.get('name')!r}; expected {expected!r}",
            offset=int(type_info["offset"]),
            context=context,
        )


def _parse_named_struct_float4(
    reader: _Reader,
    *,
    name: str,
    expected_type: str,
    component_names: tuple[str, str, str, str],
    context: str,
) -> dict[str, Any]:
    start, actual_name = reader._named_prefix(0x03, name, context)
    type_info = reader.read_type(f"{context}.type")
    _require_type(type_info, expected_type, f"{context}.type")
    fields = {
        component_name: reader.unnamed_float(f"{context}.{component_name}")
        for component_name in component_names
    }
    reader.expect_tag(0x05, f"{context}.end")
    return {
        "offset": start,
        "end_offset": reader.offset,
        "tag": "0x03",
        "entry_type": TAG_NAMES[0x03],
        "name": actual_name,
        "type": type_info,
        "fields": fields,
    }


def _parse_dialog_size(reader: _Reader, context: str) -> dict[str, Any]:
    start, name = reader._named_prefix(0x03, "dialogSize", context)
    type_info = reader.read_type(f"{context}.type")
    _require_type(type_info, _VECTOR2_INT_TYPE, f"{context}.type")
    fields = {
        "x": reader.unnamed_i32(f"{context}.x"),
        "y": reader.unnamed_i32(f"{context}.y"),
    }
    reader.expect_tag(0x05, f"{context}.end")
    return {
        "offset": start,
        "end_offset": reader.offset,
        "tag": "0x03",
        "entry_type": TAG_NAMES[0x03],
        "name": name,
        "type": type_info,
        "fields": fields,
    }


def _parse_dialog_arg(reader: _Reader, index: int, language: str) -> dict[str, Any]:
    context = f"dialogEvents[{language!r}][{index}]"
    start = reader.expect_tag(0x04, context)
    type_info = reader.read_type(f"{context}.type")
    _require_type(type_info, _DIALOG_ARG_TYPE, f"{context}.type")
    fields = {
        "index": reader.named_i32("index", f"{context}.index"),
        "time": reader.named_decimal("time", f"{context}.time"),
        "dialogType": reader.named_i32("dialogType", f"{context}.dialogType"),
        "dialogIndex": reader.named_i32("dialogIndex", f"{context}.dialogIndex"),
        "text": reader.named_string_or_null("text", f"{context}.text"),
        "textColor": _parse_named_struct_float4(
            reader,
            name="textColor",
            expected_type=_COLOR_TYPE,
            component_names=("r", "g", "b", "a"),
            context=f"{context}.textColor",
        ),
        "bgColor": _parse_named_struct_float4(
            reader,
            name="bgColor",
            expected_type=_COLOR_TYPE,
            component_names=("r", "g", "b", "a"),
            context=f"{context}.bgColor",
        ),
        "speed": reader.named_float("speed", f"{context}.speed"),
        "fontSize": reader.named_i32("fontSize", f"{context}.fontSize"),
        "dialogSize": _parse_dialog_size(reader, f"{context}.dialogSize"),
        "dialogState": reader.named_u64("dialogState", f"{context}.dialogState"),
        "alignment": reader.named_u64("alignment", f"{context}.alignment"),
    }
    reader.expect_tag(0x05, f"{context}.end")
    return {
        "offset": start,
        "end_offset": reader.offset,
        "tag": "0x04",
        "entry_type": TAG_NAMES[0x04],
        "type": type_info,
        "fields": fields,
    }


def _array_length(reader: _Reader, context: str) -> tuple[int, int]:
    start = reader.expect_tag(0x06, context)
    length = reader.read_i64(f"{context}.length")
    if length < 0 or length > MAX_ARRAY_ELEMENTS:
        raise OdinParseError(
            f"invalid Odin array length: {length}",
            offset=start + 1,
            context=f"{context}.length",
            tag=0x06,
        )
    return start, length


def _parse_dialog_events(reader: _Reader) -> dict[str, Any]:
    context = "dialogEvents"
    if reader.offset >= len(reader.payload):
        raise OdinParseError(
            "expected dialogEvents at end of payload",
            offset=reader.offset,
            context=context,
        )
    if reader.payload[reader.offset] == 0x2D:
        return reader.named_null("dialogEvents", context)

    start, name = reader._named_prefix(0x01, "dialogEvents", context)
    type_info = reader.read_type(f"{context}.type")
    _require_type(type_info, _DIALOG_DICTIONARY_TYPE, f"{context}.type")
    reference_id = reader.read_i32(f"{context}.reference_id")

    comparer_start, comparer_name = reader._named_prefix(
        0x01, "comparer", f"{context}.comparer"
    )
    comparer_type = reader.read_type(f"{context}.comparer.type")
    _require_type(comparer_type, _DIALOG_COMPARER_TYPE, f"{context}.comparer.type")
    comparer_reference_id = reader.read_i32(f"{context}.comparer.reference_id")
    reader.expect_tag(0x05, f"{context}.comparer.end")
    comparer = {
        "offset": comparer_start,
        "end_offset": reader.offset,
        "tag": "0x01",
        "entry_type": TAG_NAMES[0x01],
        "name": comparer_name,
        "type": comparer_type,
        "reference_id": comparer_reference_id,
    }

    array_start, declared_count = _array_length(reader, f"{context}.array")
    entries = []
    for entry_index in range(declared_count):
        entry_context = f"{context}.entries[{entry_index}]"
        entry_start = reader.expect_tag(0x04, entry_context)
        entry_type = reader.read_type(f"{entry_context}.type")
        _require_type(entry_type, None, f"{entry_context}.type")
        key = reader.named_string_or_null("$k", f"{entry_context}.key")
        language = key["value"]
        if not isinstance(language, str):
            raise OdinParseError(
                "dialogEvents dictionary key must be a string",
                offset=int(key["offset"]),
                context=f"{entry_context}.key",
            )
        value_start, value_name = reader._named_prefix(
            0x01, "$v", f"{entry_context}.value"
        )
        value_type = reader.read_type(f"{entry_context}.value.type")
        _require_type(value_type, _DIALOG_LIST_TYPE, f"{entry_context}.value.type")
        value_reference_id = reader.read_i32(f"{entry_context}.value.reference_id")
        list_start, list_count = _array_length(reader, f"{entry_context}.value.array")
        items = [
            _parse_dialog_arg(reader, item_index, language)
            for item_index in range(list_count)
        ]
        reader.expect_tag(0x07, f"{entry_context}.value.array.end")
        list_end = reader.offset
        reader.expect_tag(0x05, f"{entry_context}.value.end")
        value = {
            "offset": value_start,
            "end_offset": reader.offset,
            "tag": "0x01",
            "entry_type": TAG_NAMES[0x01],
            "name": value_name,
            "type": value_type,
            "reference_id": value_reference_id,
            "array": {
                "offset": list_start,
                "end_offset": list_end,
                "declared_count": list_count,
                "parsed_count": len(items),
            },
            "items": items,
        }
        reader.expect_tag(0x05, f"{entry_context}.end")
        entries.append(
            {
                "offset": entry_start,
                "end_offset": reader.offset,
                "tag": "0x04",
                "entry_type": TAG_NAMES[0x04],
                "type": entry_type,
                "key": key,
                "value": value,
            }
        )
    reader.expect_tag(0x07, f"{context}.array.end")
    array_end = reader.offset
    reader.expect_tag(0x05, f"{context}.end")
    return {
        "offset": start,
        "end_offset": reader.offset,
        "tag": "0x01",
        "entry_type": TAG_NAMES[0x01],
        "name": name,
        "value_type": "dictionary<string,list<GameDialogArgs>>",
        "type": type_info,
        "reference_id": reference_id,
        "comparer": comparer,
        "array": {
            "offset": array_start,
            "end_offset": array_end,
            "declared_count": declared_count,
            "parsed_count": len(entries),
        },
        "entries": entries,
    }
def parse_stage_info_payload(payload: bytes) -> dict[str, Any]:
    """Strictly parse every field in the observed StageInfo payload subset."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    reader = _Reader(payload)
    root_start = reader.expect_tag(0x01, "musicDatas")
    root_name = reader.read_string_body("musicDatas.name")
    if root_name != "musicDatas":
        raise OdinParseError(
            f"expected root name 'musicDatas', found {root_name!r}",
            offset=root_start,
            context="musicDatas",
            tag=0x01,
        )
    root_type = reader.read_type("musicDatas.type")
    if not root_type["name"] or "GameLogic.MusicData" not in root_type["name"]:
        raise OdinParseError(
            f"unexpected musicDatas root type: {root_type['name']!r}",
            offset=root_type["offset"],
            context="musicDatas.type",
        )
    reference_id = reader.read_i32("musicDatas.reference_id")

    array_start = reader.expect_tag(0x06, "musicDatas.array")
    declared_count = reader.read_i64("musicDatas.array.length")
    if declared_count < 0 or declared_count > MAX_ARRAY_ELEMENTS:
        raise OdinParseError(
            f"invalid Odin array length: {declared_count}",
            offset=array_start + 1,
            context="musicDatas.array.length",
            tag=0x06,
        )
    records = [_parse_music_record(reader, index) for index in range(declared_count)]
    reader.expect_tag(0x07, "musicDatas.array.end")
    array_end = reader.offset
    reader.expect_tag(0x05, "musicDatas.end")
    root_end = reader.offset

    trailing_fields = {
        "delay": reader.named_decimal("delay", "delay"),
        "dialogEvents": _parse_dialog_events(reader),
    }
    if reader.offset != len(payload):
        tag = payload[reader.offset]
        raise OdinParseError(
            f"unexpected trailing data ({len(payload) - reader.offset} bytes)",
            offset=reader.offset,
            context="payload.end",
            tag=tag,
        )

    return {
        "schema_version": 1,
        "format": "sirenix-odin-binary-observed-stageinfo-subset",
        "payload_byte_count": len(payload),
        "consumed_byte_count": reader.offset,
        "root": {
            "offset": root_start,
            "end_offset": root_end,
            "name": root_name,
            "type": root_type,
            "reference_id": reference_id,
        },
        "array": {
            "offset": array_start,
            "end_offset": array_end,
            "declared_count": declared_count,
            "parsed_count": len(records),
        },
        "records": records,
        "trailing_fields": trailing_fields,
        "type_table": {str(key): value for key, value in sorted(reader.types.items())},
        "tag_counts": {
            f"0x{tag:02x} {TAG_NAMES.get(tag, 'unknown')}": count
            for tag, count in sorted(reader.tag_counts.items())
        },
    }
