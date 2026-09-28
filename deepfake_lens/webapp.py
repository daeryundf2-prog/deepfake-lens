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
from .webapp_api import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    _analyze_file_payload,
    _analyze_upload_payload,
    _check_file_payload,
    _check_text_payload,
    _compare_payload,
    _feedback_payload,
    _heatmap_payload,
    _load_gui,
    _preview_payload,
    _report_payload,
    _scan_cancel_payload,
    _scan_job_start,
    _scan_payload,
    _scan_status_payload,
    _stats_payload,
)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Custom header required on every /api/* request when no token is configured.
# Browsers can only attach custom headers via a CORS preflight, which this
# server never answers — so drive-by requests from unrelated web pages are
# blocked even on a plain loopback bind (simple requests could otherwise
# trigger scans and write to the feedback log). The bundled GUI and local
# tools send it unconditionally.
CLIENT_HEADER = "X-Deepfake-Lens-Client"
CLIENT_HEADER_VALUE = "gui"

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
                # Serve GUI shell + extracted static assets (carry no evidence data)
                if parsed.path == "/gui.css":
                    self._send_static("gui.css", "text/css; charset=utf-8")
                    return
                if parsed.path == "/gui.js":
                    self._send_static("gui.js", "text/javascript; charset=utf-8")
                    return
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
            # GUI is fully externalized (gui.css/gui.js) and markup carries
            # no inline style attributes — script-src and style-src are both
            # strict 'self'. blob: covers object-URL previews and heatmaps.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, name: str, content_type: str) -> None:
            """Serve a bundled GUI asset (gui.css/gui.js) — package-internal only."""
            path = Path(__file__).parent / name
            if not path.exists():
                self.send_error(404, "not found")
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
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


