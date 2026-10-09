"""REST API server module for Deepfake Lens.

Provides HTTP API endpoints for external system integration.

The API reads local files on request, so it must never be exposed without a
token: ``run_server(..., token=...)`` requires an ``X-API-Token`` header on
every /api/ route, and the CLI refuses non-localhost binds without one.

Every analysis goes through :mod:`deepfake_lens.analysis_api` (G7) — the same
options, engine set and threshold profile as the CLI and the built-in web
GUI. Handlers that run synchronous analysis are plain ``def`` (FastAPI runs
them in its thread pool) or hand the work to ``run_in_threadpool`` so a long
scan never blocks the event loop (G34).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from .checks import failure_reason
from .vendor_weights import default_models_dir
from .json_text import json_bytes, json_dumps

logger = logging.getLogger(__name__)


def _layer_error(payload: dict[str, Any], layer: str, exc: BaseException) -> None:
    """Record a failed auxiliary layer in the response instead of hiding it."""
    errors = payload.setdefault("layer_errors", [])
    errors.append({"layer": layer, "status": "failed", "reason": failure_reason(exc)})

try:
    from fastapi import Request
except ImportError:
    Request = Any  # type: ignore[assignment, misc]

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Custom header required on non-GET /api/* requests when no token is set.
# Browsers can only attach custom headers via a CORS preflight, and the CORS
# policy below only allows loopback origins — so drive-by requests from
# unrelated web pages cannot reach the write endpoints on a loopback bind.
CLIENT_HEADER = "X-Deepfake-Lens-Client"
# GET endpoints need the header too (G8, review M1/M2; D16): a cross-origin
# <img src> or link can issue a "simple" GET, which must not be able to
# start or cancel a scan or read server facts. Like the built-in web server,
# every /api/* route requires it; only the liveness probe is exempt.
CLIENT_HEADER_GET_PATHS = frozenset({
    "/api/scan",
    "/api/scan-cancel",
    "/api/scan-status",
    "/api/analyze-file",
    "/api/heatmap",
    "/api/preview",
    "/api/stats",
})
CLIENT_HEADER_EXEMPT_PATHS = frozenset({"/api/health"})

# Streaming-job registry cap (G34): each job holds a worker thread and its
# results until the client disconnects; 32 matches the web server's
# _SCAN_JOB_MAX so both servers bound concurrent work the same way.
MAX_JOBS = 32
# Upper bound on /api/scan/stream's max_files query value (unchanged since
# the endpoint was added; the folder scan itself enforces it).
MAX_STREAM_SCAN_FILES = 5000
JOBS_FULL_MESSAGE = "실행 중인 작업이 너무 많습니다 — 진행 중인 작업이 끝난 뒤 다시 시도하십시오"
# Text bodies above this many characters are refused (the web server's
# /api/check limit, E21/E23).
MAX_TEXT_CHARS = 256 * 1024
TEXT_TOO_LARGE = "텍스트가 256KB를 초과합니다"
TEXT_OR_FILE_REQUIRED = "file_path 또는 text가 필요합니다"
# R9-3 (round 9): /api/scan/stream without a folder; like every other
# refusal of the stream endpoints it is a 400 before the stream starts.
DIRECTORY_REQUIRED = "directory가 필요합니다"
# R9-8 (round 9): /api/analyze/text?text= (empty or blank) answered 200 with
# the result of an empty text.
TEXT_EMPTY = "분석할 텍스트가 비어 있습니다 — text 매개변수에 분석할 글을 넣으십시오"

# Packages the API server needs at runtime; missing ones make `api-serve`
# exit 2 with an install hint instead of a traceback (G29).
SERVER_DEPENDENCIES = ("fastapi", "uvicorn")
SERVER_DEPS_EXIT_CODE = 2


@dataclass(frozen=True)
class APIResponse:
    status: int
    data: dict[str, Any]
    message: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def host_name(header_value: str) -> str:
    """Extract the hostname part of a Host header (handles [::1]:port)."""
    value = header_value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def _default_profiles() -> Path | None:
    """The models directory the API analyzes with (kept for compatibility).

    The engine set itself is resolved by analysis_api from this directory.
    """
    models_dir = default_models_dir()
    return models_dir if models_dir.is_dir() else None


def confine_request_path(path_text: str, default_folder: Path | None = None) -> Path:
    """Resolve a caller-named path and require it inside a read root (G31).

    Every endpoint that reads a ``file_path``/``directory`` the request
    names goes through this, exactly like ``/api/scan``: only roots the
    operator registered at server start (``--folder``/``--allow-root``), or
    the default folder when none are registered. Raises
    ``webapp_api.ReadRootDenied`` (-> 403 ``{"error": "허용되지 않은 경로"}``,
    no file content) otherwise. Returns the resolved path, which the
    handler then analyzes — the check and the read use the same path.
    """
    from .webapp_api import _require_read_root

    return _require_read_root(Path(path_text), default_folder)


def require_request_file(path: Path) -> None:
    """P8 (round 8): a named file that is missing is 404, a folder 400 — Korean, before any analysis.

    api-serve's own file endpoints (/api/analyze/*, /api/classify, /api/check,
    /api/check/stream, /api/compare) answered a missing file with 200
    "success" and a failed row (classify: 500, compare: a misleading 400).
    """
    import os

    from fastapi import HTTPException

    from .analysis_api import SingleFileError, check_single_file

    try:
        check_single_file(path)
    except SingleFileError as exc:
        raise HTTPException(status_code=404 if not os.path.lexists(path) else 400, detail=str(exc)) from exc


# P8: /api/multimodal combines caller-supplied scores; any other query
# parameter (a file_path, a typo) is refused instead of silently ignored.
MULTIMODAL_PARAMS = frozenset({"image_score", "text_score", "audio_score", "video_score"})
MULTIMODAL_UNKNOWN_PARAM = "알 수 없는 매개변수입니다: {names} — /api/multimodal은 점수(image_score, text_score, audio_score, video_score)만 받습니다(파일 분석은 /api/analyze/*)"
# P8: review bodies must be JSON objects (an array was a 500).
REVIEW_BODY_NOT_OBJECT = "검토 요청 본문은 JSON 객체여야 합니다"
REVIEW_ID_NOT_STRING = "artifact_id 값은 문자열이어야 합니다"


def missing_server_dependencies() -> list[str]:
    """Server packages that are not importable in this environment."""
    return [name for name in SERVER_DEPENDENCIES if importlib.util.find_spec(name) is None]


def _provenance_layer(raw: dict[str, Any]) -> dict[str, Any]:
    from .webapp_api import _provenance_layer as layer

    return layer(raw)


def _text_statistics_layer(raw: dict[str, Any]) -> dict[str, Any]:
    from .webapp_api import _text_statistics_layer as layer

    return layer(raw)


def _api_options() -> Any:
    """AnalysisOptions for an API request: the server's models dir, defaults."""
    from .webapp_api import _web_options

    return _web_options()


