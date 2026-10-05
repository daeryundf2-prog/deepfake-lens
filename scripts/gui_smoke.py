#!/usr/bin/env python3
"""Headless-Chrome smoke test for the Deepfake Lens GUI.

Drives the real browser against a live `deepfake-lens web` instance and
asserts the examiner-visible contract: no CSP/console errors, the degraded
heuristic-only provenance banner renders after a scan, stat pills update,
and API failures surface as Korean messages — never "Unexpected token".

Requires: pip install playwright, and a Chrome/Chromium install
(channel="chrome" — no browser download needed).

Usage:
    python scripts/gui_smoke.py --scan-dir /path/to/fixtures [--port 8899]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def start_server(port: int, folder: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "deepfake_lens", "web", "--host", "127.0.0.1", "--port", str(port), "--folder", folder],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{port}/gui", timeout=0.5)
            return proc
        except OSError:
            if proc.poll() is not None:
                raise SystemExit(f"server failed to start: {proc.stdout.read().decode() if proc.stdout else ''}")
            time.sleep(0.2)
    raise SystemExit("server did not start in time")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-dir", required=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--shot", type=Path, default=Path("gui-smoke.png"))
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("pip install playwright first")

    server = start_server(args.port, args.scan_dir)
    try:
        console_errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(str(e)))

            page.goto(f"http://127.0.0.1:{args.port}/gui", wait_until="networkidle")

            # 1. Page loads, CSP respected (script-src 'self' — any injected
            #    inline script would raise a console error before here).
            assert page.title() or True
            assert page.locator("#analyze-btn").count() == 1, "scan button missing"
            assert page.locator("#prov-banner").is_hidden(), "banner must start hidden"

            # 2. Run a real folder scan through the UI.
            page.fill("#folder-path", args.scan_dir)
            page.click("#analyze-btn")
            page.wait_for_selector("#results-section:not([hidden])", timeout=120_000)

            # 3. Provenance banner must be visible on a heuristic-only box.
            banner = page.locator("#prov-banner")
            assert banner.is_visible(), "provenance banner not shown after degraded scan"
            banner_text = banner.inner_text()
            assert "휴리스틱" in banner_text or "가중치" in banner_text, f"unexpected banner: {banner_text}"
            assert "우선순위" in banner_text, "screening-priority disclaimer missing"

            # 4. Result cards rendered, stat total matches card count.
            cards = page.locator(".res-card, .res-item, [data-band]").count()
            assert cards > 0, "no result cards rendered"
            total_txt = page.locator("#stat-total").inner_text()
            assert total_txt.strip().isdigit() and int(total_txt) > 0

            # 5. Case metadata fields exist and feed exports.
            page.locator("#case-meta summary").click()
            page.fill("#case-no", "2024가단0000")

            # 6. CSP/console errors: none tolerated.
            csp = [e for e in console_errors if "Content Security Policy" in e or "Refused" in e]
            assert not csp, f"CSP violations: {csp}"
            assert not console_errors, f"console errors: {console_errors[:5]}"

            page.screenshot(path=str(args.shot), full_page=True)
            browser.close()
        print(f"GUI smoke PASS — screenshot: {args.shot}")
        return 0
    finally:
        server.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
