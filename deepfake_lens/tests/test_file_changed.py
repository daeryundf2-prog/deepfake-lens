"""R12-7 (round 12): a file rewritten while it is analyzed is 판단 불가 with no hash.

Before R12-7 the row's SHA-256 was read after the analysis, so a file
rewritten mid-scan was recorded with the hash of other bytes than the ones
its verdict came from (an A1111 verdict 조작·생성 근거 있음 with the hash of
an empty file). The scanner now takes the file's state (size, mtime_ns,
inode, device) before and after the analysis and its hash after it (and
before it, up to REHASH_MAX_BYTES); any difference makes the row 판단 불가
with a failed "file_integrity" coverage entry and no hash. The rewrite is
simulated by wrapping ``core.analyze_file``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from deepfake_lens import core
from deepfake_lens.core import FILE_CHANGED_REASON, FILE_INTEGRITY_CHECK, scan_directory
from deepfake_lens.result_types import CoverageStatus, Verdict

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark"
A1111 = (FIXTURES / "a1111-metadata-marker.png").read_bytes()
TEXTURE = (FIXTURES / "real-like-texture.png").read_bytes()


class FileChangedDuringAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = self.tmp / "case"
        self.folder.mkdir()
        (self.folder / "target.png").write_bytes(A1111)
        (self.folder / "other.png").write_bytes(A1111)

    def _scan_rewriting(self, rewrite: Any) -> dict[str, Any]:
        real = core.analyze_file

        def analyze_then_rewrite(path: Any, **kwargs: Any) -> Any:
            item = real(path, **kwargs)
            if Path(path).name == "target.png":
                rewrite(Path(path))
            return item

        with patch.object(core, "analyze_file", analyze_then_rewrite):
            _, items = scan_directory(self.folder)
        return {item.path: item for item in items}

    def _assert_flagged(self, rows: dict[str, Any]) -> None:
        target = rows["target.png"]
        self.assertEqual(target.status, "analyzed")
        self.assertIsNone(target.sha256)
        result = target.result
        assert result is not None
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)  # the A1111 evidence no longer decides
        self.assertIn(FILE_CHANGED_REASON, result.verdict)
        entry = next(entry for entry in result.coverage if entry.check == FILE_INTEGRITY_CHECK)
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.FAILED, FILE_CHANGED_REASON))
        self.assertTrue(result.limitations[0].startswith("검사 실패 — "))
        self.assertEqual((result.score, result.probability), (0, None))
        payload = target.to_json()
        self.assertIsNone(payload["sha256"])
        self.assertEqual(payload["result"]["verdict_code"], "undetermined")
        # The other row is untouched.
        other = rows["other.png"]
        assert other.result is not None
        self.assertEqual(other.sha256, hashlib.sha256(A1111).hexdigest())
        self.assertEqual(other.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertFalse(any(entry.check == FILE_INTEGRITY_CHECK for entry in other.result.coverage))

    def test_rewrite_with_other_content(self) -> None:
        self._assert_flagged(self._scan_rewriting(lambda path: path.write_bytes(TEXTURE)))

    def test_truncation_mid_analysis(self) -> None:
        self._assert_flagged(self._scan_rewriting(lambda path: path.write_bytes(b"")))

    def test_same_size_rewrite_with_restored_mtime_is_caught_by_the_hash(self) -> None:
        def rewrite(path: Path) -> None:
            stat = os.stat(path)
            data = bytearray(path.read_bytes())
            data[-1] ^= 0xFF
            with open(path, "r+b") as handle:  # same inode, same size
                handle.write(bytes(data))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        self._assert_flagged(self._scan_rewriting(rewrite))

    def test_large_file_is_compared_by_state(self) -> None:
        with patch.object(core, "REHASH_MAX_BYTES", 0):  # no pre-analysis hash
            self._assert_flagged(self._scan_rewriting(lambda path: path.write_bytes(A1111 + b"x")))

    def test_replaced_file_is_caught_by_inode(self) -> None:
        def replace_file(path: Path) -> None:
            stat = os.stat(path)
            spare = path.with_name("spare.tmp")
            spare.write_bytes(A1111)  # same bytes, same size
            os.utime(spare, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            os.replace(spare, path)

        with patch.object(core, "REHASH_MAX_BYTES", 0):
            self._assert_flagged(self._scan_rewriting(replace_file))

    def test_unchanged_files_keep_their_hash_and_verdict(self) -> None:
        rows = self._scan_rewriting(lambda path: None)
        for row in rows.values():
            assert row.result is not None
            self.assertEqual(row.sha256, hashlib.sha256(A1111).hexdigest())
            self.assertEqual(row.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
            self.assertFalse(any(entry.check == FILE_INTEGRITY_CHECK for entry in row.result.coverage))


if __name__ == "__main__":
    unittest.main()
