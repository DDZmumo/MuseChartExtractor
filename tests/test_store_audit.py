from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from musedash_chart_extractor.store.audit import audit_chart_store
from musedash_chart_extractor.store.schema import compute_logical_digest
from musedash_chart_extractor.store.writer import extract_chart_store
from musedash_chart_extractor.unity.odin import parse_stage_info_payload
from test_store import (
    _Object,
    _candidate,
    _extract,
    _payload,
    _song_index,
    _unresolved_index,
)


def _build_store(root: Path) -> tuple[Path, Path, bytes, SimpleNamespace]:
    payload = _payload()
    bundle = b"synthetic bundle"
    candidate = _candidate(bundle, payload)
    game = root / "game"
    source = game / "data" / "fixture.bundle"
    source.parent.mkdir(parents=True)
    source.write_bytes(bundle)
    output = root / "MuseDashChartStore"
    environment = SimpleNamespace(objects=[_Object(payload)])
    extract_chart_store(
        game,
        output,
        [candidate],
        _song_index(candidate),
        grouping_census_summary={
            "schema_version": 1,
            "phase": 9,
            "status": "census-complete",
            "complete": True,
            "inventory_fingerprint": candidate["inventory_fingerprint"],
            "grouping_rule_version": (
                "composite-neutral-base-negative-id-singleton-v2"
            ),
            "candidate_count": 1,
            "source_count": 1,
            "raw_parse_status_counts": {"parsed": 1},
            "grouping_status_counts": {"grouped": 1},
            "parsed_raw_record_count": 2,
            "grouped_logical_object_count": 1,
        },
        note_configs_by_uid={
            "fixture-note": [
                {"uid": "fixture-note", "type": 3, "unknown": "preserve"}
            ]
        },
        note_data_provenance={"row_count": 1, "uid_count": 1},
        parser_family="sirenix-odin-binary-observed-stageinfo-subset",
        parser_version="strict-stageinfo-v1",
        loader=lambda _path: environment,
    )
    return game, output, payload, environment


