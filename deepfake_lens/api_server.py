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
                if request.headers.get("x-api-token") != token:
                    return JSONResponse({"status": "error", "message": "unauthorized"}, status_code=401)
            elif host_name(request.headers.get("host", "")) not in allowed_hosts:
                return JSONResponse({"status": "error", "message": "host not allowed"}, status_code=403)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Token"],
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
            result = analyze_audio(file_path)
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
