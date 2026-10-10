#!/usr/bin/env python3
"""Run a command with SIGINT / SIGTERM / SIGHUP at their default action (R15-4, round 15).

A test run started as a background job of a shell without job control, or
under ``nohup``, inherits SIGINT (SIGHUP) as *ignored*; an ignored signal
survives exec, and since R14-1 the scan rightly leaves it ignored — so a
test that signals its child process slept through the signal (the R14-1 …
R14-5 commits failed their own SIGINT test that way, see
``docs/KNOWN-HISTORICAL-ISSUES.md``). The QA harness and CI run the suite
through this script:

    python scripts/with_default_signals.py -- python -m unittest discover deepfake_lens/tests

This process is single-threaded when it resets the dispositions, then it
``exec``-s the command (``deepfake_lens.shutdown.with_default_signals`` is
the same for a ``subprocess.Popen`` argv). Exit code: the command's;
R16-15 (round 16): a command that cannot be started (not found, not
executable) is a Korean error on stderr and exit 127 (126 when it exists but
cannot be executed) — the shell's codes — instead of an English traceback.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.shutdown import HARNESS_DEFAULT_SIGNALS  # noqa: E402

# R16-15: the shell's exit codes for a command that was not found / cannot be executed (POSIX sh, "Exit Status").
NOT_FOUND_EXIT = 127
NOT_EXECUTABLE_EXIT = 126


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    import argparse

    parser = KoreanArgumentParser(description="중단·종료·회선 끊김 신호를 기본 동작으로 되돌린 뒤 명령을 실행합니다(R15-4: 무시된 신호를 물려받은 하네스에서도 신호 시험이 같게 동작).")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="실행할 명령(앞에 -- 를 붙임)")
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("실행할 명령이 없습니다")
    for name in HARNESS_DEFAULT_SIGNALS:
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, signal.SIG_DFL)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        if os.name == "nt":  # no exec on Windows: run and pass the exit code on
            import subprocess

            return subprocess.call(command)
        os.execvp(command[0], command)
    except FileNotFoundError:
        print(f"오류: 실행할 명령을 찾을 수 없습니다: {command[0]!r} (PATH에 없거나 경로가 틀림)", file=sys.stderr)
        return NOT_FOUND_EXIT
    except PermissionError:
        print(f"오류: 명령을 실행할 권한이 없습니다: {command[0]!r}", file=sys.stderr)
        return NOT_EXECUTABLE_EXIT
    except OSError as exc:
        from deepfake_lens.error_text import read_error_ko

        print(f"오류: 명령을 실행할 수 없습니다: {command[0]!r} — {read_error_ko(exc)}", file=sys.stderr)
        return NOT_EXECUTABLE_EXIT
    return NOT_FOUND_EXIT  # not reached (exec replaced this process)


if __name__ == "__main__":
    raise SystemExit(main())