class ChartStoreAuditTests(unittest.TestCase):
    def test_payload_parses_are_released_before_the_next_payload(self) -> None:
        class TrackedParse(dict):
            alive = 0
            peak = 0

            def __init__(self, value: dict) -> None:
                super().__init__(value)
                type(self).alive += 1
                type(self).peak = max(type(self).peak, type(self).alive)

            def __del__(self) -> None:
                type(self).alive -= 1

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = b"synthetic bundle"
            first_payload = _payload()
            second_payload = _payload(note_uid_value="fixture-note-2")
            first = _candidate(bundle, first_payload)
            second = _candidate(
                bundle,
                second_payload,
                chart_id="fixture_map2",
                path_id=202,
            )
            output, _manifest = _extract(
                root,
                [first, second],
                _unresolved_index(first, second),
                [
                    _Object(first_payload),
                    _Object(second_payload, chart_id="fixture_map2", path_id=202),
                ],
            )

            def tracked_parser(content: bytes) -> TrackedParse:
                return TrackedParse(dict(parse_stage_info_payload(content)))

            with patch(
                "musedash_chart_extractor.store.audit.parse_stage_info_payload",
                side_effect=tracked_parser,
            ):
                report = audit_chart_store(output)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(TrackedParse.peak, 1)
        self.assertEqual(TrackedParse.alive, 0)

    def test_complete_synthetic_store_passes_metadata_only_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game, output, _payload_bytes, environment = _build_store(Path(temporary))

            report = audit_chart_store(
                output,
                game_dir=game,
                loader=lambda _path: environment,
            )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["candidate_count"], 1)
        self.assertEqual(report["counts"]["chart_count"], 1)
        self.assertEqual(report["counts"]["payload_count"], 1)
        self.assertEqual(report["counts"]["raw_record_count"], 2)
        self.assertEqual(report["counts"]["logical_event_count"], 1)
        self.assertEqual(report["counts"]["sentinel_count"], 1)
        self.assertEqual(report["sqlite"]["integrity_check"], "ok")
        self.assertEqual(report["sqlite"]["foreign_key_violation_count"], 0)
        self.assertTrue(report["source_verification"]["requested"])
        self.assertEqual(report["source_verification"]["verified_source_count"], 1)
        self.assertTrue(all(value == 0 for value in report["mismatch_counts"].values()))
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn("SerializedBytes", rendered)
        self.assertNotIn('"events"', rendered)
        self.assertNotIn('"records"', rendered)
        self.assertNotIn("musicDatas", rendered)

    def test_manifest_phase_gate_contradiction_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            manifest_path = output / "store.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["phase_gate"]["all_candidate_payloads_stored"] = False
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["manifest_mismatches"], 0)
        self.assertTrue(
            any(
                "all_candidate_payloads_stored" in sample
                for sample in report["mismatch_samples"]["manifest_mismatches"]
            )
        )

    def test_audited_false_phase_gate_fails_even_when_manifest_agrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            index_path = output / "index.sqlite3"
            with closing(sqlite3.connect(index_path)) as connection:
                connection.execute(
                    "UPDATE charts SET status = 'failed', reason = 'synthetic-failure'"
                )
                digest = compute_logical_digest(connection)
                connection.execute(
                    "UPDATE metadata SET value_json = ? "
                    "WHERE key = 'logical_store_digest'",
                    (json.dumps(digest),),
                )
                connection.commit()

            manifest_path = output / "store.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["logical_store_digest"] = digest
            manifest["status_counts"] = {"failed": 1}
            manifest["charts"][0]["status"] = "failed"
            manifest["charts"][0]["reason"] = "synthetic-failure"
            manifest["phase_gate"]["no_failed_charts"] = False
            manifest["index"]["byte_count"] = index_path.stat().st_size
            manifest["index"]["sha256"] = hashlib.sha256(
                index_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(
                "no_failed_charts" in sample
                for sample in report["mismatch_samples"]["manifest_mismatches"]
            )
        )

    def test_unknown_chart_status_fails_even_when_manifest_and_digest_agree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            index_path = output / "index.sqlite3"
            with closing(sqlite3.connect(index_path)) as connection:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute("UPDATE charts SET status = 'mystery'")
                digest = compute_logical_digest(connection)
                connection.execute(
                    "UPDATE metadata SET value_json = ? "
                    "WHERE key = 'logical_store_digest'",
                    (json.dumps(digest),),
                )
                connection.commit()

            manifest_path = output / "store.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["logical_store_digest"] = digest
            manifest["status_counts"] = {"mystery": 1}
            manifest["charts"][0]["status"] = "mystery"
            manifest["index"]["byte_count"] = index_path.stat().st_size
            manifest["index"]["sha256"] = hashlib.sha256(
                index_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(
                "unsupported chart status" in sample
                for sample in report["mismatch_samples"]["manifest_mismatches"]
            )
        )

    def test_manifest_chart_summary_must_exactly_match_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            manifest_path = output / "store.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["charts"][0]["status"] = "failed"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(
                "charts" in sample
                for sample in report["mismatch_samples"]["manifest_mismatches"]
            )
        )

    def test_manifest_addressables_must_exactly_match_sqlite_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            manifest_path = output / "store.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["addressables"]["catalog_sha256"] = "f" * 64
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(
                "addressables" in sample
                for sample in report["mismatch_samples"]["manifest_mismatches"]
            )
        )

    def test_truncated_payload_reports_hash_and_strict_parse_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, payload, _environment = _build_store(Path(temporary))
            payload_path = next((output / "payloads").rglob("*.odin"))
            payload_path.write_bytes(payload[:-1])

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(
            report["mismatch_counts"]["payload_fingerprint_mismatches"], 0
        )
        self.assertGreater(
            report["mismatch_counts"]["payload_parse_mismatches"], 0
        )
        self.assertNotIn("musicDatas", json.dumps(report, sort_keys=True))

    def test_extra_payload_file_is_not_silently_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            extra = output / "payloads" / "sha256" / "ff" / ("f" * 64 + ".odin")
            extra.parent.mkdir(parents=True)
            extra.write_bytes(b"synthetic-extra")

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["payload_set_mismatches"], 0)
        self.assertTrue(
            any(
                "extra payload file" in sample
                for sample in report["mismatch_samples"]["payload_set_mismatches"]
            )
        )

    def test_payload_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _game, output, payload, _environment = _build_store(root)
            payload_path = next((output / "payloads").rglob("*.odin"))
            outside = root / "outside.odin"
            outside.write_bytes(payload)
            payload_path.unlink()
            try:
                payload_path.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["payload_set_mismatches"], 0)
        self.assertTrue(
            any(
                "symbolic link" in sample or "unsafe" in sample
                for sample in report["mismatch_samples"]["payload_set_mismatches"]
            )
        )

    def test_sqlite_foreign_key_violation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "UPDATE charts SET source_path = ? WHERE chart_id = ?",
                    ("missing.bundle", "fixture_map1"),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["sqlite"]["foreign_key_violation_count"], 0)
        self.assertGreater(report["mismatch_counts"]["foreign_key_mismatches"], 0)

    def test_candidate_and_chart_id_sets_must_match_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            rendered = json.dumps({}, separators=(",", ":"), sort_keys=True)
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                connection.execute(
                    "INSERT INTO candidates(chart_id, chart_id_casefold, "
                    "candidate_json, candidate_sha256) VALUES (?, ?, ?, ?)",
                    (
                        "extra_map1",
                        "extra_map1",
                        rendered,
                        hashlib.sha256(rendered.encode()).hexdigest(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["id_set_mismatches"], 0)

    def test_candidate_source_fingerprint_must_match_sources_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                candidate = json.loads(
                    connection.execute(
                        "SELECT candidate_json FROM candidates WHERE chart_id = ?",
                        ("fixture_map1",),
                    ).fetchone()[0]
                )
                candidate["source_size"] += 1
                rendered = json.dumps(
                    candidate,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    "UPDATE candidates SET candidate_json = ?, candidate_sha256 = ? "
                    "WHERE chart_id = ?",
                    (
                        rendered,
                        hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                        "fixture_map1",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["source_mismatches"], 0)

    def test_unreferenced_payload_row_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            content = b"unreferenced synthetic payload"
            digest = hashlib.sha256(content).hexdigest()
            relative = f"payloads/sha256/{digest[:2]}/{digest}.odin"
            path = output.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                connection.execute(
                    "INSERT INTO payloads(sha256, byte_count, relative_path, "
                    "path_casefold) VALUES (?, ?, ?, ?)",
                    (digest, len(content), relative, relative.casefold()),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["id_set_mismatches"], 0)

    def test_stage_info_envelope_must_not_duplicate_serialized_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                envelope = json.loads(
                    connection.execute(
                        "SELECT envelope_json FROM stage_info WHERE chart_id = ?",
                        ("fixture_map1",),
                    ).fetchone()[0]
                )
                envelope["serializationData"]["SerializedBytes"] = [0]
                rendered = json.dumps(
                    envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                connection.execute(
                    "UPDATE stage_info SET envelope_json = ?, envelope_sha256 = ? "
                    "WHERE chart_id = ?",
                    (
                        rendered,
                        hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                        "fixture_map1",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(
                "duplicates SerializedBytes" in sample
                for sample in report["mismatch_samples"]["envelope_mismatches"]
            )
        )

    def test_stage_info_envelope_must_retain_scene_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                envelope = json.loads(
                    connection.execute(
                        "SELECT envelope_json FROM stage_info WHERE chart_id = ?",
                        ("fixture_map1",),
                    ).fetchone()[0]
                )
                envelope.pop("sceneEvents")
                rendered = json.dumps(
                    envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                connection.execute(
                    "UPDATE stage_info SET envelope_json = ?, envelope_sha256 = ? "
                    "WHERE chart_id = ?",
                    (
                        rendered,
                        hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                        "fixture_map1",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(
                "sceneEvents" in sample
                for sample in report["mismatch_samples"]["envelope_mismatches"]
            )
        )

    def test_stage_info_serialized_format_column_must_match_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                connection.execute(
                    "UPDATE stage_info SET serialized_format = 1 WHERE chart_id = ?",
                    ("fixture_map1",),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["envelope_mismatches"], 0)

    def test_game_source_recheck_detects_removed_unknown_envelope_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game, output, _payload_bytes, environment = _build_store(Path(temporary))
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                envelope = json.loads(
                    connection.execute(
                        "SELECT envelope_json FROM stage_info WHERE chart_id = ?",
                        ("fixture_map1",),
                    ).fetchone()[0]
                )
                envelope.pop("syntheticUnknown")
                rendered = json.dumps(
                    envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                connection.execute(
                    "UPDATE stage_info SET envelope_json = ?, envelope_sha256 = ? "
                    "WHERE chart_id = ?",
                    (
                        rendered,
                        hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                        "fixture_map1",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(
                output,
                game_dir=game,
                loader=lambda _path: environment,
            )

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["source_mismatches"], 0)

    def test_declared_grouping_counts_are_recomputed_from_odin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                connection.execute(
                    "UPDATE charts SET raw_record_count = raw_record_count + 1, "
                    "record_group_count = record_group_count + 1, "
                    "logical_event_count = logical_event_count + 1, "
                    "sentinel_count = sentinel_count + 1 WHERE chart_id = ?",
                    ("fixture_map1",),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreaterEqual(
            report["mismatch_counts"]["grouping_count_mismatches"], 4
        )

    def test_truncated_sqlite_index_fails_integrity_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(
                Path(temporary)
            )
            index_path = output / "index.sqlite3"
            content = index_path.read_bytes()
            index_path.write_bytes(content[: len(content) // 2])

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(
            report["mismatch_counts"]["sqlite_integrity_mismatches"], 0
        )

    def test_game_source_bundle_hash_is_reverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game, output, _payload_bytes, environment = _build_store(Path(temporary))
            (game / "data" / "fixture.bundle").write_bytes(b"changed bundle")

            report = audit_chart_store(
                output,
                game_dir=game,
                loader=lambda _path: environment,
            )

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["source_mismatches"], 0)
        self.assertEqual(report["source_verification"]["verified_source_count"], 0)

    def test_game_source_path_id_is_reverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            environment = SimpleNamespace(objects=[])

            report = audit_chart_store(
                output,
                game_dir=game,
                loader=lambda _path: environment,
            )

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["source_mismatches"], 0)
        self.assertEqual(report["source_verification"]["verified_source_count"], 1)
        self.assertEqual(report["source_verification"]["verified_chart_count"], 0)

    def test_game_source_payload_is_reverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            game, output, payload, _environment = _build_store(Path(temporary))
            environment = SimpleNamespace(objects=[_Object(payload + b"\x00")])

            report = audit_chart_store(
                output,
                game_dir=game,
                loader=lambda _path: environment,
            )

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["source_mismatches"], 0)
        self.assertEqual(report["source_verification"]["verified_chart_count"], 0)

    def test_same_size_payload_corruption_reports_wrong_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            payload_path = next((output / "payloads").rglob("*.odin"))
            content = bytearray(payload_path.read_bytes())
            content[-1] ^= 0xFF
            payload_path.write_bytes(content)

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(
            report["mismatch_counts"]["payload_fingerprint_mismatches"], 0
        )

    def test_payload_index_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _game, output, _payload_bytes, _environment = _build_store(Path(temporary))
            connection = sqlite3.connect(output / "index.sqlite3")
            try:
                connection.execute(
                    "UPDATE payloads SET relative_path = ?, path_casefold = ?",
                    ("../outside.odin", "../outside.odin"),
                )
                connection.commit()
            finally:
                connection.close()

            report = audit_chart_store(output)

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["payload_set_mismatches"], 0)


if __name__ == "__main__":
    unittest.main()
