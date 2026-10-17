"""Dependency probes that remember a missing module (R27-5, round 27).

A check that needs an optional dependency (``torch``, ``onnxruntime``,
``audioseal``) imports it first so that, without it, :func:`checks.run_check`
records ``skipped`` "의존성 부재: <module>". Python does not remember a
failed import: every attempt searches ``sys.path`` again, and the image
watermark checks made two such failed searches per scanned file.

:func:`require` imports each name in order like ``import name`` and raises
the same ``ModuleNotFoundError`` (same message, same ``name``) — so the
coverage reason is unchanged — but a top-level module that was *not found*
is remembered for the current ``sys.path`` / ``sys.meta_path`` and not
searched again. Only "not found" is remembered: a module that is present
but fails to import (a broken install, a missing sub-dependency) is
imported again every time, exactly as before. A module present in
``sys.modules`` (imported meanwhile, or a test double) always wins, and an
explicit ``sys.modules[name] = None`` is honoured by the import system as
before (never cached). Leaf module: no imports from the package.
"""

from __future__ import annotations

import importlib
import sys

_MISSING: dict[tuple[object, ...], tuple[str, str | None]] = {}
_FOUND: set[tuple[object, ...]] = set()
# Import attempts that reached the import system — the tests assert a scan does not repeat them per file.
PROBE_STATS = {"attempts": 0}


def _key(name: str) -> tuple[object, ...]:
    return (name, tuple(sys.path), tuple(id(finder) for finder in sys.meta_path))


def require(*names: str) -> None:
    """``import name`` for each name, in order; a remembered "not found" is raised again without a search."""
    for name in names:
        if sys.modules.get(name) is not None:
            continue
        absent_before = name not in sys.modules
        key = _key(name)
        remembered = _MISSING.get(key) if absent_before else None
        if remembered is not None:
            raise ModuleNotFoundError(remembered[0], name=remembered[1])
        PROBE_STATS["attempts"] += 1
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if absent_before and name not in sys.modules and exc.name == name and "." not in name:
                _MISSING[key] = (str(exc.args[0]) if exc.args else str(exc), exc.name)
            raise


def require_available(name: str) -> None:
    """Raise what ``import name`` raises when the top-level module ``name`` cannot be found — without importing it.

    For a probe whose module is used only on some paths (``cv2`` in the SDXL check is needed only for an image
    that is decoded, not for one below the size floor): importing OpenCV starts its own BLAS thread pool, whose
    start-up spin cost a scan of small images more than the check itself. Absent (or ``None`` in ``sys.modules``)
    raises the same ``ModuleNotFoundError`` as before; a module that is found is imported where it is used, so a
    broken install still fails there with its own ImportError.
    """
    import importlib.util

    if sys.modules.get(name) is not None:
        return
    if name in sys.modules:  # sys.modules[name] is None: what the import system raises
        raise ModuleNotFoundError(f"import of {name} halted; None in sys.modules", name=name)
    key = _key(name)
    if key in _FOUND:  # as before: once ``import name`` succeeded, the module stayed importable for the process
        return
    remembered = _MISSING.get(key)
    if remembered is not None:
        raise ModuleNotFoundError(remembered[0], name=remembered[1])
    PROBE_STATS["attempts"] += 1
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        require(name)  # an unusual finder state — fall back to the import itself
        return
    if spec is None:
        _MISSING[key] = (f"No module named {name!r}", name)
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    _FOUND.add(key)


__all__ = ["PROBE_STATS", "require", "require_available"]
