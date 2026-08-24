from __future__ import annotations

import gc
import hashlib
import json
import tempfile
import unittest
import weakref
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from musedash_chart_extractor.store.equivalence import (
    compare_chart_store_to_canonical_tree,
)


_FINGERPRINT = "sha256:" + "1" * 64


class _TrackedChart(dict):
    pass


class _FakeStore:
    def __init__(
        self,
        charts: dict[str, dict],
        *,
        unresolved: tuple[str, ...] = (),
        failed: tuple[str, ...] = (),
        other_statuses: dict[str, str] | None = None,
        enforce_one_at_a_time: bool = False,
    ) -> None:
        self._charts = charts
        self._unresolved = unresolved
        self._failed = failed
        self._other_statuses = other_statuses or {}
        self._enforce_one_at_a_time = enforce_one_at_a_time
        self._previous: weakref.ReferenceType[_TrackedChart] | None = None
        self.max_live_loaded_charts = 0
        self.load_order: list[str] = []
        self.manifest = {
            "store_schema_version": "1.0.0",
            "logical_store_digest": "2" * 64,
        }
        self.metadata = {
            "canonical_schema_version": "1.1.0",
            "inventory_fingerprint": _FINGERPRINT,
        }

    def __enter__(self) -> _FakeStore:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def iter_charts(self):
        for chart_id in sorted(self._charts):
            chart = self._charts[chart_id]
            yield SimpleNamespace(
                chart_id=chart_id,
                status="success",
                raw_record_count=2,
                logical_event_count=chart["event_count"],
            )
        for chart_id in sorted(self._unresolved):
            yield SimpleNamespace(chart_id=chart_id, status="uncertain")
        for chart_id in sorted(self._failed):
            yield SimpleNamespace(chart_id=chart_id, status="failed")
        for chart_id, status in sorted(self._other_statuses.items()):
            yield SimpleNamespace(chart_id=chart_id, status=status)

    def load_chart(self, chart_id: str) -> dict:
        if self._enforce_one_at_a_time and self._previous is not None:
            gc.collect()
            if self._previous() is not None:
                raise AssertionError("previous canonical chart is still retained")
        chart = _TrackedChart(deepcopy(self._charts[chart_id]))
        self._previous = weakref.ref(chart)
        self.max_live_loaded_charts = max(self.max_live_loaded_charts, 1)
        self.load_order.append(chart_id)
        return chart


def _chart(chart_id: str, *, marker: str = "same") -> dict:
    return {
        "schema_version": "1.1.0",
        "chart_id": chart_id,
        "event_count": 1,
        "events": [{"id": 0, "raw": {"marker": marker}}],
        "raw": {
            "experimental_chart": {
                "raw_records": [{"index": 0}, {"index": 1}],
                "unknown": marker,
            }
        },
    }


