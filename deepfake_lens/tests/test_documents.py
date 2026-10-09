"""Tests for the office-document extraction pipeline."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from deepfake_lens.documents import extract_document_text


def _docx(path: Path, body: str, creator: str = "", application: str = "") -> None:
    document = (
        '<?xml version="1.0"?><w:document xmlns:w="w">'
        f"<w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>"
    )
    core = (
        '<?xml version="1.0"?><cp:coreProperties xmlns:cp="cp" xmlns:dc="dc">'
        f"<dc:creator>{creator}</dc:creator></cp:coreProperties>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("docProps/core.xml", core)
        if application:
            zf.writestr(
                "docProps/app.xml",
                '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                f"<Application>{application}</Application></Properties>",
            )


class DocumentExtractionTest(unittest.TestCase):
    def test_docx_body_text_and_creator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "note.docx"
            _docx(doc, "인공지능은 빠르게 발전하고 있습니다", creator="Test Author")
            text, meta = extract_document_text(doc)
        self.assertIn("인공지능", text)
        self.assertEqual(meta.get("docx.creator"), "Test Author")
        self.assertEqual(meta.get("extractor"), "zip-xml")

    def test_docx_analyzed_by_scan_pipeline(self) -> None:
        from deepfake_lens.core import analyze_file

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "report.docx"
            _docx(doc, "Furthermore, it is crucial to recognize that these systems " * 8, creator="ChatGPT")
            item = analyze_file(doc)
        self.assertEqual(item.kind, "text")
        self.assertEqual(item.status, "analyzed")
        self.assertIsNotNone(item.result)
        # R3: a source guess from creator metadata is reference-only.
        self.assertEqual(item.result.source_guess.label, "참고: 문서 메타데이터에 AI 도구명 기록")
        self.assertIsNotNone(item.result.document_metadata)
        self.assertEqual(item.result.document_metadata.get("docx.creator"), "ChatGPT")

    def test_application_metadata_source_guess_is_reference_only(self) -> None:
        """R3: docProps/app.xml Application "ChatGPT" gives a "참고: " source
        guess with confidence unknown, and the "no clue" default reason is
        gone once the metadata clue is listed."""
        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import NO_SOURCE_CLUE_REASON, Grade, SourceConfidence, Verdict

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "memo.docx"
            _docx(doc, "회의 결과를 정리한 메모입니다. 다음 주까지 초안을 검토합니다. " * 6, application="ChatGPT")
            item = analyze_file(doc)
        self.assertIsNotNone(item.result)
        assert item.result is not None
        guess = item.result.source_guess
        self.assertTrue(guess.label.startswith("참고: "), guess.label)
        self.assertEqual(guess.confidence, SourceConfidence.UNKNOWN)
        self.assertNotIn(NO_SOURCE_CLUE_REASON, guess.reasons)
        self.assertIn("작성 애플리케이션: 「ChatGPT」", guess.reasons)
        self.assertEqual(item.result.to_json()["source_guess"]["confidence"], "unknown")
        self.assertEqual(item.result.grade, Grade.REFERENCE)
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)

    def test_non_ai_application_is_reference_clue(self) -> None:
        """R3: a non-AI writing tool is a reference clue too — never a confident guess."""
        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import NO_SOURCE_CLUE_REASON, SourceConfidence

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "memo.docx"
            _docx(doc, "회의 결과를 정리한 메모입니다. " * 6, application="Microsoft Office Word")
            item = analyze_file(doc)
        assert item.result is not None
        guess = item.result.source_guess
        self.assertTrue(guess.label.startswith("참고: "), guess.label)
        self.assertEqual(guess.confidence, SourceConfidence.UNKNOWN)
        self.assertNotIn(NO_SOURCE_CLUE_REASON, guess.reasons)

    def test_corrupt_docx_degrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "broken.docx"
            doc.write_bytes(b"not a zip")
            text, meta = extract_document_text(doc)
        self.assertEqual(text, "")
        self.assertTrue(meta["extractor"].startswith("failed"))

    def test_extraction_failure_keeps_the_exception_class(self) -> None:
        """D16: a failed extractor records its exception class, and the scan's
        document_text coverage entry carries it (pymupdf path: documents.py
        used to drop the class)."""
        import sys
        import types
        from unittest import mock

        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import CoverageStatus

        class _BrokenFitz(types.ModuleType):
            @staticmethod
            def open(path: object) -> object:
                raise RuntimeError("cannot open broken document: xref table damaged")

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "broken.pdf"
            pdf.write_bytes(b"%PDF-1.7\n%broken\n")
            # N4: pdf_backend imports ``pymupdf`` first, then legacy ``fitz``.
            broken = _BrokenFitz("fitz")
            with mock.patch.dict(sys.modules, {"pymupdf": broken, "fitz": broken}):
                text, meta = extract_document_text(pdf)
                with self.assertLogs("deepfake_lens.documents", level="WARNING"):
                    item = analyze_file(pdf)
            docx = Path(tmp) / "broken.docx"
            docx.write_bytes(b"not a zip")
            _, docx_meta = extract_document_text(docx)
        self.assertEqual(text, "")
        self.assertEqual(meta["extractor"], "failed:pymupdf:RuntimeError")
        # B6: the untranslated English message is logged, not shown.
        self.assertEqual(meta["extractor_error"], "RuntimeError: 라이브러리 오류(RuntimeError) — 상세는 로그 참조")
        self.assertEqual(docx_meta["extractor"], "failed:zip:BadZipFile")
        assert item.result is not None
        [entry] = [c for c in item.result.coverage if c.check == "document_text"]
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertIn("RuntimeError: 라이브러리 오류(RuntimeError)", entry.reason)  # B6
        self.assertNotIn("extractor_error", item.result.document_metadata or {})

    def test_legacy_doc_marks_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "old.doc"
            doc.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
            text, meta = extract_document_text(doc)
        self.assertEqual(text, "")
        self.assertEqual(meta["extractor"], "unavailable:ole-legacy")

    def test_legacy_doc_scans_with_limitation(self) -> None:
        from deepfake_lens.core import analyze_file

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "old.doc"
            doc.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
            item = analyze_file(doc)
        self.assertEqual(item.kind, "text")
        self.assertEqual(item.status, "analyzed")
        self.assertTrue(any("추출 불가" in lim for lim in item.result.limitations))


if __name__ == "__main__":
    unittest.main()


class HwpExtractionTest(unittest.TestCase):
    """HWP via optional syhwp reader."""

    def test_invalid_hwp_degrades_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "bad.hwp"
            doc.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
            text, meta = extract_document_text(doc)
        self.assertEqual(text, "")
        # Either syhwp is absent (unavailable) or it failed on invalid bytes.
        self.assertTrue(meta["extractor"].startswith(("unavailable", "failed")))

    def test_real_hwp_extracts_when_fixture_present(self) -> None:
        import os

        fixture = os.environ.get("DEEPFAKE_LENS_TEST_HWP")
        if not fixture or not Path(fixture).exists():
            self.skipTest("set DEEPFAKE_LENS_TEST_HWP to a real .hwp file")
        try:
            import syhwp  # noqa: F401
        except ImportError:
            self.skipTest("syhwp not installed")
        text, meta = extract_document_text(Path(fixture))
        self.assertEqual(meta["extractor"], "syhwp")
        self.assertGreater(len(text), 50)


class DocumentTextFeedsModelMembersTest(unittest.TestCase):
    """Text model members must receive extracted text, not container bytes."""

    def test_docx_passes_extracted_text_to_members(self) -> None:
        from unittest.mock import patch
        from deepfake_lens.core import analyze_file

        seen: list[Path] = []

        def spy(path, model_path=None, *, modality=None, model_name=None):
            seen.append(Path(path).read_bytes()[:64])
            return None

        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "note.docx"
            body = "어제 산책을 하다가 오래된 친구를 만났다. " * 12
            _docx(doc, body)
            with patch("deepfake_lens.core.analyze_external_model", side_effect=spy):
                item = analyze_file(doc)
        self.assertEqual(item.status, "analyzed")
        self.assertTrue(seen, "member path never invoked")
        fed = seen[0].decode("utf-8", errors="replace")
        self.assertIn("산책", fed)  # extracted text, not PK zip bytes
        self.assertNotIn("PK", fed[:2])
