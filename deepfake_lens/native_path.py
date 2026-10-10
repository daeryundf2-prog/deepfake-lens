"""ASCII-only file names for native decoders (R12-1, R12-2).

OpenCV's ``cv2.VideoCapture`` converts its ``str`` argument to a C++
``std::string``; a POSIX file name that is not UTF-8 reaches Python as a
``str`` with lone surrogates (PEP 383) and that conversion crashes the
process (SIGSEGV — exit 139, no row written, the web server dies). On
Windows the same narrow-``char*`` APIs (OpenCV, libsndfile, the C2PA SDK)
cannot open a Korean name outside the ANSI code page at all, and
``soundfile``/``librosa``/``pymupdf`` raise ``UnicodeEncodeError`` on a
surrogate before opening anything.

:func:`native_safe_path` is the one gate every native decoder call goes
through (``cv2.VideoCapture``/``cv2.imread``, an ``ffmpeg`` subprocess,
``librosa.load``, ``soundfile.read``, ``pymupdf.open``, ``c2pa.Reader``,
SyncNet): it yields a path made only of ASCII characters that names the
same bytes. An ASCII path is passed through untouched. Any other path is
*staged* in a per-process session folder under the system temp directory
(never the evidence folder — QA-IN-1) under an ASCII name
``<n>-<random><.ext>``. On POSIX:

1. a symbolic link to the original — it does not touch the evidence
   file's inode at all (a hard link would change its ``ctime`` and link
   count, which an examiner may later have to explain);
2. otherwise a hard link (the R12-1 required fix; same volume only);
3. otherwise a read-only copy, only up to :data:`NATIVE_COPY_MAX_BYTES`.

R13-8 (round 13): on Windows (no symbolic links without privilege; the
evidence often on another drive than the temp folder) every Korean-named
file used to be copied in full, up to 2 GB. There the order is:

1. a hard link (same volume);
2. the file's 8.3 short name (``GetShortPathNameW``) when it is ASCII —
   nothing is created or copied at all;
3. a read-only copy up to :data:`NATIVE_COPY_MAX_BYTES`; a larger file is
   :class:`NativePathError` "판단 불가: 네이티브 디코더용 임시 사본 상한
   초과", so the check that needed the decoder is ``failed``.

The staged name is removed when the ``with`` block ends and the session
folder at interpreter exit. R13-4 (round 13): also on SIGTERM / SIGINT
(SIGHUP, SIGBREAK where they exist) — :func:`install_cleanup_handlers`,
called by the CLI at start and by :func:`session_dir` in the main thread,
removes it before the signal's previous action runs — and a folder left by
a process killed outright (SIGKILL, power loss) is swept the next time a
session folder is made: its name carries the owning process id
(``deepfake-lens-native-<pid>-<random>``), and a folder of the current
user whose process is gone is removed.

R13-1 (round 13): a decoder that cannot open the staged name quotes it in
its exception ("Error opening '/tmp/deepfake-lens-native-…/000035-
df0fda9674ca.m4a'"), and that random name used to reach the coverage
reason, limitations, CSV, HTML and the report — so two scans of the same
folder differed (QA-IN-2) and a non-ASCII row differed from its ASCII
copy (R12-2). Every staged name is therefore registered with the original
path it stands for, and :func:`restore_original_names` (applied first by
``error_text.scrub_paths``, which every failure reason goes through) puts
the original path back — exactly the text the decoder would have quoted
had it been given the original name. A staging folder or name that is
not (or no longer) registered is replaced by a fixed placeholder, so no
staging name can appear in any output.

When no route works (copy over the cap, no ASCII temp folder)
:class:`NativePathError` is raised with a Korean message, so the check that
needed the decoder is recorded as ``failed`` — never silently skipped,
never handed the raw name.

``deepfake_lens/tests/test_native_path.py`` holds the AST meta-test that
every native call site in the package is inside ``with
native_safe_path(...) as <name>:`` and passes ``<name>``.
"""

from __future__ import annotations

import atexit
import itertools
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

