"""DESK-2..7 (docs/gui/plan_desktop_mode.md §4): the GUI of the desktop mode (`deepfake-lens desktop`).

Like the other ``test_gui_*`` modules these are source assertions on gui.html / gui.css / gui.js plus, where the
behaviour is a function, that function run in node (or JXA on macOS) through ``test_gui_model_score._function``.
The browser-level run (folder dialog stub -> scan -> roots list, the lost-connection box, the shutdown page) is the
desktop step of ``scripts/gui_smoke.py``. The server routes themselves are tested in ``test_desktop_mode``.
"""

from __future__ import annotations

import json
import re
import unittest

from deepfake_lens.tests.test_gui_model_score import _function, _js_engine
from deepfake_lens.tests.test_gui_rev7 import GUI_JS, _css, _html, _run_js, _source


def _element(html: str, element_id: str) -> str:
    """The opening tag of the element with ``id``."""
    match = re.search(r'<[a-z0-9]+[^>]*\bid="' + re.escape(element_id) + r'"[^>]*>', html)
    assert match is not None, element_id
    return match.group(0)


def _async_function(name: str) -> str:
    source = _source()
    start = source.index(f"        async function {name}(")
    return source[start:source.index("\n        }\n", start) + len("\n        }\n")]


def _handler(source: str, start_text: str) -> str:
    start = source.index(start_text)
    return source[start:source.index("\n        });\n", start)]


class Desk2TopBarAndShutdownTest(unittest.TestCase):
    """DESK-2: 「이 PC에서 실행 중」 and 종료 in the top bar (desktop only); 종료 asks, stops the server and leaves a page
    that says so with every control disabled; a refusal (409 while a scan runs) goes to the error region."""

    def test_the_bar_is_hidden_until_the_server_says_desktop(self) -> None:
        html = _html()
        bar = _element(html, "desk-bar")
        self.assertIn("hidden", bar)
        start = html.index('<div class="desk-bar" id="desk-bar" hidden>')
        self.assertLess(html.index('<header class="topbar">'), start)
        self.assertLess(start, html.index("</header>"))
        self.assertIn("이 PC에서 실행 중", html[start:html.index("</header>")])
        self.assertIn('<button class="btn btn-sm" id="shutdown-btn" type="button">종료</button>', html)
        helper = _function("applyDesktopStats")
        self.assertIn("desktopMode = d.desktop === true;", helper)
        self.assertLess(helper.index("if (!desktopMode) return;"), helper.index("$('desk-bar').hidden = false;"))
        self.assertIn("applyDesktopStats(d);", _source())

    def test_shutdown_asks_then_posts_and_says_a_refusal(self) -> None:
        handler = _handler(_source(), "$('shutdown-btn').addEventListener('click', async () => {")
        self.assertLess(handler.index("if (!window.confirm(SHUTDOWN_QUESTION)) return;"), handler.index("apiFetch('/api/shutdown', { method: 'POST' })"))
        self.assertIn("if (res.ok) { showShutdownPage(); return; }", handler)
        # 409 {error: 검사가 진행 중입니다 …}: the server's own text, in the error region (showError), not a toast
        self.assertIn("showError(await apiError(res), '종료하지 못했습니다');", handler)
        self.assertIn("setBusyButton(btn, true);", handler)

    def test_the_shutdown_page_is_an_alert_and_disables_everything(self) -> None:
        html = _html()
        self.assertIn('<div class="shutdown-page" id="shutdown-page" hidden>', html)
        self.assertIn('<div id="shutdown-alert" role="alert"></div>', html)
        self.assertLess(html.index("</main>"), html.index('id="shutdown-page"'))
        page = _function("showShutdownPage")
        self.assertIn("appShutDown = true;", page)
        self.assertIn("document.querySelectorAll('*').forEach(el => { if (FORM_CONTROLS.some(type => el instanceof type)) el.disabled = true; });", page)
        self.assertIn("const FORM_CONTROLS = [HTMLButtonElement, HTMLInputElement, HTMLSelectElement, HTMLTextAreaElement];", _source())
        self.assertIn("el.inert = true;", page)
        self.assertIn("head.textContent = '종료되었습니다. 이 탭을 닫으십시오.';", page)
        self.assertIn("next.textContent = `다시 쓰려면 바탕 화면의 ${PRODUCT}를 다시 실행하십시오.`;", page)
        # the product name is the top bar's (gui.js literals hold no two-word English name — test_korean_output)
        self.assertIn("const PRODUCT = document.querySelector('.brand-name').textContent.trim();", _source())
        self.assertIn('<span class="brand-name">Deepfake Lens</span>', _html())
        self.assertIn("$('shutdown-alert').replaceChildren(head, next);", page)
        self.assertIn("head.focus();", page)


