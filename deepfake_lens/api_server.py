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
import json
import logging
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from .checks import failure_reason
from .vendor_weights import default_models_dir

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
# GET endpoints that start/cancel work or read files the caller names also
# need the header (G8, review M1/M2): a cross-origin <img src> or link can
# issue a "simple" GET, which must not be able to start or cancel a scan.
CLIENT_HEADER_GET_PATHS = frozenset({
    "/api/scan",
    "/api/scan-cancel",
    "/api/scan-status",
    "/api/analyze-file",
    "/api/heatmap",
    "/api/preview",
})

# Streaming-job registry cap (G34): each job holds a worker thread and its
# results until the client disconnects; 32 matches the web server's
# _SCAN_JOB_MAX so both servers bound concurrent work the same way.
MAX_JOBS = 32
JOBS_FULL_MESSAGE = "too many jobs in flight; retry after a running job finishes"

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


def missing_server_dependencies() -> list[str]:
    """Server packages that are not importable in this environment."""
    return [name for name in SERVER_DEPENDENCIES if importlib.util.find_spec(name) is None]


def _api_options() -> Any:
    """AnalysisOptions for an API request: the server's models dir, defaults."""
    from .webapp_api import _web_options

    return _web_options()


def create_app(
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
    default_folder: Path | None = None,
) -> Any:
    """Create a FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse
        from starlette.concurrency import run_in_threadpool
    except ImportError:
        raise ImportError("FastAPI is required. Install with: pip install fastapi uvicorn")

    from .analysis_api import analyze_path, load_thresholds

    app = FastAPI(title="Deepfake Lens API", version="0.1.0")

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
                    return JSONResponse({"status": "error", "message": "unauthorized"}, status_code=401)
            elif host_name(request.headers.get("host", "")) not in allowed_hosts:
                return JSONResponse({"status": "error", "message": "host not allowed"}, status_code=403)
            elif (
                request.method != "GET" or request.url.path in CLIENT_HEADER_GET_PATHS
            ) and not (request.headers.get(CLIENT_HEADER) or "").strip():
                return JSONResponse(
                    {"status": "error", "message": f"missing {CLIENT_HEADER} header"},
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

    # GUI is fully externalized (gui.css/gui.js) and markup carries no
    # inline style attributes — script-src and style-src are both strict
    # 'self'; blob: covers object-URL previews and heatmaps.
    GUI_CSP = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'"
    )

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
    @app.post("/api/analyze/image")
    def analyze_image(file_path: str):
        try:
            result = analyze_path(file_path, _api_options())
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/analyze/audio")
    def analyze_audio(file_path: str):
        from .audio import analyze_audio
        try:
            # Bundled audio profiles degrade gracefully when checkpoints
            # or the optional torch stack is absent.
            models_dir = default_models_dir()
            profiles = [p for name in ("aasist-runtime.json", "wav2vec-deepfake-audio-runtime.json") if (p := models_dir / name).is_file()]
            result = analyze_audio(file_path, model_path=profiles or None)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/analyze/face")
    def analyze_face(file_path: str):
        from .face import analyze_faces
        try:
            result = analyze_faces(file_path)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/analyze/text")
    def analyze_text(text: str):
        from .text_advanced import analyze_text_advanced
        try:
            result = analyze_text_advanced(text)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/analyze/forensic")
    def analyze_forensic(file_path: str):
        from .c2pa import analyze_metadata_forensic
        try:
            result = analyze_metadata_forensic(file_path)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/classify")
    def classify(file_path: str):
        from .classifier import classify_metadata, classify_text_content
        from .png import read_png_metadata
        try:
            path = Path(file_path)
            max_bytes = 64 * 1024 * 1024
            with path.open("rb") as handle:
                data = handle.read(max_bytes)
            text_extensions = {".txt", ".md", ".py", ".js", ".json", ".csv", ".log"}
            if path.suffix.lower() in text_extensions:
                result = classify_text_content(data.decode("utf-8", errors="ignore"))
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                result = classify_metadata(read_png_metadata(data))
            elif data[:2] == b"\xff\xd8":
                result = classify_metadata({"format": "jpeg", "size": str(len(data))})
            else:
                result = classify_metadata({})
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
                if len(trimmed) > 256 * 1024:
                    raise HTTPException(status_code=400, detail="text exceeds 256KB")
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
                    "advanced": analyze_text_advanced(trimmed).to_json(),
                }
                if watermark_secret:
                    try:
                        from .watermark import detect_kgw_watermark
                        data["watermark"] = detect_kgw_watermark(
                            trimmed, secret=watermark_secret, gamma=watermark_gamma
                        ).to_json()
                    except Exception as exc:
                        logger.exception("watermark layer failed")
                        data["watermark"] = {"available": False, "verdict": "워터마크 검사 실패", "error": failure_reason(exc)}
                        _layer_error(data, "watermark", exc)
                return {"status": "success", "data": data}
            if file_path:
                path = Path(file_path)
                item = analyze_path(path, options, thresholds=thresholds)
                data = {"mode": "file", "item": item.to_json()}
                try:
                    from .c2pa import analyze_metadata_forensic
                    data["forensic"] = analyze_metadata_forensic(path).to_json()
                except Exception as exc:
                    logger.exception("forensic layer failed")
                    data["forensic"] = None
                    _layer_error(data, "forensic", exc)
                if item.kind == "text":
                    try:
                        from .text_advanced import analyze_text_advanced
                        data["advanced"] = analyze_text_advanced(
                            path.read_text(encoding="utf-8", errors="replace")[: 256 * 1024]
                        ).to_json()
                    except Exception as exc:
                        logger.exception("text-advanced layer failed")
                        data["advanced"] = None
                        _layer_error(data, "advanced", exc)
                return {"status": "success", "data": data}
            raise HTTPException(status_code=400, detail="file_path or text required")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
                    emit(("error", {"detail": str(exc)}))
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
                    yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
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
                trimmed = text.strip()
                if len(trimmed) > 256 * 1024:
                    yield ("error", {"detail": "text exceeds 256KB"})
                    return
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
                stages.append(("advanced", analyze_text_advanced(trimmed).to_json()))

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
                        wm = {"available": False, "verdict": "워터마크 검사 실패", "error": failure_reason(exc)}
                    stages.append(("watermark", wm))
                payload = {"mode": "text", **dict(stages)}
            else:
                if not file_path:
                    yield ("error", {"detail": "file_path or text required"})
                    return
                path = Path(file_path)
                yield ("progress", {"stage": "core", "index": 1, "total": 2})
                item = analyze_path(path, options, thresholds=thresholds)
                if cancel.is_set():
                    yield ("cancelled", {"job_id": job_id})
                    return
                stages.append(("item", item.to_json()))

                yield ("progress", {"stage": "forensic", "index": 2, "total": 2})
                try:
                    from .c2pa import analyze_metadata_forensic
                    forensic: Any = analyze_metadata_forensic(path).to_json()
                except Exception as exc:
                    logger.exception("forensic layer failed")
                    forensic = None
                    stages.append(("forensic_error", [{"layer": "forensic", "status": "failed", "reason": failure_reason(exc)}]))
                stages.append(("forensic", forensic))
                if item.kind == "text":
                    try:
                        from .text_advanced import analyze_text_advanced
                        stages.append(("advanced", analyze_text_advanced(
                            path.read_text(encoding="utf-8", errors="replace")[: 256 * 1024]
                        ).to_json()))
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
            raise HTTPException(status_code=404, detail="unknown or finished job")
        return {"status": "success", "job_id": job_id, "cancelled": True}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            snapshot = None if job is None else (bool(job["done"]), bool(job["cancel"].is_set()))
        if snapshot is None:
            raise HTTPException(status_code=404, detail="unknown or finished job")
        return {
            "status": "success",
            "job_id": job_id,
            "done": snapshot[0],
            "cancelled": snapshot[1],
        }

    # /api/scan/stream scans a server-local directory with per-file
    # progress events over SSE. Shares the _JOBS registry so clients can
    # cancel between files via /api/jobs/{id}/cancel. Directory reads are
    # limited to max_files entries; same trust level as /api/check.
    @app.post("/api/scan/stream")
    async def scan_stream(
        request: Request,
        directory: str,
        recursive: bool = False,
        max_files: int = 200,
    ):
        job_id, cancel = _register_job()

        def run_scan():
            from .core import _iter_files

            yield ("job", {"job_id": job_id})
            root = Path(directory) if directory else None
            if root is None or not root.is_dir():
                yield ("error", {"detail": "directory required"})
                return
            try:
                paths = []
                capped = False
                for p in _iter_files(root, recursive=recursive):
                    if len(paths) >= max(1, min(max_files, 5000)):
                        capped = True
                        break
                    paths.append(p)
            except Exception as exc:  # noqa: BLE001
                logger.exception("directory listing failed: %s", root)
                yield ("error", {"detail": f"listing failed: {exc}"})
                return
            options = _api_options()
            thresholds = load_thresholds(options)
            total = len(paths)
            yield ("progress", {"stage": "enumerate", "total": total, "capped": capped})
            items: list[dict[str, Any]] = []
            # Legacy band keys ("medium" stays 0) plus contract-v2 verdict keys.
            counts = {"high": 0, "medium": 0, "unknown": 0, "low": 0, "failed": 0,
                      "manipulation_evidence": 0, "authenticity_evidence": 0, "undetermined": 0}
            for index, path in enumerate(paths, 1):
                if cancel.is_set():
                    yield ("cancelled", {"job_id": job_id, "processed": index - 1, "total": total})
                    return
                try:
                    item = analyze_path(path, options, root=root, thresholds=thresholds)
                    data = item.to_json()
                    result = data.get("result") or {}
                    band = str(result.get("band") or "unknown")
                    status = str(data.get("status") or "failed")
                    verdict_code = str(result.get("verdict_code") or "undetermined")
                    if status == "analyzed":
                        counts[band if band in counts else "unknown"] += 1
                        counts[verdict_code if verdict_code in counts else "undetermined"] += 1
                    elif status in {"skipped", "duplicate"}:
                        counts["unknown"] += 1
                    else:
                        counts["failed"] += 1
                    items.append({"path": data.get("path"), "kind": data.get("kind"),
                                  "status": status, "band": band if status == "analyzed" else None,
                                  "verdict_code": verdict_code if status == "analyzed" else None,
                                  "grade": result.get("grade"),
                                  "score": result.get("score")})
                except Exception as exc:  # noqa: BLE001 - per-file failure is data
                    logger.exception("analysis failed: %s", path)
                    counts["failed"] += 1
                    items.append({"path": str(path), "status": "failed", "error": failure_reason(exc)})
                yield ("progress", {"stage": "scan", "index": index, "total": total,
                                    "path": path.name, "band": items[-1].get("band")})
            yield ("result", {"mode": "scan", "directory": str(root), "total": total,
                              "capped": capped, "counts": counts, "items": items})

        return _sse_response(request, job_id, cancel, run_scan)

    @app.post("/api/compare")
    def compare(file_path_a: str, file_path_b: str):
        """Two-file comparison: same-speaker distance for audio pairs,
        same-author stylometry for text/document pairs."""
        from .core import compare_files

        try:
            result = compare_files(Path(file_path_a), Path(file_path_b))
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if "error" in result:
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return {"status": "success", "data": result}

    @app.post("/api/multimodal")
    def multimodal(
        image_score: int | None = None,
        text_score: int | None = None,
        audio_score: int | None = None,
        video_score: int | None = None,
    ):
        from .multimodal import analyze_multimodal
        try:
            result = analyze_multimodal(
                image_score=image_score,
                text_score=text_score,
                audio_score=audio_score,
                video_score=video_score,
            )
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            logger.exception("request failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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
            return _scan_payload(qs, default_folder=default_folder)
        except ReadRootDenied:
            return JSONResponse(read_root_denied_body(), status_code=403)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/scan-status")
    def api_scan_status(request: Request):
        from .webapp_api import _scan_status_payload
        return _scan_status_payload(str(request.url.query))

    @app.get("/api/scan-cancel")
    def api_scan_cancel(request: Request):
        from .webapp_api import _scan_cancel_payload
        return _scan_cancel_payload(str(request.url.query))

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
            return _analyze_file_payload(str(request.url.query))
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
            raise HTTPException(status_code=413, detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
        content_type = request.headers.get("content-type", "")
        return await run_in_threadpool(_analyze_upload_payload, content_type, body)

    @app.post("/api/report")
    async def api_report(request: Request):
        from fastapi import Response
        from .webapp_api import _report_payload
        body = await request.body()
        fmt = request.query_params.get("format")
        try:
            parsed = json.loads(body.decode("utf-8") if body else "{}")
            if not fmt and isinstance(parsed, dict):
                fmt = parsed.get("format")
        except (UnicodeDecodeError, ValueError):
            # Malformed body: _report_payload below returns the JSON error.
            fmt = fmt or None
        from .webapp_api import ReadRootDenied, read_root_denied_body
        try:
            rendered = await run_in_threadpool(
                lambda: _report_payload(body, format_override=fmt, default_folder=default_folder)
            )
        except ReadRootDenied:
            return JSONResponse(read_root_denied_body(), status_code=403)
        if isinstance(rendered, dict):
            return rendered
        if (fmt or "").lower() in ("pdf", "evidence", "evidence-statement"):
            fn = "deepfake-lens-evidence-statement.pdf" if (fmt or "").lower() in ("evidence", "evidence-statement") else "deepfake-lens-forensic-report.pdf"
            return Response(
                content=rendered,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{fn}"'},
            )
        return Response(
            content=rendered,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="deepfake-lens-report.html"'},
        )

    @app.post("/api/feedback")
    async def api_feedback(request: Request):
        from .webapp_api import _feedback_payload
        body = await request.body()
        return await run_in_threadpool(_feedback_payload, body)

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
            data = json.loads(body.decode("utf-8") if body else "{}")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid json body")
        saved = store.save_review(artifact_id, data)
        return {"status": "success", "artifact_id": artifact_id, "review": saved}

    @app.get("/api/review")
    async def api_get_review(request: Request):
        from urllib.parse import parse_qs
        from .reviews import get_default_review_store
        qs = parse_qs(str(request.url.query))
        artifact_id = qs.get("path", qs.get("artifact_id", [""]))[0]
        if not artifact_id:
            raise HTTPException(status_code=400, detail="missing path or artifact_id query parameter")
        store = get_default_review_store()
        review = store.get_review(artifact_id)
        return {"status": "success", "artifact_id": artifact_id, "review": review}

    @app.post("/api/review")
    async def api_post_review(request: Request):
        from .reviews import get_default_review_store
        body = await request.body()
        try:
            data = json.loads(body.decode("utf-8") if body else "{}")
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid json body")
        artifact_id = data.get("artifact_id", data.get("path", ""))
        if not artifact_id:
            raise HTTPException(status_code=400, detail="missing artifact_id in payload")
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
        f"설치: pip install {' '.join(missing)}\n"
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
    print(f"Starting Deepfake Lens unified server on http://{host}:{port}" + (" (token required)" if token else ""))
    uvicorn.run(app, host=host, port=port)
