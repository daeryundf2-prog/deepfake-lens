"""Request-payload handlers for the built-in web server.

``webapp.run_server`` owns the HTTP handler and auth; this module owns the
/api/* payload construction, upload parsing, scan-job registry, and the
read-root guard so each file stays a single readable layer.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs

from .audio_worker import in_worker_scope
from .bundle_info import PDF_ENGINE_FAILED, PDF_ENGINE_FAILED_HINT, bundle_text
from .checks import failure_reason
from .form_data import MAX_UPLOAD_PARTS as MAX_UPLOAD_PARTS  # round 31: the parts cap, beside the other upload caps
from .form_data import FormDataError, file_parts as _form_file_parts, is_form_data  # R30-1..R30-3, R30-5, R31-2
from .native_path import expand_user, scratch_dir
from .result_text import display_name
from .scan_jobs import (
    CONTEXT_KEY,
    LOCAL_OWNER,
    SOURCE_ASYNC,
    SOURCE_STREAM,
    SOURCE_SYNC,
    STORE,
    JobIntegrityError,
    client_payload,
    new_job_id,
)
from .vendor_weights import default_models_dir

logger = logging.getLogger(__name__)


def _layer_error(payload: dict[str, Any], layer: str, exc: BaseException) -> None:
    """Record a failed auxiliary layer in the response instead of hiding it."""
    errors = payload.setdefault("layer_errors", [])
    errors.append({"layer": layer, "status": "failed", "reason": failure_reason(exc)})
from .analysis_api import (
    WEB_MAX_FILE_BYTES_CEILING,
    WEB_MAX_SCAN_FILES,
    AnalysisOptions,
    analyze_path,
    analyze_rows,
    load_thresholds,
    provenance,
    scan_folder_run,
    scan_payload,
)
from .analysis_api import default_engine_profiles as _engine_profiles_in
from .core import SCAN_JSON_SCHEMA_VERSION, BatchScanSummary, DEFAULT_METADATA_BYTES, _scan_item_from_json, is_default_heatmap_output, summarize  # noqa: F401
from .datasets import is_negative_label, is_positive_label
from .reports import write_html_report
from .json_text import fs_b64encode, json_dumps


# G7: request limits live in analysis_api (AnalysisOptions.from_query);
# re-exported under their historical names.
MAX_SCAN_FILES = WEB_MAX_SCAN_FILES
MAX_FILE_BYTES_CEILING = WEB_MAX_FILE_BYTES_CEILING
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_UPLOAD_FILES = 20
# Round 31: MAX_UPLOAD_PARTS (form_data, 1000) caps the parts of one upload request — files, form fields and
# nameless parts alike — refused while parsing (form_data.MULTIPART_TOO_MANY_PARTS) before any row exists.

# Server-level models directory override — set by run_server(--models-dir)
# or DEEPFAKE_LENS_MODELS_DIR so every scan/check/coverage call resolves the
# same weight set an administrator provisioned.
_MODELS_DIR: Path | None = None


def set_models_dir(path: Path | None) -> None:
    global _MODELS_DIR
    _MODELS_DIR = expand_user(path).resolve() if path else None


def _models_dir() -> Path | None:
    return _MODELS_DIR or default_models_dir()


def default_engine_profiles(root: Path | None = None) -> list[Path]:
    """Engine profiles of the server's models dir (G7: same set as the CLI)."""
    return _engine_profiles_in(Path(root) if root is not None else _models_dir())


# R11-3 (round 11): a request body nested deeper than the JSON parser's
# recursion limit (``{"items": [[[[…]]]]}`` 50 000 deep) raised RecursionError
# out of POST /api/report and /api/feedback — a 500 on both servers. Every
# JSON request body is read through :func:`load_json_body`: a too-deep body
# is a 400 "JSON 중첩이 너무 깊습니다", an unparsable one a 400 "JSON 본문을
# 해석할 수 없습니다".
JSON_BODY_INVALID = "JSON 본문을 해석할 수 없습니다"
JSON_BODY_TOO_DEEP = "JSON 중첩이 너무 깊습니다"


class JsonBodyError(ValueError):
    """A request body that is not usable JSON; ``str(exc)`` is the Korean reason (R11-3)."""


def load_json_body(body: bytes, *, empty: object = None, errors: str = "strict") -> object:
    """``json.loads`` of a UTF-8 request body (``empty`` for an empty body).

    Raises :class:`JsonBodyError` with :data:`JSON_BODY_TOO_DEEP` on a
    RecursionError and :data:`JSON_BODY_INVALID` on any other parse error.
    """
    if not body and empty is not None:
        return empty
    try:
        return json.loads(body.decode("utf-8", errors=errors))
    except RecursionError:
        raise JsonBodyError(JSON_BODY_TOO_DEEP) from None
    except (ValueError, UnicodeDecodeError):
        raise JsonBodyError(JSON_BODY_INVALID) from None


class ApiError(dict):
    """A Korean JSON error body ``{"error": …}`` that carries its HTTP status (X4).

    Round 7: ``GET /api/analyze-file`` without ``file``, ``/api/scan-cancel``
    without ``job`` and ``/api/scan-status`` for an unknown job answered
    200 + ``{"error"}`` on both servers. Every ``/api/*`` payload function now
    returns its errors as ApiError; both servers send ``status``
    (:func:`api_status`) — 400 for a bad request, 404 for a missing job or
    file, 500 for an internal failure (docs/deepfake-lens-service.md, "오류
    상태 코드"). It is still a dict, so library callers keep reading
    ``payload["error"]``.
    """

    def __init__(self, message: str, status: int = 400, **extra: object) -> None:
        super().__init__(error=message, **extra)
        self.status = status


def api_status(payload: object) -> int:
    """HTTP status of a payload returned by an ``/api/*`` handler (200 unless an :class:`ApiError`)."""
    return payload.status if isinstance(payload, ApiError) else 200


# X4: Korean error texts shared by both servers.
JOB_PARAM_REQUIRED = "job 매개변수가 필요합니다"
JOB_UNKNOWN = "알 수 없거나 만료된 작업입니다"
FILE_PARAM_REQUIRED = "file 매개변수(파일 경로)가 필요합니다"


def async_requested(query: str) -> bool:
    """R23-13 (round 23): ``/api/scan``'s ``async`` value (both servers) — an unknown value is InvalidOption (400).

    It was "1" / "true" / "yes" or else a synchronous scan; it now takes the true/false values of every
    other option (:func:`analysis_api.parse_flag`).
    """
    from .analysis_api import parse_flag

    return parse_flag(parse_qs(query).get("async", ["false"])[0], "async")


def _web_options(params: dict[str, list[str]] | None = None) -> AnalysisOptions:
    """AnalysisOptions for a web request (InvalidOption -> HTTP 400)."""
    return AnalysisOptions.from_query(params or {}, models_dir=_models_dir())



def _load_gui() -> str:
    """Load the GUI HTML file."""
    gui_path = Path(__file__).parent / "gui.html"
    if gui_path.exists():
        return gui_path.read_text(encoding="utf-8")
    return "<h1>GUI 파일을 찾을 수 없습니다</h1>"


def scan_root_text(folder: Path) -> str:
    """The scanned folder as the absolute, resolved path a report request names (P1)."""
    try:
        return str(expand_user(folder).resolve())
    except (OSError, RuntimeError):
        return str(Path(os.path.abspath(expand_user(folder))))


# P6 (round 8): an uploaded file is a temp copy, not a file under a read
# root — its rows are marked so POST /api/report never re-analyzes a
# same-named file of the read root in its place (and never signs it).
UPLOAD_SOURCE = "upload"


def _mark_uploads(records: list[dict[str, object]]) -> list[dict[str, object]]:
    for record in records:
        record["source"] = UPLOAD_SOURCE
        # R11-14: an upload row's name is the client's file name (set after
        # to_json) — its display_name follows it.
        record["display_name"] = display_name(str(record.get("name") or ""))
        _sync_path_b64(record)
    _record_upload_rows(records)  # R22-8: the names this server received, for /api/report
    return records


def _sync_path_b64(record: dict[str, object]) -> None:
    """R12-4: ``path_b64`` follows a row path rewritten after to_json (upload rows
    get the client's name in place of the temp file's), never the temp path."""
    from .result_text import unescape_row_path

    if "member" not in record and isinstance(record.get("path"), str):
        record["path_b64"] = fs_b64encode(unescape_row_path(str(record["path"])))


def _set_row_name(record: dict[str, object], name: str) -> None:
    """Set a row's ``name`` and its escaped ``display_name`` together (R11-14)."""
    record["name"] = name
    record["display_name"] = display_name(name)


