"""R19-2 (round 19): librosa feature extraction in a supervised worker process, with a tool-owned numba cache.

The verifier ran four scans of a video with an audio track at the same time
in the main venv (librosa + numba): librosa's numba functions are compiled
with ``cache=True`` into ``site-packages/librosa/__pycache__`` (``*.nbi``
index, ``*.nbc`` code), the concurrent writers left an index pointing at
code of another overload, and from then on every scan of that file died
with SIGSEGV in ``librosa.beat`` (rc 139, no output, no report) until the
cache files were deleted.

Two changes close it:

1. **No shared writable numba cache.** Every process that imports librosa
   — this package's scan process (:func:`prepare_process_numba_cache`, before
   the first ``import librosa``) and each worker — compiles into a private
   ``NUMBA_CACHE_DIR`` inside its session folder (removed with it). A
   private folder is seeded with a copy of the *published* cache
   ``~/.cache/deepfake-lens/numba/<runtime>/published`` when there is one;
   after the first successful extraction of a worker whose folder was not
   seeded, its folder is copied to a new folder and renamed to
   ``published`` — a rename that fails when another process published first.
   ``published`` is never written after the rename, so readers copy a
   complete, unchanging tree, and no two processes ever write the same
   cache file. A ``NUMBA_CACHE_DIR`` set by the operator is replaced (it
   would be shared again).
2. **Crash isolation.** :func:`extract_features` sends the request to a
   worker (``python -P -c`` :data:`WORKER_BOOT`, started with
   :func:`shutdown.start_tracked` — supervised, tracked, stopped on
   shutdown like every child). A native crash ends the worker, not the
   scan: the check is ``failed`` "NativeCrashError: … (신호 SIGSEGV)".
   Inside a :func:`worker_scope` each thread keeps one worker (its
   supervisor is bound to that thread) and the scope ends them all; a
   call outside any scope starts a worker for that one request. A worker
   also exits after :data:`WORKER_IDLE_SECONDS` without a request.

R20-2 (round 20): every command and request that analyzes more than one
file opens a scope — a folder scan (core), ``batch`` (its thread pool's
tasks bound with :meth:`WorkerScope.bind`), ``eval`` / ``benchmark`` /
calibration (evaluate), ``feedback``, ``fusion``, a web upload (both
servers) and the report's re-analysis — so one worker serves all of its
files. They used to start one worker per file (about 2 s each, 7 s
without a published numba cache): batch of 100 WAV files 11.8 s → 87 s,
an upload of 30 WAV files 3.2 s → 30 s. A scope opened while another is
active on the same thread is that scope (a scan inside a report keeps
the report's worker); the outermost one ends the workers.

R20-3 (round 20): the protocol is framed and checked. Each worker gets a
random token (argv); after moving fd 1 to fd 2 it writes one ready line
``{"ready": <token>}`` — anything a ``sitecustomize``, a ``.pth`` file or a
native initializer wrote to fd 1 before that is start-up output, logged and
skipped (at most :data:`_MAX_STARTUP_LINES` lines). Every request carries an
``id`` and every reply must be one JSON object with the worker's token and
that id. Any other reply — unparseable, another id, no newline, over
:data:`_MAX_REPLY_BYTES` — is a :class:`WorkerProtocolError`: that file's
check fails and the worker is stopped and never asked again. (One line of
start-up output used to shift every later reply by one file: each file got
the audio features of the file before it, with no warning.)

R21-3 (round 21): the parent finds the ready marker in the byte stream —
start-up output without a newline before it no longer hides it — and sends
the first request only after it, within :data:`WORKER_READY_TIMEOUT_SECONDS`
and :data:`_MAX_STARTUP_BYTES` / :data:`_MAX_STARTUP_LINES` of start-up
output; a worker that does not send it is a :class:`WorkerStartError` (that
file's check fails, the worker is stopped). The request used to be sent
first, so the reply was skipped as start-up output too and each file waited
:data:`WORKER_REQUEST_TIMEOUT_SECONDS` (600 s), then was reported as a native
crash "(종료 코드 0)".

A decode error raised in the worker is rebuilt in this process as an
exception of the same class name, message, errno and file name, so
``failure_reason`` gives the reason an in-process extraction gave.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from typing import IO, Any, Callable, Iterator, TypeVar

from . import shutdown

logger = logging.getLogger(__name__)
T = TypeVar("T")

# R19-2: the environment variable numba reads its cache folder from.
NUMBA_CACHE_ENV = "NUMBA_CACHE_DIR"
# R19-2: a worker that got no request for this long exits; the pool retires
# an idle worker a little earlier, so it never sends to one that is exiting.
WORKER_IDLE_SECONDS = 300.0
_POOL_RETIRE_SECONDS = 240.0
# R20-2: the thread-count variables of the numeric libraries a worker loads (numpy's BLAS, OpenMP, numba).
WORKER_THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS")
# R19-2: one extraction (decode of at most --segment seconds + features).
WORKER_REQUEST_TIMEOUT_SECONDS = 600.0
# R19-2: the reason of a worker that died during a request.
NATIVE_CRASH_MESSAGE = "오디오 특징 추출 중 네이티브 라이브러리가 비정상 종료했습니다({cause}) — 이 검사만 실패로 기록했습니다"
TIMEOUT_MESSAGE = "오디오 특징 추출이 {seconds:.0f}초 안에 끝나지 않아 중단했습니다"
# argv[1]: the folder holding this package, put first on the worker's import
# path. ``-P``: no working folder on the path (the scan's working folder may be
# the evidence folder; the worker's is its cache folder anyway). The rest of the
# path is the interpreter's own (site-packages, PYTHONPATH, user site) — never
# this process's sys.path as it is at run time: importing cv2 inserts
# site-packages/cv2 into sys.path, and a worker given that path imported
# cv2/typing as "typing" and died at start-up (exit 1).
WORKER_BOOT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from deepfake_lens.audio_worker import serve\n"
    "serve(sys.argv[2])\n"
)
# R20-3: the reason of a reply that is not the answer to the request sent.
PROTOCOL_ERROR_MESSAGE = "오디오 특징 추출 작업자의 응답이 요청과 맞지 않아 작업자를 중단했습니다({detail}) — 이 검사만 실패로 기록했습니다"
# R20-3: start-up output tolerated before the ready line, and the longest reply line.
_MAX_STARTUP_LINES = 64
_MAX_REPLY_BYTES = 4 * 1024 * 1024
# R21-3 (round 21): and at most this many bytes of it (output without a newline included).
_MAX_STARTUP_BYTES = 64 * 1024
# R21-3: how long a new worker may take to write its ready marker (it imports only this module before it).
# R22-5 (round 22) correction: e427a87 said the marker is found "within 60 s" as if that bounded a scan. It is
# per new worker, and a worker that failed to start is dropped, so each later file started another one and
# waited again: 60 s per file (4 WAVs 240 s, 300 audio files about 5 hours). WORKER_START_BREAKER_FAILURES bounds it.
WORKER_READY_TIMEOUT_SECONDS = 60.0
# R22-5: after this many start failures in a row within one worker scope (one scan / multi-file request),
# the scope starts no more workers — every later file's audio check fails at once with the reason below.
WORKER_START_BREAKER_FAILURES = 2
WORKER_START_BREAKER_MESSAGE = (
    "오디오 특징 추출 작업자가 이 검사에서 연속 {count}번 준비 신호를 보내지 않아 작업자를 더 띄우지 않았습니다"
    "(처음 사유: {reason}) — 이 파일의 오디오 검사만 실패로 기록했습니다"
)
# R21-3: the reason of a worker that never wrote its ready marker (timed out, ended, too much output first).
STARTUP_ERROR_MESSAGE = "오디오 특징 추출 작업자가 준비 신호를 보내지 않아 중단했습니다({detail}) — 이 검사만 실패로 기록했습니다"


class NativeCrashError(RuntimeError):
    """R19-2: the worker process ended during a request (a native crash, a kill)."""


class WorkerTimeoutError(RuntimeError):
    """R19-2: the worker did not answer within :data:`WORKER_REQUEST_TIMEOUT_SECONDS`."""


class WorkerProtocolError(RuntimeError):
    """R20-3: the worker's reply is not a framed answer to the request sent (the worker is stopped)."""


