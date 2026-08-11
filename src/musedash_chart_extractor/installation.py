"""Public installation API with an explicit supported-fingerprint gate."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .batch import extract_all_charts
from .discovery.candidates import load_bundle_inventory
from .discovery.note_data import resolve_note_data
from .scanner import (
    ResourceRecord,
    ScannerError,
    build_inventory_fingerprint,
    scan_game_directory,
    validate_game_directory,
)

CURRENT_GAME_FINGERPRINT = (
    "sha256:1821d79ef6d53bca76c60491a2395496054fa473c31482ecc73b8d866c5f0ab5"
)


@dataclass(frozen=True, slots=True)
class SupportedResourceProfile:
    """One resource layout proven by reproducible local evidence."""

    fingerprint: str
    addressables_version: str
    build_result_hash: str
    parser_family: str
    grouping_rule_version: str
    evidence_status: str


SUPPORTED_RESOURCE_PROFILES: Mapping[str, SupportedResourceProfile] = {
    CURRENT_GAME_FINGERPRINT: SupportedResourceProfile(
        fingerprint=CURRENT_GAME_FINGERPRINT,
        addressables_version="1.21.20",
        build_result_hash="9ecc2d74a4045582f2aabf0f64c83581",
        parser_family="sirenix-odin-binary-observed-stageinfo-subset",
        grouping_rule_version="composite-neutral-base-negative-id-singleton-v2",
        evidence_status="M8-achieved-on-one-local-installation",
    )
}


class UnknownGameVersionError(ScannerError):
    """Raised when formal extraction is requested for an unproven fingerprint."""


def _read_json_object(path: Path, *, context: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ScannerError(f"invalid {context} JSON at {path}: {exc}") from exc
    except OSError as exc:
        raise ScannerError(f"cannot read {context} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScannerError(f"{context} root must be an object: {path}")
    return value


def _verify_artifact_fingerprint(
    value: Any,
    *,
    expected: str,
    context: str,
) -> None:
    if not isinstance(value, str) or not value:
        raise ScannerError(
            f"{context} inventory_fingerprint must be a non-empty string"
        )
    if value != expected:
        raise ScannerError(
            f"{context} fingerprint does not match the opened installation: "
            f"expected {expected}, found {value}"
        )


@dataclass(frozen=True, slots=True)
class ExtractedChartCollection:
    """Lazy iterable over the successful chart files named by one manifest."""

    output_dir: Path
    manifest: Mapping[str, Any]

    def __len__(self) -> int:
        value = self.manifest.get("chart_file_count")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ScannerError("batch manifest chart_file_count is not an integer")
        return value

    @property
    def uncertain(self) -> tuple[Mapping[str, Any], ...]:
        charts = self.manifest.get("charts")
        if not isinstance(charts, list):
            raise ScannerError("batch manifest charts is not an array")
        return tuple(
            row
            for row in charts
            if isinstance(row, Mapping) and row.get("status") == "uncertain"
        )

    def __iter__(self) -> Iterator[dict[str, Any]]:
        charts = self.manifest.get("charts")
        if not isinstance(charts, list):
            raise ScannerError("batch manifest charts is not an array")
        for position, row in enumerate(charts):
            if not isinstance(row, Mapping) or row.get("status") != "success":
                continue
            relative = row.get("output_path")
            if not isinstance(relative, str) or not relative:
                raise ScannerError(f"successful manifest row {position} has no output path")
            portable = Path(*relative.split("/"))
            destination = (self.output_dir / portable).resolve(strict=True)
            try:
                destination.relative_to(self.output_dir)
            except ValueError as exc:
                raise ScannerError(
                    f"manifest output path escapes extraction root: {relative}"
                ) from exc
            yield _read_json_object(destination, context=f"canonical chart {relative}")


@dataclass(frozen=True, slots=True)
class MuseDashInstallation:
    """Verified read-only view of one local Muse Dash installation."""

    root: Path
    inventory_fingerprint: str
    profile: SupportedResourceProfile | None

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        scanner: Callable[[str | Path], list[ResourceRecord]] = scan_game_directory,
    ) -> MuseDashInstallation:
        root = validate_game_directory(path)
        records = scanner(root)
        fingerprint = build_inventory_fingerprint(records)
        return cls(
            root=root,
            inventory_fingerprint=fingerprint,
            profile=SUPPORTED_RESOURCE_PROFILES.get(fingerprint),
        )

    @property
    def supported(self) -> bool:
        return self.profile is not None

    def require_supported(self) -> SupportedResourceProfile:
        if self.profile is None:
            raise UnknownGameVersionError(
                "game resource fingerprint is not formally supported; "
                f"found {self.inventory_fingerprint}. Run scan/probe and do not apply "
                "the known parser until the new evidence is reviewed."
            )
        return self.profile

    def extract_charts(
        self,
        *,
        output_dir: str | Path = "extracted",
        diagnostics_dir: str | Path = "diagnostics",
        candidate_file: str | Path | None = None,
        song_index_file: str | Path | None = None,
        bundle_inventory_file: str | Path | None = None,
        grouping_census_summary_file: str | Path | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> ExtractedChartCollection:
        """Extract all supported charts and return a lazy iterable over results."""

        self.require_supported()
        diagnostics = Path(diagnostics_dir).expanduser().resolve()
        output = Path(output_dir).expanduser().resolve()
        candidate_path = Path(
            candidate_file or diagnostics / "chart_candidates.jsonl"
        ).expanduser().resolve()
        song_index_path = Path(
            song_index_file or diagnostics / "song_chart_index.json"
        ).expanduser().resolve()
        bundle_inventory_path = Path(
            bundle_inventory_file or diagnostics / "bundle_inventory.jsonl"
        ).expanduser().resolve()
        census_path = Path(
            grouping_census_summary_file
            or diagnostics / "grouping_census_summary.json"
        ).expanduser().resolve()
        candidates = load_bundle_inventory(candidate_path)
        bundle_reports = load_bundle_inventory(bundle_inventory_path)
        song_index = _read_json_object(
            song_index_path, context="song/chart index"
        )
        census = _read_json_object(
            census_path,
            context="grouping census summary",
        )
        artifact_fingerprints = [
            ("song/chart index", song_index.get("inventory_fingerprint")),
            ("grouping census", census.get("inventory_fingerprint")),
            *(
                (
                    f"chart candidate {position}",
                    candidate.get("inventory_fingerprint"),
                )
                for position, candidate in enumerate(candidates)
            ),
        ]
        for context, fingerprint in artifact_fingerprints:
            _verify_artifact_fingerprint(
                fingerprint,
                expected=self.inventory_fingerprint,
                context=context,
            )
        note_configs, note_provenance = resolve_note_data(self.root, bundle_reports)
        manifest = extract_all_charts(
            self.root,
            output,
            candidates,
            song_index,
            grouping_census_summary=census,
            note_configs_by_uid=note_configs,
            note_data_provenance=note_provenance,
            expected_candidate_count=census.get("candidate_count"),
            progress=progress,
        )
        return ExtractedChartCollection(output_dir=output, manifest=manifest)


__all__ = [
    "CURRENT_GAME_FINGERPRINT",
    "ExtractedChartCollection",
    "MuseDashInstallation",
    "SUPPORTED_RESOURCE_PROFILES",
    "SupportedResourceProfile",
    "UnknownGameVersionError",
]
