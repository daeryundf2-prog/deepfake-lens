import io
import tarfile
import zipfile
from pathlib import Path
import unittest
from typing import Sequence
from unittest.mock import patch

from deepfake_lens.archives import (
    ExtractionBudget,
    archive_format,
    extract_archive,
    is_archive,
    _7z_link_names,
    _rar_member_rejected,
    _safe_member_name,
)
from deepfake_lens.core import analyze_file, scan_directory


def make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


class ArchiveFormatTests(unittest.TestCase):
    def test_format_detection(self):
        self.assertEqual(archive_format("a.zip"), "zip")
        self.assertEqual(archive_format("a.tar"), "tar")
        self.assertEqual(archive_format("a.tar.gz"), "tar")
        self.assertEqual(archive_format("a.tgz"), "tar")
        self.assertEqual(archive_format("a.7z"), "7z")
        self.assertEqual(archive_format("a.rar"), "rar")
        self.assertIsNone(archive_format("a.png"))
        self.assertIsNone(archive_format("a.gz"))

    def test_safe_member_name(self):
        self.assertEqual(_safe_member_name("dir/file.png"), "dir/file.png")
        self.assertIsNone(_safe_member_name("../evil.png"))
        self.assertIsNone(_safe_member_name("a/../../evil.png"))
        self.assertIsNone(_safe_member_name("/abs/path.png"))
        self.assertIsNone(_safe_member_name("C:/win/evil.png"))
        self.assertIsNone(_safe_member_name("file.txt:ads"))
        self.assertIsNone(_safe_member_name("CON"))
        self.assertIsNone(_safe_member_name("aux.png"))


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zip_members_extracted(self):
        zpath = self.tmp / "a.zip"
        make_zip(zpath, {"one.txt": b"hello world content here", "dir/two.txt": b"second file content"})
        out = extract_archive(zpath, self.tmp / "out")
        self.assertEqual(len(out.members), 2)
        self.assertEqual(out.skipped, 0)

    def test_zip_slip_blocked(self):
        zpath = self.tmp / "evil.zip"
        make_zip(zpath, {"../evil.txt": b"escape", "ok.txt": b"fine content inside"})
        out = extract_archive(zpath, self.tmp / "out")
        self.assertEqual(len(out.members), 1)
        self.assertEqual(out.skipped, 1)
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_nested_zip_expanded(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("deep.txt", b"nested inner content")
        zpath = self.tmp / "outer.zip"
        make_zip(zpath, {"inner.zip": inner.getvalue(), "top.txt": b"top level content"})
        out = extract_archive(zpath, self.tmp / "out")
        names = sorted(m.name for m in out.members)
        self.assertIn("deep.txt", names)
        self.assertIn("top.txt", names)
        self.assertNotIn("inner.zip", names)

    def test_corrupt_zip_warns_not_raises(self):
        bad = self.tmp / "bad.zip"
        bad.write_bytes(b"not a zip at all")
        out = extract_archive(bad, self.tmp / "out")
        self.assertEqual(out.members, [])
        self.assertTrue(out.warnings)

    def test_tar_members_extracted(self):
        tpath = self.tmp / "a.tar"
        with tarfile.open(tpath, "w") as tf:
            data = b"tar member content"
            info = tarfile.TarInfo("m.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        out = extract_archive(tpath, self.tmp / "out")
        self.assertEqual(len(out.members), 1)


class ScanIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_expands_zip_members(self):
        make_zip(self.tmp / "bundle.zip", {
            "notes.txt": b"This is a plain text file inside the archive with enough content to analyze.",
        })
        summary, items = scan_directory(self.tmp)
        paths = [i.path for i in items]
        self.assertTrue(any("bundle.zip::notes.txt" in p for p in paths))
        self.assertTrue(any(i.kind == "archive" for i in items))
        member = next(i for i in items if "::" in i.path)
        self.assertEqual(member.kind, "text")
        self.assertEqual(member.status, "analyzed")

    def test_scan_temp_dirs_cleaned(self):
        make_zip(self.tmp / "b.zip", {"x.txt": b"content to check cleanup"})
        dest = self.tmp / "extracted"
        dest.mkdir()
        with patch("deepfake_lens.core.tempfile.mkdtemp", return_value=str(dest)):
            scan_directory(self.tmp)
        self.assertFalse(dest.exists())
        self.assertTrue((self.tmp / "b.zip").is_file())

    def test_scan_normalizes_extraction_root(self):
        make_zip(self.tmp / "bundle.zip", {"dir/notes.txt": b"plain text inside the archive"})
        dest = self.tmp / "extracted"
        dest.mkdir()
        unresolved = str(dest / ".." / dest.name)
        with patch("deepfake_lens.core.tempfile.mkdtemp", return_value=unresolved):
            with patch("deepfake_lens.core.extract_archive", wraps=extract_archive) as extract:
                summary, items = scan_directory(self.tmp)
        self.assertEqual(extract.call_args.args[1], dest.resolve())
        self.assertEqual(summary.total, 2)
        member = next(item for item in items if item.path == "bundle.zip::dir/notes.txt")
        self.assertEqual(member.status, "analyzed")
        self.assertFalse(dest.exists())

    def test_scan_temp_dirs_cleaned_on_preparation_failure(self):
        for name in ("a.zip", "b.zip"):
            make_zip(self.tmp / name, {"notes.txt": b"plain text inside the archive"})
        destinations = [self.tmp / "first", self.tmp / "second"]
        for dest in destinations:
            dest.mkdir()

        def fail_second_extraction(path, dest):
            extraction = extract_archive(path, dest)
            if dest == destinations[1].resolve():
                raise RuntimeError("preparation failed")
            return extraction

        # G32: the scan walks a directory in sorted path order, so a.zip is
        # always extracted into destinations[0] and b.zip (the one made to
        # fail) into destinations[1]. Before the fix this depended on the
        # OS directory order and failed whenever b.zip was listed first.
        with patch("deepfake_lens.core.tempfile.mkdtemp", side_effect=[str(dest) for dest in destinations]):
            with patch("deepfake_lens.core.extract_archive", side_effect=fail_second_extraction):
                # One corrupt archive must not kill the whole scan — it
                # becomes a failed/unknown container row and cleanup runs.
                summary, items = scan_directory(self.tmp)
        self.assertTrue(all(not dest.exists() for dest in destinations))
        self.assertTrue(all((self.tmp / name).is_file() for name in ("a.zip", "b.zip")))
        # The failed archive surfaces as a non-clean row, never low.
        b_rows = [i for i in items if i.path.startswith("b.zip")]
        self.assertTrue(b_rows)
        self.assertTrue(all(
            (i.result is None) or (i.result.band.value != "low")
            for i in b_rows
        ))

    def test_scan_temp_dirs_cleaned_on_analysis_failure(self):
        make_zip(self.tmp / "bundle.zip", {"notes.txt": b"plain text inside the archive"})
        dest = self.tmp / "extracted"
        dest.mkdir()
        with patch("deepfake_lens.core.tempfile.mkdtemp", return_value=str(dest)):
            with patch("deepfake_lens.core._scan_specs", side_effect=RuntimeError("analysis failed")):
                with self.assertRaisesRegex(RuntimeError, "analysis failed"):
                    scan_directory(self.tmp)
        self.assertFalse(dest.exists())

    def test_analyze_file_archive_container(self):
        zpath = self.tmp / "single.zip"
        make_zip(zpath, {"a.txt": b"some content"})
        item = analyze_file(zpath)
        self.assertEqual(item.kind, "archive")
        self.assertEqual(item.status, "analyzed")


def _zip_payload(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


class ExtractionBudgetTests(unittest.TestCase):
    """G34: one aggregate budget per top-level archive, threaded through nesting."""

    def setUp(self) -> None:
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_budget_limits(self) -> None:
        budget = ExtractionBudget.default()
        self.assertEqual(budget.total_bytes, 2 * 1024 * 1024 * 1024)
        self.assertEqual(budget.total_members, 5000)
        self.assertEqual(budget.nested_archives, 50)

    def test_nested_archive_budget(self) -> None:
        inner = {f"in{i}.zip": _zip_payload({f"n{i}.txt": b"inner text content"}) for i in range(5)}
        zpath = self.tmp / "outer.zip"
        make_zip(zpath, inner)
        budget = ExtractionBudget(nested_archives=2)
        out = extract_archive(zpath, self.tmp / "out", budget=budget)
        self.assertEqual(budget.nested_used, 2)
        self.assertEqual(sorted(m.name for m in out.members), ["n0.txt", "n1.txt"])
        self.assertEqual(out.skipped, 3)
        self.assertTrue(any("중첩 압축 예산" in w for w in out.warnings), out.warnings)

    def test_byte_budget_counts_written_bytes(self) -> None:
        zpath = self.tmp / "a.zip"
        make_zip(zpath, {f"m{i}.bin": bytes([i]) * 100 for i in range(3)})
        budget = ExtractionBudget(total_bytes=250)
        out = extract_archive(zpath, self.tmp / "out", budget=budget)
        self.assertEqual(len(out.members), 2)
        self.assertLessEqual(budget.bytes_used, 250)
        self.assertTrue(any("총량 예산" in w for w in out.warnings), out.warnings)

    def test_member_budget_shared_across_nesting(self) -> None:
        inner = {f"in{i}.zip": _zip_payload({f"n{i}-{j}.txt": b"x" * 10 for j in range(3)}) for i in range(2)}
        zpath = self.tmp / "outer.zip"
        make_zip(zpath, inner)
        budget = ExtractionBudget(total_members=5)
        out = extract_archive(zpath, self.tmp / "out", budget=budget)
        # 2 inner zips + 3 members of the first; the second inner zip gets none.
        self.assertEqual(budget.members_used, 5)
        self.assertEqual(sorted(m.name for m in out.members), ["in1.zip", "n0-0.txt", "n0-1.txt", "n0-2.txt"])
        self.assertTrue(any("멤버 수 예산" in w for w in out.warnings), out.warnings)

    def test_same_stem_inner_archives_do_not_collide(self) -> None:
        zpath = self.tmp / "outer.zip"
        make_zip(zpath, {
            "a/x.zip": _zip_payload({"one.txt": b"first inner"}),
            "b/x.zip": _zip_payload({"two.txt": b"second inner"}),
        })
        out = extract_archive(zpath, self.tmp / "out")
        self.assertEqual(sorted(m.name for m in out.members), ["one.txt", "two.txt"])

    def test_docstring_does_not_claim_tar_filter(self) -> None:
        import deepfake_lens.archives as archives_module

        self.assertNotIn('filter="data"', archives_module.__doc__ or "")


class _FakeRarInfo:
    def __init__(self, filename: str, data: bytes, *, symlink: bool = False, redir: object = None, regular: bool = True) -> None:
        self.filename = filename
        self.file_size = len(data)
        self.data = data
        self.file_redir = redir
        self._symlink = symlink
        self._regular = regular

    def isdir(self) -> bool:
        return False

    def is_symlink(self) -> bool:
        return self._symlink

    def is_file(self) -> bool:
        return self._regular and not self._symlink


class _FakeRarFile:
    infos: list[_FakeRarInfo] = []

    def __init__(self, path: object) -> None:
        self.path = path

    def __enter__(self) -> "_FakeRarFile":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def infolist(self) -> list[_FakeRarInfo]:
        return list(self.infos)

    def open(self, info: _FakeRarInfo) -> io.BytesIO:
        return io.BytesIO(info.data)


class _Fake7zEntry:
    def __init__(self, filename: str, data: bytes, *, symlink: bool = False) -> None:
        self.filename = filename
        self.data = data
        self.uncompressed = len(data)
        self.is_directory = False
        self._symlink = symlink

    @property
    def is_symlink(self) -> bool:
        return self._symlink


class _FakeSevenZipFile:
    entries: list[_Fake7zEntry] = []
    extracted: list[str] = []

    def __init__(self, path: object) -> None:
        self.files = list(self.entries)

    def __enter__(self) -> "_FakeSevenZipFile":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list(self) -> list[_Fake7zEntry]:
        return list(self.entries)

    def extract(self, dest: Path, targets: "Sequence[str]") -> None:
        by_name = {entry.filename: entry for entry in self.entries}
        for name in targets:
            _FakeSevenZipFile.extracted.append(name)
            target = Path(dest) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(by_name[name].data)


class OptionalFormatLinkTests(unittest.TestCase):
    """G34: 7z/rar symlink (and other non-regular) members are refused.

    py7zr/rarfile are optional and absent in CI, so the libraries are
    replaced by fakes exposing the member attributes they document.
    """

    def setUp(self) -> None:
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rar_symlink_and_redirect_members_skipped(self) -> None:
        import sys
        import types

        module = types.ModuleType("rarfile")
        module.RarFile = _FakeRarFile  # type: ignore[attr-defined]
        _FakeRarFile.infos = [
            _FakeRarInfo("ok.txt", b"regular rar member"),
            _FakeRarInfo("link", b"/etc/passwd", symlink=True),
            _FakeRarInfo("hard", b"", redir=(4, 0, "ok.txt")),
            _FakeRarInfo("device", b"", regular=False),
        ]
        archive = self.tmp / "a.rar"
        archive.write_bytes(b"Rar!")
        with patch.dict(sys.modules, {"rarfile": module}):
            out = extract_archive(archive, self.tmp / "out")
        self.assertEqual([m.name for m in out.members], ["ok.txt"])
        self.assertEqual(out.skipped, 3)
        self.assertFalse((self.tmp / "out" / "link").exists())

    def test_7z_symlink_members_skipped(self) -> None:
        import sys
        import types

        module = types.ModuleType("py7zr")
        module.SevenZipFile = _FakeSevenZipFile  # type: ignore[attr-defined]
        _FakeSevenZipFile.entries = [
            _Fake7zEntry("ok.txt", b"regular 7z member"),
            _Fake7zEntry("link", b"/etc", symlink=True),
        ]
        _FakeSevenZipFile.extracted = []
        archive = self.tmp / "a.7z"
        archive.write_bytes(b"7z")
        with patch.dict(sys.modules, {"py7zr": module}):
            out = extract_archive(archive, self.tmp / "out")
        self.assertEqual([m.name for m in out.members], ["ok.txt"])
        self.assertEqual(out.skipped, 1)
        self.assertEqual(_FakeSevenZipFile.extracted, ["ok.txt"])

    def test_link_predicates(self) -> None:
        self.assertTrue(_rar_member_rejected(_FakeRarInfo("l", b"", symlink=True)))
        self.assertTrue(_rar_member_rejected(_FakeRarInfo("h", b"", redir=(4, 0, "x"))))
        self.assertFalse(_rar_member_rejected(_FakeRarInfo("f", b"data")))

        class Archive:
            files = [_Fake7zEntry("a", b""), _Fake7zEntry("b", b"", symlink=True)]

        self.assertEqual(_7z_link_names(Archive()), {"b"})


class RejectedMemberRecordTests(unittest.TestCase):
    """D9/D10: every refused member and every symlink is recorded with its reason."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _bomb_zip(path: Path) -> tuple[int, int]:
        """A real deflate bomb member (8 MiB of zeros) next to a normal member."""
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.writestr("zeros.bin", bytes(8 * 1024 * 1024))
            zf.writestr("ok.txt", "정상 구성 파일입니다. 사람이 쓴 짧은 메모입니다.")
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo("zeros.bin")
            return info.file_size, info.compress_size

    def test_hostile_members_have_specific_reasons_and_container_has_sha256(self) -> None:
        import hashlib

        from deepfake_lens.archives import MAX_ARCHIVE_RATIO
        from deepfake_lens.result_types import CoverageStatus

        folder = self.root / "case"
        folder.mkdir()
        arc = folder / "evil.zip"
        declared, compressed = self._bomb_zip(arc)
        with zipfile.ZipFile(arc, "a") as zf:
            zf.writestr("../escape.png", b"x")
            zf.writestr("/abs/evil.png", b"y")
            link = zipfile.ZipInfo("link-to-etc")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            zf.writestr(link, "/etc/passwd")
        _, items = scan_directory(folder)
        container = next(item for item in items if item.kind == "archive")
        assert container.result is not None
        self.assertEqual(container.sha256, hashlib.sha256(arc.read_bytes()).hexdigest())
        entries = [entry for entry in container.result.coverage if entry.check == "archive_member"]
        self.assertTrue(entries)
        self.assertTrue(all(entry.status == CoverageStatus.SKIPPED for entry in entries))
        reasons = {entry.reason for entry in entries}
        expected_bomb = f"zeros.bin: 압축 예산 초과(선언 크기 {declared}, 한도 {compressed * MAX_ARCHIVE_RATIO})"
        self.assertTrue(any(reason.startswith(expected_bomb) for reason in reasons), reasons)
        self.assertIn("../escape.png: 경로 이탈 멤버('..' — 대상 폴더 밖 쓰기 시도)", reasons)
        self.assertIn("/abs/evil.png: 절대 경로 멤버(대상 폴더 밖 쓰기 시도)", reasons)
        self.assertIn("link-to-etc: 심볼릭 링크 멤버", reasons)
        self.assertTrue(any("구성 파일 거부: zeros.bin — 압축 예산 초과" in lim for lim in container.result.limitations))
        members = [item.path for item in items if item.path.startswith("evil.zip::")]
        self.assertEqual(members, ["evil.zip::ok.txt"])

    def test_declared_size_bomb_and_budget_exhaustion_reasons(self) -> None:
        import struct

        from deepfake_lens import archives
        from deepfake_lens.archives import MAX_ARCHIVE_MEMBER_BYTES

        folder = self.root / "case"
        folder.mkdir()
        raw = bytearray(_zip_payload({"bomb.bin": b"z" * 4096}))
        forged = struct.pack("<I", 1 << 30)
        local = raw.index(b"PK\x03\x04")
        raw[local + 22:local + 26] = forged
        central = raw.index(b"PK\x01\x02")
        raw[central + 24:central + 28] = forged
        (folder / "declared.zip").write_bytes(bytes(raw))
        with zipfile.ZipFile(folder / "fat.zip", "w", compression=zipfile.ZIP_STORED) as zf:
            for index in range(4):
                zf.writestr(f"blob-{index}.bin", bytes([index]) * (300 * 1024))
        with patch.object(archives, "TOTAL_EXTRACTION_BYTES", 700 * 1024):
            _, items = scan_directory(folder)
        by_path = {item.path: item for item in items}
        declared_item = by_path["declared.zip"]
        assert declared_item.result is not None
        declared_reasons = [e.reason for e in declared_item.result.coverage if e.check == "archive_member"]
        self.assertEqual(
            declared_reasons,
            [f"bomb.bin: 압축 예산 초과(선언 크기 {1 << 30}, 한도 {MAX_ARCHIVE_MEMBER_BYTES}) — 멤버당 크기 상한"],
        )
        self.assertIn("압축 예산 초과", declared_item.result.verdict)
        fat_item = by_path["fat.zip"]
        assert fat_item.result is not None
        fat_reasons = [e.reason for e in fat_item.result.coverage if e.check == "archive_member"]
        self.assertEqual(len(fat_reasons), 2, fat_reasons)
        self.assertRegex(fat_reasons[0], r"^blob-2\.bin: 압축 예산 초과\(선언 크기 307200, 한도 \d+\) — 압축 해제 총량 예산 소진$")
        self.assertEqual(fat_reasons[1], "blob-3.bin: 앞선 멤버에서 해제 예산 소진으로 미해제")

    def test_nested_rejections_are_prefixed_with_the_inner_archive(self) -> None:
        inner = _zip_payload({"../up.txt": b"escape", "fine.txt": "내부의 정상 파일입니다.".encode()})
        arc = self.root / "outer.zip"
        make_zip(arc, {"sub/inner.zip": inner})
        out = extract_archive(arc, self.root / "out")
        self.assertIn(("sub/inner.zip::../up.txt", "경로 이탈 멤버('..' — 대상 폴더 밖 쓰기 시도)"), out.rejected)
        self.assertEqual(out.skipped, len(out.rejected))

    def test_symlinks_in_scanned_folder_are_skipped_rows(self) -> None:
        import os

        from deepfake_lens.core import SYMLINK_SKIP_REASON

        folder = self.root / "case"
        (folder / "sub").mkdir(parents=True)
        (folder / "real.txt").write_text("실제 파일 내용입니다.", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("폴더 밖 파일", encoding="utf-8")
        try:
            os.symlink(outside / "secret.txt", folder / "link.txt")
            os.symlink(outside, folder / "sub" / "linked-dir")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted on this platform")
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                summary, items = scan_directory(folder, recursive=recursive)
                by_path = {item.path: item for item in items}
                link = by_path["link.txt"]
                self.assertEqual(link.status, "skipped")
                self.assertEqual(link.error, SYMLINK_SKIP_REASON)
                self.assertTrue((link.error or "").startswith("심볼릭 링크"))
                self.assertIsNone(link.result)
                if recursive:
                    self.assertEqual(by_path[str(Path("sub") / "linked-dir")].status, "skipped")
                self.assertEqual(summary.skipped, 2 if recursive else 1)
                self.assertFalse(any("secret.txt" in path for path in by_path))


if __name__ == "__main__":
    unittest.main()
