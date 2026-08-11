from __future__ import annotations

import struct
import unittest

from musedash_chart_extractor.unity.odin import (
    OdinParseError,
    decode_dotnet_decimal,
    parse_stage_info_payload,
)


def _string(value: str, *, width: int = 1) -> bytes:
    encoded = value.encode("latin-1" if width == 0 else "utf-16-le")
    return bytes([width]) + struct.pack("<i", len(value)) + encoded


def _type_name(type_id: int, value: str) -> bytes:
    return b"\x2f" + struct.pack("<i", type_id) + _string(value)


def _type_id(type_id: int) -> bytes:
    return b"\x30" + struct.pack("<i", type_id)


def _decimal_raw(text: str) -> bytes:
    negative = text.startswith("-")
    unsigned = text[1:] if negative else text
    if "." in unsigned:
        whole, fraction = unsigned.split(".", 1)
        scale = len(fraction)
        coefficient = int(f"{whole}{fraction}")
    else:
        scale = 0
        coefficient = int(unsigned)
    flags = (scale << 16) | (0x80000000 if negative else 0)
    high = (coefficient >> 64) & 0xFFFFFFFF
    low = coefficient & 0xFFFFFFFF
    middle = (coefficient >> 32) & 0xFFFFFFFF
    return struct.pack("<IIII", flags, high, low, middle)


def _named(tag: int, name: str, body: bytes = b"") -> bytes:
    return bytes([tag]) + _string(name) + body


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


def _unnamed_float(raw: bytes) -> bytes:
    assert len(raw) == 4
    return b"\x20" + raw


def _dialog_arg(
    index: int,
    *,
    first: bool,
    text: str | None,
    alignment_tag: int = 0x1D,
) -> bytes:
    arg_type = _type_name(13, _DIALOG_ARG_TYPE) if first else _type_id(13)
    text_field = (
        _named(0x2D, "text")
        if text is None
        else _named(0x27, "text", _string(text))
    )
    text_color_type = _type_name(14, _COLOR_TYPE) if first else _type_id(14)
    vector_type = _type_name(15, _VECTOR2_INT_TYPE) if first else _type_id(15)
    text_color_raw = (
        bytes.fromhex("00000080"),
        struct.pack("<f", 0.25),
        struct.pack("<f", 0.5),
        struct.pack("<f", 1.0),
    )
    bg_color_raw = tuple(struct.pack("<f", value) for value in (1.0, 0.75, 0.5, 0.25))
    return b"".join(
        [
            b"\x04",
            arg_type,
            _named(0x17, "index", struct.pack("<i", index)),
            _named(0x23, "time", _decimal_raw(f"{index + 1}.25")),
            _named(0x17, "dialogType", struct.pack("<i", 2 + index)),
            _named(0x17, "dialogIndex", struct.pack("<i", 10 + index)),
            text_field,
            _named(
                0x03,
                "textColor",
                text_color_type
                + b"".join(_unnamed_float(raw) for raw in text_color_raw)
                + b"\x05",
            ),
            _named(
                0x03,
                "bgColor",
                _type_id(14)
                + b"".join(_unnamed_float(raw) for raw in bg_color_raw)
                + b"\x05",
            ),
            _named(0x1F, "speed", bytes.fromhex("0000c03f")),
            _named(0x17, "fontSize", struct.pack("<i", 24 + index)),
            _named(
                0x03,
                "dialogSize",
                vector_type
                + b"\x18"
                + struct.pack("<i", 640 + index)
                + b"\x18"
                + struct.pack("<i", -120 - index)
                + b"\x05",
            ),
            _named(0x1D, "dialogState", struct.pack("<Q", 0xFEDCBA9876543210 + index)),
            _named(
                alignment_tag,
                "alignment",
                struct.pack("<Q", 0xFFFFFFFFFFFFFFFF - index),
            ),
            b"\x05",
        ]
    )


def _dialog_events(*, malformed_second_alignment: bool = False) -> bytes:
    return b"".join(
        [
            b"\x01",
            _string("dialogEvents"),
            _type_name(10, _DIALOG_DICTIONARY_TYPE),
            struct.pack("<i", 100),
            b"\x01",
            _string("comparer"),
            _type_name(11, _DIALOG_COMPARER_TYPE),
            struct.pack("<i", 101),
            b"\x05",
            b"\x06",
            struct.pack("<q", 1),
            b"\x04\x2e",
            _named(0x27, "$k", _string("fixture-language")),
            b"\x01",
            _string("$v"),
            _type_name(12, _DIALOG_LIST_TYPE),
            struct.pack("<i", 102),
            b"\x06",
            struct.pack("<q", 2),
            _dialog_arg(0, first=True, text="Artificial dialog"),
            _dialog_arg(
                1,
                first=False,
                text=None,
                alignment_tag=0x17 if malformed_second_alignment else 0x1D,
            ),
            b"\x07\x05\x05\x07\x05",
        ]
    )