def _is_archive(path: Path) -> bool:
    from .archives import is_archive

    return is_archive(path)


def _archive_check_data(path: Path, options: Any, thresholds: Any, *, progress: Any = None) -> dict[str, Any]:
    """``/api/check`` payload for an archive: the folder-scan rows for it (R1)."""
    from .analysis_api import scan_file
    from .core import SCAN_JSON_SCHEMA_VERSION

    summary, items = scan_file(path, options, thresholds=thresholds, progress=progress)
    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "mode": "files",
        "summary": summary.to_json(),
        "items": [item.to_json() for item in items],
    }


# G9 (round 5): Korean text for FastAPI request-validation error types.
VALIDATION_ERROR_LABELS = {
    "missing": "값이 필요합니다",
    "int_parsing": "정수여야 합니다",
    "float_parsing": "숫자여야 합니다",
    "bool_parsing": "참/거짓 값이어야 합니다",
    "json_invalid": "JSON 본문을 해석할 수 없습니다",
    "string_type": "문자열이어야 합니다",
    "dict_type": "JSON 객체여야 합니다",
    "list_type": "배열이어야 합니다",
}
VALIDATION_LOCATION_LABELS = {"query": "쿼리", "body": "본문", "path": "경로", "header": "헤더"}


def validation_error_body(errors: Any) -> dict[str, object]:
    """``{"detail": "<Korean>", "errors": [{"loc", "type"}]}`` for a FastAPI 422 (no English ``msg``)."""
    lines: list[str] = []
    codes: list[dict[str, object]] = []
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        loc = [str(part) for part in error.get("loc", ())]
        kind = str(error.get("type", ""))
        where = VALIDATION_LOCATION_LABELS.get(loc[0], loc[0]) if loc else "요청"
        name = ".".join(loc[1:]) or where
        lines.append(f"{name}({where}): {VALIDATION_ERROR_LABELS.get(kind, f'형식이 올바르지 않습니다(`{kind}`)')}")
        codes.append({"loc": loc, "type": kind})
    return {"detail": "요청 매개변수 오류 — " + "; ".join(lines) if lines else "요청 매개변수 오류", "errors": codes}


def _api_json(payload: Any) -> Any:
    """X4: an ``/api/*`` payload as the response — an :class:`~deepfake_lens.webapp_api.ApiError`
    is sent with its own status (400/404/500) and Korean ``{"error"}`` body,
    exactly like the stdlib web server; anything else is returned as is (200)."""
    from .webapp_api import ApiError

    if isinstance(payload, ApiError):
        return surrogate_safe_json_response()(dict(payload), status_code=payload.status)
    return payload


def surrogate_safe_json_response() -> Any:
    """R11-1: the API's JSON response class — Starlette's ``JSONResponse`` whose
    body is :func:`~deepfake_lens.json_text.json_bytes`.

    Starlette encodes ``json.dumps(..., ensure_ascii=False)`` as strict UTF-8,
    so a row path holding a lone surrogate (a non-UTF-8 POSIX file name,
    PEP 383) made every route that echoes it a 500. The body is the same
    compact JSON with each surrogate written as the escape ``\\udcXX``.
    """
    from fastapi.responses import JSONResponse

    class SurrogateSafeJSONResponse(JSONResponse):
        def render(self, content: Any) -> bytes:
            return json_bytes(content, allow_nan=False, indent=None, separators=(",", ":"))

    return SurrogateSafeJSONResponse


