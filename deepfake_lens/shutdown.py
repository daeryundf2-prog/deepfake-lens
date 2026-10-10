"""Process shutdown: a "종료 중" flag, tracked child processes (R15-1, round 15).

A SIGTERM / SIGHUP during a scan used to leave the session folder of
:mod:`deepfake_lens.native_path` behind (verifier: 20 of 40 runs under load,
6 of 20 idle) for three reasons:

1. the worker threads went on staging names and making temp files while the
   handler removed the folder, so the removal failed (only logged);
2. the cleanup reset the session folder to "none", so the next staging call
   of a worker made a *new* session folder that nobody removed;
3. the ``ffmpeg`` children were never stopped: ``-y`` re-created
   ``tmp*.wav`` in the folder, and they kept running after the scan ended
   (after SIGKILL as well).

This module holds what the cleanup needs to make that impossible:

- :func:`begin` sets the "종료 중" flag. From then on every step that would
  create something the cleanup must remove — a session folder, a staged
  name, a child process — raises :class:`ShuttingDown` (a
  :class:`~deepfake_lens.checks.CheckSkipped`, so the check that asked is
  recorded ``skipped`` "종료 중"). Such a step runs inside :func:`guarded`;
  :func:`begin` waits (bounded) until no *other* thread is inside one, so
  everything created before the flag is registered before the cleanup looks.
- every child process is started through :func:`run_child` (the AST
  meta-test in ``tests/test_shutdown.py`` keeps it that way) and tracked
  until it ends; :func:`begin` stops each one: ``terminate`` → wait →
  ``kill`` → wait.
- on Linux each child is also bound to this process with
  ``PR_SET_PDEATHSIG`` (SIGKILL): a SIGKILLed scan, which runs no handler at
  all, takes its ``ffmpeg`` children with it. ``preexec_fn`` is not
  fork-safe in a threaded process (R15-5), so the child is started through
  a tiny exec trampoline (``python -I -S -c …``) that sets the flag and then
  ``execv``-s the real program; the trampoline also checks that its parent
  is still this process (a parent killed before ``prctl`` ran is not seen
  by the flag). Elsewhere (macOS, Windows, a frozen build without a Python
  interpreter) the tracked children are stopped by the handler and the
  atexit hook; a hard kill of the parent leaves them to their own timeout.

Signal-handler safety: :func:`begin` runs inside a signal handler in the
main thread, which may have been interrupted while holding any lock — so it
takes no lock at all (it only reads dicts copied atomically under the GIL
and sets a module-level bool).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import IO, Any, Iterator, Sequence

from .check_skipped import CheckSkipped

logger = logging.getLogger(__name__)

# R15-1: the coverage reason of a check refused because the process is ending.
SHUTDOWN_REASON = "종료 중"
# R15-1: how long :func:`begin` waits for other threads to leave a guarded
# step (creating a folder, a staged name, starting a child) — each is a few
# syscalls; one second covers a thread descheduled on a loaded machine.
PENDING_SETTLE_SECONDS = 1.0
# R15-1: how long a child gets after SIGTERM before SIGKILL, and after SIGKILL
# before the cleanup goes on without it. ffmpeg ends within milliseconds of
# SIGTERM; two seconds is the same order as systemd's stop-sigterm margin for
# a helper, and keeps a stuck child from holding the shutdown for long.
CHILD_TERM_GRACE_SECONDS = 2.0
CHILD_KILL_GRACE_SECONDS = 2.0
# Poll interval while waiting for the above.
_POLL_SECONDS = 0.005
# R15-1: <linux/prctl.h> PR_SET_PDEATHSIG.
_PR_SET_PDEATHSIG = 1
# R15-1: the exec trampoline (Linux). argv: <parent pid> <program> <args…>.
# Python itself ignores SIGPIPE (and SIGXFSZ) at start-up and an ignored
# signal survives exec, so both are put back to their default first — what
# ``subprocess``'s restore_signals does for a direct exec.
PDEATHSIG_TRAMPOLINE = (
    "import ctypes, os, signal, sys\n"
    "try:\n"
    f"    ctypes.CDLL(None, use_errno=True).prctl({_PR_SET_PDEATHSIG}, int(signal.SIGKILL), 0, 0, 0)\n"
    "except (OSError, AttributeError):\n"
    "    pass\n"
    "if os.getppid() != int(sys.argv[1]):\n"
    "    os._exit(1)\n"
    "for name in ('SIGPIPE', 'SIGXFSZ'):\n"
    "    if hasattr(signal, name):\n"
    "        signal.signal(getattr(signal, name), signal.SIG_DFL)\n"
    "os.execv(sys.argv[2], sys.argv[2:])\n"
)


class ShuttingDown(CheckSkipped):
    """R15-1: refused — the process is ending ("종료 중"); recorded as a ``skipped`` check."""

    def __init__(self, reason: str = SHUTDOWN_REASON) -> None:
        super().__init__(reason)


_SHUTTING_DOWN = False
# thread ident -> depth of guarded steps it is inside (modified under the lock,
# read without it by begin()).
_PENDING: dict[int, int] = {}
_PENDING_LOCK = threading.Lock()
# pid -> Popen of every child started by run_child that has not been waited for.
_CHILDREN: dict[int, subprocess.Popen[Any]] = {}
_CHILDREN_LOCK = threading.Lock()


def active() -> bool:
    """True once :func:`begin` has run (the process is ending)."""
    return _SHUTTING_DOWN


def refuse_if_active() -> None:
    """Raise :class:`ShuttingDown` when the process is ending."""
    if _SHUTTING_DOWN:
        raise ShuttingDown()


@contextmanager
def guarded() -> Iterator[None]:
    """A step that creates something the cleanup must see (R15-1).

    Raises :class:`ShuttingDown` when the process is ending; otherwise the
    step runs while :func:`begin` knows this thread is inside it, and the
    flag is checked again when it ends — so a step that started just before
    the flag either finishes before the cleanup looks or is refused.
    """
    me = threading.get_ident()
    with _PENDING_LOCK:
        _PENDING[me] = _PENDING.get(me, 0) + 1
    try:
        refuse_if_active()
        yield
    finally:
        with _PENDING_LOCK:
            depth = _PENDING.get(me, 1) - 1
            if depth > 0:
                _PENDING[me] = depth
            else:
                _PENDING.pop(me, None)


def _others_pending(me: int) -> bool:
    return any(ident != me for ident in list(_PENDING))


def _child_gone(proc: subprocess.Popen[Any]) -> bool:
    """True when ``proc`` has ended (reaped, or a zombie no one has reaped yet)."""
    if proc.poll() is not None:
        return True
    # poll() cannot reap while another thread waits on the child (it holds the
    # Popen's wait lock); /proc tells a zombie from a running process (Linux).
    try:
        with open(f"/proc/{proc.pid}/stat", "rb") as handle:
            state = handle.read().rsplit(b")", 1)[-1].split()[0:1]
    except OSError:
        # No such /proc entry: reaped (gone). No /proc at all: only poll() can tell.
        return os.path.isdir("/proc/self")
    return state in ([b"Z"], [b"X"])


def _wait_all(procs: list[subprocess.Popen[Any]], seconds: float) -> list[subprocess.Popen[Any]]:
    deadline = time.monotonic() + seconds
    left = [proc for proc in procs if not _child_gone(proc)]
    while left and time.monotonic() < deadline:
        time.sleep(_POLL_SECONDS)
        left = [proc for proc in left if not _child_gone(proc)]
    return left


def _signal_child(proc: subprocess.Popen[Any], kill: bool) -> None:
    try:
        if kill:
            proc.kill()
        else:
            proc.terminate()
    except OSError:  # already gone
        pass


def stop_children(term_grace: float = CHILD_TERM_GRACE_SECONDS, kill_grace: float = CHILD_KILL_GRACE_SECONDS) -> list[int]:
    """Stop every tracked child: terminate → wait → kill → wait. Returns the pids still not gone."""
    procs = list(_CHILDREN.values())
    for proc in procs:
        _signal_child(proc, kill=False)
    left = _wait_all(procs, term_grace)
    for proc in left:
        _signal_child(proc, kill=True)
    left = _wait_all(left, kill_grace)
    for proc in left:
        logger.warning("shutdown: child process %s did not end", proc.pid)
    return [proc.pid for proc in left]


def begin(settle: float = PENDING_SETTLE_SECONDS) -> None:
    """R15-1: the process is ending — refuse new folders, names and children; stop the running children.

    Safe in a signal handler (no lock is taken). Idempotent.
    """
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    me = threading.get_ident()
    deadline = time.monotonic() + settle
    while _others_pending(me) and time.monotonic() < deadline:
        time.sleep(_POLL_SECONDS)
    stop_children()


def running_children() -> list[int]:
    """The pids of the tracked children that have not been waited for (tests)."""
    return sorted(_CHILDREN)


def _resolve_program(program: str) -> str:
    """The absolute path ``program`` names (PATH search for a bare name), as ``subprocess`` would find it."""
    if os.path.dirname(program):
        return os.path.abspath(program)
    found = shutil.which(program)
    if found is None:
        raise FileNotFoundError(2, "No such file or directory", program)
    return os.path.abspath(found)


def _bind_to_parent(argv: list[str]) -> list[str]:
    """R15-1: Linux — ``argv`` started through the PR_SET_PDEATHSIG trampoline; elsewhere unchanged."""
    if not sys.platform.startswith("linux") or getattr(sys, "frozen", False) or not sys.executable:
        return argv
    return [sys.executable, "-I", "-S", "-c", PDEATHSIG_TRAMPOLINE, str(os.getpid()), *argv]


def run_child(
    args: Sequence[str | os.PathLike[str]],
    *,
    capture_output: bool = False,
    stdin: int | IO[Any] | None = None,
    stdout: int | IO[Any] | None = None,
    stderr: int | IO[Any] | None = None,
    text: bool = False,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run`` for a tracked child (R15-1): refused while ending, stopped by :func:`begin`.

    Same arguments and result as ``subprocess.run`` for what this package
    uses. A child stopped because the process is ending raises
    :class:`ShuttingDown` instead of returning its (killed) result.
    """
    argv = [os.fspath(part) for part in args]
    if not argv:
        raise ValueError("run_child: empty command")
    if capture_output:
        stdout = stderr = subprocess.PIPE
    launch = _bind_to_parent([_resolve_program(argv[0]), *argv[1:]])
    with guarded():
        proc = subprocess.Popen(launch, stdin=stdin, stdout=stdout, stderr=stderr, text=text)
        with _CHILDREN_LOCK:
            _CHILDREN[proc.pid] = proc
    try:
        if _SHUTTING_DOWN:  # begin() ran while Popen did: it may not have seen this child
            _signal_child(proc, kill=True)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            exc.output, exc.stderr = proc.communicate()
            raise
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    finally:
        with _CHILDREN_LOCK:
            _CHILDREN.pop(proc.pid, None)
    if _SHUTTING_DOWN:
        raise ShuttingDown()
    code = proc.poll()
    assert code is not None
    if check and code:
        raise subprocess.CalledProcessError(code, argv, output=out, stderr=err)
    return subprocess.CompletedProcess(argv, code, out, err)


