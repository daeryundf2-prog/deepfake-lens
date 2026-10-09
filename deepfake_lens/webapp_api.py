"""Request-payload handlers for the built-in web server.

``webapp.run_server`` owns the HTTP handler and auth; this module owns the
/api/* payload construction, upload parsing, scan-job registry, and the
read-root guard so each file stays a single readable layer.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

from .checks import failure_reason
from .error_text import exception_text
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
    InvalidOption,
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


# G7: request limits live in analysis_api (AnalysisOptions.from_query);
# re-exported under their historical names.
MAX_SCAN_FILES = WEB_MAX_SCAN_FILES
MAX_FILE_BYTES_CEILING = WEB_MAX_FILE_BYTES_CEILING
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_UPLOAD_FILES = 20

# Server-level models directory override — set by run_server(--models-dir)
# or DEEPFAKE_LENS_MODELS_DIR so every scan/check/coverage call resolves the
# same weight set an administrator provisioned.
_MODELS_DIR: Path | None = None


def set_models_dir(path: Path | None) -> None:
    global _MODELS_DIR
    _MODELS_DIR = Path(path).expanduser().resolve() if path else None


def _models_dir() -> Path | None:
    return _MODELS_DIR or default_models_dir()


def default_engine_profiles(root: Path | None = None) -> list[Path]:
    """Engine profiles of the server's models dir (G7: same set as the CLI)."""
    return _engine_profiles_in(Path(root) if root is not None else _models_dir())


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
        return str(Path(folder).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(Path(os.path.abspath(Path(folder).expanduser())))


# P8 (round 8): a multipart body cut off before its closing boundary was
# parsed as far as it went — a truncated upload was analyzed (200) as if it
# were the whole file.
MULTIPART_INCOMPLETE = "업로드 본문이 잘렸습니다(닫는 경계 없음) — 파일을 다시 보내십시오"


def _multipart_incomplete(content_type: str, body: bytes) -> bool:
    """True when a multipart/form-data ``body`` lacks its closing ``--boundary--`` (P8)."""
    import re as _re

    match = _re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type, _re.IGNORECASE)
    if match is None:
        return True
    boundary = (match.group(1) or match.group(2)).encode("latin-1", errors="replace")
    return b"--" + boundary + b"--" not in body


# P6 (round 8): an uploaded file is a temp copy, not a file under a read
# root — its rows are marked so POST /api/report never re-analyzes a
# same-named file of the read root in its place (and never signs it).
UPLOAD_SOURCE = "upload"


def _mark_uploads(records: list[dict[str, object]]) -> list[dict[str, object]]:
    for record in records:
        record["source"] = UPLOAD_SOURCE
    return records


def _scan_payload(query: str, *, default_folder: Path | None, should_stop: Callable[[], bool] | None = None) -> dict[str, object]:
    """Handle scan request.

    Raises ReadRootDenied (-> HTTP 403) when the folder is outside the
    operator-registered roots; the caller's folder is never registered (G31).
    """
    folder = _requested_folder(query, default_folder)
    _require_read_root(folder, default_folder)
    # G7: same options object, thresholds and engine set as the CLI. A bad
    # option (non-integer limit, model_path outside the models dir) raises
    # InvalidOption (a ValueError) -> HTTP 400 before any file is read.
    options = _web_options(parse_qs(query))
    try:
        run = scan_folder_run(folder, options, should_stop=should_stop)
        payload = scan_payload(run.summary, run.items, run.thresholds, options)
        # P1: the folder the row paths are relative to — the GUI sends it
        # back with POST /api/report, which resolves rows against it only.
        payload["scan_root"] = scan_root_text(folder)
        return payload
    except (OSError, ValueError) as exc:
        # X4: missing folder, a file, unreadable — the Korean reason, 400.
        return ApiError(str(exc), 400)


# In-memory scan-job registry for /api/scan?async=1. ThreadingHTTPServer
# already runs each request on its own thread, so the job model exists for
# progress polling and to keep long scans from holding a client connection —
# not for concurrency. Entries expire; nothing is persisted.
_SCAN_JOBS: dict[str, dict[str, Any]] = {}
_SCAN_JOBS_LOCK = threading.Lock()
_SCAN_JOB_TTL_SECONDS = 15 * 60
_SCAN_JOB_MAX = 32


def _scan_job_evict(now: float) -> None:
    """Drop finished jobs past the TTL (called with the lock held)."""
    for key in [key for key, entry in _SCAN_JOBS.items() if now - entry.get("finished", entry["created"]) > _SCAN_JOB_TTL_SECONDS]:
        _SCAN_JOBS.pop(key, None)