def create_app(
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
    default_folder: Path | None = None,
) -> Any:
    """Create a FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from starlette.concurrency import run_in_threadpool
    except ImportError:
        raise ImportError("FastAPI가 필요합니다. 설치: `pip install fastapi uvicorn`")

    from .analysis_api import analyze_path, load_thresholds
    from .webapp_api import JsonBodyError, ReadRootDenied, load_json_body, read_root_denied_body

    # R10-8 (round 10): FastAPI's automatic /docs, /docs/oauth2-redirect,
    # /redoc and /openapi.json were routes missing from the service
    # document's REST table — an English Swagger/ReDoc UI (rule 3) that also
    # bypassed the documented surface. They are disabled; the REST table in
    # docs/deepfake-lens-service.md is the API's description.
    JSONResponse = surrogate_safe_json_response()  # R11-1: every JSON body is surrogate-safe
    app = FastAPI(
        title="Deepfake Lens API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        default_response_class=JSONResponse,
    )

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def korean_validation_error(_request: Any, exc: Any) -> Any:
        # G9 (round 5): FastAPI's default 422 body is English ("Field
        # required"); the examiner gets the Korean reason, the machine the
        # parameter location and error type codes.
        return JSONResponse(validation_error_body(exc.errors()), status_code=422)

    from starlette.exceptions import HTTPException as StarletteHTTPException

    from .webapp_api import http_error_text

    @app.exception_handler(StarletteHTTPException)
    async def korean_http_error(request: Any, exc: Any) -> Any:
        # N5: the framework's own 404/405 bodies were English ({"detail":
        # "Not Found"}, "Method Not Allowed"); a detail the server wrote
        # itself is already Korean and kept.
        return JSONResponse(
            {"detail": http_error_text(exc.status_code, exc.detail, method=request.method)},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def korean_server_error(request: Any, exc: Exception) -> Any:
        # N5: an unhandled exception is a Korean 500 (Starlette's default is
        # the English "Internal Server Error"); the traceback goes to the log.
        logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse({"detail": http_error_text(500)}, status_code=500)

    def _denied() -> Any:
        return JSONResponse(read_root_denied_body(), status_code=403)

    allowed_hosts = set(LOCAL_HOSTS) if host in LOCAL_HOSTS else {host}

    @app.middleware("http")
    async def request_guard(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/api/"):
            if token is not None:
                import secrets
                from .webapp import TOKEN_HEADERS

                if not any(
                    (supplied := request.headers.get(name)) and secrets.compare_digest(supplied, token)
                    for name in TOKEN_HEADERS
                ):
                    # N7: Korean body (the status code stays the machine-readable part).
                    return JSONResponse({"status": "error", "message": "인증 실패: API 토큰이 없거나 일치하지 않습니다"}, status_code=401)
            elif host_name(request.headers.get("host", "")) not in allowed_hosts:
                return JSONResponse({"status": "error", "message": "허용되지 않은 호스트입니다"}, status_code=403)
            elif (
                request.url.path not in CLIENT_HEADER_EXEMPT_PATHS
            ) and not (request.headers.get(CLIENT_HEADER) or "").strip():
                return JSONResponse(
                    {"status": "error", "message": f"{CLIENT_HEADER} 헤더가 필요합니다(브라우저 밖에서 온 요청 차단)"},
                    status_code=401,
                )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Token", "X-Deepfake-Lens-Token", CLIENT_HEADER, "Content-Type"],
    )

    @app.get("/")
    async def root():
        return {"message": "Deepfake Lens API", "version": "0.1.0"}

    # Same CSP as the stdlib server's GUI shell (webapp.GUI_CSP): strict
    # 'self' scripts/styles, blob: images and media for previews (S5).
    from .webapp import GUI_CSP

    @app.get("/gui")
    async def gui_view():
        from fastapi.responses import HTMLResponse
        from .webapp import _load_gui
        return HTMLResponse(
            _load_gui(),
            headers={"Content-Security-Policy": GUI_CSP, "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/gui.css")
    async def gui_css():
        from fastapi.responses import FileResponse
        return FileResponse(
            Path(__file__).resolve().parent / "gui.css",
            media_type="text/css; charset=utf-8",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/gui.js")
    async def gui_js():
        from fastapi.responses import FileResponse
        return FileResponse(
            Path(__file__).resolve().parent / "gui.js",
            media_type="text/javascript; charset=utf-8",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    # Synchronous analysis endpoints are plain `def`: FastAPI runs them in
    # its thread pool, so a multi-second analysis never blocks the loop.
    # D2: every /api/analyze/* and /api/classify answer is the three-verdict
    # result of analysis_api.analyze_path (the scan path); per-layer extras
    # are layer diagnostics (reference numbers, no band). /api/analyze/face
    # is a layer diagnostic only, like the `face` CLI command.
    def _verdict_payload(path: Path, command: str) -> dict[str, Any]:
        from .cli_standalone import analysis_result_for_path

        # B1: an archive is expanded like the folder scan (rows).
        options = _api_options()
        payload, _ = analysis_result_for_path(path, options, command=command, thresholds=load_thresholds(options))
        return payload

    @app.post("/api/analyze/image")
    def analyze_image(file_path: str):
        try:
            path = confine_request_path(file_path, default_folder)
        except ReadRootDenied:
            return _denied()
        require_request_file(path)  # P8: missing 404, folder 400
        try:
            return {"status": "success", "data": _verdict_payload(path, "analyze/image")}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    @app.post("/api/analyze/audio")
    def analyze_audio(file_path: str):
        try:
            path = confine_request_path(file_path, default_folder)
        except ReadRootDenied:
            return _denied()
        require_request_file(path)  # P8: missing 404, folder 400
        try:
            # The scan path runs the audio heuristics as reference signals
            # and the bundled audio profiles under their pins/gates.
            return {"status": "success", "data": _verdict_payload(path, "analyze/audio")}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    @app.post("/api/analyze/face")
    def analyze_face(file_path: str):
        from .face import analyze_faces
        from .layer_diagnostic import to_layer_diagnostic
        try:
            path = confine_request_path(file_path, default_folder)
        except ReadRootDenied:
            return _denied()
        require_request_file(path)  # P8: missing 404, folder 400
        try:
            result = analyze_faces(path)
            diag = to_layer_diagnostic("face", result.to_json(), layer_label="얼굴 조작 계층", subject=str(path))
            return {"status": "success", "data": diag}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    @app.post("/api/analyze/text")
    def analyze_text(text: str):
        from .cli_standalone import analyze_text_payload
        from .layer_diagnostic import to_layer_diagnostic
        from .text_advanced import analyze_text_advanced
        if not text.strip():
            raise HTTPException(status_code=400, detail=TEXT_EMPTY)
        if len(text.strip()) > MAX_TEXT_CHARS:
            raise HTTPException(status_code=400, detail=TEXT_TOO_LARGE)
        try:
            options = _api_options()
            data = analyze_text_payload(text, options, command="analyze/text", thresholds=load_thresholds(options))
            data["layer_diagnostics"] = {
                "text_statistics": to_layer_diagnostic(
                    "text_statistics", analyze_text_advanced(text).to_json(), layer_label="텍스트 문체 통계 계층"
                ),
            }
            return {"status": "success", "data": data}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    @app.post("/api/analyze/forensic")
    def analyze_forensic(file_path: str):
        from .c2pa import analyze_metadata_forensic
        from .layer_diagnostic import to_layer_diagnostic
        try:
            path = confine_request_path(file_path, default_folder)
        except ReadRootDenied:
            return _denied()
        require_request_file(path)  # P8: missing 404, folder 400
        try:
            data = _verdict_payload(path, "analyze/forensic")
            data["layer_diagnostics"] = {
                "provenance_metadata": to_layer_diagnostic(
                    "provenance_metadata", analyze_metadata_forensic(path).to_json(), layer_label="출처 메타데이터 계층"
                ),
            }
            return {"status": "success", "data": data}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    @app.post("/api/classify")
    def classify(file_path: str):
        from .cli_standalone import tool_attribution
        from .layer_diagnostic import to_layer_diagnostic
        try:
            path = confine_request_path(file_path, default_folder)
        except ReadRootDenied:
            return _denied()
        require_request_file(path)  # P8: missing 404, folder 400
        try:
            data = _verdict_payload(path, "classify")
            data["tool_candidates"] = to_layer_diagnostic(
                "tool_attribution", tool_attribution(path).to_json(), layer_label="생성 도구 표지 대조"
            )
            return {"status": "success", "data": data}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    @app.post("/api/check")
    def check(
        file_path: str | None = None,
        text: str | None = None,
        watermark_secret: str | None = None,
        watermark_gamma: float = 0.25,
    ):
        """Unified check-all: run every layer applicable to one input.

        Accepts either ``file_path`` (any media type) or raw ``text``. Runs
        the core scan, the neural member ensemble, metadata/C2PA forensics,
        and — for text — the style-fingerprint probes, returning one
        consolidated payload with per-layer sections.
        """
        import tempfile

        options = _api_options()
        thresholds = load_thresholds(options)
        try:
            if text and text.strip():
                trimmed = text.strip()
                if len(trimmed) > MAX_TEXT_CHARS:
                    raise HTTPException(status_code=400, detail=TEXT_TOO_LARGE)
                from .text_advanced import analyze_text_advanced
                # delete=False: Windows cannot reopen a delete=True temp file.
                tmp_name = ""
                try:
                    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
                        tmp.write(trimmed)
                        tmp_name = tmp.name
                    item = analyze_path(tmp_name, options, thresholds=thresholds)
                finally:
                    if tmp_name:
                        Path(tmp_name).unlink(missing_ok=True)
                data: dict[str, Any] = {
                    "mode": "text",
                    "item": item.to_json(),
                    "advanced": _text_statistics_layer(analyze_text_advanced(trimmed).to_json()),
                }
                if watermark_secret:
                    try:
                        from .watermark import detect_kgw_watermark
                        data["watermark"] = detect_kgw_watermark(
                            trimmed, secret=watermark_secret, gamma=watermark_gamma
                        ).to_json()
                    except Exception as exc:
                        logger.exception("watermark layer failed")
                        data["watermark"] = {"available": False, "reference_band": "unavailable", "reference_note": "워터마크 검사 실패", "error": failure_reason(exc)}
                        _layer_error(data, "watermark", exc)
                return {"status": "success", "data": data}
            if file_path:
                try:
                    path = confine_request_path(file_path, default_folder)
                except ReadRootDenied:
                    return _denied()
                require_request_file(path)  # P8: missing 404, folder 400
                if _is_archive(path):
                    # R1: an archive is expanded exactly as the folder scan
                    # expands it (member rows + container row).
                    return {"status": "success", "data": _archive_check_data(path, options, thresholds)}
                item = analyze_path(path, options, thresholds=thresholds)
                data = {"mode": "file", "item": item.to_json()}
                try:
                    from .c2pa import analyze_metadata_forensic
                    data["forensic"] = _provenance_layer(analyze_metadata_forensic(path).to_json())
                except Exception as exc:
                    logger.exception("forensic layer failed")
                    data["forensic"] = None
                    _layer_error(data, "forensic", exc)
                if item.kind == "text":
                    try:
                        from .text_advanced import analyze_text_advanced
                        data["advanced"] = _text_statistics_layer(analyze_text_advanced(
                            path.read_text(encoding="utf-8", errors="replace")[: 256 * 1024]
                        ).to_json())
                    except Exception as exc:
                        logger.exception("text-advanced layer failed")
                        data["advanced"] = None
                        _layer_error(data, "advanced", exc)
                return {"status": "success", "data": data}
            raise HTTPException(status_code=400, detail=TEXT_OR_FILE_REQUIRED)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    # --- Streaming job API -------------------------------------------------
    # /api/check/stream runs the same layered check as /api/check but reports
    # per-stage progress over SSE. Jobs register in _JOBS so a client can
    # cancel between stages via /api/jobs/{id}/cancel — cancellation is
    # cooperative and takes effect at stage boundaries, not mid-analysis.
    # G34: the registry is capped (MAX_JOBS -> 429), guarded by a lock, and
    # a client disconnect cancels its job.
    _JOBS: dict[str, dict[str, Any]] = {}
    _JOBS_LOCK = threading.Lock()

    def _register_job() -> tuple[str, threading.Event]:
        import uuid

        with _JOBS_LOCK:
            if len(_JOBS) >= MAX_JOBS:
                raise HTTPException(status_code=429, detail=JOBS_FULL_MESSAGE)
            job_id = uuid.uuid4().hex[:12]
            cancel = threading.Event()
            _JOBS[job_id] = {"cancel": cancel, "done": False}
        return job_id, cancel

    def _mark_done(job_id: str) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["done"] = True

    def _drop_job(job_id: str) -> None:
        with _JOBS_LOCK:
            _JOBS.pop(job_id, None)

    def _sse_response(request: Request, job_id: str, cancel: threading.Event, run: Any) -> Any:
        """Stream ``run()``'s (event, data) tuples as SSE from a worker thread."""
        import asyncio

        from fastapi.responses import StreamingResponse

        async def events():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[Any] = asyncio.Queue()

            def emit(evt: Any) -> None:
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, evt)
                except RuntimeError:
                    # Event loop already closed (client gone, server
                    # shutting down): stop the worker at the next stage.
                    cancel.set()

            def produce() -> None:
                try:
                    for evt in run():
                        emit(evt)
                except Exception as exc:  # noqa: BLE001 - report, don't hang
                    logger.exception("streaming job %s failed", job_id)
                    emit(("error", {"detail": failure_reason(exc)}))
                finally:
                    _mark_done(job_id)
                    emit(None)

            threading.Thread(target=produce, daemon=True, name=f"api-job-{job_id}").start()
            finished = False
            try:
                while True:
                    try:
                        evt = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        continue
                    if evt is None:
                        finished = True
                        break
                    name, data = evt
                    yield f"event: {name}\ndata: {json_dumps(data, ensure_ascii=False)}\n\n"
            finally:
                # Client disconnect (or generator close) cancels the job at
                # its next stage boundary instead of running to completion.
                if not finished:
                    cancel.set()
                _drop_job(job_id)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/check/stream")
    async def check_stream(
        request: Request,
        file_path: str | None = None,
        text: str | None = None,
        watermark_secret: str | None = None,
        watermark_gamma: float = 0.25,
    ):
        # P8/R9-3: every refusal is an HTTP error status before the stream
        # starts (it used to be 200 + an SSE "error" event): no text and no
        # file_path 400, text over the limit 400, a file outside the read
        # roots 403, missing 404, a folder 400.
        confined: Path | None = None
        if text and text.strip():
            if len(text.strip()) > MAX_TEXT_CHARS:
                raise HTTPException(status_code=400, detail=TEXT_TOO_LARGE)
        elif file_path:
            try:
                confined = confine_request_path(file_path, default_folder)
            except ReadRootDenied:
                return _denied()
            require_request_file(confined)
        else:
            raise HTTPException(status_code=400, detail=TEXT_OR_FILE_REQUIRED)
        job_id, cancel = _register_job()

        def run_layered() -> Any:
            """Run the check stages, aborting between stages if cancelled."""
            import tempfile

            options = _api_options()
            thresholds = load_thresholds(options)
            stages: list[tuple[str, Any]] = []
            yield ("job", {"job_id": job_id})
            if cancel.is_set():
                yield ("cancelled", {"job_id": job_id})
                return

            if text and text.strip():
                trimmed = text.strip()  # length checked before the stream (R9-3)
                yield ("progress", {"stage": "core", "index": 1, "total": 3})
                tmp_name = ""
                try:
                    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
                        tmp.write(trimmed)
                        tmp_name = tmp.name
                    item = analyze_path(tmp_name, options, thresholds=thresholds)
                finally:
                    if tmp_name:
                        Path(tmp_name).unlink(missing_ok=True)
                if cancel.is_set():
                    yield ("cancelled", {"job_id": job_id})
                    return
                stages.append(("item", item.to_json()))

                yield ("progress", {"stage": "text-advanced", "index": 2, "total": 3})
                from .text_advanced import analyze_text_advanced
                stages.append(("advanced", _text_statistics_layer(analyze_text_advanced(trimmed).to_json())))

                if watermark_secret:
                    if cancel.is_set():
                        yield ("cancelled", {"job_id": job_id})
                        return
                    yield ("progress", {"stage": "watermark", "index": 3, "total": 3})
                    try:
                        from .watermark import detect_kgw_watermark
                        wm: Any = detect_kgw_watermark(
                            trimmed, secret=watermark_secret, gamma=watermark_gamma
                        ).to_json()
                    except Exception as exc:
                        logger.exception("watermark layer failed")
                        wm = {"available": False, "reference_band": "unavailable", "reference_note": "워터마크 검사 실패", "error": failure_reason(exc)}
                    stages.append(("watermark", wm))
                payload = {"mode": "text", **dict(stages)}
            else:
                assert confined is not None  # validated before the stream (R9-3)
                path = confined
                if _is_archive(path):
                    # R1: same expansion as the folder scan; one progress
                    # event per member/container row.
                    import queue

                    rows: queue.Queue[Any] = queue.Queue()
                    archive_outcome: dict[str, Any] = {}

                    def on_row(row: Any, done: int, planned: int) -> None:
                        rows.put(("progress", {"stage": "archive", "index": done, "total": planned, "path": row.path}))

                    def expand() -> None:
                        try:
                            archive_outcome["data"] = _archive_check_data(path, options, thresholds, progress=on_row)
                        except Exception as exc:  # noqa: BLE001 - reported as an SSE error event
                            logger.exception("archive check failed: %s", path)
                            archive_outcome["error"] = exc
                        finally:
                            rows.put(None)

                    threading.Thread(target=expand, daemon=True, name=f"api-archive-{job_id}").start()
                    while (evt := rows.get()) is not None:
                        yield evt
                    if "error" in archive_outcome:
                        yield ("error", {"detail": f"압축 파일 검사 실패: {failure_reason(archive_outcome['error'])}"})
                        return
                    yield ("result", archive_outcome["data"])
                    return
                yield ("progress", {"stage": "core", "index": 1, "total": 2})
                item = analyze_path(path, options, thresholds=thresholds)
                if cancel.is_set():
                    yield ("cancelled", {"job_id": job_id})
                    return
                stages.append(("item", item.to_json()))

                yield ("progress", {"stage": "forensic", "index": 2, "total": 2})
                try:
                    from .c2pa import analyze_metadata_forensic
                    forensic: Any = _provenance_layer(analyze_metadata_forensic(path).to_json())
                except Exception as exc:
                    logger.exception("forensic layer failed")
                    forensic = None
                    stages.append(("forensic_error", [{"layer": "forensic", "status": "failed", "reason": failure_reason(exc)}]))
                stages.append(("forensic", forensic))
                if item.kind == "text":
                    try:
                        from .text_advanced import analyze_text_advanced
                        stages.append(("advanced", _text_statistics_layer(analyze_text_advanced(
                            path.read_text(encoding="utf-8", errors="replace")[: 256 * 1024]
                        ).to_json())))
                    except Exception as exc:
                        logger.exception("text-advanced layer failed")
                        stages.append(("advanced", None))
                        stages.append(("advanced_error", [{"layer": "advanced", "status": "failed", "reason": failure_reason(exc)}]))
                payload = {"mode": "file", **dict(stages)}

            yield ("result", payload)

        return _sse_response(request, job_id, cancel, run_layered)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job["cancel"].set()
        if job is None:
            raise HTTPException(status_code=404, detail="알 수 없거나 이미 끝난 작업입니다")
        return {"status": "success", "job_id": job_id, "cancelled": True}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            snapshot = None if job is None else (bool(job["done"]), bool(job["cancel"].is_set()))
        if snapshot is None:
            raise HTTPException(status_code=404, detail="알 수 없거나 이미 끝난 작업입니다")
        return {
            "status": "success",
            "job_id": job_id,
            "done": snapshot[0],
            "cancelled": snapshot[1],
        }

    # /api/scan/stream scans a server-local directory with per-file
    # progress events over SSE. Shares the _JOBS registry so clients can
    # cancel between files via /api/jobs/{id}/cancel. Directory reads are
    # limited to max_files entries; the directory must sit inside a read
    # root (G31, 403 otherwise) like /api/scan and /api/check.
    @app.post("/api/scan/stream")
    async def scan_stream(
        request: Request,
        directory: str,
        recursive: bool = False,
        max_files: int = 200,
    ):
        if max_files < 1:
            # Y8: a zero/negative limit is refused (400), not clamped to 1.
            from .analysis_api import MAX_FILES_TOO_SMALL

            raise HTTPException(status_code=400, detail=MAX_FILES_TOO_SMALL)
        # R9-3 (round 9): the folder is validated before the stream starts —
        # no directory 400, outside the read roots 403, missing / a file /
        # unreadable 400 with scan's S4 text (as GET /api/scan, E7). It used
        # to be 200 + an SSE "error" event.
        if not directory:
            raise HTTPException(status_code=400, detail=DIRECTORY_REQUIRED)
        try:
            root = confine_request_path(directory, default_folder)
        except ReadRootDenied:
            return _denied()
        from .core import ScanFolderError, check_scan_folder

        try:
            check_scan_folder(root)
        except ScanFolderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job_id, cancel = _register_job()

        def run_scan():
            # R1: the stream runs analysis_api.scan_folder — the CLI's scan —
            # so archives are expanded into member + container rows, refused
            # members (bombs, traversal, links) are recorded per member and
            # symlinked files appear as skipped rows. Progress events come
            # from scan_folder's per-row callback.
            import dataclasses
            import queue

            from .analysis_api import scan_folder_run, scan_payload

            yield ("job", {"job_id": job_id})
            options = dataclasses.replace(
                _api_options(), recursive=recursive, max_files=max(1, min(max_files, MAX_STREAM_SCAN_FILES)),
            )
            events: queue.Queue[Any] = queue.Queue()
            outcome: dict[str, Any] = {}
            # N8: rows planned (known before the first file) and rows done,
            # for the cancelled event.
            tally = {"planned": 0, "done": 0}

            def on_plan(planned: int) -> None:
                tally["planned"] = planned

            def on_row(item: Any, done: int, planned: int) -> None:
                tally["done"] = done
                tally["planned"] = max(tally["planned"], planned)
                data = item.to_json()
                result = data.get("result") or {}
                events.put(("progress", {
                    "stage": "scan", "index": done, "total": planned,
                    "path": data.get("path"), "status": data.get("status"),
                    "verdict_code": result.get("verdict_code"),
                }))

            def work() -> None:
                try:
                    outcome["value"] = scan_folder_run(root, options, should_stop=cancel.is_set, progress=on_row, on_plan=on_plan)
                except Exception as exc:  # noqa: BLE001 - reported as an SSE error event
                    logger.exception("streaming scan failed: %s", root)
                    outcome["error"] = exc
                finally:
                    events.put(None)

            threading.Thread(target=work, daemon=True, name=f"api-scan-{job_id}").start()
            yield ("progress", {"stage": "enumerate"})
            while True:
                evt = events.get()
                if evt is None:
                    break
                yield evt
            if "error" in outcome:
                yield ("error", {"detail": f"폴더 검사 실패: {failure_reason(outcome['error'])}"})
                return
            run = outcome["value"]
            summary, items, thresholds = run.summary, run.items, run.thresholds
            if cancel.is_set():
                # N8: total = rows the scan planned, done = rows processed
                # before the cancel (processed kept as the old name of done).
                yield ("cancelled", {"job_id": job_id, "done": tally["done"], "processed": tally["done"], "total": tally["planned"]})
                return
            payload = scan_payload(summary, items, thresholds, options)
            # P1: the folder the rows are relative to, for POST /api/report.
            from .json_text import fs_b64encode
            from .webapp_api import scan_root_text

            root_text = scan_root_text(root)
            payload["scan_root"] = root_text
            payload["scan_root_b64"] = fs_b64encode(root_text)  # R12-4
            # Rows are the /api/scan rows; verdict_code/grade/probability are
            # also copied to the row top level for stream clients that read
            # them there (the pre-R1 stream row shape).
            for row in payload["items"]:
                result = row.get("result") or {}
                row.setdefault("verdict_code", result.get("verdict_code"))
                row.setdefault("grade", result.get("grade"))
                row.setdefault("probability", result.get("probability"))
            # D16/R5: verdict counts come from the scan summary (container
            # rows counted by verdict); "other" = skipped + duplicate rows,
            # "failed" = unsupported / unanalyzable rows.
            counts = {
                "failed": summary.unsupported_or_failed,
                "other": summary.skipped + summary.duplicates,
                "manipulation_evidence": summary.manipulation_evidence,
                "authenticity_evidence": summary.authenticity_evidence,
                "undetermined": summary.undetermined,
            }
            yield ("result", {"mode": "scan", "directory": str(root), "total": len(items),
                              "capped": summary.capped, "counts": counts, **payload})

        return _sse_response(request, job_id, cancel, run_scan)

    @app.post("/api/compare")
    def compare(file_path_a: str, file_path_b: str):
        """Two-file comparison: same-speaker distance for audio pairs,
        same-author stylometry for text/document pairs."""
        from .core import compare_files

        try:
            path_a = confine_request_path(file_path_a, default_folder)
            path_b = confine_request_path(file_path_b, default_folder)
        except ReadRootDenied:
            return _denied()
        # P8: a missing file was "한쪽 파일의 텍스트 추출에 실패했습니다" (400).
        require_request_file(path_a)
        require_request_file(path_b)
        try:
            result = compare_files(path_a, path_b)
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc
        if "error" in result:
            raise HTTPException(status_code=400, detail=str(result["error"]))
        from .webapp_api import compare_layer
        return {"status": "success", "data": compare_layer(result)}

    @app.post("/api/multimodal")
    def multimodal(
        request: Request,
        image_score: int | None = None,
        text_score: int | None = None,
        audio_score: int | None = None,
        video_score: int | None = None,
    ):
        # D2: caller-supplied scores are unmeasured reference numbers — the
        # combination is a layer diagnostic, never a band. A verdict for
        # files comes from /api/analyze/* or /api/scan.
        from .layer_diagnostic import to_layer_diagnostic
        from .multimodal import analyze_multimodal
        unknown = sorted(set(request.query_params) - MULTIMODAL_PARAMS)
        if unknown:
            raise HTTPException(status_code=400, detail=MULTIMODAL_UNKNOWN_PARAM.format(names=", ".join(unknown)))
        try:
            result = analyze_multimodal(
                image_score=image_score,
                text_score=text_score,
                audio_score=audio_score,
                video_score=video_score,
            )
            diag = to_layer_diagnostic("multimodal_scores", result.to_json(), layer_label="멀티모달 원점수 조합")
            return {"status": "success", "data": diag}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=failure_reason(exc)) from exc

    # --- GUI & Webapp compatibility endpoints ---
    # Same payload functions as the stdlib web server (webapp_api), which in
    # turn call analysis_api — so /api/scan here, in the web GUI and in the
    # CLI return the same verdicts and threshold provenance (G7).
    @app.get("/api/scan")
    def api_scan(request: Request):
        from urllib.parse import parse_qs
        from .webapp_api import ReadRootDenied, _scan_job_start, _scan_payload, read_root_denied_body
        qs = str(request.url.query)
        if parse_qs(qs).get("async", ["false"])[0].lower() in {"1", "true", "yes"}:
            try:
                return _scan_job_start(qs, default_folder=default_folder)
            except ReadRootDenied:
                return JSONResponse(read_root_denied_body(), status_code=403)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        try:
            return _api_json(_scan_payload(qs, default_folder=default_folder))
        except ReadRootDenied:
            return JSONResponse(read_root_denied_body(), status_code=403)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/scan-status")
    def api_scan_status(request: Request):
        from .webapp_api import _scan_status_payload
        return _api_json(_scan_status_payload(str(request.url.query)))

    @app.get("/api/scan-cancel")
    def api_scan_cancel(request: Request):
        from .webapp_api import _scan_cancel_payload
        return _api_json(_scan_cancel_payload(str(request.url.query)))

    @app.get("/api/heatmap")
    def api_heatmap(request: Request):
        from fastapi import Response
        from .webapp_api import _heatmap_payload
        # G8: the payload is (status, body, error message); the media type
        # is fixed — heatmaps are PNG, errors are plain text.
        status, data, message = _heatmap_payload(str(request.url.query))
        headers = {"X-Content-Type-Options": "nosniff"}
        if message:
            headers["X-Deepfake-Lens-Error"] = message
        media_type = "image/png" if status == 200 else "text/plain; charset=utf-8"
        return Response(content=data, status_code=status, media_type=media_type, headers=headers)

    @app.get("/api/preview")
    def api_preview(request: Request):
        from fastapi import Response
        from .webapp_api import _preview_payload
        # G8: (status, data, message, mime) — the 3rd element is the error
        # message, not a content type; nosniff keeps previews media-only.
        status, data, message, mime = _preview_payload(str(request.url.query))
        headers = {"X-Content-Type-Options": "nosniff"}
        if message:
            headers["X-Deepfake-Lens-Error"] = message
        media_type = mime if status == 200 and mime else "text/plain; charset=utf-8"
        return Response(content=data, status_code=status, media_type=media_type, headers=headers)

    @app.get("/api/analyze-file")
    def api_analyze_file(request: Request):
        from .webapp_api import ReadRootDenied, _analyze_file_payload, read_root_denied_body
        try:
            return _api_json(_analyze_file_payload(str(request.url.query)))
        except ReadRootDenied:
            return JSONResponse(read_root_denied_body(), status_code=403)

    @app.get("/api/stats")
    async def api_stats():
        from .webapp_api import _stats_payload
        return _stats_payload()

    @app.post("/api/analyze-upload")
    async def api_analyze_upload(request: Request):
        from .webapp_api import MAX_UPLOAD_BYTES, _analyze_upload_payload
        body = await request.body()
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"업로드 크기가 상한({MAX_UPLOAD_BYTES}바이트)을 초과합니다")
        content_type = request.headers.get("content-type", "")
        return _api_json(await run_in_threadpool(_analyze_upload_payload, content_type, body))

    @app.post("/api/report")
    async def api_report(request: Request):
        from fastapi import Response

        from .webapp_api import report_http_response

        body = await request.body()
        # N9/N11: one response builder for both servers — format from
        # ?format= or the JSON body, malformed requests 400 in Korean.
        status, content_type, content, headers = await run_in_threadpool(
            lambda: report_http_response(body, request.query_params.get("format"), default_folder=default_folder)
        )
        return Response(content=content, status_code=status, media_type=content_type, headers=headers)

    @app.post("/api/feedback")
    async def api_feedback(request: Request):
        from .webapp_api import _feedback_payload
        body = await request.body()
        return _api_json(await run_in_threadpool(_feedback_payload, body))

    @app.get("/api/artifacts/{artifact_id:path}/review")
    async def get_artifact_review(artifact_id: str):
        from .reviews import get_default_review_store
        store = get_default_review_store()
        review = store.get_review(artifact_id)
        return {"status": "success", "artifact_id": artifact_id, "review": review}

    @app.put("/api/artifacts/{artifact_id:path}/review")
    async def put_artifact_review(artifact_id: str, request: Request):
        from .reviews import get_default_review_store
        store = get_default_review_store()
        body = await request.body()
        try:
            data = load_json_body(body, empty={})
        except JsonBodyError as exc:  # R10-5, R11-3: "JSON 중첩이 너무 깊습니다" for a too-deep body
            raise HTTPException(status_code=400, detail=str(exc))
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail=REVIEW_BODY_NOT_OBJECT)  # P8: was a 500
        saved = store.save_review(artifact_id, data)
        return {"status": "success", "artifact_id": artifact_id, "review": saved}

    @app.get("/api/review")
    async def api_get_review(request: Request):
        from urllib.parse import parse_qs
        from .reviews import get_default_review_store
        qs = parse_qs(str(request.url.query))
        artifact_id = qs.get("path", qs.get("artifact_id", [""]))[0]
        if not artifact_id:
            raise HTTPException(status_code=400, detail="path 또는 artifact_id 쿼리 매개변수가 필요합니다")
        store = get_default_review_store()
        review = store.get_review(artifact_id)
        return {"status": "success", "artifact_id": artifact_id, "review": review}

    @app.post("/api/review")
    async def api_post_review(request: Request):
        from .reviews import get_default_review_store
        body = await request.body()
        try:
            data = load_json_body(body, empty={})
        except JsonBodyError as exc:  # R10-5, R11-3: "JSON 중첩이 너무 깊습니다" for a too-deep body
            raise HTTPException(status_code=400, detail=str(exc))
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail=REVIEW_BODY_NOT_OBJECT)  # P8: was a 500
        artifact_id = data.get("artifact_id", data.get("path", ""))
        if not artifact_id:
            raise HTTPException(status_code=400, detail="본문에 artifact_id가 없습니다")
        if not isinstance(artifact_id, str):
            raise HTTPException(status_code=400, detail=REVIEW_ID_NOT_STRING)
        store = get_default_review_store()
        saved = store.save_review(artifact_id, data)
        return {"status": "success", "artifact_id": artifact_id, "review": saved}

    @app.get("/api/reviews")
    async def api_list_reviews():
        from .reviews import get_default_review_store
        store = get_default_review_store()
        return {"status": "success", "reviews": store.list_reviews()}

    return app


