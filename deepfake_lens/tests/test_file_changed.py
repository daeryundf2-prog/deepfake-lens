"""R12-7 (round 12): a file rewritten while it is analyzed is 판단 불가 with no hash.

Before R12-7 the row's SHA-256 was read after the analysis, so a file
rewritten mid-scan was recorded with the hash of other bytes than the ones
its verdict came from (an A1111 verdict 조작·생성 근거 있음 with the hash of
an empty file). The scanner now takes the file's state (size, mtime_ns,
inode, device) before and after the analysis and its hash after it (and
before it, up to REHASH_MAX_BYTES); any difference makes the row 판단 불가
with a failed "file_integrity" coverage entry and no hash. The rewrite is
simulated by wrapping ``core.analyze_file``.

R13-2 (round 13): the same for an archive rewritten while its members are
extracted (simulated by wrapping ``core.extract_archive``): the container
row had the new archive's hash and its member rows came from the old one.
The container and every member row are now 판단 불가 with no hash.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from deepfake_lens import core
from deepfake_lens.core import ARCHIVE_MEMBER_CHANGED_REASON, FILE_CHANGED_REASON, FILE_INTEGRITY_CHECK, scan_directory
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

    @unittest.skipIf(os.name == "nt", "ctime is the creation time on Windows")
    def test_large_file_same_size_rewrite_with_restored_mtime_is_caught_by_ctime(self) -> None:
        """R14-6 (round 14): above REHASH_MAX_BYTES (no pre-analysis hash) the state's ctime_ns catches ``touch -r``."""

        def rewrite(path: Path) -> None:
            stat = os.stat(path)
            data = bytearray(path.read_bytes())
            data[-1] ^= 0xFF
            with open(path, "r+b") as handle:  # same inode, same size
                handle.write(bytes(data))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        with patch.object(core, "REHASH_MAX_BYTES", 0):  # no pre-analysis hash
            self._assert_flagged(self._scan_rewriting(rewrite))

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



def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# ZA: the archive the members are extracted from (an A1111 image — strong
# deterministic evidence); ZB: what it is rewritten to (no evidence).
ZA = _zip({"m.png": A1111, "n.png": A1111})
ZB = _zip({"m.png": TEXTURE, "n.png": TEXTURE})


