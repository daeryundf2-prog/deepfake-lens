import io
import tarfile
import zipfile
from pathlib import Path
import unittest
from typing import Any, Sequence
from unittest.mock import patch

from deepfake_lens.archives import (
    ExtractionBudget,
    archive_format,
    extract_archive,
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

    def test_missing_extractor_and_unreadable_archive_are_named_in_coverage(self) -> None:
        import sys

        from deepfake_lens.result_types import CoverageStatus, Verdict

        folder = self.root / "case"
        folder.mkdir()
        (folder / "a.7z").write_bytes(b"7z\xbc\xaf\x27\x1c\x00\x04" + bytes(24))
        (folder / "broken.zip").write_bytes(b"PK\x03\x04 not really a zip")
        with patch.dict(sys.modules, {"py7zr": None}):
            _, items = scan_directory(folder)
        by_path = {item.path: item for item in items}
        seven = by_path["a.7z"].result
        assert seven is not None
        [entry] = [c for c in seven.coverage if c.check == "archive"]
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, "의존성 부재: py7zr"))
        self.assertEqual(seven.verdict_code, Verdict.UNDETERMINED)
        self.assertIn("의존성 부재: py7zr", seven.verdict)
        broken = by_path["broken.zip"].result
        assert broken is not None
        [entry] = [c for c in broken.coverage if c.check == "archive"]
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertIn("BadZipFile", entry.reason)
        self.assertIn("압축 해제 실패", broken.verdict)

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

    def test_allowed_symlinks_never_vanish(self) -> None:
        """X3: with allow_symlinks a broken, self-referencing or circular link is a
        skipped row with its reason; a linked folder is followed by a recursive scan
        and counted as a skipped subfolder by a flat one — never "(심볼릭 링크 허용 안 함)"."""
        import os

        from deepfake_lens.core import (
            NOT_REGULAR_FILE_REASON,
            SYMLINK_DANGLING_REASON,
            SYMLINK_LOOP_REASON,
            SYMLINK_SKIP_REASON,
        )
        from deepfake_lens.scan_cache import SYMLINK_DUPLICATE_REASON

        folder = self.root / "symcase"
        (folder / "d" / "e").mkdir(parents=True)
        (folder / "real.txt").write_text("실제 파일 내용입니다.", encoding="utf-8")
        (folder / "d" / "n.txt").write_text("하위 폴더 파일입니다.", encoding="utf-8")
        try:
            os.symlink("nope.txt", folder / "dangling.txt")
            os.symlink("self.txt", folder / "self.txt")
            os.symlink("b.txt", folder / "a.txt")
            os.symlink("a.txt", folder / "b.txt")
            os.symlink("real.txt", folder / "good.txt")
            os.symlink("d", folder / "dlink")
            os.symlink("..", folder / "d" / "e" / "up")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted on this platform")
        fifo = hasattr(os, "mkfifo")
        if fifo:
            os.mkfifo(folder / "pipe.txt")
        loops = {"self.txt", "a.txt", "b.txt"}
        for recursive in (False, True):
            with self.subTest(recursive=recursive):
                summary, items = scan_directory(folder, recursive=recursive, allow_symlinks=True)
                by_path = {item.path: item for item in items}
                self.assertEqual(by_path["dangling.txt"].error, SYMLINK_DANGLING_REASON)
                for name in loops:
                    self.assertEqual((by_path[name].status, by_path[name].error), ("skipped", SYMLINK_LOOP_REASON))
                self.assertEqual(by_path["good.txt"].status, "analyzed")
                if fifo:
                    self.assertEqual((by_path["pipe.txt"].status, by_path["pipe.txt"].error), ("skipped", NOT_REGULAR_FILE_REASON))
                self.assertFalse(any(item.error == SYMLINK_SKIP_REASON for item in items))
                if recursive:
                    self.assertEqual(by_path["dlink/n.txt"].status, "analyzed")
                    self.assertEqual(by_path["d/e/up"].error, SYMLINK_LOOP_REASON)
                    self.assertEqual(summary.subfolders_skipped, 0)
                else:
                    # R9-2 (round 9): this expected "d and the linked dlink" — the same
                    # folder counted twice (encoded the defect). A flat scan counts each
                    # folder once; dlink -> d is a "이미 따라간 링크 대상" row.
                    self.assertEqual((by_path["dlink"].status, by_path["dlink"].error), ("skipped", SYMLINK_DUPLICATE_REASON))
                    self.assertEqual(summary.subfolders_skipped, 1)
                self.assertEqual(summary.total, len(items))
                self.assertEqual(summary.skipped, 4 + int(fifo) + (2 if recursive else 1))

    def _scan_with_deadline(self, folder: Path, seconds: float = 60.0, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
        """scan_directory in a thread; fails (instead of hanging the suite) when it does not finish."""
        import threading

        from deepfake_lens.core import scan_directory

        box: dict[str, Any] = {}

        def run() -> None:
            box["out"] = scan_directory(folder, **kwargs)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(seconds)
        self.assertFalse(worker.is_alive(), f"scan did not finish within {seconds} s (P3: endless symlink walk)")
        summary, items = box["out"]
        return summary, {item.path: item for item in items}

    def test_links_to_root_parents_and_mutual_links_terminate(self) -> None:
        """P3 (round 8): --recursive --allow-symlinks followed a link to "/" (the whole file
        system) and links between two folders without end. A link to the scanned folder or
        any folder above it is a "순환/상위 링크" row, a folder entered through one link is
        not entered through another, and the walk ends."""
        import os

        from deepfake_lens.scan_cache import (
            SYMLINK_ANCESTOR_REASON,
            SYMLINK_DUPLICATE_REASON,
            SYMLINK_LOOP_REASON,
        )

        folder = self.root / "p3"
        (folder / "x").mkdir(parents=True)
        (folder / "y").mkdir()
        (folder / "a.txt").write_text("검사 폴더의 파일입니다.", encoding="utf-8")
        (folder / "x" / "in_x.txt").write_text("x 폴더의 파일입니다.", encoding="utf-8")
        shared = self.root / "shared"
        shared.mkdir()
        (shared / "s.txt").write_text("공유 폴더의 파일입니다.", encoding="utf-8")
        try:
            os.symlink(os.path.abspath(os.sep), folder / "to_fs_root")
            os.symlink("..", folder / "to_parent")
            os.symlink(".", folder / "to_self")
            os.symlink(os.path.join("..", "y"), folder / "x" / "ly")
            os.symlink(os.path.join("..", "x"), folder / "y" / "lx")
            os.symlink(shared, folder / "s1")
            os.symlink(shared, folder / "s2")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted on this platform")
        summary, by_path = self._scan_with_deadline(folder, recursive=True, allow_symlinks=True)
        for name in ("to_fs_root", "to_parent", "to_self"):
            self.assertEqual((by_path[name].status, by_path[name].error), ("skipped", SYMLINK_ANCESTOR_REASON), name)
        self.assertEqual(by_path["x/ly/lx"].error, SYMLINK_LOOP_REASON)
        self.assertEqual(by_path["y/lx/ly"].error, SYMLINK_LOOP_REASON)
        self.assertEqual(by_path["s1/s.txt"].status, "analyzed")
        self.assertEqual(by_path["s2"].error, SYMLINK_DUPLICATE_REASON)
        self.assertNotIn("s2/s.txt", by_path)
        self.assertFalse(any(path.startswith(("to_fs_root/", "to_parent/", "to_self/")) for path in by_path))
        self.assertEqual(summary.total, len(by_path))

    def test_walk_limits_leave_a_row_per_unopened_folder(self) -> None:
        """P3 (round 8): the walk has folder, file and time limits; a folder it does not
        open because a limit was reached is a row with the reason, never a silent gap."""
        from deepfake_lens import scan_cache

        folder = self.root / "p3-limits"
        for name in ("a", "b", "c", "d"):
            (folder / name / "deep").mkdir(parents=True)
            (folder / name / "deep" / "f.txt").write_text(f"{name} 폴더의 파일입니다.", encoding="utf-8")
        (folder / "top.txt").write_text("최상위 파일입니다.", encoding="utf-8")

        def unopened(by_path: dict[str, Any]) -> dict[str, str]:
            return {path: item.error for path, item in by_path.items() if (item.error or "").startswith("건너뜀: 탐색 상한 도달")}

        with patch.object(scan_cache, "MAX_WALK_DIRS", 3):  # root, a, a/deep
            summary, by_path = self._scan_with_deadline(folder, recursive=True)
        self.assertEqual(sorted(unopened(by_path)), ["b", "c", "d"])
        self.assertIn("폴더 3개", by_path["b"].error)
        self.assertEqual(by_path["a/deep/f.txt"].status, "analyzed")
        self.assertEqual(summary.total, len(by_path))

        with patch.object(scan_cache, "MAX_WALK_SECONDS", -1.0):
            _, by_path = self._scan_with_deadline(folder, recursive=True)
        self.assertEqual(sorted(unopened(by_path)), ["a", "b", "c", "d"])
        self.assertEqual(by_path["top.txt"].status, "analyzed")

        with patch.object(scan_cache, "MAX_WALK_FILES_BEYOND_CAP", 1):  # max_files 1 + 1 extra
            summary, by_path = self._scan_with_deadline(folder, recursive=True, max_files=1)
        self.assertEqual(sorted(unopened(by_path)), ["c", "d"])
        self.assertTrue(summary.capped)


@unittest.skipIf(__import__("os").name == "nt", "':' is not allowed in Windows file names")
class ArchiveMemberIdentityTests(unittest.TestCase):
    """P7 (round 8): "::" in a real path collided with archive member rows — a folder
    named "evil.zip::inner" gave two rows "evil.zip::inner/a1111.png", and a file named
    "fake.zip::member.png" read as a member of a zip that does not exist."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "case"
        (self.root / "evil.zip::inner").mkdir(parents=True)
        with zipfile.ZipFile(self.root / "evil.zip", "w") as archive:
            archive.writestr("inner/a1111.txt", "압축 안의 메모입니다.")
        (self.root / "evil.zip::inner" / "a1111.txt").write_text("실제 폴더의 메모입니다.", encoding="utf-8")
        (self.root / "fake.zip::member.txt").write_text("이름에 구분자가 있는 실제 파일입니다.", encoding="utf-8")
        (self.root / "odd.zip::x.xyz").write_bytes(b"?")

    def test_escape_is_reversible_and_never_reads_as_a_member(self) -> None:
        from deepfake_lens.result_text import escape_row_path, row_identity, unescape_row_path

        names = ["a.png", "evil.zip::inner/a.png", "a\\:\\:b", "back\\slash.png", "a\\:b", "x::y\\z", "::"]
        escaped = [escape_row_path(name) for name in names]
        self.assertEqual(len(set(escaped)), len(names), "escaping is injective")
        for name, shown in zip(names, escaped):
            with self.subTest(name=name):
                self.assertEqual(unescape_row_path(shown), name)
                self.assertNotIn("::", shown)
                self.assertEqual(row_identity({"path": shown}), (shown, None))
        self.assertEqual(escape_row_path("plain/back\\slash.png"), "plain/back\\slash.png", "unchanged without '::'")
        self.assertEqual(row_identity({"path": "a.zip::b.png", "container": "a.zip", "member": "b.png"}), ("a.zip", "b.png"))

    def test_escape_round_trips_every_colon_backslash_run(self) -> None:
        """R9-1 (round 9): "tri:::c.png" escaped to "tri\\:\\::c.png", which still held "::".
        Property: over every string of ":", "\\" and "a" up to length 6 (and random
        longer ones with "/" and "."), the escape has no "::", is injective, never
        reads as a member (row identity by fields) and unescapes to the original."""
        import itertools
        import random

        from deepfake_lens.result_text import escape_row_path, is_member_row, row_identity, unescape_row_path

        names = ["".join(chars) for length in range(7) for chars in itertools.product(":\\a", repeat=length)]
        rng = random.Random(9)
        names += ["".join(rng.choice(":\\a/.") for _ in range(rng.randint(7, 24))) for _ in range(3000)]
        names += ["tri:::c.png", ":::", "::::", "\\:", "\\\\::", "a\\::b", "x\\:\\:y.png"]
        seen: dict[str, str] = {}
        for name in names:
            shown = escape_row_path(name)
            self.assertNotIn("::", shown, name)
            self.assertEqual(unescape_row_path(shown), name, name)
            self.assertEqual(seen.setdefault(shown, name), name, f"escape collision: {name!r} / {seen[shown]!r}")
            self.assertEqual(row_identity({"path": shown}), (shown, None))
            self.assertFalse(is_member_row({"path": shown}))
            if "::" not in name and "\\:" not in name:
                self.assertEqual(shown, name, "a path with neither '::' nor '\\:' is unchanged")
        self.assertEqual(escape_row_path("tri:::c.png"), "tri\\:\\:\\:c.png")
        # a display path is never split back: without the fields a "::" path is one top-level row
        self.assertEqual(row_identity({"path": "a.zip::b.png"}), ("a.zip::b.png", None))

    def test_odd_colon_runs_are_their_own_rows(self) -> None:
        """R9-1: three or more colons ("tri:::c", "quad::::q") and "\\::" are real files —
        own rows, no member fields, never counted as archive members."""
        from deepfake_lens.core import scan_directory
        from deepfake_lens.result_text import unescape_row_path, unrecorded_files

        for name in ("tri:::c.txt", "quad::::q.txt", "x\\::y.txt", "tri:::odd.xyz"):
            (self.root / name).write_text(f"{name} 실제 파일입니다.", encoding="utf-8")
        summary, items = scan_directory(self.root, recursive=True)
        rows = {item.path: item for item in items}
        self.assertEqual(len(rows), len(items), "no duplicate row paths")
        for name in ("tri:::c.txt", "quad::::q.txt", "x\\::y.txt", "tri:::odd.xyz"):
            with self.subTest(name=name):
                row = next(item for item in items if unescape_row_path(item.path) == name and item.member is None)
                self.assertNotIn("::", row.path)
                self.assertEqual((row.container, row.member), (None, None))
                self.assertIn(row.status, ("analyzed", "unsupported"))
        self.assertEqual(rows["tri\\:\\:\\:c.txt"].status, "analyzed")
        # odd.zip::x.xyz and tri:::odd.xyz: both real unsupported files, counted as such
        self.assertEqual(unrecorded_files(list(items), summary).counts["unsupported"], 2)
        self.assertEqual(
            sorted(item.path for item in items if item.member is not None),
            ["evil.zip::inner/a1111.txt"],
        )

    def test_gui_unescape_matches_python(self) -> None:
        """R9-1: the GUI preview unescapes the row path with the inverse of
        result_text.escape_row_path and never takes a "::" for a member."""
        import itertools
        import json
        import shutil
        import subprocess

        from deepfake_lens.result_text import escape_row_path

        source = (Path(__file__).resolve().parents[1] / "gui.js").read_text(encoding="utf-8")
        start = source.index("function unescapeRowPath(path)")
        end = source.index("\n        }\n", start) + len("\n        }\n")
        preview = source[source.index("const hasPreview ="):source.index("const abs = hasPreview")]
        self.assertNotIn("'::'", preview, "the preview decision is by item.member, not a '::' in the path")
        self.assertIn("!item.member", preview)
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        names = ["".join(chars) for length in range(7) for chars in itertools.product(":\\a", repeat=length)]
        script = source[start:end] + "\nconst input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" \
            "process.stdout.write(JSON.stringify(input.map(unescapeRowPath)));\n"
        done = subprocess.run([node, "-e", script], input=json.dumps([escape_row_path(n) for n in names]),
                              capture_output=True, text=True, timeout=120, check=True)
        self.assertEqual(json.loads(done.stdout), names)

    def test_real_paths_and_members_have_distinct_rows(self) -> None:
        from deepfake_lens.core import scan_directory
        from deepfake_lens.result_text import unrecorded_files

        summary, items = scan_directory(self.root, recursive=True)
        rows = {item.path: item for item in items}
        self.assertEqual(len(rows), len(items), "no duplicate row paths")
        member = rows["evil.zip::inner/a1111.txt"]
        self.assertEqual((member.container, member.member), ("evil.zip", "inner/a1111.txt"))
        self.assertEqual(member.to_json()["container"], "evil.zip")
        real = rows["evil.zip\\:\\:inner/a1111.txt"]
        self.assertEqual((real.container, real.member), (None, None))
        self.assertNotIn("container", real.to_json())
        self.assertNotEqual(real.sha256, member.sha256)
        self.assertIn("fake.zip\\:\\:member.txt", rows)
        self.assertEqual(rows["fake.zip\\:\\:member.txt"].status, "analyzed")
        # a real unsupported file with "::" in its name is counted, never taken for a member
        self.assertEqual(unrecorded_files(list(items), summary).counts["unsupported"], 1)

    def test_cached_rescan_keeps_the_escaped_paths(self) -> None:
        from deepfake_lens.core import scan_directory

        cache = Path(self._tmp.name) / "cache.json"
        first = {item.path: item.to_json() for item in scan_directory(self.root, recursive=True, cache_path=cache)[1]}
        summary, items = scan_directory(self.root, recursive=True, cache_path=cache)
        self.assertGreater(summary.cached, 0)
        self.assertEqual({item.path: item.to_json() for item in items}, first)


if __name__ == "__main__":
    unittest.main()


class WebUploadArchiveRecordTests(unittest.TestCase):
    """D9 on the web upload path: an archive uploaded to the stdlib web server
    (``webapp.build_server``) records refused members, extraction errors and
    missing extractors on its container row exactly like the folder scan."""

    def setUp(self) -> None:
        import tempfile
        import threading
        from collections import OrderedDict

        from deepfake_lens import webapp, webapp_api

        roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict[Path, None]())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        server = webapp.build_server("127.0.0.1", 0, default_folder=self.root)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def _post(self, endpoint: str, field: str, filename: str, data: bytes) -> dict:
        import json
        import urllib.request

        from deepfake_lens.webapp import CLIENT_HEADER

        boundary = "----dflarchiveupload"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self.url + endpoint, data=body, method="POST",
            headers={CLIENT_HEADER: "test", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def _hostile_zip(self) -> tuple[Path, int, int]:
        arc = self.root / "evil.zip"
        declared, compressed = RejectedMemberRecordTests._bomb_zip(arc)
        with zipfile.ZipFile(arc, "a") as zf:
            zf.writestr("../x.png", b"x")
        return arc, declared, compressed

    @staticmethod
    def _container(payload: dict, name: str) -> dict:
        rows = [row for row in payload["items"] if row.get("kind") == "archive" and row.get("path") == name]
        assert len(rows) == 1, payload["items"]
        return rows[0]

    def test_rejected_members_surface_on_upload_container(self) -> None:
        import hashlib

        from deepfake_lens.archives import MAX_ARCHIVE_RATIO

        arc, declared, compressed = self._hostile_zip()
        data = arc.read_bytes()
        expected_bomb = f"zeros.bin: 압축 예산 초과(선언 크기 {declared}, 한도 {compressed * MAX_ARCHIVE_RATIO})"
        traversal = "../x.png: 경로 이탈 멤버('..' — 대상 폴더 밖 쓰기 시도)"
        # The folder scan of the same bytes is the reference surface.
        _, scanned = scan_directory(self.root)
        scanned_row = next(item for item in scanned if item.kind == "archive").to_json()
        for endpoint, field in (("/api/analyze-upload", "files"), ("/api/check", "file")):
            with self.subTest(endpoint=endpoint):
                payload = self._post(endpoint, field, "evil.zip", data)
                container = self._container(payload, "evil.zip")
                self.assertEqual(container["sha256"], hashlib.sha256(data).hexdigest())
                result = container["result"]
                self.assertEqual(result["verdict_code"], "undetermined")
                member_entries = [entry for entry in result["coverage"] if entry["check"] == "archive_member"]
                self.assertTrue(all(entry["status"] == "skipped" for entry in member_entries), member_entries)
                reasons = {entry["reason"] for entry in member_entries}
                self.assertTrue(any(reason.startswith(expected_bomb) for reason in reasons), reasons)
                self.assertIn(traversal, reasons)
                self.assertTrue(any("구성 파일 거부: zeros.bin — 압축 예산 초과(" in lim for lim in result["limitations"]), result["limitations"])
                self.assertIn("구성 파일 거부: ../x.png — 경로 이탈 멤버('..' — 대상 폴더 밖 쓰기 시도)", result["limitations"])
                self.assertEqual(result["coverage"][0], {"check": "archive", "status": "ran", "reason": ""})
                # Same container record as the CLI/folder scan (D9 parity).
                for key in ("coverage", "limitations", "verdict", "verdict_code", "band"):
                    self.assertEqual(result[key], scanned_row["result"][key], key)
                members = [row["path"] for row in payload["items"] if str(row.get("path", "")).startswith("evil.zip::")]
                self.assertEqual(members, ["evil.zip::ok.txt"])
                self.assertFalse(any("x.png" in str(row.get("path")) and "::" in str(row.get("path")) for row in payload["items"]))

    def test_unopenable_archive_is_failed_container(self) -> None:
        payload = self._post("/api/analyze-upload", "files", "broken.zip", b"PK\x03\x04 not really a zip archive")
        result = self._container(payload, "broken.zip")["result"]
        archive_entry = next(entry for entry in result["coverage"] if entry["check"] == "archive")
        self.assertEqual(archive_entry["status"], "failed")
        self.assertIn("BadZipFile", archive_entry["reason"])
        self.assertIn("압축 해제 실패", result["verdict"])
        self.assertEqual(result["verdict_code"], "undetermined")

    def test_missing_extractor_is_skipped_container(self) -> None:
        from deepfake_lens.archives import ArchiveExtraction

        missing = ArchiveExtraction(missing_dependency="py7zr")
        missing.warnings.append("7z 압축 해제에는 py7zr가 필요합니다.")
        with patch("deepfake_lens.archives.extract_archive", return_value=missing):
            payload = self._post("/api/analyze-upload", "files", "bundle.7z", b"7z\xbc\xaf\x27\x1c\x00\x04")
        result = self._container(payload, "bundle.7z")["result"]
        self.assertIn({"check": "archive", "status": "skipped", "reason": "의존성 부재: py7zr"}, result["coverage"])
        self.assertIn("의존성 부재: py7zr", result["verdict"])
        self.assertEqual(result["verdict_code"], "undetermined")


class ArchiveContainerCountTests(unittest.TestCase):
    """R5: archive container rows are counted by their verdict in the summary
    — never as "미지원/분석 실패" — so the header equals the CLI table and the
    GUI pills (gui.js verdictOf, the same rule as result_types.is_verdict_row)."""

    A1111 = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark" / "a1111-metadata-marker.png"

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        make_zip(self.folder / "case.zip", {"inner/a1111.png": self.A1111.read_bytes()})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_summary_counts_container_by_verdict(self) -> None:
        from deepfake_lens.result_types import Verdict, is_verdict_row

        summary, items = scan_directory(self.folder)
        by_path = {item.path: item for item in items}
        self.assertEqual(by_path["case.zip"].status, "expanded")
        self.assertEqual(by_path["case.zip"].result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertEqual(by_path["case.zip::inner/a1111.png"].result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.manipulation_evidence, 2)
        self.assertEqual(summary.undetermined, 0)
        self.assertEqual(summary.unsupported_or_failed, 0)
        # GUI pills: every row with a result outside failed/unsupported/
        # duplicate/skipped is counted by verdict_code; the rest is "other".
        pills: dict[str, int] = {}
        for row in (item.to_json() for item in items):
            result = row.get("result")
            key = (result or {}).get("verdict_code") if is_verdict_row(row.get("status"), isinstance(result, dict)) else "other"
            pills[str(key)] = pills.get(str(key), 0) + 1
        self.assertEqual(pills, {"manipulation_evidence": 2})
        self.assertEqual(summary.to_json()["manipulation_evidence"], pills["manipulation_evidence"])

    def test_cli_header_equals_table_rows(self) -> None:
        import contextlib
        import io
        import re

        from deepfake_lens.cli import main as cli_main

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["scan", str(self.folder)]), 0)
        text = out.getvalue()
        # N5: "(압축 파일 N건 포함)" says how many counted rows are containers.
        header = re.search(r"총 (\d+)건\(압축 파일 (\d+)건 포함\) — 조작·생성 근거 있음 (\d+)건, 원본성 근거 있음 (\d+)건, 판단 불가 (\d+)건.*미지원/분석 실패 (\d+)건", text)
        assert header is not None, text
        total, containers, manipulated, authentic, undetermined, failed = (int(value) for value in header.groups())
        self.assertEqual((total, containers, manipulated, authentic, undetermined, failed), (2, 1, 2, 0, 0, 0))
        table_rows = [line for line in text.splitlines() if line.startswith("조작·생성 근거 있음")]
        self.assertEqual(len(table_rows), manipulated)
        self.assertTrue(any(line.rstrip().split("  #")[0].endswith("case.zip") for line in table_rows), table_rows)


class ArchiveContainerRollupEvidenceTests(unittest.TestCase):
    """N5: a container row names what its verdict rests on (the member roll-up)."""

    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        a1111 = (Path(__file__).resolve().parents[2] / "fixtures" / "benchmark" / "a1111-metadata-marker.png").read_bytes()
        with zipfile.ZipFile(self.root / "case.zip", "w") as zf:
            zf.writestr("gen/a1111.png", a1111)
            zf.writestr("notes/memo.txt", "평범한 회의 메모입니다.")
        (self.root / "plain.txt").write_text("폴더의 다른 메모", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_container_row_carries_the_rollup_item_and_summary_counts_it(self) -> None:
        import contextlib

        from deepfake_lens.cli_render import _print_table
        from deepfake_lens.core import ARCHIVE_ROLLUP_TITLE
        from deepfake_lens.evidence_statement import build_evidence_statement
        from deepfake_lens.result_text import summary_line
        from deepfake_lens.result_types import EvidenceDirection, EvidenceKind, EvidenceStrength, Verdict

        summary, items = scan_directory(self.root)
        container = next(item for item in items if item.path == "case.zip")
        self.assertIsNotNone(container.result)
        assert container.result is not None
        self.assertEqual(container.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertEqual(len(container.result.evidence), 1)
        rollup = container.result.evidence[0]
        self.assertEqual(rollup.title, ARCHIVE_ROLLUP_TITLE)
        self.assertEqual(rollup.detail, "조작·생성 근거 있음 1건 / 판단 불가 1건")
        self.assertEqual(
            (rollup.kind, rollup.direction, rollup.strength, rollup.layer),
            (EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE, "archive"),
        )
        # R5 counting is unchanged; the header says how many rows are containers.
        self.assertEqual(summary.container_rows, 1)
        self.assertEqual(summary.manipulation_evidence, 2)  # the member and the container
        self.assertIn("총 4건(압축 파일 1건 포함)", summary_line(summary))
        self.assertEqual(summary.to_json()["container_rows"], 1)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table(summary, items, include_low=True)
        container_line = next(line for line in out.getvalue().splitlines() if line.rstrip().endswith("# [결정] " + ARCHIVE_ROLLUP_TITLE) or " case.zip  #" in line)
        self.assertIn(ARCHIVE_ROLLUP_TITLE, container_line)
        self.assertNotIn("근거 항목 없음", container_line)
        self.assertIn("(압축 파일 1건 포함)", out.getvalue())

        statement = build_evidence_statement(items, scan_root=self.root)
        entry = next(e for e in statement.entries if e.file_path == "case.zip")
        self.assertNotIn("근거 항목 없음", entry.purpose_of_proof)
        self.assertNotIn("결정적 근거(결정적 근거)", entry.purpose_of_proof)
        self.assertIn("압축 파일 구성원 중 결정적 근거에 의해 조작·생성 근거가 확인된 파일이 있는 증거물임을 소명함", entry.purpose_of_proof)
        self.assertIn("조작·생성 근거 있음 1건 / 판단 불가 1건", entry.purpose_of_proof)

    def test_rollup_detail_lists_authenticity_only_when_present(self) -> None:
        from deepfake_lens.core import archive_rollup_detail

        self.assertEqual(archive_rollup_detail(0, 3), "조작·생성 근거 있음 0건 / 판단 불가 3건")
        self.assertEqual(archive_rollup_detail(1, 0, 2), "조작·생성 근거 있음 1건 / 판단 불가 0건 / 원본성 근거 있음 2건")