def server_dependency_hint(missing: list[str]) -> str:
    """Korean install hint printed when ``api-serve`` cannot start."""
    return (
        f"오류: API 서버에 필요한 패키지가 설치되어 있지 않습니다: {', '.join(missing)}\n"
        f"설치: `pip install {' '.join(missing)}`\n"
        "설치 없이 쓰려면 내장 웹 서버를 사용하세요: deepfake-lens web"
    )


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
    default_folder: Path | None = None,
    allow_lan: bool = False,
    allow_roots: list[Path] | None = None,
) -> None:
    """Run the API server.

    Read roots (G31) are registered here only: ``default_folder`` and each
    ``allow_roots`` entry (``--allow-root``); other paths get 403.

    ``token`` enables authentication and is mandatory for non-localhost binds
    (enforced by the ``api-serve`` and ``web`` CLI commands).

    G29: without fastapi/uvicorn this prints an install hint to stderr and
    exits with status 2 (no traceback).
    """
    missing = missing_server_dependencies()
    if missing:
        print(server_dependency_hint(missing), file=sys.stderr)
        raise SystemExit(SERVER_DEPS_EXIT_CODE)
    import uvicorn

    from .webapp_api import configure_read_roots

    configure_read_roots(default_folder, allow_roots)
    app = create_app(host, port, token=token, default_folder=default_folder)
    print(f"Deepfake Lens 통합 서버 시작: http://{host}:{port}" + (" (토큰 필요)" if token else ""))
    uvicorn.run(app, host=host, port=port)
