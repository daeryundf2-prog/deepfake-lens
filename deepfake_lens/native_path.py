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
``<n>-<random><.ext>``.

R14-2 (round 14): the evidence file itself is never modified in any way —
not its bytes, mode, ``ctime``, link count or (Windows) attributes. So no
route creates a hard link (that changes the evidence inode's ``ctime`` and
link count on POSIX, its link count and change time on NTFS), and nothing
is ever ``chmod``-ed except a copy this process made (see
:func:`_release_copy`). On POSIX:

1. a symbolic link to the original — it does not touch the evidence
   file's inode at all;
2. otherwise a read-only copy, only up to :data:`NATIVE_COPY_MAX_BYTES`.

R13-8 (round 13): on Windows (no symbolic links without privilege; the
evidence often on another drive than the temp folder) every Korean-named
file used to be copied in full, up to 2 GB. There the order is:

1. the file's 8.3 short name (``GetShortPathNameW``) when it is ASCII —
   nothing is created, copied or touched at all;
2. a copy up to :data:`NATIVE_COPY_MAX_BYTES`; a larger file is
   :class:`NativePathError` "판단 불가: 네이티브 디코더용 임시 사본 상한
   초과", so the check that needed the decoder is ``failed``.

The staged name is removed when the ``with`` block ends and the session
folder at interpreter exit. R13-4 (round 13): also on SIGTERM / SIGINT
(SIGHUP, SIGBREAK where they exist) — :func:`install_cleanup_handlers`,
called by the CLI at start and by :func:`session_dir` in the main thread,
removes it before the default action ends the process. R14-1 (round 14):
only where the inherited action *is* the default — an ignored signal
(``nohup``'s SIGHUP, a background job's SIGINT) stays ignored and the scan
keeps its staged names; a Python handler stays in place and the atexit hook
removes the folder if the process then exits — and a folder left by
a process killed outright (SIGKILL, power loss) is swept the next time a
session folder is made: its name carries the owning process id
(``deepfake-lens-native-<pid>-<random>``), and a folder of the current
user whose process is gone is removed.

R15-1 (round 15): the cleanup is final. It first sets the "종료 중" flag
(:mod:`deepfake_lens.shutdown`): no new session folder, staged name or child
process is made after it — such a call raises
:class:`~deepfake_lens.shutdown.ShuttingDown`, a ``CheckSkipped`` "종료 중" —
and it waits (bounded) for the other threads to finish the step they are
in; it then stops every tracked child (``ffmpeg``: terminate → wait →
kill), removes the folders until they are gone (a bounded number of
attempts) and only then lets the signal's default action end the process.
The session folder is no longer reset to "none" by the cleanup while the
process is ending, so a worker cannot make a fresh one nobody removes.

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

R13-7 (round 13): OpenCV *writes* take the same narrow name, and their
target is a temp folder that is non-ASCII under a Korean Windows user name
(a non-UTF-8 TMPDIR crashed the process): :func:`imwrite_any` encodes in
memory and writes with Python instead of ``cv2.imwrite``.

``deepfake_lens/tests/test_native_path.py`` holds the AST meta-test that
every native call site in the package (sub-packages included, aliases
such as ``import cv2 as cv`` resolved) is inside ``with
native_safe_path(...) as <name>:`` and passes ``<name>``, and that no
``cv2.imwrite`` / ``cv2.VideoWriter`` call remains.
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

from . import shutdown
from .shutdown import ShuttingDown

logger = logging.getLogger(__name__)

