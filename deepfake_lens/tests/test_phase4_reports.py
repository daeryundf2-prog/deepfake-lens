"""Tests for Phase 4 honesty fixes: measured evidence integrity, checksum
(not "digital signature") naming, PDF Latin-1 notice, EER metric."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deepfake_lens.calibration import eer
from deepfake_lens.evidence import create_evidence_chain
from deepfake_lens.enhanced_forensics import analyze_forensic
from deepfake_lens.reports import write_pdf_report


class EvidenceIntegrityTest(unittest.TestCase):
    """integrity_verified must be measured by re-hashing, not hardcoded."""

    def test_chain_for_existing_file_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_bytes(b"evidence payload")
            chain = create_evidence_chain(path, results={"score": 10})
            self.assertTrue(chain.integrity_verified)

    def test_chain_for_missing_file_reports_unverified(self) -> None:
        chain = create_evidence_chain("/nonexistent/evidence.bin", results={})
        self.assertFalse(chain.integrity_verified)


class ChecksumNamingTest(unittest.TestCase):
    """The unkeyed SHA-256 is an integrity checksum, not a signature."""

    def test_legal_text_names_checksum_not_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("forensic sample", encoding="utf-8")
            report = analyze_forensic(path)
            text = report.generate_legal_text()
            self.assertIn("무결성 체크섬", text)
            self.assertIn("전자서명 아님", text)
            self.assertNotIn("디지털 서명", text)

    def test_report_json_uses_checksum_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("forensic sample", encoding="utf-8")
            report = analyze_forensic(path)
            data = report.to_json()
            self.assertIn("integrity_checksum", data)
            self.assertNotIn("digital_signature", data)
            self.assertTrue(report.integrity_checksum)


class PdfLatin1NoticeTest(unittest.TestCase):
    def _summary(self):
        from deepfake_lens.core import BatchScanSummary

        return BatchScanSummary(
            total=1, analyzed=1, high=0, medium=1, unknown=0, low=0,
            unsupported_or_failed=0, capped=False,
        )

    def _item(self, path: Path):
        from deepfake_lens.core import ScanItem, analyze_text

        result = analyze_text("as an AI language model I conclude")
        return ScanItem(path=str(path), name=path.name, kind="text", status="analyzed", size_bytes=10, result=result)

    # B8: the --pdf-out report is the Korean forensic renderer; the old
    # Latin-1 writer (and its "NOTE: this simple PDF is Latin-1 only"
    # notice) is gone from this path. Without pymupdf it refuses in Korean.
    # Names kept from the baseline (QA-SYS-10 inventory); the bodies now pin
    # B8: Korean text instead of the old Latin-1 notice.
    def test_korean_content_gets_latin1_notice(self) -> None:
        from deepfake_lens.evidence_statement import PDF_REPORT_DEPENDENCY_MESSAGE, PdfDependencyMissing
        from deepfake_lens.pdf_backend import import_pymupdf, pymupdf_available
        from deepfake_lens.result_text import TEXT_LEGAL_LIMITATION

        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.txt"
            sample.write_text("hello", encoding="utf-8")
            out = Path(tmp) / "report.pdf"
            if not pymupdf_available():
                with self.assertRaises(PdfDependencyMissing) as ctx:
                    write_pdf_report(out, self._summary(), [self._item(sample)])
                self.assertEqual(str(ctx.exception), PDF_REPORT_DEPENDENCY_MESSAGE)
                self.assertFalse(out.exists())
                return
            write_pdf_report(out, self._summary(), [self._item(sample)])
            pymupdf = import_pymupdf()
            with pymupdf.open(str(out)) as doc:
                text = "".join(page.get_text() for page in doc)
            self.assertIn("판단 불가", text)
            self.assertIn(TEXT_LEGAL_LIMITATION[:20], text)
            self.assertNotIn("Latin-1", text)
            self.assertNotIn("UNDETERMINED", text)

    def test_ascii_only_content_has_no_notice(self) -> None:
        """B8: no PDF path of --pdf-out prints the Latin-1 notice any more."""
        from deepfake_lens.evidence_statement import PdfDependencyMissing
        from deepfake_lens.pdf_backend import pymupdf_available

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.pdf"
            if not pymupdf_available():
                with self.assertRaises(PdfDependencyMissing):
                    write_pdf_report(out, self._summary(), [])
                return
            write_pdf_report(out, self._summary(), [])
            self.assertNotIn(b"NOTE: this simple PDF", out.read_bytes())


class HtmlRowHashTest(unittest.TestCase):
    """Y10 (round 7): the HTML report had no per-row hash (the PDFs did) —
    every row now shows its full SHA-256, or the PDFs' "해시 불가" reason."""

    def test_every_html_row_carries_its_sha256(self) -> None:
        import hashlib
        import os
        import re
        import zipfile

        from deepfake_lens.core import scan_directory
        from deepfake_lens.reports import write_html_report
        from deepfake_lens.result_text import HASH_UNAVAILABLE_SYMLINK

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "case"
            folder.mkdir()
            (folder / "memo.txt").write_text("사건 메모입니다.", encoding="utf-8")
            (folder / "other.md").write_text("다른 메모입니다.", encoding="utf-8")
            with zipfile.ZipFile(folder / "bundle.zip", "w") as archive:
                archive.writestr("inner/note.txt", "압축 안의 메모입니다.")
            links = 0
            try:
                os.symlink("memo.txt", folder / "link.txt")
                links = 1
            except (OSError, NotImplementedError):
                pass
            summary, items = scan_directory(folder)
            out = Path(tmp) / "report.html"
            write_html_report(out, summary, items)
            html = out.read_text(encoding="utf-8")
        self.assertIn("<th>SHA-256</th>", html)
        body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        rows = re.findall(r"<tr>(.*?)</tr>", body, flags=re.S)
        self.assertEqual(len(rows), len(items))
        cells = [re.search(r'<td class="sha256">(.*?)</td>', row, flags=re.S) for row in rows]
        self.assertTrue(all(cells), rows)
        by_path = {item.path: item for item in items}
        self.assertEqual(by_path["memo.txt"].sha256, hashlib.sha256("사건 메모입니다.".encode("utf-8")).hexdigest())
        for item in items:
            row = next(row for row in rows if f"<td>{item.path}</td>" in row)
            cell = re.search(r'<td class="sha256">(.*?)</td>', row, flags=re.S)
            assert cell is not None
            if item.sha256:
                self.assertEqual(cell.group(1), item.sha256, item.path)
            else:
                self.assertTrue(cell.group(1).startswith("해시 불가"), (item.path, cell.group(1)))
        self.assertTrue(by_path["bundle.zip::inner/note.txt"].sha256)
        if links:
            self.assertIn(f'<td class="sha256">{HASH_UNAVAILABLE_SYMLINK}</td>', html)


class EerTest(unittest.TestCase):
    def test_separable_scores_have_near_zero_eer(self) -> None:
        scores = [(5, False), (95, True)] * 10
        value = eer(scores)
        self.assertIsNotNone(value)
        assert value is not None
        self.assertLessEqual(value, 0.05)

    def test_fully_overlapping_scores_have_high_eer(self) -> None:
        scores = [(50, False), (50, True)] * 10
        value = eer(scores)
        self.assertIsNotNone(value)
        assert value is not None
        self.assertGreaterEqual(value, 0.4)
        self.assertLessEqual(value, 0.6)

    def test_single_class_returns_none(self) -> None:
        self.assertIsNone(eer([(50, True), (60, True)]))
        self.assertIsNone(eer([]))


if __name__ == "__main__":
    unittest.main()
