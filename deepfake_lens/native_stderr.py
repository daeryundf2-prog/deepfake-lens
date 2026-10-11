"""Native-library stderr goes to the log file, not the examiner's console (G14).

Decoders written in C print straight to file descriptor 2, bypassing Python's
``sys.stderr`` and ``logging``: OpenCV's FFmpeg backend
(``[mov,mp4,m4a,3gp,3g2,mj2 @ 0x…] moov atom not found``) and mpg123 inside
libsndfile / librosa (``Note: Illegal Audio-MPEG-Header …``). A broken
evidence file is already recorded in the row's coverage with a Korean
reason; its English decoder chatter belongs in the log.

:func:`native_stderr_to_log` (a context manager, also usable as a
decorator) points fd 2 at ``<log dir>/native-stderr.log`` for the duration of
a decoding call — ``$DEEPFAKE_LENS_LOG_DIR`` or ``~/.cache/deepfake-lens/logs``,
the same folder as the CLI log, never the evidence folder.

fd 2 is process-wide, so the redirect is reference-counted across threads
(the first caller redirects, the last one restores). While it is active,
Python-level writes to the real ``sys.stderr`` (CLI warnings, progress from
another thread) are rerouted to a duplicate of the original fd 2, so only
native output is captured. When ``sys.stderr`` is not the process stderr
(a test's ``redirect_stderr``), it is left alone.
"""

from __future__ import annotations

import functools
import io
import logging
import os
import sys
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from .cli_logging import default_log_dir

NATIVE_STDERR_LOG = "native-stderr.log"
# Every ffmpeg invocation (G14): errors only, no banner, no progress stats.
# R16-6 (round 16): -nostdin — ffmpeg never reads (or puts into raw mode) a
# terminal; a scan SIGKILLed during an ffmpeg run left the operator's
# terminal with echo off. shutdown.run_child also gives children /dev/null
# as stdin.
FFMPEG_QUIET_ARGS = ("-nostdin", "-hide_banner", "-loglevel", "error", "-nostats")
# R17-4 (round 17): what ffmpeg prints (at -loglevel error) when the input
# has no stream of the kind asked for — the only failure that means "no audio
# track". ffmpeg ≤ 6 says "Output file #0 does not contain any stream" (exit 1),
# 7.x "Output file does not contain any stream" (exit 234); "-map 0:a" says
# "Stream map '0:a' matches no streams".
FFMPEG_NO_STREAM_MARKERS = ("does not contain any stream", "matches no streams")
# R17-4: how much of ffmpeg's last stderr line a failure reason quotes.
FFMPEG_ERROR_LINE_MAX_CHARS = 160


class FfmpegError(RuntimeError):
    """R17-4: ffmpeg could not run or failed — never read as "no audio track"."""


def ffmpeg_found_no_stream(stderr: bytes | str | None) -> bool:
    """R17-4: True when ffmpeg's stderr says the input has no stream of the requested kind."""
    if not stderr:
        return False
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    return any(marker in text for marker in FFMPEG_NO_STREAM_MARKERS)


def ffmpeg_error(returncode: int, stderr: bytes | str | None) -> FfmpegError:
    """R17-4: the failure of an ffmpeg run that is not "no stream" — exit code or signal, and its last error line."""
    import signal

    if returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = str(-returncode)
        cause = f"ffmpeg가 신호 {name}로 종료됨"
    elif returncode in (126, 127):
        cause = f"ffmpeg를 실행하지 못함(종료 코드 {returncode})"
    else:
        cause = f"ffmpeg 실패(종료 코드 {returncode})"
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        # ffmpeg's own (English) words go to the log; the reason stays Korean (B6).
        logger.warning("%s: %s", cause, lines[-1][:FFMPEG_ERROR_LINE_MAX_CHARS])
        cause += " — 상세는 로그 참조"
    return FfmpegError(cause)

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_DEPTH = 0
_SAVED_FD: int | None = None
_LOG_FD: int | None = None
_SAVED_PYTHON_STDERR: Any = None

F = TypeVar("F", bound=Callable[..., Any])


def native_log_path() -> str:
    return str(default_log_dir() / NATIVE_STDERR_LOG)


def _python_stderr_is_fd2() -> bool:
    try:
        return sys.stderr is not None and sys.stderr.fileno() == 2
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return False


def _enter() -> None:
    global _DEPTH, _SAVED_FD, _LOG_FD, _SAVED_PYTHON_STDERR
    with _LOCK:
        _DEPTH += 1
        if _DEPTH > 1:
            return
        try:
            directory = default_log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            log_fd = os.open(str(directory / NATIVE_STDERR_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        except OSError:
            # No writable log folder: discard rather than print to the console.
            try:
                log_fd = os.open(os.devnull, os.O_WRONLY)
            except OSError:
                logger.warning("native stderr could not be redirected")
                return
        try:
            if _python_stderr_is_fd2():
                sys.stderr.flush()
            saved = os.dup(2)
            os.dup2(log_fd, 2)
        except OSError:
            os.close(log_fd)
            logger.warning("native stderr could not be redirected", exc_info=True)
            return
        _SAVED_FD, _LOG_FD = saved, log_fd
        if _python_stderr_is_fd2():
            # Python-level messages stay on the console: write them to the
            # saved original fd 2 while native output goes to the log.
            _SAVED_PYTHON_STDERR = sys.stderr
            sys.stderr = io.TextIOWrapper(
                io.FileIO(os.dup(saved), "w", closefd=True),
                encoding=getattr(sys.stderr, "encoding", None) or "utf-8",
                errors="replace",
                line_buffering=True,
            )


def _exit() -> None:
    global _DEPTH, _SAVED_FD, _LOG_FD, _SAVED_PYTHON_STDERR
    with _LOCK:
        _DEPTH = max(0, _DEPTH - 1)
        if _DEPTH or _SAVED_FD is None:
            return
        if _SAVED_PYTHON_STDERR is not None:
            wrapper, sys.stderr = sys.stderr, _SAVED_PYTHON_STDERR
            _SAVED_PYTHON_STDERR = None
            try:
                wrapper.flush()
                wrapper.close()
            except (OSError, ValueError):
                pass
        try:
            os.dup2(_SAVED_FD, 2)
        finally:
            os.close(_SAVED_FD)
            if _LOG_FD is not None:
                os.close(_LOG_FD)
            _SAVED_FD = _LOG_FD = None


@contextmanager
def native_stderr_to_log() -> Iterator[None]:
    """Send native (fd 2) stderr to the native-stderr log while the block runs."""
    _enter()
    try:
        yield
    finally:
        _exit()


def quiet_native_stderr(func: F) -> F:
    """Decorator form of :func:`native_stderr_to_log` for decoding functions."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with native_stderr_to_log():
            return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


__all__ = ["FFMPEG_QUIET_ARGS", "NATIVE_STDERR_LOG", "native_log_path", "native_stderr_to_log", "quiet_native_stderr"]
