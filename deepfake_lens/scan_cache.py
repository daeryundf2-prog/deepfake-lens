"""Filesystem helpers for directory scans — iteration, dedupe hashing,
and the resumable scan-cache / hash-db persistence files.

Extracted from ``core.py``; all names are re-exported from ``core`` for
compatibility with existing call sites and tests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable


def _iter_files(root: Path, *, recursive: bool, allow_symlinks: bool = False, on_error: Callable[[Path, OSError], None] | None = None) -> Iterable[Path]:
    """Iterate scan targets; unreadable directories skip, not kill.

    ``rglob``/``iterdir`` raise lazily mid-iteration — one permission-
    denied subdirectory must not abort a multi-hour evidence scan.
    """
    if recursive:
        def _walk_error(exc: OSError) -> None:
            if on_error is not None:
                on_error(Path(getattr(exc, "filename", None) or root), exc)
        import os
        for dirpath, _dirs, files in os.walk(root, onerror=_walk_error):
            for name in files:
                path = Path(dirpath) / name
                try:
                    if path.is_symlink() and not allow_symlinks:
                        continue
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                if name.endswith((".ivy.json", ".model.json")):
                    continue
                yield path
        return
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        if on_error is not None:
            on_error(root, exc)
        return
    for path in entries:
        try:
            if path.is_symlink() and not allow_symlinks:
                continue
            if not path.is_file():
                continue
        except OSError:
            continue
        if path.name.endswith((".ivy.json", ".model.json")):
            continue
        yield path


def _read_prefix(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(max(0, limit))


def _display_path(path: Path, *, root: Path | None) -> str:
    try:
        return str(path.relative_to(root)) if root else str(path)
    except ValueError:
        return str(path)


def _duplicate_map(paths: list[Path], *, root: Path, max_file_bytes: int | None, hash_db_path: Path | None) -> dict[Path, str]:
    hash_db = _load_hash_db(hash_db_path)
    seen = hash_db.setdefault("hashes", {}) if hash_db is not None else {}
    if not isinstance(seen, dict):
        seen = {}
        if hash_db is not None:
            hash_db["hashes"] = seen
    duplicates: dict[Path, str] = {}
    for path in paths:
        if max_file_bytes is not None:
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
        fingerprint = _file_fingerprint(path)
        if not fingerprint:
            continue
        display_path = _display_path(path, root=root)
        if fingerprint in seen:
            duplicates[path] = str(seen[fingerprint])
        else:
            seen[fingerprint] = display_path
    if hash_db is not None:
        _write_hash_db(hash_db_path, hash_db)
    return duplicates


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _load_scan_cache(cache_path: Path | None) -> dict[str, object] | None:
    if cache_path is None:
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "items": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "items": {}}
    payload.setdefault("version", 1)
    payload.setdefault("items", {})
    return payload


def _write_scan_cache(cache_path: Path | None, cache: dict[str, object]) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(cache_path)
    except OSError:
        pass  # cache flush failure must never mask real scan results


def _load_hash_db(hash_db_path: Path | None) -> dict[str, object] | None:
    if hash_db_path is None:
        return None
    try:
        payload = json.loads(hash_db_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "hashes": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "hashes": {}}
    payload.setdefault("version", 1)
    payload.setdefault("hashes", {})
    return payload


def _write_hash_db(hash_db_path: Path | None, hash_db: dict[str, object]) -> None:
    if hash_db_path is None:
        return
    hash_db_path.parent.mkdir(parents=True, exist_ok=True)
    hash_db_path.write_text(json.dumps(hash_db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cache_key(
    path: Path,
    *,
    root: Path,
    text_bytes: int,
    metadata_bytes: int,
    pixel_mode: str,
    pixel_max_side: int,
    heatmaps: bool,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
    deep_signals: bool = False,
    provenance: str = "",
) -> str:
    try:
        stat = path.stat()
        relative = str(path.relative_to(root))
    except OSError:
        return str(path)
    if isinstance(model_path, (list, tuple)):
        model_marker = ";".join(str(Path(entry).resolve()) for entry in model_path)
    else:
        model_marker = str(Path(model_path).resolve()) if model_path else ""
    return "|".join(
        [
            relative,
            str(stat.st_size),
            str(int(stat.st_mtime_ns)),
            str(text_bytes),
            str(metadata_bytes),
            pixel_mode,
            str(pixel_max_side),
            str(bool(heatmaps)),
            model_marker,
            str(bool(deep_signals)),
            # Threshold/weights/tool provenance — a cached score computed
            # under different calibration or coverage must never replay.
            provenance,
        ]
    )