class Desk3FolderPickerTest(unittest.TestCase):
    """DESK-3: 「검사할 폴더 고르기」 above the folder box (desktop only) — the OS dialog, the box filled and the scan
    started; the list of folders that can be scanned; the 허용되지 않은 경로 refusal names them."""

    def test_the_button_sits_above_the_box_and_its_note_describes_both(self) -> None:
        html = _html()
        self.assertIn("hidden", _element(html, "pick-row"))
        button = _element(html, "pick-folder-btn")
        self.assertIn('class="btn btn-primary btn-lg"', button)
        self.assertIn('aria-describedby="pick-note"', button)
        self.assertIn('<p class="hint pick-note" id="pick-note" role="status"></p>', html)
        self.assertLess(html.index('id="pick-row"'), html.index('id="folder-path"'))
        self.assertLess(html.index('<form id="scan-form"'), html.index('id="pick-row"'))
        describedby = re.search(r'aria-describedby="([^"]+)"', _element(html, "folder-path"))
        assert describedby is not None
        self.assertEqual(describedby.group(1).split(), ["pick-note", "folder-hint", "folder-roots"])
        self.assertIn("'pick-folder-btn'", _source()[_source().index("const SCAN_INPUTS = ["):][:200])

    def test_the_click_waits_fills_and_submits(self) -> None:
        handler = _handler(_source(), "$('pick-folder-btn').addEventListener('click', async () => {")
        self.assertIn("const PICK_WAIT = '폴더 선택 창이 떠 있습니다 — 보이지 않으면 작업 표시줄을 확인하십시오';", _source())
        order = ["setBusyButton(btn, true);", "setPickNote(PICK_WAIT, false);", "apiFetch('/api/pick-folder', { method: 'POST' })",
                 "setBusyButton(btn, false);", "refreshRoots();", "$('folder-path').value = outcome.folder;", "$('scan-form').requestSubmit();"]
        self.assertEqual([handler.index(step) for step in order], sorted(handler.index(step) for step in order))
        # cancel: nothing changes but the waiting note
        self.assertIn("if (outcome.kind === 'cancel') { setPickNote('', false); return; }", handler)
        # 501/409/400: the server's text in the box's hint, the box focused for 501 (type the path instead)
        self.assertIn("setPickNote(outcome.text, true);", handler)
        self.assertIn("if (outcome.status === 501) $('folder-path').focus();", handler)
        # the chosen folder is sent by its bytes
        submit = _handler(_source(), "$('scan-form').addEventListener('submit', async e => {")
        self.assertIn("} else if (pickedFolder && pickedFolder.b64 && folderPath === pickedFolder.folder) {", submit)
        self.assertIn("params.set('folder_b64', pickedFolder.b64);", submit)
        self.assertIn("const hint = deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode);", submit)

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_pick_outcomes(self) -> None:
        body = _function("pickOutcome") + (
            "var out = JSON.stringify(["
            "pickOutcome(200, {folder: '/srv/사건', folder_b64: 'L3Nydi8'}),"
            "pickOutcome(200, {folder: null, folder_b64: null}),"
            "pickOutcome(409, {error: '폴더 선택 창이 이미 열려 있습니다'}),"
            "pickOutcome(501, {error: '이 환경에서는 폴더 선택 창을 열 수 없습니다 — 경로를 직접 입력하십시오'}),"
            "pickOutcome(400, {error: '고른 경로가 폴더가 아닙니다: /x'}),"
            "pickOutcome(404, null)]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [
            {"kind": "picked", "folder": "/srv/사건", "b64": "L3Nydi8"},
            {"kind": "cancel"},
            {"kind": "error", "text": "폴더 선택 창이 이미 열려 있습니다", "status": 409},
            {"kind": "error", "text": "이 환경에서는 폴더 선택 창을 열 수 없습니다 — 경로를 직접 입력하십시오", "status": 501},
            {"kind": "error", "text": "고른 경로가 폴더가 아닙니다: /x", "status": 400},
            {"kind": "error", "text": "폴더 선택 창을 열지 못했습니다 (HTTP 404)", "status": 404},
        ])

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_roots_list_shows_five_and_counts_the_rest(self) -> None:
        roots = [f"/r/{n}" for n in range(7)]
        body = "var ROOTS_SHOWN = 5;\n" + _function("rootsView") + (
            f"var out = JSON.stringify([rootsView({json.dumps(roots)}, true), rootsView(['/a'], false), rootsView([], true), rootsView([], false)]);"
        )
        many, one, none_desktop, none_web = json.loads(_run_js(body))
        self.assertEqual(many["items"], roots[:5])
        self.assertEqual(many["more"], "외 2개")
        self.assertIn("검사할 폴더 고르기", many["head"])
        self.assertEqual((one["items"], one["more"]), (["/a"], ""))
        self.assertIn("--allow-root", one["head"])
        self.assertEqual(none_desktop["items"], [])
        self.assertTrue(none_desktop["head"].startswith("아직 검사할 수 있는 폴더가 없습니다"))
        self.assertEqual(none_web["head"], "서버를 시작할 때 등록한 폴더(--folder, --allow-root) 안만 검사할 수 있습니다.")

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_refusal_lists_the_registered_folders(self) -> None:
        body = "var ROOTS_SHOWN = 5;\n" + _function("rootsSummary") + _function("deniedFolderHint") + (
            "var out = JSON.stringify(["
            "deniedFolderHint('허용되지 않은 경로', '/srv/case', ['/srv/case', '/srv/b'], false),"
            "deniedFolderHint('허용되지 않은 경로', '', ['/1', '/2', '/3', '/4', '/5', '/6'], true),"
            "deniedFolderHint('허용되지 않은 경로', '', [], true),"
            "deniedFolderHint('허용되지 않은 경로', '/srv/case', [], false),"
            "deniedFolderHint('폴더를 찾을 수 없습니다', '/srv/case', ['/srv/case'], true)]);"
        )
        pick = ' 다른 폴더는 위의 "검사할 폴더 고르기"로 고르십시오.'
        self.assertEqual(json.loads(_run_js(body)), [
            " — 지금 검사할 수 있는 폴더(그 안의 폴더 포함): /srv/case, /srv/b.",
            " — 지금 검사할 수 있는 폴더(그 안의 폴더 포함): /1, /2, /3, /4, /5 외 1개." + pick,
            " — 아직 검사할 수 있는 폴더가 없습니다." + pick,
            # GUI-REV7 F-3's text when the list is not known (an older server) — unchanged
            " — 서버를 시작할 때 --folder 또는 --allow-root로 등록한 폴더 안만 검사할 수 있습니다. 이 서버의 기본 폴더: /srv/case",
            "",
        ])

    def test_the_list_is_filled_from_stats_and_after_a_pick(self) -> None:
        html = _html()
        self.assertIn('<div class="hint roots" id="folder-roots"><p id="roots-head">', html)
        self.assertIn('<ul class="roots-list mono" id="roots-list" hidden></ul>', html)
        helper = _function("applyDesktopStats")
        self.assertIn("readRoots = Array.isArray(d.roots) ? d.roots.filter(root => typeof root === 'string') : [];", helper)
        self.assertLess(helper.index("renderRoots();"), helper.index("if (!desktopMode) return;"))  # the plain server too
        refresh = _async_function("refreshRoots")
        self.assertIn("apiJson('/api/roots')", refresh)
        self.assertIn("readRoots = data.roots.map(root => root && root.path)", refresh)
        self.assertIn("li.textContent = displayName(text);", _function("renderRoots"))  # never innerHTML


