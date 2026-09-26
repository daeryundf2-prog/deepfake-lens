"""Web application module for Deepfake Lens GUI.

Provides a web-based GUI that works on Windows, Mac, and Linux.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from .core import BatchScanSummary, DEFAULT_METADATA_BYTES, _scan_item_from_json, analyze_file, scan_directory, scan_to_json, summarize
from .datasets import is_negative_label, is_positive_label
from .fusion import apply_fusion_to_items, load_fusion_profile
from .reports import write_html_report


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Custom header required on every /api/* request when no token is configured.
# Browsers can only attach custom headers via a CORS preflight, which this
# server never answers — so drive-by requests from unrelated web pages are
# blocked even on a plain loopback bind (simple requests could otherwise
# trigger scans and write to the feedback log). The bundled GUI and local
# tools send it unconditionally.
CLIENT_HEADER = "X-Deepfake-Lens-Client"
CLIENT_HEADER_VALUE = "gui"
MAX_SCAN_FILES = 2000
MAX_FILE_BYTES_CEILING = 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_UPLOAD_FILES = 20
DEFAULT_PROFILE_NAMES = (
    "models/aide-runtime.json",
    "models/aasist-runtime.json",
    "models/wav2vec-deepfake-audio-runtime.json",
    "models/openai-detector-runtime.json",
)


def default_engine_profiles(root: Path | None = None) -> list[Path]:
    """Bundled default-engine profiles that exist on disk.

    Mirrors the CLI defaults (image/audio/text) so the web scan uses the
    neural adapters automatically when profiles are committed. Missing
    profiles are skipped and each adapter degrades gracefully when its
    checkpoint is absent.
    """
    base = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return [base / name for name in DEFAULT_PROFILE_NAMES if (base / name).is_file()]


def host_name(header_value: str) -> str:
    """Extract the hostname part of a Host header (handles [::1]:port)."""
    value = header_value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


# Token header names accepted by every local service. The webapp and the
# FastAPI api_server grew separate conventions (X-Deepfake-Lens-Token vs
# X-API-Token); accepting both keeps a single credential working across
# either server while clients migrate to the canonical name.
TOKEN_HEADERS = ("X-Deepfake-Lens-Token", "X-API-Token")


def api_request_allowed(headers: Any, *, token: str | None) -> bool:
    """Gate for /api/* requests.

    With a configured token any of the ``TOKEN_HEADERS`` must match
    (constant-time compare). Without one, the ``X-Deepfake-Lens-Client``
    custom header is required instead: browsers cannot send custom headers
    on cross-origin "simple" requests, so this forces a preflight the server
    never answers — blocking CSRF-style writes (``/api/feedback``) and
    drive-by scans on loopback binds.
    """
    if token:
        import secrets

        for name in TOKEN_HEADERS:
            supplied = headers.get(name) or ""
            if supplied and secrets.compare_digest(supplied, token):
                return True
        return False
    return bool((headers.get(CLIENT_HEADER) or "").strip())


def run_server(host: str = "127.0.0.1", port: int = 8765, *, default_folder: Path | None = None, allow_lan: bool = False, token: str | None = None) -> None:
    """Run the web server with GUI.

    Binds to loopback by default; any other host requires ``allow_lan=True``
    (the ``web`` CLI command maps ``--allow-lan`` to it). When ``token`` is
    set — mandatory for LAN binds — every /api/* request must send it in the
    ``X-Deepfake-Lens-Token`` header. See docs/deepfake-lens-service.md for
    the full service contract.
    """
    if not allow_lan and host not in LOCAL_HOSTS:
        raise ValueError("local web app binds to localhost by default; pass --allow-lan to bind elsewhere")
    if allow_lan and not token:
        raise ValueError("--allow-lan requires a --token; the API reads and analyzes local files on request")
    if token and not allow_lan:
        import sys

        print("note: --token set on a loopback bind; /api/* still enforces it", file=sys.stderr)

    class Handler(BaseHTTPRequestHandler):
        def _api_allowed(self) -> bool:
            if api_request_allowed(self.headers, token=token):
                return True
            message = (
                "missing or invalid X-Deepfake-Lens-Token"
                if token
                else f"missing {CLIENT_HEADER} header (cross-origin requests cannot set it)"
            )
            self.send_error(401, message)
            return False

        def _guard(self) -> bool:
            if not allow_lan:
                # DNS-rebinding guard: a remote page must not be able to reach
                # this server by pointing a hostname at 127.0.0.1.
                if host_name(self.headers.get("Host") or "") not in LOCAL_HOSTS:
                    self.send_error(403, "host not allowed")
                    return False
            return True

        def do_GET(self) -> None:
            if not self._guard():
                return
            parsed = urlparse(self.path)

            if not parsed.path.startswith("/api/"):
                # Serve GUI (static shell — carries no evidence data)
                self._send_html(_load_gui())
                return

            if not self._api_allowed():
                return

            # API endpoints
            if parsed.path == "/api/scan":
                if parse_qs(parsed.query).get("async", ["false"])[0].lower() in {"1", "true", "yes"}:
                    try:
                        self._send_json(_scan_job_start(parsed.query, default_folder=default_folder))
                    except ValueError as exc:
                        self.send_error(400, str(exc))
                    return
                try:
                    self._send_json(_scan_payload(parsed.query, default_folder=default_folder))
                except ValueError as exc:
                    self.send_error(400, str(exc))
                return
            if parsed.path == "/api/scan-status":
                self._send_json(_scan_status_payload(parsed.query))
                return
            if parsed.path == "/api/scan-cancel":
                self._send_json(_scan_cancel_payload(parsed.query))
                return
            if parsed.path == "/api/heatmap":
                self._send_png(_heatmap_payload(parsed.query))
                return
            if parsed.path == "/api/preview":
                self._send_file(_preview_payload(parsed.query))
                return
            if parsed.path == "/api/analyze-file":
                self._send_json(_analyze_file_payload(parsed.query))
                return
            if parsed.path == "/api/stats":
                self._send_json(_stats_payload())
                return
            if parsed.path == "/api/reviews":
                from .reviews import get_default_review_store
                self._send_json({"status": "success", "reviews": get_default_review_store().list_reviews()})
                return
            if parsed.path == "/api/review":
                from .reviews import get_default_review_store
                qs = parse_qs(parsed.query)
                art_id = qs.get("path", qs.get("artifact_id", [""]))[0]
                if not art_id:
                    self.send_error(400, "missing path or artifact_id")
                    return
                self._send_json({"status": "success", "artifact_id": art_id, "review": get_default_review_store().get_review(art_id)})
                return
            self.send_error(404, "not found")

        def do_POST(self) -> None:
            if not self._guard():
                return
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self.send_error(404, "not found")
                return
            if not self._api_allowed():
                return
            if parsed.path == "/api/analyze-upload":
                self._handle_analyze_upload()
                return
            if parsed.path == "/api/check":
                self._handle_check()
                return
            if parsed.path == "/api/compare":
                self._handle_compare()
                return
            if parsed.path == "/api/report":
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self.send_error(400, "invalid Content-Length")
                    return
                if length <= 0 or length > 64 * 1024 * 1024:
                    self.send_error(400, "invalid report body size")
                    return
                req_fmt = (parse_qs(parsed.query).get("format", [""])[0] or "").lower()
                rendered = _report_payload(self.rfile.read(length), format_override=req_fmt or None)
                if isinstance(rendered, dict):
                    self._send_json(rendered)
                else:
                    body = rendered
                    is_pdf = req_fmt in ("pdf", "evidence", "evidence-statement")
                    if req_fmt in ("evidence", "evidence-statement"):
                        filename = "deepfake-lens-evidence-statement.pdf"
                    elif is_pdf:
                        filename = "deepfake-lens-forensic-report.pdf"
                    else:
                        filename = "deepfake-lens-report.html"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf" if is_pdf else "text/html; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return
            if parsed.path == "/api/feedback":
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self.send_error(400, "invalid Content-Length")
                    return
                if length <= 0 or length > 1024 * 1024:
                    self.send_error(400, "invalid feedback body size")
                    return
                self._send_json(_feedback_payload(self.rfile.read(length)))
                return
            if parsed.path == "/api/review":
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self.send_error(400, "invalid Content-Length")
                    return
                if length <= 0 or length > 1024 * 1024:
                    self.send_error(400, "invalid review body size")
                    return
                try:
                    raw = json.loads(self.rfile.read(length).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.send_error(400, "invalid JSON")
                    return
                art_id = raw.get("artifact_id", raw.get("path", ""))
                if not art_id:
                    self.send_error(400, "missing artifact_id")
                    return
                from .reviews import get_default_review_store
                saved = get_default_review_store().save_review(art_id, raw)
                self._send_json({"status": "success", "artifact_id": art_id, "review": saved})
                return
            self.send_error(404, "not found")

        def _handle_analyze_upload(self) -> None:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self.send_error(400, "invalid Content-Length")
                return
            if length <= 0:
                self.send_error(400, "empty upload")
                return
            if length > MAX_UPLOAD_BYTES:
                self.send_error(413, f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
                return
            body = self.rfile.read(length)
            self._send_json(_analyze_upload_payload(self.headers.get("Content-Type") or "", body))

        def _handle_compare(self) -> None:
            """Two-file comparison: speaker or stylometry by extension pair."""
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self.send_error(400, "invalid Content-Length")
                return
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self.send_error(400, "invalid compare body size")
                return
            body = self.rfile.read(length)
            self._send_json(_compare_payload(self.headers.get("Content-Type") or "", body))

        def _handle_check(self) -> None:
            """Unified check-all: JSON {text} or a single multipart file.

            Runs every layer applicable to the input — core scan heuristics,
            the neural member ensemble, metadata/C2PA forensics, and the
            text fingerprint probes — and returns one consolidated payload.
            """
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self.send_error(400, "invalid Content-Length")
                return
            if length <= 0:
                self.send_error(400, "empty body")
                return
            if length > MAX_UPLOAD_BYTES:
                self.send_error(413, f"body exceeds {MAX_UPLOAD_BYTES} bytes")
                return
            body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type") or ""
            if "application/json" in content_type:
                try:
                    payload = json.loads(body.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    self._send_json({"error": "invalid JSON body"})
                    return
                secret = payload.get("watermark_secret")
                try:
                    gamma = float(payload.get("watermark_gamma") or 0.25)
                except (TypeError, ValueError):
                    gamma = 0.25
                self._send_json(_check_text_payload(
                    str(payload.get("text") or ""),
                    watermark_secret=str(secret) if secret else None,
                    watermark_gamma=gamma,
                ))
                return
            self._send_json(_check_file_payload(content_type, body))

        def log_message(self, format: str, *args) -> None:
            return
        
        def _send_json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
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

        def _send_file(self, payload: tuple[int, bytes, str, str]) -> None:
            status, body, message, mime = payload
            self.send_response(status)
            self.send_header("Content-Type", mime if status == 200 else "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if message:
                self.send_header("X-Deepfake-Lens-Error", message)
            self.end_headers()
            self.wfile.write(body)
    
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Deepfake Lens GUI: http://{host}:{port}", flush=True)
    print(f"Windows에서 접속: http://localhost:{port}", flush=True)
    server.serve_forever()


def _load_gui() -> str:
    """Load the GUI HTML file."""
    gui_path = Path(__file__).parent / "gui.html"
    if gui_path.exists():
        return gui_path.read_text(encoding="utf-8")
    return "<h1>GUI 파일을 찾을 수 없습니다</h1>"


def _scan_payload(query: str, *, default_folder: Path | None, should_stop: Callable[[], bool] | None = None) -> dict[str, object]:
    """Handle scan request."""
    params = parse_qs(query)
    folder = Path(params.get("folder", [str(default_folder or ".")])[0]).expanduser()
    _register_read_root(folder)
    pixel = params.get("pixel", ["off"])[0]
    recursive = params.get("recursive", ["false"])[0].lower() in {"1", "true", "yes"}
    try:
        max_files = int(params.get("max_files", ["500"])[0])
    except ValueError as exc:
        raise ValueError("max_files must be an integer") from exc
    max_files = max(1, min(max_files, MAX_SCAN_FILES))
    max_file_bytes = params.get("max_file_bytes", [None])[0]
    if max_file_bytes is not None:
        try:
            max_file_bytes = min(int(max_file_bytes), MAX_FILE_BYTES_CEILING)
        except ValueError as exc:
            raise ValueError("max_file_bytes must be an integer") from exc
    dedupe = params.get("dedupe", ["false"])[0].lower() in {"1", "true", "yes"}
    heatmaps = params.get("heatmaps", ["false"])[0].lower() in {"1", "true", "yes"}
    deep_signals = params.get("deep_signals", ["false"])[0].lower() in {"1", "true", "yes"}
    model_path_raw = params.get("model_path", [""])[0]
    no_default_engine = params.get("no_default_engine", ["false"])[0].lower() in {"1", "true", "yes"}
    if model_path_raw.strip():
        model_path = _optional_path(model_path_raw)
    elif no_default_engine:
        model_path = None
    else:
        model_path = default_engine_profiles() or None
    fusion_profile = load_fusion_profile(_optional_path(params.get("fusion_profile", [""])[0]))
    
    try:
        summary, items = scan_directory(
            folder,
            recursive=recursive,
            max_files=max_files,
            pixel_mode=pixel,
            heatmaps=heatmaps and pixel == "deep",
            max_file_bytes=max_file_bytes,
            dedupe=dedupe,
            model_path=model_path,
            deep_signals=deep_signals,
            should_stop=should_stop,
        )
        if fusion_profile:
            items = apply_fusion_to_items(items, fusion_profile)
            summary = summarize(items, capped=summary.capped, cached=summary.cached)
        return scan_to_json(summary, items)
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}


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
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        if len(_SCAN_JOBS) >= _SCAN_JOB_MAX:
            raise ValueError("too many scan jobs in flight; retry after a running job finishes")
        job_id = secrets.token_hex(8)
        cancel = threading.Event()
        _SCAN_JOBS[job_id] = {"status": "running", "created": time.time(), "cancel": cancel}

    def work() -> None:
        try:
            result = _scan_payload(query, default_folder=default_folder, should_stop=cancel.is_set)
            status = "done"
        except Exception as exc:  # noqa: BLE001 - a worker crash must not kill the job silently
            result = {"error": str(exc)}
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
        return {"error": "missing job parameter"}
    with _SCAN_JOBS_LOCK:
        _scan_job_evict(time.time())
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return {"error": "unknown or expired job"}
        payload: dict[str, object] = {"job_id": job_id, "status": entry["status"]}
        if entry["status"] != "running":
            payload["result"] = entry.get("result")
        return payload


def _scan_cancel_payload(query: str) -> dict[str, object]:
    params = parse_qs(query)
    job_id = params.get("job", [""])[0].strip()
    if not job_id:
        return {"error": "missing job parameter"}
    with _SCAN_JOBS_LOCK:
        entry = _SCAN_JOBS.get(job_id)
        if entry is None:
            return {"error": "unknown or expired job"}
        if entry["status"] != "running":
            return {"job_id": job_id, "status": entry["status"], "cancelled": False}
        cancel = entry.get("cancel")
        if isinstance(cancel, threading.Event):
            cancel.set()
        return {"job_id": job_id, "status": "cancelling", "cancelled": True}


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
        
        path = Path(file_path).expanduser()
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
        # Detail stays in `detail` so the GUI shows a clean headline instead
        # of a raw exception sentence; the type/message aid local debugging.
        return {"error": "파일 분석 중 오류가 발생했습니다", "detail": f"{type(exc).__name__}: {exc}"}


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

    metadata = {}
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
    except Exception:
        pass
    return metadata


def _optional_path(value: str) -> Path | None:
    value = value.strip()
    return Path(value).expanduser() if value else None


# Server-side read roots for /api/heatmap and /api/preview. The `root`
# parameter is kept for backward compatibility but is no longer trusted:
# a path is only served when it lives under a directory the server itself
# registered via /api/scan (or an explicit allow-root registration), so a
# caller cannot widen the read scope by passing root=C:\.
_READ_ROOTS_LOCK = threading.Lock()
_READ_ROOTS: set[Path] = set()
_READ_ROOTS_MAX = 64


def _register_read_root(folder: Path) -> None:
    resolved = folder.expanduser().resolve()
    with _READ_ROOTS_LOCK:
        if resolved in _READ_ROOTS:
            return
        if len(_READ_ROOTS) >= _READ_ROOTS_MAX:
            _READ_ROOTS.pop()
        _READ_ROOTS.add(resolved)


def _read_root_allows(path: Path, root_value: str = "") -> bool:
    """True when `path` sits under a server-registered root.

    The caller-supplied root is also checked when present so a stale or
    mismatched root argument cannot broaden access beyond the registered
    set — both conditions must hold.
    """
    with _READ_ROOTS_LOCK:
        roots = set(_READ_ROOTS)
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
        return 400, b"missing path", "missing"
    path = Path(path_value).expanduser().resolve()
    if path.suffix.lower() != ".png" or not _read_root_allows(path, root_value):
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
        return 400, b"missing path", "missing", ""
    path = Path(path_value).expanduser().resolve()
    mime = _PREVIEW_MIME.get(path.suffix.lower())
    if mime is None or not _read_root_allows(path, root_value):
        return 403, b"forbidden", "forbidden", ""
    try:
        if path.stat().st_size > MAX_PREVIEW_BYTES:
            return 413, b"too large", "too-large", ""
        data = path.read_bytes()
    except OSError:
        return 404, b"not found", "not-found", ""
    return 200, data, "", mime


def _summarize_records(items: list[dict[str, object]], source: str) -> dict[str, object]:
    analyzed = [item for item in items if not item.get("error")]
    high = sum(1 for item in analyzed if (item.get("result") or {}).get("band") == "high")
    medium = sum(1 for item in analyzed if (item.get("result") or {}).get("band") == "medium")
    low = sum(1 for item in analyzed if (item.get("result") or {}).get("band") == "low")
    return {
        "total": len(items),
        "analyzed": len(analyzed),
        "high": high,
        "medium": medium,
        "low": low,
        "unknown": len(analyzed) - high - medium - low,
        "unsupported_or_failed": len(items) - len(analyzed),
        "external_model_active": sum(
            1 for item in analyzed if (item.get("result") or {}).get("model_analysis")
        ),
        "source": source,
    }


def _archive_upload_items(filename: str, suffix: str, payload: bytes) -> list[dict[str, object]]:
    """Extract an uploaded archive to a temp dir and analyze each member.

    Members are reported as ``archive.zip::inner/path.png`` rows; the
    archive bytes and extracted tree are deleted before returning.
    """
    from .archives import extract_archive

    tmp_name = ""
    dest = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_name = tmp.name
        dest = tempfile.mkdtemp(prefix="dflens-up-")
        extraction = extract_archive(tmp_name, dest)
        items: list[dict[str, object]] = []
        for member in extraction.members:
            rel = member.relative_to(dest).as_posix()
            display = f"{filename}::{rel}"
            item = analyze_file(member, display=display, model_path=default_engine_profiles() or None)
            record = item.to_json()
            record["path"] = display
            record["name"] = display
            items.append(record)
        if extraction.warnings or extraction.skipped:
            items.append({
                "name": filename, "path": filename, "kind": "archive", "status": "expanded",
                "size_bytes": len(payload),
                "result": {
                    "score": 0, "band": "low", "band_label": "컨테이너",
                    "verdict": f"압축 해제 — {len(extraction.members)}개 분석, {extraction.skipped}개 스킵",
                    "signals": [{"title": "압축 컨테이너", "detail": f"구성 {len(extraction.members)}개", "weight": 0}],
                    "limitations": extraction.warnings,
                    "next_checks": [],
                },
            })
        if not items:
            items.append({
                "name": filename, "path": filename, "kind": "archive", "status": "failed",
                "error": "; ".join(extraction.warnings) or "해제된 파일이 없습니다",
            })
        return items
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
        if dest:
            shutil.rmtree(dest, ignore_errors=True)


def _analyze_upload_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Analyze files uploaded via multipart/form-data.

    Each part is written to a temporary file, analyzed with the default
    engine profiles, then deleted. Archives are extracted and each member
    analyzed as its own row. The server never persists uploads.
    """
    if "multipart/form-data" not in content_type:
        return {"error": "multipart/form-data upload required"}
    from .archives import is_archive

    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    items: list[dict[str, object]] = []
    for part in message.iter_parts():
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if not filename or payload is None:
            continue
        if len(items) >= MAX_UPLOAD_FILES:
            items.append({"name": filename, "error": f"파일 수 상한({MAX_UPLOAD_FILES}) 초과"})
            break
        suffix = Path(filename).suffix[:16]
        try:
            if is_archive(filename):
                items.extend(_archive_upload_items(filename, suffix, payload))
                continue
            # delete=False: Windows cannot reopen a delete=True temp file.
            tmp_name = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(payload)
                    tmp_name = tmp.name
                item = analyze_file(tmp_name, model_path=default_engine_profiles() or None)
            finally:
                if tmp_name:
                    Path(tmp_name).unlink(missing_ok=True)
            record = item.to_json()
            record["path"] = filename
            record["name"] = filename
            items.append(record)
        except Exception as exc:
            items.append({"name": filename, "error": str(exc)})
    if not items:
        return {"error": "업로드된 파일이 없습니다"}
    return {
        "schema_version": 1,
        "summary": _summarize_records(items, "upload"),
        "items": items,
    }


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
        return {"error": "분석할 텍스트가 너무 짧습니다 (8자 이상)."}
    if len(trimmed) > 256 * 1024:
        return {"error": "텍스트가 256KB를 초과합니다."}
    models_dir = Path(__file__).resolve().parent.parent / "models"
    model_path = models_dir if models_dir.is_dir() else (default_engine_profiles() or None)
    # delete=False: Windows cannot reopen a delete=True NamedTemporaryFile,
    # so the analyzers below would hit Permission denied.
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
            tmp.write(trimmed)
            tmp_name = tmp.name
        item = analyze_file(tmp_name, model_path=model_path)
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = analyze_metadata_forensic(Path(tmp_name)).to_json()
        except Exception:
            forensic = None
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
        except Exception:
            watermark = {"available": False, "verdict": "워터마크 검사 실패"}
    record = item.to_json()
    record["name"] = "pasted-text"
    record["path"] = "pasted-text"
    return {
        "schema_version": 1,
        "mode": "text",
        "item": record,
        "advanced": advanced.to_json(),
        "forensic": forensic,
        "watermark": watermark,
    }


def _check_file_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Unified single-file check: full scan + forensics + text probes."""
    if "multipart/form-data" not in content_type:
        return {"error": "multipart/form-data 또는 application/json 본문이 필요합니다"}
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    part = next(
        (p for p in message.iter_parts() if p.get_filename() and p.get_payload(decode=True)),
        None,
    )
    if part is None:
        return {"error": "업로드된 파일이 없습니다"}
    filename = part.get_filename() or "upload"
    payload = part.get_payload(decode=True) or b""
    suffix = Path(filename).suffix[:16]
    from .archives import is_archive
    if is_archive(filename):
        items = _archive_upload_items(filename, suffix, payload)
        return {
            "schema_version": 1,
            "mode": "files",
            "summary": _summarize_records(items, "upload"),
            "items": items,
        }
    models_dir = Path(__file__).resolve().parent.parent / "models"
    model_path = models_dir if models_dir.is_dir() else (default_engine_profiles() or None)
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(payload)
            tmp_name = tmp.name
        tmp_path = Path(tmp_name)
        item = analyze_file(tmp_path, model_path=model_path)
        record = item.to_json()
        forensic = None
        try:
            from .c2pa import analyze_metadata_forensic
            forensic = analyze_metadata_forensic(tmp_path).to_json()
        except Exception:
            pass
        advanced = None
        if item.kind == "text":
            try:
                from .text_advanced import analyze_text_advanced
                advanced = analyze_text_advanced(payload.decode("utf-8", errors="replace")).to_json()
            except Exception:
                pass
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    record["name"] = filename
    record["path"] = filename
    return {
        "schema_version": 1,
        "mode": "file",
        "item": record,
        "advanced": advanced,
        "forensic": forensic,
    }


def _compare_payload(content_type: str, body: bytes) -> dict[str, object]:
    """Two-file comparison: speaker distance for audio pairs, stylometry
    for text/document pairs. Both parts must carry filenames."""
    if "multipart/form-data" not in content_type:
        return {"error": "multipart/form-data 본문이 필요합니다"}
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    parts = [
        p for p in message.iter_parts() if p.get_filename() and p.get_payload(decode=True)
    ]
    if len(parts) < 2:
        return {"error": "비교할 파일 2개가 필요합니다"}
    tmp_paths: list[Path] = []
    try:
        for part in parts[:2]:
            suffix = Path(part.get_filename() or "upload").suffix[:16]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(part.get_payload(decode=True) or b"")
                tmp_paths.append(Path(tmp.name))
        from .core import compare_files
        return compare_files(tmp_paths[0], tmp_paths[1])
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
        return {"error": "invalid JSON body"}
    label = str(data.get("expected_label", "") or "").strip().lower()
    if not (is_positive_label(label) or is_negative_label(label)):
        return {"error": "expected_label must be a recognized label (e.g. synthetic, real)"}
    path = str(data.get("path") or data.get("name") or "").strip()
    if not path:
        return {"error": "path is required"}
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


def _report_payload(body: bytes, format_override: str | None = None) -> bytes | dict[str, object]:
    """Render the HTML or court-admissible forensic PDF report for web-scan results.

    Accepts the items array the GUI holds (scan/upload payload rows), rebuilds
    ScanItem objects through the same cache deserializer used on disk, and
    returns the same report the CLI's --html-out produces — so web and CLI
    artifacts are identical.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"error": "invalid JSON body"}
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return {"error": "items array is required"}
    try:
        items = [_scan_item_from_json(row) for row in raw_items if isinstance(row, dict)]
    except (TypeError, ValueError) as exc:
        return {"error": f"malformed item: {exc}"}
    if not items:
        return {"error": "items array is required"}
    analyzed = [item for item in items if item.result is not None]
    bands = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for item in analyzed:
        band = item.result.band.value if item.result else "unknown"
        bands[band if band in bands else "unknown"] += 1
    summary = BatchScanSummary(
        total=len(items),
        analyzed=len(analyzed),
        high=bands["high"],
        medium=bands["medium"],
        low=bands["low"],
        unknown=bands["unknown"],
        unsupported_or_failed=len(items) - len(analyzed),
        capped=False,
        external_model_active=sum(
            1 for item in analyzed if item.result and item.result.model_analysis
        ),
    )
    req_format = (format_override or data.get("format") or "html").lower()
    suffix = ".pdf" if req_format in ("pdf", "evidence", "evidence-statement") else ".html"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if req_format in ("evidence", "evidence-statement"):
            from .evidence_statement import build_evidence_statement, write_evidence_statement_pdf
            case_no = str(data.get("case_no") or "(사건번호 입력)")
            stmt = build_evidence_statement(items, case_no=case_no)
            write_evidence_statement_pdf(tmp_path, stmt)
        elif req_format == "pdf":
            from .reports import write_forensic_pdf_report
            exhibit_no = str(data.get("exhibit_no") or "갑 제        호증")
            write_forensic_pdf_report(tmp_path, summary, items, exhibit_no=exhibit_no)
        else:
            write_html_report(tmp_path, summary, items)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
