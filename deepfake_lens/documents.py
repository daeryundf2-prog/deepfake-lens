"""Office-document text extraction for the scan pipeline.

Real-world documents are rarely .txt/.md — this module extracts body text
and provenance metadata from PDF/DOCX/HWP/XLSX/PPTX so the text detectors
run on the same corpus users actually have. Every extractor is optional:
a missing dependency yields an ``unavailable:<dep>`` note rather than an
error, and extraction failures degrade to empty text plus a limitation —
never an exception.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

# Extensions routed to this module from analyze_file.
SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".hwp", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}

MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_CHARS = 1024 * 1024


def extract_document_text(path: Path | str) -> tuple[str, dict[str, str]]:
    """Extract (body_text, metadata) from an office document.

    ``metadata`` carries provenance fields the forensic layer records:
    ``extractor`` (which backend ran or ``unavailable:<dep>``),
    plus format-specific fields like ``pdf.producer`` or ``docx.creator``.
    """
    doc_path = Path(path)
    meta: dict[str, str] = {"extractor": "none"}
    try:
        size = doc_path.stat().st_size
    except OSError:
        return "", meta
    if size > MAX_DOCUMENT_BYTES:
        meta["extractor"] = "skipped:too-large"
        return "", meta

    suffix = doc_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(doc_path, meta)
    if suffix == ".docx":
        return _extract_docx(doc_path, meta)
    if suffix in (".xlsx", ".pptx"):
        return _extract_ooxml(doc_path, meta, suffix)
    if suffix == ".hwp":
        return _extract_hwp(doc_path, meta)
    # Legacy OLE formats (.doc/.xls/.ppt): no zero-dependency extractor —
    # mark honestly rather than guessing at binary text.
    meta["extractor"] = "unavailable:ole-legacy"
    return "", meta


def _extract_pdf(path: Path, meta: dict[str, str]) -> tuple[str, dict[str, str]]:
    try:
        import fitz  # pymupdf
    except ImportError:
        meta["extractor"] = "unavailable:pymupdf"
        return "", meta
    try:
        doc = fitz.open(path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
            if sum(len(t) for t in text_parts) > MAX_EXTRACTED_CHARS:
                break
        info = doc.metadata or {}
        doc.close()
    except Exception:
        meta["extractor"] = "failed:pymupdf"
        return "", meta
    meta["extractor"] = "pymupdf"
    for key, out_key in (("producer", "pdf.producer"), ("creator", "pdf.creator"),
                         ("author", "pdf.author"), ("creationDate", "pdf.created")):
        if info.get(key):
            meta[out_key] = str(info[key])[:200]
    return "".join(text_parts)[:MAX_EXTRACTED_CHARS], meta


def _strip_xml_text(xml: str) -> str:
    """Pull visible text out of an OOXML fragment: <w:t>/<a:t> runs become
    text, paragraph ends become newlines, everything else drops."""
    xml = re.sub(r"<w:p[ >]", "\n<w:p ", xml)
    xml = re.sub(r"<a:p[ >]", "\n<a:p ", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _docx_core_props(zf: zipfile.ZipFile, meta: dict[str, str]) -> None:
    try:
        core = zf.read("docProps/core.xml").decode("utf-8", errors="replace")
    except KeyError:
        return
    fields = {
        "dc:creator": "docx.creator",
        "cp:lastModifiedBy": "docx.last_modified_by",
        "cp:revision": "docx.revision",
        "dcterms:created": "docx.created",
        "dcterms:modified": "docx.modified",
    }
    for tag, key in fields.items():
        match = re.search(rf"<{tag}[^>]*>([^<]*)</{tag}>", core)
        if match and match.group(1).strip():
            meta[key] = match.group(1).strip()[:200]
    try:
        app = zf.read("docProps/app.xml").decode("utf-8", errors="replace")
        match = re.search(r"<Application[^>]*>([^<]*)</Application>", app)
        if match:
            meta["docx.application"] = match.group(1).strip()[:100]
    except KeyError:
        pass


def _extract_docx(path: Path, meta: dict[str, str]) -> tuple[str, dict[str, str]]:
    # docx is a zip of XML — the stdlib alone extracts body text, so there
    # is no dependency gate here.
    try:
        with zipfile.ZipFile(path) as zf:
            _docx_core_props(zf, meta)
            try:
                document = zf.read("word/document.xml").decode("utf-8", errors="replace")
            except KeyError:
                meta["extractor"] = "failed:no-document-xml"
                return "", meta
    except (zipfile.BadZipFile, OSError):
        meta["extractor"] = "failed:zip"
        return "", meta
    meta["extractor"] = "zip-xml"
    return _strip_xml_text(document)[:MAX_EXTRACTED_CHARS], meta


def _extract_ooxml(path: Path, meta: dict[str, str], suffix: str) -> tuple[str, dict[str, str]]:
    """xlsx/pptx share the docx zip container; text lives in sharedStrings
    or slide XML respectively."""
    try:
        with zipfile.ZipFile(path) as zf:
            _docx_core_props(zf, meta)
            names = zf.namelist()
            if suffix == ".xlsx":
                targets = [n for n in names if n == "xl/sharedStrings.xml"]
                targets += sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
            else:
                targets = sorted(
                    (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"\d+", n).group()),
                )
            parts = []
            for name in targets[:200]:
                parts.append(_strip_xml_text(zf.read(name).decode("utf-8", errors="replace")))
    except (zipfile.BadZipFile, OSError):
        meta["extractor"] = "failed:zip"
        return "", meta
    meta["extractor"] = "zip-xml"
    return "\n".join(parts)[:MAX_EXTRACTED_CHARS], meta


def _extract_hwp(path: Path, meta: dict[str, str]) -> tuple[str, dict[str, str]]:
    """HWP is an OLE compound file; extraction needs olefile. Without it we
    degrade honestly — binary grepping produces garbage text."""
    try:
        import olefile  # noqa: F401
    except ImportError:
        meta["extractor"] = "unavailable:olefile"
        return "", meta
    # HWP text lives in the PrvText/BodyText streams, often deflate-packed;
    # a full decoder is out of scope — record that extraction is partial.
    meta["extractor"] = "unavailable:hwp-decoder"
    return "", meta
