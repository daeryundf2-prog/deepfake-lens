import io
import tarfile
import zipfile
from pathlib import Path
import unittest

from deepfake_lens.archives import (
    archive_format,
    extract_archive,
    is_archive,
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
        scan_directory(self.tmp)
        import tempfile, os
        leftovers = [d for d in os.listdir(tempfile.gettempdir()) if d.startswith("dflens-arc-")]
        self.assertEqual(leftovers, [])

    def test_analyze_file_archive_container(self):
        zpath = self.tmp / "single.zip"
        make_zip(zpath, {"a.txt": b"some content"})
        item = analyze_file(zpath)
        self.assertEqual(item.kind, "archive")
        self.assertEqual(item.status, "analyzed")


if __name__ == "__main__":
    unittest.main()