def _record(
    index: int,
    *,
    first: bool,
    type_id: int = 1,
    note_width: int = 1,
    is_long_pressing: bool | None = None,
    is_long_press_end: bool | None = None,
) -> bytes:
    record_type = (
        _type_name(type_id, "GameLogic.MusicData, Assembly-CSharp")
        if first
        else _type_id(type_id)
    )
    config_type = (
        _type_name(2, "GameLogic.MusicConfigData, Assembly-CSharp")
        if first
        else _type_id(2)
    )
    tick = "0" if first else "3.117"
    note = _named(0x2D, "note_uid") if first else _named(
        0x27, "note_uid", _string("fixture-note", width=note_width)
    )
    config = b"".join(
        [
            _named(0x03, "configData", config_type),
            _named(0x17, "id", struct.pack("<i", index)),
            _named(0x23, "time", _decimal_raw(tick)),
            note,
            _named(0x23, "length", _decimal_raw("1.5" if index else "0")),
            _named(0x2B, "blood", bytes([index % 2])),
            _named(0x17, "pathway", struct.pack("<i", index)),
            b"\x05",
        ]
    )
    return b"".join(
        [
            b"\x04",
            record_type,
            _named(0x13, "objId", struct.pack("<h", index)),
            _named(0x23, "tick", _decimal_raw(tick)),
            config,
            _named(
                0x2B,
                "isLongPressing",
                bytes([index % 2 if is_long_pressing is None else is_long_pressing]),
            ),
            _named(0x17, "doubleIdx", struct.pack("<i", -1)),
            _named(0x2D, "sameTickNoteIdx"),
            _named(0x2B, "isDouble", bytes([0])),
            _named(
                0x2B,
                "isLongPressEnd",
                bytes([index % 2 if is_long_press_end is None else is_long_press_end]),
            ),
            _named(0x23, "longPressPTick", _decimal_raw("0")),
            _named(0x17, "endIndex", struct.pack("<i", 0)),
            _named(0x23, "dt", _decimal_raw("-1.25" if index else "0")),
            _named(0x17, "longPressNum", struct.pack("<i", index)),
            _named(0x23, "showTick", _decimal_raw("1.85" if index else "0")),
            b"\x05",
        ]
    )


def _payload(
    *,
    declared_count: int = 2,
    records: list[bytes] | None = None,
    dialog_events: bytes | None = None,
) -> bytes:
    actual_records = records if records is not None else [
        _record(0, first=True),
        _record(
            1,
            first=False,
            note_width=0,
            is_long_pressing=False,
            is_long_press_end=False,
        ),
    ]
    return b"".join(
        [
            b"\x01",
            _string("musicDatas"),
            _type_name(
                0,
                "System.Collections.Generic.List`1[[GameLogic.MusicData, Assembly-CSharp]], mscorlib",
            ),
            struct.pack("<i", 0),
            b"\x06",
            struct.pack("<q", declared_count),
            *actual_records,
            b"\x07\x05",
            _named(0x23, "delay", _decimal_raw("0")),
            (
                dialog_events
                if dialog_events is not None
                else _named(0x2D, "dialogEvents")
            ),
        ]
    )


