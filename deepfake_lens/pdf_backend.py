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
import sys
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


_availability: tuple[tuple[object, object, object], bool] | None = None
_failure_logged = False


def pymupdf_available() -> bool:
    """Whether PyMuPDF imports (REV2-D4: the answer is cached, a failure is logged once with its traceback).

    In the bundle pymupdf is always shipped, so a failed import is a broken install (a missing or blocked DLL); the
    traceback in the log says which. The cache is keyed on :func:`import_pymupdf` and the ``pymupdf``/``fitz``
    entries of ``sys.modules`` — a test that patches either (to stand in for a missing package) gets a fresh answer,
    and the answer is recomputed once the patch is undone.
    """
    global _availability, _failure_logged
    key = (import_pymupdf, sys.modules.get("pymupdf"), sys.modules.get("fitz"))
    cached = _availability
    if cached is not None and all(a is b for a, b in zip(cached[0], key)):
        return cached[1]
    try:
        import_pymupdf()
    except ImportError:
        if not _failure_logged:
            _failure_logged = True
            logger.warning("PyMuPDF could not be imported; PDF output is unavailable", exc_info=True)
        available = False
    else:
        available = True
    _availability = ((import_pymupdf, sys.modules.get("pymupdf"), sys.modules.get("fitz")), available)
    return available
