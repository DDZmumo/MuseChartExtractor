from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from musedash_chart_extractor.installation import (
    CURRENT_GAME_FINGERPRINT,
    MuseDashInstallation,
    STEAM_DEPOT_241392741196033182_FINGERPRINT,
)

CURRENT_BATCH_MANIFEST_SIZE = 2_606_521
CURRENT_BATCH_MANIFEST_SHA256 = (
    "20d1bcd8f9a733614d9e0ab968abe855220c34e7e4bb2d2f390916d3426db4ea"
)
CURRENT_BATCH_CHART_BYTES = 14_086_037_521
SECOND_BATCH_MANIFEST_SIZE = 2_577_100
SECOND_BATCH_MANIFEST_SHA256 = (
    "d893ca25bbb86683d3b27cdf016c594afc3406be9fd1432e5b2398298a0d94d2"
)
SECOND_BATCH_CHART_BYTES = 13_884_429_834


def _game_dir() -> Path:
    value = os.environ.get("MUSEDASH_GAME_DIR")
    if not value:
        pytest.skip("MUSEDASH_GAME_DIR is not set")
    return Path(value)


def _second_game_dir() -> Path:
    value = os.environ.get("MUSEDASH_SECOND_GAME_DIR")
    if not value:
        pytest.skip("MUSEDASH_SECOND_GAME_DIR is not set")
    return Path(value)


def _assert_batch_manifest(
    output: Path,
    *,
    fingerprint: str,
    manifest_size: int,
    manifest_sha256: str,
    chart_bytes: int,
    success_count: int,
) -> dict[str, Any]:
    manifest_bytes = (output / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    files = {path.resolve() for path in (output / "charts").rglob("*.json")}
    success_rows = [row for row in manifest["charts"] if row["status"] == "success"]
    expected_files = {
        (output / Path(*row["output_path"].split("/"))).resolve()
        for row in success_rows
    }

    assert len(manifest_bytes) == manifest_size
    assert hashlib.sha256(manifest_bytes).hexdigest() == manifest_sha256
    assert manifest["game_fingerprint"] == fingerprint
    assert manifest["canonical_schema_version"] == "1.1.0"
    assert manifest["extractor_version"] == "0.1.0"
    assert manifest["milestone_status"] == "M8-achieved"
    assert manifest["status_counts"] == {
        "success": success_count,
        "uncertain": 1,
    }
    assert (
        manifest["chart_file_count"]
        == len(success_rows)
        == len(files)
        == success_count
    )
    assert sum(row["output_byte_count"] for row in success_rows) == chart_bytes
    assert files == expected_files
    for row in success_rows:
        destination = output / Path(*row["output_path"].split("/"))
        assert destination.stat().st_size == row["output_byte_count"]

    sample_row = success_rows[0]
    sample = json.loads(
        (output / Path(*sample_row["output_path"].split("/"))).read_bytes()
    )
    assert sample["schema_version"] == "1.1.0"
    assert sample["raw"]["layout"]["strategy"] == "single-raw-record-table-v1"
    assert "logical_objects" not in sample["raw"]["experimental_chart"]
    assert all(
        set(event["raw"]) == {"base_raw_record_index", "raw_record_indices"}
        for event in sample["events"]
    )
    return manifest


@pytest.mark.local_game
def test_known_local_installation_fingerprint() -> None:
    installation = MuseDashInstallation.open(_game_dir())
    assert installation.inventory_fingerprint == CURRENT_GAME_FINGERPRINT
    assert installation.supported


@pytest.mark.local_game
def test_local_batch_manifest_is_complete_when_supplied() -> None:
    value = os.environ.get("MUSEDASH_EXTRACTED_DIR")
    if not value:
        pytest.skip("MUSEDASH_EXTRACTED_DIR is not set")
    output = Path(value).resolve()
    _assert_batch_manifest(
        output,
        fingerprint=CURRENT_GAME_FINGERPRINT,
        manifest_size=CURRENT_BATCH_MANIFEST_SIZE,
        manifest_sha256=CURRENT_BATCH_MANIFEST_SHA256,
        chart_bytes=CURRENT_BATCH_CHART_BYTES,
        success_count=2330,
    )


@pytest.mark.local_game
def test_second_known_local_installation_fingerprint() -> None:
    installation = MuseDashInstallation.open(_second_game_dir())
    assert (
        installation.inventory_fingerprint
        == STEAM_DEPOT_241392741196033182_FINGERPRINT
    )
    assert installation.supported
    assert installation.profile is not None
    assert (
        installation.profile.evidence_status
        == "M8-achieved-on-steam-depot-manifest-241392741196033182"
    )


@pytest.mark.local_game
def test_second_local_batch_manifest_is_complete_when_supplied() -> None:
    value = os.environ.get("MUSEDASH_SECOND_EXTRACTED_DIR")
    if not value:
        pytest.skip("MUSEDASH_SECOND_EXTRACTED_DIR is not set")
    output = Path(value).resolve()
    manifest = _assert_batch_manifest(
        output,
        fingerprint=STEAM_DEPOT_241392741196033182_FINGERPRINT,
        manifest_size=SECOND_BATCH_MANIFEST_SIZE,
        manifest_sha256=SECOND_BATCH_MANIFEST_SHA256,
        chart_bytes=SECOND_BATCH_CHART_BYTES,
        success_count=2304,
    )
    assert manifest["profile_support"]["formal_support"] is False
    assert manifest["profile_support"]["status"] == (
        "unsupported-fingerprint-research"
    )
