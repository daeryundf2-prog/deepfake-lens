"""Per-process caches tied to a file's version (R27-5 patterns for the techniques-2026b layers).

- :func:`load_versioned`: a packaged table (``models/*.json``) is parsed and hashed once per file version
  instead of once per scanned file. A cached value is reused only while ``os.stat`` of the table (device,
  inode, size, mtime_ns, ctime_ns — taken *before* the read) is unchanged; any change runs the loader again,
  so a changed or broken table is seen exactly as before. Failures are never cached.
- :func:`offer` / :func:`take`: a value one layer already read from a scanned file, handed to a later layer
  of the same file in the same thread (single slot), so the file is not opened and decoded twice. ``take``
  returns it only for the same path and an unchanged file version; otherwise the caller reads the file itself.

Leaf module (``read_prefix`` falls back to ``scan_cache._read_prefix``, imported when called).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_TABLES: dict[tuple[str, str], tuple[tuple[int, ...], Any]] = {}
# Loader runs (cache misses) — the tests assert a scan does not re-read the tables per file.
LOADS = {"tables": 0}
_HANDOFF = threading.local()
_MISSING = object()


def file_version(path: Path | str) -> tuple[int, ...] | None:
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load_versioned(path: Path, loader: Callable[[], T]) -> T:
    """``loader()`` (which reads ``path``), remembered while ``path``'s version is unchanged."""
    key = (str(path), f"{getattr(loader, '__module__', '')}.{getattr(loader, '__qualname__', repr(loader))}")
    version = file_version(path)
    hit = _TABLES.get(key)
    if hit is not None and version is not None and hit[0] == version:
        return hit[1]  # type: ignore[no-any-return]
    _TABLES.pop(key, None)
    LOADS["tables"] += 1
    value = loader()
    if version is not None:
        _TABLES[key] = (version, value)
    return value


def offer(kind: str, path: Path | str, version: tuple[int, ...] | None, value: object) -> None:
    """Hand ``value`` (read from ``path`` at ``version``, taken before the read) to a later layer of this thread."""
    if version is None:
        return
    slots = getattr(_HANDOFF, "slots", None)
    if slots is None:
        slots = _HANDOFF.slots = {}
    slots[kind] = (str(path), version, value)


def take(kind: str, path: Path | str) -> object:
    """The offered value for ``path`` if the file is unchanged since, else :data:`MISSING` (read it yourself)."""
    slots = getattr(_HANDOFF, "slots", None)
    if not slots:
        return _MISSING
    slot = slots.pop(kind, None)
    if slot is None or slot[0] != str(path) or slot[1] != file_version(path):
        return _MISSING
    return slot[2]


MISSING = _MISSING
PREFIX_HANDOFF = "file.prefix"


def offer_prefix(path: Path | str, version: tuple[int, ...] | None, limit: int, data: bytes) -> None:
    """``data`` = the first ``limit`` bytes of ``path`` (one ``read(limit)``), for later layers of the same file."""
    offer(PREFIX_HANDOFF, path, version, (limit, data))


def read_prefix(path: Path, limit: int) -> bytes:
    """``scan_cache._read_prefix(path, limit)`` — served from an offered prefix of the same unchanged file.

    Not consumed (several layers read the same prefix). A shorter request is a slice; a longer one is served only
    when the offered read already reached the end of the file; otherwise the file is read as before.
    """
    slots = getattr(_HANDOFF, "slots", None)
    slot = slots.get(PREFIX_HANDOFF) if slots else None
    if slot is not None and slot[0] == str(path) and slot[1] == file_version(path):
        offered_limit, data = slot[2]
        limit = max(0, limit)
        if limit <= offered_limit:
            return data[:limit]  # type: ignore[no-any-return]
        if len(data) < offered_limit:
            return data  # type: ignore[no-any-return]
    from .scan_cache import _read_prefix

    return _read_prefix(path, limit)


__all__ = ["LOADS", "MISSING", "PREFIX_HANDOFF", "file_version", "load_versioned", "offer", "offer_prefix", "read_prefix", "take"]
