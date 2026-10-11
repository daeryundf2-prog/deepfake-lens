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
            # GUI-REV2 H-10: an error that is not a scan's (an empty quick check) is never
            #     marked "이전 검사".
            page.evaluate("location.hash = '#/tools'")
            page.fill("#qc-text", "")
            page.click("#qc-text-btn")
            page.wait_for_selector("#error-region .err-item:not([data-scan])", timeout=10_000)
            page.evaluate("location.hash = '#/scan'")
            prefilled = page.input_value("#folder-path")
            page.fill("#folder-path", "/")
            page.keyboard.press("Enter")
            page.wait_for_selector("#error-region .err-item[data-scan]", timeout=10_000)
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
            stale = page.locator("#error-region .err-item[data-scan]")
            assert stale.count() >= 1 and all("이전 검사" in stale.nth(i).inner_text() for i in range(stale.count())), \
                "an earlier attempt's error must be marked 이전 검사 (G-4)"
            other = page.locator("#error-region .err-item:not([data-scan])")
            assert other.count() >= 1 and all("이전 검사" not in other.nth(i).inner_text() for i in range(other.count())), \
                "a quick-check error must not be marked 이전 검사 (H-10)"
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

            def a4_ready() -> str:
                # H-2: the preview comes from the server (debounced); wait for this render.
                page.wait_for_function("() => document.getElementById('a4').dataset.ready !== ''", timeout=60_000)
                return page.locator("#a4").inner_text()

            if page.evaluate("selectedEntries().length") == 0:
                page.check("#inc-undet")  # a folder without evidence-bearing rows: preview the 판단 불가 rows
            assert "2024가단0000" in a4_ready()
            # GUI-REV G-9: the submitting side has no default and is required for the
            # evidence statement; the choice numbers the exhibits (갑/을) and is posted.
            assert page.locator("input[name='submitter-side']:checked").count() == 0, "의뢰 측 must have no default"
            assert "의뢰 측을 고르지 않았습니다" in page.locator("#report-checks").inner_text()
            assert "의뢰 측" in page.evaluate("exportBlockReason('evidence', selectedEntries())")
            page.check("input[name='submitter-side'][value='defendant']")
            assert "을 제1호증" in a4_ready()
            assert page.evaluate("exportPayload('evidence').submitter_side") == "defendant"
            # GUI-REV2 H-3: the forensic PDF's exhibit box follows the side.
            assert page.evaluate("exportPayload('pdf').exhibit_no").startswith("을 제"), "forensic PDF exhibit box must follow the side"
            assert "의뢰 측" not in page.evaluate("exportBlockReason('evidence', selectedEntries())")
            note = page.locator("#export-note").inner_text()
            assert "지금 내보낼 수 있는 형식" in note and "HTML 보고서" in note, note
            # GUI-REV2 H-4: with the default choice, the checklist says which appendix rows
            #     carry a cue or a failed check (the hostile folder has both).
            appendix_cues = page.evaluate("results.filter(e => itemBand(e) === 'undetermined' && e.item.source !== 'upload' && cueEvidence(e.item.result).length).length")
            if appendix_cues:
                assert "부록으로 가는 판단 불가 행 중 단서" in page.locator("#report-checks").inner_text()
            # GUI-REV2 H-1: the preview's (호증, 파일) list is the signed statement's, in
            #     the same order — with 판단 불가 chosen too (cue/failed/reference rows,
            #     archive members). The statement comes from format=evidence-json, the
            #     body the evidence PDF is rendered from.
            page.check("#inc-undet")
            a4_ready()
            compared = page.evaluate("""async () => {
                const preview = [...document.querySelectorAll('#a4 tr[data-exhibit]')].map(r => [r.dataset.exhibit, r.dataset.path]);
                const res = await apiFetch('/api/report?format=evidence-json', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(exportPayload('evidence'))});
                const body = await res.json();
                return [preview, (body.entries || []).map(e => [e.exhibit_no, displayName(e.file_path)]), res.status];
            }""")
            assert compared[2] == 200, compared
            assert compared[0] and compared[0] == compared[1], (compared[0][:6], compared[1][:6])
            assert compared[0][0][0] == "을 제1호증"
            # GUI-REV2 H-2: the preview shows exactly the PDF's sections — every case-box,
            #     box and signoff line of the preview is in the downloaded PDF, the box
            #     titles in the same order, and no section the PDF lacks.
            preview_text = a4_ready()
            for absent in ("결론 요약", "검사의 한계", "판단할 근거가 없었으며", "특이 사항 없음"):
                assert absent not in preview_text, f"preview shows {absent!r}, which the PDF does not have"
            if page.evaluate("pdfAvailable") is True:
                preview_lines = page.evaluate("[...document.querySelectorAll('#a4 .a4-box p, #a4 .a4-signoff p')].map(p => p.textContent)")
                box_titles = page.evaluate("[...document.querySelectorAll('#a4 .a4-box .a4-h')].map(h => h.textContent)")
                with page.expect_download() as statement_download:
                    page.click("#exp-evidence")
                import pymupdf  # type: ignore[import-not-found]

                with pymupdf.open(statement_download.value.path()) as document:
                    pdf_flat = "".join("".join(page_.get_text().split()) for page_ in document)
                for line in preview_lines:
                    assert "".join(line.split()) in pdf_flat, f"preview line not in the PDF: {line[:80]!r}"
                # GUI-REV3 J-3: the table cells too — no literal "\n", every cell's text in the PDF.
                cells = page.evaluate("[...document.querySelectorAll('#a4 tr[data-exhibit] td')].map(td => td.innerText)")
                assert cells, "no preview table cells"
                for cell in cells:
                    # (a literal "\\n" from the old rendering is not in the PDF, which breaks the line)
                    assert "".join(cell.split()) in pdf_flat, f"preview cell not in the PDF: {cell[:80]!r}"
                positions = [pdf_flat.find("".join(title.split())) for title in box_titles]
                assert all(position >= 0 for position in positions) and positions == sorted(positions), (box_titles, positions)
            page.uncheck("#inc-undet")
            a4_ready()
            # GUI-REV G-1: every row is posted for every format; the choice only names exhibits.
            total_rows = page.evaluate("results.length")
            for kind in ("evidence", "pdf", "html", "json"):
                assert page.evaluate(f"exportPayload('{kind}').items.length") == total_rows, f"{kind} export dropped rows"
            chosen = page.evaluate("exportPayload('evidence').exhibit_paths.length")
            assert chosen == page.evaluate("selectedEntries().length") and chosen <= total_rows
            if 0 < chosen < total_rows:
                assert f"서증으로 넣지 않은 파일 {total_rows - chosen}개" in a4_ready()

            # 5b. GUI-REV G-5: while a new scan runs the other screens say the rows are
            #     the previous scan's, exports wait, and the new options are not
            #     attached to the old rows. The status poll is held at "running".
            import json as _json

            held = _json.dumps({"job_id": "held", "status": "running", "done": 1, "total": 9, "current": "a", "recent": [], "elapsed_seconds": 1})
            page.route("**/api/scan-status*", lambda route: route.fulfill(status=200, content_type="application/json", body=held))
            before_options = page.evaluate("JSON.stringify(lastScanOptions)")
            page.evaluate("location.hash = '#/scan'")
            if not page.locator("#scan-options").evaluate("d => d.open"):
                page.locator("#scan-options summary").click()
            page.check("input[name='pixel-mode'][value='fast']")
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === true", timeout=10_000)
            page.evaluate("location.hash = '#/ledger'")
            assert page.locator("#busy-band").is_visible(), "no 'new scan running' band over the previous results"
            assert "이전 결과" in page.locator("#busy-band").inner_text()
            page.evaluate("location.hash = '#/report'")
            for button in ("#exp-evidence", "#exp-pdf", "#exp-report", "#exp-signed"):
                assert page.get_attribute(button, "aria-disabled") == "true", f"{button} must wait for the running scan"
            assert page.evaluate("JSON.stringify(lastScanOptions)") == before_options, "options changed before the new results arrived"
            # GUI-REV2 H-5: the examiner is typing on the report screen when the scan ends —
            #     no screen change, no focus move; the band says it finished.
            page.focus("#case-name")
            page.unroute("**/api/scan-status*")
            page.wait_for_function("() => scanBusy === false", timeout=180_000)
            assert page.evaluate("lastScanOptions.pixel") == "fast", "the new scan's options were not attached to its results"
            assert page.evaluate("location.hash") == "#/report", "a finished scan must not move the examiner off the report screen"
            assert page.evaluate("document.activeElement.id") == "case-name", "a finished scan must not take the focus"
            assert "검사가 끝났습니다" in page.locator("#busy-band").inner_text()
            page.click("#busy-band-link")
            page.wait_for_function("() => currentView === 'ledger' && document.getElementById('busy-band').hidden", timeout=10_000)

            # 5c. GUI-REV G-6: a scan reconnected after a reload is shown (검사 screen),
            #     the ledger says it runs, and a cancelled result handled on another
            #     screen leaves focus on a visible element, never <body>.
            import urllib.request

            def hold(route: object) -> None:
                route.fulfill(status=200, content_type="application/json", body=held)  # type: ignore[attr-defined]

            page.route("**/api/scan-status*", hold)
            page.evaluate("location.hash = '#/scan'")
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === true", timeout=10_000)
            job_id = page.evaluate("JSON.parse(sessionStorage.getItem('dfl.scan-job')).job")
            page.evaluate("location.hash = '#/ledger'")
            page.reload(wait_until="networkidle")
            page.wait_for_function("() => scanBusy === true", timeout=10_000)
            assert page.evaluate("location.hash") == "#/scan", "a reconnected scan must be shown on the 검사 screen"
            page.evaluate("location.hash = '#/ledger'")
            assert page.locator("#busy-band").is_visible(), "the ledger must say a scan is running"
            final: dict = {}
            for _ in range(600):
                request = urllib.request.Request(f"http://127.0.0.1:{args.port}/api/scan-status?job={job_id}", headers={"X-Deepfake-Lens-Client": "gui"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    final = _json.loads(response.read())
                if final["status"] != "running":
                    break
                time.sleep(0.2)
            final["status"] = "cancelled"
            final["result"]["summary"].update(cancelled=True, files_cancelled=3)
            # The newer route wins; the hold is removed after it, so no poll slips through.
            page.route("**/api/scan-status*", lambda route: route.fulfill(status=200, content_type="application/json", body=_json.dumps(final)))
            page.unroute("**/api/scan-status*", hold)
            page.wait_for_function("() => scanBusy === false", timeout=30_000)
            assert page.evaluate("document.activeElement !== document.body"), "focus fell to <body> after a cancelled scan"
            page.unroute("**/api/scan-status*")
            page.evaluate("location.hash = '#/ledger'")
            assert page.locator("#partial-banner").is_visible(), "the partial-result banner (R18-3) must show"

            # 5d. GUI-REV G-7: a pasted-text quick check leads with the legal limitation
            #     and its reference signals (outside the disclosure), and shows no
            #     "C2PA 판독 불가" — C2PA does not apply to pasted text.
            page.evaluate("location.hash = '#/tools'")
            page.fill("#qc-text", "As an AI language model, I cannot help with that. 오늘 회의는 세 시에 시작합니다.")
            page.click("#qc-text-btn")
            page.wait_for_selector("#qc-out .qc-head", timeout=120_000)
            legal = page.locator("#qc-out > .qc-legal")
            assert legal.is_visible() and "증거능력이 없으며" in legal.inner_text(), "the legal limitation must lead the text result"
            assert page.locator("#qc-out > .qc-soft").is_visible(), "the lexical reference signal must be visible"
            assert "C2PA 판독 불가" not in (page.locator("#qc-out").text_content() or ""), "pasted text must not show a C2PA failure"

            # 6. Reflow (WCAG 1.4.10): no horizontal page scroll at 375px on any screen.
            page.set_viewport_size({"width": 375, "height": 812})
            for view in ("scan", "ledger", "report", "tools"):
                page.evaluate(f"location.hash = '#/{view}'")
                page.wait_for_timeout(200)
                width = page.evaluate("document.documentElement.scrollWidth")
                assert width <= 375, f"{view}: page scrolls sideways at 375px ({width}px)"
            page.set_viewport_size({"width": 1440, "height": 900})
            page.evaluate("location.hash = '#/ledger'")

            # 6b. GUI-REV G-1/G-10: the signed JSON the report screen downloads covers the
            #     whole scan (summary total == ledger rows) whatever the exhibit choice.
            page.evaluate("location.hash = '#/report'")
            with page.expect_download() as download_info:
                page.click("#exp-signed")
            signed = _json.loads(Path(download_info.value.path()).read_text(encoding="utf-8"))
            rows_now = page.evaluate("results.length")
            uploads = page.evaluate("results.filter(e => e.item.source === 'upload').length")
            assert signed["summary"]["total"] == rows_now - uploads, (signed["summary"]["total"], rows_now)
            assert len(signed["items"]) + len(signed.get("excluded_items", [])) == rows_now
            # G-9(d): clearing the ledger asks first; dismissing keeps the results.
            page.evaluate("location.hash = '#/ledger'")
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.evaluate("document.getElementById('exp-clear').click()")
            assert page.evaluate("results.length") == rows_now, "results cleared without confirmation"

            # 6b2. GUI-REV2 H-9: an upload whose second batch fails leaves the shown results'
            #      provenance alone (batches are staged until the results arrive).
            page.evaluate("location.hash = '#/scan'")
            before_coverage = page.evaluate("JSON.stringify(lastProvenance.coverage)")
            batches = {"n": 0}

            def upload(route: object) -> None:
                batches["n"] += 1
                if batches["n"] == 1:
                    body = _json.dumps({"items": [], "coverage": {"weights_total": 99, "weights_available": 0}, "thresholds": {"source": "staged"}})
                    route.fulfill(status=200, content_type="application/json", body=body)  # type: ignore[attr-defined]
                else:
                    route.fulfill(status=500, content_type="application/json", body=_json.dumps({"error": "시험용 실패"}))  # type: ignore[attr-defined]

            page.route("**/api/analyze-upload", upload)
            page.evaluate("addFiles(Array.from({length: 21}, (_, i) => new File(['글 ' + i], 'up' + i + '.txt', {type: 'text/plain'})))")
            page.click("#upload-btn")
            page.wait_for_function("() => scanBusy === false && document.getElementById('prog-h').textContent === '검사 실패'", timeout=60_000)
            page.unroute("**/api/analyze-upload", upload)
            assert batches["n"] == 2, batches
            assert page.evaluate("JSON.stringify(lastProvenance.coverage)") == before_coverage, "a failed upload changed the shown results' provenance"
            page.evaluate("document.getElementById('upload-clear').click()")
            console_errors[:] = [e for e in console_errors if "status of 500" not in e]

            # 6c. GUI-REV2 H-8: case fields entered for one folder are not silently reused
            #     for another — scanning a subfolder asks for a confirmation first.
            subfolders = sorted(path for path in Path(args.scan_dir).iterdir() if path.is_dir())
            if subfolders:
                page.evaluate("location.hash = '#/scan'")
                page.fill("#folder-path", str(subfolders[0]))
                page.evaluate("document.getElementById('opt-recursive').checked = true")  # the reload above reset the options
                page.click("#analyze-btn")
                page.wait_for_function("() => scanBusy === false && currentView === 'ledger'", timeout=180_000)
                page.evaluate("location.hash = '#/report'")
                page.wait_for_function("() => currentView === 'report'")
                assert "다른 검사" in page.locator("#report-checks").inner_text(), "the case fields of another folder must be confirmed"
                assert "확인" in page.evaluate("exportBlockReason('evidence', selectedEntries())")
                # GUI-REV3 J-4: editing another case field or the side does not clear it.
                page.evaluate("document.querySelector('.case-more').open = true")
                page.fill("#case-firm", "법무법인 시험")
                page.check("input[name='submitter-side'][value='plaintiff']")
                assert "다른 검사" in page.locator("#report-checks").inner_text(), "editing 법무법인/의뢰 측 silently confirmed the folder"
                page.check("input[name='submitter-side'][value='defendant']")
                page.get_by_role("button", name="이 폴더의 사건이 맞음").click()
                assert "다른 검사" not in page.locator("#report-checks").inner_text()

            # GUI-REV3 J-4: each upload is its own scope — a second, unrelated upload asks again.
            for index in range(2):
                page.evaluate("location.hash = '#/scan'")
                page.evaluate(f"addFiles([new File(['올린 글 {index}'], 'upload{index}.txt', {{type: 'text/plain'}})])")
                page.click("#upload-btn")
                page.wait_for_function("() => scanBusy === false && results.length > 0", timeout=60_000)
                page.evaluate("location.hash = '#/report'")
                page.wait_for_function("() => currentView === 'report'")
                assert "다른 검사" in page.locator("#report-checks").inner_text(), f"upload {index}: the case fields of another scope must be confirmed"
                page.get_by_role("button", name="이 폴더의 사건이 맞음").click()

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
