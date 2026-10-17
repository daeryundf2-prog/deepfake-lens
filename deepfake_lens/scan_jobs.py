"""P0R-6 (user decision 2026-10-17): the server's folder-scan jobs — the only thing a web report is built from.

``/api/report`` used to take the rows the client posted and reconcile them with a server re-analysis (about 900
lines; twelve verification findings R19-13 … R26-1 came from it). It now signs the rows of a scan job the server
itself ran and holds. This module is that job store, shared by both servers (stdlib ``web`` and ``api-serve``):

- **every folder scan is a job** — ``/api/scan`` (synchronous and ``async=1``) and ``/api/scan/stream``; the job's
  record is the scan payload the client received plus a server-only :data:`CONTEXT_KEY` (the absolute scan folder,
  the options, threshold provenance and model coverage captured when the scan ran);
- **bound to the session**: a job belongs to the token the server was started with (:func:`owner_for`; ``local`` for
  a loopback server without a token). A lookup by another owner is "unknown" (404) — the job id alone is no
  capability, and its existence is not revealed;
- **persisted** to a tool-owned folder outside the evidence (:func:`store_folder`: ``DEEPFAKE_LENS_SCAN_JOBS``, else
  ``scan-jobs`` in the per-user application-data folder of :mod:`user_data`), owner-only permissions, written whole;
  a finished job survives a server restart. A folder inside the examined folders (``cd ~; deepfake-lens web``) or no
  absolute base is refused like the review records (R22-3): the job is then held in memory only and the scan
  response says so (:data:`JOB_NOT_PERSISTED_NOTE`);
- **integrity**: with a report key (``DEEPFAKE_LENS_REPORT_KEY``) each record carries an HMAC over its content
  (:func:`_record_mac`); a record whose MAC is missing or wrong is refused (:class:`JobIntegrityError`), so editing a
  job file cannot change what a signed report says. Without a key reports are unsigned anyway;
- **TTL**: finished jobs expire after ``DEEPFAKE_LENS_SCAN_JOB_TTL_DAYS`` days (default :data:`DEFAULT_TTL_DAYS`);
  a running job is **never** evicted (the 15-minute eviction counted from the job's *creation*, so a scan running
  longer than that lost its result and its polling became 404 — review §2.4).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STORE_ENV = "DEEPFAKE_LENS_SCAN_JOBS"
TTL_ENV = "DEEPFAKE_LENS_SCAN_JOB_TTL_DAYS"
DEFAULT_TTL_DAYS = 7.0
STORE_FOLDER_NAME = "scan-jobs"
RECORD_SCHEMA = "deepfake-lens-scan-job-v1"
# Server-only part of a job record: never sent to a client, never signed as such.
CONTEXT_KEY = "_report_context"
LOCAL_OWNER = "local"
# Async scans running at once (a stream or a synchronous scan holds its request instead).
MAX_RUNNING = 32
# Finished jobs kept in memory; older ones are read back from the store folder when asked for (a job held in memory
# only — no store folder — is lost when it falls out of this window, oldest first).
MAX_FINISHED_IN_MEMORY = 64
# A job id: 128 random bits as hex (was 64).
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
SOURCE_ASYNC = "async"
SOURCE_SYNC = "sync"
SOURCE_STREAM = "stream"
FINISHED_STATUSES = ("done", "cancelled", "error")

JOB_NOT_PERSISTED_NOTE = (
    "검사 작업 기록을 디스크에 보관하지 않았습니다({reason}) — 이 서버가 다시 시작되면 이 검사로 보고서를 만들 수 없습니다"
)
JOB_INTEGRITY_FAILED = "검사 작업 기록의 무결성을 확인할 수 없습니다(기록이 바뀌었거나 다른 키로 만들어짐) — 폴더를 다시 검사하십시오"
TOO_MANY_RUNNING = "실행 중인 검사 작업이 너무 많습니다 — 진행 중인 작업이 끝난 뒤 다시 시도하십시오"
TTL_INVALID = "환경 변수 {env}의 값이 양의 숫자(일)가 아닙니다: 「{value}」 — 기본값 {default:g}일을 씁니다"


class JobIntegrityError(RuntimeError):
    """A persisted job record whose MAC does not verify under the current report key (HTTP 409)."""


def owner_for(token: str | None) -> str:
    """The owner a server's jobs are bound to: a digest of its token, or :data:`LOCAL_OWNER` without one."""
    if not token:
        return LOCAL_OWNER
    return "token:" + hashlib.sha256(b"deepfake-lens scan-job owner\x00" + token.encode("utf-8")).hexdigest()[:32]


def ttl_seconds() -> float:
    value = os.environ.get(TTL_ENV, "").strip()
    if not value:
        return DEFAULT_TTL_DAYS * 86400
    try:
        days = float(value)
    except ValueError:
        days = -1.0
    if not days > 0 or days != days:  # NaN
        logger.warning(TTL_INVALID.format(env=TTL_ENV, value=value[:40], default=DEFAULT_TTL_DAYS))
        return DEFAULT_TTL_DAYS * 86400
    return days * 86400


