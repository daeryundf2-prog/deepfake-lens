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
folder at interpreter exit. When no route works (copy over the cap, no
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
import uuid
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
            atexit.register(shutil.rmtree, created, True)
            return created
        raise NativePathError(
            "비ASCII 파일 이름을 네이티브 디코더에 넘길 ASCII 임시 폴더가 없습니다"
            f"(환경 변수 {NATIVE_TMP_ENV}로 ASCII 경로의 폴더를 지정하십시오)"
        )


def _stage(source: str) -> str:
    folder = session_dir()
    target = os.path.join(folder, f"{next(_COUNTER):06d}-{uuid.uuid4().hex[:12]}{_ascii_suffix(source)}")
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
    "session_dir",
    "staged_names",
]
