from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from deepfake_lens import documents
from deepfake_lens.reviews import ReviewStore


def _zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return path


class OoxmlExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_xlsx_reads_shared_strings_then_sheets(self) -> None:
        path = _zip(
            self.root / "book.xlsx",
            {
                "xl/sharedStrings.xml": "<sst><si><t>alpha</t></si></sst>",
                "xl/worksheets/sheet1.xml": "<worksheet><c><v>42</v></c></worksheet>",
                "xl/other.xml": "<x>ignored</x>",
            },
        )
        text, meta = documents.extract_document_text(path)
        self.assertEqual(meta["extractor"], "zip-xml")
        self.assertIn("alpha", text)
        self.assertIn("42", text)
        self.assertNotIn("ignored", text)

    def test_pptx_slides_are_ordered_numerically(self) -> None:
        members = {
            f"ppt/slides/slide{n}.xml": f"<p><a:p><a:t>slide {n}</a:t></a:p></p>" for n in (10, 2, 1)
        }
        text, meta = documents.extract_document_text(_zip(self.root / "deck.pptx", members))
        self.assertEqual(meta["extractor"], "zip-xml")
        self.assertEqual([line for line in text.splitlines() if line.strip()], ["slide 1", "slide 2", "slide 10"])

    def test_bad_zip_degrades_to_failure_note(self) -> None:
        for name in ("broken.xlsx", "broken.pptx", "broken.docx"):
            bad = self.root / name
            bad.write_bytes(b"this is not a zip archive")
            with self.subTest(name=name):
                text, meta = documents.extract_document_text(bad)
                self.assertEqual(text, "")
                self.assertEqual(meta["extractor"], "failed:zip")

    def test_docx_core_and_app_properties_are_recorded(self) -> None:
        core = (
            "<cp:coreProperties>"
            "<dc:creator>Kim</dc:creator>"
            "<cp:lastModifiedBy>  Lee </cp:lastModifiedBy>"
            "<cp:revision>7</cp:revision>"
            "<dcterms:created>2026-01-02T03:04:05Z</dcterms:created>"
            "<dcterms:modified> </dcterms:modified>"
            "</cp:coreProperties>"
        )
        app = "<Properties><Application>Microsoft Word</Application></Properties>"
        path = _zip(
            self.root / "doc.docx",
            {
                "docProps/core.xml": core,
                "docProps/app.xml": app,
                "word/document.xml": "<w:document><w:p ><w:t>first</w:t></w:p><w:p><w:t>second</w:t></w:p></w:document>",
            },
        )
        text, meta = documents.extract_document_text(path)
        self.assertEqual(meta["docx.creator"], "Kim")
        self.assertEqual(meta["docx.last_modified_by"], "Lee")
        self.assertEqual(meta["docx.revision"], "7")
        self.assertEqual(meta["docx.created"], "2026-01-02T03:04:05Z")
        self.assertNotIn("docx.modified", meta)
        self.assertEqual(meta["docx.application"], "Microsoft Word")
        self.assertEqual(meta["extractor"], "zip-xml")
        self.assertEqual(text.split(), ["first", "second"])

    def test_docx_without_document_xml_is_reported(self) -> None:
        path = _zip(self.root / "empty.docx", {"docProps/core.xml": "<cp:coreProperties/>"})
        text, meta = documents.extract_document_text(path)
        self.assertEqual(text, "")
        self.assertEqual(meta["extractor"], "failed:no-document-xml")

    def test_missing_oversized_and_legacy_files(self) -> None:
        self.assertEqual(documents.extract_document_text(self.root / "nope.docx"), ("", {"extractor": "none"}))
        legacy = self.root / "old.doc"
        legacy.write_bytes(b"\xd0\xcf\x11\xe0")
        self.assertEqual(documents.extract_document_text(legacy)[1]["extractor"], "unavailable:ole-legacy")
        big = self.root / "big.pdf"
        big.write_bytes(b"x")
        with mock.patch.object(documents, "MAX_DOCUMENT_BYTES", 0):
            self.assertEqual(documents.extract_document_text(big)[1]["extractor"], "skipped:too-large")


class OptionalBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _fake_fitz(self, pages: list[str], doc_metadata: dict[str, str] | None, fail: bool = False) -> types.ModuleType:
        module = types.ModuleType("fitz")

        class Page:
            def __init__(self, text: str) -> None:
                self._text = text

            def get_text(self) -> str:
                return self._text

        class Doc:
            metadata = doc_metadata

            def __iter__(self):
                return iter(Page(p) for p in pages)

            def close(self) -> None:
                pass

        def open_(path):
            if fail:
                raise RuntimeError("corrupt pdf")
            return Doc()

        setattr(module, "open", open_)
        return module

    def test_pdf_without_pymupdf_is_unavailable(self) -> None:
        pdf = self.root / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with mock.patch.dict(sys.modules, {"fitz": None}):
            text, meta = documents.extract_document_text(pdf)
        self.assertEqual((text, meta["extractor"]), ("", "unavailable:pymupdf"))

    def test_pdf_text_and_metadata(self) -> None:
        pdf = self.root / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        fitz = self._fake_fitz(["page one ", "page two"], {"producer": "Acme PDF", "author": "", "creationDate": "D:2026"})
        with mock.patch.dict(sys.modules, {"fitz": fitz}):
            text, meta = documents.extract_document_text(pdf)
        self.assertEqual(text, "page one page two")
        self.assertEqual(meta["extractor"], "pymupdf")
        self.assertEqual(meta["pdf.producer"], "Acme PDF")
        self.assertEqual(meta["pdf.created"], "D:2026")
        self.assertNotIn("pdf.author", meta)

    def test_pdf_extraction_stops_at_character_cap(self) -> None:
        pdf = self.root / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        fitz = self._fake_fitz(["abcdef", "ghijkl", "mnopqr"], None)
        with mock.patch.dict(sys.modules, {"fitz": fitz}), mock.patch.object(documents, "MAX_EXTRACTED_CHARS", 8):
            text, _ = documents.extract_document_text(pdf)
        self.assertEqual(text, "abcdefgh")

    def test_pdf_open_failure_is_reported_not_raised(self) -> None:
        pdf = self.root / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with mock.patch.dict(sys.modules, {"fitz": self._fake_fitz([], None, fail=True)}):
            text, meta = documents.extract_document_text(pdf)
        self.assertEqual((text, meta["extractor"]), ("", "failed:pymupdf"))

    def test_hwp_without_reader_is_unavailable(self) -> None:
        hwp = self.root / "a.hwp"
        hwp.write_bytes(b"hwp")
        with mock.patch.dict(sys.modules, {"syhwp": None}):
            text, meta = documents.extract_document_text(hwp)
        self.assertEqual((text, meta["extractor"]), ("", "unavailable:syhwp"))

    def test_hwp_text_format_and_failures(self) -> None:
        hwp = self.root / "a.hwp"
        hwp.write_bytes(b"hwp")
        reader = types.SimpleNamespace(extract_text=lambda p: "본문", detect_format=lambda p: "hwp5")
        with mock.patch.dict(sys.modules, {"syhwp": reader}):
            text, meta = documents.extract_document_text(hwp)
        self.assertEqual((text, meta["extractor"], meta["hwp.format"]), ("본문", "syhwp", "hwp5"))

        def boom(_path: str) -> str:
            raise ValueError("bad ole")

        broken = types.SimpleNamespace(extract_text=boom, detect_format=boom)
        with mock.patch.dict(sys.modules, {"syhwp": broken}):
            text, meta = documents.extract_document_text(hwp)
        self.assertEqual((text, meta["extractor"]), ("", "failed:syhwp:ValueError"))

        no_format = types.SimpleNamespace(extract_text=lambda p: None, detect_format=boom)
        with mock.patch.dict(sys.modules, {"syhwp": no_format}):
            text, meta = documents.extract_document_text(hwp)
        self.assertEqual((text, meta["extractor"]), ("", "syhwp"))
        self.assertNotIn("hwp.format", meta)


class ReviewStoreRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "reviews.json"

    def test_corrupt_or_non_object_file_loads_as_empty(self) -> None:
        self.path.parent.mkdir(parents=True)
        for content in ("{not json", json.dumps(["list"])):
            self.path.write_text(content, encoding="utf-8")
            with self.subTest(content=content):
                self.assertEqual(ReviewStore(self.path).list_reviews(), {})

    def test_unreadable_file_loads_as_empty(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=OSError("denied")):
            self.assertEqual(ReviewStore(self.path).list_reviews(), {})

    def test_blank_review_is_pruned_and_delete_reports_presence(self) -> None:
        store = ReviewStore(self.path)
        store.save_review(" a.jpg ", {"note": "suspicious"})
        self.assertEqual((store.get_review("a.jpg") or {}).get("note"), "suspicious")
        store.save_review("a.jpg", {"note": ""})
        self.assertIsNone(store.get_review("a.jpg"))
        self.assertFalse(store.delete_review("a.jpg"))
        store.save_review("b.jpg", {"star": True})
        self.assertTrue(store.delete_review("b.jpg"))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {})

    def test_update_preserves_created_at_and_unspecified_fields(self) -> None:
        store = ReviewStore(self.path)
        first = store.save_review("c.jpg", {"verdict": "synthetic", "analyst_id": "x1"})
        second = store.save_review("c.jpg", {"note": "added later"})
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertEqual((second["verdict"], second["analyst_id"]), ("synthetic", "x1"))
        reloaded = ReviewStore(self.path).get_review("c.jpg") or {}
        self.assertEqual(reloaded.get("note"), "added later")

    def test_persist_failure_cleans_temp_file_and_keeps_memory(self) -> None:
        store = ReviewStore(self.path)
        store.save_review("d.jpg", {"star": True})
        with mock.patch.object(Path, "replace", side_effect=OSError("disk full")):
            entry = store.save_review("e.jpg", {"star": True})
        self.assertEqual(entry["artifact_id"], "e.jpg")
        self.assertFalse(self.path.with_suffix(".tmp").exists())
        self.assertIsNotNone(store.get_review("e.jpg"))


if __name__ == "__main__":
    unittest.main()