class WorkerStartError(WorkerProtocolError):
    """R21-3: the worker did not write its ready marker — timed out, ended first or wrote too much before it (stopped)."""


# --- numba cache ------------------------------------------------------------


def _runtime_key() -> str:
    """The folder name of the published cache: Python, numba, librosa (version and location), machine."""
    import importlib.metadata
    import importlib.util
    import platform

    parts = [sys.implementation.name, f"{sys.version_info[0]}.{sys.version_info[1]}", platform.machine()]
    for dist in ("numba", "librosa"):
        try:
            parts.append(f"{dist}={importlib.metadata.version(dist)}")
        except Exception:  # noqa: BLE001 — not installed / broken metadata
            parts.append(f"{dist}=none")
    try:
        spec = importlib.util.find_spec("librosa")
        parts.append(str(spec.origin) if spec is not None else "")
    except (ImportError, ValueError):
        parts.append("")
    return hashlib.sha256("\n".join(parts).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def shared_cache_root() -> Path | None:
    """``~/.cache/deepfake-lens/numba/<runtime>`` — None when there is no absolute home folder.

    R21-10 (round 21): under the relocated tool base when the CLI moved it
    (``native_path.tool_cache_base``).
    """
    from .native_path import tool_cache_base

    base = tool_cache_base()  # None for a relative HOME: it would put the cache into the working (evidence) folder
    return base / "numba" / _runtime_key() if base is not None else None


def private_cache_dir() -> tuple[str, bool]:
    """A new private numba cache folder in the session folder, seeded from the published cache; (folder, seeded)."""
    import tempfile

    from .native_path import scratch_dir

    folder = tempfile.mkdtemp(prefix="numba-", dir=scratch_dir())
    root = shared_cache_root()
    published = root / "published" if root is not None else None
    if published is not None and published.is_dir():
        try:
            shutil.copytree(published, folder, dirs_exist_ok=True)
            return folder, True
        except (OSError, shutil.Error) as exc:
            logger.info("numba cache: published cache not copied (%s)", type(exc).__name__)
    return folder, False


def publish_cache(folder: str) -> bool:
    """Copy a private cache to a new folder and rename it to ``published`` — False when one exists or on any error."""
    root = shared_cache_root()
    if root is None:
        return False
    published = root / "published"
    if published.exists():
        return False
    staging = root / f".publish-{os.getpid()}-{os.urandom(6).hex()}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(folder, staging)
        os.rename(staging, published)  # fails (EEXIST/ENOTEMPTY) when another process published first
        return True
    except (OSError, shutil.Error):
        shutil.rmtree(staging, ignore_errors=True)
        return False


_PROCESS_CACHE_LOCK = threading.Lock()
_PROCESS_CACHE_DIR: str | None = None


def prepare_process_numba_cache() -> None:
    """Point this process's numba at a private cache folder — call before the first ``import librosa``.

    numba reads ``NUMBA_CACHE_DIR`` when it is first imported; once it is
    imported this does nothing (and never raises: a process that cannot make
    the folder keeps numba's default).
    """
    global _PROCESS_CACHE_DIR
    if "numba" in sys.modules:
        return
    with _PROCESS_CACHE_LOCK:
        if _PROCESS_CACHE_DIR is not None or "numba" in sys.modules:
            return
        try:
            folder, _ = private_cache_dir()
        except Exception as exc:  # noqa: BLE001 — shutting down, no temp folder: keep the default
            logger.info("numba cache: no private folder (%s)", type(exc).__name__)
            return
        _PROCESS_CACHE_DIR = folder
        os.environ[NUMBA_CACHE_ENV] = folder


# --- worker side ------------------------------------------------------------


def _error_reply(exc: BaseException) -> dict[str, Any]:
    import traceback

    reply: dict[str, Any] = {"class": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    if isinstance(exc, OSError):
        reply.update(errno=exc.errno, strerror=exc.strerror, filename=exc.filename if isinstance(exc.filename, str) else None)
    return {"error": reply}


def _serve_one(request: dict[str, Any]) -> dict[str, Any]:
    from dataclasses import asdict

    try:
        import librosa  # noqa: F401
    except ImportError as exc:
        return {"missing": exc.name or "librosa"}
    from .audio import _extract_features

    try:
        features = _extract_features(Path(request["path"]), segment_seconds=int(request["segment_seconds"]), raise_on_decode=True)
    except Exception as exc:  # noqa: BLE001 — every decode failure goes back as a failed check
        return _error_reply(exc)
    return {"features": asdict(features) if features is not None else None}


def serve(token: str = "") -> None:
    """The worker loop: one JSON request per line on stdin, one JSON reply per line on fd 1.

    R20-3: first the ready line ``{"ready": token}``; every reply carries
    ``token`` and the request's ``id``.
    """
    import select

    global _PROCESS_CACHE_DIR
    _PROCESS_CACHE_DIR = os.environ.get(NUMBA_CACHE_ENV)  # the parent's private folder for this worker
    # Replies go to a private copy of fd 1; fd 1 itself becomes fd 2 (the
    # log), so a library that prints cannot corrupt the protocol.
    replies = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    replies.write(json.dumps({"ready": token}).encode("ascii") + b"\n")
    replies.flush()
    incoming = sys.stdin.buffer
    while True:
        if os.name == "posix":
            ready, _, _ = select.select([incoming], [], [], WORKER_IDLE_SECONDS)
            if not ready:
                return
        line = incoming.readline()
        if not line:
            return
        request_id: object = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            reply = _serve_one(request)
        except Exception as exc:  # noqa: BLE001 — a malformed request still gets an answer
            reply = _error_reply(exc)
        reply.update(token=token, id=request_id)
        replies.write(json.dumps(reply, ensure_ascii=True).encode("ascii") + b"\n")
        replies.flush()


# --- parent side ------------------------------------------------------------


def _parsed(line: bytes) -> object:
    """R20-3: one reply line as JSON, or None when it is not JSON."""
    try:
        return json.loads(line)
    except ValueError:
        return None


def _rebuilt_exception(error: dict[str, Any]) -> Exception:
    """An exception like the worker's: same class name, message, errno and file name."""
    name = str(error.get("class") or "Exception")
    message = str(error.get("message") or "")
    errno = error.get("errno")
    base: type[Exception] = OSError if isinstance(errno, int) else Exception
    cls = type(name, (base,), {"__str__": lambda self: message, "__module__": "deepfake_lens.audio_worker"})
    exc = cls(errno, error.get("strerror")) if base is OSError else cls(message)
    if base is OSError and isinstance(error.get("filename"), str):
        exc.filename = error["filename"]
    return exc


def _package_parent() -> str:
    """The folder that holds the ``deepfake_lens`` package this process imported."""
    return str(Path(__file__).resolve().parent.parent)


def _native_log() -> IO[bytes] | None:
    from .native_stderr import native_log_path

    try:
        path = Path(native_log_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "ab")
    except (OSError, RuntimeError, ValueError):
        return None


class _Worker:
    # R21-3: bytes read past the ready marker, served before the next read of the pipe.
    _pending = b""

    def __init__(self) -> None:
        # The thread that started it: its supervisor dies with that thread
        # (PR_SET_PDEATHSIG), so only that thread may use it.
        self.owner = threading.current_thread()
        self.token = os.urandom(8).hex()  # R20-3
        self.next_id = 0
        self.ready = False
        self.cache_dir, self.seeded = private_cache_dir()
        self.published = self.seeded
        self.expired = False
        self.last_used = time.monotonic()
        env = dict(os.environ)
        env[NUMBA_CACHE_ENV] = self.cache_dir
        # R20-2 (round 20): one worker extracts one file at a time (a scan gets its parallelism from
        # one worker per thread), so its numeric libraries run one thread each. With the default (a
        # thread per core) every worker's OpenMP / OpenBLAS pool spun on all cores: a scan of 300
        # audio/video files used 190-220 s of CPU for 50 s of wall time (2130892, in-process: the
        # same), and 4 workers made a scan 16-22 % slower than 2130892.
        # R21-2 (round 21) correction: R20-2 said "the features are byte-identical (the 300-row
        # comparison)". That is not true for every input: with one thread the last float32 digits
        # of some MFCC standard deviations of an MP4's audio track differ from a multi-threaded run
        # (the verifier: 8 of 300 rows, all MP4, verdicts equal). So the count is now always 1 — an
        # operator's OMP_NUM_THREADS (which R20-2 kept, and which no cache key recorded) no longer
        # changes a worker's features — and scan_cache.OUTPUT_FORMAT_GENERATION is 9, so no row
        # cached before this change is replayed.
        for name in WORKER_THREAD_ENV:
            env[name] = "1"
        log = _native_log()
        try:
            self.proc = shutdown.start_tracked(
                [sys.executable, "-P", "-c", WORKER_BOOT, _package_parent(), self.token],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log if log is not None else subprocess.DEVNULL, env=env,
                cwd=self.cache_dir,  # never the scan's working folder (possibly the evidence folder)
            )
        finally:
            if log is not None:
                log.close()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _expire(self) -> None:
        self.expired = True
        shutdown.stop_tracked(self.proc)

    def _line(self) -> bytes:
        assert self.proc.stdout is not None
        if self._pending:
            pending = self._pending
            end = pending.find(b"\n")
            if end >= 0:
                self._pending = pending[end + 1:]
                return pending[:end + 1]
            self._pending = b""
            return pending + self.proc.stdout.readline(max(1, _MAX_REPLY_BYTES - len(pending)))
        return self.proc.stdout.readline(_MAX_REPLY_BYTES)

    def _wait_ready(self, timeout: float) -> None:
        """R20-3/R21-3: skip start-up output up to the ready marker, before any request is sent.

        R21-3 (round 21): start-up output without a newline (``partial`` and
        then the marker on the same line) made the ready line unreadable; the
        request had already been sent, so the worker's reply was skipped as
        start-up output too and the parent waited
        :data:`WORKER_REQUEST_TIMEOUT_SECONDS` (600 s) per file for a line
        that never came — then reported "NativeCrashError … (종료 코드 0)".
        The marker ``{"ready": <token>}`` is now found anywhere in the byte
        stream (no newline needed), at most :data:`_MAX_STARTUP_BYTES` bytes /
        :data:`_MAX_STARTUP_LINES` lines after the start, within ``timeout``
        seconds (the worker is stopped when it expires); the request is sent
        only after it. Raises :class:`WorkerStartError` otherwise.
        """
        assert self.proc.stdout is not None
        marker = json.dumps({"ready": self.token}).encode("ascii")
        timer = threading.Timer(timeout, self._expire)
        timer.daemon = True
        timer.start()
        buffer = b""
        try:
            while True:
                at = buffer.find(marker)
                if at >= 0:
                    break
                if len(buffer) > _MAX_STARTUP_BYTES or buffer.count(b"\n") > _MAX_STARTUP_LINES:
                    raise WorkerStartError(STARTUP_ERROR_MESSAGE.format(
                        detail=f"준비 신호 전 출력 {_MAX_STARTUP_LINES}줄 또는 {_MAX_STARTUP_BYTES // 1024} KiB 초과"))
                try:
                    stream: Any = self.proc.stdout  # a BufferedReader: read1 returns what has arrived (no newline needed)
                    chunk = stream.read1(_MAX_STARTUP_BYTES)
                except (OSError, ValueError):
                    chunk = b""
                if not chunk:
                    if shutdown.active():
                        raise shutdown.ShuttingDown()  # the process is ending: not this worker's fault
                    if self.expired:
                        detail = f"{timeout:.0f}초 안에 준비 신호가 오지 않음"
                    else:
                        try:
                            code = self.proc.wait(timeout=5)
                        except (subprocess.TimeoutExpired, AttributeError):
                            code = None
                        detail = f"준비 신호 전에 끝남 — {_crash_cause(code)}" if code is not None else "준비 신호 전에 출력이 닫힘"
                    raise WorkerStartError(STARTUP_ERROR_MESSAGE.format(detail=detail))
                buffer += chunk
        finally:
            timer.cancel()
        startup, rest = buffer[:at], buffer[at + len(marker):]
        self._pending = rest[1:] if rest.startswith(b"\n") else rest
        if startup.strip():
            logger.warning("audio feature worker: start-up output before the ready marker skipped: %r", startup[:200])
        self.ready = True

    def ask(self, request: dict[str, Any], timeout: float) -> dict[str, Any] | None:
        """The reply, or None when the worker ended without one.

        R20-3: raises :class:`WorkerProtocolError` for a reply that is not
        this request's framed answer — the caller stops the worker. R21-3: a
        new worker's ready marker is awaited first (:meth:`_wait_ready`,
        :class:`WorkerStartError`).
        """
        assert self.proc.stdin is not None and self.proc.stdout is not None
        if not self.ready:
            self._wait_ready(WORKER_READY_TIMEOUT_SECONDS)  # R21-3: before the request (raises WorkerStartError)
        self.next_id += 1
        request_id = self.next_id
        timer = threading.Timer(timeout, self._expire)
        timer.daemon = True
        timer.start()
        try:
            self.proc.stdin.write(json.dumps({**request, "id": request_id}, ensure_ascii=True).encode("ascii") + b"\n")
            self.proc.stdin.flush()
            line = self._line()
        except (OSError, ValueError):
            line = b""
        finally:
            timer.cancel()
        self.last_used = time.monotonic()
        if not line:
            return None
        reply = _parsed(line)
        if not line.endswith(b"\n") or not isinstance(reply, dict):
            raise WorkerProtocolError(PROTOCOL_ERROR_MESSAGE.format(detail="해석할 수 없는 응답"))
        if reply.pop("token", None) != self.token or reply.pop("id", None) != request_id:
            raise WorkerProtocolError(PROTOCOL_ERROR_MESSAGE.format(detail="다른 요청의 응답"))
        return reply

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        shutdown.release_tracked(self.proc)
        for stream in (self.proc.stdout,):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


# R19-2: where workers live. Inside a scope (:func:`worker_scope`, entered by
# core._scan_specs and, R20-2, by every multi-file command and request) each
# thread keeps one worker for the scope's files and the scope ends them when
# it ends. The Linux supervisor of a child is bound to the thread that started
# it (PR_SET_PDEATHSIG fires when that thread ends), so a worker is only ever
# used by the thread that started it (R20-2: checked by thread object, not
# only by ident — a finished pool thread's ident can be reused). Outside any
# scope (one file: classify, the web server's analyze-file) a worker serves
# one request and is ended. No worker outlives its scope.
_LOCAL = threading.local()
_ALL: list[_Worker] = []
_ALL_LOCK = threading.Lock()


class WorkerScope:
    """The workers of one scan, one per thread; :meth:`close` ends them."""

    def __init__(self) -> None:
        self._workers: dict[int, _Worker] = {}
        self._lock = threading.Lock()
        # R22-5: start failures in a row (any thread of the scope) and, once tripped, the error every later file gets.
        self._start_failures = 0
        self._first_start_error = ""
        self.tripped: WorkerStartError | None = None

    def start_failed(self, exc: WorkerStartError) -> None:
        """R22-5: count a worker that never got ready; trip the breaker after :data:`WORKER_START_BREAKER_FAILURES` in a row."""
        with self._lock:
            self._start_failures += 1
            if self._start_failures == 1:
                self._first_start_error = str(exc)
            if self._start_failures >= WORKER_START_BREAKER_FAILURES and self.tripped is None:
                self.tripped = WorkerStartError(WORKER_START_BREAKER_MESSAGE.format(
                    count=self._start_failures, reason=self._first_start_error))
                logger.error("audio feature worker: %d start failures in a row — no more workers in this scan", self._start_failures)

    def start_succeeded(self) -> None:
        """R22-5: a worker got ready — the run of failures is over (a tripped scope stays tripped)."""
        with self._lock:
            self._start_failures = 0

    def bind(self, func: Callable[..., T]) -> Callable[..., T]:
        """``func`` run with this scope as the calling thread's scope (for a thread pool's tasks)."""

        def run(*args: Any, **kwargs: Any) -> T:
            previous = getattr(_LOCAL, "scope", None)
            _LOCAL.scope = self
            try:
                return func(*args, **kwargs)
            finally:
                _LOCAL.scope = previous

        return run

    def worker(self) -> _Worker:
        me = threading.current_thread()
        ident = threading.get_ident()
        with self._lock:
            worker = self._workers.get(ident)
            # R20-2: the workers of threads that have ended (a finished thread
            # pool of a scan inside this scope) — their supervisors are gone.
            ended = [(key, held) for key, held in self._workers.items() if not held.owner.is_alive()]
            for key, _ in ended:
                del self._workers[key]
        for _, held in ended:
            _forget(held)
        if worker is not None and worker.owner is me and worker.alive() and time.monotonic() - worker.last_used < _POOL_RETIRE_SECONDS:
            return worker
        if worker is not None and all(worker is not held for _, held in ended):
            _forget(worker)
        worker = _start()
        with self._lock:
            self._workers[ident] = worker
        return worker

    def drop(self, worker: _Worker) -> None:
        """R20-3: forget a stopped worker (its thread starts a new one for the next file)."""
        with self._lock:
            for key, held in list(self._workers.items()):
                if held is worker:
                    del self._workers[key]

    def size(self) -> int:
        """How many workers the scope holds (tests)."""
        with self._lock:
            return len(self._workers)

    def close(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            _forget(worker)


@contextmanager
def worker_scope() -> Iterator[WorkerScope]:
    """R19-2: the audio workers of one scan — this thread's scope until the end, then every worker ended.

    R20-2: inside an active scope of this thread, the same scope (the
    outermost one ends the workers) — a scan run by a multi-file command
    keeps the command's worker instead of starting another.
    """
    previous = getattr(_LOCAL, "scope", None)
    if previous is not None:
        yield previous
        return
    scope = WorkerScope()
    _LOCAL.scope = scope
    try:
        yield scope
    finally:
        _LOCAL.scope = previous
        scope.close()


def in_worker_scope(func: Callable[..., T]) -> Callable[..., T]:
    """R20-2: ``func`` run inside :func:`worker_scope` — for a function that analyzes many files on this thread."""
    import functools

    @functools.wraps(func)
    def run(*args: Any, **kwargs: Any) -> T:
        with worker_scope():
            return func(*args, **kwargs)

    return run


def _start() -> _Worker:
    worker = _Worker()
    with _ALL_LOCK:
        _ALL.append(worker)
    return worker


def _forget(worker: _Worker) -> None:
    worker.close()
    with _ALL_LOCK:
        if worker in _ALL:
            _ALL.remove(worker)


@atexit.register
def close_workers() -> None:
    """End every worker still running (stdin closed: the worker returns from its loop)."""
    with _ALL_LOCK:
        workers = list(_ALL)
        _ALL.clear()
    for worker in workers:
        worker.close()


def worker_available() -> bool:
    """False in a build without a Python interpreter to start (the extraction then runs in this process)."""
    return bool(sys.executable) and not getattr(sys, "frozen", False)


def _crash_cause(code: int | None) -> str:
    if code is not None and code < 0:
        try:
            return f"신호 {signal.Signals(-code).name}"
        except ValueError:
            return f"신호 {-code}"
    return f"종료 코드 {code}"


def extract_features(native_path: str, *, segment_seconds: int) -> dict[str, Any]:
    """The worker's reply for one file: ``{"features": …}`` or ``{"missing": <module>}``.

    A decode error is raised as the rebuilt exception; a worker that died
    raises :class:`NativeCrashError`, one that timed out
    :class:`WorkerTimeoutError`, and the process ending
    :class:`shutdown.ShuttingDown`.
    """
    request = {"path": native_path, "segment_seconds": int(segment_seconds)}
    for attempt in range(2):
        scope: WorkerScope | None = getattr(_LOCAL, "scope", None)
        if scope is not None and scope.tripped is not None:
            # R22-5 (round 22): this scope's workers failed to start WORKER_START_BREAKER_FAILURES times in a
            # row — no new worker (each would wait WORKER_READY_TIMEOUT_SECONDS again); the file fails now.
            raise WorkerStartError(str(scope.tripped))
        worker = scope.worker() if scope is not None else _start()
        was_ready = worker.ready
        try:
            reply = worker.ask(request, WORKER_REQUEST_TIMEOUT_SECONDS)
            if scope is not None and not was_ready and worker.ready:
                scope.start_succeeded()
        except WorkerProtocolError as exc:
            if scope is not None and isinstance(exc, WorkerStartError):
                scope.start_failed(exc)
            # R20-3: a desynchronized worker is stopped and never asked again; this file's check fails.
            logger.error("audio feature worker reply for %s refused: %s", native_path, exc)
            shutdown.stop_tracked(worker.proc)
            _forget(worker)
            if scope is not None:
                scope.drop(worker)
            raise
        finally:
            if scope is None:
                _forget(worker)  # outside a scope: one request per worker
        if reply is not None:
            if "features" in reply and reply["features"] is not None and not worker.published:
                worker.published = True
                publish_cache(worker.cache_dir)
            if "error" in reply:
                error = reply["error"]
                logger.error("audio decode failed: %s\n%s", native_path, error.get("traceback", ""))
                raise _rebuilt_exception(error)
            return reply
        _forget(worker)
        code = worker.proc.returncode
        if shutdown.active():
            raise shutdown.ShuttingDown()
        if worker.expired:
            raise WorkerTimeoutError(TIMEOUT_MESSAGE.format(seconds=WORKER_REQUEST_TIMEOUT_SECONDS))
        if code == 0 and attempt == 0:
            continue  # it exited idle just as the request was sent — ask a new one
        logger.error("audio feature worker ended during %s: %s", native_path, _crash_cause(code))
        raise NativeCrashError(NATIVE_CRASH_MESSAGE.format(cause=_crash_cause(code)))
    raise NativeCrashError(NATIVE_CRASH_MESSAGE.format(cause="응답 없음"))
