"""REST API server module for Deepfake Lens.

Provides HTTP API endpoints for external system integration.

The API reads local files on request, so it must never be exposed without a
token: ``run_server(..., token=...)`` requires an ``X-API-Token`` header on
every /api/ route, and the CLI refuses non-localhost binds without one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Custom header required on non-GET /api/* requests when no token is set.
# Browsers can only attach custom headers via a CORS preflight, and the CORS
# policy below only allows loopback origins — so drive-by requests from
# unrelated web pages cannot reach the write endpoints on a loopback bind.
CLIENT_HEADER = "X-Deepfake-Lens-Client"


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
    """Bundled profile directory — every committed runtime profile.

    ``analyze_external_model`` accepts a directory of ``*.json`` profiles and
    filters by modality, so passing the models dir applies every engine that
    fits the input and degrades gracefully on missing checkpoints.
    """
    models_dir = Path(__file__).resolve().parent.parent / "models"
    return models_dir if models_dir.is_dir() else None


def create_app(host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> Any:
    """Create a FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse
    except ImportError:
        raise ImportError("FastAPI is required. Install with: pip install fastapi uvicorn")

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
            elif request.method != "GET" and not (request.headers.get(CLIENT_HEADER) or "").strip():
                return JSONResponse(
                    {"status": "error", "message": f"missing {CLIENT_HEADER} header"},
                    status_code=401,
                )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Token", "X-Deepfake-Lens-Token", CLIENT_HEADER],
    )
    
    @app.get("/")
    async def root():
        return {"message": "Deepfake Lens API", "version": "0.1.0"}
    
    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}
    
    @app.post("/api/analyze/image")
    async def analyze_image(file_path: str):
        from .core import analyze_file
        try:
            result = analyze_file(file_path, pixel_mode="off")
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    
    @app.post("/api/analyze/audio")
    async def analyze_audio(file_path: str):
        from .audio import analyze_audio
        try:
            # Bundled audio profiles degrade gracefully when checkpoints
            # or the optional torch stack is absent.
            models_dir = Path(__file__).resolve().parent.parent / "models"
            profiles = [p for name in ("aasist-runtime.json", "wav2vec-deepfake-audio-runtime.json") if (p := models_dir / name).is_file()]
            result = analyze_audio(file_path, model_path=profiles or None)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    
    @app.post("/api/analyze/face")
    async def analyze_face(file_path: str):
        from .face import analyze_faces
        try:
            result = analyze_faces(file_path)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    
    @app.post("/api/analyze/text")
    async def analyze_text(text: str):
        from .text_advanced import analyze_text_advanced
        try:
            result = analyze_text_advanced(text)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    
    @app.post("/api/analyze/forensic")
    async def analyze_forensic(file_path: str):
        from .c2pa import analyze_metadata_forensic
        try:
            result = analyze_metadata_forensic(file_path)
            return {"status": "success", "data": result.to_json()}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    
    @app.post("/api/classify")
    async def classify(file_path: str):
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
            raise HTTPException(status_code=500, detail=str(exc))
    
    @app.post("/api/check")
    async def check(
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

        from .core import analyze_file
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
                    item = analyze_file(tmp_name, model_path=_default_profiles())
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
                    except Exception:
                        data["watermark"] = {"available": False, "verdict": "워터마크 검사 실패"}
                return {"status": "success", "data": data}
            if file_path:
                path = Path(file_path)
                item = analyze_file(path, model_path=_default_profiles())
                data: dict[str, Any] = {"mode": "file", "item": item.to_json()}
                try:
                    from .c2pa import analyze_metadata_forensic
                    data["forensic"] = analyze_metadata_forensic(path).to_json()
                except Exception:
                    data["forensic"] = None
                if item.kind == "text":
                    try:
                        from .text_advanced import analyze_text_advanced
                        data["advanced"] = analyze_text_advanced(
                            path.read_text(encoding="utf-8", errors="replace")[: 256 * 1024]
                        ).to_json()
                    except Exception:
                        data["advanced"] = None
                return {"status": "success", "data": data}
            raise HTTPException(status_code=400, detail="file_path or text required")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # --- Streaming job API -------------------------------------------------
    # /api/check/stream runs the same layered check as /api/check but reports
    # per-stage progress over SSE. Jobs register in _JOBS so a client can
    # cancel between stages via /api/jobs/{id}/cancel — cancellation is
    # cooperative and takes effect at stage boundaries, not mid-analysis.
    _JOBS: dict[str, dict[str, Any]] = {}

    @app.post("/api/check/stream")
    async def check_stream(
        file_path: str | None = None,
        text: str | None = None,
        watermark_secret: str | None = None,
        watermark_gamma: float = 0.25,
    ):
        import asyncio
        import json as _json
        import threading
        import uuid

        from fastapi.responses import StreamingResponse

        job_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        _JOBS[job_id] = {"cancel": cancel, "done": False}

        def run_layered() -> Any:
            """Run the check stages, aborting between stages if cancelled."""
            import tempfile

            from .core import analyze_file

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
                    item = analyze_file(tmp_name, model_path=_default_profiles())
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
                    except Exception:
                        wm = {"available": False, "verdict": "워터마크 검사 실패"}
                    stages.append(("watermark", wm))
                payload = {"mode": "text", **dict(stages)}
            else:
                if not file_path:
                    yield ("error", {"detail": "file_path or text required"})
                    return
                path = Path(file_path)
                yield ("progress", {"stage": "core", "index": 1, "total": 2})
                item = analyze_file(path, model_path=_default_profiles())
                if cancel.is_set():
                    yield ("cancelled", {"job_id": job_id})
                    return
                stages.append(("item", item.to_json()))

                yield ("progress", {"stage": "forensic", "index": 2, "total": 2})
                try:
                    from .c2pa import analyze_metadata_forensic
                    forensic: Any = analyze_metadata_forensic(path).to_json()
                except Exception:
                    forensic = None
                stages.append(("forensic", forensic))
                if item.kind == "text":
                    try:
                        from .text_advanced import analyze_text_advanced
                        stages.append(("advanced", analyze_text_advanced(
                            path.read_text(encoding="utf-8", errors="replace")[: 256 * 1024]
                        ).to_json()))
                    except Exception:
                        stages.append(("advanced", None))
                payload = {"mode": "file", **dict(stages)}

            yield ("result", payload)

        async def events():
            loop = asyncio.get_event_loop()
            queue: asyncio.Queue[Any] = asyncio.Queue()

            def produce() -> None:
                try:
                    for evt in run_layered():
                        loop.call_soon_threadsafe(queue.put_nowait, evt)
                except Exception as exc:  # noqa: BLE001 - report, don't hang
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("error", {"detail": str(exc)}))
                finally:
                    _JOBS[job_id]["done"] = True
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            threading.Thread(target=produce, daemon=True).start()
            try:
                while True:
                    evt = await queue.get()
                    if evt is None:
                        break
                    name, data = evt
                    yield f"event: {name}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"
            finally:
                _JOBS.pop(job_id, None)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown or finished job")
        job["cancel"].set()
        return {"status": "success", "job_id": job_id, "cancelled": True}

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown or finished job")
        return {
            "status": "success",
            "job_id": job_id,
            "done": bool(job["done"]),
            "cancelled": bool(job["cancel"].is_set()),
        }

    # /api/scan/stream scans a server-local directory with per-file
    # progress events over SSE. Shares the _JOBS registry so clients can
    # cancel between files via /api/jobs/{id}/cancel. Directory reads are
    # limited to max_files entries; same trust level as /api/check.
    @app.post("/api/scan/stream")
    async def scan_stream(
        directory: str,
        recursive: bool = False,
        max_files: int = 200,
    ):
        import asyncio
        import json as _json
        import threading
        import uuid

        from fastapi.responses import StreamingResponse

        job_id = uuid.uuid4().hex[:12]
        cancel = threading.Event()
        _JOBS[job_id] = {"cancel": cancel, "done": False}

        def run_scan():
            from .core import _iter_files, analyze_file

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
                yield ("error", {"detail": f"listing failed: {exc}"})
                return
            total = len(paths)
            yield ("progress", {"stage": "enumerate", "total": total, "capped": capped})
            items: list[dict[str, Any]] = []
            counts = {"high": 0, "medium": 0, "unknown": 0, "low": 0, "failed": 0}
            for index, path in enumerate(paths, 1):
                if cancel.is_set():
                    yield ("cancelled", {"job_id": job_id, "processed": index - 1, "total": total})
                    return
                try:
                    item = analyze_file(path, model_path=_default_profiles())
                    data = item.to_json()
                    result = data.get("result") or {}
                    band = str(result.get("band") or "unknown")
                    status = str(data.get("status") or "failed")
                    if status == "analyzed":
                        counts[band if band in counts else "unknown"] += 1
                    elif status in {"skipped", "duplicate"}:
                        counts["unknown"] += 1
                    else:
                        counts["failed"] += 1
                    items.append({"path": data.get("path"), "kind": data.get("kind"),
                                  "status": status, "band": band if status == "analyzed" else None,
                                  "score": result.get("score")})
                except Exception as exc:  # noqa: BLE001 - per-file failure is data
                    counts["failed"] += 1
                    items.append({"path": str(path), "status": "failed", "error": str(exc)})
                yield ("progress", {"stage": "scan", "index": index, "total": total,
                                    "path": path.name, "band": items[-1].get("band")})
            yield ("result", {"mode": "scan", "directory": str(root), "total": total,
                              "capped": capped, "counts": counts, "items": items})

        async def events():
            loop = asyncio.get_event_loop()
            queue: asyncio.Queue[Any] = asyncio.Queue()

            def produce() -> None:
                try:
                    for evt in run_scan():
                        loop.call_soon_threadsafe(queue.put_nowait, evt)
                except Exception as exc:  # noqa: BLE001 - report, don't hang
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("error", {"detail": str(exc)}))
                finally:
                    _JOBS[job_id]["done"] = True
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            threading.Thread(target=produce, daemon=True).start()
            try:
                while True:
                    evt = await queue.get()
                    if evt is None:
                        break
                    name, data = evt
                    yield f"event: {name}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"
            finally:
                _JOBS.pop(job_id, None)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/compare")
    async def compare(file_path_a: str, file_path_b: str):
        """Two-file comparison: same-speaker distance for audio pairs,
        same-author stylometry for text/document pairs."""
        from .core import compare_files

        try:
            result = compare_files(Path(file_path_a), Path(file_path_b))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        if "error" in result:
            raise HTTPException(status_code=400, detail=str(result["error"]))
        return {"status": "success", "data": result}

    @app.post("/api/multimodal")
    async def multimodal(
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
            raise HTTPException(status_code=500, detail=str(exc))
    
    return app


def run_server(host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> None:
    """Run the API server.

    ``token`` enables X-API-Token authentication and is mandatory for
    non-localhost binds (enforced by the ``api-serve`` CLI command).
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError("uvicorn is required. Install with: pip install uvicorn")

    app = create_app(host, port, token=token)
    print(f"Starting Deepfake Lens API server on http://{host}:{port}" + (" (token required)" if token else ""))
    uvicorn.run(app, host=host, port=port)
