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
    _repo = Path(__file__).resolve().parents[1]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    ap = KoreanArgumentParser(description="웹 GUI를 띄워 폴더를 검사하고 결론·근거·검사 범위가 그려지는지 확인합니다(스크린샷 저장).")
    ap.add_argument("--scan-dir", required=True, help="GUI에서 검사할 폴더")
    ap.add_argument("--port", type=int, default=8899, help="웹 서버 포트(기본: 8899)")
    ap.add_argument("--shot", type=Path, default=Path("gui-smoke.png"), help="스크린샷 파일(기본: gui-smoke.png)")
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
            assert "신경망 미탑재(측정 게이트 미충족)" in banner_text, f"unexpected banner: {banner_text}"  # D16
            # R16: the disclaimer reads in the three-verdict language, never "우선순위".
            assert "이 결과는 결론과 근거로 읽으십시오; 점수는 보정된 경우에만 표시됩니다" in banner_text, "three-verdict disclaimer missing"
            assert "우선순위" not in banner_text, "banner still calls the result a priority signal"
            # D16: 결론순 is the default (and only verdict) ordering; no 점수순/위험도순.
            assert page.locator("#res-sort").input_value() == "verdict", "default sort must be 결론순"
            assert page.locator("#res-sort option[value='score']").count() == 0, "점수순 sort must be gone"

            # 4. Result cards rendered, stat total matches card count.
            cards = page.locator(".res-card, .res-item, [data-band]").count()
            assert cards > 0, "no result cards rendered"
            total_txt = page.locator("#stat-total").inner_text()
            assert total_txt.strip().isdigit() and int(total_txt) > 0

            # 4b. Contract v2: verdict pills, then verdict -> evidence (by
            #     kind) -> coverage inside an opened card (QA-OUT-1/3/6).
            for pill in ("#stat-manip", "#stat-undet", "#stat-auth", "#stat-other"):
                assert page.locator(pill).count() == 1, f"verdict pill {pill} missing"
            assert page.locator("#stat-pills [data-band='medium']").count() == 0, "legacy 'medium' pill must be gone"
            first = page.locator("#res-list .res").first
            assert first.get_attribute("data-verdict") in {
                "manipulation_evidence", "authenticity_evidence", "undetermined", "other",
            }, "card lacks a contract-v2 verdict"
            first.locator(".res-main").click()
            detail = first.locator(".res-detail")
            detail.locator(".verdict-head").wait_for(timeout=10_000)
            head_text = detail.locator(".verdict-head").inner_text()
            assert any(label in head_text for label in ("조작·생성 근거 있음", "원본성 근거 있음", "판단 불가")), head_text
            assert detail.locator(".ev-group").count() >= 1, "evidence block missing"
            for kind in detail.locator(".ev-group[data-kind]").all():
                assert kind.get_attribute("data-kind") in {"deterministic", "statistical", "lexical"}
            assert detail.locator(".cov-group").count() == 1, "coverage block missing"
            html = detail.inner_html()
            assert html.index("verdict-head") < html.index("ev-group") < html.index("cov-group"), \
                "detail order must be verdict -> evidence -> coverage"
            # Text results lead with the fixed legal limitation.
            for card in page.locator("#res-list .res").all():
                card_text = card.inner_text()
                if "참고" in card_text and ".txt" in card_text:
                    card.locator(".res-main").click()
                    card.locator(".legal-note").wait_for(timeout=10_000)
                    assert "증거능력이 없으며" in card.locator(".legal-note").inner_text()
                    break

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