# Largest file copied when no link (and, on Windows, no ASCII short name) can be made.
# Same figure as the archive extraction budget (phase-0 spec WP-H, G34:
# "총 바이트 2 GB") — the most this tool ever writes to temp for one input.
NATIVE_COPY_MAX_BYTES = 2 * 1024 * 1024 * 1024
# Operator override for the staging base folder (must itself be ASCII).
NATIVE_TMP_ENV = "DEEPFAKE_LENS_NATIVE_TMPDIR"
SESSION_PREFIX = "deepfake-lens-native-"
# Extensions longer than this are not real container suffixes; dropped.
MAX_SUFFIX_CHARS = 12

# R13-4: a session folder is "<prefix><pid>-<random>"; the pid tells a
# later run whether its owner is still alive.
_SESSION_OWNER = re.compile(re.escape(SESSION_PREFIX) + r"(\d+)-[A-Za-z0-9_]+")
# R13-4: a session folder of a tool version before R13-4 has no pid in its
# name; it is swept once it is this old (a day — no scan keeps one staging
# folder that long without touching it, and the tool never reuses one).
STALE_LEGACY_SECONDS = 24 * 60 * 60
# R13-4: a folder whose owner is gone is swept only once it has not changed
# for this long ("오래된") — a process in another PID namespace sharing the
# temp folder looks dead from here; every staged name it creates or removes
# touches the folder, so an hour of no change is not a scan in progress.
STALE_MIN_AGE_SECONDS = 60 * 60
# R13-4: the signals whose default action ends the process without running
# atexit; the folder is removed first.
CLEANUP_SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK")
# R13-8: Windows API constants (winnt.h / winerror.h).
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
# R13-8: the message of a file that would need a copy over the cap.
COPY_CAP_EXCEEDED = "판단 불가: 네이티브 디코더용 임시 사본 상한 초과"

_LOCK = threading.Lock()
_SESSION_DIR: str | None = None
_COUNTER = itertools.count(1)
_HANDLERS_INSTALLED = False

# R13-8: a Windows 8.3 short path handed to a decoder -> the original path.
_SHORT_ALIASES: OrderedDict[str, str] = OrderedDict()
# R13-1: staged base name -> the original path text it stands for. Bounded
# (oldest out first): an exception is turned into its reason right after the
# ``with`` block that raised it, so only recent names are ever looked up; the
# bound keeps a very large scan from growing the map without limit.
NATIVE_ALIAS_MAX = 65536
_ALIASES: OrderedDict[str, str] = OrderedDict()
# Every session folder this process created (normally one).
_SESSION_DIRS: list[str] = []
# A staged base name: "<6-digit counter>-<12 hex><.ext>" (see _stage).
_STAGED_BASE = r"\d{6}-[0-9a-f]{12}(?:\.[0-9a-z]{1,%d})?" % (MAX_SUFFIX_CHARS - 1)
_STAGED_NAME = re.compile(rf"(?<![\w.-])({_STAGED_BASE})(?![\w.-])")
# Any session folder (this process's or another's, a stale one), with the
# staged name under it when there is one.
_SESSION_PATH = re.compile(re.escape(SESSION_PREFIX) + rf"[A-Za-z0-9_-]+(?:(?:\\\\|[\\/])({_STAGED_BASE})(?![\w.-]))?")
# R13-1: what a staging folder / name that is not registered is shown as —
# fixed text, so a scan's output never depends on a random temp name.
STAGED_FOLDER_PLACEHOLDER = "<네이티브 디코더용 임시 폴더>"
STAGED_NAME_PLACEHOLDER = "<네이티브 디코더용 임시 이름>"


class NativePathError(RuntimeError):
    """A non-ASCII path could not be given an ASCII name for a native decoder."""


def is_native_safe(path: str | os.PathLike[str]) -> bool:
    """True when ``path`` can be handed to a native decoder as it is."""
    text = os.fspath(path)
    return isinstance(text, str) and text.isascii()


def _ascii_suffix(path: str) -> str:
    suffix = os.path.splitext(path)[1].lower()
    if 1 < len(suffix) <= MAX_SUFFIX_CHARS and suffix.isascii() and suffix[1:].isalnum():
        return suffix
    return ""


def _base_candidates() -> list[str]:
    candidates = []
    override = os.environ.get(NATIVE_TMP_ENV)
    if override:
        candidates.append(override)
    candidates.append(tempfile.gettempdir())
    candidates.append("C:\\Windows\\Temp" if os.name == "nt" else "/tmp")
    return candidates


def _is_windows() -> bool:
    return os.name == "nt"


