"""R18-10 (round 18): --allow-symlinks never analyzes a kernel pseudo file (/proc, /sys).

A link to ``/proc/self/environ`` was followed and the scanner's own
environment analyzed — DEEPFAKE_LENS_REPORT_KEY may be in it, and its hash
was recorded; the row said ``size_bytes 0`` next to the sha256 of content.
Such a file is now a skipped row "건너뜀: 특수 파일 시스템 …".
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from deepfake_lens import scan_cache
from deepfake_lens.core import scan_directory

# Read from the module so the scan test reports the old behaviour on a tree without R18-10 (not an import error).
SPECIAL_FILESYSTEM_REASON = getattr(scan_cache, "SPECIAL_FILESYSTEM_REASON", "건너뜀: 특수 파일 시스템")


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/self/environ").exists(), "Linux /proc needed")
class ProcfsLinkTest(unittest.TestCase):
    def test_links_into_proc_are_skipped_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.symlink("/proc/self/environ", root / "environ.txt")
            os.symlink("/proc/self/status", root / "status.txt")
            (root / "empty.txt").write_bytes(b"")
            (root / "memo.txt").write_text("메모", encoding="utf-8")
            summary, items = scan_directory(root, allow_symlinks=True)
        rows = {item.name: item for item in items}
        for name in ("environ.txt", "status.txt"):
            with self.subTest(row=name):
                self.assertEqual(rows[name].status, "skipped")
                self.assertEqual(rows[name].error, SPECIAL_FILESYSTEM_REASON)
                self.assertIsNone(rows[name].sha256)
                self.assertIsNone(rows[name].result)
        # A real empty file and a regular file are analyzed as before.
        self.assertEqual(rows["empty.txt"].status, "analyzed")
        self.assertEqual(rows["memo.txt"].status, "analyzed")
        self.assertEqual(summary.total, 4)

    def test_the_probe(self) -> None:
        special_filesystem_file = scan_cache.special_filesystem_file
        self.assertTrue(special_filesystem_file(Path("/proc/self/environ")))
        self.assertTrue(special_filesystem_file(Path("/proc/self/status")))
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp, "empty")
            empty.write_bytes(b"")
            self.assertFalse(special_filesystem_file(empty))
            self.assertFalse(special_filesystem_file(Path(tmp, "missing")))


if __name__ == "__main__":
    unittest.main()
