"""PyMuPDF import shared by the PDF reader and writers (N4).

PyMuPDF ships two module names: ``pymupdf`` (current) and the legacy
``fitz``, whose import prints ``warning: The `fitz` API is deprecated …``
to **stdout**. That line used to land in the middle of
``scan --format json`` output whenever a folder held a PDF, so stdout was
no longer valid JSON. :func:`import_pymupdf` imports ``pymupdf`` first and
falls back to ``fitz`` only with stdout captured (the captured text goes to
the debug log), so no import ever writes to stdout.
"""

from __future__ import annotations

import contextlib
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


def import_pymupdf() -> Any:
    """The PyMuPDF module (``pymupdf``, else legacy ``fitz``); ImportError if neither.

    Both imports run with stdout captured — the captured text (the fitz
    deprecation warning, or anything a future release prints) goes to the
    debug log, never to stdout.
    """
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            try:
                import pymupdf as module
            except ImportError:
                import fitz as module
    finally:
        if captured.getvalue().strip():
            logger.debug("PyMuPDF import output (kept off stdout): %s", captured.getvalue().strip())
    return module


def pymupdf_available() -> bool:
    try:
        import_pymupdf()
    except ImportError:
        return False
    return True