def _remove_tree(path: str) -> None:
    """``shutil.rmtree`` that also removes read-only copies (Windows); errors ignored."""
    if not os.path.lexists(path):
        return

    def writable_then_retry(function: Callable[..., Any], target: str, *_: Any) -> None:
        try:
            os.chmod(target, stat.S_IWUSR | stat.S_IRUSR)
            function(target)
        except OSError:
            logger.debug("native_safe_path: %s not removed", target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable_then_retry)
    else:  # pragma: no cover - Python < 3.12
        shutil.rmtree(path, onerror=writable_then_retry)


def _pid_alive(pid: int) -> bool:
    """R13-4: True unless process ``pid`` is known to be gone (access denied counts as alive)."""
    if pid <= 0:
        return False
    if _is_windows():
        # os.kill(pid, 0) would TerminateProcess on Windows — ask, never signal.
        import ctypes

        kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return getattr(ctypes, "get_last_error")() != _ERROR_INVALID_PARAMETER
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # EPERM: another user's live process
    return True


def sweep_stale_sessions(base: str) -> list[str]:
    """R13-4: remove the session folders in ``base`` whose owning process is gone.

    Only folders (never a link) owned by the current user (POSIX) are
    touched: ``<prefix><pid>-<random>`` when that process no longer runs and
    the folder is :data:`STALE_MIN_AGE_SECONDS` old, and a pre-R13-4
    ``<prefix><random>`` once it is :data:`STALE_LEGACY_SECONDS` old.
    Returns the removed names.
    """
    removed: list[str] = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return removed
    now = time.time()
    own_uid = os.getuid() if hasattr(os, "getuid") else None
    for name in names:
        if not name.startswith(SESSION_PREFIX):
            continue
        path = os.path.join(base, name)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode) or (own_uid is not None and info.st_uid != own_uid):
            continue
        age = now - info.st_mtime
        owner = _SESSION_OWNER.fullmatch(name)
        if owner is not None:
            pid = int(owner.group(1))
            if age < STALE_MIN_AGE_SECONDS or pid == os.getpid() or _pid_alive(pid):
                continue
        elif age < STALE_LEGACY_SECONDS:
            continue
        _remove_tree(path)
        if not os.path.lexists(path):
            removed.append(name)
            logger.info("native_safe_path: stale staging folder %s removed", path)
    return removed


def cleanup_session() -> None:
    """R13-4: remove this process's session folder(s) now (atexit, signal handlers).

    No lock: a signal handler runs in the main thread between bytecodes and
    may interrupt a holder of :data:`_LOCK`; copying the list is atomic.
    """
    global _SESSION_DIR
    for folder in list(_SESSION_DIRS):
        if os.path.isdir(folder):
            _remove_tree(folder)
    _SESSION_DIR = None


def _cleanup_then(previous: Any) -> Callable[[int, Any], None]:
    import signal

    def handler(signum: int, frame: Any) -> None:
        cleanup_session()
        if callable(previous):
            previous(signum, frame)  # e.g. SIGINT's default_int_handler: KeyboardInterrupt
            return
        if previous == signal.SIG_IGN:
            return
        # SIG_DFL (or a handler set outside Python): the default action.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    return handler


def install_cleanup_handlers() -> bool:
    """R13-4: remove the session folder on SIGTERM / SIGINT (SIGHUP, SIGBREAK) before their previous action.

    Signal handlers can only be set from the main thread; elsewhere this
    returns False (the CLI calls it at start, so staging in a worker thread
    is covered). Idempotent.
    """
    global _HANDLERS_INSTALLED
    if _HANDLERS_INSTALLED:
        return True
    if threading.current_thread() is not threading.main_thread():
        return False
    import signal

    for name in CLEANUP_SIGNALS:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _cleanup_then(signal.getsignal(signum)))
        except (OSError, RuntimeError, ValueError):
            logger.debug("native_safe_path: no cleanup handler for %s", name)
    _HANDLERS_INSTALLED = True
    return True