def _scan_job_start(query: str, *, default_folder: Path | None) -> dict[str, object]:
    """Start a background scan job; poll /api/scan-status?job=<id>."""
    # Refuse out-of-root folders (403) and invalid options (400) up front,
    # not inside the job.
    _require_read_root(_requested_folder(query, default_folder), default_folder)
    _web_options(parse_qs(query))
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        if len(_SCAN_JOBS) >= _SCAN_JOB_MAX:
            raise ValueError("실행 중인 검사 작업이 너무 많습니다 — 진행 중인 작업이 끝난 뒤 다시 시도하십시오")
        job_id = secrets.token_hex(8)
        cancel = threading.Event()
        _SCAN_JOBS[job_id] = {"status": "running", "created": time.time(), "cancel": cancel}

    def work() -> None:
        try:
            result = _scan_payload(query, default_folder=default_folder, should_stop=cancel.is_set)
            status = "done"
        except Exception as exc:  # noqa: BLE001 - a worker crash is recorded, not raised into the thread
            # D16: log the traceback and record the failure as coverage +
            # limitation — never just a bare message (fail-closed: the scan
            # did not complete, so nothing in it may read as a conclusion).
            logger.exception("scan job %s failed", job_id)
            reason = failure_reason(exc)
            result = {
                "error": "폴더 검사 작업이 실패했습니다",
                "detail": reason,
                "coverage": [{"check": "scan_job", "status": "failed", "reason": reason}],
                "limitations": [f"검사 작업 실패 — {reason}. 이 작업의 결과는 결론으로 사용할 수 없습니다."],
            }
            status = "error"
        with _SCAN_JOBS_LOCK:
            entry = _SCAN_JOBS.get(job_id)
            if entry is not None:
                entry.update(status=status, result=result, finished=time.time())

    threading.Thread(target=work, daemon=True, name=f"scan-job-{job_id}").start()
    return {"job_id": job_id, "status": "running"}


def _scan_status_payload(query: str) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return ApiError(JOB_PARAM_REQUIRED, 400)  # X4
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return ApiError(JOB_UNKNOWN, 404)  # X4
        payload: dict[str, object] = {"job_id": job_id, "status": entry["status"]}
        if entry["status"] != "running":
            payload["result"] = entry.get("result")
        return payload


def _scan_cancel_payload(query: str) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return ApiError(JOB_PARAM_REQUIRED, 400)  # X4
    with _SCAN_JOBS_LOCK:
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return ApiError(JOB_UNKNOWN, 404)  # X4
        if entry["status"] != "running":
            return {"job_id": job_id, "status": entry["status"], "cancelled": False}
        cancel = entry.get("cancel")
        if isinstance(cancel, threading.Event):
            cancel.set()
        return {"job_id": job_id, "status": "cancelling", "cancelled": True}


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
    file_path = params.get("file", [""])[0]

    if not file_path:
        return ApiError(FILE_PARAM_REQUIRED, 400)  # X4
    from .analysis_api import SingleFileError, check_single_file, is_symlink_path
    from .cli_standalone import symlink_layer

    requested = Path(file_path).expanduser()
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


def _stats_payload() -> dict[str, object]:
    """Handle stats request with real package facts."""
    import importlib.metadata

    try:
        version = importlib.metadata.version("deepfake-lens")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    package_dir = Path(__file__).parent
    module_count = sum(1 for entry in package_dir.glob("*.py") if not entry.name.startswith("_"))
    return {
        "status": "ok",
        "version": version,
        "modules": module_count,
    }


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


def read_root_denied_body() -> dict[str, object]:
    return {"error": READ_ROOT_DENIED_MESSAGE}


def _register_read_root(folder: Path) -> None:
    """Register an operator read root. Call only from server setup."""
    resolved = folder.expanduser().resolve()
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
    return [Path(default_folder or ".").expanduser().resolve()]


def _require_read_root(path: Path, default_folder: Path | None = None) -> Path:
    """Resolve ``path`` and raise ReadRootDenied unless it is inside a root."""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE) from exc
    if not any(_is_within(resolved, root) for root in _effective_roots(default_folder)):
        raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)
    return resolved


def _requested_folder(query: str, default_folder: Path | None) -> Path:
    params = parse_qs(query)
    return Path(params.get("folder", [str(default_folder or ".")])[0]).expanduser()


def _read_root_allows(path: Path, root_value: str = "", default_folder: Path | None = None) -> bool:
    """True when `path` sits under an operator-registered root.

    The caller-supplied root is also checked when present so a stale or
    mismatched root argument cannot broaden access beyond the registered
    set — both conditions must hold.
    """
    roots = _effective_roots(default_folder)
    if root_value:
        try:
            root = Path(root_value).expanduser().resolve()
        except OSError:
            return False
        if not _is_within(path, root):
            return False
    return any(_is_within(path, root) for root in roots)


def _heatmap_payload(query: str) -> tuple[int, bytes, str]:
    params = parse_qs(query)
    path_value = params.get("path", [""])[0]
    root_value = params.get("root", [""])[0]
    if not path_value:
        return 400, "경로가 없습니다".encode("utf-8"), "missing"
    path = Path(path_value).expanduser().resolve()
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
    params = parse_qs(query)
    path_value = params.get("path", [""])[0]
    root_value = params.get("root", [""])[0]
    if not path_value:
        return 400, "경로가 없습니다".encode("utf-8"), "missing", ""
    path = Path(path_value).expanduser().resolve()
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


def _part_bytes(part) -> bytes | None:
    """Normalize a multipart payload to bytes (email API may return str)."""
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8", errors="replace")
    return None


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


