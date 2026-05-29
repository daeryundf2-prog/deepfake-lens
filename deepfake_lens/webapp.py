from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core import scan_directory, scan_to_json, summarize
from .fusion import apply_fusion_to_items, load_fusion_profile


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def run_server(host: str, port: int, *, default_folder: Path | None = None, allow_lan: bool = False) -> None:
    if not allow_lan and host not in LOCAL_HOSTS:
        raise ValueError("local web app binds to localhost by default; pass --allow-lan to bind elsewhere")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path == "/api/scan":
                self._send_json(_scan_payload(parsed.query, default_folder=default_folder))
                return
            if parsed.path == "/api/heatmap":
                self._send_png(_heatmap_payload(parsed.query))
                return
            self._send_html(_INDEX_HTML)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - BaseHTTPRequestHandler API
            return

        def _send_json(self, payload: dict[str, object]) -> None:
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

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Deepfake Lens local web app: http://{host}:{port}")
    server.serve_forever()


def _scan_payload(query: str, *, default_folder: Path | None) -> dict[str, object]:
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


_INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Deepfake Lens Local</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; color: #1f2933; }
    input, select, button { font: inherit; padding: 6px 8px; }
    form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .wide { min-width: 260px; }
    table { border-collapse: collapse; width: 100%; margin-top: 16px; }
    th, td { border-bottom: 1px solid #d8dee9; padding: 8px; text-align: left; }
    img { width: 96px; height: 96px; object-fit: cover; image-rendering: pixelated; }
  </style>
</head>
<body>
  <form id="scan-form">
    <input class="wide" name="folder" size="48" placeholder="/path/to/folder">
    <select name="pixel"><option>off</option><option>fast</option><option>deep</option></select>
    <input name="max_files" type="number" min="1" value="500">
    <input name="max_file_bytes" type="number" min="1" placeholder="max bytes">
    <input class="wide" name="model_path" placeholder="model profile">
    <input class="wide" name="fusion_profile" placeholder="fusion profile">
    <label><input name="recursive" value="true" type="checkbox"> recursive</label>
    <label><input name="dedupe" value="true" type="checkbox"> dedupe</label>
    <label><input name="heatmaps" value="true" type="checkbox"> heatmaps</label>
    <button>Scan</button>
  </form>
  <table><thead><tr><th>risk</th><th>score</th><th>pixel</th><th>source</th><th>file</th><th>heatmap</th></tr></thead><tbody id="rows"></tbody></table>
  <script>
    document.querySelector('#scan-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const params = new URLSearchParams(new FormData(event.target));
      const response = await fetch('/api/scan?' + params.toString());
      const data = await response.json();
      const rows = document.querySelector('#rows');
      const root = params.get('folder') || '';
      rows.innerHTML = '';
      for (const item of data.items || []) {
        const result = item.result || {};
        const pixelInfo = result.pixel_analysis && result.pixel_analysis.available ? result.pixel_analysis : null;
        const pixel = pixelInfo ? pixelInfo.score : '-';
        const heatmap = pixelInfo && pixelInfo.heatmap_path ? `<img alt="heatmap" src="/api/heatmap?root=${encodeURIComponent(root)}&path=${encodeURIComponent(pixelInfo.heatmap_path)}">` : '';
        rows.insertAdjacentHTML('beforeend', `<tr><td>${esc(result.band_label || item.status)}</td><td>${esc(result.score ?? '-')}</td><td>${esc(pixel)}</td><td>${esc(result.source_guess?.label || '-')}</td><td>${esc(item.path)}</td><td>${heatmap}</td></tr>`);
      }
    });
    function esc(value) {
      return String(value).replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[ch]));
    }
  </script>
</body>
</html>
"""