def session_dir() -> str:
    """The per-process staging folder (created on first use, ASCII path).

    R13-4: named ``<prefix><pid>-<random>``; stale folders of dead
    processes in the same base are swept first.
    """
    global _SESSION_DIR
    with _LOCK:
        if _SESSION_DIR is not None and os.path.isdir(_SESSION_DIR):
            return _SESSION_DIR
        for base in _base_candidates():
            if not base.isascii() or not os.path.isdir(base):
                continue
            sweep_stale_sessions(base)
            try:
                created = tempfile.mkdtemp(prefix=f"{SESSION_PREFIX}{os.getpid()}-", dir=base)
            except OSError:
                continue
            _SESSION_DIR = created
            _SESSION_DIRS.append(created)
            atexit.register(_remove_tree, created)
            break
        else:
            created = ""
    if created:
        install_cleanup_handlers()
        return created
    raise NativePathError(
        "비ASCII 파일 이름을 네이티브 디코더에 넘길 ASCII 임시 폴더가 없습니다"
        f"(환경 변수 {NATIVE_TMP_ENV}로 ASCII 경로의 폴더를 지정하십시오)"
    )


def _register_alias(staged: str, original: str) -> None:
    """R13-1: remember that ``staged`` stands for ``original`` (as given)."""
    with _LOCK:
        _ALIASES[os.path.basename(staged)] = original
        while len(_ALIASES) > NATIVE_ALIAS_MAX:
            _ALIASES.popitem(last=False)


def _alias(name: str) -> str | None:
    with _LOCK:
        return _ALIASES.get(name)


def _register_short_alias(short: str, original: str) -> None:
    """R13-8: remember that the 8.3 short path ``short`` stands for ``original``."""
    with _LOCK:
        _SHORT_ALIASES[short] = original
        while len(_SHORT_ALIASES) > NATIVE_ALIAS_MAX:
            _SHORT_ALIASES.popitem(last=False)


def _replace_short_paths(text: str) -> str:
    """R13-8: 8.3 short paths handed to a decoder -> the originals (``repr()`` form when quoted)."""
    if "~" not in text:
        return text
    with _LOCK:
        shorts = sorted(_SHORT_ALIASES.items(), key=lambda pair: len(pair[0]), reverse=True)
    for short, original in shorts:
        for quoted, shown in ((repr(short), repr(original)), (short, original)):
            text = text.replace(quoted, shown)
    return text


def _replace_staged_paths(text: str, folder: str) -> str:
    """Full staged paths under ``folder`` -> the originals, quoted the way the decoder quoted them.

    A decoder that quotes a path does it with ``repr()`` ("Error opening
    '/tmp/…/000001-….m4a'"): a quoted staged path becomes ``repr(original)``
    — what that decoder writes for the original name, and the form the scan
    cache rewrites quoted paths to (P2) — so a lone surrogate shows as
    ``\\udcc1`` exactly as in a Pillow message. An unquoted one becomes the
    original as given; a Windows ``repr()`` doubles the separators.
    """
    for escaped in (False, True):
        shown = folder.replace("\\", "\\\\") if escaped else folder
        if shown not in text:
            continue
        separator = r"(?:\\\\|/)" if escaped else r"[\\/]"
        pattern = re.compile(r"(?P<quote>['\"]?)" + re.escape(shown) + separator + rf"(?P<name>{_STAGED_BASE})(?![\w.-])(?P=quote)")

        def original(match: re.Match[str], escaped: bool = escaped) -> str:
            known = _alias(match.group("name"))
            if known is None:
                return match.group("quote") + STAGED_NAME_PLACEHOLDER + match.group("quote")
            if match.group("quote"):
                return repr(known)
            # An unquoted repr() text (rare): separators doubled the same way.
            return known.replace("\\", "\\\\") if escaped else known

        text = pattern.sub(original, text)
    return text


def restore_original_names(text: str) -> str:
    """``text`` with every staged name replaced by the original path it stands for (R13-1).

    A full staged path becomes the original path as it was handed to
    :func:`native_safe_path` (``repr()``-quoted when the decoder quoted it);
    a bare staged base name becomes the original's base name — what the
    decoder would have printed for the original. A staging folder or name
    this process does not know becomes a fixed placeholder.
    """
    if not text:
        return text
    text = _replace_short_paths(text)
    if SESSION_PREFIX not in text and not _STAGED_NAME.search(text):
        return text
    with _LOCK:
        folders = sorted(_SESSION_DIRS, key=len, reverse=True)
    for folder in folders:
        text = _replace_staged_paths(text, folder)

    def bare(match: re.Match[str]) -> str:
        known = _alias(match.group(1))
        if known is None:
            return match.group(0)  # not ours: an evidence file may be named like this
        return re.split(r"[\\/]", known)[-1] or known

    text = _STAGED_NAME.sub(bare, text)
    return _SESSION_PATH.sub(lambda m: STAGED_NAME_PLACEHOLDER if m.group(1) else STAGED_FOLDER_PLACEHOLDER, text)


