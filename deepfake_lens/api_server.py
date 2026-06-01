"""REST API server module for Deepfake Lens.

Provides HTTP API endpoints for external system integration.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class APIResponse:
    status: int
    data: dict[str, Any]
    message: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def create_app(host: str = "127.0.0.1", port: int = 8765) -> Any:
    """Create a FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError:
        raise ImportError("FastAPI is required. Install with: pip install fastapi uvicorn")
    
    app = FastAPI(title="Deepfake Lens API", version="0.1.0")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
        from .classifier import classify_metadata
        try:
            metadata = {}  # Would extract from file in production
            result = classify_metadata(metadata)
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
    
    @app.get("/api/scout")
    async def scout(sources: str = "github,arxiv,huggingface"):
        from .model_scout import scan_for_new_models, compare_with_known, generate_scout_report
        try:
            source_list = [s.strip() for s in sources.split(",")]
            discovered = scan_for_new_models(source_list)
            diff = compare_with_known(discovered)
            report = generate_scout_report(diff)
            return {"status": "success", "data": report.to_json()}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    
    return app


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the API server."""
    try:
        import uvicorn
    except ImportError:
        raise ImportError("uvicorn is required. Install with: pip install uvicorn")
    
    app = create_app(host, port)
    print(f"Starting Deepfake Lens API server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
