"""Store-only streaming Canonical digest without expanded chart files."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..charts.models import CANONICAL_SCHEMA_VERSION
from ..scanner import fingerprint_file
from .equivalence import canonical_chart_counts, update_canonical_corpus_digest
from .reader import ChartStore
from .schema import STORE_MANIFEST_NAME, ChartStoreError, stable_json

CANONICAL_DIGEST_REPORT_SCHEMA_VERSION = 1
DEFAULT_FAILURE_SAMPLE_LIMIT = 10
ID_SET_DIGEST_ALGORITHM = (
    "sha256 of casefold/original sorted UTF-8 IDs, each framed by an 8-byte "
    "little-endian length"
)
_EXPECTED_FIELDS = {
    "inventory_fingerprint",
    "canonical_corpus_digest",
    "resolved_id_set_digest",
    "uncertain_id_set_digest",
    "resolved_chart_count",
    "resolved_raw_record_count",
    "resolved_event_count",
    "resolved_sentinel_count",
    "semantic_byte_count",
}
_COUNT_EXPECTED_FIELDS = {
    "resolved_chart_count",
    "resolved_raw_record_count",
    "resolved_event_count",
    "resolved_sentinel_count",
    "semantic_byte_count",
}
_DIGEST_EXPECTED_FIELDS = {
    "canonical_corpus_digest",
    "resolved_id_set_digest",
    "uncertain_id_set_digest",
}
_MISMATCH_CATEGORIES = (
    "manifest_mismatches",
    "payload_mismatches",
    "canonical_load_mismatches",
    "canonical_count_mismatches",
    "expected_mismatches",
)


class _Failures:
    def __init__(self, sample_limit: int) -> None:
        self.sample_limit = sample_limit
        self.counts = {category: 0 for category in _MISMATCH_CATEGORIES}
        self.samples: list[dict[str, Any]] = []

    def add(
        self,
        category: str,
        issue: str,
        *,
        chart_id: str | None = None,
        field: str | None = None,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        self.counts[category] += 1
        if len(self.samples) >= self.sample_limit:
            return
        row: dict[str, Any] = {"category": category, "issue": issue}
        if chart_id is not None:
            row["chart_id"] = chart_id
        if field is not None:
            row["field"] = field
            row["expected"] = expected
            row["actual"] = actual
        self.samples.append(row)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _id_set_digest(values: set[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values, key=lambda item: (item.casefold(), item)):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "little"))
        digest.update(encoded)
    return digest.hexdigest()


def _canonical_sentinel_count(chart: Mapping[str, Any]) -> int:
    raw = chart.get("raw")
    experimental = raw.get("experimental_chart") if isinstance(raw, Mapping) else None
    groups = (
        experimental.get("record_groups")
        if isinstance(experimental, Mapping)
        else None
    )
    if not isinstance(groups, list):
        raise ChartStoreError("canonical chart has no record_groups array")
    count = 0
    for group in groups:
        if not isinstance(group, Mapping):
            raise ChartStoreError("canonical record group is not an object")
        if group.get("role_status") == "observed-sentinel":
            count += 1
    return count


def _validate_expected(expected: Mapping[str, Any] | None) -> dict[str, Any]:
    if expected is None:
        return {}
    unknown = set(expected) - _EXPECTED_FIELDS
    if unknown:
        raise ValueError(f"unsupported expected fields: {sorted(unknown)!r}")
    result = dict(expected)
    for field in _COUNT_EXPECTED_FIELDS:
        if field in result and (
            not _is_integer(result[field]) or result[field] < 0
        ):
            raise ValueError(f"expected {field} must be a non-negative integer")
    for field in _DIGEST_EXPECTED_FIELDS:
        if field in result and (
            not isinstance(result[field], str)
            or len(result[field]) != 64
            or any(character not in "0123456789abcdef" for character in result[field])
        ):
            raise ValueError(f"expected {field} must be a lowercase SHA-256")
    if "inventory_fingerprint" in result:
        value = result["inventory_fingerprint"]
        if (
            not isinstance(value, str)
            or not value.startswith("sha256:")
            or len(value) != 71
            or any(character not in "0123456789abcdef" for character in value[7:])
        ):
            raise ValueError(
                "expected inventory_fingerprint must be a sha256:<lowercase hex> value"
            )
    return result


def _manifest_rows(
    manifest: Mapping[str, Any], failures: _Failures
) -> dict[str, Mapping[str, Any]]:
    rows = manifest.get("charts")
    if not isinstance(rows, list):
        failures.add("manifest_mismatches", "store manifest charts is not an array")
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    casefold_ids: set[str] = set()
    for position, row in enumerate(rows):
        if not isinstance(row, Mapping):
            failures.add(
                "manifest_mismatches",
                f"store manifest chart {position} is not an object",
            )
            continue
        chart_id = row.get("chart_id")
        if not isinstance(chart_id, str) or not chart_id:
            failures.add(
                "manifest_mismatches",
                f"store manifest chart {position} has no chart_id",
            )
            continue
        folded = chart_id.casefold()
        if chart_id in result or folded in casefold_ids:
            failures.add(
                "manifest_mismatches",
                "store manifest has duplicate or case-colliding chart IDs",
                chart_id=chart_id,
            )
            continue
        result[chart_id] = row
        casefold_ids.add(folded)
    return result


def _compare_manifest_ref(
    row: Mapping[str, Any], ref: Any, failures: _Failures
) -> None:
    fields = {
        "status": ref.status,
        "raw_record_count": ref.raw_record_count,
        "logical_event_count": ref.logical_event_count,
        "sentinel_count": ref.sentinel_count,
        "payload_sha256": ref.payload_sha256,
    }
    for field, actual in fields.items():
        expected = row.get(field)
        if expected != actual:
            failures.add(
                "manifest_mismatches",
                "Store manifest chart row differs from SQLite reference",
                chart_id=ref.chart_id,
                field=field,
                expected=expected,
                actual=actual,
            )


def digest_chart_store(
    store_dir: str | Path,
    *,
    expected: Mapping[str, Any] | None = None,
    failure_sample_limit: int = DEFAULT_FAILURE_SAMPLE_LIMIT,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Stream resolved charts into the legacy-compatible Canonical corpus digest."""

    if not _is_integer(failure_sample_limit) or failure_sample_limit < 0:
        raise ValueError("failure_sample_limit must be a non-negative integer")
    expected_values = _validate_expected(expected)
    failures = _Failures(failure_sample_limit)
    corpus_digest = hashlib.sha256()

    with ChartStore.open(store_dir) as store:
        status_ids: dict[str, set[str]] = defaultdict(set)
        status_counts: Counter[str] = Counter()
        manifest_by_id = _manifest_rows(store.manifest, failures)
        ref_ids: set[str] = set()
        payload_sizes: dict[str, int] = {}
        all_raw = 0
        all_events = 0
        all_sentinels = 0

        for ref in store.iter_charts():
            if ref.chart_id in ref_ids:
                failures.add(
                    "manifest_mismatches",
                    "SQLite returned a duplicate chart ID",
                    chart_id=ref.chart_id,
                )
                continue
            ref_ids.add(ref.chart_id)
            status_ids[ref.status].add(ref.chart_id)
            status_counts[ref.status] += 1
            row = manifest_by_id.get(ref.chart_id)
            if row is None:
                failures.add(
                    "manifest_mismatches",
                    "SQLite chart is absent from Store manifest",
                    chart_id=ref.chart_id,
                )
            else:
                _compare_manifest_ref(row, ref, failures)
            if _is_integer(ref.raw_record_count):
                all_raw += ref.raw_record_count
            if _is_integer(ref.logical_event_count):
                all_events += ref.logical_event_count
            if _is_integer(ref.sentinel_count):
                all_sentinels += ref.sentinel_count
            if ref.payload_sha256 is not None and ref.payload_byte_count is not None:
                previous = payload_sizes.setdefault(
                    ref.payload_sha256, ref.payload_byte_count
                )
                if previous != ref.payload_byte_count:
                    failures.add(
                        "manifest_mismatches",
                        "one payload SHA has inconsistent byte counts",
                        chart_id=ref.chart_id,
                    )

        for chart_id in sorted(
            set(manifest_by_id) - ref_ids,
            key=lambda value: (value.casefold(), value),
        ):
            failures.add(
                "manifest_mismatches",
                "Store manifest chart is absent from SQLite",
                chart_id=chart_id,
            )

        manifest_checks = {
            "candidate_count": len(ref_ids),
            "status_counts": dict(sorted(status_counts.items())),
            "payload_count": len(payload_sizes),
            "payload_byte_count": sum(payload_sizes.values()),
            "raw_record_count": all_raw,
            "logical_event_count": all_events,
            "sentinel_count": all_sentinels,
        }
        for field, actual in manifest_checks.items():
            declared = store.manifest.get(field)
            if declared != actual:
                failures.add(
                    "manifest_mismatches",
                    "Store manifest aggregate differs from SQLite references",
                    field=field,
                    expected=declared,
                    actual=actual,
                )

        resolved_ids = status_ids.get("success", set())
        uncertain_ids = status_ids.get("uncertain", set())
        failed_ids = status_ids.get("failed", set())
        resolved_raw = 0
        resolved_events = 0
        resolved_sentinels = 0
        semantic_bytes = 0
        loaded_count = 0
        ordered_resolved = sorted(
            resolved_ids, key=lambda value: (value.casefold(), value)
        )
        total_resolved = len(ordered_resolved)
        for position, chart_id in enumerate(ordered_resolved, start=1):
            try:
                chart = store.load_chart(chart_id)
                canonical_json = stable_json(chart).encode("utf-8")
                raw_count, event_count = canonical_chart_counts(chart)
                sentinel_count = _canonical_sentinel_count(chart)
                update_canonical_corpus_digest(
                    corpus_digest, chart_id, canonical_json
                )
            except Exception as exc:
                failures.add(
                    "canonical_load_mismatches",
                    f"Store chart cannot be reconstructed: {type(exc).__name__}",
                    chart_id=chart_id,
                )
            else:
                loaded_count += 1
                semantic_bytes += len(canonical_json)
                resolved_raw += raw_count
                resolved_events += event_count
                resolved_sentinels += sentinel_count
                ref_row = manifest_by_id.get(chart_id)
                if ref_row is not None:
                    expected_counts = {
                        "raw_record_count": raw_count,
                        "logical_event_count": event_count,
                        "sentinel_count": sentinel_count,
                    }
                    for field, actual in expected_counts.items():
                        if ref_row.get(field) != actual:
                            failures.add(
                                "canonical_count_mismatches",
                                "Canonical content count differs from Store row",
                                chart_id=chart_id,
                                field=field,
                                expected=ref_row.get(field),
                                actual=actual,
                            )
                del chart, canonical_json
            if progress is not None:
                progress(position, total_resolved, chart_id)

        for chart_id in sorted(
            uncertain_ids | failed_ids,
            key=lambda value: (value.casefold(), value),
        ):
            row = manifest_by_id.get(chart_id)
            payload_sha = row.get("payload_sha256") if row is not None else None
            if payload_sha is None:
                continue
            try:
                payload = store.read_payload(chart_id)
            except Exception as exc:
                failures.add(
                    "payload_mismatches",
                    f"non-success payload cannot be verified: {type(exc).__name__}",
                    chart_id=chart_id,
                )
            else:
                del payload

        manifest_path = store.root / STORE_MANIFEST_NAME
        manifest_size, manifest_sha256, _prefix = fingerprint_file(manifest_path)
        canonical = {
            "resolved_chart_count": loaded_count,
            "raw_record_count": resolved_raw,
            "logical_event_count": resolved_events,
            "sentinel_count": resolved_sentinels,
            "semantic_byte_count": semantic_bytes,
            "corpus_digest": corpus_digest.hexdigest(),
        }
        id_sets = {
            "resolved": {
                "count": len(resolved_ids),
                "digest": _id_set_digest(resolved_ids),
            },
            "uncertain": {
                "count": len(uncertain_ids),
                "digest": _id_set_digest(uncertain_ids),
            },
            "failed": {
                "count": len(failed_ids),
                "digest": _id_set_digest(failed_ids),
            },
            "algorithm": ID_SET_DIGEST_ALGORITHM,
        }
        actual_expected_values = {
            "inventory_fingerprint": store.metadata.get("inventory_fingerprint"),
            "canonical_corpus_digest": canonical["corpus_digest"],
            "resolved_id_set_digest": id_sets["resolved"]["digest"],
            "uncertain_id_set_digest": id_sets["uncertain"]["digest"],
            "resolved_chart_count": canonical["resolved_chart_count"],
            "resolved_raw_record_count": canonical["raw_record_count"],
            "resolved_event_count": canonical["logical_event_count"],
            "resolved_sentinel_count": canonical["sentinel_count"],
            "semantic_byte_count": canonical["semantic_byte_count"],
        }
        for field, expected_value in expected_values.items():
            actual_value = actual_expected_values[field]
            if actual_value != expected_value:
                failures.add(
                    "expected_mismatches",
                    "Store-only Canonical result differs from expected baseline",
                    field=field,
                    expected=expected_value,
                    actual=actual_value,
                )

        report = {
            "schema_version": CANONICAL_DIGEST_REPORT_SCHEMA_VERSION,
            "status": "passed" if failures.total == 0 else "failed",
            "copyright_boundary": (
                "metadata, hashes, counts, and bounded failures only; no chart "
                "events, payload bytes, or expanded Canonical files"
            ),
            "store_schema_version": store.manifest.get("store_schema_version"),
            "canonical_schema_version": store.metadata.get(
                "canonical_schema_version", CANONICAL_SCHEMA_VERSION
            ),
            "inventory_fingerprint": store.metadata.get("inventory_fingerprint"),
            "store": {
                "root_name": store.root.name,
                "manifest_byte_count": manifest_size,
                "manifest_sha256": manifest_sha256,
                "index_byte_count": store.manifest.get("index", {}).get(
                    "byte_count"
                ),
                "index_sha256": store.manifest.get("index", {}).get("sha256"),
                "logical_store_digest": store.manifest.get(
                    "logical_store_digest"
                ),
                "payload_count": store.manifest.get("payload_count"),
                "payload_byte_count": store.manifest.get("payload_byte_count"),
            },
            "status_counts": dict(sorted(status_counts.items())),
            "id_sets": id_sets,
            "manifest_totals": manifest_checks,
            "canonical": canonical,
            "expected": expected_values,
            "mismatch_count": failures.total,
            "mismatch_counts": failures.counts,
            "failure_samples": failures.samples,
            "failure_sample_limit": failure_sample_limit,
        }
        return report


__all__ = [
    "CANONICAL_DIGEST_REPORT_SCHEMA_VERSION",
    "DEFAULT_FAILURE_SAMPLE_LIMIT",
    "ID_SET_DIGEST_ALGORITHM",
    "digest_chart_store",
]