def _short_path_name(path: str) -> str | None:
    """R13-8: the Windows 8.3 short form of ``path`` when it is ASCII, else None.

    ``GetShortPathNameW`` returns the long path itself where 8.3 names are
    disabled (or a component has none); that is not ASCII and gives None.
    """
    if not _is_windows():
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    function = kernel32.GetShortPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    needed = function(path, None, 0)
    if not needed:
        return None
    buffer = ctypes.create_unicode_buffer(needed)
    written = function(path, buffer, needed)
    if not written or written >= needed:
        return None
    short = buffer.value
    return short if short.isascii() and short != path else None


def _copy_staged(absolute: str, target: str) -> str:
    try:
        size = os.stat(absolute).st_size
    except OSError as exc:
        raise NativePathError(f"비ASCII 이름의 파일을 열 수 없습니다({type(exc).__name__})") from exc
    if size > NATIVE_COPY_MAX_BYTES:
        raise NativePathError(
            f"{COPY_CAP_EXCEEDED} — 비ASCII 이름의 파일을 링크할 수 없고"
            + (" ASCII 짧은 이름(8.3)도 없어" if _is_windows() else "")
            + f" 사본이 필요하지만 크기({size}바이트)가 사본 상한({NATIVE_COPY_MAX_BYTES}바이트)을 넘어"
            " 네이티브 디코더로 열지 않았습니다"
        )
    shutil.copyfile(absolute, target)
    os.chmod(target, stat.S_IRUSR)
    return target


def _stage(source: str) -> tuple[str, bool]:
    """An ASCII name for ``source`` and whether it is ours to remove afterwards.

    POSIX: symbolic link, hard link, copy. R13-8 — Windows: hard link,
    ASCII 8.3 short name (the evidence file itself; nothing to remove),
    copy.
    """
    absolute = os.path.abspath(source)
    folder = session_dir()
    target = os.path.join(folder, f"{next(_COUNTER):06d}-{uuid.uuid4().hex[:12]}{_ascii_suffix(source)}")
    _register_alias(target, source)
    if not _is_windows():
        try:
            os.symlink(absolute, target)
            return target, True
        except OSError:
            logger.debug("native_safe_path: symlink refused for staged name %s", target)
    try:
        os.link(absolute, target)
        return target, True
    except OSError:
        logger.debug("native_safe_path: hard link refused for staged name %s", target)
    short = _short_path_name(absolute)
    if short is not None:
        _register_short_alias(short, source)
        return short, False
    return _copy_staged(absolute, target), True


@contextmanager
def native_safe_path(path: str | os.PathLike[str]) -> Iterator[str]:
    """Yield an ASCII-only path naming the same file as ``path``.

    Use the yielded name for the native call and nothing else (rows,
    messages and hashes keep the original path).
    """
    text = os.fspath(path)
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if text.isascii():
        yield text
        return
    staged, owned = _stage(text)
    try:
        yield staged
    finally:
        if owned:
            _unlink_staged(staged)


def _unlink_staged(staged: str) -> None:
    try:
        os.unlink(staged)
    except FileNotFoundError:
        pass
    except OSError:
        # A read-only copy on Windows: clear the flag, then remove.
        try:
            os.chmod(staged, stat.S_IWUSR | stat.S_IRUSR)
            os.unlink(staged)
        except OSError:
            logger.warning("native_safe_path: staged name %s not removed", staged)


def staged_names() -> list[str]:
    """Names currently in the session folder (for tests)."""
    with _LOCK:
        folder = _SESSION_DIR
    if folder is None or not os.path.isdir(folder):
        return []
    return sorted(os.listdir(folder))


__all__ = [
    "COPY_CAP_EXCEEDED",
    "NATIVE_COPY_MAX_BYTES",
    "NativePathError",
    "cleanup_session",
    "install_cleanup_handlers",
    "is_native_safe",
    "native_safe_path",
    "restore_original_names",
    "session_dir",
    "staged_names",
    "sweep_stale_sessions",
]
