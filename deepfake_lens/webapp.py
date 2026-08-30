"""Web application module for Deepfake Lens GUI.

Provides a web-based GUI that works on Windows, Mac, and Linux.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from .core import scan_directory, scan_to_json, summarize
from .fusion import apply_fusion_to_items, load_fusion_profile


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def run_server(host: str, port: int, *, default_folder: Path | None = None, allow_lan: bool = False) -> None:
    """Run the web server with GUI."""
    if not allow_lan and host not in LOCAL_HOSTS:
        raise ValueError("local web app binds to localhost by default; pass --allow-lan to bind elsewhere")
    
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            
            # Serve GUI
            if parsed.path == "/" or parsed.path == "/gui":
                self._send_html(_load_gui())
                return
            
            # API endpoints
            if parsed.path == "/api/scan":
                self._send_json(_scan_payload(parsed.query, default_folder=default_folder))
                return
            if parsed.path == "/api/heatmap":
                self._send_png(_heatmap_payload(parsed.query))
                return
            if parsed.path == "/api/analyze-file":
                self._send_json(_analyze_file_payload(parsed.query))
                return
            if parsed.path == "/api/stats":
                self._send_json(_stats_payload())
                return
            
            # Default to GUI
            self._send_html(_load_gui())
        
        def log_message(self, format: str, *args) -> None:
            return
        
        def _send_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        
        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        
        def _send_png(self, payload: tuple[int, bytes, str]) -> None:
            status, body, message = payload
            self.send_response(status)
            self.send_header("Content-Type", "image/png" if status == 200 else "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if message:
                self.send_header("X-Deepfake-Lens-Error", message)
            self.end_headers()
            self.wfile.write(body)
    
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Deepfake Lens GUI: http://{host}:{port}")
    print(f"Windows에서 접속: http://localhost:{port}")
    server.serve_forever()


def _load_gui() -> str:
    """Load the GUI HTML file."""
    gui_path = Path(__file__).parent / "gui.html"
    if gui_path.exists():
        return gui_path.read_text(encoding="utf-8")
    return "<h1>GUI 파일을 찾을 수 없습니다</h1>"


def _scan_payload(query: str, *, default_folder: Path | None) -> dict[str, object]:
    """Handle scan request."""
    params = parse_qs(query)
    folder = Path(params.get("folder", [str(default_folder or ".")])[0]).expanduser()
    pixel = params.get("pixel", ["off"])[0]
    recursive = params.get("recursive", ["false"])[0].lower() in {"1", "true", "yes"}
    max_files = int(params.get("max_files", ["500"])[0])
    max_file_bytes = params.get("max_file_bytes", [None])[0]
    dedupe = params.get("dedupe", ["false"])[0].lower() in {"1", "true", "yes"}
    heatmaps = params.get("heatmaps", ["false"])[0].lower() in {"1", "true", "yes"}
    model_path = _optional_path(params.get("model_path", [""])[0])
    fusion_profile = load_fusion_profile(_optional_path(params.get("fusion_profile", [""])[0]))
    
    try:
        summary, items = scan_directory(
            folder,
            recursive=recursive,
            max_files=max_files,
            pixel_mode=pixel,
            heatmaps=heatmaps and pixel == "deep",
            max_file_bytes=int(max_file_bytes) if max_file_bytes else None,
            dedupe=dedupe,
            model_path=model_path,
        )
        if fusion_profile:
            items = apply_fusion_to_items(items, fusion_profile)
            summary = summarize(items, capped=summary.capped, cached=summary.cached)
        return scan_to_json(summary, items)
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}


def _analyze_file_payload(query: str) -> dict[str, object]:
    """Handle single file analysis request."""
    params = parse_qs(query)
    file_path = params.get("file", [""])[0]
    
    if not file_path:
        return {"error": "파일 경로가 없습니다"}
    
    try:
        from .classifier import classify_metadata
        from .c2pa import analyze_metadata_forensic
        from .pixel_analyzer import analyze_pixels
        
        path = Path(file_path)
        if not path.exists():
            return {"error": f"파일이 존재하지 않습니다: {file_path}"}
        
        # Extract metadata
        metadata = _extract_metadata(path)
        
        # Classify
        classification = classify_metadata(metadata)
        
        # Forensic analysis
        forensic = analyze_metadata_forensic(path)
        
        # Pixel analysis (if image)
        pixel_result = None
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
            try:
                pixel_result = analyze_pixels(path)
            except Exception:
                pass
        
        return {
            "file": str(path),
            "classification": classification.to_json(),
            "forensic": forensic.to_json(),
            "pixel_analysis": pixel_result.to_json() if pixel_result else None,
        }
    except Exception as exc:
        return {"error": str(exc)}


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
    """Extract metadata from file."""
    import struct
    
    metadata = {}
    try:
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
    except Exception:
        pass
    return metadata


def _optional_path(value: str) -> Path | None:
    value = value.strip()
    return Path(value).expanduser() if value else None


def _heatmap_payload(query: str) -> tuple[int, bytes, str]:
    params = parse_qs(query)
    path_value = params.get("path", [""])[0]
    root_value = params.get("root", [""])[0]
    if not path_value or not root_value:
        return 400, b"missing path or root", "missing"
    path = Path(path_value).expanduser().resolve()
    root = Path(root_value).expanduser().resolve()
    if path.suffix.lower() != ".png" or not _is_within(path, root):
        return 403, b"forbidden", "forbidden"
    try:
        data = path.read_bytes()
    except OSError:
        return 404, b"not found", "not-found"
    return 200, data, ""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
