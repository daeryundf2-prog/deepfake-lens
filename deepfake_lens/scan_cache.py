"""Filesystem helpers for directory scans — iteration, dedupe hashing,
and the resumable scan-cache / hash-db persistence files.

Extracted from ``core.py``; all names are re-exported from ``core`` for
compatibility with existing call sites and tests.
"""

from __future__ import annotations

import hashlib
import re
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .profile_pins import ModelPathArg, model_path_digest, pin_tokens, profile_pins
from .result_types import ScanItem


def _scan_order_key(root: Path) -> Callable[[Path], str]:
    """Sort key of the scan order (G32, W1): the POSIX relative path string.

    One global order over the whole walk — "sub/a.txt" sorts between
    "su.txt" and "z.txt" by plain string comparison, never "files of a
    folder first, then its subfolders" — and the same on every OS (Windows
    separators are normalized to "/").
    """

    def key(path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()

    return key


# D10/N13: reason on the row of a symlink found in a scanned folder when
# symlinks are not allowed (the default).
SYMLINK_SKIP_REASON = "심볼릭 링크 — 링크를 따라가지 않으므로 분석하지 않았습니다(심볼릭 링크 허용 안 함)"
# X3 (round 7): with --allow-symlinks a link is followed; one that cannot be
# followed is still a row with its reason (it used to vanish from the rows
# and from summary.total).
SYMLINK_DANGLING_REASON = "건너뜀: 깨진 심볼릭 링크 — 링크 대상이 없습니다"
SYMLINK_LOOP_REASON = "건너뜀: 순환 링크 — 링크가 자기 자신이나 상위 폴더를 가리킵니다"
SYMLINK_UNREADABLE_REASON = "건너뜀: 심볼릭 링크 대상을 읽을 수 없습니다({reason})"
# X3: a FIFO, socket or device node in the evidence folder is listed, never
# opened (reading a FIFO blocks) and never dropped silently.
NOT_REGULAR_FILE_REASON = "건너뜀: 일반 파일이 아닙니다(파이프·소켓·장치 파일) — 열지 않았습니다"


def symlink_problem(path: Path) -> str | None:
    """Why an allowed symlink cannot be followed (X3), or None when its target exists.

    A self-referencing or circular chain is "순환 링크", a missing target
    "깨진 심볼릭 링크"; any other error names its reason.
    """
    import errno

    try:
        path.resolve(strict=True)
    except RuntimeError:  # Python < 3.13 reports a symlink loop this way
        return SYMLINK_LOOP_REASON
    except FileNotFoundError:
        return SYMLINK_DANGLING_REASON
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return SYMLINK_LOOP_REASON
        if exc.errno == errno.ENOENT:
            return SYMLINK_DANGLING_REASON
        return SYMLINK_UNREADABLE_REASON.format(reason=exc.strerror or type(exc).__name__)
    return None


def _iter_files(
    root: Path,
    *,
    recursive: bool,
    allow_symlinks: bool = False,
    on_error: Callable[[Path, OSError], None] | None = None,
    on_symlink: Callable[[Path], None] | None = None,
    on_skip: Callable[[Path, str], None] | None = None,
) -> Iterable[Path]:
    """Iterate scan targets; unreadable directories skip, not kill.

    A symlinked file is not yielded unless ``allow_symlinks``; it is
    reported through ``on_symlink`` so the scan can list it as skipped
    (D10) instead of dropping it from the report silently.

    X3: with ``allow_symlinks`` a link to a file is yielded, a link to a
    folder is followed by a recursive walk (not entered by a flat one — it
    is a subfolder, counted by :func:`count_subfolders`), and a link that
    cannot be followed — missing target, a self-loop or a link back to a
    folder on the current path — is reported through ``on_skip(path,
    reason)``. A FIFO, socket or device node is reported the same way
    (never opened). Nothing in the folder is dropped without a row.

    Directory read errors go to ``on_error`` — one permission-denied
    subdirectory must not abort a multi-hour evidence scan.

    Order is deterministic (G32, W1): the whole walk is collected first and
    yielded in one global sort by the POSIX relative path string
    (:func:`_scan_order_key`), the same on every OS. The OS directory order
    (creation order on ext4, hash order elsewhere) used to decide which
    files a max-files cap kept and which copy dedupe called the original.
    Symlinks and skipped entries are reported in the same order, before
    the first file is yielded.
    """
    import os

    key = _scan_order_key(root)
    found: list[Path] = []
    links: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    def _file(path: Path) -> None:
        try:
            if not path.is_file():
                skipped.append((path, NOT_REGULAR_FILE_REASON))
                return
        except OSError:
            return
        if path.name.endswith((".ivy.json", ".model.json")):
            return
        found.append(path)

    def _walk(folder: Path, ancestors: frozenset[str]) -> None:
        try:
            with os.scandir(folder) as handle:
                entries = sorted(handle, key=lambda entry: entry.name)
        except OSError as exc:
            if on_error is not None:
                on_error(folder, exc)
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                is_link = entry.is_symlink()
                is_dir = not is_link and entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if recursive:
                    _walk(path, ancestors | {os.path.realpath(path)})
                continue
            if not is_link:
                _file(path)
                continue
            if not allow_symlinks:
                links.append(path)
                continue
            problem = symlink_problem(path)
            if problem is not None:
                skipped.append((path, problem))
                continue
            try:
                target_is_dir = path.is_dir()
            except OSError:
                target_is_dir = False
            if not target_is_dir:
                _file(path)
                continue
            if not recursive:
                continue  # a subfolder of a flat scan (count_subfolders counts it)
            real = os.path.realpath(path)
            if real in ancestors:
                skipped.append((path, SYMLINK_LOOP_REASON))
                continue
            _walk(path, ancestors | {real})

    _walk(root, frozenset({os.path.realpath(root)}))
    if on_symlink is not None:
        for link in sorted(links, key=key):
            on_symlink(link)
    if on_skip is not None:
        for path, reason in sorted(skipped, key=lambda pair: key(pair[0])):
            on_skip(path, reason)
    yield from sorted(found, key=key)


def count_subfolders(root: Path, *, follow_links: bool = False) -> int:
    """Subfolders directly under ``root`` that a non-recursive scan does not enter (N8).

    A symlinked folder is not counted unless ``follow_links`` (X3:
    --allow-symlinks) — without it, it is reported as a skipped symlink row
    instead (D10). A link that cannot be followed is never counted (it has
    its own row).
    """
    count = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_symlink():
                if follow_links and symlink_problem(entry) is None and entry.is_dir():
                    count += 1
            elif entry.is_dir():
                count += 1
        except OSError:
            continue
    return count


def _read_prefix(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(max(0, limit))


def _display_path(path: Path, *, root: Path | None) -> str:
    try:
        return str(path.relative_to(root)) if root else str(path)
    except ValueError:
        return str(path)


def _duplicate_map(
    paths: list[Path],
    *,
    root: Path,
    max_file_bytes: int | None,
    hash_db_path: Path | None,
    fingerprints: dict[Path, str] | None = None,
) -> dict[Path, str]:
    """Map each duplicate path to the display path of its first occurrence.

    ``fingerprints`` (path -> SHA-256) is filled as a side effect so the
    scan-cache key and the item ``sha256`` reuse these digests instead of
    hashing every file a second time.
    """
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
        fingerprint = _content_sha256(path, fingerprints)
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
    root: Path | None = None,
    text_bytes: int,
    metadata_bytes: int,
    pixel_mode: str,
    pixel_max_side: int,
    heatmaps: bool,
    model_path: ModelPathArg,
    deep_signals: bool = False,
    provenance: str = "",
    fingerprints: dict[Path, str] | None = None,
    scan_context: str | None = None,
) -> str:
    """Content-addressed cache key (G11, N6).

    key = SHA-256(file bytes) + the file's extension + hash(analysis
    options) + tool version + sorted profile-pin list (+ threshold/weights
    provenance). Path, size and mtime are deliberately absent: a same-size
    edit whose mtime was restored must miss (QA-IN-4), and a renamed folder
    must still hit. N6: the extension is part of the key because it selects
    the analysis — the kind (image/audio/…) and the reader (the C2PA SDK
    decides the format from it: an empty ``.png`` is an I/O failure, an
    empty ``.jpg`` an unsupported format) — so ``zero.png`` no longer
    replays ``empty.jpg``'s row. ``root`` is accepted for call-site
    compatibility and ignored. Returns "" (never cacheable) when the file
    cannot be read.
    """
    del root
    digest = _content_sha256(path, fingerprints)
    if not digest:
        return ""
    options = json.dumps(
        {
            "text_bytes": int(text_bytes),
            "metadata_bytes": int(metadata_bytes),
            "pixel_mode": pixel_mode,
            "pixel_max_side": int(pixel_max_side),
            "heatmaps": bool(heatmaps),
            "deep_signals": bool(deep_signals),
            "model_profiles": model_path_digest(model_path),
        },
        sort_keys=True,
    )
    context = scan_context if scan_context is not None else _cache_scan_context(model_path)
    return "|".join(
        [
            CACHE_KEY_VERSION,
            f"sha256:{digest}",
            f"ext:{cache_extension(path)}",
            f"opts:{_short_digest(options)}",
            context,
            # Threshold/weights provenance — a cached verdict computed under
            # different calibration or coverage must never replay.
            f"prov:{_short_digest(provenance)}",
        ]
    )


def _short_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _content_sha256(path: Path, fingerprints: dict[Path, str] | None = None) -> str:
    """SHA-256 of ``path``'s bytes, memoized per scan in ``fingerprints``.

    The memo lives only for one scan (``_scan_specs`` creates it), so a file
    rewritten between scans is always re-read — never trusted by its size
    or mtime (G11, QA-IN-4).
    """
    if fingerprints is not None:
        known = fingerprints.get(path)
        if known:
            return known
    digest = _file_fingerprint(path)
    if digest and fingerprints is not None:
        fingerprints[path] = digest
    return digest


# Bumped whenever the key layout changes so entries written under an older
# layout (the path+size+mtime keys before G11) can never match.
CACHE_KEY_VERSION = "content-v3"  # v3 (N6): + extension


def cache_extension(path: Path) -> str:
    """The lowercased extension that selects the analysis (compound archive suffixes kept)."""
    name = path.name.lower()
    for compound in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if name.endswith(compound):
            return compound
    return path.suffix.lower()


def _cache_scan_context(model_path: ModelPathArg, *, models_dir: Path | str | None = None) -> str:
    """Per-scan part of every cache key: tool version + model pins.

    Computed once per scan (reading every profile per file would be
    wasteful) and passed to ``_cache_key`` as ``scan_context``. Unpinned
    profiles contribute ``unpinned:<name>``, so pinning or re-pinning a
    profile invalidates the verdicts cached under the old weights.
    """
    from .core import TOOL_VERSION

    tokens = pin_tokens(profile_pins(models_dir, model_path))
    return f"tool:{TOOL_VERSION}|pins:{_short_digest(chr(10).join(tokens))}"


def _cached_scan_item(cached: object, path: Path, *, root: Path) -> ScanItem | None:
    """Rebuild a cached row for the file at ``path``, or None to re-analyze.

    The key is content + extension, so the stored row may come from a file
    with the same bytes elsewhere (or from before a folder rename): path and
    name are rewritten to the current file, and so is every path-dependent
    text in the row (N6 — an error message that named "<root>/empty.jpg"
    must name the file this row is about). A row whose heatmap no longer
    exists is a miss rather than a replay with a dead link.
    """
    if not isinstance(cached, dict):
        return None
    from .serialization import _scan_item_from_json

    display = _display_path(path, root=root)
    row: dict[str, object] = cached
    old_path, old_name = row.get("path"), row.get("name")
    if isinstance(old_path, str) and isinstance(old_name, str) and (old_path, old_name) != (display, path.name):
        row = _rewrite_path_text(row, old_path, old_name, display, path.name)
    try:
        item = _scan_item_from_json(row)
    except (ValueError, TypeError, KeyError):
        return None  # corrupt entry — re-analyze
    pixel = item.result.pixel_analysis if item.result is not None else None
    heatmap_path = getattr(pixel, "heatmap_path", None)
    if heatmap_path and not Path(heatmap_path).is_file():
        return None
    return replace(item, path=display, name=path.name)


def _rewrite_path_text(node: object, old_path: str, old_name: str, new_path: str, new_name: str) -> Any:
    """``node`` with every mention of the cached file's path/name replaced by the current file's (N6).

    Messages carry the file as ``<root>/<relative path>`` (scrubbed root,
    either separator) or, for paths outside the root, as its base name
    (``error_text.scrub_paths``); both forms are rewritten — the name only
    as a whole path token, so "e.png" inside "the.png" is left alone.
    """
    from .error_text import ROOT_PLACEHOLDER

    pairs = [
        (f"{ROOT_PLACEHOLDER}/{old_path}", f"{ROOT_PLACEHOLDER}/{new_path}"),
        (f"{ROOT_PLACEHOLDER}\\{old_path.replace('/', chr(92))}", f"{ROOT_PLACEHOLDER}\\{new_path.replace('/', chr(92))}"),
    ]
    name_pattern = re.compile(rf"(?<![\w.\-]){re.escape(old_name)}(?![\w.\-])") if old_name and old_name != new_name else None

    def fix(text: str) -> str:
        for old, new in pairs:
            text = text.replace(old, new)
        return name_pattern.sub(new_name, text) if name_pattern is not None else text

    def walk(value: object) -> object:
        if isinstance(value, str):
            return fix(value)
        if isinstance(value, list):
            return [walk(entry) for entry in value]
        if isinstance(value, dict):
            return {key: walk(entry) for key, entry in value.items()}
        return value

    return walk(node)


def _with_content_sha256(item: ScanItem, path: Path, fingerprints: dict[Path, str] | None) -> ScanItem:
    """Record the analyzed file's SHA-256 on the row (reusing the scan memo)."""
    if item.sha256:
        return item
    digest = _content_sha256(path, fingerprints)
    return replace(item, sha256=digest) if digest else item