class ArchiveChangedDuringExtractionTest(unittest.TestCase):
    """R13-2: an archive rewritten mid-extraction — container and members 판단 불가, no hash."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = self.tmp / "case"
        self.folder.mkdir()
        (self.folder / "hot.zip").write_bytes(ZA)
        (self.folder / "cold.zip").write_bytes(ZA)

    def _scan_rewriting(self, rewrite: Any) -> dict[str, Any]:
        real = core.extract_archive

        def extract_then_rewrite(path: Any, dest: Any, *args: Any, **kwargs: Any) -> Any:
            outcome = real(path, dest, *args, **kwargs)
            if Path(path).name == "hot.zip":
                rewrite(Path(path))
            return outcome

        with patch.object(core, "extract_archive", extract_then_rewrite):
            _, items = scan_directory(self.folder)
        return {item.path: item for item in items}

    def _assert_flagged(self, rows: dict[str, Any]) -> None:
        container = rows["hot.zip"]
        self.assertIsNone(container.sha256)
        result = container.result
        assert result is not None
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertIn(FILE_CHANGED_REASON, result.verdict)
        entry = next(entry for entry in result.coverage if entry.check == FILE_INTEGRITY_CHECK)
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.FAILED, FILE_CHANGED_REASON))
        members = [row for path, row in rows.items() if path.startswith("hot.zip::")]
        self.assertEqual(sorted(row.member for row in members), ["m.png", "n.png"])
        self.assertIn("분석 중 파일 변경", ARCHIVE_MEMBER_CHANGED_REASON)
        for member in members:
            with self.subTest(member=member.member):
                self.assertIsNone(member.sha256)
                assert member.result is not None
                # The A1111 evidence of ZA's members no longer decides.
                self.assertEqual(member.result.verdict_code, Verdict.UNDETERMINED)
                self.assertIn(ARCHIVE_MEMBER_CHANGED_REASON, member.result.verdict)
                flagged = next(entry for entry in member.result.coverage if entry.check == FILE_INTEGRITY_CHECK)
                self.assertEqual((flagged.status, flagged.reason), (CoverageStatus.FAILED, ARCHIVE_MEMBER_CHANGED_REASON))
                self.assertIsNone(member.to_json()["sha256"])
        # The roll-up counts the members as 판단 불가.
        self.assertEqual(result.evidence[0].detail, core.archive_rollup_detail(0, 2))
        # The untouched archive keeps its hash, verdict and members.
        cold = rows["cold.zip"]
        assert cold.result is not None
        self.assertEqual(cold.sha256, hashlib.sha256(ZA).hexdigest())
        self.assertEqual(cold.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        for path, row in rows.items():
            if path.startswith("cold.zip::"):
                self.assertEqual(row.sha256, hashlib.sha256(A1111).hexdigest())
                self.assertEqual(row.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
                self.assertFalse(any(entry.check == FILE_INTEGRITY_CHECK for entry in row.result.coverage))

    def test_rewrite_with_another_archive(self) -> None:
        self._assert_flagged(self._scan_rewriting(lambda path: path.write_bytes(ZB)))

    def test_same_size_rewrite_with_restored_mtime_is_caught_by_the_hash(self) -> None:
        def rewrite(path: Path) -> None:
            stat = os.stat(path)
            data = bytearray(path.read_bytes())
            data[-1] ^= 0xFF  # the zip comment-length byte: same size, same inode
            with open(path, "r+b") as handle:
                handle.write(bytes(data))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        self._assert_flagged(self._scan_rewriting(rewrite))

    def test_replaced_archive_is_caught_by_inode(self) -> None:
        def replace_file(path: Path) -> None:
            stat = os.stat(path)
            spare = path.with_name("spare.tmp")
            spare.write_bytes(ZA)  # same bytes, size and mtime — another inode
            os.utime(spare, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            os.replace(spare, path)

        # R14-6: an archive is hashed before extraction at any size now (the
        # patch no longer turns that off); the bytes are the same, the inode decides.
        with patch.object(core, "REHASH_MAX_BYTES", 0):
            self._assert_flagged(self._scan_rewriting(replace_file))

    def test_large_archive_same_size_rewrite_with_restored_mtime_is_caught(self) -> None:
        """R14-6 (round 14): an archive over REHASH_MAX_BYTES rewritten in place, same size, ``touch -r``.

        It was compared by (size, mtime, inode) only and kept the hash of the
        new bytes with members from the old ones. Now it is hashed before
        extraction at any size and its state carries ctime_ns: either catches
        it — the hash alone where the OS has no change time (Windows).
        """

        def rewrite(path: Path) -> None:
            stat = os.stat(path)
            data = bytearray(path.read_bytes())
            data[-1] ^= 0xFF  # the zip comment-length byte: same size, same inode
            with open(path, "r+b") as handle:
                handle.write(bytes(data))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

        for has_ctime in (True, False):
            with self.subTest(state_has_ctime=has_ctime):
                (self.folder / "hot.zip").write_bytes(ZA)
                with patch.object(core, "REHASH_MAX_BYTES", 0), patch.object(core, "STATE_HAS_CTIME", has_ctime and os.name != "nt"):
                    self._assert_flagged(self._scan_rewriting(rewrite))

    def test_unreadable_archive_rewritten_is_flagged_too(self) -> None:
        (self.folder / "hot.zip").write_bytes(b"PK\x03\x04 not really a zip")
        rows = self._scan_rewriting(lambda path: path.write_bytes(ZB))
        container = rows["hot.zip"]
        self.assertIsNone(container.sha256)
        assert container.result is not None
        self.assertEqual(container.result.verdict_code, Verdict.UNDETERMINED)
        self.assertTrue(any(entry.check == FILE_INTEGRITY_CHECK and entry.status == CoverageStatus.FAILED for entry in container.result.coverage))

    def test_unchanged_archives_keep_their_hash(self) -> None:
        rows = self._scan_rewriting(lambda path: None)
        for name in ("hot.zip", "cold.zip"):
            row = rows[name]
            assert row.result is not None
            self.assertEqual(row.sha256, hashlib.sha256(ZA).hexdigest())
            self.assertEqual(row.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
            self.assertFalse(any(entry.check == FILE_INTEGRITY_CHECK for entry in row.result.coverage))


if __name__ == "__main__":
    unittest.main()
