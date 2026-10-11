"""Output files and the console output — written whole or reported in Korean (R18-15, round 18).

The verifier found three ways an output went wrong without a proper report:

- ``--json-out`` was written in place (``Path.write_text``): an interrupted
  or failed write left the previous report truncated, and a full disk
  (``/dev/full``) ended a whole scan with "예기치 않은 오류(OSError)";
- ``scan … | head`` ended with "예기치 않은 오류(BrokenPipeError)", exit 1;
- with standard output closed (``>&-``) the JSON was silently lost, exit 0.

:func:`write_output_text` / :func:`write_output_bytes` write a sibling
temporary file (exclusive create, so the usual umask permissions) and
``os.replace`` it over the target — the target holds the old report or the
new one, never half of one — and turn any ``OSError`` into
:class:`OutputWriteError` with the path and a Korean reason, which the CLI
prints as "출력 파일을 쓸 수 없습니다: <경로> (<사유>)". The other two cases
are handled in ``cli.main``.
"""

from __future__ import annotations

import errno
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# R18-15: the exit code of a command whose standard output reader went away
# (``| head``) — the shell's 128 + SIGPIPE, what a C tool killed by SIGPIPE reports.
BROKEN_PIPE_EXIT = 141
# R18-15: errnos of a standard output that cannot take the result at the final flush (full disk, quota, I/O error, closed descriptor).
OUTPUT_ERRNOS = frozenset(code for code in (errno.ENOSPC, getattr(errno, "EDQUOT", None), errno.EIO, errno.EBADF) if code is not None)
STDOUT_CLOSED_MESSAGE = "표준 출력이 닫혀 있어 결과를 출력할 수 없습니다(>&-) — 출력을 파일이나 터미널로 연결하십시오"
STDOUT_WRITE_FAILED = "결과를 표준 출력에 쓸 수 없습니다({reason})"
OUTPUT_FILE_FAILED = "출력 파일을 쓸 수 없습니다: {path} ({reason})"


class OutputWriteError(OSError):
    """R18-15: an output file could not be written; ``str()`` is the Korean line (the target is untouched)."""

    def __init__(self, path: Path | str, cause: OSError) -> None:
        from .error_text import read_error_ko, scrub_paths

        self.path = Path(path)
        self.cause = cause
        super().__init__(cause.errno, OUTPUT_FILE_FAILED.format(path=self.path, reason=scrub_paths(read_error_ko(cause))))

    def __str__(self) -> str:
        return str(self.strerror)


def write_output_bytes(path: Path | str, data: bytes) -> None:
    """Write ``data`` to ``path`` whole (temp file + ``os.replace``) — :class:`OutputWriteError` on failure.

    A symbolic link is followed (its target is replaced, as a plain write
    did); a target that exists and is not a regular file (``/dev/stdout``, a
    FIFO, ``/dev/full``) is written in place — renaming over it would
    replace the device or pipe itself.
    """
    target = Path(path)
    if target.is_symlink():
        target = Path(os.path.realpath(target))
    try:
        special = target.exists() and not target.is_file()
    except OSError:
        special = False
    if special:
        try:
            with open(target, "wb") as handle:
                handle.write(data)
                handle.flush()
        except OSError as exc:
            raise OutputWriteError(path, exc) from exc
        return
    temp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except BaseException as exc:
        # R19-6 (round 19): every exception removes the temp file — a Ctrl-C (KeyboardInterrupt) or a
        # SIGTERM's SystemExit during the write left ".<name>.<pid>.<id>.tmp" next to the output.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise OutputWriteError(target, exc) from exc
        raise


def write_output_text(path: Path | str, text: str) -> None:
    """:func:`write_output_bytes` of UTF-8 ``text``."""
    write_output_bytes(path, text.encode("utf-8"))


def stdout_is_closed() -> bool:
    """R18-15: True when the process has no usable standard output (started with ``>&-``: ``sys.stdout`` is None)."""
    stream = sys.stdout
    if stream is None:
        return True
    try:
        descriptor = stream.fileno()
    except (OSError, ValueError, AttributeError):
        return False  # a stream without a descriptor (a test's StringIO) is usable
    try:
        os.fstat(descriptor)
    except OSError:
        return True
    return False


def silence_stdout() -> None:
    """R18-15: point the standard output at the null device — nothing more is written to a reader that went away."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, sys.stdout.fileno())
        os.close(null)
    except (OSError, ValueError, AttributeError):
        pass


class StdoutWriteError(OSError):
    """R18-15: writing the result to standard output failed (full disk behind ``>``, closed descriptor …)."""

    def __init__(self, cause: OSError) -> None:
        from .error_text import read_error_ko

        self.cause = cause
        super().__init__(cause.errno, STDOUT_WRITE_FAILED.format(reason=read_error_ko(cause)))

    def __str__(self) -> str:
        return str(self.strerror)


class StdoutGuard:
    """R18-15: ``sys.stdout`` for the length of a command — a failed write is a :class:`StdoutWriteError`.

    A :class:`BrokenPipeError` (the reader left) passes through as it is.
    Everything else is the wrapped stream's.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        try:
            return int(self._stream.write(text))
        except BrokenPipeError:
            raise
        except OSError as exc:
            raise StdoutWriteError(exc) from exc

    def flush(self) -> None:
        try:
            self._stream.flush()
        except BrokenPipeError:
            raise
        except OSError as exc:
            raise StdoutWriteError(exc) from exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)