class OdinStageInfoParserTests(unittest.TestCase):
    def test_strictly_parses_observed_stageinfo_subset(self) -> None:
        payload = _payload(
            records=[
                _record(0, first=True),
                _record(
                    1,
                    first=False,
                    note_width=0,
                    is_long_pressing=True,
                    is_long_press_end=False,
                ),
            ]
        )
        result = parse_stage_info_payload(payload)

        self.assertEqual(result["consumed_byte_count"], len(payload))
        self.assertEqual(result["array"]["declared_count"], 2)
        self.assertEqual(result["array"]["parsed_count"], 2)
        self.assertEqual(result["type_table"]["1"], "GameLogic.MusicData, Assembly-CSharp")
        second = result["records"][1]
        self.assertEqual(second["fields"]["objId"]["value"], 1)
        self.assertEqual(second["fields"]["tick"]["value"]["text"], "3.117")
        self.assertEqual(
            second["fields"]["configData"]["fields"]["note_uid"]["value"],
            "fixture-note",
        )
        self.assertTrue(second["fields"]["isLongPressing"]["value"])
        self.assertEqual(second["fields"]["dt"]["value"]["text"], "-1.25")
        self.assertEqual(result["trailing_fields"]["dialogEvents"]["value"], None)

    def test_decimal_layout_preserves_scale_sign_bits_and_raw_bytes(self) -> None:
        raw = _decimal_raw("-3.117")
        value = decode_dotnet_decimal(raw, offset=10, context="fixture.tick")

        self.assertEqual(value.text, "-3.117")
        self.assertEqual(value.scale, 3)
        self.assertTrue(value.negative)
        self.assertEqual(value.coefficient, 3117)
        self.assertEqual(value.raw_hex, raw.hex())

    def test_non_null_dialog_events_preserve_strict_structure_and_values(self) -> None:
        payload = _payload(dialog_events=_dialog_events())
        result = parse_stage_info_payload(payload)

        self.assertEqual(result["consumed_byte_count"], len(payload))
        dialog_events = result["trailing_fields"]["dialogEvents"]
        self.assertEqual(dialog_events["type"]["entry"], "TypeName")
        self.assertEqual(dialog_events["array"]["declared_count"], 1)
        self.assertEqual(dialog_events["array"]["parsed_count"], 1)
        entry = dialog_events["entries"][0]
        self.assertEqual(entry["type"]["entry"], "UnnamedNull")
        self.assertEqual(entry["key"]["value"], "fixture-language")
        self.assertEqual(entry["value"]["array"]["declared_count"], 2)
        first, second = entry["value"]["items"]
        self.assertEqual(first["type"]["entry"], "TypeName")
        self.assertEqual(second["type"]["entry"], "TypeID")
        self.assertEqual(first["fields"]["text"]["value"], "Artificial dialog")
        self.assertIsNone(second["fields"]["text"]["value"])
        self.assertEqual(
            first["fields"]["textColor"]["fields"]["r"]["value"]["raw_hex"],
            "00000080",
        )
        self.assertEqual(first["fields"]["speed"]["value"]["value"], 1.5)
        self.assertEqual(
            first["fields"]["speed"]["value"]["raw_hex"],
            "0000c03f",
        )
        self.assertEqual(first["fields"]["bgColor"]["type"]["entry"], "TypeID")
        self.assertEqual(second["fields"]["dialogSize"]["type"]["entry"], "TypeID")
        self.assertEqual(second["fields"]["dialogSize"]["fields"]["y"]["value"], -121)
        self.assertEqual(first["fields"]["dialogState"]["value"], 0xFEDCBA9876543210)
        self.assertEqual(second["fields"]["alignment"]["value"], 0xFFFFFFFFFFFFFFFE)
        self.assertEqual(result["type_table"]["14"], _COLOR_TYPE)

    def test_malformed_dialog_reports_language_item_and_field_context(self) -> None:
        with self.assertRaises(OdinParseError) as caught:
            parse_stage_info_payload(
                _payload(dialog_events=_dialog_events(malformed_second_alignment=True))
            )

        self.assertEqual(
            caught.exception.context,
            "dialogEvents['fixture-language'][1].alignment",
        )
        self.assertEqual(caught.exception.tag, 0x17)
        self.assertIn("expected NamedULong", caught.exception.message)

    def test_unknown_entry_tag_reports_exact_offset_and_context(self) -> None:
        record = bytearray(_record(0, first=True))
        obj_id_offset = record.index(b"\x13" + _string("objId"))
        record[obj_id_offset] = 0x34

        with self.assertRaises(OdinParseError) as caught:
            parse_stage_info_payload(_payload(declared_count=1, records=[bytes(record)]))

        self.assertEqual(caught.exception.tag, 0x34)
        self.assertIn("musicDatas[0].objId", caught.exception.context)
        self.assertGreater(caught.exception.offset, 0)

    def test_unknown_type_id_and_array_count_mismatch_fail_loudly(self) -> None:
        records = [_record(0, first=True), _record(1, first=False, type_id=99)]
        with self.assertRaisesRegex(OdinParseError, "unknown Odin TypeID"):
            parse_stage_info_payload(_payload(records=records))

        with self.assertRaisesRegex(OdinParseError, "expected UnnamedStartOfStructNode"):
            parse_stage_info_payload(_payload(declared_count=3))

    def test_truncation_and_invalid_decimal_flags_are_rejected(self) -> None:
        with self.assertRaises(OdinParseError) as caught:
            parse_stage_info_payload(_payload()[:-1])
        self.assertIn("dialogEvents", caught.exception.context)

        invalid = struct.pack("<IIII", 1, 0, 0, 0)
        with self.assertRaisesRegex(OdinParseError, "reserved bits"):
            decode_dotnet_decimal(invalid, offset=4, context="fixture")


if __name__ == "__main__":
    unittest.main()