# Largest file copied when no link (and, on Windows, no ASCII short name) can be made.
# Same figure as the archive extraction budget (phase-0 spec WP-H, G34:
# "총 바이트 2 GB") — the most this tool ever writes to temp for one input.
NATIVE_COPY_MAX_BYTES = 2 * 1024 * 1024 * 1024
# Operator override for the staging base folder (must itself be ASCII).
NATIVE_TMP_ENV = "DEEPFAKE_LENS_NATIVE_TMPDIR"
# R15-6 (round 15): the operator's choice of the folder every temp file of a
# run is made in (the session folder's base; ASCII path) — tried first.
TMP_ENV = "DEEPFAKE_LENS_TMPDIR"
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
# R14-2: a copy this process made is marked by a sidecar file holding the
# copy's "<st_dev>:<st_ino>"; only a file whose marker names its own inode
# may have its read-only flag cleared before removal (Windows).
COPY_MARKER_SUFFIX = ".dfl-copy"
# R15-1: how many times the cleanup removes the session folders before it
# gives up (logged), and the pause between attempts. A folder still has
# entries only while a thread that started before the "종료 중" flag finishes
# its step; 40 x 25 ms = 1 s covers that on a loaded machine and bounds an
# unremovable folder (immutable, foreign owner) to a second.
CLEANUP_ATTEMPTS = 40
CLEANUP_RETRY_SECONDS = 0.025

_LOCK = threading.Lock()
_SESSION_DIR: str | None = None
# R15-6: session folder -> where it was made and why (temp_location()).
_TEMP_LOCATIONS: dict[str, dict[str, object]] = {}
_TEMP_NOTICE_SHOWN = False
# R15-6: the stderr notice when the temp files of a run go elsewhere than the
# temp folder the system (or DEEPFAKE_LENS_TMPDIR) names — printed once.
TEMP_FALLBACK_NOTICE = (
    "알림: 임시 폴더 {requested}을(를) 쓸 수 없어({reason}) 이 실행의 임시 파일(압축 해제 최대 2 GB 포함)을 "
    "{base}에 만듭니다. 다른 위치를 쓰려면 환경 변수 " + TMP_ENV + "에 ASCII 경로의 폴더를 지정하십시오."
)
_COUNTER = itertools.count(1)
_HANDLERS_INSTALLED = False
_ATEXIT_REGISTERED = False
# R14-7: the session folder for temp files when no ASCII staging folder exists.
_SCRATCH_FALLBACK: str | None = None

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
# R14-3: any native-staging text (a session folder prefix, a staged base name,
# a placeholder) — a cached row carrying it is never replayed (scan_cache).
STAGING_TEXT = re.compile(rf"{re.escape(SESSION_PREFIX)}|{_STAGED_NAME.pattern}|{re.escape('<네이티브 디코더용 임시')}")
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
    for name in (TMP_ENV, NATIVE_TMP_ENV):
        override = os.environ.get(name)
        if override:
            candidates.append(override)
    candidates.append(tempfile.gettempdir())
    candidates.append("C:\\Windows\\Temp" if os.name == "nt" else "/tmp")
    return candidates


def _is_windows() -> bool:
    return os.name == "nt"


def _can_chmod_without_following() -> bool:
    """R14-2: whether ``os.chmod(..., follow_symlinks=False)`` exists here (Windows 3.13+, BSD/macOS)."""
    return os.chmod in os.supports_follow_symlinks


def _marked_identity(path: str) -> tuple[int, int] | None:
    """R14-2: the ``(st_dev, st_ino)`` the copy marker of ``path`` records, or None."""
    marker = path + COPY_MARKER_SUFFIX
    try:
        if not stat.S_ISREG(os.lstat(marker).st_mode):
            return None
        with open(marker, encoding="ascii") as handle:
            device, inode = handle.read(64).strip().split(":")
        return int(device), int(inode)
    except (OSError, ValueError, UnicodeError):
        return None


