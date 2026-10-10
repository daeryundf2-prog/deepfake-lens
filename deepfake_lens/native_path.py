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
``<n>-<random><.ext>``:

1. a symbolic link to the original (POSIX) — it does not touch the
   evidence file's inode at all (a hard link would change its ``ctime``
   and link count, which an examiner may later have to explain);
2. otherwise a hard link (the R12-1 required fix; same volume only);
3. otherwise a read-only copy, only up to :data:`NATIVE_COPY_MAX_BYTES`.

The staged name is removed when the ``with`` block ends and the session
folder at interpreter exit.

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
staging name can appear in any output. When no route works (copy over the cap, no
ASCII temp folder) :class:`NativePathError` is raised with a Korean
message, so the check that needed the decoder is recorded as ``failed`` —
never silently skipped, never handed the raw name.

``deepfake_lens/tests/test_native_path.py`` holds the AST meta-test that
every native call site in the package is inside ``with
native_safe_path(...) as <name>:`` and passes ``<name>``.
"""

from __future__ import annotations

import atexit
import itertools
import logging
import os
import shutil
import stat
import tempfile
import threading
import re
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

# Largest file copied when neither a symbolic nor a hard link can be made.
# Same figure as the archive extraction budget (phase-0 spec WP-H, G34:
# "총 바이트 2 GB") — the most this tool ever writes to temp for one input.
NATIVE_COPY_MAX_BYTES = 2 * 1024 * 1024 * 1024
# Operator override for the staging base folder (must itself be ASCII).
NATIVE_TMP_ENV = "DEEPFAKE_LENS_NATIVE_TMPDIR"
SESSION_PREFIX = "deepfake-lens-native-"
# Extensions longer than this are not real container suffixes; dropped.
MAX_SUFFIX_CHARS = 12

_LOCK = threading.Lock()
_SESSION_DIR: str | None = None
_COUNTER = itertools.count(1)

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
_SESSION_PATH = re.compile(re.escape(SESSION_PREFIX) + rf"[A-Za-z0-9_]+(?:(?:\\\\|[\\/])({_STAGED_BASE})(?![\w.-]))?")
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


def session_dir() -> str:
    """The per-process staging folder (created on first use, ASCII path)."""
    global _SESSION_DIR
    with _LOCK:
        if _SESSION_DIR is not None and os.path.isdir(_SESSION_DIR):
            return _SESSION_DIR
        for base in _base_candidates():
            if not base.isascii() or not os.path.isdir(base):
                continue
            try:
                created = tempfile.mkdtemp(prefix=SESSION_PREFIX, dir=base)
            except OSError:
                continue
            _SESSION_DIR = created
            _SESSION_DIRS.append(created)
            atexit.register(shutil.rmtree, created, True)
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
    if not text or (SESSION_PREFIX not in text and not _STAGED_NAME.search(text)):
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


def _stage(source: str) -> str:
    folder = session_dir()
    target = os.path.join(folder, f"{next(_COUNTER):06d}-{uuid.uuid4().hex[:12]}{_ascii_suffix(source)}")
    _register_alias(target, source)
    absolute = os.path.abspath(source)
    if os.name != "nt":
        try:
            os.symlink(absolute, target)
            return target
        except OSError:
            logger.debug("native_safe_path: symlink refused for staged name %s", target)
    try:
        os.link(absolute, target)
        return target
    except OSError:
        logger.debug("native_safe_path: hard link refused for staged name %s", target)
    try:
        size = os.stat(absolute).st_size
    except OSError as exc:
        raise NativePathError(f"비ASCII 이름의 파일을 열 수 없습니다({type(exc).__name__})") from exc
    if size > NATIVE_COPY_MAX_BYTES:
        raise NativePathError(
            "비ASCII 이름의 파일을 링크할 수 없고 크기가 사본 상한"
            f"({NATIVE_COPY_MAX_BYTES}바이트)을 넘어 네이티브 디코더로 열지 않았습니다"
        )
    shutil.copyfile(absolute, target)
    os.chmod(target, stat.S_IRUSR)
    return target


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
    staged = _stage(text)
    try:
        yield staged
    finally:
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
    "NATIVE_COPY_MAX_BYTES",
    "NativePathError",
    "is_native_safe",
    "native_safe_path",
    "restore_original_names",
    "session_dir",
    "staged_names",
]
