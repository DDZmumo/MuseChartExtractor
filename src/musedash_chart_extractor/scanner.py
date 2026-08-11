"""Read-only, deterministic inventory of a Muse Dash installation tree."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

READ_CHUNK_SIZE = 1024 * 1024
MAGIC_PREFIX_SIZE = 4096
DEFAULT_LARGE_FILE_THRESHOLD = 16 * 1024 * 1024


class ScannerError(RuntimeError):
    """Base error for inventory failures that need user-visible context."""


class GameDirectoryError(ScannerError):
    """Raised when the requested game directory cannot be scanned."""


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    relative_path: str
    size: int
    suffix: str
    magic: str
    sha256: str
    category: str

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def validate_game_directory(game_dir: str | Path) -> Path:
    """Resolve a user-supplied directory or raise a concise domain error."""

    candidate = Path(game_dir).expanduser()
    if not candidate.exists():
        raise GameDirectoryError(f"game directory does not exist: {candidate}")
    if not candidate.is_dir():
        raise GameDirectoryError(f"game directory is not a directory: {candidate}")
    return candidate.resolve(strict=True)


def detect_magic(prefix: bytes) -> str:
    """Classify only signatures supported by the Phase 1 ROADMAP."""

    if prefix.startswith(b"UnityFS"):
        return "UnityFS"
    if prefix.startswith(b"UnityWeb"):
        return "UnityWeb"
    if prefix.startswith(b"UnityRaw"):
        return "UnityRaw"
    if prefix.startswith(b"MZ"):
        return "PE"

    json_prefix = prefix
    if json_prefix.startswith(b"\xef\xbb\xbf"):
        json_prefix = json_prefix[3:]
    json_prefix = json_prefix.lstrip(b" \t\r\n")
    if json_prefix.startswith((b"{", b"[")):
        return "JSON"

    return "unknown"


def classify_resource(relative_path: str, magic: str) -> str:
    """Assign an evidence-based inventory category without format guesses."""

    path = Path(relative_path)
    name = path.name.casefold()
    suffix = path.suffix.casefold()

    if magic in {"UnityFS", "UnityWeb", "UnityRaw"} or suffix == ".bundle":
        return "unity_bundle_candidate"
    if suffix == ".assets":
        return "unity_assets_candidate"
    if suffix in {".resource", ".ress"}:
        return "unity_resource_companion"
    if name == "catalog.json":
        return "addressables_catalog"
    if name == "settings.json":
        return "addressables_settings"
    if name == "global-metadata.dat":
        return "il2cpp_metadata"
    if name == "gameassembly.dll":
        return "il2cpp_native_binary"
    if magic == "PE":
        return "native_binary"
    if magic == "JSON":
        return "json"
    return "unknown"


def fingerprint_file(path: str | Path) -> tuple[int, str, bytes]:
    """Read one file once and return stable size, SHA-256, and bounded prefix."""

    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    prefix = bytearray()

    try:
        with path.open("rb") as stream:
            while chunk := stream.read(READ_CHUNK_SIZE):
                if len(prefix) < MAGIC_PREFIX_SIZE:
                    remaining = MAGIC_PREFIX_SIZE - len(prefix)
                    prefix.extend(chunk[:remaining])
                digest.update(chunk)
    except OSError as exc:
        raise ScannerError(f"cannot read resource file {path}: {exc}") from exc

    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ScannerError(f"resource changed while being scanned: {path}")

    return before.st_size, digest.hexdigest(), bytes(prefix)


def _relative_sort_key(path: Path, root: Path) -> tuple[str, str]:
    relative = path.relative_to(root).as_posix()
    return relative.casefold(), relative


def scan_game_directory(game_dir: str | Path) -> list[ResourceRecord]:
    """Hash and classify every regular file below ``game_dir`` without writes."""

    root = validate_game_directory(game_dir)
    try:
        files = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: _relative_sort_key(path, root),
        )
    except OSError as exc:
        raise ScannerError(f"cannot enumerate game directory {root}: {exc}") from exc

    records: list[ResourceRecord] = []
    for path in files:
        if path.is_symlink():
            raise ScannerError(f"refusing to follow symbolic-link resource: {path}")

        relative_path = path.relative_to(root).as_posix()
        size, sha256, prefix = fingerprint_file(path)
        magic = detect_magic(prefix)
        records.append(
            ResourceRecord(
                relative_path=relative_path,
                size=size,
                suffix=path.suffix,
                magic=magic,
                sha256=sha256,
                category=classify_resource(relative_path, magic),
            )
        )

    return records


def build_inventory_fingerprint(records: list[ResourceRecord]) -> str:
    """Hash file identities and content hashes without including install location."""

    digest = hashlib.sha256()
    ordered_records = sorted(
        records,
        key=lambda record: (record.relative_path.casefold(), record.relative_path),
    )
    for record in ordered_records:
        path_bytes = record.relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, byteorder="big", signed=False))
        digest.update(path_bytes)
        digest.update(record.size.to_bytes(8, byteorder="big", signed=False))
        digest.update(bytes.fromhex(record.sha256))
    return f"sha256:{digest.hexdigest()}"


def build_resource_summary(
    game_dir: str | Path,
    records: list[ResourceRecord],
    *,
    large_file_threshold: int = DEFAULT_LARGE_FILE_THRESHOLD,
) -> dict[str, object]:
    """Build stable Phase 1 counts from an already completed inventory."""

    if large_file_threshold < 0:
        raise ValueError("large file threshold cannot be negative")

    root = validate_game_directory(game_dir)
    magic_counts = Counter(record.magic for record in records)
    category_counts = Counter(record.category for record in records)
    unknown_large_files = sum(
        record.category == "unknown" and record.size >= large_file_threshold
        for record in records
    )

    return {
        "schema_version": 1,
        "game_dir": str(root),
        "inventory_fingerprint": build_inventory_fingerprint(records),
        "file_count": len(records),
        "total_size_bytes": sum(record.size for record in records),
        "bundle_count": category_counts["unity_bundle_candidate"],
        "unityfs_count": magic_counts["UnityFS"],
        "catalog_count": category_counts["addressables_catalog"],
        "assets_count": category_counts["unity_assets_candidate"],
        "unknown_large_file_count": unknown_large_files,
        "large_file_threshold_bytes": large_file_threshold,
        "magic_counts": dict(sorted(magic_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
    }