def new_job_id() -> str:
    return secrets.token_hex(16)


def store_folder(evidence: list[Path] | tuple[Path, ...] = ()) -> tuple[Path | None, str | None]:
    """``(folder, None)`` where finished jobs are kept, or ``(None, reason)`` when they are held in memory only."""
    from .user_data import UserDataRefused, check_outside, user_data_dir

    override = os.environ.get(STORE_ENV, "")
    if override:
        if not os.path.isabs(override):
            return None, f"환경 변수 {STORE_ENV}의 값이 절대 경로가 아님: 「{override[:200]}」"
        folder = Path(override)
    else:
        base = user_data_dir()
        if base is None:
            return None, "HOME 또는 APPDATA가 절대 경로가 아니어서 보관 위치를 정할 수 없음"
        folder = base / STORE_FOLDER_NAME
    try:
        check_outside(folder / "x.json", evidence, STORE_ENV)
    except UserDataRefused:
        return None, f"보관 폴더 {folder}이(가) 검사 대상 폴더 안에 있음 — 증거 폴더에는 쓰지 않음"
    return folder, None


def _canonical(record: dict[str, Any]) -> bytes:
    from .json_text import json_dumps

    return json_dumps({key: value for key, value in record.items() if key != "mac"}, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _record_mac(record: dict[str, Any], key: bytes) -> str:
    derived = hmac.new(key, b"deepfake-lens scan-job record v1", hashlib.sha256).digest()
    return hmac.new(derived, _canonical(record), hashlib.sha256).hexdigest()


class JobStore:
    """The job registry of this process (both servers use the module-level :data:`STORE`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    # -- bookkeeping -------------------------------------------------------------------------------------------------

    def _evict(self, now: float) -> None:
        """Drop finished jobs past the TTL (memory and disk); a running job is never evicted. Lock held."""
        ttl = ttl_seconds()
        for key in [key for key, entry in self._jobs.items()
                    if entry.get("status") in FINISHED_STATUSES and now - float(entry.get("finished") or now) > ttl]:
            entry = self._jobs.pop(key)
            self._unlink(entry)
        finished = sorted(
            (float(entry.get("finished") or 0), key) for key, entry in self._jobs.items() if entry.get("status") in FINISHED_STATUSES
        )
        for _, key in finished[: max(0, len(finished) - MAX_FINISHED_IN_MEMORY)]:
            self._jobs.pop(key, None)  # on disk when it could be persisted; get() reads it back

    def _unlink(self, entry: dict[str, Any]) -> None:
        path = entry.get("_file")
        if isinstance(path, Path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.info("scan job file %s could not be removed", path, exc_info=True)

    def sweep_disk(self, folder: Path | None) -> None:
        """Remove expired job files of ``folder`` (finished jobs only — a file is written when a job finishes)."""
        if folder is None or not folder.is_dir():
            return
        ttl = ttl_seconds()
        now = time.time()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            try:
                if now - entry.stat(follow_symlinks=False).st_mtime > ttl:
                    os.unlink(entry.path)
            except OSError:
                continue

    # -- lifecycle ---------------------------------------------------------------------------------------------------

    def start(self, owner: str, source: str, *, job_id: str | None = None) -> tuple[str, threading.Event]:
        """Register a running job; raises ValueError(:data:`TOO_MANY_RUNNING`) past :data:`MAX_RUNNING` async jobs."""
        cancel = threading.Event()
        with self._lock:
            self._evict(time.time())
            if source == SOURCE_ASYNC and sum(
                1 for entry in self._jobs.values() if entry.get("status") == "running" and entry.get("source") == SOURCE_ASYNC
            ) >= MAX_RUNNING:
                raise ValueError(TOO_MANY_RUNNING)
            job_id = job_id or new_job_id()
            while job_id in self._jobs:
                job_id = new_job_id()
            self._jobs[job_id] = {"status": "running", "created": time.time(), "owner": owner, "source": source, "cancel": cancel}
        return job_id, cancel

    def finish(
        self, job_id: str, status: str, result: dict[str, Any], *, evidence: list[Path] | tuple[Path, ...] = (),
    ) -> str | None:
        """Record a job's end and persist it; returns :data:`JOB_NOT_PERSISTED_NOTE` text when it stays in memory."""
        now = time.time()
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None:
                return None
            entry.update(status=status, result=result, finished=now)
            entry.pop("cancel", None)
            record = self._record(job_id, entry)
        if status == "error":
            return None  # nothing to report from; kept in memory for polling only
        folder, reason = store_folder(evidence)
        if folder is None:
            return JOB_NOT_PERSISTED_NOTE.format(reason=reason)
        from .output_files import OutputWriteError, write_output_bytes
        from .signing import resolve_report_key

        key = resolve_report_key()
        if key:
            record["mac"] = _record_mac(record, key)
        path = folder / f"{job_id}.json"
        try:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            from .json_text import json_dumps

            write_output_bytes(path, json_dumps(record, ensure_ascii=False).encode("utf-8"))
            os.chmod(path, 0o600)
        except (OSError, OutputWriteError) as exc:
            logger.warning("scan job %s could not be persisted: %s", job_id, exc)
            return JOB_NOT_PERSISTED_NOTE.format(reason=f"쓰기 실패: {type(exc).__name__}")
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["_file"] = path
        self.sweep_disk(folder)
        return None

    def record_finished(
        self, job_id: str, owner: str, source: str, status: str, result: dict[str, Any], *,
        evidence: list[Path] | tuple[Path, ...] = (),
    ) -> str | None:
        """A job that ran inside its request (a synchronous scan, a stream): start and finish at once."""
        self.start(owner, source, job_id=job_id)
        return self.finish(job_id, status, result, evidence=evidence)

    @staticmethod
    def _record(job_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": RECORD_SCHEMA, "job_id": job_id, "owner": entry.get("owner"), "source": entry.get("source"),
            "status": entry.get("status"), "created": entry.get("created"), "finished": entry.get("finished"),
            "result": entry.get("result"),
        }

    def annotate(self, job_id: str, key: str, value: object) -> None:
        """Add ``key`` to a finished job's in-memory result (e.g. the not-persisted note the client should see)."""
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is not None and isinstance(entry.get("result"), dict):
                entry["result"][key] = value

    # GUI redesign (U-7): a running job's progress (``done``, ``total``, ``current``, ``recent``) for
    # /api/scan-status — held in memory with the job, never persisted or signed.
    def set_progress(self, job_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is not None:
                entry.setdefault("progress", {}).update(fields)

    def add_progress_row(self, job_id: str, done: int, planned: int, row: dict[str, Any], *, keep: int) -> None:
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None:
                return
            progress = entry.setdefault("progress", {})
            progress["done"] = done
            progress["total"] = max(planned, progress.get("total") or 0)
            progress["current"] = row.get("name")
            progress["recent"] = ([row] + list(progress.get("recent") or []))[:keep]

    def cancel(self, job_id: str, owner: str) -> dict[str, Any] | None:
        """Ask a running job to stop: ``{"status", "cancelled"}``, or None for an unknown job / another owner's."""
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None or entry.get("owner") != owner:
                return None
            if entry.get("status") != "running":
                return {"status": entry.get("status"), "cancelled": False}
            cancel = entry.get("cancel")
            if isinstance(cancel, threading.Event):
                cancel.set()
            return {"status": "cancelling", "cancelled": True}

    def get(self, job_id: str, owner: str, *, evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, Any] | None:
        """``{"status", "created", "finished", "source", "result"}`` of ``job_id`` for ``owner``, or None.

        Looks in memory first, then (a finished job of an earlier server process) in the store folder. Raises
        :class:`JobIntegrityError` for a persisted record whose MAC does not verify with the current report key.
        """
        if not isinstance(job_id, str) or not JOB_ID_PATTERN.match(job_id):
            return None
        now = time.time()
        with self._lock:
            self._evict(now)
            entry = self._jobs.get(job_id)
            if entry is not None:
                if entry.get("owner") != owner:
                    return None
                return {key: entry.get(key) for key in ("status", "created", "finished", "source", "result", "progress")}
        record = self._load(job_id, evidence)
        if record is None or record.get("owner") != owner:
            return None
        finished = record.get("finished")
        if not isinstance(finished, (int, float)) or now - float(finished) > ttl_seconds():
            return None
        return {key: record.get(key) for key in ("status", "created", "finished", "source", "result")}

    def _load(self, job_id: str, evidence: list[Path] | tuple[Path, ...]) -> dict[str, Any] | None:
        folder, _reason = store_folder(evidence)
        if folder is None:
            return None
        path = folder / f"{job_id}.json"
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return None
        try:
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read()
            record = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            logger.warning("scan job record %s is unreadable", path, exc_info=True)
            return None
        if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA or record.get("job_id") != job_id:
            return None
        from .signing import resolve_report_key

        key = resolve_report_key()
        if key:
            mac = record.get("mac")
            if not isinstance(mac, str) or not hmac.compare_digest(mac, _record_mac(record, key)):
                raise JobIntegrityError(JOB_INTEGRITY_FAILED)
        return record

    def forget(self) -> None:
        """Drop every in-memory job (tests: a server restart)."""
        with self._lock:
            self._jobs.clear()


STORE = JobStore()


def client_payload(result: dict[str, Any]) -> dict[str, Any]:
    """The job result as a client sees it — without the server-only :data:`CONTEXT_KEY`."""
    return {key: value for key, value in result.items() if key != CONTEXT_KEY}
