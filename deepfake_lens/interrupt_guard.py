"""R17-8 (round 17): Ctrl-C while the CLI is still importing — a Korean line and exit 130, never a traceback.

``deepfake_lens/__init__`` imports most of the package (≈0.35 s); a Ctrl-C
in that window raised KeyboardInterrupt before ``cli.main`` could catch it,
so the console script (and ``python -m deepfake_lens``) ended with an
English traceback and a SIGINT death. When the package is imported for the
CLI (the program is ``deepfake-lens``, or ``python -m`` is still locating
its module), :func:`install` replaces Python's default SIGINT handler for
the import: nothing exists yet that needs a cleanup, so the handler writes
:data:`INTERRUPTED_MESSAGE` and ends the process with
:data:`INTERRUPTED_EXIT`. ``cli.main`` calls :func:`release` first, after
which a Ctrl-C is a KeyboardInterrupt again and runs the R16-13 cleanup.
An inherited SIG_IGN (a background job, R14-1) is left alone.
Standard library only — imported first by the package.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from typing import Any

INTERRUPTED_MESSAGE = "중단됨(사용자 요청)"
INTERRUPTED_EXIT = 130
# The console script's names (pip writes deepfake-lens; on Windows .exe / -script.py).
CLI_PROGRAM_NAMES = frozenset({"deepfake-lens", "deepfake-lens.exe", "deepfake-lens-script.py"})


def importing_for_the_cli() -> bool:
    """True when this process imports the package to run the CLI."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 == "-m":  # `python -m deepfake_lens`: the package is imported while argv[0] is still "-m"
        return True
    return os.path.basename(argv0) in CLI_PROGRAM_NAMES


def _interrupted_while_importing(signum: int, frame: Any) -> None:
    try:
        os.write(2, (INTERRUPTED_MESSAGE + "\n").encode("utf-8"))
    except OSError:
        pass
    os._exit(INTERRUPTED_EXIT)


def install() -> bool:
    """Guard the import (CLI only, main thread, SIGINT at Python's default handler). True when installed."""
    if not importing_for_the_cli() or threading.current_thread() is not threading.main_thread():
        return False
    try:
        if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
            return False
        signal.signal(signal.SIGINT, _interrupted_while_importing)
    except (OSError, ValueError):
        return False
    return True


def release() -> None:
    """From here a Ctrl-C is a KeyboardInterrupt again (``cli.main``: the cleanup runs, then exit 130)."""
    try:
        if signal.getsignal(signal.SIGINT) is _interrupted_while_importing:
            signal.signal(signal.SIGINT, signal.default_int_handler)
    except (OSError, ValueError):
        pass
