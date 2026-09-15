"""Tests for the office-document extraction pipeline."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from deepfake_lens.documents import extract_document_text


def _docx(path: Path, body: str, creator: str = "") -> None:
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
        self.assertEqual(item.result.source_guess.label, "AI 도구 생성 메타데이터 추정")
        self.assertIsNotNone(item.result.document_metadata)
        self.assertEqual(item.result.document_metadata.get("docx.creator"), "ChatGPT")

    def test_corrupt_docx_degrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "broken.docx"
            doc.write_bytes(b"not a zip")
            text, meta = extract_document_text(doc)
        self.assertEqual(text, "")
        self.assertTrue(meta["extractor"].startswith("failed"))

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
