#!/usr/bin/env python3
"""Headless-Chrome smoke test for the Deepfake Lens GUI.

Drives the real browser against a live `deepfake-lens web` instance and
asserts the examiner-visible contract: no CSP/console errors, the folder
box is prefilled and Enter starts the scan, focus lands on the conclusion
headline, the verdict strip and ledger rows agree, the evidence panel keeps
verdict -> evidence -> coverage, the ledger keys stay inside the list, the
evidence statement waits for the case number and name, the page reflows at
375px and nothing is requested outside the local server.

Requires: pip install playwright, and either Google Chrome (channel="chrome" —
no browser download needed) or Playwright's Chromium (`playwright install
chromium`), which is used when Chrome is not installed (e.g. Linux arm64).

Usage:
    python scripts/gui_smoke.py --scan-dir /path/to/fixtures [--port 8899]
"""

from __future__ import annotations

import json as _json_mod
import subprocess
import sys
import time
from pathlib import Path


def start_server(port: int, folder: str, extra_roots: tuple[str, ...] = ()) -> subprocess.Popen:
    roots = [arg for root in extra_roots for arg in ("--allow-root", root)]
    proc = subprocess.Popen(
        [sys.executable, "-m", "deepfake_lens", "web", "--host", "127.0.0.1", "--port", str(port), "--folder", folder, *roots],
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

    # R19-13 step: a scratch folder (a copy of two files of the scan folder) that the
    # smoke may change on disk — the examiner's folder itself is never written to.
    import shutil as _shutil
    import tempfile as _tempfile

    mutable = Path(_tempfile.mkdtemp(prefix="gui-smoke-mutable-"))
    candidates = [path for path in sorted(Path(args.scan_dir).rglob("*")) if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".txt"}]
    for source in candidates[:2]:
        _shutil.copy2(source, mutable / source.name)
    if not any(mutable.iterdir()):
        (mutable / "note.txt").write_text("시험용 글입니다.", encoding="utf-8")
    server = start_server(args.port, args.scan_dir, (str(mutable),))
    try:
        console_errors: list[str] = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # no Google Chrome (e.g. Linux arm64): Playwright's own Chromium
                browser = p.chromium.launch(headless=True)
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
            # GUI-REV4 K-7: the text row is found by its kind, not by ".txt" in its name
            # (an image named "rtl\u202egnp.txt.png" is not a text result).
            if page.evaluate("results.some(e => e.row && e.item.kind === 'text' && e.item.result)"):
                page.evaluate("results.find(e => e.row && e.item.kind === 'text' && e.item.result).row.click()")
                text_tech = page.locator("#detail details.disc", has_text="기술 세부")
                text_tech.locator("summary").click()
                text_tech.locator(".legal-note").wait_for(timeout=10_000)
                assert "증거능력이 없으며" in text_tech.locator(".legal-note").inner_text()

            # 4b'. Merge of phase0 R22-3: a review / feedback save the server refuses (409, E49 — the
            #      record would be inside an examined folder) is said next to the review controls
            #      with the server's reason; it was "서버에 저장됨" whatever the server answered.
            refusal = "검토 기록 파일이 검사 대상 폴더 안에 있어 저장하지 않았습니다(시험)"
            refused_body = _json_mod.dumps({"error": refusal}, ensure_ascii=False)
            page.route("**/api/review", lambda route: route.fulfill(status=409, content_type="application/json; charset=utf-8", body=refused_body)
                       if route.request.method == "POST" else route.continue_())
            page.route("**/api/feedback", lambda route: route.fulfill(status=409, content_type="application/json; charset=utf-8", body=refused_body))
            page.evaluate("() => { const n = document.querySelector('#detail .rev-note'); n.value = '스모크 메모'; n.dispatchEvent(new Event('input')); }")
            page.wait_for_function("() => (document.querySelector('#detail .rev-sync-status') || {}).textContent.includes('서버에 저장하지 못했습니다')", timeout=10_000)
            sync_text = page.evaluate("document.querySelector('#detail .rev-sync-status').textContent")
            assert refusal in sync_text and "이 브라우저에만" in sync_text, sync_text
            assert page.get_attribute("#detail .rev-sync-status", "role") == "status"
            page.evaluate("() => document.querySelector('#detail button[data-label=\"real\"]').click()")
            page.wait_for_function("() => (document.querySelector('#detail .fb-status') || {}).textContent.includes('기록하지 못했습니다')", timeout=10_000)
            fb_text = page.evaluate("document.querySelector('#detail .fb-status').textContent")  # inside a closed disclosure
            assert refusal in fb_text, fb_text
            page.unroute("**/api/review")
            page.unroute("**/api/feedback")
            page.evaluate("() => { const n = document.querySelector('#detail .rev-note'); n.value = ''; n.dispatchEvent(new Event('input')); }")
            page.wait_for_function("() => document.querySelector('#detail .rev-sync-status').textContent === '서버에 저장됨'", timeout=10_000)
            console_errors[:] = [e for e in console_errors if "status of 409" not in e]

            # 4b''. Merge of phase0 P0R-12: a conclusion's caveats ("이 결론이 틀릴 수 있는 이유") are in the
            #       evidence panel outside any disclosure, and a self-declared record is marked.
            caveated = page.evaluate("results.findIndex(e => e.row && ((e.item.result || {}).conclusion_caveats || []).length)")
            if caveated >= 0:
                page.evaluate(f"results[{caveated}].row.click()")
                section = page.locator("#detail .d-caveat")
                section.wait_for(timeout=10_000)
                assert "이 결론이 틀릴 수 있는 이유" in section.inner_text() and section.is_visible()
                if page.evaluate(f"((results[{caveated}].item.result || {{}}).evidence || []).some(e => e.self_declared)"):
                    assert page.locator("#detail .self-declared").count() >= 1, "a self-declared record is not marked"
                print("gui_smoke: conclusion caveats shown in the evidence panel")
            else:
                print("gui_smoke: no row with conclusion caveats in this folder — caveat display not exercised")

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
            # GUI-REV3 J-6: no side yet — the preview says so instead of drawing the 원고·갑
            #     form, and the forensic PDF waits for the side too.
            unsided = a4_ready()
            assert "(의뢰 측 미선택)" in unsided, "the preview must mark the missing side"
            assert "갑 제1호증" not in unsided and "원고(고소인)의 소송대리인은" not in unsided, "the preview drew the plaintiff form without a side"
            assert "의뢰 측" in page.evaluate("exportBlockReason('pdf', selectedEntries())")
            # Merge of P0R-6: the server makes the plaintiff side only (BL-15) — the defendant choice is
            # disabled with a note, the side is sent as `side`.
            assert page.locator("input[name='submitter-side'][value='defendant']").is_disabled(), "the defendant side must wait for BL-15"
            assert "후속 작업 예정" in page.locator("#side-note").inner_text()
            page.check("input[name='submitter-side'][value='plaintiff']")
            assert "갑 제1호증" in a4_ready()
            assert page.evaluate("exportPayload('evidence').side") == "plaintiff"
            payload_keys = set(page.evaluate("Object.keys(exportPayload('evidence'))"))
            assert not payload_keys & {"items", "summary", "options", "scan_root", "thresholds", "coverage"}, payload_keys
            # GUI-REV3 J-10: the empty boxes are named apart.
            assert "원고·고소인 칸과 피고·피의자 칸이 비어 있습니다" in page.locator("#report-checks").inner_text()
            # GUI-REV2 H-3: the forensic PDF's exhibit box follows the side.
            assert page.evaluate("exportPayload('pdf').exhibit_no").startswith("갑 제"), "forensic PDF exhibit box must follow the side"
            assert "의뢰 측" not in page.evaluate("exportBlockReason('evidence', selectedEntries())")
            note = page.locator("#export-note").inner_text()
            assert "지금 내보낼 수 있는 형식" in note and "HTML 보고서" in note, note
            # GUI-REV2 H-4: with the default choice, the checklist says which appendix rows
            #     carry a cue or a failed check (the hostile folder has both).
            appendix_cues = page.evaluate("results.filter(e => itemBand(e) === 'undetermined' && e.item.source !== 'upload' && cueEvidence(e.item.result).length).length")
            if appendix_cues:
                assert "번호 없이 남는 판단 불가 행 중 단서" in page.locator("#report-checks").inner_text()
            # GUI-REV2 H-1, rebuilt on P0R-6: the preview's (호증, 파일) list is the job's statement — the
            #     chosen rows numbered in the chosen order, every other job row after them unnumbered
            #     ("서증 외(검사 행)"); the PDF comparison below checks every cell.
            page.check("#inc-undet")
            a4_ready()
            preview_rows = page.evaluate("[...document.querySelectorAll('#a4 tr[data-exhibit]')].map(r => [r.dataset.exhibit, r.dataset.path])")
            chosen_paths = page.evaluate("exportPayload('evidence').exhibit_paths.map(p => displayName(p))")
            assert [path for _, path in preview_rows[:len(chosen_paths)]] == chosen_paths, (preview_rows[:4], chosen_paths[:4])
            assert preview_rows[0][0] == "갑 제1호증"
            job_rows = page.evaluate("results.filter(e => e.item.source !== 'upload').length")
            assert len(preview_rows) == job_rows, (len(preview_rows), job_rows)
            assert all(exhibit == "서증 외(검사 행)" for exhibit, _ in preview_rows[len(chosen_paths):])
            # GUI-REV2 H-2: the preview shows exactly the PDF's sections — every case-box,
            #     box and signoff line of the preview is in the downloaded PDF, the box
            #     titles in the same order, and no section the PDF lacks.
            preview_text = a4_ready()
            for absent in ("결론 요약", "검사의 한계", "판단할 근거가 없었으며", "특이 사항 없음"):
                assert absent not in preview_text, f"preview shows {absent!r}, which the PDF does not have"
            def preview_equals_pdf() -> None:
                """Every preview line and table cell is in the downloaded evidence PDF, box titles in order."""
                a4_ready()
                preview_lines = page.evaluate("[...document.querySelectorAll('#a4 .a4-box p, #a4 .a4-signoff p')].map(p => p.textContent)")
                box_titles = page.evaluate("[...document.querySelectorAll('#a4 .a4-box .a4-h')].map(h => h.textContent)")
                with page.expect_download() as statement_download:
                    page.click("#exp-evidence")
                import pymupdf  # type: ignore[import-not-found]

                with pymupdf.open(statement_download.value.path()) as document:
                    pdf_flat = "".join("".join(page_.get_text().split()) for page_ in document)
                for line in preview_lines:
                    assert "".join(line.split()) in pdf_flat, f"preview line not in the PDF: {line[:80]!r}"
                # Merge of phase0 R23-4: a name with characters the PDF font cannot draw (e.g. Hebrew)
                # is \uNNNN in the PDF and in the preview, and the preview says why.
                glyphless = page.evaluate("results.some(e => /[\\u0590-\\u08ff]|[\\u{1F000}-\\u{1FFFF}]/u.test(String(e.item.path)))")
                drawn = page.locator("#a4").inner_text()
                if glyphless and "\\u" in drawn:
                    assert page.locator("#a4 .a4-glyph").count() == 1, "escaped preview text without its note"
                # GUI-REV3 J-3: the table cells too — no literal "\n", every cell's text in the PDF.
                cells = page.evaluate("[...document.querySelectorAll('#a4 tr[data-exhibit] td')].map(td => td.innerText)")
                assert cells, "no preview table cells"
                for cell in cells:
                    # (a literal "\\n" from the old rendering is not in the PDF, which breaks the line)
                    assert "".join(cell.split()) in pdf_flat, f"preview cell not in the PDF: {cell[:80]!r}"
                positions = [pdf_flat.find("".join(title.split())) for title in box_titles]
                assert all(position >= 0 for position in positions) and positions == sorted(positions), (box_titles, positions)

            if page.evaluate("pdfAvailable") is True:
                preview_equals_pdf()
            page.uncheck("#inc-undet")
            if page.evaluate("selectedEntries().length") == 0:
                # A folder without evidence-bearing rows (e.g. the adversarial set): with 판단 불가
                # unticked nothing is chosen — the preview must still settle and say so.
                assert "고른 서증이 없습니다" in a4_ready(), page.locator("#a4").inner_text()
                print("gui_smoke: no evidence-bearing rows — checked the empty-choice preview")
            else:
                a4_ready()
            # GUI-REV G-1, rebuilt on P0R-6: every format names the server-held job (all its rows are signed);
            # the choice only names exhibits.
            total_rows = page.evaluate("results.length")
            for kind in ("evidence", "pdf", "html", "json"):
                assert page.evaluate(f"exportPayload('{kind}').scan_job") == page.evaluate("lastScanJob"), f"{kind} export lost the job"
            chosen = page.evaluate("exportPayload('evidence').exhibit_paths.length")
            assert chosen == page.evaluate("selectedEntries().length") and chosen <= total_rows
            if 0 < chosen < total_rows:
                # P0R-6: the other job rows are in the table unnumbered (the appendix is gone)
                assert "서증 외(검사 행)" in a4_ready()

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
            # GUI-REV3 J-5: a cancelled scan finishing while the examiner types on the
            #     report screen keeps the focus there; the band says it was cancelled.
            page.unroute("**/api/scan-status*")
            page.route("**/api/scan-status*", hold)
            page.evaluate("location.hash = '#/scan'")
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === true", timeout=10_000)
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            page.focus("#case-plaintiff")
            page.route("**/api/scan-status*", lambda route: route.fulfill(status=200, content_type="application/json", body=_json.dumps(final)))
            page.unroute("**/api/scan-status*", hold)
            page.wait_for_function("() => scanBusy === false", timeout=30_000)
            assert page.evaluate("location.hash") == "#/report"
            assert page.evaluate("document.activeElement.id") == "case-plaintiff", "a cancelled scan took the focus"
            assert "검사가 취소되었습니다" in page.locator("#busy-band").inner_text()
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
                page.check("input[name='submitter-side'][value='plaintiff']")
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
                # GUI-REV3 J-8: uploads only — they cannot be exhibits, and the groups count none.
                checks = page.locator("#report-checks").inner_text()
                assert "서증이 될 수 없습니다" in checks and "올린 파일만 있어 증거설명서를 만들 수 없습니다" in checks, checks
                assert "판단 불가 칸을 고를 수 있습니다" not in checks
                assert all(page.inner_text(f"#{group}-n") == "0개" for group in ("inc-manip", "inc-auth", "inc-undet", "inc-other"))
                page.get_by_role("button", name="이 폴더의 사건이 맞음").click()

            # 6d. GUI-REV3 J-2: with "같은 내용의 파일은 한 번만 검사", a duplicate row shows its
            #     original's conclusion, and duplicates of a manipulated file going to the
            #     appendix are named in the checklist. (The signed export re-derives the
            #     rows; its duplicate handling is the phase-0 R19-13 fix, not compared here.)
            page.evaluate("location.hash = '#/scan'")
            page.fill("#folder-path", prefilled)
            page.evaluate("document.getElementById('opt-recursive').checked = true; document.getElementById('opt-dedupe').checked = true")
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === false && currentView === 'ledger'", timeout=180_000)
            page.evaluate("document.getElementById('opt-dedupe').checked = false")
            duplicates = page.evaluate("results.filter(e => e.item.status === 'duplicate' && originOf(e.item)).map(e => [shortVerdict(e.item), verdictOf(originOf(e.item))])")
            for short, origin in duplicates:
                assert "원본:" in short, short
            dup_manip = sum(1 for _, origin in duplicates if origin == "manipulation_evidence")
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            if dup_manip:
                assert f"번호 없이 남는 중복 행 중 조작·생성 근거 있음 {dup_manip}개" in page.locator("#report-checks").inner_text()
            # Merge of R19-13: duplicate rows stay duplicates in the export when their bytes still
            # match, so the dedupe preview (incl. 기록되지 않은 파일 and the 분석 못 함 exhibits) now
            # equals the signed PDF.
            if page.evaluate("pdfAvailable") is True and page.evaluate("caseUnconfirmed()"):
                page.get_by_role("button", name="이 폴더의 사건이 맞음").click()
            if page.evaluate("pdfAvailable") is True:
                page.check("#inc-other")
                page.check("#inc-undet")
                preview_equals_pdf()
                page.uncheck("#inc-other")
                page.uncheck("#inc-undet")

            # Merge of R19-13: a file changed on disk after the scan — the export is refused
            # (409), the error names it with both verdicts and offers 다시 검사, the row is
            # marked; scanned again, the export goes through.
            page.evaluate("location.hash = '#/scan'")
            page.fill("#folder-path", str(mutable))
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === false && currentView === 'ledger' && results.length > 0", timeout=180_000)
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            if page.evaluate("caseUnconfirmed()"):
                page.get_by_role("button", name="이 폴더의 사건이 맞음").click()
            victim = sorted(mutable.iterdir())[0]
            victim.write_bytes(victim.read_bytes() + b"\nchanged after the scan")
            # GUI-REV4 K-2: at 1366×768, with the export buttons scrolled into view, the
            # refusal shows next to them (role=alert) — not only in the top error region.
            page.set_viewport_size({"width": 1366, "height": 768})
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.click("#exp-signed")
            page.wait_for_selector("#export-alert:not([hidden])", timeout=120_000)
            in_view = page.evaluate("(() => { const r = document.getElementById('export-alert').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })()")
            assert in_view, "the refusal next to the export buttons is not in view"
            assert page.get_attribute("#export-alert", "role") == "alert"
            assert victim.name in page.locator("#export-alert").inner_text()
            assert "검사 뒤 바뀐 파일 1개(다시 검사 필요)" in page.locator("#report-checks").inner_text()
            assert page.get_attribute("#exp-signed", "aria-disabled") == "true", "the signed formats wait for the rescan"
            page.set_viewport_size({"width": 1440, "height": 900})
            # GUI-REV5 L-1: clearing the results takes the changed-file alert and checklist item
            # with them (a copy of the rows is scanned again right below).
            page.once("dialog", lambda dialog: dialog.accept())
            page.evaluate("document.getElementById('exp-clear').click()")
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            assert page.locator("#export-alert").is_hidden(), "the changed-file alert outlived 지우기"
            assert "검사 뒤 바뀐 파일" not in page.locator("#report-checks").inner_text()
            assert page.evaluate("changedPaths.size") == 0
            page.evaluate("location.hash = '#/scan'")
            page.fill("#folder-path", str(mutable))
            victim.write_bytes(victim.read_bytes() + b"\nchanged again")
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === false && currentView === 'ledger' && results.length > 0", timeout=180_000)
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            # GUI-REV5 L-3: an HTML export asked for before the file changed is answered by the
            # server at once, but its download is held until the JSON export's 409 has landed.
            page.evaluate("""() => {
                window.__realFetch = window.fetch;
                window.__htmlGate = new Promise(resolve => { window.__releaseHtml = resolve; });
                window.__htmlAnswered = false;
                window.fetch = async (url, options) => {
                    const res = await window.__realFetch(url, options);
                    if (String(url).includes('/api/report?format=html')) { window.__htmlAnswered = true; await window.__htmlGate; }
                    return res;
                };
            }""")
            page.click("#exp-report")
            page.wait_for_function("() => window.__htmlAnswered === true", timeout=180_000)
            victim.write_bytes(victim.read_bytes() + b"\nand once more")
            page.click("#exp-signed")
            page.wait_for_selector("#export-alert:not([hidden])", timeout=120_000)
            alert = page.locator("#export-alert")
            assert victim.name in alert.inner_text(), alert.inner_text()
            assert page.locator("#export-alert-rescan").is_visible()
            # GUI-REV5 Info (c): the 409 is said once — next to the buttons, not again in the error region.
            assert page.locator("#error-region .err-item", has_text="검사 뒤 바뀐 파일").count() == 0, "the 409 was announced twice"
            with page.expect_download():
                page.evaluate("() => window.__releaseHtml()")
            page.evaluate("() => { window.fetch = window.__realFetch; }")
            page.wait_for_function("() => document.getElementById('export-note').textContent.includes('내려받았습니다')")
            assert page.evaluate("changedPaths.size") == 1, "an earlier-started download cleared the 409 state"
            assert alert.is_visible() and victim.name in alert.inner_text(), "the alert lost its changed files"
            note = page.locator("#export-note").inner_text()
            assert "검사 뒤 바뀐 파일이 확인되기 전에 요청한 파일" in note and "다시 검사" in note, note
            assert page.evaluate(f"changedPaths.has({_json.dumps(victim.name)})"), "the changed row is not marked"
            console_errors[:] = [e for e in console_errors if "status of 409" not in e]
            page.click("#export-alert-rescan")
            page.wait_for_function("() => currentView === 'scan'")
            # GUI-REV4 K-3: 다시 검사 leaves the focus on 검사 시작.
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'analyze-btn'", timeout=5_000)
            assert Path(page.input_value("#folder-path")).resolve() == mutable.resolve()
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === false && currentView === 'ledger' && results.length > 0", timeout=180_000)
            assert page.evaluate("changedPaths.size") == 0
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            with page.expect_download() as signed_again:
                page.click("#exp-signed")
            assert _json.loads(Path(signed_again.value.path()).read_text(encoding="utf-8"))["summary"]["total"] == page.evaluate("results.length")
            # GUI-REV4 K-1: after 다시 검사 and a successful export no refusal is left standing.
            assert page.locator("#export-alert").is_hidden(), "a 409 alert outlived the rescan"
            # K-1: a 409 that arrives after other results replaced the ones it refused marks
            # nothing and shows no refusal (the server's slow re-analysis is simulated).
            page.evaluate("""() => {
                window.__realFetch = window.fetch;
                window.fetch = async (url, options) => {
                    if (String(url).includes('/api/report?format=json')) {
                        const path = results[0].item.path;
                        await new Promise(resolve => setTimeout(resolve, 4000));
                        return new Response(JSON.stringify({error: '검사 뒤 바뀐 파일이 있습니다', changed_rows: [{path, posted_verdict: 'undetermined',
                            server_verdict: 'manipulation_evidence', posted_status: 'analyzed', server_status: 'analyzed', posted_sha256: 'a', server_sha256: 'b'}]}),
                            {status: 409, headers: {'Content-Type': 'application/json'}});
                    }
                    return window.__realFetch(url, options);
                };
            }""")
            page.click("#exp-signed")
            page.evaluate("location.hash = '#/scan'")
            page.wait_for_function("() => currentView === 'scan'")
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === false && currentView === 'ledger' && results.length > 0", timeout=180_000)
            page.wait_for_timeout(5000)
            page.evaluate("() => { window.fetch = window.__realFetch; }")
            assert page.evaluate("changedPaths.size") == 0, "a late 409 marked rows of the new results"
            assert page.locator("#export-alert").is_hidden(), "a late 409 showed a refusal"
            # GUI-REV5 Info (b): the notice stays in the export note (after the toast is gone)
            # until the next export action.
            page.wait_for_timeout(4500)
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            assert "이전 검사의 것이라 무시했습니다" in page.locator("#export-note").inner_text(), page.locator("#export-note").inner_text()

            # 6e. Rebuilt on P0R-6: a folder on a hashed row's path replaced by a link after the scan is a
            #     changed row (409 with the server's reason), said once next to the buttons; and a refusal
            #     that is not a changed file — the server-held job unknown or expired (404, a stand-in here,
            #     e.g. after a restart of a server that could not persist it) — is said there too, with
            #     다시 검사 (a new scan makes a new job).
            linked = mutable / "lnk"
            (linked / "sub").mkdir(parents=True)
            (linked / "sub" / "k.txt").write_text("링크 시험 글", encoding="utf-8")
            (mutable / "target").mkdir()
            (mutable / "target" / "k.txt").write_text("링크 시험 글", encoding="utf-8")
            page.evaluate("location.hash = '#/scan'")
            page.fill("#folder-path", str(linked))
            page.evaluate("document.getElementById('opt-recursive').checked = true; document.getElementById('opt-dedupe').checked = false")
            page.click("#analyze-btn")
            page.wait_for_function("() => scanBusy === false && currentView === 'ledger' && results.length > 0", timeout=180_000)
            page.evaluate("location.hash = '#/report'")
            page.wait_for_function("() => currentView === 'report'")
            if page.evaluate("caseUnconfirmed()"):
                page.get_by_role("button", name="이 폴더의 사건이 맞음").click()
            page.evaluate("""() => {
                window.__realFetch = window.fetch;
                window.fetch = async (url, options) => {
                    if (String(url).includes('/api/report?format=html')) {
                        return new Response(JSON.stringify({error: '알 수 없거나 만료된 검사 작업입니다 — 폴더를 다시 검사하십시오'}),
                            {status: 404, headers: {'Content-Type': 'application/json'}});
                    }
                    return window.__realFetch(url, options);
                };
            }""")
            page.click("#exp-report")
            page.wait_for_selector("#export-alert:not([hidden])", timeout=60_000)
            assert "만료된 검사 작업" in page.locator("#export-alert").inner_text()
            assert page.locator("#export-alert-rescan").is_visible()
            assert page.locator("#error-region .err-item", has_text="만료된 검사 작업").count() == 0, "the refusal went to the error region"
            page.evaluate("() => { window.fetch = window.__realFetch; }")
            # a real export clears that failure
            with page.expect_download():
                page.click("#exp-report")
            page.wait_for_function("() => document.getElementById('export-alert').hidden", timeout=60_000)
            _shutil.rmtree(linked / "sub")
            (linked / "sub").symlink_to(mutable / "target", target_is_directory=True)
            page.click("#exp-report")
            page.wait_for_function("() => changedPaths.has('sub/k.txt')", timeout=120_000)
            assert "sub/k.txt" in page.locator("#export-alert").inner_text()
            assert "링크" in page.locator("#export-alert").inner_text(), page.locator("#export-alert").inner_text()
            console_errors[:] = [e for e in console_errors if "status of 409" not in e]

            # 6f. Merge of phase0 R24-1: a real scan cancelled from the GUI while an archive's members are
            #     still queued — the archive row is "cancelled" with the roll-up of what was analyzed; the
            #     ledger says 검사 취소 (부분 결과), and an export is not refused as changed (P0R-6: the job's
            #     rows, the container row included, are signed with the server's cancel count).
            import zipfile as _zipfile

            czip = mutable / "czip"
            czip.mkdir()
            with _zipfile.ZipFile(czip / "bundle.zip", "w") as archive:
                for index in range(600):
                    archive.writestr(f"m{index:03d}.txt", f"구성 파일 {index}번의 글입니다. " * 20)
            page.evaluate("location.hash = '#/scan'")
            page.fill("#folder-path", str(czip))
            page.click("#analyze-btn")
            page.wait_for_selector("#scan-cancel:not([hidden])", timeout=30_000)
            page.click("#scan-cancel")
            page.wait_for_function("() => scanBusy === false", timeout=180_000)
            container = page.evaluate("(results.find(e => e.item.path === 'bundle.zip') || {}).item || null")
            if not container or container.get("status") != "cancelled":
                # The cancel landed after every member ran (a fast machine): said, never silently passed.
                print(f"gui_smoke: the cancel came after the archive finished (status {container and container.get('status')!r}) — cancelled-archive step not exercised")
            else:
                page.evaluate("location.hash = '#/ledger'")
                page.wait_for_function("() => currentView === 'ledger'")
                row_text = page.evaluate("results.find(e => e.item.path === 'bundle.zip').row.textContent")
                assert "검사 취소" in row_text, row_text
                assert page.locator("#partial-banner").is_visible(), "a cancelled scan's partial banner"
                page.evaluate("location.hash = '#/report'")
                page.wait_for_function("() => currentView === 'report'")
                if page.evaluate("caseUnconfirmed()"):
                    page.get_by_role("button", name="이 폴더의 사건이 맞음").click()
                with page.expect_download() as cancelled_download:
                    page.click("#exp-signed")
                signed_cancelled = _json.loads(Path(cancelled_download.value.path()).read_text(encoding="utf-8"))
                assert signed_cancelled["summary"].get("cancelled") is True, signed_cancelled["summary"]
                assert "bundle.zip" in [row["path"] for row in signed_cancelled["items"]]
                assert page.locator("#export-alert").is_hidden(), "a cancelled scan's export was refused"
                print("gui_smoke: cancelled archive — ledger, banner and the signed cancelled job checked")

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
        _shutil.rmtree(mutable, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