def _write_legacy_tree(
    root: Path,
    charts: dict[str, dict],
    *,
    unresolved: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    other_statuses: dict[str, str] | None = None,
    path_overrides: dict[str, str] | None = None,
    omitted_files: frozenset[str] = frozenset(),
) -> dict:
    rows: list[dict] = []
    path_overrides = path_overrides or {}
    for chart_id, chart in sorted(charts.items()):
        relative = path_overrides.get(chart_id, f"charts/song/{chart_id}.json")
        content = (json.dumps(chart, ensure_ascii=False, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        if chart_id not in omitted_files and ".." not in Path(relative).parts:
            destination = root.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        rows.append(
            {
                "chart_id": chart_id,
                "status": "success",
                "output_path": relative,
                "output_byte_count": len(content),
                "output_sha256": hashlib.sha256(content).hexdigest(),
                "raw_record_count": 2,
                "event_count": chart["event_count"],
            }
        )
    rows.extend(
        {
            "chart_id": chart_id,
            "status": "uncertain",
            "output_path": None,
            "output_byte_count": None,
            "output_sha256": None,
        }
        for chart_id in unresolved
    )
    rows.extend(
        {
            "chart_id": chart_id,
            "status": "failed",
            "output_path": None,
            "output_byte_count": None,
            "output_sha256": None,
        }
        for chart_id in failed
    )
    rows.extend(
        {
            "chart_id": chart_id,
            "status": status,
            "output_path": None,
            "output_byte_count": None,
            "output_sha256": None,
        }
        for chart_id, status in sorted((other_statuses or {}).items())
    )
    status_counts = {"success": len(charts)}
    if unresolved:
        status_counts["uncertain"] = len(unresolved)
    if failed:
        status_counts["failed"] = len(failed)
    status_counts.update(Counter((other_statuses or {}).values()))
    manifest = {
        "schema_version": 1,
        "canonical_schema_version": "1.1.0",
        "game_fingerprint": _FINGERPRINT,
        "complete": True,
        "chart_file_count": len(charts),
        "status_counts": status_counts,
        "charts": rows,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return manifest


class StoreEquivalenceTests(unittest.TestCase):
    def _compare(self, root: Path, fake: _FakeStore, **kwargs) -> dict:
        with patch(
            "musedash_chart_extractor.store.equivalence.ChartStore.open",
            return_value=fake,
        ):
            return compare_chart_store_to_canonical_tree(
                root / "store", root / "legacy", **kwargs
            )

    def test_equivalent_tree_is_streamed_one_chart_at_a_time(self) -> None:
        charts = {
            "fixture_map1": _chart("fixture_map1"),
            "fixture_map2": _chart("fixture_map2"),
            "fixture_map3": _chart("fixture_map3"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            legacy.mkdir()
            _write_legacy_tree(legacy, charts, unresolved=("tutorial_map1",))
            fake = _FakeStore(
                charts,
                unresolved=("tutorial_map1",),
                enforce_one_at_a_time=True,
            )

            report = self._compare(root, fake)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["mismatch_count"], 0)
        self.assertEqual(report["comparison"]["compared_chart_count"], 3)
        self.assertEqual(report["comparison"]["equivalent_chart_count"], 3)
        self.assertEqual(report["comparison"]["legacy_raw_record_count"], 6)
        self.assertEqual(report["comparison"]["store_logical_event_count"], 3)
        self.assertEqual(
            report["comparison"]["legacy_canonical_digest"],
            report["comparison"]["store_canonical_digest"],
        )
        self.assertEqual(
            report["unresolved"],
            {"legacy_count": 1, "store_count": 1, "id_sets_equal": True},
        )
        self.assertEqual(fake.load_order, sorted(charts))
        self.assertEqual(fake.max_live_loaded_charts, 1)

    def test_canonical_mismatch_records_only_hashes_and_capped_metadata(self) -> None:
        legacy_charts = {
            "fixture_map1": _chart("fixture_map1", marker="old-secret"),
            "fixture_map2": _chart("fixture_map2", marker="old-secret"),
        }
        store_charts = {
            "fixture_map1": _chart("fixture_map1", marker="new-secret"),
            "fixture_map2": _chart("fixture_map2", marker="new-secret"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            legacy.mkdir()
            _write_legacy_tree(legacy, legacy_charts)

            report = self._compare(
                root, _FakeStore(store_charts), mismatch_sample_limit=1
            )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["mismatch_counts"]["canonical_mismatches"], 2)
        self.assertEqual(len(report["mismatches"]["canonical_mismatches"]), 1)
        sample = report["mismatches"]["canonical_mismatches"][0]
        self.assertEqual(sample["chart_id"], "fixture_map1")
        self.assertEqual(len(sample["expected_sha256"]), 64)
        self.assertEqual(len(sample["actual_sha256"]), 64)
        rendered_report = json.dumps(report, sort_keys=True)
        self.assertNotIn("old-secret", rendered_report)
        self.assertNotIn("new-secret", rendered_report)

    def test_failed_and_other_status_id_sets_must_match(self) -> None:
        charts = {"fixture_map1": _chart("fixture_map1")}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            legacy.mkdir()
            _write_legacy_tree(legacy, charts, failed=("legacy_failed_map1",))

            report = self._compare(root, _FakeStore(charts))

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["id_set_mismatches"], 0)
        self.assertTrue(
            any(
                sample.get("chart_id") == "legacy_failed_map1"
                for sample in report["mismatches"]["id_set_mismatches"]
            )
        )

    def test_matching_unknown_status_is_still_rejected(self) -> None:
        charts = {"fixture_map1": _chart("fixture_map1")}
        unknown = {"mystery_map1": "mystery"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            legacy.mkdir()
            _write_legacy_tree(legacy, charts, other_statuses=unknown)

            report = self._compare(
                root, _FakeStore(charts, other_statuses=unknown)
            )

        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["id_set_mismatches"], 0)

    def test_traversing_legacy_output_path_fails_closed(self) -> None:
        charts = {"fixture_map1": _chart("fixture_map1")}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            legacy.mkdir()
            _write_legacy_tree(
                legacy,
                charts,
                path_overrides={"fixture_map1": "../outside.json"},
            )

            report = self._compare(root, _FakeStore(charts))

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["mismatch_counts"]["legacy_file_mismatches"], 1)
        self.assertEqual(
            report["mismatches"]["legacy_file_mismatches"][0]["issue"],
            "legacy canonical output path is unsafe",
        )

    def test_missing_legacy_output_file_fails_closed(self) -> None:
        charts = {"fixture_map1": _chart("fixture_map1")}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            legacy.mkdir()
            _write_legacy_tree(
                legacy, charts, omitted_files=frozenset({"fixture_map1"})
            )

            report = self._compare(root, _FakeStore(charts))

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["mismatch_counts"]["legacy_file_mismatches"], 1)
        self.assertEqual(report["comparison"]["legacy_loaded_chart_count"], 0)
        self.assertEqual(report["comparison"]["store_loaded_chart_count"], 1)

    def test_fingerprint_schema_and_success_id_sets_are_required(self) -> None:
        legacy_charts = {"fixture_map1": _chart("fixture_map1")}
        store_charts = {"fixture_map2": _chart("fixture_map2")}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            legacy.mkdir()
            manifest = _write_legacy_tree(legacy, legacy_charts)
            manifest["canonical_schema_version"] = "1.0.0"
            manifest["game_fingerprint"] = "sha256:" + "9" * 64
            (legacy / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8"
            )

            report = self._compare(root, _FakeStore(store_charts))

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["mismatch_counts"]["manifest_mismatches"], 2)
        self.assertEqual(report["mismatch_counts"]["id_set_mismatches"], 2)
        self.assertFalse(report["comparison"]["success_id_sets_equal"])


if __name__ == "__main__":
    unittest.main()
