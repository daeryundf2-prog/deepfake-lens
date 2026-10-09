"""Web application module for Deepfake Lens GUI.

Provides a web-based GUI that works on Windows, Mac, and Linux.
"""

from __future__ import annotations

import json
import logging
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
    ReadRootDenied,
    configure_read_roots,
    read_root_denied_body,
    report_error_status,
    _scan_cancel_payload,
    _scan_job_start,
    _scan_payload,
    _scan_status_payload,
    _stats_payload,
)

logger = logging.getLogger(__name__)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Custom header required on every /api/* request when no token is configured.
# Browsers can only attach custom headers via a CORS preflight, which this
# server never answers — so drive-by requests from unrelated web pages are
# blocked even on a plain loopback bind (simple requests could otherwise
# trigger scans and write to the feedback log). The bundled GUI and local
# tools send it unconditionally.
CLIENT_HEADER = "X-Deepfake-Lens-Client"
CLIENT_HEADER_VALUE = "gui"

# Content-Security-Policy of the GUI shell, shared by this server and the
# FastAPI /gui route (api_server). The GUI is fully externalized
# (gui.css/gui.js) and its markup carries no inline style attributes —
# script-src and style-src are both strict 'self'. blob: covers the
# object-URL previews: img-src for images and heatmaps, media-src for the
# <audio>/<video> previews of wav/mp3/mp4 files (S5 — without it the
# browser refuses to play them); 'self' keeps same-origin media working.
GUI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'"
)


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


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    default_folder: Path | None = None,
    allow_lan: bool = False,
    token: str | None = None,
    models_dir: Path | None = None,
    allow_roots: list[Path] | None = None,
) -> None:
    """Run the web server with GUI.

    Binds to loopback by default; any other host requires ``allow_lan=True``
    (the ``web`` CLI command maps ``--allow-lan`` to it). When ``token`` is
    set — mandatory for LAN binds — every /api/* request must send it in the
    ``X-Deepfake-Lens-Token`` header. See docs/deepfake-lens-service.md for
    the full service contract.

    Read roots (G31): ``default_folder`` (``--folder``) and every
    ``allow_roots`` entry (``--allow-root``) are the only directories the
    API reads from; requests for any other path get 403.
    """
    server = build_server(
        host, port, default_folder=default_folder, allow_lan=allow_lan,
        token=token, models_dir=models_dir, allow_roots=allow_roots,
    )
    print(f"Deepfake Lens GUI: http://{host}:{server.server_address[1]}", flush=True)
    print(f"Windows에서 접속: http://localhost:{server.server_address[1]}", flush=True)
    server.serve_forever()


def build_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    default_folder: Path | None = None,
    allow_lan: bool = False,
    token: str | None = None,
    models_dir: Path | None = None,
    allow_roots: list[Path] | None = None,
) -> ThreadingHTTPServer:
    """Configure and bind the server without serving (``run_server`` serves it).

    Tests drive the returned server with ``serve_forever``/``shutdown``.
    """
    if models_dir is not None:
        from .webapp_api import set_models_dir
        set_models_dir(models_dir)
    if not allow_lan and host not in LOCAL_HOSTS:
        raise ValueError("로컬 웹 앱은 기본적으로 localhost에만 바인딩합니다 — 다른 주소에는 --allow-lan을 지정하십시오")
    if allow_lan and not token:
        raise ValueError("--allow-lan에는 --token이 필요합니다 — API는 요청에 따라 로컬 파일을 읽고 분석합니다")
    if token and not allow_lan:
        import sys

        print("참고: 루프백 바인드에 --token이 지정되었습니다. /api/*는 토큰을 계속 요구합니다.", file=sys.stderr)
    # The only place read roots are registered (G31): operator setup.
    configure_read_roots(default_folder, allow_roots)

    class Handler(BaseHTTPRequestHandler):
        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            """Error responses as JSON ``{"error": <Korean message>}`` (D16).

            The stock implementation puts ``message`` in the HTTP status
            line, which is latin-1 only — a Korean message would raise. The
            status line keeps the standard reason phrase; the examiner-facing
            text travels in the UTF-8 body, where gui.js apiError reads it.
            """
            del explain
            self.close_connection = True
            text = message or self.responses.get(code, ("", ""))[0] or "요청을 처리할 수 없습니다"
            try:
                self._send_json({"error": text}, status=code)
            except OSError:
                # Client already gone; nothing left to tell it.
                logger.debug("error response not delivered (%s)", code)

        def _api_allowed(self) -> bool:
            if api_request_allowed(self.headers, token=token):
                return True
            message = (
                "X-Deepfake-Lens-Token이 없거나 올바르지 않습니다"
                if token
                else f"{CLIENT_HEADER} 헤더가 없습니다 (교차 출처 요청은 이 헤더를 붙일 수 없습니다)"
            )
            self.send_error(401, message)
            return False

        def _guard(self) -> bool:
            if not allow_lan:
                # DNS-rebinding guard: a remote page must not be able to reach
                # this server by pointing a hostname at 127.0.0.1.
                if host_name(self.headers.get("Host") or "") not in LOCAL_HOSTS:
                    self.send_error(403, "허용되지 않은 호스트입니다")  # N7
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

            try:
                self._route_get(parsed)
            except ReadRootDenied:
                self._send_json(read_root_denied_body(), status=403)

        def _route_get(self, parsed: Any) -> None:
            # API endpoints
            if parsed.path == "/api/scan":
                # Invalid options (analysis_api.InvalidOption) and a full job
                # registry are JSON 400s — send_error's status line is
                # latin-1 only and the message may echo a non-ASCII name.
                if parse_qs(parsed.query).get("async", ["false"])[0].lower() in {"1", "true", "yes"}:
                    try:
                        self._send_json(_scan_job_start(parsed.query, default_folder=default_folder))
                    except ValueError as exc:
                        self._send_json({"error": str(exc)}, status=400)
                    return
                try:
                    self._send_json(_scan_payload(parsed.query, default_folder=default_folder))
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
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
                    self.send_error(400, "path 또는 artifact_id가 필요합니다")
                    return
                self._send_json({"status": "success", "artifact_id": art_id, "review": get_default_review_store().get_review(art_id)})
                return
            self.send_error(404, "찾을 수 없는 경로입니다")

        def do_POST(self) -> None:
            if not self._guard():
                return
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self.send_error(404, "찾을 수 없는 경로입니다")
                return
            if not self._api_allowed():
                return
            try:
                self._route_post(parsed)
            except ReadRootDenied:
                self._send_json(read_root_denied_body(), status=403)

        def _route_post(self, parsed: Any) -> None:
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
                    self.send_error(400, "Content-Length가 올바르지 않습니다")
                    return
                if length <= 0 or length > 64 * 1024 * 1024:
                    self.send_error(400, "보고서 요청 본문 크기가 올바르지 않습니다")
                    return
                req_fmt = (parse_qs(parsed.query).get("format", [""])[0] or "").lower()
                rendered = _report_payload(self.rfile.read(length), format_override=req_fmt or None, default_folder=default_folder)
                if isinstance(rendered, dict):
                    self._send_json(rendered, status=report_error_status(rendered))
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
                    self.send_error(400, "Content-Length가 올바르지 않습니다")
                    return
                if length <= 0 or length > 1024 * 1024:
                    self.send_error(400, "피드백 요청 본문 크기가 올바르지 않습니다")
                    return
                self._send_json(_feedback_payload(self.rfile.read(length)))
                return
            if parsed.path == "/api/review":
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self.send_error(400, "Content-Length가 올바르지 않습니다")
                    return
                if length <= 0 or length > 1024 * 1024:
                    self.send_error(400, "검토 요청 본문 크기가 올바르지 않습니다")
                    return
                try:
                    raw = json.loads(self.rfile.read(length).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.send_error(400, "JSON을 해석할 수 없습니다")
                    return
                art_id = raw.get("artifact_id", raw.get("path", ""))
                if not art_id:
                    self.send_error(400, "artifact_id가 필요합니다")
                    return
                from .reviews import get_default_review_store
                saved = get_default_review_store().save_review(art_id, raw)
                self._send_json({"status": "success", "artifact_id": art_id, "review": saved})
                return
            self.send_error(404, "찾을 수 없는 경로입니다")

        def _handle_analyze_upload(self) -> None:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self.send_error(400, "Content-Length가 올바르지 않습니다")
                return
            if length <= 0:
                self.send_error(400, "업로드된 내용이 없습니다")
                return
            if length > MAX_UPLOAD_BYTES:
                self.send_error(413, f"업로드 크기가 상한({MAX_UPLOAD_BYTES}바이트)을 초과합니다")
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_error(400, "요청 본문이 불완전합니다 — 업로드 중 연결이 끊겼습니다")
                return
            try:
                self._send_json(_analyze_upload_payload(self.headers.get("Content-Type") or "", body))
            except Exception as exc:
                self._send_json({"error": "업로드 분석 중 오류가 발생했습니다", "detail": f"{type(exc).__name__}: {exc}"})

        def _handle_compare(self) -> None:
            """Two-file comparison: speaker or stylometry by extension pair."""
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self.send_error(400, "Content-Length가 올바르지 않습니다")
                return
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self.send_error(400, "비교 요청 본문 크기가 올바르지 않습니다")
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_error(400, "요청 본문이 불완전합니다 — 업로드 중 연결이 끊겼습니다")
                return
            try:
                self._send_json(_compare_payload(self.headers.get("Content-Type") or "", body))
            except Exception as exc:
                self._send_json({"error": "비교 분석 중 오류가 발생했습니다", "detail": f"{type(exc).__name__}: {exc}"})

        def _handle_check(self) -> None:
            """Unified check-all: JSON {text} or a single multipart file.

            Runs every layer applicable to the input — core scan heuristics,
            the neural member ensemble, metadata/C2PA forensics, and the
            text fingerprint probes — and returns one consolidated payload.
            """
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self.send_error(400, "Content-Length가 올바르지 않습니다")
                return
            if length <= 0:
                self.send_error(400, "요청 본문이 비어 있습니다")
                return
            if length > MAX_UPLOAD_BYTES:
                self.send_error(413, f"요청 본문이 상한({MAX_UPLOAD_BYTES}바이트)을 초과합니다")
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self.send_error(400, "요청 본문이 불완전합니다 — 업로드 중 연결이 끊겼습니다")
                return
            content_type = self.headers.get("Content-Type") or ""
            if "application/json" in content_type:
                try:
                    payload = json.loads(body.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    self._send_json({"error": "JSON 본문을 해석할 수 없습니다"})
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
        
        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        
        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", GUI_CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, name: str, content_type: str) -> None:
            """Serve a bundled GUI asset (gui.css/gui.js) — package-internal only."""
            path = Path(__file__).parent / name
            if not path.exists():
                self.send_error(404, "찾을 수 없는 경로입니다")
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
    
    return ThreadingHTTPServer((host, port), Handler)