# R15-4 / R15-5 (round 15): the signals a harness (tests, qa_phase0, the
# stress script, CI) resets to their default action in every child it starts —
# an inherited SIG_IGN (a background job's SIGINT, nohup's SIGHUP) survives
# exec, and the R14-1 handler then rightly leaves it ignored, so a test that
# signals its child slept through it.
HARNESS_DEFAULT_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP")
# R15-5: dispositions are set in an exec trampoline, never with preexec_fn
# (which is not fork-safe in a process with threads). argv: <json> <program> <args…>.
SIGNALS_TRAMPOLINE = (
    "import json, os, signal, sys\n"
    "for name, action in json.loads(sys.argv[1]).items():\n"
    "    if hasattr(signal, name):\n"
    "        signal.signal(getattr(signal, name), getattr(signal, action))\n"
    "os.execv(sys.argv[2], sys.argv[2:])\n"
)


def with_signals(dispositions: dict[str, str], argv: Sequence[str | os.PathLike[str]]) -> list[str]:
    """``argv`` started with the given signal dispositions ({"SIGINT": "SIG_IGN", …}) — fork-safe (R15-5).

    For ``subprocess.Popen``: the trampoline sets them and ``execv``-s
    ``argv`` (its program resolved on PATH), so the program inherits exactly
    those dispositions.
    """
    parts = [os.fspath(part) for part in argv]
    if not parts:
        raise ValueError("with_signals: empty command")
    return [sys.executable, "-I", "-S", "-c", SIGNALS_TRAMPOLINE, json.dumps(dispositions), _resolve_program(parts[0]), *parts[1:]]


def with_default_signals(argv: Sequence[str | os.PathLike[str]]) -> list[str]:
    """R15-4: ``argv`` started with :data:`HARNESS_DEFAULT_SIGNALS` at their default action, whatever was inherited."""
    return with_signals({name: "SIG_DFL" for name in HARNESS_DEFAULT_SIGNALS}, argv)


__all__ = [
    "CHILD_KILL_GRACE_SECONDS",
    "HARNESS_DEFAULT_SIGNALS",
    "SIGNALS_TRAMPOLINE",
    "CHILD_TERM_GRACE_SECONDS",
    "PDEATHSIG_TRAMPOLINE",
    "PENDING_SETTLE_SECONDS",
    "SHUTDOWN_REASON",
    "ShuttingDown",
    "active",
    "begin",
    "guarded",
    "refuse_if_active",
    "run_child",
    "running_children",
    "stop_children",
    "with_default_signals",
    "with_signals",
]