def _archive_upload_items(
    filename: str, suffix: str, payload: bytes, *, options: AnalysisOptions, thresholds: Any,
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
    returning. ``suffix`` is kept for the call sites; the name carries it.
    """
    del suffix
    base = Path(filename.replace("\\", "/")).name or "upload.zip"
    folder = Path(tempfile.mkdtemp(prefix="dflens-up-")).resolve()
    try:
        target = folder / base
        target.write_bytes(payload)
        from .result_text import escape_row_path, member_row_path

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
                if record.get("name") == base:
                    record["name"] = filename
                elif record.get("name") == old_path:
                    record["name"] = record["path"]
            records.append(record)
        return records
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _analyze_upload_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Analyze files uploaded via multipart/form-data.

    Each part is written to a temporary file, analyzed with the default
    engine profiles, then deleted. Archives are extracted and each member
    analyzed as its own row. The server never persists uploads.
    """
    if "multipart/form-data" not in content_type:
        return ApiError("multipart/form-data 업로드가 필요합니다", 400)
    if _multipart_incomplete(content_type, body):
        return ApiError(MULTIPART_INCOMPLETE, 400)  # P8
    from .archives import is_archive

    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    options = _web_options()
    thresholds = load_thresholds(options)
    items: list[dict[str, object]] = []
    for part in message.iter_parts():
        filename = part.get_filename()
        payload = _part_bytes(part)
        if not filename or payload is None:
            continue
        if len(items) >= MAX_UPLOAD_FILES:
            items.append({"name": filename, "error": f"파일 수 상한({MAX_UPLOAD_FILES}) 초과"})
            break
        suffix = Path(filename).suffix[:16]
        try:
            if is_archive(filename):
                items.extend(_archive_upload_items(filename, suffix, payload, options=options, thresholds=thresholds))
                continue
            # delete=False: Windows cannot reopen a delete=True temp file.
            tmp_name = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(payload)
                    tmp_name = tmp.name
                item = analyze_path(tmp_name, options, thresholds=thresholds)
            finally:
                if tmp_name:
                    Path(tmp_name).unlink(missing_ok=True)
            record = item.to_json()
            record["path"] = filename
            record["name"] = filename
            items.append(record)
        except Exception as exc:
            # Per-upload failure is a failed row (status "failed"), never a
            # silently missing file.
            logger.exception("upload analysis failed: %s", filename)
            items.append({"name": filename, "path": filename, "status": "failed", "error": failure_reason(exc)})
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
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
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
    record["name"] = "pasted-text"
    record["path"] = "pasted-text"
    record["source"] = UPLOAD_SOURCE  # P6: pasted text is no file of a read root
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
    if "multipart/form-data" not in content_type:
        return ApiError("multipart/form-data 또는 application/json 본문이 필요합니다", 400)
    if _multipart_incomplete(content_type, body):
        return ApiError(MULTIPART_INCOMPLETE, 400)  # P8
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    part = next(
        (p for p in message.iter_parts() if p.get_filename() and p.get_payload(decode=True)),
        None,
    )
    if part is None:
        return ApiError("업로드된 파일이 없습니다", 400)
    filename = part.get_filename() or "upload"
    payload = _part_bytes(part) or b""
    suffix = Path(filename).suffix[:16]
    from .archives import is_archive
    options = _web_options()
    thresholds = load_thresholds(options)
    if is_archive(filename):
        items = _mark_uploads(_archive_upload_items(filename, suffix, payload, options=options, thresholds=thresholds))  # P6
        return {
            "schema_version": SCAN_JSON_SCHEMA_VERSION,
            "mode": "files",
            "summary": _summarize_records(items, "upload"),
            "items": items,
            **_provenance(options, thresholds),
        }
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_name = tmp.name
        tmp_path = Path(tmp_name)
        item = analyze_path(tmp_path, options, thresholds=thresholds)
        record = item.to_json()
        forensic = None
        layer_errors: dict[str, Any] = {}
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = _provenance_layer(analyze_metadata_forensic(tmp_path).to_json())
        except Exception as exc:
            logger.exception("forensic layer failed")
            _layer_error(layer_errors, "forensic", exc)
        advanced = None
        if item.kind == "text":
            try:
                from .text_advanced import analyze_text_advanced
                advanced = _text_statistics_layer(analyze_text_advanced(payload.decode("utf-8", errors="replace")).to_json())
            except Exception as exc:
                logger.exception("text-advanced layer failed")
                _layer_error(layer_errors, "advanced", exc)
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    record["name"] = filename
    record["path"] = filename
    record["source"] = UPLOAD_SOURCE  # P6
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
    if "multipart/form-data" not in content_type:
        return ApiError("multipart/form-data 본문이 필요합니다", 400)
    if _multipart_incomplete(content_type, body):
        return ApiError(MULTIPART_INCOMPLETE, 400)  # P8
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    parts = [
        p for p in message.iter_parts() if p.get_filename() and _part_bytes(p)
    ]
    if len(parts) < 2:
        return ApiError("비교할 파일 2개가 필요합니다", 400)
    tmp_paths: list[Path] = []
    try:
        for part in parts[:2]:
            suffix = Path(part.get_filename() or "upload").suffix[:16]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(_part_bytes(part) or b"")
                tmp_paths.append(Path(tmp.name))
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
    finally:
        for tmp_path in tmp_paths:
            tmp_path.unlink(missing_ok=True)


def _feedback_path() -> Path:
    """Where web-UI examiner labels accumulate; overridable for tests."""
    override = os.environ.get("DEEPFAKE_LENS_FEEDBACK")
    if override:
        return Path(override)
    return Path.home() / ".deepfake-lens" / "feedback.jsonl"


def _feedback_payload(body: bytes) -> dict[str, object]:
    """Record one examiner verdict from the web UI.

    Appends a ``{path, expected_label, notes, embedded_result}`` JSONL row the
    ``feedback`` CLI command can consume directly — ``embedded_result`` carries
    the scan result so labeled rows need no rescan.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ApiError("JSON 본문을 해석할 수 없습니다", 400)
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
    feedback_file = _feedback_path()
    feedback_file.parent.mkdir(parents=True, exist_ok=True)
    with feedback_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True, "feedback_file": str(feedback_file)}


# B8: /api/report?format=pdf without pymupdf answers this body with HTTP 501
# (both the stdlib web server and the FastAPI server) instead of a PDF.
# P8: the install command in backticks, as in evidence_statement (an unquoted
# "pip install" read as English prose).
PDF_REPORT_UNAVAILABLE_ERROR = "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다(설치: `pip install pymupdf`)."
PDF_REPORT_UNAVAILABLE_BODY: dict[str, object] = {"error": PDF_REPORT_UNAVAILABLE_ERROR}
PDF_REPORT_UNAVAILABLE_STATUS = 501


# N11: /api/report answers a malformed request with HTTP 400 and a Korean
# error (was 200 + {"error"}); a renderer failure on the server side is 500.
REPORT_BAD_REQUEST_STATUS = 400
REPORT_RENDER_FAILED_STATUS = 500
EVIDENCE_PDF_FAILED_PREFIX = "증거설명서 PDF 생성 실패: "
# N9: the report format comes from ?format= or, failing that, the JSON body's
# "format" — both servers decide the content type from the same value.
REPORT_FORMATS = ("html", "pdf", "evidence", "evidence-statement", "json")
PDF_REPORT_FORMATS = ("pdf", "evidence", "evidence-statement")
REPORT_FORMAT_UNSUPPORTED = "지원되지 않는 보고서 형식입니다: 「{value}」 (`html`, `pdf`, `evidence`, `json` 중 하나)"
REPORT_FORMAT_NOT_STRING = "`format` 값은 문자열이어야 합니다"


def report_error_status(body: dict[str, object]) -> int:
    """HTTP status for a JSON body returned by ``_report_payload``.

    200 for the signed report body (``format=json``); 501 for the missing
    PDF renderer (B8); 500 when the evidence-statement PDF could not be
    built; 400 for every request error (N11 — was 200 + ``{"error": ...}``).
    """
    error = body.get("error")
    if error is None:
        return 200
    if error == PDF_REPORT_UNAVAILABLE_ERROR:
        return PDF_REPORT_UNAVAILABLE_STATUS
    if isinstance(error, str) and error.startswith(EVIDENCE_PDF_FAILED_PREFIX):
        return REPORT_RENDER_FAILED_STATUS
    return REPORT_BAD_REQUEST_STATUS


# G7 (round 5): /api/report request errors in Korean (were "items array is
# required", "thresholds must be an object", "malformed item: …").
REPORT_BODY_NOT_OBJECT = "보고서 요청 본문은 JSON 객체여야 합니다"
REPORT_ITEMS_REQUIRED = "보고서에 넣을 검사 결과 항목(items 배열)이 필요합니다"
REPORT_FIELD_NOT_OBJECT = "{field} 값은 JSON 객체여야 합니다"
REPORT_ITEM_MALFORMED = "검사 결과 항목 {index}번을 해석할 수 없습니다: {reason}"
REPORT_JSON_INVALID = "JSON 본문을 해석할 수 없습니다"
# X2 (round 7): the report never signs what the client says about a file.
# Every posted row is re-analyzed on the server (analysis_api, the read
# roots, the server's models dir and thresholds); its verdict, evidence,
# coverage and SHA-256 are the server's. A row that cannot be re-analyzed
# is left out of the signed body and rendered under this marker.
UNSIGNED_CLIENT_ROW_MARKER = "서명 제외(클라이언트 제공 결과)"
REPORT_ROW_NOT_FOUND = "읽기 루트 안에서 파일을 찾을 수 없어 서버가 재분석하지 못했습니다"
REPORT_ROW_LINK_ON_PATH = "경로 중간의 폴더가 심볼릭 링크라 따라가지 않았습니다 — 서버가 재분석하지 못했습니다"
REPORT_ROW_NOT_DERIVED = "서버 재분석 결과에 이 항목이 없습니다(압축 파일 구성이 다름)"
REPORT_ROW_FAILED = "서버 재분석 실패: {reason}"
# P1 (round 8): rows are relative to the folder that was scanned, not to the
# read root — a report of <root>/caseA signed <root>/target.png (a same-named
# file of the root). The request names the scan folder (the scan response's
# scan_root) and every row is resolved against it only.
REPORT_SCAN_ROOT_REQUIRED = "스캔 폴더(scan_root)가 필요합니다 — 폴더 검사 응답의 scan_root 값을 그대로 보내십시오(행 경로는 그 폴더 기준입니다)"
REPORT_SCAN_ROOT_NOT_STRING = "scan_root 값은 문자열이어야 합니다"
REPORT_SCAN_ROOT_NOT_ABSOLUTE = "scan_root는 절대 경로여야 합니다: 「{value}」"
REPORT_SCAN_ROOT_MISSING = "스캔 폴더를 찾을 수 없습니다: 「{value}」"
REPORT_ROW_OUTSIDE_SCAN_ROOT = "스캔 폴더 밖의 경로라 서버가 재분석하지 않았습니다"
# P7 (round 8): a member row's container/member fields must spell its path.
REPORT_ROW_IDENTITY_MISMATCH = "path가 container::member와 일치하지 않습니다"
# P6 (round 8): an upload row is never re-analyzed from the read root.
REPORT_ROW_UPLOAD = "업로드 파일 — 서버 읽기 폴더의 파일이 아니므로 재분석·서명하지 않습니다"


def report_format(body: bytes, query_format: str | None) -> str | dict[str, object]:
    """The effective report format (N9): ``?format=`` first, else the body's ``format``, else html.

    Returns a ``{"error": …}`` dict (HTTP 400) for a value outside
    :data:`REPORT_FORMATS`; an unreadable body is left to ``_report_payload``.
    """
    value: object = query_format or None
    if value is None:
        try:
            data = json.loads(body.decode("utf-8")) if body else None
        except (ValueError, UnicodeDecodeError):
            data = None
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
    body: bytes, query_format: str | None, *, default_folder: Path | None = None,
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
            rendered = _report_payload(body, fmt, default_folder=default_folder)
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
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class _ReportHasher:
    """SHA-256 of each posted row's evidence, read only under the read roots (G31, N2, N10).

    ``resolve`` maps a row path to the regular file it names inside a root
    without following any symbolic link (the file itself or a folder
    between the root and it) — so a link row can never be hashed as its
    target. ``sha256`` hashes that file with ``O_NOFOLLOW``; an archive
    member row ("a.zip::x") is hashed from the container re-extracted with
    the scanner's own ``extract_archive`` (one extraction per container per
    report), which yields the same bytes — and digest — the scan recorded.
    """

    def __init__(self, roots: list[Path], scan_root: Path | None = None) -> None:
        self.roots = roots
        # P1: the scanned folder (inside a read root) the row paths are
        # relative to; every row is resolved against it only.
        self.scan_root = scan_root
        self._members: dict[str, dict[str, str]] = {}
        self._temp_dirs: list[Path] = []

    def _candidates(self, path_text: str) -> list[tuple[Path, Path]]:
        """(file, folder no link may sit under) pairs for a top-level row path (P1: the scan root only).

        P7: the row path is unescaped ("a\\:\\:b" names the real file "a::b").
        """
        from .result_text import unescape_row_path

        p = Path(os.path.normpath(Path(unescape_row_path(path_text)).expanduser()))
        if self.scan_root is not None:
            cand = p if p.is_absolute() else Path(os.path.normpath(self.scan_root / p))
            return [(cand, self.scan_root)] if _is_within(cand, self.scan_root) else []
        return [(p, root) for root in self.roots] if p.is_absolute() else [(root / p, root) for root in self.roots]

    def resolve(self, path_text: str) -> Path | None:
        from .evidence_statement import _no_symlink_on_path, _SymlinkRefused

        for cand, root in self._candidates(path_text):
            if not _is_within(cand, root):
                continue
            try:
                _no_symlink_on_path(cand, root)
                mode = os.lstat(cand).st_mode
            except _SymlinkRefused:
                return None  # N2: a link is never followed
            except OSError:
                continue
            if stat.S_ISREG(mode):
                return cand
        return None

    def locate(self, path_text: str) -> Path | str:
        """The file a posted top-level row names, for server re-analysis (X2).

        Returns the path inside a read root of a regular file or of a
        symbolic link itself (never followed — its re-analysis is the
        scan's skipped link row), or the Korean reason it cannot be
        re-analyzed. A folder between the root and the file that is a link
        refuses the row (N2).
        """
        from .evidence_statement import _no_symlink_on_path, _SymlinkRefused

        pairs = self._candidates(path_text)
        if self.scan_root is not None and not pairs:
            return REPORT_ROW_OUTSIDE_SCAN_ROOT
        for cand, root in pairs:
            if not _is_within(cand, root) or cand == root:
                continue
            try:
                _no_symlink_on_path(cand.parent, root)
                mode = os.lstat(cand).st_mode
            except _SymlinkRefused:
                return REPORT_ROW_LINK_ON_PATH
            except OSError:
                continue
            if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                return cand
        return REPORT_ROW_NOT_FOUND

    def sha256(self, item: Any) -> str | None:
        from .evidence_statement import _compute_sha256, _SymlinkRefused
        from .result_text import is_symlink_row, row_identity

        if is_symlink_row(item.status, item.error):
            return None  # N2: the scan never followed the link; nor does the report
        top, member = row_identity(item)  # P7: the fields, not a "::" in a real path
        if member is not None:
            return self._member_digests(top).get(member)
        path = self.resolve(top)
        if path is None:
            return None
        root = next((r for r in self.roots if _is_within(path, r)), None)
        if self.scan_root is not None and _is_within(path, self.scan_root):
            root = self.scan_root
        try:
            return _compute_sha256(path, root)
        except _SymlinkRefused:
            return None

    def _member_digests(self, container_text: str) -> dict[str, str]:
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
        dest = Path(tempfile.mkdtemp(prefix="dflens-report-")).resolve()
        self._temp_dirs.append(dest)
        try:
            extraction = extract_archive(container, dest)
        except (OSError, ValueError, RuntimeError):
            logger.info("report: archive %s could not be re-extracted", container, exc_info=True)
            return digests
        for member in extraction.members:
            try:
                digest = _compute_sha256(member, dest)
            except _SymlinkRefused:
                continue
            if digest:
                digests[extraction.member_name(member, dest)] = digest  # Y9: the scan's member name
        return digests

    def close(self) -> None:
        for path in self._temp_dirs:
            shutil.rmtree(path, ignore_errors=True)
        self._temp_dirs.clear()


def _rederive_report_items(
    posted: list[Any], hasher: "_ReportHasher", options: Any, thresholds: Any,
) -> tuple[list[Any], list[tuple[Any, str]]]:
    """Re-analyze every posted row on the server (X2).

    Rows are grouped by their top-level file ("a.zip::x" -> "a.zip"); each
    file is located inside the read roots (:meth:`_ReportHasher.locate`)
    and analyzed once with ``analysis_api.scan_file_run`` — the scan's own
    body, so an archive yields the same member and container rows and a
    symbolic link the same skipped row. Each posted row is replaced by the
    server row of the same path (path naming kept: the posted folder-relative
    path). Returns ``(server rows in posted order, [(posted row, reason)]
    for rows that could not be re-derived)``.
    """
    from .analysis_api import scan_file_run
    from .result_text import escape_row_path, member_row_path, row_identity

    derived_by_top: dict[str, dict[str | None, Any] | str] = {}
    for item in posted:
        top = row_identity(item)[0]
        if top in derived_by_top:
            continue
        located = hasher.locate(top)
        if isinstance(located, str):
            derived_by_top[top] = located
            continue
        try:
            run = scan_file_run(located, options, thresholds=thresholds)
        except Exception as exc:  # noqa: BLE001 - one unreadable row must not fail the report
            logger.info("report: %s could not be re-analyzed", top, exc_info=True)
            derived_by_top[top] = REPORT_ROW_FAILED.format(reason=failure_reason(exc))
            continue
        name = escape_row_path(located.name)
        # P7: server rows keyed by member path (None = the file's own row);
        # the scan of the single file names its rows from the file name.
        rows: dict[str | None, Any] = {}
        for row in run.items:
            row_top, row_member = row_identity(row)
            if row_top != name:
                continue
            path = top if row_member is None else member_row_path(top, row_member)
            rows[row_member] = replace(row, path=path, container=None if row_member is None else top, member=row_member)
        derived_by_top[top] = rows
    derived: list[Any] = []
    excluded: list[tuple[Any, str]] = []
    seen: set[tuple[str, str | None]] = set()
    for item in posted:
        top, member = row_identity(item)
        found = derived_by_top[top]
        if isinstance(found, str):
            excluded.append((item, found))
        elif member not in found:
            excluded.append((item, REPORT_ROW_NOT_DERIVED))
        elif (top, member) not in seen:
            seen.add((top, member))
            derived.append(found[member])
    return derived, excluded


def _posted_heatmaps_allowed(row: dict[str, Any], roots: list[Path]) -> bool:
    """Every heatmap path a posted row names (top level or result.pixel_analysis) is under the roots."""
    result = row.get("result")
    pixel = result.get("pixel_analysis") if isinstance(result, dict) else None
    candidates = [row.get("heatmap_path"), pixel.get("heatmap_path") if isinstance(pixel, dict) else None]
    for value in candidates:
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            return False
        try:
            resolved = Path(value).expanduser().resolve()
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


def _report_scan_root(value: object, roots: list[Path], *, required: bool) -> Path | None | dict[str, object]:
    """P1: the scanned folder named by the request — absolute, existing, inside a read root.

    Raises ReadRootDenied (403) for a folder outside the roots; returns an
    error dict (400) when it is missing (with rows to re-analyze), not a
    string, relative or not a folder.
    """
    if value is None or value == "":
        return {"error": REPORT_SCAN_ROOT_REQUIRED} if required else None
    if not isinstance(value, str):
        return {"error": REPORT_SCAN_ROOT_NOT_STRING}
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return {"error": REPORT_SCAN_ROOT_NOT_ABSOLUTE.format(value=value[:200])}
    try:
        resolved = candidate.resolve(strict=True)
        is_dir = resolved.is_dir()
    except (OSError, RuntimeError):
        is_dir = False
    if not is_dir:
        return {"error": REPORT_SCAN_ROOT_MISSING.format(value=value[:200])}
    if not any(_is_within(resolved, root) for root in roots):
        raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)
    return resolved


def _excluded_rows_json(excluded: list[tuple[Any, str]]) -> list[dict[str, object]]:
    """The signed body's record of rows it does not vouch for (X2): path, marker, reason — no client verdict."""
    return [{"path": item.path, "marker": UNSIGNED_CLIENT_ROW_MARKER, "reason": reason} for item, reason in excluded]


def _report_payload(body: bytes, format_override: str | None = None, *, default_folder: Path | None = None) -> bytes | dict[str, object]:
    """Render the HTML or court-admissible forensic PDF report for web-scan results.

    ``format=json`` returns the signed report body itself. Raises
    ReadRootDenied (-> 403) when a posted heatmap_path is outside the roots.

    Accepts the items array the GUI holds (scan/upload payload rows) and
    checks every row against the scan-result item contract (N11). X2: the
    posted results are never signed — every row's file is re-analyzed on
    the server inside the read roots (:func:`_rederive_report_items`) with
    the server's options, models dir and threshold profile, and the report
    (verdict, evidence, coverage, SHA-256, ``thresholds`` and ``coverage``
    provenance) is built from those server rows only. A row that cannot be
    re-analyzed (file not under a root, a linked folder on its path, an
    archive member the server's extraction does not produce) is left out of
    the signed body — the body lists its path under ``excluded_items`` — and
    the HTML/PDF renderings show it in a separate "서명 제외(클라이언트
    제공 결과)" section. Case metadata is taken from the request.
    """
    from .report_items import ItemContractError, check_report_item

    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"error": REPORT_JSON_INVALID}
    if not isinstance(data, dict):
        return {"error": REPORT_BODY_NOT_OBJECT}
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return {"error": REPORT_ITEMS_REQUIRED}

    thresholds = data.get("thresholds")
    if thresholds is not None and not isinstance(thresholds, dict):
        return {"error": REPORT_FIELD_NOT_OBJECT.format(field="thresholds")}
    coverage = data.get("coverage")
    if coverage is not None and not isinstance(coverage, dict):
        return {"error": REPORT_FIELD_NOT_OBJECT.format(field="coverage")}
    if format_override is None:
        fmt_or_error = report_format(body, None)
        if isinstance(fmt_or_error, dict):
            return fmt_or_error
        format_override = fmt_or_error

    # QA-SYS-7/P8: a heatmap_path outside the roots is a probe for host files
    # — 403 whatever else is wrong with the row (a top-level heatmap_path
    # included, which the GUI also reads).
    roots_for_probe = _effective_roots(default_folder)
    for row in raw_items:
        if isinstance(row, dict) and not _posted_heatmaps_allowed(row, roots_for_probe):
            raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)

    items = []
    uploads: list[tuple[Any, str]] = []
    for index, row in enumerate(raw_items):
        if isinstance(row, dict) and row.get("source") == UPLOAD_SOURCE:
            # P6: an upload row is listed as unsigned, never re-analyzed from
            # a same-named file of the read root.
            upload = _upload_row_item(row)
            if upload is None:
                return {"error": REPORT_ITEM_MALFORMED.format(index=index + 1, reason="path 값이 없습니다")}
            uploads.append((upload, REPORT_ROW_UPLOAD))
            continue
        try:
            # N11: a row that is not a scan-result item is refused (was
            # skipped, or rendered as a report about e.g. path 3).
            check_report_item(row)
            parsed = _scan_item_from_json(row)
            if parsed.member is not None and (
                parsed.container is None or parsed.path != f"{parsed.container}::{parsed.member}"
            ):
                # P7: the identity fields and the display path must agree.
                return {"error": REPORT_ITEM_MALFORMED.format(index=index + 1, reason=REPORT_ROW_IDENTITY_MISMATCH)}
            items.append(parsed)
        except ItemContractError as exc:
            return {"error": REPORT_ITEM_MALFORMED.format(index=index + 1, reason=str(exc))}
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            logger.info("report item %d could not be read", index, exc_info=True)
            return {"error": REPORT_ITEM_MALFORMED.format(index=index + 1, reason=exception_text(exc))}

    # G31: every disk read this report makes — evidence hashing and heatmap
    # embedding — is confined to the operator read roots (or, with none
    # registered, the server's default folder). Scan rows carry relative
    # paths, so they are resolved against the roots; an unresolved or
    # escaping path stays unread. N2: a symbolic link — the file itself or
    # a folder between the root and it — is never followed.
    roots = _effective_roots(default_folder)

    def _heatmap_allowed(path_text: str) -> bool:
        try:
            resolved = Path(path_text).expanduser().resolve()
        except OSError:
            return False
        return any(_is_within(resolved, r) for r in roots) or is_default_heatmap_output(resolved)

    # A posted heatmap_path outside the roots is a probe for host files:
    # refuse the whole report (403) instead of rendering around it.
    for item in items:
        pixel = item.result.pixel_analysis if item.result is not None else None
        heatmap_path = getattr(pixel, "heatmap_path", None)
        if heatmap_path and not _heatmap_allowed(str(heatmap_path)):
            raise ReadRootDenied(READ_ROOT_DENIED_MESSAGE)

    if format_override.lower() == "pdf":
        from .pdf_backend import pymupdf_available

        if not pymupdf_available():
            # B8: the Korean 501 before any re-analysis work (nothing to render with).
            return dict(PDF_REPORT_UNAVAILABLE_BODY)

    # P1: rows are resolved against the scanned folder only (inside a read
    # root; 403 outside) — required whenever a row is to be re-analyzed.
    scan_root = _report_scan_root(data.get("scan_root"), roots, required=bool(items))
    if isinstance(scan_root, dict):
        return scan_root
    hasher = _ReportHasher(roots, scan_root)
    _resolve_item_path = hasher.resolve

    def _path_allowed(path_text: str) -> bool:
        return _resolve_item_path(path_text) is not None

    # X2: nothing the client says about a file is signed. Each row is
    # re-analyzed on the server (scan_file_run inside the roots — an archive
    # re-extracted exactly as the scan extracted it, N10; a symbolic link
    # never followed, N2) and replaced by the server's row: verdict,
    # evidence, coverage and SHA-256. Client sha256 values were already
    # never trusted (G11). The threshold/weights provenance is the server's
    # too — posted ``thresholds``/``coverage`` are ignored.
    from .reports import signed_report_body
    from .vendor_weights import weights_coverage

    # The request may name the analysis options the scan used (``options``:
    # the /api/scan query keys — pixel, heatmaps, deep_signals,
    # no_default_engine, model_path/fusion_profile as bare names inside the
    # models dir); they are validated like a scan request and only choose
    # what the server runs.
    raw_options = data.get("options")
    if raw_options is not None and not isinstance(raw_options, dict):
        return {"error": REPORT_FIELD_NOT_OBJECT.format(field="options")}
    try:
        options = _web_options(
            {str(key): [str(value).lower() if isinstance(value, bool) else str(value)] for key, value in (raw_options or {}).items()},
        )
    except InvalidOption as exc:
        return {"error": str(exc)}
    server_thresholds = load_thresholds(options)
    try:
        items, excluded = _rederive_report_items(items, hasher, options, server_thresholds)
    finally:
        hasher.close()
    excluded = uploads + excluded  # P6: upload rows first, in posted order
    thresholds = server_thresholds
    coverage = dict(weights_coverage(options.resolved_models_dir()))
    excluded_rows = _excluded_rows_json(excluded)

    # Same counting rule as the CLI (core.summarize) so web and CLI report
    # headers agree on every verdict count.
    summary = summarize(items, capped=False)
    req_format = format_override.lower()
    # G30: web reports are signed when DEEPFAKE_LENS_REPORT_KEY is set and
    # say "서명 없음" otherwise; the pins are those of the server's models dir.
    from .profile_pins import profile_pins

    signed = signed_report_body(
        summary, items, thresholds=thresholds, coverage=coverage,
        report_format=req_format, model_pins=profile_pins(_models_dir()),
        excluded_items=excluded_rows,
    )
    if req_format == "json":
        return signed
    suffix = ".pdf" if req_format in ("pdf", "evidence", "evidence-statement") else ".html"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if req_format in ("evidence", "evidence-statement"):
            from .evidence_statement import build_evidence_statement, signed_statement_body, write_evidence_statement_pdf
            try:
                stmt = build_evidence_statement(
                    items,
                    case_no=str(data.get("case_no") or "(사건번호 입력)"),
                    case_name=str(data.get("case_name") or "성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반"),
                    plaintiff=str(data.get("plaintiff") or "(의뢰사 상호명 입력) 귀하"),
                    defendant=str(data.get("defendant") or "(피고/피의자 성명 입력)"),
                    court=str(data.get("court") or "○○지방법원 귀중"),
                    # N17: the request's value, else the operator config, else blank.
                    law_firm=str(data.get("law_firm") or "") or None,
                    contact=str(data.get("contact") or "") or None,
                    center=str(data.get("center") or "디지털포렌식 감정센터"),
                    thresholds=thresholds,
                    coverage=coverage,
                    excluded_items=excluded_rows,
                )
                # G30: signed like the other web reports (server key + pins).
                write_evidence_statement_pdf(
                    tmp_path, stmt, signed=signed_statement_body(stmt, model_pins=profile_pins(_models_dir())),
                    unsigned_rows=excluded,
                )
            except RuntimeError as exc:
                return {"error": f"{EVIDENCE_PDF_FAILED_PREFIX}{exc}", "hint": "`pip install 'deepfake-lens[forensic]'` 후 재시도하거나 Markdown 출력을 사용하세요."}
        elif req_format == "pdf":
            from .evidence_statement import PdfDependencyMissing
            from .reports import write_forensic_pdf_report
            exhibit_no = str(data.get("exhibit_no") or "갑 제        호증")
            try:
                write_forensic_pdf_report(
                    tmp_path, summary, items,
                    exhibit_no=exhibit_no,
                    thresholds=thresholds,
                    coverage=coverage,
                    resolve_path=_resolve_item_path,
                    allow_path=_path_allowed,
                    signed_report=signed,
                    law_firm=str(data.get("law_firm") or "") or None,  # N17
                    contact=str(data.get("contact") or "") or None,
                    unsigned_rows=excluded,
                )
            except PdfDependencyMissing:
                # B8: no English Latin-1 fallback PDF — a Korean error (HTTP 501).
                return dict(PDF_REPORT_UNAVAILABLE_BODY)
        else:
            write_html_report(tmp_path, summary, items, thresholds=thresholds, allow_path=_heatmap_allowed, signed_report=signed, unsigned_rows=excluded)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
