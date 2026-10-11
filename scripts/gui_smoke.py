#!/usr/bin/env python3
"""Headless-Chrome smoke test for the Deepfake Lens GUI.

Drives the real browser against a live `deepfake-lens web` instance and
asserts the examiner-visible contract: no CSP/console errors, the folder
box is prefilled and Enter starts the scan, focus lands on the conclusion
headline, the verdict strip and ledger rows agree, the evidence panel keeps
verdict -> evidence -> coverage, the ledger keys stay inside the list, the
evidence statement waits for the case number and name, the page reflows at
375px and nothing is requested outside the local server.

Requires: pip install playwright, and a Chrome/Chromium install
(channel="chrome" — no browser download needed).

Usage:
    python scripts/gui_smoke.py --scan-dir /path/to/fixtures [--port 8899]
"""

from __future__ import annotations

import subprocess
import sys
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
            # The browser's own /favicon.ico probe is a 404 by design (R11-12: only the
            # GUI paths are served) — not an error of the page.
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" and not str((m.location or {}).get("url", "")).endswith("/favicon.ico") else None)
            page.on("pageerror", lambda e: console_errors.append(str(e)))

            requests: list[str] = []
            page.on("request", lambda r: requests.append(r.url))
            failed_responses: list[str] = []
            page.on("response", lambda r: failed_responses.append(f"{r.status} {r.url}") if r.status >= 400 else None)

            page.goto(f"http://127.0.0.1:{args.port}/gui", wait_until="networkidle")

            # 1. Page loads, CSP respected (script-src 'self' — any injected
            #    inline script would raise a console error before here).
            #    GUI redesign: the scan screen is first; the folder box is
            #    prefilled from /api/stats default_folder (U-8).
            assert page.locator("#analyze-btn").count() == 1, "scan button missing"
            assert page.locator("#prov-banner").is_hidden(), "banner must start hidden"
            page.wait_for_function("() => document.getElementById('folder-path').value !== ''", timeout=10_000)
            assert Path(page.input_value("#folder-path")).resolve() == Path(args.scan_dir).resolve(), "folder not prefilled from --folder"
            assert page.locator("a.skip-link[href='#main']").count() == 1, "skip link missing"

            # GUI-REV G-4: an error of one attempt (a folder outside the read roots)
            # is marked "이전 검사" once the next scan starts — not left standing
            # above results it does not describe.
            prefilled = page.input_value("#folder-path")
            page.fill("#folder-path", "/")
            page.keyboard.press("Enter")
            page.wait_for_selector("#error-region .err-item", timeout=10_000)
            assert "허용되지 않은 경로" in page.locator("#error-region").inner_text()
            page.fill("#folder-path", prefilled)
            # The 403 above was asked for; the browser logs it as a failed resource.
            console_errors[:] = [e for e in console_errors if "status of 403" not in e]
            failed_responses.clear()

            # 2. Run a real folder scan through the UI — Enter in the folder box submits (U-8).
            page.locator("#scan-options summary").click()
            page.check("#opt-recursive")
            page.focus("#folder-path")
            page.keyboard.press("Enter")
            page.wait_for_selector("#results-section:not([hidden])", timeout=180_000)
            # A11Y-4: focus lands on the conclusion headline, never on <body>.
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'sum-h'", timeout=10_000)
            stale = page.locator("#error-region .err-item")
            assert stale.count() >= 1 and all("이전 검사" in stale.nth(i).inner_text() for i in range(stale.count())), \
                "an earlier attempt's error must be marked 이전 검사 (G-4)"
            headline = page.locator("#sum-h").inner_text()
            assert "개 중 " in headline and headline.endswith("."), headline
            bar = page.locator("#progress")
            assert bar.get_attribute("role") == "progressbar" and bar.get_attribute("aria-valuemin") == "0"
            assert int(bar.get_attribute("aria-valuemax") or 0) > 0, "progressbar has no value range"

            # 3. Environment notice (one line) and the full provenance text behind 자세히.
            assert page.locator("#env-note").is_visible(), "environment notice not shown after a degraded scan"
            page.click("#env-more")
            banner = page.locator("#prov-banner")
            assert banner.is_visible(), "provenance details not shown"
            banner_text = banner.inner_text()
            assert "신경망 미탑재(측정 게이트 미충족)" in banner_text, f"unexpected banner: {banner_text}"  # D16
            # R16: the disclaimer reads in the three-verdict language, never "우선순위".
            assert "이 결과는 결론과 근거로 읽으십시오; 점수는 보정된 경우에만 표시됩니다" in banner_text, "three-verdict disclaimer missing"
            assert "우선순위" not in banner_text, "banner still calls the result a priority signal"
            # D16: 결론순 is the default (and only verdict) ordering; no 점수순/위험도순.
            assert page.locator("#res-sort").input_value() == "verdict", "default sort must be 결론순"
            assert page.locator("#res-sort option[value='score']").count() == 0, "점수순 sort must be gone"

            # 4. Ledger rows rendered; the strip counts add up to the row count.
            rows = page.locator("#res-list .row")
            assert rows.count() > 0, "no ledger rows rendered"
            counts = [int(page.locator(sel).inner_text()) for sel in ("#stat-manip", "#stat-auth", "#stat-undet", "#stat-other")]
            if sum(counts) <= 200:
                assert sum(counts) == rows.count(), (counts, rows.count())
            verdicts = [rows.nth(i).get_attribute("data-verdict") for i in range(rows.count())]
            assert set(verdicts) <= {"manipulation_evidence", "authenticity_evidence", "undetermined", "other"}, verdicts
            order = {"manipulation_evidence": 0, "authenticity_evidence": 1, "undetermined": 2, "other": 3}
            assert [order[v] for v in verdicts] == sorted(order[v] for v in verdicts), "evidence-bearing rows must come first"
            assert page.locator("#stat-pills [data-band='medium']").count() == 0, "legacy 'medium' pill must be gone"
            seg = page.locator("#stat-pills .seg[data-band='undetermined']")
            seg.click()
            assert seg.get_attribute("aria-pressed") == "true"
            assert all(rows.nth(i).get_attribute("data-verdict") == "undetermined" for i in range(rows.count()))
            seg.click()
            assert seg.get_attribute("aria-pressed") == "false"

            # 4b. Contract v2 in the evidence panel: verdict line, then 기술 세부 holds
            #     verdict -> evidence (by kind) -> coverage (QA-OUT-1/3/6).
            rows.first.click()
            detail = page.locator("#detail")
            detail.locator("#detail-h").wait_for(timeout=10_000)
            tech = detail.locator("details.disc", has_text="기술 세부")
            tech.locator("summary").click()
            head_text = tech.locator(".verdict-head").inner_text()
            assert any(label in head_text for label in ("조작·생성 근거 있음", "원본성 근거 있음", "판단 불가")), head_text
            assert tech.locator(".ev-group").count() >= 1, "evidence block missing"
            for kind in tech.locator(".ev-group[data-kind]").all():
                assert kind.get_attribute("data-kind") in {"deterministic", "statistical", "lexical"}
            assert tech.locator(".cov-group").count() == 1, "coverage block missing"
            html = tech.inner_html()
            assert html.index("verdict-head") < html.index("ev-group") < html.index("cov-group"), \
                "detail order must be verdict -> evidence -> coverage"
            assert detail.locator("details.disc", has_text="실행하지 않은 검사").count() == 1, "skipped-check disclosure missing"
            # Text results lead with the fixed legal limitation.
            for index in range(rows.count()):
                if ".txt" in rows.nth(index).inner_text():
                    rows.nth(index).click()
                    text_tech = page.locator("#detail details.disc", has_text="기술 세부")
                    text_tech.locator("summary").click()
                    text_tech.locator(".legal-note").wait_for(timeout=10_000)
                    assert "증거능력이 없으며" in text_tech.locator(".legal-note").inner_text()
                    break

            # 4c. A11Y-5: list keys only inside the ledger (roving tabindex), J/K switchable.
            assert page.locator("#res-list .row[tabindex='0']").count() == 1, "roving tabindex must leave one tab stop"
            if rows.count() > 2:
                rows.first.focus()
                page.keyboard.press("ArrowDown")
                assert page.evaluate("document.activeElement === document.querySelectorAll('#res-list .row')[1]")
                page.keyboard.press("j")
                assert page.evaluate("document.activeElement === document.querySelectorAll('#res-list .row')[2]")
                page.evaluate("document.getElementById('kb-toggle').checked = false")
                page.keyboard.press("k")
                assert page.evaluate("document.activeElement === document.querySelectorAll('#res-list .row')[2]"), "J/K must stop when switched off"
                page.evaluate("document.getElementById('kb-toggle').checked = true")

            # 5. Report screen: case fields have no defaults and block the evidence statement.
            page.click("a[data-view-link='report']")
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'rep-h'")
            assert page.input_value("#case-no") == "" and page.input_value("#case-name") == "", "case fields must start empty"
            assert "필수" in page.locator("#report-checks").inner_text()
            assert page.get_attribute("#exp-evidence", "aria-disabled") == "true", "evidence statement must wait for 사건번호·사건명"
            page.fill("#case-no", "2024가단0000")
            page.fill("#case-name", "시험 사건")
            assert "2024가단0000" in page.locator("#a4").inner_text()

            # 6. Reflow (WCAG 1.4.10): no horizontal page scroll at 375px on any screen.
            page.set_viewport_size({"width": 375, "height": 812})
            for view in ("scan", "ledger", "report", "tools"):
                page.evaluate(f"location.hash = '#/{view}'")
                page.wait_for_timeout(200)
                width = page.evaluate("document.documentElement.scrollWidth")
                assert width <= 375, f"{view}: page scrolls sideways at 375px ({width}px)"
            page.set_viewport_size({"width": 1440, "height": 900})
            page.evaluate("location.hash = '#/ledger'")

            # 7. CSP/console errors: none tolerated. No request leaves the local server
            #    (fonts are local stacks; nothing is fetched from the network).
            csp = [e for e in console_errors if "Content Security Policy" in e or "Refused" in e]
            assert not csp, f"CSP violations: {csp}"
            assert not console_errors, f"console errors: {console_errors[:5]} (failed responses: {failed_responses[:5]})"
            outside = [url for url in requests if not url.startswith((f"http://127.0.0.1:{args.port}/", "blob:", "data:"))]
            assert not outside, f"requests outside the local server: {outside[:5]}"

            page.screenshot(path=str(args.shot), full_page=True)
            browser.close()
        print(f"GUI smoke PASS — screenshot: {args.shot}")
        return 0
    finally:
        server.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