def _release_copy(path: str, original: str | None = None) -> bool:
    """R14-2: make the read-only copy at ``path`` removable — never anything else.

    Only Windows needs this (POSIX removal does not depend on the file's
    mode, so nothing is ever ``chmod``-ed there). The flag is cleared only
    when every test holds: ``path`` is a regular file and not a link, its
    link count is 1 (a hard link shares the evidence file's attributes),
    it is not the ``original``'s inode, its copy marker names exactly its
    inode, and ``os.chmod`` can be told not to follow a link — where it
    cannot, nothing is changed (the copy stays and is logged).
    """
    if not _is_windows() or not _can_chmod_without_following():
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return False
    if _marked_identity(path) != (info.st_dev, info.st_ino):
        return False
    if original is not None:
        try:
            source = os.stat(original)
        except OSError:
            source = None
        if source is not None and (source.st_dev, source.st_ino) == (info.st_dev, info.st_ino):
            return False
    try:
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR, follow_symlinks=False)
    except (OSError, NotImplementedError):
        return False
    return True


def _remove_tree(path: str) -> None:
    """``shutil.rmtree`` of a session folder; errors logged, never a mode change outside our copies.

    R14-2: the error handler used to ``chmod`` whatever could not be
    removed — through a staged symbolic link that is the evidence file
    (0444 -> 0600, its ``ctime`` changed), and on Windows a hard link's
    read-only attribute is the evidence file's. Now only the copies this
    process (or an earlier run) made are released first
    (:func:`_release_copy`); everything else that cannot be removed stays.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return
    if not stat.S_ISDIR(info.st_mode):
        return
    try:
        names = os.listdir(path)
    except OSError:
        names = []
    for name in names:
        if name.endswith(COPY_MARKER_SUFFIX):
            _release_copy(os.path.join(path, name[: -len(COPY_MARKER_SUFFIX)]))

    def log_only(function: Callable[..., Any], target: str, *_: Any) -> None:
        logger.debug("native_safe_path: %s not removed (%s)", target, getattr(function, "__name__", function))

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=log_only)
    else:  # pragma: no cover - Python < 3.12
        shutil.rmtree(path, onerror=log_only)


def _pid_alive(pid: int) -> bool:
    """R13-4: True unless process ``pid`` is known to be gone (access denied counts as alive)."""
    if pid <= 0:
        return False
    if _is_windows():
        # os.kill(pid, 0) would TerminateProcess on Windows — ask, never signal.
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # EPERM: another user's live process
    return True


def _kernel32() -> Any:
    """R14-8: ``kernel32`` with the signatures this module calls (the one ctypes entry point).

    Every function gets explicit ``argtypes``/``restype``: without them a
    64-bit ``HANDLE`` comes back as a C ``int`` (truncated) and a Python int
    is passed where a ``DWORD`` is expected. The tests replace
    ``ctypes.WinDLL`` with a recorder and check these signatures.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel32.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetShortPathNameW.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _win_last_error() -> int:
    """R14-8: the thread's last Win32 error as ctypes saved it (``use_last_error=True``)."""
    import ctypes

    return int(getattr(ctypes, "get_last_error")())


