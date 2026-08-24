"""Small command-line boundary for the current ROADMAP phase."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from . import __version__
from .diagnostics import (
    write_compact_json,
    write_json,
    write_jsonl,
    write_resource_inventory,
    write_text,
)
from .scanner import (
    DEFAULT_LARGE_FILE_THRESHOLD,
    ScannerError,
    build_inventory_fingerprint,
    build_resource_summary,
    fingerprint_file,
    scan_game_directory,
    validate_game_directory,
)
from .store.schema import path_is_link, reject_symlink_path


_PROFILE_GATED_COMMANDS = frozenset(
    {"candidates", "inspect-stageinfo", "extract", "index", "grouping-census"}
)


def _add_unsupported_research_override(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-unsupported-research",
        action="store_true",
        help=(
            "explicitly run this research parser on an unsupported fingerprint; "
            "results are diagnostic-only and do not establish formal support"
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="musedash-chart-extractor",
        description="Read-only, offline Muse Dash resource extraction research tooling.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser(
        "scan",
        help="create a deterministic inventory of a local game directory",
    )
    scan.add_argument("--game-dir", required=True, type=Path)
    scan.add_argument("--output-dir", type=Path, default=Path("diagnostics"))
    scan.add_argument(
        "--large-file-threshold-mib",
        type=int,
        default=DEFAULT_LARGE_FILE_THRESHOLD // (1024 * 1024),
        help="threshold used only for the unknown-large-file summary (default: 16)",
    )

    probe = commands.add_parser(
        "probe",
        help="inventory Unity bundle and serialized-file metadata with UnityPy",
    )
    probe.add_argument("--game-dir", required=True, type=Path)
    probe.add_argument("--output-dir", type=Path, default=Path("diagnostics"))
    probe.add_argument(
        "--max-sources",
        type=int,
        help="development-only source limit; omitted means all candidates",
    )
    probe.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="emit progress after this many sources (default: 100; 0 disables)",
    )

    candidates = commands.add_parser(
        "candidates",
        help="rank verified StageInfo objects without exporting serialized payloads",
    )
    candidates.add_argument("--game-dir", required=True, type=Path)
    candidates.add_argument("--output-dir", type=Path, default=Path("diagnostics"))
    candidates.add_argument(
        "--bundle-inventory",
        type=Path,
        help="Phase 2 bundle_inventory.jsonl (default: OUTPUT_DIR/bundle_inventory.jsonl)",
    )
    candidates.add_argument(
        "--max-sources",
        type=int,
        help="development-only StageInfo source limit; omitted means all candidates",
    )
    candidates.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="emit progress after this many sources (default: 50; 0 disables)",
    )
    _add_unsupported_research_override(candidates)

    inspect = commands.add_parser(
        "inspect-stageinfo",
        help="strictly recover one ranked StageInfo/Odin structure",
    )
    inspect.add_argument("--game-dir", required=True, type=Path)
    inspect.add_argument("--source", required=True, help="candidate source path relative to game-dir")
    inspect.add_argument("--path-id", required=True, type=int)
    inspect.add_argument("--output-dir", type=Path, default=Path("diagnostics"))
    inspect.add_argument(
        "--candidate-file",
        type=Path,
        help="Phase 3 chart_candidates.jsonl (default: OUTPUT_DIR/chart_candidates.jsonl)",
    )
    inspect.add_argument(
        "--sample-records",
        type=int,
        default=2,
        help="bounded record samples written to diagnostics (default: 2; maximum: 10)",
    )
    _add_unsupported_research_override(inspect)

    extract = commands.add_parser(
        "extract",
        help="write one local-only, unvalidated raw StageInfo chart",
    )
    extract.add_argument("--game-dir", required=True, type=Path)
    extract.add_argument("--source", required=True, help="candidate source path relative to game-dir")
    extract.add_argument("--path-id", required=True, type=int)
    extract.add_argument(
        "--candidate-file",
        type=Path,
        default=Path("diagnostics/chart_candidates.jsonl"),
    )
    extract.add_argument(
        "--bundle-inventory",
        type=Path,
        default=Path("diagnostics/bundle_inventory.jsonl"),
        help="Phase 2 inventory used to locate notedata.json",
    )
    extract.add_argument(
        "--output",
        type=Path,
        default=Path("experimental/first_chart.json"),
    )
    _add_unsupported_research_override(extract)

    index = commands.add_parser(
        "index",
        help="recover song, difficulty, and chart relationships",
    )
    index.add_argument("--game-dir", required=True, type=Path)
    index.add_argument(
        "--candidate-file",
        type=Path,
        default=Path("diagnostics/chart_candidates.jsonl"),
    )
    index.add_argument(
        "--bundle-inventory",
        type=Path,
        default=Path("diagnostics/bundle_inventory.jsonl"),
    )
    index.add_argument(
        "--addressables-index",
        type=Path,
        default=Path("diagnostics/addressables_index.json"),
    )
    index.add_argument(
        "--output",
        type=Path,
        default=Path("diagnostics/song_chart_index.json"),
    )
    _add_unsupported_research_override(index)

    canonicalize = commands.add_parser(
        "canonicalize",
        help="convert one Phase 5/6 evidence pair to the canonical model",
    )
    canonicalize.add_argument(
        "--raw-chart",
        type=Path,
        default=Path("experimental/first_chart.json"),
    )
    canonicalize.add_argument(
        "--song-index",
        type=Path,
        default=Path("diagnostics/song_chart_index.json"),
    )
    canonicalize.add_argument(
        "--validation-report",
        type=Path,
        help="optional matching M4 report used to promote validation status",
    )
    canonicalize.add_argument(
        "--output",
        type=Path,
        default=Path("experimental/first_chart_canonical.json"),
    )
    canonicalize.add_argument(
        "--report",
        type=Path,
        help="optional metadata-only canonicalization diagnostic",
    )

    validate = commands.add_parser(
        "validate",
        help="validate one or more canonical charts and write difference reports",
    )
    validate.add_argument(
        "--chart",
        type=Path,
        action="append",
        required=True,
        help="canonical chart JSON; repeat for multiple charts",
    )
    validate.add_argument(
        "--game-dir",
        type=Path,
        help="optional game directory used to verify source existence and SHA-256",
    )
    validate.add_argument(
        "--reference-file",
        type=Path,
        help="optional aggregate reference JSON; never required by extraction",
    )
    validate.add_argument(
        "--output",
        type=Path,
        default=Path("diagnostics/validation_report.json"),
    )
    validate.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("diagnostics/validation_report.md"),
    )

    census = commands.add_parser(
        "grouping-census",
        help="classify raw parse and grouping shapes without exporting events",
    )
    census.add_argument("--game-dir", required=True, type=Path)
    census.add_argument(
        "--candidate-file",
        type=Path,
        default=Path("diagnostics/chart_candidates.jsonl"),
    )
    census.add_argument("--output-dir", type=Path, default=Path("diagnostics"))
    census.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="emit progress after this many bundles (default: 50; 0 disables)",
    )
    _add_unsupported_research_override(census)

    extract_all = commands.add_parser(
        "extract-all",
        help="extract and classify every inventoried chart into canonical local files",
    )
    extract_all.add_argument("--game-dir", required=True, type=Path)
    extract_all.add_argument(
        "--output",
        type=Path,
        default=Path("extracted"),
        help="local-only batch output directory (default: extracted)",
    )
    extract_all.add_argument(
        "--candidate-file",
        type=Path,
        default=Path("diagnostics/chart_candidates.jsonl"),
    )
    extract_all.add_argument(
        "--song-index",
        type=Path,
        default=Path("diagnostics/song_chart_index.json"),
    )
    extract_all.add_argument(
        "--bundle-inventory",
        type=Path,
        default=Path("diagnostics/bundle_inventory.jsonl"),
        help="Phase 2 inventory used to locate notedata.json",
    )
    extract_all.add_argument(
        "--grouping-census-summary",
        type=Path,
        default=Path("diagnostics/grouping_census_summary.json"),
        help="completed Phase 9 grouping census gate",
    )
    extract_all.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="emit progress after this many bundles (default: 50; 0 disables)",
    )
    _add_unsupported_research_override(extract_all)

    extract_store = commands.add_parser(
        "extract-store",
        help="build a compact content-addressed Odin Store for a supported installation",
    )
    extract_store.add_argument("--game-dir", required=True, type=Path)
    extract_store.add_argument(
        "--output",
        type=Path,
        default=Path("MuseDashChartStore"),
        help="compact local Store directory (default: MuseDashChartStore)",
    )
    extract_store.add_argument(
        "--candidate-file",
        type=Path,
        default=Path("diagnostics/chart_candidates.jsonl"),
    )
    extract_store.add_argument(
        "--song-index",
        type=Path,
        default=Path("diagnostics/song_chart_index.json"),
    )
    extract_store.add_argument(
        "--bundle-inventory",
        type=Path,
        default=Path("diagnostics/bundle_inventory.jsonl"),
    )
    extract_store.add_argument(
        "--grouping-census-summary",
        type=Path,
        default=Path("diagnostics/grouping_census_summary.json"),
    )
    extract_store.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="emit progress after this many bundles (default: 50; 0 disables)",
    )

    audit_store = commands.add_parser(
        "audit-store",
        help="fail-closed audit of a compact Odin Store",
    )
    audit_store.add_argument("--store", required=True, type=Path)
    audit_store.add_argument(
        "--game-dir",
        type=Path,
        help="optional source installation used to reverify bundle and PathID evidence",
    )
    audit_store.add_argument(
        "--report",
        type=Path,
        default=Path("diagnostics/store_audit.json"),
    )
    return parser


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_json_object(path: Path, *, context: str) -> dict:
    resolved = path.expanduser().resolve()
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ScannerError(f"invalid {context} JSON at {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScannerError(f"{context} root must be an object: {resolved}")
    return value


def _require_supported_parser_command(arguments: argparse.Namespace) -> None:
    if arguments.command not in _PROFILE_GATED_COMMANDS:
        return
    if arguments.allow_unsupported_research:
        return

    from .installation import MuseDashInstallation

    MuseDashInstallation.open(arguments.game_dir).require_supported()


def _run_scan(arguments: argparse.Namespace, stdout: TextIO) -> int:
    game_dir = validate_game_directory(arguments.game_dir)
    if arguments.large_file_threshold_mib < 0:
        raise ScannerError("large file threshold cannot be negative")

    output_dir = arguments.output_dir.expanduser().resolve()
    if _path_is_within(output_dir, game_dir):
        raise ScannerError(
            f"output directory must not be inside the game directory: {output_dir}"
        )

    records = scan_game_directory(game_dir)
    threshold = arguments.large_file_threshold_mib * 1024 * 1024
    summary = build_resource_summary(
        game_dir,
        records,
        large_file_threshold=threshold,
    )
    inventory_path, summary_path = write_resource_inventory(
        output_dir,
        (record.to_dict() for record in records),
        summary,
    )

    result = {
        "status": "ok",
        "inventory": str(inventory_path.resolve()),
        "summary": str(summary_path.resolve()),
        "file_count": summary["file_count"],
        "total_size_bytes": summary["total_size_bytes"],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0


def _run_probe(
    arguments: argparse.Namespace,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    from .unity.bundles import (
        PROBE_CATEGORIES,
        build_object_type_summary,
        probe_unity_sources,
    )
    from .unity.addressables import parse_addressables_catalog

    game_dir = validate_game_directory(arguments.game_dir)
    output_dir = arguments.output_dir.expanduser().resolve()
    if _path_is_within(output_dir, game_dir):
        raise ScannerError(
            f"output directory must not be inside the game directory: {output_dir}"
        )
    if arguments.max_sources is not None and arguments.max_sources <= 0:
        raise ScannerError("max sources must be a positive integer")
    if arguments.progress_every < 0:
        raise ScannerError("progress interval cannot be negative")

    inventory = scan_game_directory(game_dir)
    candidates = [record for record in inventory if record.category in PROBE_CATEGORIES]
    selected = candidates
    if arguments.max_sources is not None:
        selected = candidates[: arguments.max_sources]

    def report_progress(index: int, total: int, source: str) -> None:
        interval = arguments.progress_every
        if interval and (index % interval == 0 or index == total):
            print(f"probe: {index}/{total} {source}", file=stderr)

    reports = probe_unity_sources(game_dir, selected, progress=report_progress)
    fingerprint = build_inventory_fingerprint(inventory)
    summary = build_object_type_summary(
        reports,
        inventory_fingerprint=fingerprint,
        candidate_source_count=len(candidates),
    )

    catalog_records = [
        record for record in inventory if record.category == "addressables_catalog"
    ]
    settings_records = [
        record for record in inventory if record.category == "addressables_settings"
    ]
    if len(catalog_records) != 1:
        raise ScannerError(
            f"expected exactly one Addressables catalog, found {len(catalog_records)}"
        )
    if len(settings_records) != 1:
        raise ScannerError(
            f"expected exactly one Addressables settings file, found {len(settings_records)}"
        )
    addressables = parse_addressables_catalog(
        game_dir,
        game_dir / catalog_records[0].relative_path,
        game_dir / settings_records[0].relative_path,
        inventory,
    )
    summary["addressables"] = {
        "version": addressables["addressables_version"],
        "entry_count": addressables["counts"]["entry_count"],
        "key_count": addressables["counts"]["key_count"],
        "catalog_bundle_path_count": addressables["bundle_path_crosscheck"][
            "catalog_bundle_path_count"
        ],
        "matched_inventory_bundle_path_count": addressables[
            "bundle_path_crosscheck"
        ]["matched_count"],
        "catalog_only_bundle_path_count": len(
            addressables["bundle_path_crosscheck"]["catalog_only"]
        ),
        "inventory_only_bundle_path_count": len(
            addressables["bundle_path_crosscheck"]["inventory_only"]
        ),
    }

    bundle_path = write_jsonl(
        output_dir / "bundle_inventory.jsonl",
        (
            report
            for report in reports
            if report["source_category"] == "unity_bundle_candidate"
        ),
    )
    serialized_path = write_jsonl(
        output_dir / "serialized_file_inventory.jsonl",
        (
            report
            for report in reports
            if report["source_category"] == "unity_assets_candidate"
        ),
    )
    summary_path = write_json(output_dir / "object_type_summary.json", summary)
    addressables_path = write_compact_json(
        output_dir / "addressables_index.json",
        addressables,
    )

    status = "ok"
    if not summary["probe_complete"] or summary["failed_source_count"]:
        status = "partial"
    result = {
        "status": status,
        "phase": 2,
        "bundle_inventory": str(bundle_path.resolve()),
        "serialized_file_inventory": str(serialized_path.resolve()),
        "object_type_summary": str(summary_path.resolve()),
        "addressables_index": str(addressables_path.resolve()),
        "probed_source_count": summary["probed_source_count"],
        "parseable_source_count": summary["parseable_source_count"],
        "failed_source_count": summary["failed_source_count"],
        "probe_complete": summary["probe_complete"],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 1 if summary["probe_complete"] and summary["failed_source_count"] else 0


def _run_candidates(
    arguments: argparse.Namespace,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    from .discovery.candidates import (
        discover_stage_info_candidates,
        load_bundle_inventory,
    )

    game_dir = validate_game_directory(arguments.game_dir)
    output_dir = arguments.output_dir.expanduser().resolve()
    if _path_is_within(output_dir, game_dir):
        raise ScannerError(
            f"output directory must not be inside the game directory: {output_dir}"
        )
    if arguments.max_sources is not None and arguments.max_sources <= 0:
        raise ScannerError("max sources must be a positive integer")
    if arguments.progress_every < 0:
        raise ScannerError("progress interval cannot be negative")

    inventory_path = (
        arguments.bundle_inventory.expanduser().resolve()
        if arguments.bundle_inventory is not None
        else output_dir / "bundle_inventory.jsonl"
    )
    bundle_reports = load_bundle_inventory(inventory_path)
    current_inventory = scan_game_directory(game_dir)
    fingerprint = build_inventory_fingerprint(current_inventory)

    def report_progress(index: int, total: int, source: str) -> None:
        interval = arguments.progress_every
        if interval and (index % interval == 0 or index == total):
            print(f"candidates: {index}/{total} {source}", file=stderr)

    rows, summary = discover_stage_info_candidates(
        game_dir,
        bundle_reports,
        current_inventory,
        inventory_fingerprint=fingerprint,
        max_sources=arguments.max_sources,
        progress=report_progress,
    )
    destination = write_jsonl(output_dir / "chart_candidates.jsonl", rows)
    status = "ok"
    if not summary["discovery_complete"] or summary["failed_candidate_count"]:
        status = "partial"
    result = {
        "status": status,
        "phase": 3,
        "chart_candidates": str(destination.resolve()),
        **summary,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 1 if summary["discovery_complete"] and summary["failed_candidate_count"] else 0


def _run_inspect_stageinfo(
    arguments: argparse.Namespace,
    stdout: TextIO,
) -> int:
    from .discovery.structure import (
        inspect_stage_info_candidate,
        load_ranked_candidate,
    )

    game_dir = validate_game_directory(arguments.game_dir)
    output_dir = arguments.output_dir.expanduser().resolve()
    if _path_is_within(output_dir, game_dir):
        raise ScannerError(
            f"output directory must not be inside the game directory: {output_dir}"
        )
    candidate_path = (
        arguments.candidate_file.expanduser().resolve()
        if arguments.candidate_file is not None
        else output_dir / "chart_candidates.jsonl"
    )
    candidate = load_ranked_candidate(
        candidate_path,
        source=arguments.source,
        path_id=arguments.path_id,
    )
    hypotheses, summary = inspect_stage_info_candidate(
        game_dir,
        candidate,
        sample_records=arguments.sample_records,
    )
    destination = write_jsonl(output_dir / "field_hypotheses.jsonl", hypotheses)
    result = {
        "status": "ok",
        "field_hypotheses": str(destination.resolve()),
        **summary,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0


def _run_extract(arguments: argparse.Namespace, stdout: TextIO) -> int:
    from .discovery.candidates import load_bundle_inventory
    from .discovery.first_chart import build_experimental_chart
    from .discovery.note_data import resolve_note_data
    from .discovery.structure import (
        load_ranked_candidate,
        recover_stage_info_candidate,
    )

    game_dir = validate_game_directory(arguments.game_dir)
    output_path = arguments.output.expanduser().resolve()
    if _path_is_within(output_path, game_dir):
        raise ScannerError(f"output file must not be inside the game directory: {output_path}")
    candidate = load_ranked_candidate(
        arguments.candidate_file.expanduser().resolve(),
        source=arguments.source,
        path_id=arguments.path_id,
    )
    parsed, payload_sha256, stage_info_raw = recover_stage_info_candidate(
        game_dir,
        candidate,
    )
    bundle_reports = load_bundle_inventory(
        arguments.bundle_inventory.expanduser().resolve()
    )
    note_configs_by_uid, note_data_provenance = resolve_note_data(
        game_dir,
        bundle_reports,
    )
    chart = build_experimental_chart(
        candidate,
        parsed,
        payload_sha256=payload_sha256,
        stage_info_raw=stage_info_raw,
        note_configs_by_uid=note_configs_by_uid,
        note_data_provenance=note_data_provenance,
    )
    destination = write_json(output_path, chart)
    result = {
        "status": chart["status"],
        "validation_status": chart["validation_status"],
        "schema_version": chart["schema_version"],
        "chart": str(destination.resolve()),
        "raw_record_count": chart["raw_record_count"],
        "record_group_count": chart["record_group_count"],
        "logical_object_count": chart["logical_object_count"],
        "used_note_uid_count": chart["note_data"]["used_note_uid_count"],
        "source": chart["source"]["bundle"],
        "path_id": chart["source"]["path_id"],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0


def _run_index(arguments: argparse.Namespace, stdout: TextIO) -> int:
    from .discovery.candidates import load_bundle_inventory
    from .discovery.indexing import (
        build_song_chart_index,
        read_album_rows,
        select_album_sources,
    )

    game_dir = validate_game_directory(arguments.game_dir)
    output_path = arguments.output.expanduser().resolve()
    if _path_is_within(output_path, game_dir):
        raise ScannerError(f"output file must not be inside the game directory: {output_path}")

    candidates = load_bundle_inventory(arguments.candidate_file.expanduser().resolve())
    bundle_reports = load_bundle_inventory(
        arguments.bundle_inventory.expanduser().resolve()
    )
    addressables_index = _read_json_object(
        arguments.addressables_index,
        context="Addressables index",
    )

    album_sources = select_album_sources(bundle_reports)
    album_rows, album_provenance = read_album_rows(game_dir, album_sources)
    index = build_song_chart_index(
        candidates,
        album_rows,
        album_provenance,
        addressables_index,
    )
    destination = write_json(output_path, index)
    print(
        json.dumps(
            {
                "status": index["status"],
                "milestone_status": index["milestone_status"],
                "schema_version": index["schema_version"],
                "song_chart_index": str(destination.resolve()),
                **index["counts"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


def _run_canonicalize(arguments: argparse.Namespace, stdout: TextIO) -> int:
    from .charts.canonicalize import (
        canonicalize_chart,
        reconstruct_experimental_chart,
    )

    experimental = _read_json_object(arguments.raw_chart, context="experimental chart")
    song_index = _read_json_object(arguments.song_index, context="song/chart index")
    validation_report = (
        _read_json_object(arguments.validation_report, context="validation report")
        if arguments.validation_report is not None
        else None
    )
    model = canonicalize_chart(experimental, song_index, validation_report)
    rendered = model.to_dict()
    reconstructed_experimental = reconstruct_experimental_chart(rendered["raw"])
    destination = write_json(arguments.output.expanduser().resolve(), rendered)
    report_destination = None
    if arguments.report is not None:
        raw_size, raw_sha256, _raw_prefix = fingerprint_file(arguments.raw_chart)
        index_size, index_sha256, _index_prefix = fingerprint_file(arguments.song_index)
        output_size, output_sha256, _output_prefix = fingerprint_file(destination)
        type_counts: dict[str, int] = {}
        for event in model.events:
            key = "unknown" if event.type_id is None else str(event.type_id)
            type_counts[key] = type_counts.get(key, 0) + 1
        validation_source = None
        if arguments.validation_report is not None:
            validation_size, validation_sha256, _validation_prefix = fingerprint_file(
                arguments.validation_report
            )
            validation_source = {
                "path": str(arguments.validation_report.expanduser().resolve()),
                "byte_count": validation_size,
                "sha256": validation_sha256,
            }
        report_destination = write_json(
            arguments.report.expanduser().resolve(),
            {
                "schema_version": 1,
                "phase": 7,
                "status": "canonicalized-losslessly",
                "milestone_status": "M6-achieved",
                "chart_id": model.chart_id,
                "song_id": model.song.song_id,
                "difficulty_id": model.difficulty.difficulty_id,
                "event_count": len(model.events),
                "type_id_distribution": dict(sorted(type_counts.items())),
                "unknown_type_count": sum(
                    event.type_status == "unknown" for event in model.events
                ),
                "null_is_air_count": sum(event.is_air is None for event in model.events),
                "duration_interpreted_count": sum(
                    event.duration_sec is not None for event in model.events
                ),
                "inputs": {
                    "experimental_chart": {
                        "path": str(arguments.raw_chart.expanduser().resolve()),
                        "byte_count": raw_size,
                        "sha256": raw_sha256,
                    },
                    "song_chart_index": {
                        "path": str(arguments.song_index.expanduser().resolve()),
                        "byte_count": index_size,
                        "sha256": index_sha256,
                    },
                    "validation_report": validation_source,
                },
                "output": {
                    "path": str(destination.resolve()),
                    "byte_count": output_size,
                    "sha256": output_sha256,
                    "git_ignored_required": True,
                },
                "lossless_checks": {
                    "experimental_chart_reconstructed_equal": (
                        reconstructed_experimental == experimental
                    ),
                    "validation_report_equal": (
                        validation_report is None
                        or rendered["raw"].get("validation_report") == validation_report
                    ),
                    "raw_record_references_accounted_with_sentinel": (
                        sum(
                            len(event.raw["raw_record_indices"])
                            for event in model.events
                        )
                        + sum(
                            len(group["raw_record_indices"])
                            for group in experimental["record_groups"]
                            if group["role_status"] == "observed-sentinel"
                        )
                        == experimental["raw_record_count"]
                    ),
                    "event_payloads_not_duplicated": all(
                        "music_data_records" not in event.raw
                        and "group" not in event.raw
                        for event in model.events
                    ),
                },
                "warnings": list(model.warnings),
            },
        )
    print(
        json.dumps(
            {
                "status": model.canonicalization_status,
                "validation_status": model.validation_status,
                "schema_version": model.schema_version,
                "chart_id": model.chart_id,
                "song_id": model.song.song_id,
                "difficulty_id": model.difficulty.difficulty_id,
                "event_count": len(model.events),
                "chart": str(destination.resolve()),
                "report": (
                    str(report_destination.resolve())
                    if report_destination is not None
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


def _run_validate(arguments: argparse.Namespace, stdout: TextIO) -> int:
    from .charts.validator import (
        render_validation_markdown,
        validate_canonical_charts,
    )

    game_dir = (
        validate_game_directory(arguments.game_dir)
        if arguments.game_dir is not None
        else None
    )
    output_path = arguments.output.expanduser().resolve()
    markdown_path = arguments.markdown_output.expanduser().resolve()
    if game_dir is not None:
        for destination in (output_path, markdown_path):
            if _path_is_within(destination, game_dir):
                raise ScannerError(
                    f"output file must not be inside the game directory: {destination}"
                )

    charts = [
        _read_json_object(path, context=f"canonical chart {position}")
        for position, path in enumerate(arguments.chart, start=1)
    ]
    references = (
        _read_json_object(arguments.reference_file, context="validation references")
        if arguments.reference_file is not None
        else None
    )
    report = validate_canonical_charts(
        charts,
        game_dir=game_dir,
        references=references,
    )
    destination = write_json(output_path, report)
    markdown_destination = write_text(
        markdown_path,
        render_validation_markdown(report),
    )
    summary = report["summary"]
    print(
        json.dumps(
            {
                "status": report["status"],
                "milestone_status": report["milestone_status"],
                "chart_count": summary["chart_count"],
                "structural_valid_count": summary["structural_valid_count"],
                "source_verified_count": summary["source_verified_count"],
                "reference_matched_count": summary["reference_matched_count"],
                "event_reference_compared_count": summary[
                    "event_reference_compared_count"
                ],
                "event_reference_matched_count": summary[
                    "event_reference_matched_count"
                ],
                "event_reference_mismatch_count": summary[
                    "event_reference_mismatch_count"
                ],
                "validation_report": str(destination.resolve()),
                "validation_markdown": str(markdown_destination.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0 if report["status"] != "validation-failed" else 1


def _run_grouping_census(
    arguments: argparse.Namespace,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    from .discovery.candidates import load_bundle_inventory
    from .discovery.grouping_census import census_stage_info_grouping

    game_dir = validate_game_directory(arguments.game_dir)
    output_dir = arguments.output_dir.expanduser().resolve()
    if _path_is_within(output_dir, game_dir):
        raise ScannerError(
            f"output directory must not be inside the game directory: {output_dir}"
        )
    candidates = load_bundle_inventory(arguments.candidate_file.expanduser().resolve())

    def progress(current: int, total: int, source: str) -> None:
        every = arguments.progress_every
        if every > 0 and (current % every == 0 or current == total):
            print(
                f"grouping census: {current}/{total} bundles ({source})",
                file=stderr,
            )

    rows, summary = census_stage_info_grouping(
        game_dir,
        candidates,
        progress=progress,
    )
    rows_path = write_jsonl(output_dir / "grouping_census.jsonl", rows)
    summary_path = write_json(output_dir / "grouping_census_summary.json", summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "candidate_count": summary["candidate_count"],
                "raw_parse_status_counts": summary["raw_parse_status_counts"],
                "grouping_status_counts": summary["grouping_status_counts"],
                "grouping_census": str(rows_path.resolve()),
                "grouping_census_summary": str(summary_path.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0


def _run_extract_all(
    arguments: argparse.Namespace,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    from .installation import MuseDashInstallation

    game_dir = validate_game_directory(arguments.game_dir)
    if path_is_link(arguments.output.expanduser()):
        raise ScannerError(
            f"batch output must not be a symbolic link or junction: {arguments.output}"
        )
    output_dir = arguments.output.expanduser().resolve()
    if _path_is_within(output_dir, game_dir):
        raise ScannerError(
            f"output directory must not be inside the game directory: {output_dir}"
        )
    if arguments.progress_every < 0:
        raise ScannerError("progress interval cannot be negative")

    def progress(current: int, total: int, source: str) -> None:
        every = arguments.progress_every
        if every > 0 and (current % every == 0 or current == total):
            print(
                f"extract all: {current}/{total} bundles ({source})",
                file=stderr,
            )

    installation = MuseDashInstallation.open(game_dir)
    collection = installation.extract_charts(
        output_dir=output_dir,
        candidate_file=arguments.candidate_file,
        song_index_file=arguments.song_index,
        bundle_inventory_file=arguments.bundle_inventory,
        grouping_census_summary_file=arguments.grouping_census_summary,
        progress=progress,
        allow_unsupported_research=arguments.allow_unsupported_research,
    )
    manifest = collection.manifest
    support = manifest.get("profile_support")
    formal_support = not (
        isinstance(support, dict) and support.get("formal_support") is False
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "milestone_status": manifest["milestone_status"],
                "candidate_count": manifest["candidate_count"],
                "source_count": manifest["source_count"],
                "chart_file_count": manifest["chart_file_count"],
                "event_count": manifest["event_count"],
                "status_counts": manifest["status_counts"],
                "raw_parse_status_counts": manifest["raw_parse_status_counts"],
                "canonical_status_counts": manifest["canonical_status_counts"],
                "formal_support": formal_support,
                "batch_manifest": str((output_dir / "manifest.json").resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0 if manifest["milestone_status"] == "M8-achieved" else 1


def _run_extract_store(
    arguments: argparse.Namespace,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    from .installation import MuseDashInstallation

    game_dir = validate_game_directory(arguments.game_dir)
    requested_output = arguments.output.expanduser()
    if path_is_link(requested_output):
        raise ScannerError(
            f"store output must not be a symbolic link or junction: {requested_output}"
        )
    output_dir = requested_output.resolve()
    if _path_is_within(output_dir, game_dir):
        raise ScannerError(
            f"output directory must not be inside the game directory: {output_dir}"
        )
    if arguments.progress_every < 0:
        raise ScannerError("progress interval cannot be negative")

    def progress(current: int, total: int, source: str) -> None:
        every = arguments.progress_every
        if every > 0 and (current % every == 0 or current == total):
            print(
                f"extract store: {current}/{total} bundles ({source})",
                file=stderr,
            )

    installation = MuseDashInstallation.open(game_dir)
    manifest = installation.extract_store(
        output_dir=output_dir,
        candidate_file=arguments.candidate_file,
        song_index_file=arguments.song_index,
        bundle_inventory_file=arguments.bundle_inventory,
        grouping_census_summary_file=arguments.grouping_census_summary,
        progress=progress,
    )
    summary = {
        "status": manifest["status"],
        "candidate_count": manifest["candidate_count"],
        "source_count": manifest["source_count"],
        "payload_count": manifest["payload_count"],
        "payload_byte_count": manifest["payload_byte_count"],
        "raw_record_count": manifest["raw_record_count"],
        "logical_event_count": manifest["logical_event_count"],
        "status_counts": manifest["status_counts"],
        "logical_store_digest": manifest["logical_store_digest"],
        "store_manifest": str((output_dir / "store.json").resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=stdout)
    phase_gate = manifest.get("phase_gate")
    passed = isinstance(phase_gate, dict) and all(value is True for value in phase_gate.values())
    return 0 if passed else 1


def _run_audit_store(arguments: argparse.Namespace, stdout: TextIO) -> int:
    from .store.audit import audit_chart_store

    requested_store = arguments.store.expanduser()
    if path_is_link(requested_store):
        raise ScannerError(
            f"store root must not be a symbolic link or junction: {requested_store}"
        )
    store_dir = requested_store.resolve(strict=False)
    game_dir = (
        validate_game_directory(arguments.game_dir)
        if arguments.game_dir is not None
        else None
    )
    requested_report = arguments.report.expanduser()
    if path_is_link(requested_report):
        raise ScannerError(
            f"audit report must not be a symbolic link or junction: {requested_report}"
        )
    lexical_report_path = requested_report.absolute()
    try:
        lexical_report_path.relative_to(store_dir)
    except ValueError:
        pass
    else:
        reject_symlink_path(
            store_dir, lexical_report_path, context="store audit report path"
        )
    report_path = requested_report.resolve()
    if _path_is_within(report_path, store_dir):
        allowed_store_audit_root = (store_dir / "audit").resolve()
        if not _path_is_within(report_path, allowed_store_audit_root):
            raise ScannerError(
                "audit report inside a Store must be written below its audit directory"
            )
    if game_dir is not None and _path_is_within(report_path, game_dir):
        raise ScannerError(
            f"audit report must not be inside the game directory: {report_path}"
        )
    report = audit_chart_store(store_dir, game_dir=game_dir)
    destination = write_json(report_path, report)
    counts = report.get("counts", {})
    print(
        json.dumps(
            {
                "status": report["status"],
                "chart_count": counts.get("chart_count"),
                "payload_count": counts.get("payload_count"),
                "mismatch_counts": report["mismatch_counts"],
                "store_audit": str(destination.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=stdout,
    )
    return 0 if report["status"] == "passed" else 1


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return an exit status without hiding domain failures."""

    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    try:
        _require_supported_parser_command(arguments)
        if arguments.command == "scan":
            return _run_scan(arguments, output)
        if arguments.command == "probe":
            return _run_probe(arguments, output, errors)
        if arguments.command == "candidates":
            return _run_candidates(arguments, output, errors)
        if arguments.command == "inspect-stageinfo":
            return _run_inspect_stageinfo(arguments, output)
        if arguments.command == "extract":
            return _run_extract(arguments, output)
        if arguments.command == "index":
            return _run_index(arguments, output)
        if arguments.command == "canonicalize":
            return _run_canonicalize(arguments, output)
        if arguments.command == "validate":
            return _run_validate(arguments, output)
        if arguments.command == "grouping-census":
            return _run_grouping_census(arguments, output, errors)
        if arguments.command == "extract-all":
            return _run_extract_all(arguments, output, errors)
        if arguments.command == "extract-store":
            return _run_extract_store(arguments, output, errors)
        if arguments.command == "audit-store":
            return _run_audit_store(arguments, output)
    except (ScannerError, OSError) as exc:
        print(f"error: {exc}", file=errors)
        return 2

    parser.error(f"unknown command: {arguments.command}")
    return 2


def main() -> None:
    raise SystemExit(run())
