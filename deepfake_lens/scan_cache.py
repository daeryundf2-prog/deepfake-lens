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
from .json_text import json_dumps


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


# P3 (round 8): --recursive --allow-symlinks followed a link to "/" (or to
# any folder above the scanned one) and walked the whole file system; two
# folders linking to each other re-entered each other through every path.
# A link whose target folder is the scanned folder itself or one of its
# parents is a loop row; a folder already entered through another link is
# not entered twice ((st_dev, st_ino) visited set — the real folder and one
# link to it are both walked, as in X3).
SYMLINK_ANCESTOR_REASON = "건너뜀: 순환/상위 링크 — 링크가 검사 폴더 자신이나 그 상위 폴더를 가리킵니다"
SYMLINK_DUPLICATE_REASON = "건너뜀: 이미 따라간 링크 대상 — 같은 폴더를 링크로 두 번 검사하지 않습니다"
# P3: backstop limits on the walk itself (listing only, no analysis). A
# local evidence tree of 20 000 folders lists in seconds; the limits trip on
# a link into a huge tree (a system folder, a network share) and stop the
# walk with a row per folder left unopened instead of hanging the scan.
MAX_WALK_DIRS = 20_000
# Files counted beyond --max-files (X1 reports how many were not recorded);
# past this many extra files no further folder is opened.
MAX_WALK_FILES_BEYOND_CAP = 100_000
MAX_WALK_SECONDS = 300.0
WALK_LIMIT_REASON = "건너뜀: 탐색 상한 도달({limit}) — 이 폴더 이하는 열지 않았습니다"


def _dir_identity(path: Path | str) -> tuple[int, int] | None:
    import os

    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _root_and_parents(root: Path) -> set[tuple[int, int]]:
    """(st_dev, st_ino) of the scanned folder and every folder above it (P3)."""
    import os

    out: set[tuple[int, int]] = set()
    current = os.path.realpath(root)
    while True:
        ident = _dir_identity(current)
        if ident is not None:
            out.add(ident)
        parent = os.path.dirname(current)
        if parent == current:
            return out
        current = parent


class _WalkLimits:
    """The P3 backstop limits of one walk (folders, seconds, files).

    R9-2: shared by the scan walk (:func:`_iter_files`) and the flat-scan
    subfolder count (:func:`flat_subfolder_survey`).
    """

    def __init__(self, file_limit: int | None = None) -> None:
        import time

        self.dirs_entered = 0
        self.file_limit = file_limit
        self.deadline = time.monotonic() + MAX_WALK_SECONDS

    def hit(self, files: int = 0) -> str | None:
        """The limit reached (Korean label for :data:`WALK_LIMIT_REASON`), or None."""
        import time

        if self.dirs_entered >= MAX_WALK_DIRS:
            return f"폴더 {MAX_WALK_DIRS}개"
        if self.file_limit is not None and files >= self.file_limit:
            return f"파일 {self.file_limit}개"
        if time.monotonic() > self.deadline:
            return f"{int(MAX_WALK_SECONDS)}초"
        return None


def _link_refusal(
    path: Path,
    *,
    root_parents: set[tuple[int, int]],
    ancestors: frozenset[tuple[int, int]],
    entered: set[tuple[int, int]],
) -> tuple[str | None, tuple[int, int] | None]:
    """(why a followable link to a folder is not entered, or None; the target's (st_dev, st_ino)).

    P3: a link to the scanned folder or a folder above it (``/``, ``..``,
    ``.``, the folder's own path) is :data:`SYMLINK_ANCESTOR_REASON`, a
    link back to a folder on the current path :data:`SYMLINK_LOOP_REASON`,
    a folder already entered :data:`SYMLINK_DUPLICATE_REASON`. R9-2: the
    flat-scan subfolder count applies the same rule.
    """
    ident = _dir_identity(path)
    if ident is None:
        return SYMLINK_UNREADABLE_REASON.format(reason="상태를 읽을 수 없음"), None
    if ident in root_parents:
        return SYMLINK_ANCESTOR_REASON, ident
    if ident in ancestors:
        return SYMLINK_LOOP_REASON, ident
    if ident in entered:
        return SYMLINK_DUPLICATE_REASON, ident
    return None, ident