class Desk4UploadBlockTest(unittest.TestCase):
    """DESK-4: the upload block says what it is (unsigned, no evidence statement exhibits) and is folded in the desktop
    mode, where the folder dialog is the way in."""

    def test_title_buttons_and_disclosure(self) -> None:
        html = _html()
        self.assertNotIn("폴더 대신 파일 올리기", html)
        self.assertIn('<details class="block upload-block" id="upload-block" open>', html)
        self.assertIn('<summary><h2 class="block-h" id="upload-h">파일 올려서 검사(서명 제외 — 감정서 불가)</h2></summary>', html)
        self.assertIn('<button class="btn" id="file-picker-btn" type="button">올릴 파일 고르기</button>', html)
        self.assertIn('<button class="btn" id="dir-picker-btn" type="button">올릴 폴더 고르기</button>', html)
        block = html[html.index('id="upload-block"'):html.index("</details>", html.index('id="upload-block"'))]
        for inner in ("drop-zone", "file-list", "upload-btn", "upload-clear"):
            self.assertIn(f'id="{inner}"', block)
        # the hint no longer says the uploads go into "the 감정서" — they are never exhibits of the statement
        self.assertIn("증거설명서의 서증이 될 수 없고", block)
        self.assertNotIn("감정서에는 서명 제외로 실립니다", block)

    def test_folded_only_in_the_desktop_mode_and_opened_for_an_outside_drop(self) -> None:
        helper = _function("applyDesktopStats")
        self.assertLess(helper.index("if (!desktopMode) return;"), helper.index("if (!selectedFiles.length) $('upload-block').open = false;"))
        drop = _handler(_source(), "document.addEventListener('drop', e => {")
        self.assertLess(drop.index("$('upload-block').open = true;"), drop.index("toast("))
        self.assertIn('"파일 올려서 검사" 칸에 놓으십시오', drop)
        self.assertIn(".upload-block > summary", _css())