def _win_pid_alive(pid: int) -> bool:
    """R13-4 / R14-8: Windows — True unless ``pid`` is known to be gone.

    ``OpenProcess`` failing with ERROR_INVALID_PARAMETER means no such
    process; any other failure (access denied: another user's process) and
    an exit code that cannot be read count as alive. The handle is always
    closed.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return _win_last_error() != _ERROR_INVALID_PARAMETER
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return int(code.value) == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


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


def cleanup_session(attempts: int = CLEANUP_ATTEMPTS) -> bool:
    """R13-4 / R15-1: remove this process's session folder(s) now; True when none is left.

    R15-1: removed until gone — up to ``attempts`` passes, the list re-read
    each time (a folder registered meanwhile is included) — instead of one
    ``rmtree`` whose failure (a name staged during it) was only logged.
    No lock: a signal handler runs in the main thread between bytecodes and
    may interrupt a holder of :data:`_LOCK`; copying the list is atomic.
    While the process is ending (:func:`shutdown.active`) the session folder
    is not reset to "none": nothing may make a new one.
    """
    global _SESSION_DIR, _SCRATCH_FALLBACK
    left: list[str] = []
    for attempt in range(max(1, attempts)):
        left = [folder for folder in list(_SESSION_DIRS) if os.path.lexists(folder)]
        if not left:
            break
        if attempt:
            time.sleep(CLEANUP_RETRY_SECONDS)
        for folder in left:
            _remove_tree(folder)
        left = [folder for folder in list(_SESSION_DIRS) if os.path.lexists(folder)]
        if not left:
            break
    for folder in left:
        logger.warning("native_safe_path: session folder %s not removed after %d attempts", folder, attempts)
    if not shutdown.active():
        _SESSION_DIR = None
        _SCRATCH_FALLBACK = None
    return not left


def shutdown_session() -> bool:
    """R15-1: the process is ending — "종료 중" flag, children stopped, folders removed until gone.

    Signal-handler safe (no lock). True when no session folder is left.
    """
    shutdown.begin()
    return cleanup_session()


def _at_exit() -> None:
    """R15-1: the atexit hook — the same final cleanup as a signal (daemon threads may still run)."""
    shutdown_session()


def _cleanup_then_default(signum: int, frame: Any) -> None:
    """R14-1: the handler put in place of ``SIG_DFL`` — remove the folder, then the default action.

    Installed only where the inherited action is ``SIG_DFL`` for a signal
    whose default action ends the process (SIGTERM, SIGINT without Python's
    handler, SIGHUP, SIGBREAK), so the process is about to end: nothing is
    being decoded from the folder afterwards.
    """
    import signal

    shutdown_session()  # R15-1: flag, children stopped, folders removed until gone
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _wants_cleanup_handler(previous: Any) -> bool:
    """R14-1: whether the inherited action of a signal is replaced by :func:`_cleanup_then_default`.

    - ``SIG_IGN`` (``nohup`` for SIGHUP, a background job for SIGINT): the
      process is told to survive the signal — no handler; removing the live
      staging folder made the file being decoded a false ``failed`` row
      ("System error") while the scan went on;
    - a Python handler (SIGINT's ``default_int_handler``, an embedding
      application's own): left in place — it decides whether the process
      ends; when it does (KeyboardInterrupt, SystemExit), the ``with`` blocks
      unwind and the atexit hook removes the folder after the workers stop;
    - a handler set outside Python (``None``): unknown, left in place;
    - ``SIG_DFL``: the process ends — the folder is removed first (atexit
      never runs when a signal's default action ends the process).
    """
    import signal

    return previous == signal.SIG_DFL


def install_cleanup_handlers() -> bool:
    """R13-4 / R14-1: remove the session folder on SIGTERM / SIGINT (SIGHUP, SIGBREAK) when it ends the process.

    Only a signal whose inherited action is ``SIG_DFL`` gets the handler
    (:func:`_wants_cleanup_handler`): an ignored signal stays ignored and a
    Python handler stays the handler. Signal handlers can only be set from
    the main thread; elsewhere this returns False (the CLI calls it at
    start, so staging in a worker thread is covered). Idempotent.
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
            if _wants_cleanup_handler(signal.getsignal(signum)):
                signal.signal(signum, _cleanup_then_default)
        except (OSError, RuntimeError, ValueError):
            logger.debug("native_safe_path: no cleanup handler for %s", name)
    _HANDLERS_INSTALLED = True
    return True


def _register_session_folder(created: str) -> None:
    """Record a new session folder (caller holds :data:`_LOCK`, inside ``shutdown.guarded()``)."""
    global _ATEXIT_REGISTERED
    _SESSION_DIRS.append(created)
    if not _ATEXIT_REGISTERED:
        atexit.register(_at_exit)
        _ATEXIT_REGISTERED = True


def _unusable_base(base: str) -> str:
    """R15-6: why ``base`` cannot hold the session folder ("" when it can)."""
    if not base.isascii():
        return "경로에 ASCII가 아닌 문자가 있음"
    if not os.path.isdir(base):
        return "폴더가 없음"
    return ""


def _record_temp_location(created: str, base: str, skipped: list[tuple[str, str]]) -> None:
    """R15-6: remember where the session folder went; tell the operator (once) when it is not the folder they named.

    A first candidate (``DEEPFAKE_LENS_TMPDIR``, ``DEEPFAKE_LENS_NATIVE_TMPDIR``
    or the system temp folder) that could not be used used to send every
    temp file of the run — up to a 2 GB extraction — silently to ``/tmp``
    (``C:\\Windows\\Temp``). The notice goes to stderr once per process and
    the location is part of every scan result (``temp_folder``).
    """
    global _TEMP_NOTICE_SHOWN
    first = skipped[0] if skipped and skipped[0][0] != base else None
    if first is None:
        _TEMP_LOCATIONS[created] = {"fallback": False}
        return
    requested, reason = first
    _TEMP_LOCATIONS[created] = {"fallback": True, "base": base, "reason": reason, "override_env": TMP_ENV}
    logger.info("native_safe_path: temp folder %r unusable (%s); session folder in %s", requested, reason, base)
    if not _TEMP_NOTICE_SHOWN:
        _TEMP_NOTICE_SHOWN = True
        try:
            print(TEMP_FALLBACK_NOTICE.format(requested=repr(requested), reason=reason, base=base), file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass  # no usable stderr: the result still records it


def temp_location() -> dict[str, object]:
    """R15-6: where this run's temp files are made — ``{"fallback": False}``, or the fallback folder and why.

    The folder in use when one exists; otherwise what :func:`session_dir`
    would choose now (no folder is made).
    """
    with _LOCK:
        folder = _SESSION_DIR
        recorded = _TEMP_LOCATIONS.get(folder) if folder is not None else None
    if recorded is not None:
        return dict(recorded)
    candidates = _base_candidates()
    for index, base in enumerate(candidates):
        if not _unusable_base(base) and os.access(base, os.W_OK):
            if index == 0:
                return {"fallback": False}
            reason = _unusable_base(candidates[0]) or "쓸 수 없음"
            return {"fallback": True, "base": base, "reason": reason, "override_env": TMP_ENV}
    return {"fallback": False}


def session_dir() -> str:
    """The per-process staging folder (created on first use, ASCII path).

    R13-4: named ``<prefix><pid>-<random>``; stale folders of dead
    processes in the same base are swept first. R15-1: refused
    (:class:`ShuttingDown` "종료 중") once the process is ending — the
    existing folder is not handed out any more and no new one is made.
    """
    global _SESSION_DIR
    shutdown.refuse_if_active()
    with _LOCK, shutdown.guarded():
        if _SESSION_DIR is not None and os.path.isdir(_SESSION_DIR):
            return _SESSION_DIR
        skipped: list[tuple[str, str]] = []
        for base in _base_candidates():
            unusable = _unusable_base(base)
            if unusable:
                skipped.append((base, unusable))
                continue
            sweep_stale_sessions(base)
            try:
                created = tempfile.mkdtemp(prefix=f"{SESSION_PREFIX}{os.getpid()}-", dir=base)
            except OSError as exc:
                skipped.append((base, f"폴더를 만들 수 없음({type(exc).__name__})"))
                continue
            _SESSION_DIR = created
            _register_session_folder(created)
            _record_temp_location(created, base, skipped)
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


def scratch_dir() -> str:
    """R14-7 (round 14): the folder every temporary file and folder of this process is made in.

    The session folder (:func:`session_dir`) — so the atexit / signal
    cleanup and the next run's sweep of a dead process's folder (SIGKILL,
    power loss) remove them with the staged names; temp files made directly
    in TMPDIR (``tmp*.wav``, extraction folders, uploads) used to be left
    behind by a killed scan. When no ASCII temp folder exists for staging,
    a session folder is made in the system temp folder anyway (Python
    writes any name; only native decoders need ASCII) and cleaned up the
    same way. ``deepfake_lens/tests/test_native_path.py`` checks with an AST
    meta-test that every ``tempfile`` call in the package passes
    ``dir=scratch_dir()``.
    """
    global _SCRATCH_FALLBACK
    try:
        return session_dir()
    except NativePathError:
        pass
    with _LOCK, shutdown.guarded():  # R15-1: refused once the process is ending
        if _SCRATCH_FALLBACK is not None and os.path.isdir(_SCRATCH_FALLBACK):
            return _SCRATCH_FALLBACK
        base = tempfile.gettempdir()
        sweep_stale_sessions(base)
        created = tempfile.mkdtemp(prefix=f"{SESSION_PREFIX}{os.getpid()}-", dir=base)
        _SCRATCH_FALLBACK = created
        _register_session_folder(created)
    install_cleanup_handlers()
    return created


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


# R14-4: a registered folder is matched only as a whole path — never as the
# tail of a longer one ("/case/tmp/deepfake-lens-native-…" is not
# "/tmp/deepfake-lens-native-…").
_PATH_BEFORE = r"(?<![\w.~/\\-])"
# R14-4: what may follow a folder named on its own (end, space, quote, punctuation).
_PATH_AFTER = r"(?=$|[\s'\"`:;,()\[\]{}<>|])"


def _session_text_forms() -> list[str]:
    """R14-4: the session folders this process created, as given and resolved (longest first).

    Only these exact absolute paths — each made by :func:`session_dir` with
    ``mkdtemp`` directly in a temp base — are ever replaced in a message;
    an evidence folder that merely *looks* like a session folder
    ("deepfake-lens-native-7-ab") is text like any other.
    """
    with _LOCK:
        folders = list(_SESSION_DIRS)
    forms: set[str] = set()
    for folder in folders:
        forms.add(folder)
        if os.path.isabs(folder):
            try:
                forms.add(os.path.realpath(folder))  # a decoder may print the resolved path (TMPDIR a link)
            except (OSError, ValueError):
                pass
    return sorted(forms, key=len, reverse=True)


def _replace_staged_paths(text: str, folder: str) -> str:
    """Full staged paths under ``folder`` -> the originals, quoted the way the decoder quoted them.

    A decoder that quotes a path does it with ``repr()`` ("Error opening
    '/tmp/…/000001-….m4a'"): a quoted staged path becomes ``repr(original)``
    — what that decoder writes for the original name, and the form the scan
    cache rewrites quoted paths to (P2) — so a lone surrogate shows as
    ``\\udcc1`` exactly as in a Pillow message. An unquoted one becomes the
    original as given; a Windows ``repr()`` doubles the separators. A name
    in ``folder`` that is not (or no longer) registered becomes
    :data:`STAGED_NAME_PLACEHOLDER`, and ``folder`` named on its own
    :data:`STAGED_FOLDER_PLACEHOLDER` — ``folder`` is this process's own.
    """
    for escaped in (False, True):
        shown = folder.replace("\\", "\\\\") if escaped else folder
        if shown not in text:
            continue
        separator = r"(?:\\\\|/)" if escaped else r"[\\/]"
        pattern = re.compile(
            r"(?P<quote>['\"]?)" + _PATH_BEFORE + re.escape(shown) + separator + rf"(?P<name>{_STAGED_BASE})(?![\w.-])(?P=quote)"
        )

        def original(match: re.Match[str], escaped: bool = escaped) -> str:
            known = _alias(match.group("name"))
            if known is None:
                return match.group("quote") + STAGED_NAME_PLACEHOLDER + match.group("quote")
            if match.group("quote"):
                return repr(known)
            # An unquoted repr() text (rare): separators doubled the same way.
            return known.replace("\\", "\\\\") if escaped else known

        text = pattern.sub(original, text)
        text = re.sub(_PATH_BEFORE + re.escape(shown) + _PATH_AFTER, lambda _: STAGED_FOLDER_PLACEHOLDER, text)
    return text


def restore_original_names(text: str) -> str:
    """``text`` with every staged name replaced by the original path it stands for (R13-1).

    A full staged path becomes the original path as it was handed to
    :func:`native_safe_path` (``repr()``-quoted when the decoder quoted it);
    a bare staged base name becomes the original's base name — what the
    decoder would have printed for the original. R14-4 (round 14): only
    what this process registered is replaced — its own session folders (by
    their exact absolute paths) and its own staged names. There is no
    pattern replacement any more: an evidence folder named
    "deepfake-lens-native-7-ab" used to be shown as the staging-folder
    placeholder, and a scan root below such a folder lost its ``<root>/``.
    """
    if not text:
        return text
    text = _replace_short_paths(text)
    forms = _session_text_forms()
    if not any(form in text or form.replace("\\", "\\\\") in text for form in forms) and not _STAGED_NAME.search(text):
        return text
    for folder in forms:
        text = _replace_staged_paths(text, folder)

    def bare(match: re.Match[str]) -> str:
        known = _alias(match.group(1))
        if known is None:
            return match.group(0)  # not ours: an evidence file may be named like this
        return re.split(r"[\\/]", known)[-1] or known

    return _STAGED_NAME.sub(bare, text)


def _short_path_name(path: str) -> str | None:
    """R13-8: the Windows 8.3 short form of ``path`` when it is ASCII, else None.

    ``GetShortPathNameW`` returns the long path itself where 8.3 names are
    disabled (or a component has none); that is not ASCII and gives None.
    """
    if not _is_windows():
        return None
    import ctypes

    function = _kernel32().GetShortPathNameW
    # R14-8: with no buffer the call returns the size needed *including* the
    # terminating NUL; with a buffer, the characters written *excluding* it —
    # so a result >= the buffer size means the name grew in between (retry
    # is not worth it: no short name), and 0 is an error (GetLastError).
    needed = int(function(path, None, 0))
    if needed <= 0:
        return None
    buffer = ctypes.create_unicode_buffer(needed)
    written = int(function(path, buffer, needed))
    if written <= 0 or written >= needed:
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
            f"{COPY_CAP_EXCEEDED} — 비ASCII 이름의 파일에"
            + (" ASCII 짧은 이름(8.3)이 없어" if _is_windows() else " 심볼릭 링크를 만들 수 없어")
            + f" 사본이 필요하지만 크기({size}바이트)가 사본 상한({NATIVE_COPY_MAX_BYTES}바이트)을 넘어"
            " 네이티브 디코더로 열지 않았습니다"
        )
    shutil.copyfile(absolute, target)
    copied = os.lstat(target)
    # R14-2: the marker that lets removal clear this copy's read-only flag
    # (Windows) — it names the copy's own inode, so it never vouches for a
    # link or for any other file.
    with open(target + COPY_MARKER_SUFFIX, "x", encoding="ascii") as marker:
        marker.write(f"{copied.st_dev}:{copied.st_ino}")
    if not _is_windows():
        os.chmod(target, stat.S_IRUSR)  # a fresh regular file in our 0700 folder
    elif _can_chmod_without_following():
        os.chmod(target, stat.S_IRUSR, follow_symlinks=False)
    # (Windows without a no-follow chmod: the copy stays writable — removing a
    # read-only copy would need a chmod that could follow a link.)
    return target


def _stage(source: str) -> tuple[str, bool]:
    """An ASCII name for ``source`` and whether it is ours to remove afterwards.

    POSIX: symbolic link, copy. R13-8 / R14-2 — Windows: ASCII 8.3 short
    name (the evidence file itself; nothing to remove), copy. Never a hard
    link (R14-2: it modifies the evidence file's metadata).
    """
    absolute = os.path.abspath(source)
    folder = session_dir()
    target = os.path.join(folder, f"{next(_COUNTER):06d}-{uuid.uuid4().hex[:12]}{_ascii_suffix(source)}")
    _register_alias(target, source)
    # R15-1: the staged name is made inside a guarded step — refused once the
    # process is ending, and waited for by the cleanup when it started before.
    with shutdown.guarded():
        if not _is_windows():
            try:
                os.symlink(absolute, target)
                return target, True
            except OSError:
                shutdown.refuse_if_active()  # the folder went away under a final cleanup
                logger.debug("native_safe_path: symlink refused for staged name %s", target)
        else:
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
            _unlink_staged(staged, os.path.abspath(text))


def _try_unlink(path: str) -> bool:
    """``os.unlink`` that reports instead of raising (True when ``path`` is gone)."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _unlink_staged(staged: str, original: str | None = None) -> None:
    """Remove a staged name (and its copy marker); R14-2: never a mode change but on our own copy.

    The fallback used to ``chmod`` the staged name when its removal failed —
    through a symbolic link that is the evidence file. Now only a verified
    copy (:func:`_release_copy`; Windows read-only flag) is made writable.
    """
    if not _try_unlink(staged) and not (_release_copy(staged, original) and _try_unlink(staged)):
        logger.warning("native_safe_path: staged name %s not removed", staged)
        return
    try:
        os.unlink(staged + COPY_MARKER_SUFFIX)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("native_safe_path: copy marker of %s not removed", staged)


class CascadeLoadError(RuntimeError):
    """R14-5: a Haar cascade file exists but OpenCV could not load it (``str()`` is Korean)."""


def load_cascade(path: str | os.PathLike[str]) -> Any:
    """R14-5 (round 14): ``cv2.CascadeClassifier`` for any path — the file name goes through :func:`native_safe_path`.

    ``cv2.CascadeClassifier(path)`` takes a narrow ``char*`` name: under a
    Korean Windows install path (``C:\\Users\\김…\\site-packages\\cv2\\data``)
    it silently loads nothing (``empty()``), and the face checks then
    reported "얼굴 미검출" from a detector that never ran. An empty cascade
    raises :class:`CascadeLoadError`, so the check that needed it is
    ``failed``.
    """
    import cv2

    with native_safe_path(path) as native:
        cascade = cv2.CascadeClassifier(native)  # type: ignore[attr-defined]  # (OpenCV 5 stubs: contrib only)
    if cascade is None or cascade.empty():
        raise CascadeLoadError(f"Haar 얼굴 검출기 파일을 불러오지 못했습니다: {os.fspath(path)!r}")
    return cascade


def imwrite_any(path: str | os.PathLike[str], image: Any, params: list[int] | tuple[int, ...] = ()) -> bool:
    """R13-7 (round 13): ``cv2.imwrite`` for any path — encoded in memory, written by Python.

    ``cv2.imwrite`` takes a narrow ``char*`` name: a temp folder under a
    Korean Windows user name (``C:\\Users\\김…\\AppData\\Local\\Temp``) is not
    writable through it, and a non-UTF-8 POSIX TMPDIR crashes the process
    (SIGSEGV, like ``cv2.VideoCapture`` before R12-1). ``cv2.imencode`` takes
    the extension only; Python's ``open`` takes any name. False when the
    image cannot be encoded (as ``cv2.imwrite`` returns False).
    """
    import cv2

    suffix = os.path.splitext(os.fspath(path))[1] or ".png"
    ok, encoded = cv2.imencode(suffix, image, list(params))
    if not ok:
        return False
    with open(path, "wb") as handle:
        handle.write(encoded.tobytes())
    return True


def staged_names() -> list[str]:
    """Names currently in the session folder (for tests)."""
    with _LOCK:
        folder = _SESSION_DIR
    if folder is None or not os.path.isdir(folder):
        return []
    return sorted(os.listdir(folder))


__all__ = [
    "COPY_CAP_EXCEEDED",
    "CascadeLoadError",
    "NATIVE_COPY_MAX_BYTES",
    "NativePathError",
    "ShuttingDown",
    "cleanup_session",
    "imwrite_any",
    "install_cleanup_handlers",
    "is_native_safe",
    "load_cascade",
    "native_safe_path",
    "restore_original_names",
    "scratch_dir",
    "session_dir",
    "shutdown_session",
    "staged_names",
    "sweep_stale_sessions",
    "temp_location",
]
