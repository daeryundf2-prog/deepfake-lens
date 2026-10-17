"""TB-6: document structure facts — PDF revisions, signature ranges, tool history; OOXML edit metadata."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from deepfake_lens.core import analyze_file
from deepfake_lens.document_structure import DOCUMENT_STRUCTURE_CHECK, ooxml_facts, pdf_evidence, pdf_facts
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength, Verdict

BASE = (
    b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
    b"3 0 obj << /Producer (Writer 1.0) /Creator (Hangul \\(HWP\\)) >> endobj\n"
    b"xref\n0 4\n0000000000 65535 f \ntrailer << /Size 4 /Root 1 0 R /Info 3 0 R >>\nstartxref\n120\n%%EOF\n"
)


def incremental(base: bytes, producer: bytes) -> bytes:
    update = (
        b"4 0 obj << /Producer <FEFF" + producer.decode("ascii").encode("utf-16-be").hex().upper().encode() + b"> >> endobj\n"
        b"xref\n4 1\n0000000300 00000 n \ntrailer << /Size 5 /Root 1 0 R /Info 4 0 R /Prev 120 >>\nstartxref\n400\n%%EOF\n"
    )
    return base + update


class PdfFactsTest(unittest.TestCase):
    def test_single_revision_has_no_item(self) -> None:
        facts = pdf_facts(BASE)
        self.assertEqual((facts.revisions, facts.xref_tables, facts.prev_links), (1, 1, 0))
        self.assertEqual(pdf_evidence(facts), [])
        self.assertEqual(facts.tools, [("Producer", "Writer 1.0"), ("Creator", "Hangul (HWP)")])

    def test_incremental_update_and_tool_history(self) -> None:
        facts = pdf_facts(incremental(BASE, b"Editor 9"))
        self.assertEqual((facts.revisions, facts.prev_links), (2, 1))
        titles = [item.title for item in pdf_evidence(facts)]
        self.assertIn("PDF 증분 저장 구조", titles)
        self.assertIn("PDF 작성 도구 기록 이력", titles)
        self.assertIn(("Producer", "Editor 9"), facts.tools)

    def test_save_after_signature(self) -> None:
        signed = BASE.replace(b"endobj\nxref", b"endobj\n5 0 obj << /Type /Sig /ByteRange [0 10 20 " + str(len(BASE) - 20).encode() + b"] >> endobj\nxref", 1)
        # Recompute so the range ends exactly at this file's end, then append an update.
        end = len(signed)
        signed = signed.replace(b"[0 10 20 " + str(len(BASE) - 20).encode() + b"]", b"[0 10 20 " + str(end - 20).encode() + b"]")
        signed = signed + b" " * (end - len(signed))
        self.assertEqual(pdf_facts(signed).signatures, [(end, 0)])
        later = incremental(signed, b"Editor 9")
        facts = pdf_facts(later)
        self.assertEqual(facts.signatures, [(end, len(later) - end)])
        [item] = [i for i in pdf_evidence(facts) if i.title == "PDF 전자서명 범위 뒤의 후속 저장"]
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))

    def test_linearized_first_page_section_is_not_a_revision(self) -> None:
        data = b"%PDF-1.7\n1 0 obj << /Linearized 1 >> endobj\nxref\ntrailer<<>>\nstartxref\n0\n%%EOF\n" + BASE[9:]
        self.assertEqual(pdf_facts(data).revisions, 1)

    def test_trailing_bytes_and_c2pa_embedding(self) -> None:
        data = BASE.replace(b"endobj\nxref", b"endobj\n6 0 obj << /Type /EmbeddedFile /Subtype /application#2Fc2pa >> endobj\nxref", 1) + b"GARBAGE"
        facts = pdf_facts(data)
        self.assertTrue(facts.c2pa_embedded)
        self.assertEqual(facts.trailing_after_eof, 7)
        titles = [i.title for i in pdf_evidence(facts)]
        self.assertIn("PDF 내장 C2PA 매니페스트 저장소 표기", titles)
        self.assertIn("PDF 끝 표식 뒤의 덧붙은 바이트", titles)


def docx(path: Path, *, rsids: int = 3, created: str = "2026-01-02T00:00:00Z", modified: str = "2026-03-04T00:00:00Z") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("docProps/core.xml", f"<cp:coreProperties><cp:revision>7</cp:revision><dcterms:created xsi:type=\"x\">{created}</dcterms:created><dcterms:modified>{modified}</dcterms:modified></cp:coreProperties>")
        zf.writestr("docProps/app.xml", "<Properties><Application>Microsoft Office Word</Application><AppVersion>16.0000</AppVersion><TotalTime>42</TotalTime></Properties>")
        zf.writestr("word/settings.xml", "<w:settings><w:rsids>" + "".join(f"<w:rsid w:val=\"00{i:06d}\"/>" for i in range(rsids)) + "</w:rsids></w:settings>")
        zf.writestr("word/document.xml", "<w:document><w:body><w:p><w:ins w:id=\"1\"><w:r><w:t>본문</w:t></w:r></w:ins><w:del w:id=\"2\"/></w:p></w:body></w:document>")
    return path


class OoxmlFactsTest(unittest.TestCase):
    def test_edit_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts = ooxml_facts(docx(Path(tmp) / "a.docx", rsids=5))
        self.assertEqual(facts.rsids, 5)
        self.assertEqual((facts.insertions, facts.deletions), (1, 1))
        self.assertIn(("총 편집 시간(분)", "42"), facts.fields)


class DocumentStructureInScanTest(unittest.TestCase):
    def test_docx_row_gets_the_check_and_stays_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = analyze_file(docx(Path(tmp) / "a.docx", created="2026-05-01T00:00:00Z", modified="2026-03-04T00:00:00Z")).result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == DOCUMENT_STRUCTURE_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn("w:rsid 3개", entry.reason)
        titles = [e.title for e in result.evidence]
        self.assertIn("문서 수정 메타데이터(자기 신고)", titles)
        self.assertIn("문서 생성 시각이 수정 시각보다 늦음", titles)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)

    def test_pdf_row_runs_without_pymupdf_and_hwp_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "a.pdf"
            pdf.write_bytes(incremental(BASE, b"Editor 9"))
            hwp = Path(tmp) / "a.hwp"
            hwp.write_bytes(b"\xd0\xcf\x11\xe0" + bytes(60))
            pdf_result, hwp_result = analyze_file(pdf).result, analyze_file(hwp).result
        assert pdf_result is not None and hwp_result is not None
        [entry] = [c for c in pdf_result.coverage if c.check == DOCUMENT_STRUCTURE_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn("개정 2개", entry.reason)
        [entry] = [c for c in hwp_result.coverage if c.check == DOCUMENT_STRUCTURE_CHECK]
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)

    def test_not_a_pdf_is_skipped_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "fake.pdf"
            pdf.write_bytes(b"hello")
            result = analyze_file(pdf).result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == DOCUMENT_STRUCTURE_CHECK]
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)


if __name__ == "__main__":
    unittest.main()
