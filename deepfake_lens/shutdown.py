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
- R16-6 (round 16): a child's stdin is ``/dev/null`` unless the caller
  passes one (``ffmpeg`` also gets ``-nostdin``): it used to inherit the
  operator's terminal, and an ``ffmpeg`` killed together with the scan left
  the terminal with echo off.
- R16-7 (round 16): a child is started in a new session and process group
  (``start_new_session=True``) and is stopped by signalling the whole group
  (``killpg``) — grandchildren included (a wrapper script ``sh → ffmpeg``
  used to leave its ``ffmpeg`` running after the wrapper was killed).
- on Linux each child runs under a small supervisor (``python -I -S -c``
  :data:`CHILD_SUPERVISOR`, the leader of the child's session): it is bound
  to this process with ``PR_SET_PDEATHSIG`` (SIGTERM — a SIGKILLed scan,
  which runs no handler at all, still ends its children) and is a
  ``PR_SET_CHILD_SUBREAPER``, so a grandchild orphaned by its parent's death
  is re-parented to it, not to init. It starts the program in a process
  group of its own (PR_SET_PDEATHSIG SIGKILL bound to the supervisor), waits
  for it, then signals that group and every orphan it adopted — SIGTERM, a
  grace of :data:`SUPERVISOR_TERM_GRACE_SECONDS`, SIGKILL — when it is told
  to stop (or this process died), and SIGKILL to anything the program left
  behind when it ends normally; it exits with the program's status.
  ``preexec_fn`` is not fork-safe in a threaded process (R15-5), hence the
  separate interpreter; it also checks that its parent is still this
  process (a parent killed before ``prctl`` ran is not seen by the flag).
  Elsewhere (macOS, Windows, a frozen build without a Python interpreter)
  the tracked children (and, on POSIX, their process groups) are stopped by
  the handler and the atexit hook; a hard kill of the parent leaves them to
  their own timeout.

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
# R15-1: <linux/prctl.h> PR_SET_PDEATHSIG; R16-7: PR_SET_CHILD_SUBREAPER.
_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
# R16-7: how long the supervisor gives the program's process group (and the
# orphans it adopted) after SIGTERM before SIGKILL — below
# CHILD_TERM_GRACE_SECONDS, so the supervisor is done before this process
# would escalate to SIGKILL of the supervisor itself.
SUPERVISOR_TERM_GRACE_SECONDS = 1.0
# R16-7 (round 16): the child supervisor (Linux), replacing the R15-1 exec
# trampoline. argv: <parent pid> <grace seconds> <program> <args…>. See the
# module docstring. Python itself ignores SIGPIPE (and SIGXFSZ) at start-up
# and an ignored signal survives exec, so the program gets both back at
# their default — what ``subprocess``'s restore_signals does for a direct
# exec — and SIGTERM/SIGHUP/SIGINT at their default too.
CHILD_SUPERVISOR = f"""
import ctypes, os, signal, sys, time
PARENT, GRACE, ARGV = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3:]
STOPS = ("SIGTERM", "SIGHUP", "SIGINT")
class Stop(Exception):
    pass
received = []
def on_stop(signum, frame):
    received.append(signum)
    raise Stop()
for name in STOPS:
    signal.signal(getattr(signal, name), on_stop)
libc = ctypes.CDLL(None, use_errno=True)
def prctl(option, value):
    try:
        libc.prctl(option, value, 0, 0, 0)
    except (OSError, AttributeError):
        pass
pid = -1
statuses = {{}}
try:
    prctl({_PR_SET_CHILD_SUBREAPER}, 1)
    prctl({_PR_SET_PDEATHSIG}, int(signal.SIGTERM))
    if os.getppid() != PARENT:
        raise Stop()
    pid = os.fork()
    if pid == 0:
        try:
            os.setpgid(0, 0)
            prctl({_PR_SET_PDEATHSIG}, int(signal.SIGKILL))
            for name in (*STOPS, "SIGPIPE", "SIGXFSZ"):
                if hasattr(signal, name):
                    signal.signal(getattr(signal, name), signal.SIG_DFL)
            os.execv(ARGV[0], ARGV)
        finally:
            os._exit(127)
    try:
        os.setpgid(pid, pid)
    except OSError:
        pass
    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
except Stop:
    pass
for name in STOPS:
    signal.signal(getattr(signal, name), signal.SIG_IGN)
def adopted():
    out = []
    for task in os.listdir("/proc/self/task"):
        try:
            with open("/proc/self/task/" + task + "/children") as handle:
                out += [int(value) for value in handle.read().split()]
        except OSError:
            pass
    return out
def send(signum):
    if pid > 0:
        try:
            os.killpg(pid, signum)
        except OSError:
            pass
    for child in adopted():
        try:
            os.kill(child, signum)
        except OSError:
            pass
def reap(seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            got, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if got:
            statuses[got] = status
        elif time.monotonic() >= deadline:
            return
        else:
            time.sleep(0.005)
if received:
    send(signal.SIGTERM)
    reap(GRACE)
send(signal.SIGKILL)
reap(GRACE)
if received or pid not in statuses:
    signum = received[0] if received else signal.SIGKILL
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    os._exit(128 + signum)
code = os.waitstatus_to_exitcode(statuses[pid])
if code < 0:
    signal.signal(-code, signal.SIG_DFL)
    os.kill(os.getpid(), -code)
    os._exit(128 - code)
os._exit(code)
"""


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


def _supervised_groups(proc: subprocess.Popen[Any]) -> list[int]:
    """R16-7: the process groups of the programs a Linux supervisor runs (each program leads its own)."""
    if not _SUPERVISED:
        return []
    groups: list[int] = []
    try:
        tasks = os.listdir(f"/proc/{proc.pid}/task")
    except OSError:
        return []
    for task in tasks:
        try:
            with open(f"/proc/{proc.pid}/task/{task}/children", encoding="ascii") as handle:
                groups += [int(value) for value in handle.read().split()]
        except (OSError, ValueError):
            continue
    return groups


def _signal_child(proc: subprocess.Popen[Any], kill: bool) -> None:
    """Signal a tracked child's whole process group (R16-7); on Windows the child only."""
    import signal

    if os.name != "posix":
        try:
            if kill:
                proc.kill()
            else:
                proc.terminate()
        except OSError:  # already gone
            pass
        return
    signum = signal.SIGKILL if kill else signal.SIGTERM
    if proc.returncode is not None:
        return  # reaped: its pid (and group id) may belong to another process by now
    if kill:
        # The supervisor is killed outright: end the program groups it ran first.
        for group in _supervised_groups(proc):
            try:
                os.killpg(group, signum)
            except OSError:
                pass
    try:
        os.killpg(proc.pid, signum)  # the child leads its own session and group (start_new_session)
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


# R16-7: children run under CHILD_SUPERVISOR (Linux with a Python interpreter).
_SUPERVISED = sys.platform.startswith("linux") and not getattr(sys, "frozen", False) and bool(sys.executable)


def _bind_to_parent(argv: list[str]) -> list[str]:
    """R15-1/R16-7: Linux — ``argv`` started under :data:`CHILD_SUPERVISOR`; elsewhere unchanged."""
    if not _SUPERVISED:
        return argv
    return [sys.executable, "-I", "-S", "-c", CHILD_SUPERVISOR, str(os.getpid()), str(SUPERVISOR_TERM_GRACE_SECONDS), *argv]


def _stop_one(proc: subprocess.Popen[Any]) -> None:
    """R16-7: stop one tracked child like :func:`stop_children` does (group SIGTERM → grace → SIGKILL) and reap it."""
    _signal_child(proc, kill=False)
    if _wait_all([proc], CHILD_TERM_GRACE_SECONDS):
        _signal_child(proc, kill=True)
    try:
        proc.wait(timeout=CHILD_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("child process %s did not end", proc.pid)


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
    if stdin is None:
        stdin = subprocess.DEVNULL  # R16-6: never the operator's terminal
    launch = _bind_to_parent([_resolve_program(argv[0]), *argv[1:]])
    with guarded():
        # R16-7: a new session and process group — stopped with killpg, grandchildren included.
        proc = subprocess.Popen(launch, stdin=stdin, stdout=stdout, stderr=stderr, text=text, start_new_session=os.name == "posix")
        with _CHILDREN_LOCK:
            _CHILDREN[proc.pid] = proc
    try:
        if _SHUTTING_DOWN:  # begin() ran while Popen did: it may not have seen this child
            _signal_child(proc, kill=False)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_one(proc)  # R16-7: the whole group, through the supervisor
            exc.output, exc.stderr = proc.communicate()
            raise
        except BaseException:
            _stop_one(proc)
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


def children_start_with_default_signals(signals: Sequence[str] = HARNESS_DEFAULT_SIGNALS) -> list[str]:
    """R15-4: from now on every child of this process starts with ``signals`` at their default action.

    An *ignored* signal survives ``exec``; a *caught* one is reset to the
    default by it. So each of ``signals`` this process inherited as
    ``SIG_IGN`` gets a handler that does nothing: this process still ignores
    it, and every program it starts (``subprocess``, any exec) begins with
    the default action — as under an interactive terminal. Called by the
    test package (``deepfake_lens/tests/__init__.py``) and the QA harness.
    Main thread only (elsewhere nothing changes). Returns the names changed.
    """
    import signal

    if threading.current_thread() is not threading.main_thread():
        return []
    changed: list[str] = []
    for name in signals:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            if signal.getsignal(signum) == signal.SIG_IGN:
                signal.signal(signum, _still_ignored)
                changed.append(name)
        except (OSError, RuntimeError, ValueError):
            continue
    return changed


def _still_ignored(signum: int, frame: Any) -> None:
    """R15-4: the handler that keeps an inherited SIG_IGN in effect here while exec resets it for children."""


def with_default_signals(argv: Sequence[str | os.PathLike[str]]) -> list[str]:
    """R15-4: ``argv`` started with :data:`HARNESS_DEFAULT_SIGNALS` at their default action, whatever was inherited."""
    return with_signals({name: "SIG_DFL" for name in HARNESS_DEFAULT_SIGNALS}, argv)


__all__ = [
    "CHILD_KILL_GRACE_SECONDS",
    "CHILD_SUPERVISOR",
    "HARNESS_DEFAULT_SIGNALS",
    "SIGNALS_TRAMPOLINE",
    "CHILD_TERM_GRACE_SECONDS",
    "PENDING_SETTLE_SECONDS",
    "SHUTDOWN_REASON",
    "SUPERVISOR_TERM_GRACE_SECONDS",
    "ShuttingDown",
    "active",
    "begin",
    "children_start_with_default_signals",
    "guarded",
    "refuse_if_active",
    "run_child",
    "running_children",
    "stop_children",
    "with_default_signals",
    "with_signals",
]
