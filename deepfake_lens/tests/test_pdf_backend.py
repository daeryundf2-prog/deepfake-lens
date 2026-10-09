"""N4: importing PyMuPDF never writes to stdout.

``import fitz`` (PyMuPDF's legacy module name) prints ``warning: The `fitz`
API is deprecated …`` to stdout; a folder holding a PDF used to break
``scan --format json``. ``pdf_backend.import_pymupdf`` imports ``pymupdf``
first and captures stdout around both imports.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.pdf_backend import import_pymupdf

REPO_ROOT = Path(__file__).resolve().parents[2]
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None

# A one-page PDF with a text object (hand-written; xref offsets computed below).
_PDF_OBJECTS = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    b"<< /Length 44 >>\nstream\nBT /F1 12 Tf 20 100 Td (Case memo 7) Tj ET\nendstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]


def minimal_pdf() -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(_PDF_OBJECTS, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(_PDF_OBJECTS) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(_PDF_OBJECTS) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


class LegacyFitzImportIsSilentTest(unittest.TestCase):
    """A stand-in ``fitz`` that prints like the real one; ``pymupdf`` absent."""

    def test_fallback_import_keeps_stdout_clean(self) -> None:
        saved = {name: sys.modules.get(name) for name in ("pymupdf", "fitz")}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "fitz.py").write_text(
                "print('warning: The `fitz` API is deprecated and will be removed in future. Use `import pymupdf` instead.')\n"
                "MARKER = 'stand-in fitz'\n",
                encoding="utf-8",
            )
            sys.modules["pymupdf"] = None  # type: ignore[assignment]  # import pymupdf -> ImportError
            sys.modules.pop("fitz", None)
            sys.path.insert(0, tmp)
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    module = import_pymupdf()
            finally:
                sys.path.remove(tmp)
                for name, value in saved.items():
                    if value is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = value
        self.assertEqual(getattr(module, "MARKER", None), "stand-in fitz")
        self.assertEqual(out.getvalue(), "")

    def test_neither_module_raises_import_error(self) -> None:
        saved = {name: sys.modules.get(name) for name in ("pymupdf", "fitz")}
        sys.modules["pymupdf"] = None  # type: ignore[assignment]
        sys.modules["fitz"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(ImportError):
                import_pymupdf()
        finally:
            for name, value in saved.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value


@unittest.skipUnless(HAVE_PYMUPDF, "pymupdf not installed — the venv_api / extras run covers this")
class ScanJsonStdoutWithPdfTest(unittest.TestCase):
    """N4: ``scan --format json`` on a folder with a PDF prints parseable JSON only."""

    def test_stdout_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "case"
            folder.mkdir()
            (folder / "memo.pdf").write_bytes(minimal_pdf())
            (folder / "note.txt").write_text("사건 메모입니다.", encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            completed = subprocess.run(
                [sys.executable, "-m", "deepfake_lens", "scan", str(folder), "--format", "json", "--no-default-engine"],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=300,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertNotIn("deprecated", completed.stdout)
        payload = json.loads(completed.stdout)  # the whole stdout, not a slice
        pdf_row = next(item for item in payload["items"] if item["path"] == "memo.pdf")
        self.assertEqual(pdf_row["status"], "analyzed")
        coverage = {entry["check"]: entry["status"] for entry in pdf_row["result"]["coverage"]}
        self.assertEqual(coverage.get("document_text"), "ran")  # PyMuPDF did extract the text


if __name__ == "__main__":
    unittest.main()
