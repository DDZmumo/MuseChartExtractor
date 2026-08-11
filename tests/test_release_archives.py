from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from importlib.metadata import version as installed_version
from pathlib import Path

from musedash_chart_extractor import __version__
from tools.audit_release_archives import ArchiveAuditError, audit_archives, main


WHEEL_MEMBERS = {
    "musedash_chart_extractor/__init__.py": b"",
    "musedash_chart_extractor-0.1.0.dist-info/METADATA": (
        b"Metadata-Version: 2.4\nName: musedash-chart-extractor\nVersion: 0.1.0\n"
    ),
    "musedash_chart_extractor-0.1.0.dist-info/RECORD": b"",
    "musedash_chart_extractor-0.1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
    "musedash_chart_extractor-0.1.0.dist-info/licenses/LICENSE": b"fixture",
}
SDIST_MEMBERS = {
    "musedash_chart_extractor-0.1.0/LICENSE": b"fixture",
    "musedash_chart_extractor-0.1.0/README.md": b"fixture",
    "musedash_chart_extractor-0.1.0/pyproject.toml": b"fixture",
    "musedash_chart_extractor-0.1.0/src/musedash_chart_extractor/__init__.py": b"",
    "musedash_chart_extractor-0.1.0/src/musedash_chart_extractor.egg-info/PKG-INFO": (
        b"Metadata-Version: 2.4\nName: musedash-chart-extractor\nVersion: 0.1.0\n"
    ),
    "musedash_chart_extractor-0.1.0/tests/test_scanner.py": b"",
    "musedash_chart_extractor-0.1.0/tools/audit_release_archives.py": b"",
}


def _write_archives(
    root: Path,
    *,
    extra_wheel_member: str | None = None,
    sdist_link: str | None = None,
) -> tuple[Path, Path]:
    wheel = root / "musedash_chart_extractor-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        for name, content in WHEEL_MEMBERS.items():
            archive.writestr(name, content)
        if extra_wheel_member is not None:
            archive.writestr(extra_wheel_member, b"must not ship")

    sdist = root / "musedash_chart_extractor-0.1.0.tar.gz"
    with tarfile.open(sdist, mode="w:gz") as archive:
        for name, content in SDIST_MEMBERS.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if sdist_link is not None:
            info = tarfile.TarInfo(sdist_link)
            info.type = tarfile.SYMTYPE
            info.linkname = "README.md"
            archive.addfile(info)
    return wheel, sdist


class ReleaseArchiveAuditTests(unittest.TestCase):
    def test_runtime_and_installed_metadata_versions_match(self) -> None:
        self.assertEqual(installed_version("musedash-chart-extractor"), __version__)

    def test_accepts_one_complete_sdist_and_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = audit_archives(_write_archives(root))
            self.assertEqual(main((str(root / "*"),)), 0)

        self.assertEqual([report["kind"] for report in reports], ["sdist", "wheel"])
        self.assertTrue(all(report["status"] == "passed" for report in reports))

    def test_rejects_local_output_or_game_resource_members(self) -> None:
        for member in (
            "extracted/charts/chart.json",
            "exports/chart.csv",
            "package/StreamingAssets/chart.bundle",
            "musedash_chart_extractor/fixtures/official_chart.json",
        ):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as temporary:
                archives = _write_archives(
                    Path(temporary), extra_wheel_member=member
                )
                with self.assertRaisesRegex(
                    ArchiveAuditError, "forbidden local or game-derived"
                ):
                    audit_archives(archives)

    def test_rejects_links_and_missing_license(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archives = _write_archives(
                root,
                sdist_link="musedash_chart_extractor-0.1.0/docs/latest.md",
            )
            with self.assertRaisesRegex(ArchiveAuditError, "unsafe archive member type"):
                audit_archives(archives)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel, sdist = _write_archives(root)
            with zipfile.ZipFile(wheel, mode="w") as archive:
                for name, content in WHEEL_MEMBERS.items():
                    if not name.endswith("/LICENSE"):
                        archive.writestr(name, content)
            with self.assertRaisesRegex(ArchiveAuditError, "LICENSE.*missing"):
                audit_archives((wheel, sdist))

    def test_rejects_metadata_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel, sdist = _write_archives(root)
            with zipfile.ZipFile(wheel, mode="w") as archive:
                for name, content in WHEEL_MEMBERS.items():
                    if name.endswith("/METADATA"):
                        content = content.replace(b"Version: 0.1.0", b"Version: 9.9.9")
                    archive.writestr(name, content)
            with self.assertRaisesRegex(ArchiveAuditError, "Version mismatch"):
                audit_archives((wheel, sdist))

    def test_rejects_path_traversal_and_duplicate_archive_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel, _sdist = _write_archives(root)
            with zipfile.ZipFile(wheel, mode="a") as archive:
                archive.writestr("../outside.py", b"")
            with self.assertRaisesRegex(ArchiveAuditError, "unsafe archive member"):
                audit_archives((wheel,))

        with tempfile.TemporaryDirectory() as temporary:
            wheel, _sdist = _write_archives(Path(temporary))
            copy_root = wheel.parent / "copy"
            copy_root.mkdir()
            copy = copy_root / wheel.name
            copy.write_bytes(wheel.read_bytes())
            with self.assertRaisesRegex(ArchiveAuditError, "exactly one sdist"):
                audit_archives((wheel, copy))


if __name__ == "__main__":
    unittest.main()
