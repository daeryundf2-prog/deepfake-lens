"""CLI logging: tracebacks to a log file, one Korean line on stderr (N8).

Without any logging configuration, Python's last-resort handler printed
every ``logger.exception`` — a full traceback for each corrupt evidence
file (truncated JPEG, garbage WAV, broken DOCX…) — to stderr, burying the
CLI's own output. A failed check is already recorded in the row's
coverage; its traceback is for investigation, not for the examiner's
console. :func:`configure_cli_logging` sends records to a log file
(``$DEEPFAKE_LENS_LOG_DIR`` or ``~/.cache/deepfake-lens/logs``, never the
evidence folder) and counts errors; :meth:`CliLogging.finish` prints one
Korean line naming the count and the log file. ``--verbose`` also shows
every record with its traceback on stderr.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

LOG_DIR_ENV = "DEEPFAKE_LENS_LOG_DIR"
LOG_FILE_NAME = "deepfake-lens.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
PACKAGE_LOGGER = "deepfake_lens"


def default_log_dir() -> Path:
    env = os.environ.get(LOG_DIR_ENV)
    return Path(env).expanduser() if env else Path.home() / ".cache" / "deepfake-lens" / "logs"


class _ErrorCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


@dataclass
class CliLogging:
    """The handlers one CLI run installed; :meth:`finish` removes them."""

    verbose: bool
    log_path: Path | None
    counter: _ErrorCounter
    handlers: list[logging.Handler] = field(default_factory=list)
    previous_level: int = logging.NOTSET
    previous_propagate: bool = True
    previous_capture: bool = False
    previous_warnings_propagate: bool = True
    # R10-5: errors logged for the command as a whole (cli.main's handler),
    # not for a file — they produced no row and are not "per-row" errors.
    command_errors: int = 0

    def log_hint(self) -> str:
        """Where the traceback went: "로그 파일 <path>을(를) 확인하십시오" (R10-5)."""
        if self.log_path is None:
            return "로그 파일을 열 수 없어 트레이스백은 기록되지 않았습니다(--verbose 로 화면에 표시)"
        return f"로그 파일 {self.log_path}을(를) 확인하십시오"

    def finish(self, stream: TextIO | None = None) -> None:
        """Print the one-line summary (if anything failed) and uninstall."""
        stream = stream if stream is not None else sys.stderr
        package = logging.getLogger(PACKAGE_LOGGER)
        for handler in self.handlers:
            package.removeHandler(handler)
            logging.getLogger("py.warnings").removeHandler(handler)
            handler.close()
        package.setLevel(self.previous_level)
        package.propagate = self.previous_propagate
        logging.getLogger("py.warnings").propagate = self.previous_warnings_propagate
        logging.captureWarnings(self.previous_capture)
        # R10-5: the line speaks of per-row errors recorded in each row's
        # coverage; a command that failed before producing rows has none
        # (its own error line names the log file).
        row_errors = self.counter.count - self.command_errors
        if row_errors > 0 and not self.verbose:
            where = f"로그 파일 {self.log_path}" if self.log_path is not None else "로그 파일을 열 수 없어 기록되지 않았습니다"
            print(
                f"참고: 판독 불가·손상 파일 등 처리 오류 {row_errors}건 — 각 행의 검사 범위(coverage)에 사유가 기록되었고, "
                f"상세 트레이스백은 {where}" + ("에 기록했습니다" if self.log_path is not None else "") + "(--verbose 로 화면에도 표시).",
                file=stream,
            )


def configure_cli_logging(verbose: bool = False, *, log_dir: Path | None = None) -> CliLogging:
    """Route package logging to a log file (+ stderr with ``verbose``) for one CLI run."""
    package = logging.getLogger(PACKAGE_LOGGER)
    counter = _ErrorCounter()
    setup = CliLogging(
        verbose=verbose, log_path=None, counter=counter,
        previous_level=package.level, previous_propagate=package.propagate,
        previous_capture=bool(getattr(logging, "_warnings_showwarning", None)),
    )
    handlers: list[logging.Handler] = [counter]
    directory = log_dir if log_dir is not None else default_log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # R11-1: a log line naming a non-UTF-8 file (lone surrogates, PEP 383)
        # is written with backslash escapes instead of "--- Logging error ---".
        file_handler = logging.FileHandler(directory / LOG_FILE_NAME, encoding="utf-8", errors="backslashreplace")
    except OSError:
        file_handler = None
    if file_handler is not None:
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handlers.append(file_handler)
        setup.log_path = directory / LOG_FILE_NAME
    if verbose:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        handlers.append(console)
    for handler in handlers:
        package.addHandler(handler)
    # Library warnings (librosa/audioread fallbacks, …) follow the same route.
    logging.captureWarnings(True)
    warnings_logger = logging.getLogger("py.warnings")
    setup.previous_warnings_propagate = warnings_logger.propagate
    for handler in handlers[1:]:
        warnings_logger.addHandler(handler)
    warnings_logger.propagate = False
    package.setLevel(logging.INFO)
    # The package's records stop here: no last-resort traceback on stderr.
    package.propagate = False
    setup.handlers = handlers
    return setup
