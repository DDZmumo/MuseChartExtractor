"""Bounded, read-only UnityPy metadata probing.

This module deliberately avoids typetree reads and object-content exports.  It
records enough metadata to choose evidence-backed candidates in later phases.
"""

from __future__ import annotations

import warnings
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import UnityPy

from ..scanner import ResourceRecord, validate_game_directory

PROBE_CATEGORIES = {"unity_bundle_candidate", "unity_assets_candidate"}
NAME_SEARCH_TERMS = (
    "music",
    "song",
    "stage",
    "chart",
    "map",
    "level",
    "difficulty",
    "note",
    "battle",
)
NAME_PROBE_TYPES = {
    "AssetBundle",
    "MonoBehaviour",
    "MonoScript",
    "ScriptableObject",
    "TextAsset",
}
HIGH_VALUE_TYPES = {"MonoBehaviour", "ScriptableObject", "TextAsset"}


def _type_name(obj: Any) -> str:
    return str(obj.type.name)


def _asset_version(asset: Any) -> str | None:
    version = getattr(asset, "unity_version", None)
    if version is None:
        version = getattr(asset, "version", None)
    return None if version is None else str(version)


def _target_platform(asset: Any) -> str | None:
    platform = getattr(asset, "target_platform", None)
    if platform is None:
        return None
    name = getattr(platform, "name", None)
    return str(name if name is not None else platform)


def _warning_rows(caught: Iterable[warnings.WarningMessage]) -> list[dict[str, str]]:
    return [
        {
            "category": item.category.__name__,
            "message": str(item.message),
        }
        for item in caught
    ]


def _name_search_hits(report: dict[str, Any]) -> list[str]:
    searchable = [str(report["source"])]
    searchable.extend(str(item["path"]) for item in report["container_entries"])
    searchable.extend(str(item["name"]) for item in report["named_objects"])
    folded = [value.casefold() for value in searchable]
    return [
        term
        for term in NAME_SEARCH_TERMS
        if any(term.casefold() in value for value in folded)
    ]


