"""Whether this process runs from the double-click Windows bundle, and the install advice that fits (RISK B10).

The Windows bundle (``scripts/build_desktop_bundle.py``) is python.org's embeddable CPython with a ``python312._pth``
file next to ``python.exe``; it has no pip, so advice such as ``pip install pymupdf`` cannot be followed there. A
normal install (venv, system Python, the macOS bundle's ``.venv``) has no ``._pth`` file in ``sys.prefix``.

Nothing here writes files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BUNDLE_MISSING_FEATURE = "이 기능은 꾸러미에 포함되어 있지 않습니다 — 담당자에게 요청하십시오"
"""What the bundle says instead of a pip command; :func:`install_hint` adds the package name in parentheses."""

_PIP_ADVICE = re.compile(r"(?:설치:\s*)?`pip install ([^`]+)`")


def running_from_bundle(prefix: str | None = None) -> bool:
    """True when ``sys.prefix`` (or ``prefix``) holds a ``python3*._pth`` file — the embeddable Python of the bundle."""
    root = Path(sys.prefix if prefix is None else prefix)
    try:
        return any(root.glob("python3*._pth"))
    except OSError:
        return False


def install_hint(package_extra: str, *, bundle: bool | None = None) -> str:
    """``\\`pip install <package_extra>\\``` normally; in the bundle the "not included — ask the person in charge" line.

    ``bundle`` overrides the detection (tests).
    """
    in_bundle = running_from_bundle() if bundle is None else bundle
    if in_bundle:
        return f"{BUNDLE_MISSING_FEATURE}({package_extra})"
    return f"`pip install {package_extra}`"


def bundle_advice(text: str, *, bundle: bool | None = None) -> str:
    """``text`` unchanged outside the bundle; in the bundle every ``[설치: ]`pip install X``` becomes :func:`install_hint`."""
    in_bundle = running_from_bundle() if bundle is None else bundle
    if not in_bundle:
        return text
    return _PIP_ADVICE.sub(lambda match: install_hint(match.group(1), bundle=True), text)


DOCTOR_LAUNCHER = "문제 확인.cmd"
"""The bundle's diagnostics launcher (``scripts/build_desktop_bundle.py`` ``WIN_DOCTOR``; a test keeps them equal)."""

PDF_ENGINE_FAILED = "PDF 엔진을 불러오지 못했습니다 — 「{doctor}」 결과를 보내 주십시오"
"""REV2-D4: the bundle ships pymupdf, so a PDF engine that does not load there is a broken install (a missing or blocked
DLL — :func:`deepfake_lens.pdf_backend.pymupdf_available` logs the traceback), not a package to install."""
PDF_ENGINE_FAILED_HINT = "Markdown 출력을 사용하거나 「{doctor}」 결과를 보내 주십시오."
"""REV2-D4: the bundle's hint beside a failed evidence-statement PDF (outside the bundle: a pip command)."""


def bundle_text(text: str, inside: str, *, bundle: bool | None = None) -> str:
    """``text`` outside the bundle (byte for byte); ``inside`` with ``{doctor}`` filled in the bundle (REV2-D4)."""
    in_bundle = running_from_bundle() if bundle is None else bundle
    return inside.format(doctor=DOCTOR_LAUNCHER) if in_bundle else text