def _scan_run(
    query: str, *, default_folder: Path | None, should_stop: Callable[[], bool] | None = None,
    progress: Callable[[Any, int, int], None] | None = None, on_plan: Callable[[int], None] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Run a folder scan: ``(client payload, server-only report context)``.

    Raises ReadRootDenied (-> HTTP 403) when the folder is outside the
    operator-registered roots; the caller's folder is never registered (G31).
    """
    folder = _requested_folder(query, default_folder)
    _require_read_root(folder, default_folder)
    # G7: same options object, thresholds and engine set as the CLI. A bad
    # option (non-integer limit, model_path outside the models dir) raises
    # InvalidOption (a ValueError) -> HTTP 400 before any file is read.
    options = _web_options(parse_qs(query))
    # GUI redesign (U-7): ``progress``/``on_plan`` are the scanner's per-row callbacks — the async job reports n/N.
    run = scan_folder_run(folder, options, should_stop=should_stop, progress=progress, on_plan=on_plan)
    payload = scan_payload(run.summary, run.items, run.thresholds, options)
    # P1: the folder the row paths are relative to.
    root_text = scan_root_text(folder)
    payload["scan_root"] = root_text
    payload["scan_root_b64"] = fs_b64encode(root_text)  # R12-4
    return payload, report_context(Path(root_text), options, run.thresholds)


def report_context(folder: Path, options: AnalysisOptions, thresholds: Any) -> dict[str, object]:
    """P0R-6: what a report of this scan needs from the server, captured when the scan ran (server-only).

    The absolute scan folder (rows are relative to it, change detection re-hashes under it), the analysis options
    as names (signed as ``analysis_options``), the threshold provenance and the model coverage of the run, and the
    pins of the models folder. A report never re-reads these from the request or recomputes them later.
    """
    from .reports import _thresholds_payload
    from .vendor_weights import weights_coverage

    return {
        "scan_root": str(folder),
        "analysis_options": report_analysis_options(options),
        "thresholds": _thresholds_payload(thresholds),
        "coverage": dict(weights_coverage(options.resolved_models_dir())),
    }


def _scan_payload(
    query: str, *, default_folder: Path | None, should_stop: Callable[[], bool] | None = None, owner: str = LOCAL_OWNER,
) -> dict[str, object]:
    """Handle a synchronous folder scan: the payload, held as this server's finished job ``scan_job`` (P0R-6).

    ReadRootDenied (403) and an invalid option (InvalidOption, a ValueError -> 400) are raised before any file is
    read, as before; a scan error is the Korean 400 body.
    """
    _require_read_root(_requested_folder(query, default_folder), default_folder)
    _web_options(parse_qs(query))
    try:
        payload, context = _scan_run(query, default_folder=default_folder, should_stop=should_stop)
    except UnicodeError:
        # R11-1: a codec error (a non-UTF-8 name reaching a strict encoder)
        # is a server fault — the Korean 500 — never its English text as a 400.
        raise
    except (OSError, ValueError) as exc:
        # X4: missing folder, a file, unreadable — the Korean reason, 400.
        return ApiError(str(exc), 400)
    summary = payload.get("summary")
    status = "cancelled" if isinstance(summary, dict) and summary.get("cancelled") is True else "done"
    job_id = new_job_id()
    payload["scan_job"] = job_id
    note = STORE.record_finished(job_id, owner, SOURCE_SYNC, status, {**payload, CONTEXT_KEY: context}, evidence=_effective_roots(default_folder))
    if note:
        payload["scan_job_note"] = note
    return payload


# P0R-6 (user decision 2026-10-17): every folder scan is a job of scan_jobs.STORE — bound to the server's token,
# persisted outside the evidence, a running job never evicted. /api/report signs a job's rows and nothing else.
JOB_INTEGRITY_STATUS = 409
# GUI redesign: the most recent finished rows a running job reports.
_SCAN_JOB_RECENT = 5


def record_stream_scan_job(
    job_id: str, status: str, result: dict[str, Any], *, owner: str = LOCAL_OWNER,
    context: dict[str, object] | None = None, evidence: list[Path] | tuple[Path, ...] = (),
) -> str | None:
    """R26-5/P0R-6: a finished ``/api/scan/stream`` scan (``done`` or ``cancelled``) as this server's job ``job_id``.

    Returns :data:`scan_jobs.JOB_NOT_PERSISTED_NOTE` text when the job is held in memory only.
    """
    if status not in ("done", "cancelled") or not isinstance(result, dict):
        return None
    record = dict(result)
    record["scan_job"] = job_id
    if context is not None:
        record[CONTEXT_KEY] = context
    return STORE.record_finished(job_id, owner, SOURCE_STREAM, status, record, evidence=evidence)


def _scan_job_start(query: str, *, default_folder: Path | None, owner: str = LOCAL_OWNER) -> dict[str, object]:
    """Start a background scan job; poll /api/scan-status?job=<id>."""
    # Refuse out-of-root folders (403) and invalid options (400) up front,
    # not inside the job.
    _require_read_root(_requested_folder(query, default_folder), default_folder)
    _web_options(parse_qs(query))
    job_id, cancel = STORE.start(owner, SOURCE_ASYNC)  # ValueError past MAX_RUNNING (400)
    evidence = _effective_roots(default_folder)
    STORE.set_progress(job_id, {"done": 0, "total": None, "current": None, "recent": []})

    def on_plan(planned: int) -> None:
        STORE.set_progress(job_id, {"total": planned})

    def on_row(item: Any, done: int, planned: int) -> None:
        # GUI redesign (U-7): the row as the scanner finished it (pre-fusion) — its escaped name and verdict
        # code, for the GUI's "방금 끝난 파일" list only; the final rows are the job's result.
        name = display_name(str(getattr(item, "name", "") or getattr(item, "path", "") or ""))
        result = getattr(item, "result", None)
        verdict = getattr(getattr(result, "verdict_code", None), "value", None) if result is not None else None
        STORE.add_progress_row(job_id, done, planned, {"name": name, "status": str(getattr(item, "status", "") or ""), "verdict_code": verdict}, keep=_SCAN_JOB_RECENT)

    def work() -> None:
        try:
            payload, context = _scan_run(query, default_folder=default_folder, should_stop=cancel.is_set, progress=on_row, on_plan=on_plan)
            payload["scan_job"] = job_id
            # R18-3 (round 18): a scan the cancel cut short is "cancelled" (its
            # partial result kept, summary.cancelled) — it was "done", like a
            # complete scan.
            summary = payload.get("summary")
            status = "cancelled" if isinstance(summary, dict) and summary.get("cancelled") is True else "done"
            result: dict[str, Any] = {**payload, CONTEXT_KEY: context}
        except Exception as exc:  # noqa: BLE001 - a worker crash is recorded, not raised into the thread
            # D16: log the traceback and record the failure as coverage +
            # limitation — never just a bare message (fail-closed: the scan
            # did not complete, so nothing in it may read as a conclusion).
            known = isinstance(exc, (OSError, ValueError)) and not isinstance(exc, UnicodeError)
            if not known:
                logger.exception("scan job %s failed", job_id)
            # X4: a missing / unreadable folder keeps its Korean reason as the error (as the synchronous 400 does)
            reason = str(exc) if known else failure_reason(exc)
            result = {
                "error": reason if known else "폴더 검사 작업이 실패했습니다",
                "detail": reason,
                "coverage": [{"check": "scan_job", "status": "failed", "reason": reason}],
                "limitations": [f"검사 작업 실패 — {reason}. 이 작업의 결과는 결론으로 사용할 수 없습니다."],
            }
            status = "error"
        note = STORE.finish(job_id, status, result, evidence=evidence)
        if note:
            STORE.annotate(job_id, "scan_job_note", note)

    threading.Thread(target=work, daemon=True, name=f"scan-job-{job_id}").start()
    return {"job_id": job_id, "status": "running"}


def _scan_status_payload(query: str, *, owner: str = LOCAL_OWNER, default_folder: Path | None = None) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return ApiError(JOB_PARAM_REQUIRED, 400)  # X4
    try:
        entry = STORE.get(job_id, owner, evidence=_effective_roots(default_folder))
    except JobIntegrityError as exc:
        return ApiError(str(exc), JOB_INTEGRITY_STATUS)
    if entry is None:
        return ApiError(JOB_UNKNOWN, 404)  # X4 (another owner's job is "unknown" too)
    payload: dict[str, object] = {"job_id": job_id, "status": entry["status"]}
    # GUI redesign (U-7): progress of the job — rows finished (``done``), rows the scan will report (``total``;
    # null until the folder is enumerated), the escaped name of the row finished last (``current``), the last
    # few finished rows (``recent``) and the seconds since the job started (``elapsed_seconds``).
    raw_progress = entry.get("progress")
    progress: dict[str, Any] = raw_progress if isinstance(raw_progress, dict) else {}
    payload["done"] = progress.get("done", 0)
    payload["total"] = progress.get("total")
    payload["current"] = progress.get("current")
    payload["recent"] = list(progress.get("recent") or [])
    created, finished = entry.get("created"), entry.get("finished")
    if isinstance(created, (int, float)):
        payload["elapsed_seconds"] = round((finished if isinstance(finished, (int, float)) else time.time()) - created, 1)
    if entry["status"] != "running":
        result = entry.get("result")
        payload["result"] = client_payload(result) if isinstance(result, dict) else result
    return payload


def report_analysis_options(options: AnalysisOptions) -> dict[str, object]:
    """R19-3 (round 19): the analysis options a web report re-analyzed every row with — names, never paths.

    The GUI posted the options of the scan it was *running*, not of the rows
    on screen, and the server re-analyzed with them; the report did not say
    which options it used. The signed body now carries them
    (``analysis_options``) and every rendering prints them.
    """
    model_path = options.model_path
    models = [Path(path).name for path in (model_path if isinstance(model_path, tuple) else (model_path,)) if path is not None]
    return {
        "pixel": options.pixel_mode,
        "heatmaps": bool(options.heatmaps),
        "deep_signals": bool(options.deep_signals),
        "default_engine": not options.no_default_engine,
        "model_path": models,
        "fusion_profile": Path(options.fusion_profile).name if options.fusion_profile is not None else None,
    }


def _scan_cancel_payload(query: str, *, owner: str = LOCAL_OWNER) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return ApiError(JOB_PARAM_REQUIRED, 400)  # X4
    outcome = STORE.cancel(job_id, owner)
    if outcome is None:
        return ApiError(JOB_UNKNOWN, 404)  # X4
    return {"job_id": job_id, **outcome}


def _analyze_file_payload(query: str) -> dict[str, object]:
    """Single-file analysis (``/api/analyze-file``).

    D3: the conclusion is the scan result (``analysis_api.analyze_rows``,
    three verdicts) — the same path, photo gate and thresholds as /api/scan;
    an archive is expanded like the folder scan (B1) and ``rows`` lists the
    member and container rows.
    The provenance-metadata scan and the tool-marker match ride along as
    layer diagnostics (reference, no band). No pixel pre-screen runs here:
    scan's own pixel layer is gated by the photo classifier and recorded in
    coverage.
    """
    from .cli_standalone import analysis_result_for_path, tool_attribution
    from .layer_diagnostic import to_layer_diagnostic

    params = parse_qs(query)
    try:
        file_path = _b64_or_text(params, "file")  # R13-3: file_b64 for a non-UTF-8 name
    except ValueError as exc:
        return ApiError(str(exc), 400)

    if not file_path:
        return ApiError(FILE_PARAM_REQUIRED, 400)  # X4
    from .analysis_api import SingleFileError, check_single_file, is_symlink_path
    from .cli_standalone import symlink_layer

    requested = expand_user(file_path)
    link = is_symlink_path(requested)
    # G31: confined to the operator roots even when none are registered
    # (then only the server's default folder) — raises ReadRootDenied (403).
    # G6: a symbolic link is confined by where the link itself lives (its
    # resolved folder + its own name) and analyzed like a scan row — skipped,
    # target never opened — instead of being followed.
    if link:
        _require_read_root(requested.parent)
        path = requested.parent.resolve() / requested.name
    else:
        path = _require_read_root(requested)
    try:
        check_single_file(path)
    except SingleFileError as exc:
        # X4: a missing file is 404, a folder 400.
        return ApiError(str(exc), 404 if not path.exists() else 400)

    options = _web_options()
    thresholds = load_thresholds(options)
    try:
        response, _ = analysis_result_for_path(path, options, command="analyze-file", thresholds=thresholds)
    except Exception as exc:
        logger.exception("file analysis failed")
        # Detail stays in `detail` so the GUI shows a clean headline instead
        # of a raw exception sentence; the type/message aid local debugging.
        return ApiError("파일 분석 중 오류가 발생했습니다", 500, detail=failure_reason(exc))
    response["file"] = str(path)
    response["file_b64"] = fs_b64encode(str(path))  # R13-3: the bytes, for a non-UTF-8 name
    if link:
        response["layer_diagnostics"] = {
            "provenance_metadata": symlink_layer("provenance_metadata", "출처 메타데이터 계층"),
            "tool_candidates": symlink_layer("tool_attribution", "생성 도구 표지 대조"),
        }
        response.update(_provenance(options, thresholds))
        return response
    layers: dict[str, Any] = {}
    try:
        from .c2pa import analyze_metadata_forensic

        layers["provenance_metadata"] = to_layer_diagnostic(
            "provenance_metadata", analyze_metadata_forensic(path).to_json(), layer_label="출처 메타데이터 계층"
        )
    except Exception as exc:
        logger.exception("provenance layer failed for %s", path)
        _layer_error(response, "provenance_metadata", exc)
    try:
        layers["tool_candidates"] = to_layer_diagnostic(
            "tool_attribution", tool_attribution(path).to_json(), layer_label="생성 도구 표지 대조"
        )
    except Exception as exc:
        logger.exception("tool attribution layer failed for %s", path)
        _layer_error(response, "tool_candidates", exc)
    response["layer_diagnostics"] = layers
    response.update(_provenance(options, thresholds))
    return response


def _stats_payload(default_folder: Path | None = None) -> dict[str, object]:
    """Handle stats request with real package facts.

    GUI redesign (U-8): ``default_folder`` — the server's ``--folder`` as an
    absolute path (``default_folder_b64``: its file-system bytes, R12-4), or
    null — so the GUI can prefill the scan folder; ``pdf_available`` — whether
    the PDF renderer (pymupdf) is installed, so PDF exports can be offered
    only when they can be produced.
    """
    from .pdf_backend import pymupdf_available

    import importlib.metadata

    try:
        version = importlib.metadata.version("deepfake-lens")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    package_dir = Path(__file__).parent
    module_count = sum(1 for entry in package_dir.glob("*.py") if not entry.name.startswith("_"))
    folder = scan_root_text(default_folder) if default_folder is not None else None
    return {
        "status": "ok",
        "version": version,
        "modules": module_count,
        "default_folder": folder,
        "default_folder_b64": fs_b64encode(folder) if folder is not None else None,
        "pdf_available": bool(pymupdf_available()),
        **desktop_stats_fields(),
    }


def desktop_stats_fields() -> dict[str, object]:
    """DESK: ``desktop``, ``roots``, ``signing`` and ``idle_exit_minutes`` of /api/stats (the plain server says desktop false)."""
    from . import desktop as _desktop
    from .signing import key_id_for, resolve_report_key

    key = resolve_report_key()
    state = _desktop.STATE
    signing: dict[str, object] = {
        "available": key is not None,
        "source": (state.key_source if state is not None and key is not None else ("env" if key is not None else None)),
        "key_id": key_id_for(key) if key is not None else None,
    }
    return {
        "desktop": state is not None,
        "roots": [scan_root_text(root) for root in _effective_roots(None)] if _READ_ROOTS else [],
        "signing": signing,
        "idle_exit_minutes": state.idle_exit_minutes if state is not None else None,
        "instance_user": _desktop.instance_user() if state is not None else None,
    }


def roots_payload() -> dict[str, object]:
    """DESK ``GET /api/roots``: the registered read roots in registration order."""
    with _READ_ROOTS_LOCK:
        roots = list(_READ_ROOTS)
    return {"roots": [{"path": scan_root_text(root), "path_b64": fs_b64encode(scan_root_text(root))} for root in roots]}


def register_user_folder(folder: Path) -> Path:
    """DESK: register a folder the person chose in the OS dialog as a read root (desktop mode only — the caller checks).

    Unlike the start-up registration this never evicts an earlier root (a running scan's previews would 403);
    past :data:`_READ_ROOTS_MAX` it raises ValueError(:data:`desktop.ROOTS_FULL`).
    """
    from .desktop import FOLDER_TOO_WIDE, ROOTS_FULL, FolderTooWide, folder_too_wide

    resolved = expand_user(folder).resolve()
    reason = folder_too_wide(resolved)
    if reason is not None:
        raise FolderTooWide(FOLDER_TOO_WIDE.format(what=reason))
    with _READ_ROOTS_LOCK:
        if resolved in _READ_ROOTS:
            return resolved
        if len(_READ_ROOTS) >= _READ_ROOTS_MAX:
            raise ValueError(ROOTS_FULL)
        _READ_ROOTS[resolved] = None
    return resolved


def _extract_metadata(path: Path) -> dict[str, str]:
    """Extract metadata from file.

    Reads at most ``DEFAULT_METADATA_BYTES`` — the PNG text/iTXt chunks this
    parses live in the header region anyway, and an unbounded read of a
    user-supplied path is a memory-exhaustion vector.
    """
    import struct

    metadata: dict[str, str] = {}
    try:
        if path.stat().st_size > DEFAULT_METADATA_BYTES:
            return metadata
        data = path.read_bytes()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            offset = 8
            while offset + 8 <= len(data):
                length = struct.unpack(">I", data[offset:offset+4])[0]
                chunk_type = data[offset+4:offset+8]
                if chunk_type in (b"tEXt", b"iTXt"):
                    chunk_data = data[offset+8:offset+8+length]
                    if b"\x00" in chunk_data:
                        key, value = chunk_data.split(b"\x00", 1)
                        metadata[key.decode("latin-1", errors="ignore")] = value.decode("utf-8", errors="ignore")
                offset += 12 + length
                if chunk_type == b"IEND":
                    break
    except (OSError, struct.error, ValueError) as exc:
        # Truncated/corrupt header: keep the chunks parsed so far, but log
        # it — this helper only feeds the legacy /api/analyze classifier.
        logger.warning("metadata extraction stopped early for %s: %s", path, failure_reason(exc))
    return metadata


# Operator-registered read roots (G31). Every endpoint that reads a path the
# caller names — /api/scan, /api/analyze-file, /api/heatmap, /api/preview
# and the report hashing/heatmap embedding — is confined to these. Only
# server setup registers them (``configure_read_roots`` from run_server:
# ``--folder`` and each ``--allow-root``); a request can never add one. With
# none registered, the server's own default folder (``--folder`` or the
# working directory it was started in) is the only root — never "anything".
# The `root` query parameter is kept for backward compatibility but can only
# narrow, never widen, the scope.
_READ_ROOTS_LOCK = threading.Lock()
_READ_ROOTS: OrderedDict[Path, None] = OrderedDict()
_READ_ROOTS_MAX = 64
READ_ROOT_DENIED_MESSAGE = "허용되지 않은 경로"


# N5: the frameworks' own error bodies were English — FastAPI {"detail":
# "Not Found"} / "Method Not Allowed", the stdlib server's 501 "Unsupported
# method ('PUT')". Both servers map every status they answer without a
# Korean message of their own to this text.
HTTP_ERROR_TEXT_KO: dict[int, str] = {
    400: "잘못된 요청입니다",
    401: "인증이 필요합니다",
    403: "허용되지 않은 요청입니다",
    404: "찾을 수 없는 경로입니다",
    405: "이 경로에서 허용되지 않는 요청 메서드입니다",
    408: "요청 시간이 초과되었습니다",
    411: "요청 본문 길이(Content-Length)가 필요합니다",
    413: "요청 본문이 너무 큽니다",
    414: "요청 주소가 너무 깁니다",
    415: "지원하지 않는 요청 본문 형식입니다",
    422: "요청 매개변수 오류입니다",
    429: "요청이 너무 많습니다 — 잠시 후 다시 시도하십시오",
    431: "요청 헤더가 너무 큽니다",
    500: "서버 내부 오류가 발생했습니다 — 상세는 서버 로그를 확인하십시오",
    501: "지원하지 않는 요청 메서드입니다",
    503: "서버를 일시적으로 사용할 수 없습니다",
    505: "지원하지 않는 HTTP 버전입니다",
}
HTTP_ERROR_TEXT_FALLBACK = "요청을 처리할 수 없습니다 (HTTP {code})"


def _has_hangul(text: str) -> bool:
    return any("\uac00" <= ch <= "\ud7a3" for ch in text)


def http_error_text(code: int, message: object = None, *, method: str | None = None) -> str:
    """The Korean error text for an HTTP status (N5).

    A message the server wrote itself (it carries Hangul) is kept; a
    framework's English reason ("Not Found", "Unsupported method ('PUT')")
    is replaced by the Korean text for the status. 405/501 name the method.
    """
    if isinstance(message, str) and message.strip() and _has_hangul(message):
        return message
    text = HTTP_ERROR_TEXT_KO.get(int(code), HTTP_ERROR_TEXT_FALLBACK.format(code=int(code)))
    if method and int(code) in (405, 501):
        text = f"{text}: {method.upper()}"
    return text


class ReadRootDenied(PermissionError):
    """The requested path is outside every operator-registered read root.

    HTTP layers turn this into status 403 with ``read_root_denied_body()`` —
    a fixed message that echoes no file content.
    """


# R22-6 (round 22): a path value with a NUL character (a query parameter, its base64 form, a report row's
# path / container / member / duplicate_of / heatmap_path, scan_root, an api-serve file_path or directory) reached
# the file system and ValueError("embedded null byte") was an HTTP 500 on both servers. NUL can never be part of a
# file-system path, so it is refused as a request error (400) before any file is touched. Other control
# characters are legal in POSIX file names (the hostile-name fixtures of R11-9 carry \r, \n, \t, ESC) and
# a scan of such a folder writes them in its rows — they are kept.
PATH_TEXT_NUL = "경로 값에 NUL 문자(\\x00)가 있습니다 — 파일 시스템 경로가 될 수 없습니다: {field}"


class PathTextInvalid(ValueError):
    """R22-6: a path value the request names cannot be a file-system path (NUL) — HTTP 400 on both servers."""


def reject_nul_path(value: object, field: str) -> None:
    """R22-6: raise :class:`PathTextInvalid` when a path value of the request holds a NUL character."""
    if isinstance(value, str) and "\x00" in value:
        raise PathTextInvalid(PATH_TEXT_NUL.format(field=field))
    if isinstance(value, (bytes, bytearray)) and b"\x00" in value:
        raise PathTextInvalid(PATH_TEXT_NUL.format(field=field))


def read_root_denied_body() -> dict[str, object]:
    return {"error": READ_ROOT_DENIED_MESSAGE}


def _register_read_root(folder: Path) -> None:
    """Register an operator read root. Call only from server setup."""
    resolved = expand_user(folder).resolve()
    with _READ_ROOTS_LOCK:
        if resolved in _READ_ROOTS:
            _READ_ROOTS.move_to_end(resolved)
            return
        while len(_READ_ROOTS) >= _READ_ROOTS_MAX:
            _READ_ROOTS.popitem(last=False)  # evict the oldest registration
        _READ_ROOTS[resolved] = None


def configure_read_roots(default_folder: Path | None, allow_roots: list[Path] | tuple[Path, ...] | None = None) -> None:
    """Server-setup hook: register ``--folder`` and every ``--allow-root``."""
    if default_folder is not None:
        _register_read_root(Path(default_folder))
    for root in allow_roots or ():
        _register_read_root(Path(root))


def _effective_roots(default_folder: Path | None = None) -> list[Path]:
    """Registered roots in registration order, or the default folder alone."""
    with _READ_ROOTS_LOCK:
        roots = list(_READ_ROOTS)
    if roots:
        return roots
    from . import desktop as _desktop

    if _desktop.STATE is not None:
        # DESK: the desktop server reads nothing until a folder is registered (the launcher's working folder is the
        # bundle; a terminal's is the home) — the roots shown on screen are the roots that exist.
        return []
    return [expand_user(default_folder or ".").resolve()]


def _require_read_root(path: Path, default_folder: Path | None = None) -> Path:
    """Resolve ``path`` and raise ReadRootDenied unless it is inside a root (PathTextInvalid for a NUL — R22-6)."""
    reject_nul_path(str(path), "경로")
    try:
        resolved = expand_user(path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE) from exc
    if not any(_is_within(resolved, root) for root in _effective_roots(default_folder)):
        raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)
    return resolved


def _b64_or_text(params: dict[str, list[str]], name: str) -> str:
    """R13-3: ``<name>_b64`` (URL-safe base64 of the file-system bytes) when given, else ``<name>``.

    A non-UTF-8 name cannot travel as text in a URL — a percent-encoded raw
    byte is decoded as U+FFFD — so ``file_b64``/``folder_b64`` (the values of
    a row's ``path_b64`` joined to ``scan_root_b64``, or ``os.fsencode`` of
    the path) take precedence, exactly as ``path_b64`` does for previews
    (R12-4). The decoded path is confined to the read roots like the text
    one. ValueError (:data:`json_text.PATH_B64_INVALID`) when malformed.
    """
    from .json_text import fs_b64decode

    encoded = params.get(f"{name}_b64", [""])[0]
    value = fs_b64decode(encoded) if encoded else params.get(name, [""])[0]
    reject_nul_path(value, f"{name}_b64" if encoded else name)  # R22-6
    return value


def _requested_folder(query: str, default_folder: Path | None) -> Path:
    params = parse_qs(query)
    if params.get("folder_b64", [""])[0]:
        return expand_user(_b64_or_text(params, "folder"))  # R13-3
    folder = params.get("folder", [str(default_folder or ".")])[0]
    reject_nul_path(folder, "folder")  # R22-6
    return expand_user(folder)


def _read_root_allows(path: Path, root_value: str = "", default_folder: Path | None = None) -> bool:
    """True when `path` sits under an operator-registered root.

    The caller-supplied root is also checked when present so a stale or
    mismatched root argument cannot broaden access beyond the registered
    set — both conditions must hold.
    """
    roots = _effective_roots(default_folder)
    if root_value:
        try:
            root = expand_user(root_value).resolve()
        except OSError:
            return False
        if not _is_within(path, root):
            return False
    return any(_is_within(path, root) for root in roots)


def _media_query_paths(query: str) -> tuple[str, str]:
    """(path, root) of a /api/preview or /api/heatmap query.

    R12-4: ``path_b64``/``root_b64`` (URL-safe base64 of the file-system
    bytes — the rows' ``path_b64``, a heatmap's ``heatmap_path_b64``, the scan
    payload's ``scan_root_b64``) take precedence over ``path``/``root``: a
    non-UTF-8 name cannot travel as text in a URL. A relative ``path_b64``
    (a row path) is joined to the root. ValueError when a value is malformed.
    """
    from .json_text import fs_b64decode

    params = parse_qs(query)
    path_b64 = params.get("path_b64", [""])[0]
    root_b64 = params.get("root_b64", [""])[0]
    root_value = fs_b64decode(root_b64) if root_b64 else params.get("root", [""])[0]
    path_value = fs_b64decode(path_b64) if path_b64 else params.get("path", [""])[0]
    reject_nul_path(root_value, "root_b64" if root_b64 else "root")  # R22-6: a ValueError — 400 bad-path
    reject_nul_path(path_value, "path_b64" if path_b64 else "path")
    if path_b64 and root_value and not os.path.isabs(path_value):
        path_value = os.path.join(root_value, path_value)
    return path_value, root_value


def _heatmap_payload(query: str) -> tuple[int, bytes, str]:
    try:
        path_value, root_value = _media_query_paths(query)
    except ValueError as exc:
        return 400, str(exc).encode("utf-8"), "bad-path"
    if not path_value:
        return 400, "경로가 없습니다".encode("utf-8"), "missing"
    path = expand_user(path_value).resolve()
    # Heatmaps live in the tool-owned output root (never in the evidence
    # folder, R-IN-1); those are served without a read root.
    if path.suffix.lower() != ".png" or not (_read_root_allows(path, root_value) or is_default_heatmap_output(path)):
        return 403, READ_ROOT_DENIED_MESSAGE.encode("utf-8"), "forbidden"  # G9: Korean body, ASCII header code
    try:
        data = path.read_bytes()
    except OSError:
        return 404, "파일을 찾을 수 없습니다".encode("utf-8"), "not-found"
    return 200, data, ""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# Media the result viewer may inline-preview. Deliberately excludes
# HTML/SVG and documents — preview is for media inspection, never for
# rendering active content inside the app.
_PREVIEW_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".m4v": "video/mp4",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac",
    ".opus": "audio/opus",
}
MAX_PREVIEW_BYTES = 128 * 1024 * 1024
PREVIEW_TOO_LARGE_MESSAGE = "파일이 너무 커서 미리보기를 제공하지 않습니다(상한 128 MiB)"
PREVIEW_NOT_MEDIA_MESSAGE = "미리보기를 지원하지 않는 형식입니다: {suffix} (이미지·영상·음성만)"


def _preview_payload(query: str) -> tuple[int, bytes, str, str]:
    """Serve a scanned media file for inline preview in the result viewer.

    Same trust model as /api/heatmap: the file must live under a
    server-registered scan root (see _read_root_allows), must be a known
    media type, and is served with nosniff so it can only render as media.
    """
    try:
        path_value, root_value = _media_query_paths(query)
    except ValueError as exc:
        return 400, str(exc).encode("utf-8"), "bad-path", ""
    if not path_value:
        return 400, "경로가 없습니다".encode("utf-8"), "missing", ""
    path = expand_user(path_value).resolve()
    mime = _PREVIEW_MIME.get(path.suffix.lower())
    if not _read_root_allows(path, root_value):
        return 403, READ_ROOT_DENIED_MESSAGE.encode("utf-8"), "forbidden", ""  # G9: Korean body, ASCII header code
    if mime is None:
        # P8 (round 8): a non-media file inside the roots was 403 (the
        # documented status is 400 — the request, not the place, is wrong).
        return 400, PREVIEW_NOT_MEDIA_MESSAGE.format(suffix=path.suffix or "(확장자 없음)").encode("utf-8"), "not-media", ""
    try:
        if path.stat().st_size > MAX_PREVIEW_BYTES:
            return 413, PREVIEW_TOO_LARGE_MESSAGE.encode("utf-8"), "too-large", ""
        data = path.read_bytes()
    except OSError:
        return 404, "파일을 찾을 수 없습니다".encode("utf-8"), "not-found", ""
    return 200, data, "", mime


def _provenance(options: AnalysisOptions | None = None, thresholds: Any = None) -> dict[str, object]:
    """Coverage + threshold provenance attached to every scan-shaped payload.

    A response that omits these lets a heuristic-only run read as a full
    neural one downstream (GUI banner, /api/report, exported JSON). G7: the
    thresholds reported are the ones the analysis actually used.
    """
    opts = options or _web_options()
    try:
        return provenance(opts, thresholds)
    except Exception as exc:
        logger.exception("weights coverage unavailable")
        from .core import _thresholds_json
        return {"coverage": {"error": failure_reason(exc)}, "thresholds": _thresholds_json(thresholds)}


def _model_active(item: dict[str, object]) -> bool:
    result = item.get("result")
    if not isinstance(result, dict):
        return False
    ma = result.get("model_analysis")
    return isinstance(ma, dict) and bool(ma.get("available"))


def _summarize_records(items: list[dict[str, object]], source: str) -> dict[str, object]:
    """Mirror core.summarize() — identical inputs must yield identical counts
    across CLI JSON, web uploads, and report headers. A row with a result
    is counted by its verdict unless its status is failed/unsupported/
    duplicate/skipped (``result_types.is_verdict_row``) — archive container
    rows included (R5); everything else lands in unsupported_or_failed,
    duplicates or skipped.
    """
    from .result_types import is_verdict_row

    def _status(item: dict[str, object]) -> str:
        return str(item.get("status") or ("failed" if item.get("error") else "analyzed"))

    def _verdict(item: dict[str, object]) -> str:
        result = item.get("result")
        return str(result.get("verdict_code") or "undetermined") if isinstance(result, dict) else ""

    def _has_failed_check(item: dict[str, object]) -> bool:
        result = item.get("result")
        coverage = result.get("coverage") if isinstance(result, dict) else None
        return isinstance(coverage, list) and any(isinstance(e, dict) and e.get("status") == "failed" for e in coverage)

    analyzed = [i for i in items if is_verdict_row(_status(i), isinstance(i.get("result"), dict))]
    verdict_rows = {id(i) for i in analyzed}
    # D16: verdict counts only — same keys as BatchScanSummary.to_json().
    return {
        "manipulation_evidence": sum(1 for item in analyzed if _verdict(item) == "manipulation_evidence"),
        "authenticity_evidence": sum(1 for item in analyzed if _verdict(item) == "authenticity_evidence"),
        "undetermined": sum(1 for item in analyzed if _verdict(item) == "undetermined"),
        "checks_failed": sum(1 for item in analyzed if _has_failed_check(item)),
        "container_rows": sum(1 for item in analyzed if item.get("kind") == "archive"),
        "total": len(items),
        "analyzed": len(analyzed),
        "unsupported_or_failed": sum(1 for i in items if id(i) not in verdict_rows and _status(i) not in {"duplicate", "skipped"}),
        "duplicates": sum(1 for i in items if _status(i) == "duplicate"),
        "skipped": sum(1 for i in items if _status(i) == "skipped"),
        "external_model_active": sum(1 for item in analyzed if _model_active(item)),
        "source": source,
    }


def _upload_base_name(filename: str) -> str:
    """The name an uploaded file is written under: the last ``/``- or ``\\``-separated component of the client's name.

    R29-3 (round 29): every upload path decides *what* the bytes are (an archive or one file) and *what name* they
    are analyzed under from this one name — ``x.zip\\`` was written as ``x.zip`` but routed by the raw name (no
    archive suffix) to the single-file path, which gave only the container row of what a folder scan expands.
    """
    return Path(filename.replace("\\", "/")).name


def _stand_in_name(base: str) -> str:
    """``upload`` + the base name's suffix — a compound archive suffix (``.tar.gz``) kept whole (R29-2)."""
    lower = base.lower()
    for compound in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lower.endswith(compound):
            return "upload" + base[-len(compound):]
    return "upload" + Path(base).suffix[:16]


def _archive_upload_items(
    filename: str, payload: bytes, *, options: AnalysisOptions, thresholds: Any,
) -> list[dict[str, object]]:
    """Analyze an uploaded archive exactly as a folder scan analyzes it (B1).

    The bytes are written under their own file name into a fresh temp
    folder and handed to ``analysis_api.analyze_rows`` — the folder
    scanner's own body — so the rows are the scan's rows: members as
    ``archive.zip::inner/path.png``, the container row with every refused
    member (traversal, absolute path, link, "압축 예산 초과(...)" bomb/budget
    limits) as a skipped ``archive_member`` coverage entry plus a
    limitation, an unopenable archive as a failed ``archive`` check, a
    missing extractor as a skipped one, and the uploaded archive's SHA-256.
    The temp folder (archive and extracted tree) is deleted before
    returning.

    R29-2 (round 29): the archive is written through :func:`_upload_copy`
    like a single upload — under the client's base name, else (a name this
    system cannot create: too long, NUL, not encodable) under the stand-in
    ``upload.zip`` — so its members are analyzed instead of the whole row
    failing with "OSError: … 경로가 너무 깁니다"; the rows still carry the
    client's name, and a name check on a row named by the stand-in is
    marked skipped (:func:`_mark_name_not_checked`).
    """
    from .result_text import escape_row_path, member_row_path

    with _upload_copy(filename, payload) as (target, named):
        base = target.name
        records: list[dict[str, object]] = []
        shown = escape_row_path(filename)
        for row in analyze_rows(target, options, thresholds=thresholds):
            record = row.to_json()
            if base != filename:
                # Report the name the client sent (a path in the archive
                # name is kept as given, like the pre-B1 upload rows).
                # R9-1: a member row is renamed through its container/member
                # fields — never by splitting its "::" display path.
                old_path = record.get("path")
                if row.member is not None:
                    record["container"] = shown
                    record["path"] = member_row_path(shown, row.member)
                elif old_path == escape_row_path(base):
                    record["path"] = shown
                    if not named:
                        _mark_name_not_checked(record)
                if record.get("name") == base:
                    record["name"] = filename
                elif record.get("name") == old_path:
                    record["name"] = record["path"]
            records.append(record)
        return records


# R28-1 (round 28): the reason a single upload's file-name check names when the client's
# name cannot be a file name here (it is not checked under another name).
UPLOAD_NAME_NOT_CHECKED = "업로드 파일의 원래 이름을 이 시스템에서 파일 이름으로 쓸 수 없어 원래 이름으로 검사하지 않음"


@contextlib.contextmanager
def _upload_copy(filename: str, payload: bytes) -> Iterator[tuple[Path, bool]]:
    """Write one uploaded file under the client's own base name; yield ``(path, named)``.

    R28-1 (round 28): a single upload was analyzed as a ``NamedTemporaryFile``
    (``tmpXXXX.png``), so every check that reads the file's name — the
    platform-trace file-name format check above all — ran on the temp name
    and reported "일치 없음" for ``KakaoTalk_…png`` while a folder scan or a
    zip upload of the same bytes matched. The bytes are now written into a
    fresh temp folder under the base name the client sent
    (:func:`_upload_base_name`), so the row is the folder scan's row for that
    one file and no temp name reaches it. When that name cannot be a file
    name here (empty, ``.``/``..``, NUL, too long, not encodable) the bytes go
    under the stand-in :func:`_stand_in_name` (``upload.png``, ``upload.zip``,
    ``upload.tar.gz``; plain ``upload`` when even the suffix cannot be part
    of a name) and ``named`` is False — the caller then marks the
    name-dependent check skipped instead of reporting a check it did not run.
    Used by single uploads and, since R29-2, by archive uploads. The folder
    is deleted on exit.
    """
    folder = Path(tempfile.mkdtemp(prefix="dflens-up-", dir=scratch_dir())).resolve()
    try:
        base = _upload_base_name(filename)
        stand_in = _stand_in_name(base)
        # (name, named): the client's base name first, the stand-ins last.
        names = ([(base, True)] if base not in ("", ".", "..") else []) + [(stand_in, False)] + ([("upload", False)] if stand_in != "upload" else [])
        for index, (name, named) in enumerate(names):
            try:
                target = folder / name
                target.write_bytes(payload)  # a fresh folder: nothing to overwrite
            except (OSError, ValueError, UnicodeError):
                if index == len(names) - 1:
                    raise
                with contextlib.suppress(OSError, ValueError, UnicodeError):
                    (folder / name).unlink(missing_ok=True)
                continue
            break
        yield target, named
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _mark_name_not_checked(record: dict[str, object]) -> None:
    """R28-1: an upload analyzed under a stand-in name did not check its name — say so (skipped)."""
    from .platform_traces import FILE_NAME_CHECK

    result = record.get("result")
    coverage = result.get("coverage") if isinstance(result, dict) else None
    for entry in coverage if isinstance(coverage, list) else []:
        if isinstance(entry, dict) and entry.get("check") == FILE_NAME_CHECK:
            entry["status"] = "skipped"
            entry["reason"] = UPLOAD_NAME_NOT_CHECKED


def _failed_upload_row(filename: str, exc: BaseException) -> dict[str, object]:
    """An upload whose analysis raised: one failed row under the client's name (never a missing file, never a 500)."""
    from .result_text import escape_row_path

    logger.exception("upload analysis failed: %s", filename)
    return {"name": filename, "path": escape_row_path(filename), "status": "failed", "error": failure_reason(exc)}


def _upload_part_rows(filename: str, payload: bytes, *, options: AnalysisOptions, thresholds: Any) -> list[dict[str, object]]:
    """The rows of one uploaded file — the folder scan's rows for those bytes under the client's base name.

    R29 (round 29): the one body behind ``/api/analyze-upload`` (web and api-serve) and the archive branch of
    ``/api/check``. An archive (decided on :func:`_upload_base_name`, R29-3) gives its member rows and container row,
    any other file its one row; a client name this system cannot create is analyzed under a stand-in with the name
    check skipped (R28-1, R29-2); an analysis that raises is one failed row (R29-2: was a 500 on ``/api/check``).
    """
    from .archives import is_archive
    from .result_text import escape_row_path

    try:
        if is_archive(_upload_base_name(filename)):
            return _archive_upload_items(filename, payload, options=options, thresholds=thresholds)
        # R28-1: under the client's base name (name-dependent checks see it), never a temp name.
        with _upload_copy(filename, payload) as (upload_path, named):
            item = analyze_path(upload_path, options, thresholds=thresholds)
        record = item.to_json()
        if not named:
            _mark_name_not_checked(record)
        # R10-6: the row path of an uploaded file is escaped like any
        # real file's (R9-1) — "t:::c.png" is the row "t\:\:\:c.png" and
        # never holds "::"; ``name`` keeps the name the client sent.
        record["path"] = escape_row_path(filename)
        record["name"] = filename
        return [record]
    except Exception as exc:  # noqa: BLE001 - logged by _failed_upload_row
        # Per-upload failure is a failed row (status "failed"), never a
        # silently missing file.
        return [_failed_upload_row(filename, exc)]


def _upload_file_parts(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """``(client name, bytes)`` of every part that is a file: it has a file name, its bytes empty or not.

    R29-1 (round 29): ``/api/check`` chose its part by ``p.get_payload(decode=True)`` being truthy, so a 0-byte file
    was "업로드된 파일이 없습니다" there while ``/api/analyze-upload`` and a folder scan analyzed it. Every upload
    path selects parts here. R30-1..R30-3 (round 30): the body is split by :mod:`form_data` (was the e-mail parser,
    which dropped a ``message/*`` part, re-serialized a ``multipart/*`` part and unescaped backslashes in names);
    :class:`form_data.FormDataError` carries the 400 reason (R30-5).
    """
    return _form_file_parts(content_type, body)


# R29-4 (round 29): the reason a file over the upload cap was not analyzed. The cap counts files, not rows — it
# counted rows, so an archive with 20 members made the next file "파일 수 상한(20) 초과" in a two-file upload.
UPLOAD_FILES_OVER_CAP = "파일 수 상한({cap}개) 초과 — 이 요청의 파일 {count}개 중 앞의 {cap}개만 분석함"

# R29-5 (round 29): /api/check is the single-file quick check; a request with several files is refused, not cut to one.
CHECK_ONE_FILE = "빠른 검사는 파일 1개만 받습니다(받은 파일 {count}개) — 여러 파일은 업로드 검사(/api/analyze-upload)로 보내십시오"

# R29-6 (round 29): /api/compare compares exactly two files; any other count is refused with the count received.
COMPARE_TWO_FILES = "비교할 파일 2개가 필요합니다(받은 파일 {count}개)"


@in_worker_scope  # R20-2: one audio worker for all files (was one per file)
def _analyze_upload_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Analyze files uploaded via multipart/form-data.

    Each part is written to a temporary file, analyzed with the default
    engine profiles, then deleted. Archives are extracted and each member
    analyzed as its own row. The server never persists uploads.
    """
    if not is_form_data(content_type):  # R31-2: the media type, case-insensitively (was a case-sensitive substring)
        return ApiError("multipart/form-data 업로드가 필요합니다", 400)
    from .result_text import escape_row_path

    try:
        files = _upload_file_parts(content_type, body)  # R30-1..R30-3, R30-5; P8 (cut off)
    except FormDataError as exc:
        return ApiError(str(exc), 400)
    options = _web_options()
    thresholds = load_thresholds(options)
    items: list[dict[str, object]] = []
    for index, (filename, payload) in enumerate(files):
        if index >= MAX_UPLOAD_FILES:
            # R29-4: every file over the cap is its own skipped row with the true count (was one failed row, by rows).
            items.append({"name": filename, "path": escape_row_path(filename), "status": "skipped",
                          "error": UPLOAD_FILES_OVER_CAP.format(cap=MAX_UPLOAD_FILES, count=len(files))})
            continue
        items.extend(_upload_part_rows(filename, payload, options=options, thresholds=thresholds))
    if not items:
        return ApiError("업로드된 파일이 없습니다", 400)
    _mark_uploads(items)  # P6
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "summary": _summarize_records(items, "upload"),
        "items": items,
        **_provenance(options, thresholds),
    }


def _provenance_layer(raw: dict[str, Any]) -> dict[str, Any]:
    """c2pa.analyze_metadata_forensic output as a layer diagnostic (D1/D3)."""
    from .layer_diagnostic import to_layer_diagnostic

    return to_layer_diagnostic("provenance_metadata", raw, layer_label="출처 메타데이터 계층")


def _text_statistics_layer(raw: dict[str, Any]) -> dict[str, Any]:
    """text_advanced output as a layer diagnostic (D1)."""
    from .layer_diagnostic import to_layer_diagnostic

    return to_layer_diagnostic("text_statistics", raw, layer_label="텍스트 문체 통계 계층")


def compare_layer(raw: dict[str, Any]) -> dict[str, Any]:
    """core.compare_files output as a layer diagnostic (D1)."""
    from .layer_diagnostic import to_layer_diagnostic

    return to_layer_diagnostic("compare", raw, layer_label="두 파일 유사도 계층")


def _check_text_payload(text: str, *, watermark_secret: str | None = None, watermark_gamma: float = 0.25) -> dict[str, object]:
    """Unified text check: core scan heuristics + neural member ensemble
    + fingerprint probes in one payload.

    The text is written to a temp .txt so the full file pipeline (including
    the causal-LM/classifier members) runs exactly as it would on a saved
    document; the temp file is deleted immediately. When ``watermark_secret``
    is supplied, a KGW green-list test runs under that key.
    """
    trimmed = text.strip()
    if len(trimmed) < 8:
        return ApiError("분석할 텍스트가 너무 짧습니다 (8자 이상).", 400)
    if len(trimmed) > 256 * 1024:
        return ApiError("텍스트가 256KB를 초과합니다.", 400)
    options = _web_options()
    thresholds = load_thresholds(options)
    # delete=False: Windows cannot reopen a delete=True NamedTemporaryFile,
    # so the analyzers below would hit Permission denied.
    tmp_name = ""
    layer_errors: dict[str, Any] = {}
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False, dir=scratch_dir()) as tmp:
            tmp.write(trimmed)
            tmp_name = tmp.name
        item = analyze_path(tmp_name, options, thresholds=thresholds)
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = _provenance_layer(analyze_metadata_forensic(Path(tmp_name)).to_json())
        except Exception as exc:
            logger.exception("forensic layer failed")
            forensic = None
            _layer_error(layer_errors, "forensic", exc)
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    from .text_advanced import analyze_text_advanced
    advanced = analyze_text_advanced(trimmed)
    watermark = None
    if watermark_secret:
        try:
            from .watermark import detect_kgw_watermark
            watermark = detect_kgw_watermark(trimmed, secret=watermark_secret, gamma=watermark_gamma).to_json()
        except Exception as exc:
            logger.exception("watermark layer failed")
            watermark = {"available": False, "reference_band": "unavailable", "reference_note": "워터마크 검사 실패", "error": failure_reason(exc)}
            _layer_error(layer_errors, "watermark", exc)
    record = item.to_json()
    _set_row_name(record, "pasted-text")
    record["path"] = "pasted-text"
    record["source"] = UPLOAD_SOURCE  # P6: pasted text is no file of a read root
    _sync_path_b64(record)
    _record_upload_rows([record])  # R22-8
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "mode": "text",
        "item": record,
        "advanced": _text_statistics_layer(advanced.to_json()),
        "forensic": forensic,
        "watermark": watermark,
        **layer_errors,
        **_provenance(options, thresholds),
    }


def _check_file_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Unified single-file check: full scan + forensics + text probes."""
    if not is_form_data(content_type):  # R31-2: the media type, case-insensitively (was a case-sensitive substring)
        return ApiError("multipart/form-data 또는 application/json 본문이 필요합니다", 400)
    try:
        files = _upload_file_parts(content_type, body)  # R30-1..R30-3, R30-5; P8 (cut off)
    except FormDataError as exc:
        return ApiError(str(exc), 400)
    if not files:  # R29-1: a 0-byte file is a file (was "업로드된 파일이 없습니다")
        return ApiError("업로드된 파일이 없습니다", 400)
    if len(files) > 1:
        # R29-5: the quick check takes one file — the others were silently dropped (only the first was analyzed).
        return ApiError(CHECK_ONE_FILE.format(count=len(files)), 400)
    [(filename, payload)] = files
    from .archives import is_archive
    from .result_text import escape_row_path
    options = _web_options()
    thresholds = load_thresholds(options)
    if is_archive(_upload_base_name(filename)):  # R29-3: decided on the name the bytes are written under
        # R29-2: the same rows as /api/analyze-upload — a stand-in name or a failed row, never a 500.
        items = _mark_uploads(_upload_part_rows(filename, payload, options=options, thresholds=thresholds))  # P6
        return {
            "schema_version": SCAN_JSON_SCHEMA_VERSION,
            "mode": "files",
            "summary": _summarize_records(items, "upload"),
            "items": items,
            **_provenance(options, thresholds),
        }
    forensic = None
    advanced = None
    layer_errors: dict[str, Any] = {}
    try:
        # R28-1: as /api/analyze-upload — under the client's base name, never a temp name.
        with _upload_copy(filename, payload) as (tmp_path, named):
            item = analyze_path(tmp_path, options, thresholds=thresholds)
            record = item.to_json()
            if not named:
                _mark_name_not_checked(record)
            try:
                from .c2pa import analyze_metadata_forensic
                forensic = _provenance_layer(analyze_metadata_forensic(tmp_path).to_json())
            except Exception as exc:
                logger.exception("forensic layer failed")
                _layer_error(layer_errors, "forensic", exc)
            if item.kind == "text":
                try:
                    from .text_advanced import analyze_text_advanced
                    advanced = _text_statistics_layer(analyze_text_advanced(payload.decode("utf-8", errors="replace")).to_json())
                except Exception as exc:
                    logger.exception("text-advanced layer failed")
                    _layer_error(layer_errors, "advanced", exc)
    except Exception as exc:  # noqa: BLE001 - logged by _failed_upload_row
        # R29-2: the failed row /api/analyze-upload gives for the same file (was a 500).
        record = _failed_upload_row(filename, exc)
        forensic, advanced, layer_errors = None, None, {}
    _set_row_name(record, filename)
    record["path"] = escape_row_path(filename)  # R10-6: as /api/analyze-upload
    record["source"] = UPLOAD_SOURCE  # P6
    _sync_path_b64(record)
    _record_upload_rows([record])  # R22-8
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "mode": "file",
        "item": record,
        "advanced": advanced,
        "forensic": forensic,
        **layer_errors,
        **_provenance(options, thresholds),
    }


def _compare_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Two-file comparison: speaker distance for audio pairs, stylometry
    for text/document pairs. Both parts must carry filenames."""
    if not is_form_data(content_type):  # R31-2: the media type, case-insensitively (was a case-sensitive substring)
        return ApiError("multipart/form-data 본문이 필요합니다", 400)
    # R29-6 (round 29): parts as every upload path selects them — a 0-byte file is a file (it was dropped, so two
    # files sent gave "비교할 파일 2개가 필요합니다"), and a third file is refused instead of silently ignored.
    try:
        parts = _upload_file_parts(content_type, body)  # R30-1..R30-3, R30-5; P8 (cut off)
    except FormDataError as exc:
        return ApiError(str(exc), 400)
    if len(parts) != 2:
        return ApiError(COMPARE_TWO_FILES.format(count=len(parts)), 400)
    with contextlib.ExitStack() as stack:
        # The comparison reads only each file's suffix (its kind); written like every upload (R28-1, R29-2), so a
        # client name this system cannot create no longer makes the temp file fail (a 500).
        tmp_paths = [stack.enter_context(_upload_copy(name, payload))[0] for name, payload in parts]
        from .core import compare_files
        result = compare_files(tmp_paths[0], tmp_paths[1])
        if isinstance(result, dict) and not result.get("error"):
            # D1: similarity is an unmeasured reference number — the
            # former same/unclear/different band is not returned.
            options = _web_options()
            diag = compare_layer(result)
            diag.update(_provenance(options, load_thresholds(options)))
            return diag
        if isinstance(result, dict) and result.get("error"):
            return ApiError(str(result["error"]), 400)  # X4: e.g. a mixed audio/text pair
        return result


def _feedback_path() -> Path:
    """Where web-UI examiner labels accumulate (``DEEPFAKE_LENS_FEEDBACK`` overrides it).

    R22-3 (round 22): the fixed per-user ``feedback.jsonl`` of :mod:`user_data`
    (was ``~/.deepfake-lens/feedback.jsonl`` — inside the served HOME, or the
    working folder with a relative HOME). Raises :class:`user_data.UserDataRefused`
    when there is no place for it.
    """
    from .user_data import FEEDBACK_ENV, FEEDBACK_FILE, record_path

    return record_path(FEEDBACK_FILE, FEEDBACK_ENV)


def evidence_folders(default_folder: Path | None = None) -> list[Path]:
    """R22-3: the folders a server examines — its read roots (or its default folder) — where it never writes."""
    return _effective_roots(default_folder)


# R22-3: an examiner record that would be written inside the evidence (or nowhere) is refused with this status.
USER_DATA_REFUSED_STATUS = 409


def _feedback_payload(body: bytes, evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, object]:
    """Record one examiner verdict from the web UI.

    Appends a ``{path, expected_label, notes, embedded_result}`` JSONL row the
    ``feedback`` CLI command can consume directly — ``embedded_result`` carries
    the scan result so labeled rows need no rescan.
    """
    try:
        data = load_json_body(body)
    except JsonBodyError as exc:
        return ApiError(str(exc), 400)  # R11-3: a too-deep body was a 500
    if not isinstance(data, dict):
        return ApiError("피드백 요청 본문은 JSON 객체여야 합니다", 400)
    label = str(data.get("expected_label", "") or "").strip().lower()
    if not (is_positive_label(label) or is_negative_label(label)):
        return ApiError("expected_label은 인식 가능한 라벨이어야 합니다 (예: `synthetic`, `real`)", 400)
    path = str(data.get("path") or data.get("name") or "").strip()
    if not path:
        return ApiError("path가 필요합니다", 400)
    entry: dict[str, object] = {
        "path": path,
        "expected_label": label,
        "notes": str(data.get("notes", "") or ""),
    }
    result = data.get("result")
    if isinstance(result, dict):
        # load_feedback reads the embedded scan result from the "result" key.
        entry["result"] = result
    from .user_data import FEEDBACK_ENV, FEEDBACK_FILE, UserDataRefused, check_outside, migrate_default, write_failed

    try:
        feedback_file = _feedback_path()
        check_outside(feedback_file, evidence, FEEDBACK_ENV)  # R22-3: never into the evidence
    except UserDataRefused as exc:
        return ApiError(str(exc), USER_DATA_REFUSED_STATUS)
    migrate_default(FEEDBACK_FILE, FEEDBACK_ENV, feedback_file)  # R23-8: the reviews' rule
    line = (json_dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    size: int | None = None
    try:
        feedback_file.parent.mkdir(parents=True, exist_ok=True)
        size = feedback_file.stat().st_size if feedback_file.is_file() else 0
        with feedback_file.open("ab") as handle:
            handle.write(line)
    except OSError as exc:
        # R23-7 (round 23): a folder or file that cannot be written was a 500 (PermissionError); a line
        # half-written when the disk filled is cut off again, so the file holds whole entries only.
        if size is not None:
            with contextlib.suppress(OSError):
                os.truncate(feedback_file, size)
        failed = write_failed(feedback_file, exc, FEEDBACK_ENV)
        return ApiError(str(failed), failed.status)
    return {"ok": True, "feedback_file": str(feedback_file)}


# B8: /api/report?format=pdf without pymupdf answers this body with HTTP 501
# (both the stdlib web server and the FastAPI server) instead of a PDF.
# P8: the install command in backticks, as in evidence_statement (an unquoted
# "pip install" read as English prose). REV2-D4: the bundle ships pymupdf and
# has no pip — there the text asks for the diagnostics launcher's output.
PDF_REPORT_UNAVAILABLE_ERROR = bundle_text(
    "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다(설치: `pip install pymupdf`).", PDF_ENGINE_FAILED,
)
EVIDENCE_PDF_FAILED_HINT = bundle_text(
    "`pip install 'deepfake-lens[forensic]'` 후 재시도하거나 Markdown 출력을 사용하세요.", PDF_ENGINE_FAILED_HINT,
)
PDF_REPORT_UNAVAILABLE_BODY: dict[str, object] = {"error": PDF_REPORT_UNAVAILABLE_ERROR}
PDF_REPORT_UNAVAILABLE_STATUS = 501


# N11: /api/report answers a malformed request with HTTP 400 and a Korean
# error (was 200 + {"error"}); a renderer failure on the server side is 500.
REPORT_BAD_REQUEST_STATUS = 400
REPORT_RENDER_FAILED_STATUS = 500
EVIDENCE_PDF_FAILED_PREFIX = "증거설명서 PDF 생성 실패: "
# N9: the report format comes from ?format= or, failing that, the JSON body's
# "format" — both servers decide the content type from the same value.
# GUI redesign: ``evidence-preview`` — the evidence statement's PDF texts as JSON for the GUI preview, built from
# the same job and the same statement as ``evidence`` (unsigned; no change detection — the export does that).
REPORT_FORMATS = ("html", "pdf", "evidence", "evidence-statement", "json", "evidence-preview")
PDF_REPORT_FORMATS = ("pdf", "evidence", "evidence-statement")
REPORT_FORMAT_UNSUPPORTED = "지원되지 않는 보고서 형식입니다: 「{value}」 (" + ", ".join(f"`{name}`" for name in REPORT_FORMATS) + " 중 하나)"
REPORT_FORMAT_NOT_STRING = "`format` 값은 문자열이어야 합니다"


def report_error_status(body: dict[str, object]) -> int:
    """HTTP status for a JSON body returned by ``_report_payload``.

    200 for the signed report body (``format=json``); 501 for the missing
    PDF renderer (B8); 500 when the evidence-statement PDF could not be
    built; 400 for every request error (N11 — was 200 + ``{"error": ...}``).
    """
    if isinstance(body, ApiError):
        return body.status  # R19-13: 409 for rows that changed after the scan
    error = body.get("error")
    if error is None:
        return 200
    if error == PDF_REPORT_UNAVAILABLE_ERROR:
        return PDF_REPORT_UNAVAILABLE_STATUS
    if isinstance(error, str) and error.startswith(EVIDENCE_PDF_FAILED_PREFIX):
        return REPORT_RENDER_FAILED_STATUS
    return REPORT_BAD_REQUEST_STATUS


# G7 (round 5): /api/report request errors in Korean.
REPORT_BODY_NOT_OBJECT = "보고서 요청 본문은 JSON 객체여야 합니다"
REPORT_FIELD_NOT_OBJECT = "{field} 값은 JSON 객체여야 합니다"
REPORT_ITEM_MALFORMED = "업로드 항목 {index}번을 해석할 수 없습니다: {reason}"
REPORT_JSON_INVALID = JSON_BODY_INVALID
# P0R-6 (user decision 2026-10-17): the request names a scan job the server ran and holds — never rows. The signed
# row set, the options, the threshold/model provenance and the "기록되지 않은 파일"/cancel accounting are the job's.
REPORT_FIELDS = (
    "scan_job", "exhibit_paths", "uploads", "format", "side",
    "case_no", "case_name", "plaintiff", "defendant", "court", "law_firm", "contact", "center", "exhibit_no",
)
REPORT_CASE_FIELDS = ("case_no", "case_name", "plaintiff", "defendant", "court", "law_firm", "contact", "center", "exhibit_no")
REPORT_UNKNOWN_FIELD = (
    "보고서 요청에 알 수 없는 필드가 있습니다: {fields} — 보고서는 서버가 보관한 검사 작업(scan_job)으로만 만듭니다"
    "(받는 값: 검사 작업 id, 서증 선택, 업로드 결과, 형식, 제출 측과 사건 필드). 검사 행·옵션·요약은 보내지 마십시오"
)
REPORT_SCAN_JOB_REQUIRED = "보고서에 넣을 검사 작업(scan_job)이 필요합니다 — 폴더를 먼저 검사하고 그 응답의 scan_job 값을 보내십시오(업로드 결과만 넣으려면 uploads)"
REPORT_SCAN_JOB_NOT_STRING = "scan_job 값은 문자열이어야 합니다"
REPORT_SCAN_JOB_UNKNOWN = "알 수 없거나 만료된 검사 작업입니다 — 폴더를 다시 검사하십시오"
REPORT_SCAN_JOB_NOT_FINISHED = "검사 작업이 아직 끝나지 않았습니다 — 끝난 뒤 다시 요청하십시오"
REPORT_SCAN_JOB_FAILED = "검사 작업이 실패해 보고서를 만들 수 없습니다 — 폴더를 다시 검사하십시오"
REPORT_CASE_FIELD_NOT_STRING = "사건 필드 {field} 값은 문자열이어야 합니다"
REPORT_EXHIBIT_PATHS_INVALID = "exhibit_paths는 검사 작업의 행 경로(문자열)의 배열이어야 합니다"
REPORT_EXHIBIT_PATHS_EMPTY = "exhibit_paths가 비어 있습니다 — 서증으로 넣을 행을 한 개 이상 고르거나 필드를 빼십시오(빼면 모든 행)"
REPORT_EXHIBIT_PATHS_DUPLICATE = "exhibit_paths에 같은 행이 두 번 있습니다: 「{path}」"
REPORT_EXHIBIT_PATHS_UNKNOWN = "exhibit_paths 중 {count}개는 이 검사 작업의 행이 아닙니다(첫 항목: 「{path}」)"
REPORT_EXHIBIT_PATHS_WITHOUT_JOB = "exhibit_paths는 검사 작업(scan_job)의 행만 고를 수 있습니다 — scan_job이 없습니다"
REPORT_SIDE_UNSUPPORTED = "제출 측(side) 「{value}」은(는) 이 서버가 지원하지 않습니다(지원: plaintiff)"
REPORT_UPLOADS_INVALID = "uploads는 업로드 검사 결과 행(객체)의 배열이어야 합니다"
# X2 / P6 (kept from the posted-row design): an upload's file is a temp file, never a file of the read roots — its
# row is listed unsigned under this marker with the reason below (R22-8 name rules).
UNSIGNED_CLIENT_ROW_MARKER = "서명 제외(클라이언트 제공 결과)"
REPORT_ROW_UPLOAD = "업로드 파일 — 서버 읽기 폴더의 파일이 아니므로 재분석·서명하지 않습니다"
# R22-8 (round 22): an upload row's path went into the signed body's excluded_items unchecked — any sentence,
# "../", NUL. The name is still the client's (an upload has no file of the read roots), but it is now limited
# (UPLOAD_ROW_NAME_MAX characters, relative, no "." / ".." / empty component; NUL is R22-6) and marked: a
# name this server process received with that sha256 in /api/analyze-upload is "server_upload"; any other
# is "client" and its reason says so.
REPORT_ROW_UPLOAD_CLIENT_NAME = (
    "업로드 파일(클라이언트 제공 이름 — 이 서버가 받은 업로드 기록에 없음) — 서버 읽기 폴더의 파일이 아니므로 재분석·서명하지 않습니다"
)
UPLOAD_ROW_NAME_MAX = 255
REPORT_UPLOAD_NAME_INVALID = (
    "업로드 항목 {index}번의 이름이 업로드 파일 이름 형식이 아닙니다(상대 경로, {limit}자 이하, 빈·`.`·`..` 구성 요소 불가)"
)
_UPLOAD_NAMES: OrderedDict[tuple[str, str | None], None] = OrderedDict()
_UPLOAD_NAMES_LOCK = threading.Lock()
_UPLOAD_NAMES_MAX = 4096


def _record_upload_rows(items: list[dict[str, object]]) -> None:
    """R22-8: remember (row path, sha256) of every row this process produced for an upload (bounded, oldest dropped)."""
    with _UPLOAD_NAMES_LOCK:
        for item in items:
            path, digest = item.get("path"), item.get("sha256")
            if isinstance(path, str):
                key = (path, digest if isinstance(digest, str) else None)
                _UPLOAD_NAMES[key] = None
                _UPLOAD_NAMES.move_to_end(key)
        while len(_UPLOAD_NAMES) > _UPLOAD_NAMES_MAX:
            _UPLOAD_NAMES.popitem(last=False)


def _upload_name_source(row: dict[str, Any]) -> str:
    """R22-8: "server_upload" when this process produced an upload row of that path and sha256, else "client"."""
    digest = row.get("sha256")
    key = (str(row.get("path")), digest if isinstance(digest, str) else None)
    with _UPLOAD_NAMES_LOCK:
        return "server_upload" if key in _UPLOAD_NAMES else "client"


def _upload_name_valid(path: str) -> bool:
    """R22-8: an upload row name — relative, at most UPLOAD_ROW_NAME_MAX characters, no empty / "." / ".." component."""
    parts = path.replace("\\", "/").split("/")
    return (
        0 < len(path) <= UPLOAD_ROW_NAME_MAX and not os.path.isabs(path) and not path.startswith(("/", "\\"))
        and not any(part in ("", ".", "..") for part in parts) and "\x00" not in path
    )


# R19-13 (round 19) / P0R-6: a file of the job changed after the scan (its bytes, or it is gone, or it is now a link
# or a folder) — the report is refused in every format: 409 with ``changed_rows`` (path and reason only — the
# server's digests, statuses and verdicts are not echoed: review §2.4).
REPORT_ROWS_CHANGED = "검사 뒤 바뀐 파일이 있습니다 — 다시 검사하십시오(보고서를 만들지 않았습니다)"
REPORT_ROWS_CHANGED_STATUS = 409
REPORT_ROW_CHANGED_CONTENT = "검사 뒤 내용이 바뀌었습니다"
REPORT_ROW_CHANGED_GONE = "검사 뒤 파일이 없어졌거나 읽을 수 없습니다"
REPORT_ROW_CHANGED_LINK = "검사 뒤 파일이나 그 경로의 폴더가 심볼릭 링크로 바뀌었습니다"
REPORT_ROW_CHANGED_MEMBER = "검사 뒤 압축 파일에서 이 구성 파일이 바뀌었거나 없어졌습니다"
# R22-1 / P0R-6: the "기록되지 않은 파일" section of a report without a scan job (uploads only).
REPORT_LISTING_NO_SCAN_ROOT = "검사 폴더가 없는 보고서 — 업로드 결과만 있음"


def report_format(body: bytes, query_format: str | None) -> str | dict[str, object]:
    """The effective report format (N9): ``?format=`` first, else the body's ``format``, else html.

    Returns a ``{"error": …}`` dict (HTTP 400) for a value outside
    :data:`REPORT_FORMATS`; an unreadable body is left to ``_report_payload``.
    """
    value: object = query_format or None
    if value is None:
        try:
            data = load_json_body(body) if body else None
        except JsonBodyError:
            data = None  # the body itself is refused by _report_payload (R11-3)
        value = data.get("format") if isinstance(data, dict) else None
    if value is None or value == "":
        return "html"
    if not isinstance(value, str):
        return {"error": REPORT_FORMAT_NOT_STRING}
    fmt = value.strip().lower()
    if fmt not in REPORT_FORMATS:
        return {"error": REPORT_FORMAT_UNSUPPORTED.format(value=value[:40])}
    return fmt


def report_http_response(
    body: bytes, query_format: str | None, *, default_folder: Path | None = None, owner: str = LOCAL_OWNER,
) -> tuple[int, str, bytes, dict[str, str]]:
    """``(status, content type, body, extra headers)`` for POST /api/report.

    Both servers (stdlib ``webapp`` and FastAPI ``api_server``) answer
    through this one function, so the format (N9), the error statuses
    (N11) and the content type of a PDF can no longer differ between them.
    """
    fmt = report_format(body, query_format)
    if isinstance(fmt, dict):
        rendered: bytes | dict[str, object] = fmt
    else:
        try:
            rendered = _report_payload(body, fmt, default_folder=default_folder, owner=owner)
        except ReadRootDenied:
            rendered = read_root_denied_body()
            return 403, "application/json; charset=utf-8", _json_bytes(rendered), {}
    if isinstance(rendered, dict):
        return report_error_status(rendered), "application/json; charset=utf-8", _json_bytes(rendered), {}
    assert isinstance(fmt, str)
    if fmt in ("evidence", "evidence-statement"):
        return 200, "application/pdf", rendered, {"Content-Disposition": 'attachment; filename="deepfake-lens-evidence-statement.pdf"'}
    if fmt == "pdf":
        return 200, "application/pdf", rendered, {"Content-Disposition": 'attachment; filename="deepfake-lens-forensic-report.pdf"'}
    return 200, "text/html; charset=utf-8", rendered, {"Content-Disposition": 'attachment; filename="deepfake-lens-report.html"'}


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json_dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class _JobFiles:
    """P0R-6: re-hash a job's files under its scan folder (change detection) — never following a link (N2).

    A top-level row path is resolved against the job's scan folder only (P1) — the row path unescaped (P7) — with no
    symbolic link on the way (the file itself or a folder between); an archive member is hashed from the container
    re-extracted with the scanner's own ``extract_archive`` (once per container), which yields the bytes and digest
    the scan recorded; two members of one name give no digest (R26-8: fail closed).
    """

    def __init__(self, scan_root: Path) -> None:
        self.scan_root = scan_root
        self._members: dict[str, dict[str, str]] = {}
        self._temp_dirs: list[Path] = []

    def resolve(self, path_text: str) -> Path | None:
        from .evidence_statement import _no_symlink_on_path, _SymlinkRefused
        from .result_text import unescape_row_path

        candidate = Path(os.path.normpath(self.scan_root / unescape_row_path(path_text)))
        if not _is_within(candidate, self.scan_root) or candidate == self.scan_root:
            return None
        try:
            _no_symlink_on_path(candidate, self.scan_root)
            mode = os.lstat(candidate).st_mode
        except (_SymlinkRefused, OSError):
            return None
        return candidate if stat.S_ISREG(mode) else None

    def change(self, path_text: str, expected: str) -> str | None:
        """The Korean reason the top-level file ``path_text`` no longer has digest ``expected``, or None."""
        from .evidence_statement import _compute_sha256, _no_symlink_on_path, _SymlinkRefused
        from .result_text import unescape_row_path

        candidate = Path(os.path.normpath(self.scan_root / unescape_row_path(path_text)))
        try:
            _no_symlink_on_path(candidate, self.scan_root)
            mode = os.lstat(candidate).st_mode
        except _SymlinkRefused:
            return REPORT_ROW_CHANGED_LINK
        except OSError:
            return REPORT_ROW_CHANGED_GONE
        if stat.S_ISLNK(mode):
            return REPORT_ROW_CHANGED_LINK
        if not stat.S_ISREG(mode):
            return REPORT_ROW_CHANGED_GONE
        try:
            digest = _compute_sha256(candidate, self.scan_root)
        except _SymlinkRefused:
            return REPORT_ROW_CHANGED_LINK
        except OSError:
            return REPORT_ROW_CHANGED_GONE
        return None if digest == expected else REPORT_ROW_CHANGED_CONTENT

    def member_digests(self, container_text: str) -> dict[str, str]:
        if container_text in self._members:
            return self._members[container_text]
        digests: dict[str, str] = {}
        self._members[container_text] = digests
        container = self.resolve(container_text)
        if container is None:
            return digests
        from .archives import extract_archive, is_archive
        from .evidence_statement import _compute_sha256, _SymlinkRefused

        if not is_archive(container):
            return digests
        dest = Path(tempfile.mkdtemp(prefix="dflens-report-", dir=scratch_dir())).resolve()
        self._temp_dirs.append(dest)
        try:
            extraction = extract_archive(container, dest)
        except (OSError, ValueError, RuntimeError):
            logger.info("report: archive %s could not be re-extracted", container, exc_info=True)
            return digests
        names: set[str] = set()
        for member in extraction.members:
            name = extraction.member_name(member, dest)
            try:
                digest = _compute_sha256(member, dest)
            except _SymlinkRefused:
                digest = None
            if name in names:
                logger.warning("report: archive %s yields two members named %r", container, name)  # R26-8
                digests.clear()
                return digests
            names.add(name)
            if digest:
                digests[name] = digest
        return digests

    def close(self) -> None:
        for path in self._temp_dirs:
            shutil.rmtree(path, ignore_errors=True)
        self._temp_dirs.clear()


def _changed_job_rows(items: list[Any], files: _JobFiles) -> list[dict[str, object]]:
    """P0R-6 (R19-13's rule on the job): every row the scan hashed, re-hashed on disk now; ``[{path, reason}]``.

    A top-level row with a digest is re-hashed. An archive member is covered by its container's digest when the
    job holds one; otherwise (a container the scan could not hash) the member itself is re-hashed from a fresh
    extraction. Rows the scan did not hash (links, special files, unreadable files, folders) are signed as the scan
    recorded them — the scan's own row says why it has no digest.
    """
    from .result_text import row_identity

    container_digest = {row_identity(item)[0]: item.sha256 for item in items if row_identity(item)[1] is None and item.sha256}
    changed: list[dict[str, object]] = []
    for item in items:
        if not item.sha256:
            continue
        top, member = row_identity(item)
        if member is None:
            reason = files.change(top, item.sha256)
        elif top in container_digest:
            continue  # the container's own digest is compared (its row is in the job)
        else:
            reason = None if files.member_digests(top).get(member) == item.sha256 else REPORT_ROW_CHANGED_MEMBER
        if reason is not None:
            changed.append({"path": item.path, "reason": reason})
    return changed


def _job_summary(items: list[Any], job_summary: dict[str, Any], cancelled: bool) -> Any:
    """P0R-6: the report summary — counted from the job's rows, enumeration and cancel accounting from the job's summary."""
    def count(name: str) -> int:
        value = job_summary.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    detail = job_summary.get("subfolders_skipped_detail")
    return replace(
        summarize(items, capped=job_summary.get("capped") is True),
        files_over_cap=count("files_over_cap"), subfolders_skipped=count("subfolders_skipped"),
        subfolder_files_skipped=count("subfolder_files_skipped"),
        subfolders_skipped_detail=[dict(entry) for entry in detail if isinstance(entry, dict)] if isinstance(detail, list) else [],
        cancelled=cancelled, files_cancelled=count("files_cancelled") if cancelled else 0,
        unrecorded_listing={"source": "scan_job", "not_in_report": 0},
    )


def _uploads_from_request(raw: object, roots: list[Path]) -> list[tuple[Any, str]] | dict[str, object]:
    """P6/R22-8: the request's ``uploads`` rows as unsigned rows ``[(ScanItem, reason)]``, or a 400 body."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        return {"error": REPORT_UPLOADS_INVALID}
    uploads: list[tuple[Any, str]] = []
    for index, row in enumerate(raw):
        nul_field = _row_nul_field(row)
        if nul_field is not None:
            # R22-6 (round 22): a NUL in a row's path-like value was a 500.
            return {"error": REPORT_ITEM_MALFORMED.format(index=index + 1, reason=PATH_TEXT_NUL.format(field=nul_field))}
        if not _posted_heatmaps_allowed(row, roots):
            raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)  # QA-SYS-7/P8: a heatmap path outside the roots is a probe
        upload = _upload_row_item(row)
        if upload is None:
            return {"error": REPORT_ITEM_MALFORMED.format(index=index + 1, reason="path 값이 없습니다")}
        if not _upload_name_valid(str(upload.path)):
            return {"error": REPORT_UPLOAD_NAME_INVALID.format(index=index + 1, limit=UPLOAD_ROW_NAME_MAX)}  # R22-8
        source = _upload_name_source({"path": upload.path, "sha256": row.get("sha256")})
        uploads.append((upload, REPORT_ROW_UPLOAD if source == "server_upload" else REPORT_ROW_UPLOAD_CLIENT_NAME))
    return uploads


def _row_nul_field(row: object) -> str | None:
    """R22-6: the name of a posted row's path-like field holding a NUL character, or None."""
    if not isinstance(row, dict):
        return None
    result = row.get("result")
    pixel = result.get("pixel_analysis") if isinstance(result, dict) else None
    fields = {name: row.get(name) for name in ("path", "name", "container", "member", "duplicate_of", "heatmap_path")}
    fields["result.pixel_analysis.heatmap_path"] = pixel.get("heatmap_path") if isinstance(pixel, dict) else None
    for name, value in fields.items():
        if isinstance(value, str) and "\x00" in value:
            return name
    return None


def _posted_heatmaps_allowed(row: dict[str, Any], roots: list[Path]) -> bool:
    """Every heatmap path an upload row names (top level or result.pixel_analysis) is under the roots."""
    result = row.get("result")
    pixel = result.get("pixel_analysis") if isinstance(result, dict) else None
    candidates = [row.get("heatmap_path"), pixel.get("heatmap_path") if isinstance(pixel, dict) else None]
    for value in candidates:
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            return False
        try:
            resolved = expand_user(value).resolve()
        except (OSError, RuntimeError):
            return False
        if not (any(_is_within(resolved, root) for root in roots) or is_default_heatmap_output(resolved)):
            return False
    return True


def _upload_row_item(row: dict[str, Any]) -> Any:
    """A posted upload row as a ScanItem for the unsigned-rows section (P6), or None without a path."""
    from .report_items import ItemContractError, check_report_item
    from .result_types import ScanItem

    path = row.get("path") or row.get("name")
    if not isinstance(path, str) or not path:
        return None
    try:
        check_report_item(row)
        return replace(_scan_item_from_json(row), path=path)
    except (ItemContractError, TypeError, ValueError, KeyError, AttributeError):
        # A failed upload row ({name, path, status, error}) is still listed (path only, never signed).
        logger.info("upload row %s is listed without its result", path, exc_info=True)
        raw_status = row.get("status")
        status = raw_status if isinstance(raw_status, str) else "failed"
        return ScanItem(path, Path(path).name or path, "unknown", status, 0)


def _scan_root_label(scan_root: Path | None, roots: list[Path]) -> str | None:
    """R9-9: the scanned folder relative to the (innermost) read root holding it ("." for the root itself)."""
    if scan_root is None:
        return None
    holders = [root for root in roots if _is_within(scan_root, root)]
    if not holders:
        return None
    root = max(holders, key=lambda path: len(path.parts))
    relative = scan_root.relative_to(root).as_posix()
    return relative or "."


def _excluded_rows_json(excluded: list[tuple[Any, str]]) -> list[dict[str, object]]:
    """The signed body's record of rows it does not vouch for (X2): path, marker, reason — no client verdict.

    R22-8 (round 22): an upload row also carries ``name_source`` — "server_upload" (this server produced an
    upload row of that name and sha256) or "client" (the name is only the client's text).
    """
    rows: list[dict[str, object]] = []
    for item, reason in excluded:
        row: dict[str, object] = {"path": item.path, "marker": UNSIGNED_CLIENT_ROW_MARKER, "reason": reason}
        if reason in (REPORT_ROW_UPLOAD, REPORT_ROW_UPLOAD_CLIENT_NAME):
            row["name_source"] = "server_upload" if reason == REPORT_ROW_UPLOAD else "client"
        rows.append(row)
    return rows


def _preview_as_pdf_draws(preview: dict[str, Any]) -> dict[str, Any]:
    """Merge of phase0 R23-4 (GUI preview equals the PDF): every text the evidence PDF would draw is given
    as the PDF draws it — a character its font has no glyph for (Hebrew, Arabic, emoji …) as its
    ``\\uNNNN`` escape (:meth:`pdf_layout.PdfLayout.drawable`, the renderer's own rule).
    ``pdf_glyph_escapes`` says whether any text was escaped, so the GUI can say why. Without pymupdf no
    PDF can be made (501) and the texts are left as they are (``pdf_glyph_escapes`` false).
    """
    from .pdf_backend import import_pymupdf, pymupdf_available

    preview["pdf_glyph_escapes"] = False
    if not pymupdf_available():
        return preview
    from .pdf_layout import PdfLayout

    layout = PdfLayout(import_pymupdf())
    escaped = False

    def draw(value: Any) -> Any:
        nonlocal escaped
        if isinstance(value, str):
            out = layout.drawable(value)
            escaped = escaped or out != value
            return out
        if isinstance(value, list):
            return [draw(entry) for entry in value]
        if isinstance(value, tuple):
            return tuple(draw(entry) for entry in value)
        if isinstance(value, dict):
            # an entry's file_path is the row's identity (the GUI matches changed rows by it), not drawn text
            return {key: entry if key == "file_path" else draw(entry) for key, entry in value.items()}
        return value

    try:
        listing = preview.pop("unrecorded_listing", None)  # GUI data, never drawn
        drawn = {key: draw(value) for key, value in preview.items()}
    finally:
        layout.doc.close()
    if listing is not None:
        drawn["unrecorded_listing"] = listing
    drawn["pdf_glyph_escapes"] = escaped
    return drawn


def _report_payload(
    body: bytes, format_override: str | None = None, *, default_folder: Path | None = None, owner: str = LOCAL_OWNER,
) -> bytes | dict[str, object]:
    """Render the HTML / forensic PDF / evidence statement / signed JSON of a server-held scan job (P0R-6).

    The request names ``scan_job`` (a folder scan this server ran for this owner — ``/api/scan``, ``async=1`` or
    ``/api/scan/stream``), the case fields, the format, ``side`` and optionally ``exhibit_paths`` (an ordered
    selection of the job's row paths: the evidence statement numbers those rows as exhibits in that order; every
    other row of the job is still in the statement, unnumbered, and in every other format). Nothing the client says
    about a file is read: the signed rows are the job's rows, the options / threshold and model provenance and the
    "기록되지 않은" / cancel accounting are the job's; every file the scan hashed is re-hashed on disk and a change
    refuses the report (409, path and reason only). ``uploads`` (rows of ``/api/analyze-upload``) are listed
    unsigned under :data:`UNSIGNED_CLIENT_ROW_MARKER` (P6, R22-8). Raises ReadRootDenied (403) when the job's
    folder is no longer inside the read roots or an upload row names a heatmap outside them.
    """
    try:
        data = load_json_body(body)
    except JsonBodyError as exc:
        return {"error": str(exc)}  # R11-3: a too-deep body was a 500
    if not isinstance(data, dict):
        return {"error": REPORT_BODY_NOT_OBJECT}
    unknown = sorted(str(key) for key in data if key not in REPORT_FIELDS)
    if unknown:
        return {"error": REPORT_UNKNOWN_FIELD.format(fields=", ".join(name[:40] for name in unknown[:8]))}
    for field in REPORT_CASE_FIELDS:
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            return {"error": REPORT_CASE_FIELD_NOT_STRING.format(field=field)}
    side = data.get("side")
    if side is not None and side != "plaintiff":
        return {"error": REPORT_SIDE_UNSUPPORTED.format(value=str(side)[:40])}
    if format_override is None:
        fmt_or_error = report_format(body, None)
        if isinstance(fmt_or_error, dict):
            return fmt_or_error
        format_override = fmt_or_error
    req_format = format_override.lower()
    roots = _effective_roots(default_folder)

    uploads = _uploads_from_request(data.get("uploads"), roots)
    if isinstance(uploads, dict):
        return uploads
    job_id = data.get("scan_job")
    if job_id is None or job_id == "":
        if not uploads:
            return {"error": REPORT_SCAN_JOB_REQUIRED}
        if data.get("exhibit_paths") is not None:
            return {"error": REPORT_EXHIBIT_PATHS_WITHOUT_JOB}
        job_id = None
    elif not isinstance(job_id, str):
        return {"error": REPORT_SCAN_JOB_NOT_STRING}

    raw_exhibits = data.get("exhibit_paths")
    exhibit_paths: list[str] | None = None
    if raw_exhibits is not None:
        if not isinstance(raw_exhibits, list) or not all(isinstance(path, str) for path in raw_exhibits):
            return {"error": REPORT_EXHIBIT_PATHS_INVALID}
        if not raw_exhibits:
            return {"error": REPORT_EXHIBIT_PATHS_EMPTY}
        seen_paths: set[str] = set()
        for path in raw_exhibits:
            if path in seen_paths:
                return {"error": REPORT_EXHIBIT_PATHS_DUPLICATE.format(path=path[:200])}
            seen_paths.add(path)
        exhibit_paths = list(raw_exhibits)

    if req_format in PDF_REPORT_FORMATS:
        from .pdf_backend import pymupdf_available

        if not pymupdf_available():
            # B8 / R22-7: the Korean 501 before any work (nothing to render with).
            return dict(PDF_REPORT_UNAVAILABLE_BODY)

    from .reports import signed_report_body

    items: list[Any] = []
    scan_root: Path | None = None
    context: dict[str, Any] = {}
    job_summary: dict[str, Any] = {}
    cancelled = False
    files: _JobFiles | None = None
    if job_id is not None:
        try:
            job = STORE.get(job_id, owner, evidence=roots)
        except JobIntegrityError as exc:
            return ApiError(str(exc), JOB_INTEGRITY_STATUS)
        if job is None:
            return ApiError(REPORT_SCAN_JOB_UNKNOWN, 404)
        if job.get("status") == "running":
            return ApiError(REPORT_SCAN_JOB_NOT_FINISHED, 409)
        result = job.get("result")
        if job.get("status") not in ("done", "cancelled") or not isinstance(result, dict) or not isinstance(result.get("items"), list):
            return ApiError(REPORT_SCAN_JOB_FAILED, 409)
        raw_context = result.get(CONTEXT_KEY)
        context = raw_context if isinstance(raw_context, dict) else {}
        root_text = context.get("scan_root") or result.get("scan_root")
        if not isinstance(root_text, str) or not root_text:
            return ApiError(REPORT_SCAN_JOB_FAILED, 409)
        scan_root = Path(root_text)
        if not any(_is_within(scan_root, root) for root in roots):
            raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)  # G31: a root removed since the scan
        try:
            items = [_scan_item_from_json(row) for row in result["items"] if isinstance(row, dict)]
        except (TypeError, ValueError, KeyError, AttributeError):
            logger.warning("report: the rows of scan job %s could not be read", job_id, exc_info=True)
            return ApiError(REPORT_SCAN_JOB_FAILED, 409)
        summary_json = result.get("summary")
        job_summary = summary_json if isinstance(summary_json, dict) else {}
        cancelled = job.get("status") == "cancelled" or job_summary.get("cancelled") is True
        if exhibit_paths is not None:
            known = {item.path for item in items}
            unknown_paths = [path for path in exhibit_paths if path not in known]
            if unknown_paths:
                return {"error": REPORT_EXHIBIT_PATHS_UNKNOWN.format(count=len(unknown_paths), path=unknown_paths[0][:200])}
        files = _JobFiles(scan_root)
        try:
            # GUI preview: no re-hash (it runs on every selection change); the export refuses a changed file.
            changed = _changed_job_rows(items, files) if req_format != "evidence-preview" else []
        except Exception:
            files.close()
            raise
        if changed:
            files.close()
            # R19-13 (round 19): never sign rows whose files changed after the scan the examiner saw.
            return ApiError(REPORT_ROWS_CHANGED, REPORT_ROWS_CHANGED_STATUS, changed_rows=changed[:200])

    try:
        excluded_rows = _excluded_rows_json(uploads)
        thresholds = context.get("thresholds")
        coverage = context.get("coverage") if isinstance(context.get("coverage"), dict) else None
        analysis_options = context.get("analysis_options") if isinstance(context.get("analysis_options"), dict) else None
        if job_id is not None:
            summary = _job_summary(items, job_summary, cancelled)
        else:
            summary = replace(
                summarize([], capped=False),
                unrecorded_listing={"source": "unknown", "not_in_report": 0, "reason": REPORT_LISTING_NO_SCAN_ROOT},
            )
        root_label = _scan_root_label(scan_root, roots)
        case = {field: data.get(field) for field in REPORT_CASE_FIELDS}

        def _statement() -> Any:
            """The evidence statement of this job — one build for the PDF and the GUI preview."""
            from .evidence_statement import build_evidence_statement
            from .office_config import DEFAULT_CASE_NAME

            return build_evidence_statement(
                items,
                case_no=str(case["case_no"] or "(사건번호 입력)"),
                case_name=str(case["case_name"] or DEFAULT_CASE_NAME),  # R18-16
                plaintiff=str(case["plaintiff"] or "(의뢰사 상호명 입력) 귀하"),
                defendant=str(case["defendant"] or "(피고/피의자 성명 입력)"),
                court=str(case["court"] or "○○지방법원 귀중"),
                # N17: the request's value, else the operator config, else blank.
                law_firm=str(case["law_firm"] or "") or None,
                contact=str(case["contact"] or "") or None,
                center=str(case["center"] or "디지털포렌식 감정센터"),
                thresholds=thresholds,
                coverage=coverage,
                excluded_items=excluded_rows,
                summary=summary,  # R18-3: the "사용자 취소" count of a cancelled scan
                exhibit_paths=exhibit_paths,
            )

        if req_format == "evidence-preview":
            from .evidence_statement import statement_preview
            from .reports import analysis_options_line, scan_root_line

            preview = statement_preview(_statement(), uploads)
            preview["scan_root_line"] = (scan_root_line({"scan_root": root_label}) or "") if root_label else ""
            preview["analysis_options_line"] = analysis_options_line({"analysis_options": analysis_options}) or ""  # R19-3
            # R22-1: the listing behind the 기록되지 않은 파일 box, for the GUI checklist (not drawn).
            preview["unrecorded_listing"] = dict(getattr(summary, "unrecorded_listing", None) or {})
            return _preview_as_pdf_draws(preview)
        signed = signed_report_body(
            summary, items, thresholds=thresholds, coverage=coverage,
            report_format=req_format,
            excluded_items=excluded_rows, scan_root=root_label,
            analysis_options=analysis_options, files_cancelled_source="server" if cancelled else None,
            exhibit_paths=exhibit_paths,
        )
        if req_format == "json":
            return signed
        resolve = files.resolve if files is not None else (lambda _path: None)

        def _heatmap_allowed(path_text: str) -> bool:
            try:
                resolved = expand_user(path_text).resolve()
            except OSError:
                return False
            return any(_is_within(resolved, r) for r in roots) or is_default_heatmap_output(resolved)

        suffix = ".pdf" if req_format in PDF_REPORT_FORMATS else ".html"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=scratch_dir()) as tmp:
            tmp_path = Path(tmp.name)
        try:
            if req_format in ("evidence", "evidence-statement"):
                from .evidence_statement import (
                    PdfDependencyMissing as StatementPdfMissing,
                    signed_statement_body,
                    write_evidence_statement_pdf,
                )
                try:
                    stmt = _statement()  # the same build as the GUI preview
                    write_evidence_statement_pdf(
                        tmp_path, stmt, signed=signed_statement_body(
                            stmt, scan_root=root_label, analysis_options=analysis_options,
                        ),
                        unsigned_rows=uploads,
                    )
                except StatementPdfMissing:
                    return dict(PDF_REPORT_UNAVAILABLE_BODY)  # R22-7: 501 as documented (E31), never a 500
                except RuntimeError as exc:
                    return {"error": f"{EVIDENCE_PDF_FAILED_PREFIX}{exc}", "hint": EVIDENCE_PDF_FAILED_HINT}
            elif req_format == "pdf":
                from .evidence_statement import PdfDependencyMissing
                from .reports import write_forensic_pdf_report
                try:
                    write_forensic_pdf_report(
                        tmp_path, summary, items,
                        exhibit_no=str(case["exhibit_no"] or "갑 제        호증"),
                        thresholds=thresholds,
                        coverage=coverage,
                        resolve_path=resolve,
                        allow_path=lambda path_text: resolve(path_text) is not None,
                        signed_report=signed,
                        law_firm=str(case["law_firm"] or "") or None,  # N17
                        contact=str(case["contact"] or "") or None,
                        unsigned_rows=uploads,
                    )
                except PdfDependencyMissing:
                    return dict(PDF_REPORT_UNAVAILABLE_BODY)  # B8: a Korean error (HTTP 501)
            else:
                write_html_report(tmp_path, summary, items, thresholds=thresholds, allow_path=_heatmap_allowed, signed_report=signed, unsigned_rows=uploads)
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)
    finally:
        if files is not None:
            files.close()