class Desk5HeartbeatTest(unittest.TestCase):
    """DESK-5: every 30 s in the desktop mode; two failures in a row put a lasting box in the error region (not a
    scan's), a success removes it; nothing is sent after the shutdown."""

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_two_failures_in_a_row_and_a_success_clears(self) -> None:
        body = _function("heartbeatFailuresAfter") + _function("connectionLost") + (
            "var n = 0, seen = [];\n"
            "[false, false, false, true, false, true].forEach(function (ok) { n = heartbeatFailuresAfter(n, ok); seen.push(connectionLost(n)); });\n"
            "var out = JSON.stringify(seen);"
        )
        self.assertEqual(json.loads(_run_js(body)), [False, True, True, False, False, False])

    def test_the_box_is_lasting_and_not_a_scans(self) -> None:
        source = _source()
        self.assertIn("const HEARTBEAT_MS = 30000;", source)
        self.assertIn("const CONNECTION_LOST = `서버와 연결이 끊겼습니다 — 바탕 화면의 ${PRODUCT}를 다시 실행하십시오`;", source)
        box = _function("showConnectionLost")
        self.assertIn("box.id = 'conn-lost';", box)
        self.assertNotIn("dataset.scan", box)  # retireErrors / dropRetiredErrors never touch it
        self.assertNotIn("button", box)  # no 닫기: it stays until the server answers again
        self.assertIn("$('error-region').prepend(box);", box)
        self.assertIn("if (old) old.remove();", box)
        # the error region keeps at most four other boxes — the connection box is never the one trimmed
        self.assertIn("const trimmable = () => region.querySelectorAll('.err-item:not(#conn-lost)');", _function("showError"))
        beat = _async_function("sendHeartbeat")
        self.assertIn("apiFetch('/api/heartbeat', { method: 'POST', signal: controller.signal })", beat)
        self.assertIn("if (heartbeatInFlight || appShutDown) return;", beat)
        self.assertIn("showConnectionLost(connectionLost(heartbeatFailures));", beat)
        helper = _function("applyDesktopStats")
        self.assertLess(helper.index("if (!desktopMode) return;"), helper.index("startHeartbeat();"))
        self.assertIn("clearInterval(heartbeatTimer);", _function("stopHeartbeat"))
        self.assertIn("stopHeartbeat();", _function("showShutdownPage"))  # nothing is sent after the shutdown


