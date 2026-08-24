"""Fail closed when release archives contain local or game-derived data."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from glob import glob
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

PACKAGE_NAME = "musedash-chart-extractor"
PACKAGE_PATH_NAME = "musedash_chart_extractor"
FORBIDDEN_COMPONENTS = {
    "diagnostics",
    "experimental",
    "extracted",
    "exports",
    "musedashchartstore",
    "musedash_data",
    "payloads",
    "streamingassets",
}

SDIST_ROOT_FILES = {
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "PKG-INFO",
    "README.md",
    "ROADMAP.md",
    "pyproject.toml",
    "setup.cfg",
}
SDIST_EGG_INFO_FILES = {
    "PKG-INFO",
    "SOURCES.txt",
    "dependency_links.txt",
    "entry_points.txt",
    "requires.txt",
    "top_level.txt",
}
SDIST_TOOL_FILES = {
    "audit_extracted_batch.py",
    "audit_release_archives.py",
}
WHEEL_DIST_INFO_FILES = {
    "METADATA",
    "RECORD",
    "WHEEL",
    "entry_points.txt",
    "top_level.txt",
}
FORBIDDEN_FILE_NAMES = {
    "store.json",
    "store_audit.json",
}
FORBIDDEN_SUFFIXES = {
    ".assets",
    ".bundle",
    ".flac",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".ogg",
    ".odin",
    ".png",
    ".ress",
    ".resource",
    ".sqlite3",
    ".wav",
    ".webp",
}


class ArchiveAuditError(RuntimeError):
    """Raised when an archive is malformed, incomplete, or unsafe to publish."""


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    name: str
    member_type: str


def _archive_kind(path: Path) -> str:
    name = path.name.casefold()
    if name.endswith(".whl"):
        return "wheel"
    if name.endswith(".tar.gz"):
        return "sdist"
    raise ArchiveAuditError(f"unsupported release archive: {path}")


def _archive_members(path: Path, *, kind: str) -> list[ArchiveMember]:
    try:
        if kind == "wheel":
            with zipfile.ZipFile(path) as archive:
                members: list[ArchiveMember] = []
                for entry in archive.infolist():
                    mode = (entry.external_attr >> 16) & 0xFFFF
                    file_type = stat.S_IFMT(mode)
                    if entry.is_dir():
                        member_type = "directory"
                    elif entry.create_system == 3 and file_type == stat.S_IFLNK:
                        member_type = "symlink"
                    elif entry.create_system == 3 and file_type not in (0, stat.S_IFREG):
                        member_type = "special"
                    else:
                        member_type = "file"
                    members.append(ArchiveMember(entry.filename, member_type))
                return members
        with tarfile.open(path, mode="r:gz") as archive:
            return [
                ArchiveMember(
                    entry.name,
                    "file"
                    if entry.isfile()
                    else "directory"
                    if entry.isdir()
                    else "link"
                    if entry.issym() or entry.islnk()
                    else "special",
                )
                for entry in archive.getmembers()
            ]
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise ArchiveAuditError(f"cannot inspect {kind} archive {path}: {exc}") from exc


def _normalise_name(name: str, *, archive: Path) -> str:
    portable = name.replace("\\", "/")
    parsed = PurePosixPath(portable)
    if (
        not portable
        or parsed.is_absolute()
        or ".." in parsed.parts
        or (parsed.parts and ":" in parsed.parts[0])
    ):
        raise ArchiveAuditError(f"unsafe archive member in {archive}: {name!r}")
    return parsed.as_posix()


def _require_member(names: Sequence[str], suffix: str, *, archive: Path) -> None:
    if not any(name == suffix or name.endswith(f"/{suffix}") for name in names):
        raise ArchiveAuditError(f"required member {suffix!r} is missing from {archive}")


def _archive_version(path: Path, *, kind: str) -> str:
    escaped = re.escape(PACKAGE_PATH_NAME)
    if kind == "wheel":
        match = re.fullmatch(
            rf"{escaped}-(?P<version>[^-]+)-py3-none-any\.whl",
            path.name,
            flags=re.IGNORECASE,
        )
    else:
        match = re.fullmatch(
            rf"{escaped}-(?P<version>[^-]+)\.tar\.gz",
            path.name,
            flags=re.IGNORECASE,
        )
    if match is None:
        raise ArchiveAuditError(f"unexpected {kind} filename: {path.name}")
    return match.group("version")


def _wheel_file_allowed(name: str, *, version: str) -> bool:
    parts = PurePosixPath(name).parts
    if len(parts) >= 2 and parts[0] == PACKAGE_PATH_NAME:
        return PurePosixPath(name).suffix == ".py"

    dist_info = f"{PACKAGE_PATH_NAME}-{version}.dist-info"
    if len(parts) == 2 and parts[0] == dist_info:
        return parts[1] in WHEEL_DIST_INFO_FILES
    return (
        len(parts) == 3
        and parts[0] == dist_info
        and parts[1] == "licenses"
        and parts[2] == "LICENSE"
    )


def _sdist_file_allowed(name: str, *, version: str) -> bool:
    parts = PurePosixPath(name).parts
    root = f"{PACKAGE_PATH_NAME}-{version}"
    if not parts or parts[0] != root:
        return False
    relative = parts[1:]
    if len(relative) == 1:
        return relative[0] in SDIST_ROOT_FILES
    if len(relative) == 2 and relative[0] == "docs":
        return PurePosixPath(relative[1]).suffix == ".md"
    if (
        len(relative) >= 3
        and relative[:2] == ("src", PACKAGE_PATH_NAME)
    ):
        return PurePosixPath(*relative).suffix == ".py"
    if (
        len(relative) == 3
        and relative[:2] == ("src", f"{PACKAGE_PATH_NAME}.egg-info")
    ):
        return relative[2] in SDIST_EGG_INFO_FILES
    if len(relative) == 2 and relative[0] == "tests":
        return relative[1] == "conftest.py" or (
            relative[1].startswith("test_") and relative[1].endswith(".py")
        )
    return (
        len(relative) == 2
        and relative[0] == "tools"
        and relative[1] in SDIST_TOOL_FILES
    )


def _read_archive_file(path: Path, *, kind: str, name: str) -> bytes:
    try:
        if kind == "wheel":
            with zipfile.ZipFile(path) as archive:
                return archive.read(name)
        with tarfile.open(path, mode="r:gz") as archive:
            extracted = archive.extractfile(name)
            if extracted is None:
                raise ArchiveAuditError(
                    f"cannot read archive metadata member {name!r} from {path}"
                )
            return extracted.read()
    except (KeyError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise ArchiveAuditError(
            f"cannot read archive metadata member {name!r} from {path}: {exc}"
        ) from exc


def _verify_metadata(
    path: Path,
    *,
    kind: str,
    names: Sequence[str],
    version: str,
) -> None:
    suffix = ".dist-info/METADATA" if kind == "wheel" else ".egg-info/PKG-INFO"
    matches = [name for name in names if name.endswith(suffix)]
    if len(matches) != 1:
        raise ArchiveAuditError(
            f"{kind} must contain exactly one {suffix.rsplit('/', 1)[-1]}: {path}"
        )
    message = BytesParser(policy=email_policy).parsebytes(
        _read_archive_file(path, kind=kind, name=matches[0])
    )
    if message.get("Name") != PACKAGE_NAME:
        raise ArchiveAuditError(
            f"archive metadata Name mismatch in {path}: {message.get('Name')!r}"
        )
    if message.get("Version") != version:
        raise ArchiveAuditError(
            f"archive metadata Version mismatch in {path}: expected {version!r}, "
            f"found {message.get('Version')!r}"
        )


def audit_archive(path: str | Path) -> dict[str, Any]:
    archive_path = Path(path).expanduser().resolve(strict=True)
    kind = _archive_kind(archive_path)
    version = _archive_version(archive_path, kind=kind)
    members = [
        ArchiveMember(
            _normalise_name(member.name, archive=archive_path),
            member.member_type,
        )
        for member in _archive_members(archive_path, kind=kind)
    ]
    names = [member.name for member in members]
    if len(names) != len(set(names)):
        raise ArchiveAuditError(f"duplicate member names in {archive_path}")

    unsafe_types = [
        member.name
        for member in members
        if member.member_type not in {"file", "directory"}
    ]
    if unsafe_types:
        sample = ", ".join(unsafe_types[:5])
        raise ArchiveAuditError(
            f"unsafe archive member type in {archive_path}: {sample}"
        )

    file_names = [member.name for member in members if member.member_type == "file"]
    directory_names = [
        member.name.rstrip("/")
        for member in members
        if member.member_type == "directory"
    ]

    forbidden: list[str] = []
    for name in file_names:
        parsed = PurePosixPath(name)
        components = {part.casefold() for part in parsed.parts}
        if components & FORBIDDEN_COMPONENTS:
            forbidden.append(name)
            continue
        if parsed.name.casefold() in FORBIDDEN_FILE_NAMES:
            forbidden.append(name)
            continue
        if parsed.suffix.casefold() in FORBIDDEN_SUFFIXES:
            forbidden.append(name)
            continue
        allowed = (
            _wheel_file_allowed(name, version=version)
            if kind == "wheel"
            else _sdist_file_allowed(name, version=version)
        )
        if not allowed:
            forbidden.append(name)
    for name in directory_names:
        prefix = f"{name}/"
        if not any(file_name.startswith(prefix) for file_name in file_names):
            forbidden.append(name)
    if forbidden:
        sample = ", ".join(forbidden[:5])
        raise ArchiveAuditError(
            f"forbidden local or game-derived members in {archive_path}: {sample}"
        )

    _require_member(file_names, "musedash_chart_extractor/__init__.py", archive=archive_path)
    if kind == "wheel":
        dist_info = f"{PACKAGE_PATH_NAME}-{version}.dist-info"
        _require_member(file_names, f"{dist_info}/METADATA", archive=archive_path)
        _require_member(file_names, f"{dist_info}/RECORD", archive=archive_path)
        _require_member(
            file_names,
            f"{dist_info}/licenses/LICENSE",
            archive=archive_path,
        )
    else:
        for required in (
            "LICENSE",
            "README.md",
            "pyproject.toml",
            "tests/test_scanner.py",
            "tools/audit_extracted_batch.py",
            "tools/audit_release_archives.py",
        ):
            _require_member(file_names, required, archive=archive_path)
    _verify_metadata(
        archive_path,
        kind=kind,
        names=file_names,
        version=version,
    )

    return {
        "archive": archive_path.name,
        "kind": kind,
        "name": PACKAGE_NAME,
        "version": version,
        "member_count": len(names),
        "status": "passed",
    }


def audit_archives(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    if not paths:
        raise ArchiveAuditError("at least one release archive is required")
    reports = [audit_archive(path) for path in paths]
    counts = Counter(report["kind"] for report in reports)
    if counts != {"sdist": 1, "wheel": 1}:
        raise ArchiveAuditError(
            "release directory must contain exactly one sdist and one wheel; "
            f"found {dict(sorted(counts.items()))}"
        )
    versions = {report["version"] for report in reports}
    if len(versions) != 1:
        raise ArchiveAuditError(
            f"sdist and wheel versions differ: {sorted(versions)}"
        )
    return sorted(reports, key=lambda report: report["kind"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit release archives for local or game-derived content."
    )
    parser.add_argument("archives", nargs="+")
    arguments = parser.parse_args(argv)
    try:
        expanded: list[str] = []
        for value in arguments.archives:
            if any(character in value for character in "*?["):
                matches = sorted(glob(value))
                if not matches:
                    raise ArchiveAuditError(f"archive pattern matched no files: {value}")
                expanded.extend(matches)
            else:
                expanded.append(value)
        reports = audit_archives(expanded)
    except (ArchiveAuditError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"archives": reports, "status": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
