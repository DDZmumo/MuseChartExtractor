"""Streaming semantic comparison of a Chart Store and a canonical JSON tree."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from ..batch import BATCH_MANIFEST_SCHEMA_VERSION
from ..charts.models import CANONICAL_SCHEMA_VERSION
from .reader import ChartStore
from .schema import (
    STORE_SCHEMA_VERSION,
    ChartStoreError,
    contained_path,
    path_is_link,
    reject_symlink_path,
    stable_json,
)

EQUIVALENCE_REPORT_SCHEMA_VERSION = 1
DEFAULT_MISMATCH_SAMPLE_LIMIT = 10
_MISMATCH_CATEGORIES = (
    "manifest_mismatches",
    "id_set_mismatches",
    "legacy_file_mismatches",
    "store_load_mismatches",
    "canonical_mismatches",
)


class _Mismatches:
    def __init__(self, sample_limit: int) -> None:
        self.sample_limit = sample_limit
        self.counts = {category: 0 for category in _MISMATCH_CATEGORIES}
        self.samples: dict[str, list[dict[str, Any]]] = {
            category: [] for category in _MISMATCH_CATEGORIES
        }
        self.chart_ids: set[str] = set()

    def add(
        self,
        category: str,
        issue: str,
        *,
        chart_id: str | None = None,
        expected_sha256: str | None = None,
        actual_sha256: str | None = None,
    ) -> None:
        self.counts[category] += 1
        if chart_id is not None:
            self.chart_ids.add(chart_id)
        values: dict[str, Any] = {"issue": issue}
        if chart_id is not None:
            values["chart_id"] = chart_id
        if expected_sha256 is not None:
            values["expected_sha256"] = expected_sha256
        if actual_sha256 is not None:
            values["actual_sha256"] = actual_sha256
        if len(self.samples[category]) < self.sample_limit:
            self.samples[category].append(values)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_root(path: str | Path) -> Path:
    requested = Path(path).expanduser()
    if path_is_link(requested):
        raise ChartStoreError(
            "canonical JSON root must not be a symbolic link or junction"
        )
    root = requested.resolve(strict=False)
    if not root.is_dir():
        raise ChartStoreError(f"canonical JSON root is not a directory: {root}")
    return root


def _read_manifest(
    root: Path, mismatches: _Mismatches
) -> tuple[dict[str, Any] | None, int | None, str | None]:
    path = root / "manifest.json"
    try:
        reject_symlink_path(root, path, context="canonical manifest")
    except ChartStoreError:
        mismatches.add("manifest_mismatches", "legacy manifest path is unsafe")
        return None, None, None
    if not path.is_file():
        mismatches.add("manifest_mismatches", "legacy manifest is missing")
        return None, None, None
    try:
        content = path.read_bytes()
    except OSError as exc:
        mismatches.add(
            "manifest_mismatches",
            f"legacy manifest cannot be read: {type(exc).__name__}",
        )
        return None, None, None
    fingerprint = hashlib.sha256(content).hexdigest()
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        mismatches.add(
            "manifest_mismatches",
            f"legacy manifest is invalid JSON: {type(exc).__name__}",
        )
        return None, len(content), fingerprint
    if not isinstance(value, dict):
        mismatches.add("manifest_mismatches", "legacy manifest root is not an object")
        return None, len(content), fingerprint
    return value, len(content), fingerprint


def _manifest_rows(
    manifest: Mapping[str, Any], mismatches: _Mismatches
) -> list[Mapping[str, Any]]:
    value = manifest.get("charts")
    if not isinstance(value, list):
        mismatches.add("manifest_mismatches", "legacy manifest charts is not an array")
        return []
    rows: list[Mapping[str, Any]] = []
    exact_ids: set[str] = set()
    casefold_ids: set[str] = set()
    for position, row in enumerate(value):
        if not isinstance(row, Mapping):
            mismatches.add(
                "manifest_mismatches",
                f"legacy chart row {position} is not an object",
            )
            continue
        chart_id = row.get("chart_id")
        if not isinstance(chart_id, str) or not chart_id:
            mismatches.add(
                "manifest_mismatches",
                f"legacy chart row {position} has no chart ID",
            )
            continue
        folded = chart_id.casefold()
        if chart_id in exact_ids:
            mismatches.add(
                "id_set_mismatches", "duplicate legacy chart ID", chart_id=chart_id
            )
            continue
        if folded in casefold_ids:
            mismatches.add(
                "id_set_mismatches",
                "case-insensitive legacy chart ID collision",
                chart_id=chart_id,
            )
            continue
        exact_ids.add(chart_id)
        casefold_ids.add(folded)
        rows.append(row)
    return rows


def _safe_output_path(
    root: Path,
    row: Mapping[str, Any],
    chart_id: str,
    mismatches: _Mismatches,
) -> Path | None:
    value = row.get("output_path")
    if not isinstance(value, str) or not value:
        mismatches.add(
            "legacy_file_mismatches",
            "successful legacy row has no output path",
            chart_id=chart_id,
        )
        return None
    try:
        destination = contained_path(
            root, value, context="legacy canonical output path"
        )
        portable = PurePosixPath(value)
        unresolved = root.joinpath(*portable.parts)
        reject_symlink_path(root, unresolved, context="legacy canonical output")
    except ChartStoreError:
        mismatches.add(
            "legacy_file_mismatches",
            "legacy canonical output path is unsafe",
            chart_id=chart_id,
        )
        return None
    if not destination.is_file():
        mismatches.add(
            "legacy_file_mismatches",
            "legacy canonical output file is missing",
            chart_id=chart_id,
        )
        return None
    return destination


def _read_legacy_chart(
    root: Path,
    row: Mapping[str, Any],
    chart_id: str,
    mismatches: _Mismatches,
) -> Mapping[str, Any] | None:
    path = _safe_output_path(root, row, chart_id, mismatches)
    if path is None:
        return None
    try:
        content = path.read_bytes()
    except OSError as exc:
        mismatches.add(
            "legacy_file_mismatches",
            f"legacy canonical output cannot be read: {type(exc).__name__}",
            chart_id=chart_id,
        )
        return None
    actual_sha256 = hashlib.sha256(content).hexdigest()
    expected_size = row.get("output_byte_count")
    expected_sha256 = row.get("output_sha256")
    reportable_expected_sha256 = (
        expected_sha256
        if isinstance(expected_sha256, str)
        and len(expected_sha256) == 64
        and all(character in "0123456789abcdef" for character in expected_sha256)
        else None
    )
    if (
        not _integer(expected_size)
        or expected_size != len(content)
        or not isinstance(expected_sha256, str)
        or expected_sha256 != actual_sha256
    ):
        mismatches.add(
            "legacy_file_mismatches",
            "legacy canonical output fingerprint differs from manifest",
            chart_id=chart_id,
            expected_sha256=reportable_expected_sha256,
            actual_sha256=actual_sha256,
        )
    try:
        chart = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        mismatches.add(
            "legacy_file_mismatches",
            f"legacy canonical output is invalid JSON: {type(exc).__name__}",
            chart_id=chart_id,
        )
        return None
    if not isinstance(chart, Mapping):
        mismatches.add(
            "legacy_file_mismatches",
            "legacy canonical output root is not an object",
            chart_id=chart_id,
        )
        return None
    if chart.get("schema_version") != CANONICAL_SCHEMA_VERSION:
        mismatches.add(
            "canonical_mismatches",
            "legacy chart has an unexpected canonical schema version",
            chart_id=chart_id,
        )
    if chart.get("chart_id") != chart_id:
        mismatches.add(
            "canonical_mismatches",
            "legacy chart identity differs from manifest",
            chart_id=chart_id,
        )
    return chart


def update_canonical_corpus_digest(
    digest: Any, chart_id: str, canonical_json: bytes
) -> None:
    chart_id_bytes = chart_id.encode("utf-8")
    digest.update(len(chart_id_bytes).to_bytes(8, "little"))
    digest.update(chart_id_bytes)
    digest.update(len(canonical_json).to_bytes(8, "little"))
    digest.update(canonical_json)


def canonical_chart_counts(chart: Mapping[str, Any]) -> tuple[int, int]:
    raw = chart.get("raw_record_count")
    if not _integer(raw):
        raw_evidence = chart.get("raw")
        experimental = (
            raw_evidence.get("experimental_chart")
            if isinstance(raw_evidence, Mapping)
            else None
        )
        raw_records = (
            experimental.get("raw_records")
            if isinstance(experimental, Mapping)
            else None
        )
        if isinstance(raw_records, Sequence) and not isinstance(
            raw_records, (str, bytes, bytearray)
        ):
            raw = len(raw_records)
    events = chart.get("event_count")
    return (
        raw if _integer(raw) else 0,
        events if _integer(events) else 0,
    )


def compare_chart_store_to_canonical_tree(
    store_dir: str | Path,
    canonical_dir: str | Path,
    *,
    mismatch_sample_limit: int = DEFAULT_MISMATCH_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Compare canonical charts one at a time without retaining a corpus in memory."""

    if not _integer(mismatch_sample_limit) or mismatch_sample_limit < 0:
        raise ValueError("mismatch_sample_limit must be a non-negative integer")
    root = _safe_root(canonical_dir)
    mismatches = _Mismatches(mismatch_sample_limit)
    manifest, manifest_size, manifest_sha256 = _read_manifest(root, mismatches)

    with ChartStore.open(store_dir) as store:
        store_refs = list(store.iter_charts())
        store_status_counts = Counter(ref.status for ref in store_refs)
        store_by_id: dict[str, Any] = {}
        store_casefold_ids: set[str] = set()
        for ref in store_refs:
            if ref.chart_id in store_by_id:
                mismatches.add(
                    "id_set_mismatches",
                    "duplicate Store chart ID",
                    chart_id=ref.chart_id,
                )
                continue
            folded = ref.chart_id.casefold()
            if folded in store_casefold_ids:
                mismatches.add(
                    "id_set_mismatches",
                    "case-insensitive Store chart ID collision",
                    chart_id=ref.chart_id,
                )
                continue
            store_casefold_ids.add(folded)
            store_by_id[ref.chart_id] = ref

        report: dict[str, Any] = {
            "schema_version": EQUIVALENCE_REPORT_SCHEMA_VERSION,
            "status": "failed",
            "store_schema_version": store.manifest.get(
                "store_schema_version", STORE_SCHEMA_VERSION
            ),
            "canonical_schema_version": store.metadata.get(
                "canonical_schema_version"
            ),
            "inventory_fingerprint": store.metadata.get("inventory_fingerprint"),
            "legacy": {
                "root_name": root.name,
                "manifest": {
                    "relative_path": "manifest.json",
                    "byte_count": manifest_size,
                    "sha256": manifest_sha256,
                },
                "status_counts": {},
            },
            "store": {
                "root_name": Path(store_dir).name,
                "logical_store_digest": store.manifest.get("logical_store_digest"),
                "status_counts": dict(sorted(store_status_counts.items())),
            },
            "unresolved": {
                "legacy_count": 0,
                "store_count": store_status_counts.get("uncertain", 0),
                "id_sets_equal": False,
            },
            "comparison": {
                "success_id_sets_equal": False,
                "compared_chart_count": 0,
                "equivalent_chart_count": 0,
                "mismatch_chart_count": 0,
                "legacy_loaded_chart_count": 0,
                "store_loaded_chart_count": 0,
                "legacy_semantic_byte_count": 0,
                "store_semantic_byte_count": 0,
                "legacy_raw_record_count": 0,
                "store_raw_record_count": 0,
                "legacy_logical_event_count": 0,
                "store_logical_event_count": 0,
                "legacy_canonical_digest": None,
                "store_canonical_digest": None,
            },
            "mismatch_count": 0,
            "mismatch_counts": mismatches.counts,
            "mismatches": mismatches.samples,
        }

        if manifest is None:
            report["mismatch_count"] = mismatches.total
            return report

        legacy_schema = manifest.get("canonical_schema_version")
        store_schema = store.metadata.get("canonical_schema_version")
        if manifest.get("schema_version") != BATCH_MANIFEST_SCHEMA_VERSION:
            mismatches.add(
                "manifest_mismatches", "legacy manifest schema version is unsupported"
            )
        if legacy_schema != CANONICAL_SCHEMA_VERSION or legacy_schema != store_schema:
            mismatches.add(
                "manifest_mismatches",
                "legacy and Store canonical schema versions differ",
            )
        legacy_fingerprint = manifest.get("game_fingerprint")
        store_fingerprint = store.metadata.get("inventory_fingerprint")
        if legacy_fingerprint != store_fingerprint:
            mismatches.add(
                "manifest_mismatches", "legacy and Store inventory fingerprints differ"
            )
        if manifest.get("complete") is not True:
            mismatches.add("manifest_mismatches", "legacy manifest is not complete")

        legacy_rows = _manifest_rows(manifest, mismatches)
        legacy_status_counts = Counter(str(row.get("status")) for row in legacy_rows)
        report["legacy"]["status_counts"] = dict(sorted(legacy_status_counts.items()))
        declared_status_counts = manifest.get("status_counts")
        if declared_status_counts != dict(sorted(legacy_status_counts.items())):
            mismatches.add(
                "manifest_mismatches",
                "legacy manifest status counts differ from chart rows",
            )
        if manifest.get("chart_file_count") != legacy_status_counts.get("success", 0):
            mismatches.add(
                "manifest_mismatches",
                "legacy manifest chart file count differs from successful rows",
            )

        legacy_by_id = {str(row["chart_id"]): row for row in legacy_rows}
        supported_statuses = {"success", "uncertain", "failed"}
        for chart_id, row in legacy_by_id.items():
            if row.get("status") not in supported_statuses:
                mismatches.add(
                    "id_set_mismatches",
                    "legacy chart has an unsupported status",
                    chart_id=chart_id,
                )
        for chart_id, ref in store_by_id.items():
            if ref.status not in supported_statuses:
                mismatches.add(
                    "id_set_mismatches",
                    "Store chart has an unsupported status",
                    chart_id=chart_id,
                )
        legacy_other_status = {
            chart_id: str(row.get("status"))
            for chart_id, row in legacy_by_id.items()
            if row.get("status") not in {"success", "uncertain"}
        }
        store_other_status = {
            chart_id: str(ref.status)
            for chart_id, ref in store_by_id.items()
            if ref.status not in {"success", "uncertain"}
        }
        for chart_id in sorted(
            set(legacy_other_status) | set(store_other_status),
            key=lambda value: (value.casefold(), value),
        ):
            if legacy_other_status.get(chart_id) != store_other_status.get(chart_id):
                mismatches.add(
                    "id_set_mismatches",
                    "non-exportable chart status differs between legacy and Store",
                    chart_id=chart_id,
                )
        legacy_success = {
            chart_id
            for chart_id, row in legacy_by_id.items()
            if row.get("status") == "success"
        }
        store_success = {
            chart_id for chart_id, ref in store_by_id.items() if ref.status == "success"
        }
        success_equal = legacy_success == store_success
        report["comparison"]["success_id_sets_equal"] = success_equal
        for chart_id in sorted(legacy_success - store_success, key=lambda x: (x.casefold(), x)):
            mismatches.add(
                "id_set_mismatches",
                "successful legacy chart is absent from Store",
                chart_id=chart_id,
            )
        for chart_id in sorted(store_success - legacy_success, key=lambda x: (x.casefold(), x)):
            mismatches.add(
                "id_set_mismatches",
                "successful Store chart is absent from legacy tree",
                chart_id=chart_id,
            )

        legacy_unresolved = {
            chart_id
            for chart_id, row in legacy_by_id.items()
            if row.get("status") == "uncertain"
        }
        store_unresolved = {
            chart_id for chart_id, ref in store_by_id.items() if ref.status == "uncertain"
        }
        report["unresolved"] = {
            "legacy_count": len(legacy_unresolved),
            "store_count": len(store_unresolved),
            "id_sets_equal": legacy_unresolved == store_unresolved,
        }
        for chart_id in sorted(
            legacy_unresolved - store_unresolved, key=lambda x: (x.casefold(), x)
        ):
            mismatches.add(
                "id_set_mismatches",
                "unresolved legacy chart is absent from Store",
                chart_id=chart_id,
            )
        for chart_id in sorted(
            store_unresolved - legacy_unresolved, key=lambda x: (x.casefold(), x)
        ):
            mismatches.add(
                "id_set_mismatches",
                "unresolved Store chart is absent from legacy manifest",
                chart_id=chart_id,
            )

        legacy_digest = hashlib.sha256()
        store_digest = hashlib.sha256()
        ordered_success_ids = sorted(
            legacy_success | store_success, key=lambda value: (value.casefold(), value)
        )
        for chart_id in ordered_success_ids:
            legacy_chart: Mapping[str, Any] | None = None
            store_chart: Mapping[str, Any] | None = None
            legacy_json: bytes | None = None
            store_json: bytes | None = None
            if chart_id in legacy_success:
                legacy_chart = _read_legacy_chart(
                    root, legacy_by_id[chart_id], chart_id, mismatches
                )
                if legacy_chart is not None:
                    legacy_json = stable_json(legacy_chart).encode("utf-8")
                    update_canonical_corpus_digest(legacy_digest, chart_id, legacy_json)
                    report["comparison"]["legacy_loaded_chart_count"] += 1
                    report["comparison"]["legacy_semantic_byte_count"] += len(
                        legacy_json
                    )
                    raw_count, event_count = canonical_chart_counts(legacy_chart)
                    legacy_row = legacy_by_id[chart_id]
                    if (
                        legacy_row.get("raw_record_count") != raw_count
                        or legacy_row.get("event_count") != event_count
                    ):
                        mismatches.add(
                            "manifest_mismatches",
                            "legacy chart counts differ from canonical content",
                            chart_id=chart_id,
                        )
                    report["comparison"]["legacy_raw_record_count"] += raw_count
                    report["comparison"]["legacy_logical_event_count"] += event_count
            if chart_id in store_success:
                try:
                    loaded = store.load_chart(chart_id)
                except Exception as exc:
                    mismatches.add(
                        "store_load_mismatches",
                        f"Store chart cannot be reconstructed: {type(exc).__name__}",
                        chart_id=chart_id,
                    )
                else:
                    if not isinstance(loaded, Mapping):
                        mismatches.add(
                            "store_load_mismatches",
                            "Store chart reconstruction is not an object",
                            chart_id=chart_id,
                        )
                        del loaded
                    else:
                        store_chart = loaded
                        del loaded
                        store_json = stable_json(store_chart).encode("utf-8")
                        update_canonical_corpus_digest(store_digest, chart_id, store_json)
                        report["comparison"]["store_loaded_chart_count"] += 1
                        report["comparison"]["store_semantic_byte_count"] += len(
                            store_json
                        )
                        raw_count, event_count = canonical_chart_counts(store_chart)
                        store_ref = store_by_id[chart_id]
                        if (
                            getattr(store_ref, "raw_record_count", raw_count) != raw_count
                            or getattr(
                                store_ref, "logical_event_count", event_count
                            )
                            != event_count
                        ):
                            mismatches.add(
                                "store_load_mismatches",
                                "Store chart counts differ from canonical content",
                                chart_id=chart_id,
                            )
                        report["comparison"]["store_raw_record_count"] += raw_count
                        report["comparison"]["store_logical_event_count"] += event_count
            if legacy_json is not None and store_json is not None:
                report["comparison"]["compared_chart_count"] += 1
                if legacy_json == store_json:
                    report["comparison"]["equivalent_chart_count"] += 1
                else:
                    mismatches.add(
                        "canonical_mismatches",
                        "canonical JSON objects differ",
                        chart_id=chart_id,
                        expected_sha256=hashlib.sha256(legacy_json).hexdigest(),
                        actual_sha256=hashlib.sha256(store_json).hexdigest(),
                    )

            # Keep peak memory bounded by the largest legacy/store chart pair.
            del legacy_chart, store_chart, legacy_json, store_json

        report["comparison"]["legacy_canonical_digest"] = legacy_digest.hexdigest()
        report["comparison"]["store_canonical_digest"] = store_digest.hexdigest()
        report["comparison"]["mismatch_chart_count"] = len(mismatches.chart_ids)
        report["mismatch_count"] = mismatches.total
        report["status"] = "passed" if mismatches.total == 0 else "failed"
        return report


__all__ = [
    "DEFAULT_MISMATCH_SAMPLE_LIMIT",
    "EQUIVALENCE_REPORT_SCHEMA_VERSION",
    "canonical_chart_counts",
    "compare_chart_store_to_canonical_tree",
    "update_canonical_corpus_digest",
]