def _iter_files(
    root: Path,
    *,
    recursive: bool,
    allow_symlinks: bool = False,
    on_error: Callable[[Path, OSError], None] | None = None,
    on_symlink: Callable[[Path], None] | None = None,
    on_skip: Callable[[Path, str], None] | None = None,
    max_files: int | None = None,
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

    P3: a link to the scanned folder or any folder above it (``/``, ``..``
    from the root) is a loop row (:data:`SYMLINK_ANCESTOR_REASON`); a folder
    already entered through a link is not entered through a second one
    (:data:`SYMLINK_DUPLICATE_REASON`). The walk stops opening folders after
    :data:`MAX_WALK_DIRS` folders, :data:`MAX_WALK_SECONDS` seconds or
    ``max_files`` + :data:`MAX_WALK_FILES_BEYOND_CAP` files; every folder
    left unopened is a row (:data:`WALK_LIMIT_REASON`).

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
    root_parents = _root_and_parents(root) if allow_symlinks and recursive else set()
    link_entered: set[tuple[int, int]] = set()
    limits = _WalkLimits(None if max_files is None else max_files + MAX_WALK_FILES_BEYOND_CAP)

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

    def _walk(folder: Path, ancestors: frozenset[tuple[int, int]]) -> None:
        if folder != root:
            limit = limits.hit(len(found))
            if limit is not None:
                skipped.append((folder, WALK_LIMIT_REASON.format(limit=limit)))
                return
        limits.dirs_entered += 1
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
                    ident = _dir_identity(path)
                    if ident is not None and ident in ancestors:  # bind-mount loop
                        skipped.append((path, SYMLINK_LOOP_REASON))
                        continue
                    _walk(path, ancestors | ({ident} if ident else set()))
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
                # a subfolder of a flat scan: flat_subfolder_survey counts it
                # or gives it a row (R9-2: a link it does not count)
                continue
            refusal, ident = _link_refusal(path, root_parents=root_parents, ancestors=ancestors, entered=link_entered)
            if refusal is not None or ident is None:
                skipped.append((path, refusal or SYMLINK_UNREADABLE_REASON.format(reason="상태를 읽을 수 없음")))
                continue
            link_entered.add(ident)
            _walk(path, ancestors | {ident})

    root_ident = _dir_identity(root)
    _walk(root, frozenset({root_ident} if root_ident else set()))
    if on_symlink is not None:
        for link in sorted(links, key=key):
            on_symlink(link)
    if on_skip is not None:
        for path, reason in sorted(skipped, key=lambda pair: key(pair[0])):
            on_skip(path, reason)
    yield from sorted(found, key=key)


class FlatSubfolders:
    """What a flat (non-recursive) scan leaves out below the folder (N8, P5, R9-2).

    ``detail``: one ``{"path", "files", "complete"}`` entry per subfolder
    it does not enter, in name order — the regular files below it,
    counted recursively. ``refused``: ``(link, reason)`` for each folder
    link (``--allow-symlinks``) that is not counted — its row reason.
    """

    def __init__(self, detail: list[dict[str, object]], refused: list[tuple[Path, str]]) -> None:
        self.detail = detail
        self.refused = refused


def flat_subfolder_survey(root: Path, *, follow_links: bool = False) -> FlatSubfolders:
    """The subfolders a flat scan does not enter and the files inside them (N8, P5, R9-2).

    A symbolic link to a folder is a subfolder only with ``follow_links``
    (X3: --allow-symlinks; without it, it is a skipped symlink row — D10)
    and only when it can be followed (else it has its own row — X3).

    R9-2 (round 9): the count used to follow every folder link, so a link
    to ``/`` walked the whole file system (146 800 files, a different
    number each run), ``..``, ``.`` and a link to the folder itself
    re-counted the scanned folder, and 50 links to one folder counted it 50
    times. It now uses the P3 walker's rules: a link to the scanned folder
    or a folder above it is refused (:data:`SYMLINK_ANCESTOR_REASON`), and
    every folder — real or linked — is counted once ((st_dev, st_ino)
    visited set over the whole count; a link to a folder already counted is
    :data:`SYMLINK_DUPLICATE_REASON`). Real subfolders are counted first,
    then links, each in name order, and every listing is sorted, so the
    result is the same on every run. Links inside a subfolder are neither
    followed nor counted (P5). The P3 limits (:class:`_WalkLimits`) bound
    the whole count; a folder whose count stopped there is
    ``complete: False``.

    R10-8 (round 10): a folder below a counted one that was already counted
    (through an earlier link, or a bind mount) is skipped, so that count
    leaves its files out — "B 4개" for a linked folder of 5 files whose
    subfolder an earlier link "A" had counted. Such an entry carries
    ``already_counted_folders`` (the number of folders skipped) and the
    reports print "(이미 센 폴더 N개 중복 제외)" after its count.
    """
    import os

    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return FlatSubfolders([], [])
    real: list[Path] = []
    linked: list[Path] = []
    for entry in entries:
        try:
            if entry.is_symlink():
                if follow_links and symlink_problem(entry) is None and entry.is_dir():
                    linked.append(entry)
            elif entry.is_dir():
                real.append(entry)
        except OSError:
            continue
    limits = _WalkLimits()
    root_ident = _dir_identity(root)
    visited: set[tuple[int, int]] = {root_ident} if root_ident else set()
    root_parents = _root_and_parents(root) if linked else set()

    def count(top: Path, top_ident: tuple[int, int] | None) -> dict[str, object]:
        files = 0
        complete = True
        already_counted = 0
        if top_ident is not None:
            visited.add(top_ident)
        stack = [top]
        while stack:
            if limits.hit() is not None:
                complete = False
                break
            folder = stack.pop()
            limits.dirs_entered += 1
            try:
                with os.scandir(folder) as handle:
                    children = sorted(handle, key=lambda child: child.name, reverse=True)
            except OSError:
                complete = False
                continue
            for child in children:
                try:
                    if child.is_symlink():
                        continue  # P5: links inside are neither followed nor counted
                    if child.is_dir(follow_symlinks=False):
                        ident = _dir_identity(child.path)
                        if ident is not None and ident in visited:
                            already_counted += 1  # R10-8: shown as "(이미 센 폴더 N개 중복 제외)"
                            continue  # bind mount / already counted folder
                        if ident is not None:
                            visited.add(ident)
                        stack.append(Path(child.path))
                    elif child.is_file(follow_symlinks=False):
                        files += 1
                except OSError:
                    complete = False
        entry: dict[str, object] = {"path": top.name, "files": files, "complete": complete}
        if already_counted:
            entry["already_counted_folders"] = already_counted
        return entry

    by_name: dict[str, dict[str, object]] = {}
    for folder in real:
        by_name[folder.name] = count(folder, _dir_identity(folder))
    refused: list[tuple[Path, str]] = []
    for link in linked:
        reason, ident = _link_refusal(link, root_parents=root_parents, ancestors=frozenset(), entered=visited)
        if reason is not None or ident is None:
            refused.append((link, reason or SYMLINK_UNREADABLE_REASON.format(reason="상태를 읽을 수 없음")))
            continue
        by_name[link.name] = count(link, ident)
    return FlatSubfolders([by_name[name] for name in sorted(by_name)], refused)


def count_subfolders(root: Path, *, follow_links: bool = False) -> int:
    """Subfolders directly under ``root`` that a non-recursive scan does not enter (N8).

    The folders :func:`flat_subfolder_survey` counts (R9-2: a refused
    folder link is a row, not a subfolder).
    """
    return len(flat_subfolder_survey(root, follow_links=follow_links).detail)


def subfolder_file_counts(root: Path, *, follow_links: bool = False) -> list[dict[str, object]]:
    """Files inside each subfolder a flat scan does not enter (P5) — :func:`flat_subfolder_survey`'s detail."""
    return flat_subfolder_survey(root, follow_links=follow_links).detail


def _read_prefix(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(max(0, limit))


def _display_path(path: Path, *, root: Path | None) -> str:
    """The row path of a real file: relative to ``root``, "::" escaped (P7)."""
    from .result_text import escape_row_path

    try:
        return escape_row_path(str(path.relative_to(root)) if root else str(path))
    except ValueError:
        return escape_row_path(str(path))


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


# Y4 (round 7): --cache silently overwrote any existing JSON file (a scan
# report, a profile) with a cache. A cache file carries this header; an
# existing file without it is refused before the scan instead of replaced.
SCAN_CACHE_FORMAT = "deepfake-lens-cache-v1"
CACHE_NOT_A_CACHE = (
    "캐시 파일이 아닙니다: {path} — --cache 파일 형식({fmt})이 아닌 기존 파일은 덮어쓰지 않습니다. "
    "새 캐시 파일 경로를 지정하십시오"
)
# Keys of a cache written before the header existed (accepted and upgraded).
_LEGACY_CACHE_KEYS = frozenset({"version", "items"})


class CacheFileError(ValueError):
    """``--cache`` names an existing file that is not a scan cache (Y4); ``str()`` is Korean."""


def _is_scan_cache(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("format") == SCAN_CACHE_FORMAT:
        return isinstance(payload.get("items", {}), dict)
    # A pre-Y4 cache: only {"version": 1, "items": {...}}.
    return set(payload) <= _LEGACY_CACHE_KEYS and "items" in payload and isinstance(payload["items"], dict)


def check_scan_cache_file(cache_path: Path | str | None) -> None:
    """Raise :class:`CacheFileError` unless ``cache_path`` is absent, empty or a scan cache (Y4)."""
    if cache_path is None:
        return
    path = Path(cache_path)
    try:
        if not path.exists():
            return
        if path.is_file() and path.stat().st_size == 0:
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise CacheFileError(CACHE_NOT_A_CACHE.format(path=cache_path, fmt=SCAN_CACHE_FORMAT)) from exc
    if not _is_scan_cache(payload):
        raise CacheFileError(CACHE_NOT_A_CACHE.format(path=cache_path, fmt=SCAN_CACHE_FORMAT))


def _load_scan_cache(cache_path: Path | None) -> dict[str, object] | None:
    """The cache at ``cache_path`` (a new one when absent or empty).

    Y4: an existing file that is not a scan cache raises
    :class:`CacheFileError` — it is never overwritten.
    """
    if cache_path is None:
        return None
    check_scan_cache_file(cache_path)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # P4: ValueError covers JSONDecodeError and UnicodeDecodeError
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["format"] = SCAN_CACHE_FORMAT
    payload.setdefault("version", 1)
    payload.setdefault("items", {})
    return payload


def _write_scan_cache(cache_path: Path | None, cache: dict[str, object]) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        body = {"format": SCAN_CACHE_FORMAT, **{key: value for key, value in cache.items() if key != "format"}}
        tmp.write_text(json_dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(cache_path)
    except OSError:
        pass  # cache flush failure must never mask real scan results


def _load_hash_db(hash_db_path: Path | None) -> dict[str, object] | None:
    if hash_db_path is None:
        return None
    try:
        payload = json.loads(hash_db_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # P4: ValueError covers JSONDecodeError and UnicodeDecodeError
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
    hash_db_path.write_text(json_dumps(hash_db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()[:32]  # R11-1


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
        from .result_text import unescape_row_path

        # Messages name the real path; P7: the row path may be escaped.
        row = _rewrite_path_text(row, unescape_row_path(old_path), old_name, unescape_row_path(display), path.name)
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
    from .error_text import ROOT_PLACEHOLDER, unescape_surrogates

    def quoted(path: str) -> str:
        # R13-1: repr() with its surrogate escapes undone — the quoted form
        # error_text.scrub_paths gives every message.
        return unescape_surrogates(repr(f"{ROOT_PLACEHOLDER}/{path}"))

    pairs = [
        # P2: library messages quote the path with repr() ("cannot identify
        # image file '<root>/back\\\\slash.jpg'"), so the quoted, escaped form
        # is rewritten first — to the escaped form of the new path, exactly
        # what an uncached analysis of that file would have written.
        (quoted(old_path), quoted(new_path)),
        (repr(f"{ROOT_PLACEHOLDER}/{old_path}"), quoted(new_path)),  # a row cached before R13-1
        (f"{ROOT_PLACEHOLDER}/{old_path}", f"{ROOT_PLACEHOLDER}/{new_path}"),
        (f"{ROOT_PLACEHOLDER}\\{old_path.replace('/', chr(92))}", f"{ROOT_PLACEHOLDER}\\{new_path.replace('/', chr(92))}"),
    ]
    # R13-1 (round 13): one pass over the text, every form at once — the old
    # sequence (paths, then the bare name) re-scanned the text it had just
    # inserted, so "cut.wav" -> "녹음 1 cut.wav" became "녹음 1 녹음 1 cut.wav"
    # (the name re-matched after the space), and a warm cache differed from a
    # cold scan. At each position the longest form (the quoted path) wins.
    forms = [(re.escape(old), new) for old, new in pairs if old != new]
    if old_name and old_name != new_name:
        forms.append((rf"(?<![\w.\-]){re.escape(old_name)}(?![\w.\-])", new_name))
    combined = re.compile("|".join(f"({pattern})" for pattern, _ in forms)) if forms else None

    def fix(text: str) -> str:
        # P2: a function replacement — ``new_name`` is a file name, not a
        # regex template; a name with "\", "\1" or "\g<0>" must be inserted
        # literally instead of raising re.error (PatternError) mid-scan.
        if combined is None:
            return text
        return combined.sub(lambda match: forms[(match.lastindex or 1) - 1][1], text)

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