class RiskA4B3HeartbeatOnReturnTest(unittest.TestCase):
    """RISK A4/B3 (docs/gui/risk_review_desktop_2026-10-23.md): a sleeping tab or a PC waking from sleep missed the 30 s
    timer and the idle server exited — the page sends a heartbeat the moment it is visible again (visibilitychange,
    focus, pageshow), besides the timer, one request at a time."""

    def test_the_three_events_call_it(self) -> None:
        source = _source()
        for line in ("document.addEventListener('visibilitychange', heartbeatOnReturn);",
                     "window.addEventListener('focus', heartbeatOnReturn);",
                     "window.addEventListener('pageshow', heartbeatOnReturn);"):
            self.assertIn(line, source)
        self.assertIn("const HEARTBEAT_MS = 30000;", source)  # the timer stays
        self.assertIn("heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS);", _function("startHeartbeat"))
        # one in flight: the immediate beat goes through the same guard as the timer's
        self.assertIn("if (heartbeatInFlight || appShutDown) return;", _async_function("sendHeartbeat"))

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_only_while_the_timer_runs_and_the_page_is_seen(self) -> None:
        body = _function("heartbeatOnReturn") + (
            "var sent = 0; function sendHeartbeat() { sent += 1; }\n"
            "var document = {visibilityState: 'visible'};\n"
            "var seen = [];\n"
            "var heartbeatTimer = null, appShutDown = false; heartbeatOnReturn(); seen.push(sent);\n"  # plain web server
            "heartbeatTimer = 7; heartbeatOnReturn(); seen.push(sent);\n"
            "document.visibilityState = 'hidden'; heartbeatOnReturn(); seen.push(sent);\n"
            "document.visibilityState = 'visible'; appShutDown = true; heartbeatOnReturn(); seen.push(sent);\n"
            "var out = JSON.stringify(seen);"
        )
        self.assertEqual(json.loads(_run_js(body)), [0, 1, 1, 1])


