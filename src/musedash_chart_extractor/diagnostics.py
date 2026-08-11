"""Deterministic writers for reproducible local diagnostic artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def _json_line(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)

        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def write_json(path: str | Path, value: Mapping[str, Any]) -> Path:
    """Write one stable, human-readable JSON document atomically."""

    destination = Path(path)
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write_text(destination, content)
    return destination


def write_compact_json(path: str | Path, value: Mapping[str, Any]) -> Path:
    """Write a stable compact JSON document for large normalized indexes."""

    destination = Path(path)
    content = _json_line(value) + "\n"
    _atomic_write_text(destination, content)
    return destination


def write_jsonl(
    path: str | Path,
    rows: Iterable[Mapping[str, Any]],
) -> Path:
    """Write stable JSON Lines atomically, preserving the supplied row order."""

    destination = Path(path)
    content = "".join(f"{_json_line(row)}\n" for row in rows)
    _atomic_write_text(destination, content)
    return destination


def write_text(path: str | Path, content: str) -> Path:
    """Write UTF-8 text atomically with stable LF newlines."""

    destination = Path(path)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    _atomic_write_text(destination, normalized)
    return destination


def write_resource_inventory(
    output_dir: str | Path,
    records: Iterable[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> tuple[Path, Path]:
    """Write the two Phase 1 resource-inventory artifacts."""

    destination = Path(output_dir)
    inventory_path = write_jsonl(destination / "resource_inventory.jsonl", records)
    summary_path = write_json(destination / "resource_summary.json", summary)
    return inventory_path, summary_path