def probe_unity_source(
    game_dir: str | Path,
    resource: ResourceRecord,
    *,
    loader: Callable[[str], Any] = UnityPy.load,
) -> dict[str, Any]:
    """Probe one inventoried source and return success or explicit failure data."""

    root = validate_game_directory(game_dir)
    source_path = (root / Path(resource.relative_path)).resolve(strict=True)
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"inventory source escapes game directory: {resource.relative_path}") from exc

    base: dict[str, Any] = {
        "schema_version": 1,
        "source": resource.relative_path,
        "source_category": resource.category,
        "size": resource.size,
        "sha256": resource.sha256,
    }

    caught: list[warnings.WarningMessage]
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            environment = loader(str(source_path))
            objects = list(environment.objects)
            object_types = Counter(_type_name(obj) for obj in objects)

            asset_files = []
            for asset in environment.assets:
                asset_files.append(
                    {
                        "name": str(getattr(asset, "name", "")),
                        "unity_version": _asset_version(asset),
                        "target_platform": _target_platform(asset),
                        "object_count": len(asset.objects),
                    }
                )

            container_entries = []
            for container_path, pointer_or_object in sorted(
                environment.container.items(),
                key=lambda item: (str(item[0]).casefold(), str(item[0])),
            ):
                path_id = int(pointer_or_object.path_id)
                try:
                    obj = (
                        pointer_or_object.deref()
                        if hasattr(pointer_or_object, "deref")
                        else pointer_or_object
                    )
                except Exception as exc:
                    container_entries.append(
                        {
                            "path": str(container_path),
                            "path_id": path_id,
                            "file_id": getattr(pointer_or_object, "file_id", None),
                            "resolved": False,
                            "resolution_error_type": type(exc).__name__,
                            "resolution_error": str(exc),
                        }
                    )
                    continue
                container_entries.append(
                    {
                        "path": str(container_path),
                        "path_id": path_id,
                        "type": _type_name(obj),
                        "byte_size": int(obj.byte_size),
                        "resolved": True,
                    }
                )

            named_objects = []
            name_read_errors = []
            for obj in objects:
                type_name = _type_name(obj)
                if type_name not in NAME_PROBE_TYPES:
                    continue
                try:
                    name = obj.peek_name()
                except Exception as exc:  # the error is retained with object identity
                    name_read_errors.append(
                        {
                            "path_id": int(obj.path_id),
                            "type": type_name,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                    continue
                if name:
                    named_objects.append(
                        {
                            "path_id": int(obj.path_id),
                            "type": type_name,
                            "byte_size": int(obj.byte_size),
                            "name": str(name),
                        }
                    )

        report = {
            **base,
            "parseable": True,
            "asset_file_count": len(asset_files),
            "asset_files": asset_files,
            "container_count": len(container_entries),
            "container_entries": container_entries,
            "object_count": len(objects),
            "object_types": dict(sorted(object_types.items())),
            "named_objects": named_objects,
            "name_read_errors": name_read_errors,
            "warnings": _warning_rows(caught),
        }
        report["name_search_hits"] = _name_search_hits(report)
        return report
    except Exception as exc:
        return {
            **base,
            "parseable": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "warnings": _warning_rows(caught) if "caught" in locals() else [],
        }


def probe_unity_sources(
    game_dir: str | Path,
    resources: Iterable[ResourceRecord],
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Probe selected Unity sources sequentially so diagnostics stay ordered."""

    selected = [record for record in resources if record.category in PROBE_CATEGORIES]
    selected.sort(key=lambda record: (record.relative_path.casefold(), record.relative_path))
    reports = []
    total = len(selected)
    for index, resource in enumerate(selected, start=1):
        reports.append(probe_unity_source(game_dir, resource))
        if progress is not None:
            progress(index, total, resource.relative_path)
    return reports


def build_object_type_summary(
    reports: list[dict[str, Any]],
    *,
    inventory_fingerprint: str,
    candidate_source_count: int | None = None,
) -> dict[str, Any]:
    """Aggregate parseability, versions and object types for Phase 2 acceptance."""

    object_types: Counter[str] = Counter()
    unity_versions: Counter[str] = Counter()
    failure_types: Counter[str] = Counter()
    name_search_sources: Counter[str] = Counter()
    high_value_source_count = 0
    total_objects = 0
    total_containers = 0
    unresolved_containers = 0
    name_read_errors = 0
    warning_count = 0

    for report in reports:
        if not report["parseable"]:
            failure_types[str(report["error_type"])] += 1
            continue
        object_types.update(report["object_types"])
        total_objects += int(report["object_count"])
        total_containers += int(report["container_count"])
        unresolved_containers += sum(
            entry.get("resolved") is False for entry in report["container_entries"]
        )
        name_read_errors += len(report["name_read_errors"])
        warning_count += len(report["warnings"])
        for asset_file in report["asset_files"]:
            version = asset_file["unity_version"]
            if version is not None:
                unity_versions[str(version)] += 1
        for term in report["name_search_hits"]:
            name_search_sources[str(term)] += 1
        if any(report["object_types"].get(name, 0) for name in HIGH_VALUE_TYPES):
            high_value_source_count += 1

    total_candidates = len(reports) if candidate_source_count is None else candidate_source_count
    parseable_count = sum(bool(report["parseable"]) for report in reports)
    return {
        "schema_version": 1,
        "unitypy_version": UnityPy.__version__,
        "inventory_fingerprint": inventory_fingerprint,
        "candidate_source_count": total_candidates,
        "probed_source_count": len(reports),
        "probe_complete": len(reports) == total_candidates,
        "bundle_source_count": sum(
            report["source_category"] == "unity_bundle_candidate" for report in reports
        ),
        "serialized_asset_source_count": sum(
            report["source_category"] == "unity_assets_candidate" for report in reports
        ),
        "parseable_source_count": parseable_count,
        "failed_source_count": len(reports) - parseable_count,
        "total_object_count": total_objects,
        "total_container_count": total_containers,
        "unresolved_container_count": unresolved_containers,
        "name_read_error_count": name_read_errors,
        "warning_count": warning_count,
        "high_value_source_count": high_value_source_count,
        "object_types": dict(sorted(object_types.items())),
        "unity_versions": dict(sorted(unity_versions.items())),
        "failure_types": dict(sorted(failure_types.items())),
        "name_search_source_counts": dict(sorted(name_search_sources.items())),
    }