class Desk6StatusChipsTest(unittest.TestCase):
    """DESK-6: PDF and signing chips next to the version; the report checklist says where the signing key comes from."""

    def test_chips_sit_next_to_the_version(self) -> None:
        html = _html()
        version = html.index('id="ver-chip"')
        self.assertLess(version, html.index('id="pdf-chip"'))
        self.assertLess(html.index('id="pdf-chip"'), html.index('id="sign-chip"'))
        self.assertLess(html.index('id="sign-chip"'), html.index('<div class="case-line">'))
        for chip in ("pdf-chip", "sign-chip"):
            self.assertIn("hidden", _element(html, chip))
        self.assertIn("const signed = signingCheck(signingInfo);", _function("reportChecks"))

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_chip_texts(self) -> None:
        body = _function("statusChipTexts") + (
            "var out = JSON.stringify([statusChipTexts(true, {available: true}), statusChipTexts(false, {available: false}),"
            " statusChipTexts(null, null), statusChipTexts(true, {})]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [["PDF 가능", "서명 자동"], ["PDF 불가", "서명 없음"], ["", ""], ["PDF 가능", ""]])

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_checklist_line_names_the_key_source(self) -> None:
        body = _function("signingCheck") + (
            "var out = JSON.stringify(["
            "signingCheck({available: true, source: 'file', key_id: 'ab12'}), signingCheck({available: true, source: 'created', key_id: null}),"
            "signingCheck({available: true, source: 'env', key_id: 'cd34'}), signingCheck({available: false, source: 'none', key_id: null}),"
            "signingCheck({available: true, source: null, key_id: 'ef'}), signingCheck(null), signingCheck({})]);"
        )
        file_, created, env, none, other, absent, empty = json.loads(_run_js(body))
        self.assertEqual(file_, {"tag": "info", "text": "서명 키: 이 PC의 사용자 폴더(report-key) — 다른 PC에서는 검증되지 않음 (키 id ab12)"})
        self.assertEqual(created, {"tag": "info", "text": "서명 키: 이 PC의 사용자 폴더(report-key) — 다른 PC에서는 검증되지 않음"})
        self.assertEqual(env, {"tag": "info", "text": "서명 키: 환경 변수(DEEPFAKE_LENS_REPORT_KEY) (키 id cd34)"})
        self.assertEqual(none["tag"], "warn")
        self.assertIn("서명 없음", none["text"])
        self.assertEqual(other, {"tag": "info", "text": "서명 키: 있음 (키 id ef)"})
        self.assertIsNone(absent)
        self.assertIsNone(empty)

    def test_the_variable_name_matches_the_server(self) -> None:
        from deepfake_lens.signing import REPORT_KEY_ENV

        self.assertIn(f"서명 키: 환경 변수({REPORT_KEY_ENV})", _function("signingCheck"))


class Desk7EmptyStateTest(unittest.TestCase):
    """DESK-7: three first-use steps on the scan screen while there are no results."""

    def test_the_steps_stand_only_without_results(self) -> None:
        html = _html()
        guide = html[html.index('<ol class="scan-guide" id="scan-guide"'):html.index("</ol>", html.index('id="scan-guide"'))]
        self.assertIn('aria-label="처음 쓰는 순서" hidden', guide)
        for step in ("<b>폴더 고르기</b>", "<b>결과 확인</b>", "<b>감정서</b>"):
            self.assertIn(step, guide)
        self.assertLess(html.index('<section id="view-scan"'), html.index('id="scan-guide"'))
        self.assertLess(html.index('id="scan-guide"'), html.index('<div class="scan-grid">'))
        self.assertIn("$('scan-guide').hidden = has;", _function("renderResults"))
        helper = _function("applyDesktopStats")
        self.assertLess(helper.index("if (!desktopMode) return;"), helper.index("$('guide-step1').textContent"))


class DeskMarkupRulesTest(unittest.TestCase):
    """GUI-REV7 rules kept by the new markup: no inline style or script (CSP 'self'), the new routes exist."""

    def test_no_inline_style_or_script(self) -> None:
        html = _html()
        self.assertNotIn(" style=", html)
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])

    def test_the_routes_the_gui_calls_are_the_servers(self) -> None:
        from pathlib import Path

        server = (Path(GUI_JS).parent / "webapp.py").read_text(encoding="utf-8") + (Path(GUI_JS).parent / "desktop.py").read_text(encoding="utf-8")
        for route in ("/api/pick-folder", "/api/shutdown", "/api/heartbeat", "/api/roots"):
            with self.subTest(route=route):
                self.assertIn(route, _source())
                self.assertIn(route, server)


if __name__ == "__main__":
    unittest.main()
