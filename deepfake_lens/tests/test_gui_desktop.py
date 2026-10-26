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
        # RISK B17: 「프로그램 종료」 (「종료」 alone did not say what ends)
        self.assertIn('<button class="btn btn-sm" id="shutdown-btn" type="button" aria-label="프로그램 종료">프로그램 종료</button>', html)
        helper = _function("applyDesktopStats")
        self.assertIn("desktopMode = d.desktop === true;", helper)
        self.assertLess(helper.index("if (!desktopMode) return;"), helper.index("$('desk-bar').hidden = false;"))
        self.assertIn("applyDesktopStats(d);", _source())

    def test_shutdown_asks_then_posts_and_says_a_refusal(self) -> None:
        handler = _handler(_source(), "$('shutdown-btn').addEventListener('click', async e => {")
        self.assertLess(handler.index("const answer = window.confirm(SHUTDOWN_QUESTION);"), handler.index("apiFetch('/api/shutdown', { method: 'POST' })"))
        self.assertLess(handler.index("if (!answer) {"), handler.index("apiFetch('/api/shutdown', { method: 'POST' })"))
        no = handler[handler.index("if (!answer) {"):handler.index("setBusyButton(btn, true);")]
        self.assertIn("return;", no)
        self.assertNotIn("apiFetch", no)
        self.assertIn("if (res.ok) { showShutdownPage(); return; }", handler)
        # 409 {error: 검사가 진행 중입니다 …}: the server's own text, in the error region (showError), not a toast
        self.assertIn("showError(await apiError(res), '종료하지 못했습니다');", handler)
        self.assertIn("setBusyButton(btn, true);", handler)

    def test_the_shutdown_page_is_an_alert_and_disables_everything(self) -> None:
        html = _html()
        # REV2-D6: the page is the main landmark once the program has stopped (axe landmark-one-main)
        self.assertIn('<div class="shutdown-page" id="shutdown-page" role="main" hidden>', html)
        self.assertIn('<div id="shutdown-alert" role="alert"></div>', html)
        self.assertLess(html.index("</main>"), html.index('id="shutdown-page"'))
        page = _function("showShutdownPage")
        self.assertIn("appShutDown = true;", page)
        self.assertIn("document.querySelectorAll('*').forEach(el => { if (FORM_CONTROLS.some(type => el instanceof type)) el.disabled = true; });", page)
        self.assertIn("const FORM_CONTROLS = [HTMLButtonElement, HTMLInputElement, HTMLSelectElement, HTMLTextAreaElement];", _source())
        self.assertIn("el.inert = true;", page)
        # REV2-D2: the same page says "이미 종료되었습니다" when the server could not be reached at all
        self.assertIn("head.textContent = already ? ALREADY_SHUT_DOWN : '종료되었습니다. 이 탭을 닫으십시오.';", page)
        self.assertIn("next.textContent = `다시 쓰려면 ${LAUNCHER}를 다시 실행하십시오.`;", page)  # RISK B13
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
        # REV2-D5: the button and the box are described by the lasting note under the box too
        self.assertIn('aria-describedby="pick-note folder-note"', button)
        self.assertIn('<p class="hint pick-note" id="pick-note" role="status"></p>', html)
        self.assertLess(html.index('id="pick-row"'), html.index('id="folder-path"'))
        # REV2-D1 (was: inside the scan form, under the case fields — off-screen at 1366×768): the row is above
        # the case fields; see Rev2D1PickRowOnTopTest
        self.assertLess(html.index('id="pick-row"'), html.index('<form id="scan-form"'))
        describedby = re.search(r'aria-describedby="([^"]+)"', _element(html, "folder-path"))
        assert describedby is not None
        self.assertEqual(describedby.group(1).split(), ["pick-note", "folder-note", "folder-hint", "folder-roots"])
        self.assertIn("'pick-folder-btn'", _source()[_source().index("const SCAN_INPUTS = ["):][:200])

    def test_the_click_waits_fills_and_submits(self) -> None:
        handler = _handler(_source(), "$('pick-folder-btn').addEventListener('click', async () => {")
        # RISK B5: the owner window is not on the taskbar — Alt+Tab finds the dialog
        self.assertIn("const PICK_WAIT = \"폴더 선택 창을 여는 중입니다(처음에는 몇 초 걸릴 수 있습니다). 창이 안 보이면 Alt+Tab으로 '폴더 찾아보기' 창을 찾으십시오.\";", _source())
        self.assertNotIn("작업 표시줄", _source())
        order = ["setBusyButton(btn, true);", "setPickNote(PICK_WAIT, false);", "apiFetch('/api/pick-folder', { method: 'POST' })",
                 "setBusyButton(btn, false);", "refreshRoots();", "$('folder-path').value = outcome.folder;", "$('scan-form').requestSubmit();"]
        self.assertEqual([handler.index(step) for step in order], sorted(handler.index(step) for step in order))
        # cancel: nothing changes but the waiting note, which says the cancel (RISK B17: role=status reads it)
        self.assertIn("if (outcome.kind === 'cancel') { setPickNote(PICK_CANCELLED, false); return; }", handler)
        self.assertIn("const PICK_CANCELLED = '폴더 선택을 취소했습니다';", _source())
        # 501/409/400: the server's text in the box's hint, the box focused for 501 (type the path instead)
        # REV2-D5: the status line only points to it (the server's text is said once, under the box)
        self.assertIn("setPickNote(PICK_REFUSED, true);", handler)
        self.assertIn("setFolderNote(outcome.text, true, $('folder-path').value);", handler)
        self.assertIn("if (outcome.status === 501) $('folder-path').focus();", handler)
        # the chosen folder is sent by its bytes
        submit = _handler(_source(), "$('scan-form').addEventListener('submit', async e => {")
        self.assertIn("} else if (pickedFolder && pickedFolder.b64 && folderPath === pickedFolder.folder) {", submit)
        self.assertIn("params.set('folder_b64', pickedFolder.b64);", submit)
        self.assertIn("const hint = deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode);", submit)
        # REV2-D3: a folder seen registered before (a restarted server) gets the "pick it again" text
        self.assertIn("const said = earlier ? deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode, true) : hint;", submit)

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
            {"kind": "picked", "folder": "/srv/사건", "b64": "L3Nydi8", "note": ""},
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
        self.assertIn("const CONNECTION_LOST = `서버와 연결이 끊겼습니다 — ${LAUNCHER}를 다시 실행하십시오`;", source)  # RISK B13
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


class RiskB4B5B13B17Test(unittest.TestCase):
    """RISK B4 (a 403 for a scan already made says to pick the same folder again), B5 (the dialog's waiting text, the
    OneDrive note and the too-wide refusal kept under the path box), B13 (the launcher's real place), B17 (프로그램 종료,
    the cancel said, a blocked confirm explained)."""

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_a_refusal_for_an_earlier_scan_says_pick_it_again(self) -> None:
        body = "var ROOTS_SHOWN = 5;\n" + _function("rootsSummary") + _function("deniedFolderHint") + (
            "var out = JSON.stringify(["
            "deniedFolderHint('허용되지 않은 경로', '', ['/a'], true, true),"
            "deniedFolderHint('허용되지 않은 경로', '/srv/case', ['/srv/case'], false, true),"
            "deniedFolderHint('허용되지 않은 호스트입니다', '', [], true, true),"
            "deniedFolderHint('허용되지 않은 경로', '', ['/a'], true, false)]);"
        )
        desktop, web, host, new_scan = json.loads(_run_js(body))
        self.assertIn("같은 폴더를 '검사할 폴더 고르기'로 다시 고르십시오", desktop)
        self.assertIn("--folder 또는 --allow-root", web)
        self.assertEqual(host, "")
        self.assertTrue(new_scan.startswith(" — 지금 검사할 수 있는 폴더"))  # a new scan's refusal is unchanged

    def test_every_request_about_a_scan_already_made_uses_it(self) -> None:
        source = _source()
        helper = _async_function("deniedResponseHint")
        self.assertIn("if (res.status !== 403) return '';", helper)
        self.assertIn("await res.clone().text()", helper)
        self.assertIn("deniedFolderHint(text, defaultFolder, readRoots, desktopMode, true)", helper)
        for name in ("loadPreview", "loadHeatmap", "loadForensicStudio"):
            with self.subTest(name=name):
                self.assertIn("await deniedResponseHint(", _async_function(name))
        self.assertIn("deniedFolderHint(String(body.error || ''), defaultFolder, readRoots, desktopMode, true)", _async_function("loadA4"))
        self.assertIn("if (res.status === 403) message += deniedFolderHint(message, defaultFolder, readRoots, desktopMode, true);", source)
        self.assertIn("toast('검사 중 오류: ' + error.message + deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode, true)", source)
        # a new scan's refusal keeps naming the folders that can be scanned
        self.assertIn("const hint = deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode);", source)

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_onedrive_note_is_read(self) -> None:
        note = "OneDrive 폴더입니다 — 온라인 전용 파일은 내려받으면서 검사됩니다"
        body = _function("pickOutcome") + (
            f"var out = JSON.stringify([pickOutcome(200, {{folder: '/u/OneDrive/증거', folder_b64: 'x', note: {json.dumps(note)}}}),"
            " pickOutcome(200, {folder: '/u/a', folder_b64: 'y', note: null}),"
            " pickOutcome(400, {error: '너무 넓은 폴더입니다(사용자 폴더 전체) — 증거 파일이 든 폴더를 고르십시오'})]);"
        )
        onedrive, plain, wide = json.loads(_run_js(body))
        self.assertEqual(onedrive["note"], note)
        self.assertEqual(plain["note"], "")
        self.assertEqual(wide, {"kind": "error", "text": "너무 넓은 폴더입니다(사용자 폴더 전체) — 증거 파일이 든 폴더를 고르십시오", "status": 400})

    def test_the_note_and_the_refusal_stay_under_the_path_box(self) -> None:
        html = _html()
        self.assertIn('<p class="hint pick-note" id="folder-note" hidden></p>', html)
        self.assertLess(html.index('id="folder-path"'), html.index('id="folder-note"'))
        self.assertLess(html.index('id="folder-note"'), html.index('id="folder-hint"'))
        handler = _handler(_source(), "$('pick-folder-btn').addEventListener('click', async () => {")
        # REV2-D8 (was: the note cleared when the dialog opened — a cancelled pick lost it): the next answer replaces it
        self.assertNotIn("setFolderNote('', false, null);", handler)
        self.assertIn("setFolderNote(outcome.text, true, $('folder-path').value);", handler)
        self.assertIn("if (outcome.status === 400) btn.focus();", handler)
        # REV2-D5 (was: setPickNote(outcome.note, false) — the same sentence twice): the status line points to the note
        self.assertIn("setPickNote(outcome.note ? PICK_NOTED : '', false);", handler)
        self.assertLess(handler.index("$('folder-path').value = outcome.folder;"), handler.index("setFolderNote(outcome.note, false, outcome.folder);"))
        shown = _function("showFolderNote")
        self.assertIn("if (note.textContent !== text) note.textContent = text;", shown)  # never innerHTML
        self.assertIn("note.hidden = !shown;", shown)
        # REV2-D8: hidden while the box names another folder, back when it names that folder again
        self.assertIn("$('folder-path').addEventListener('input', showFolderNote);", _source())

    def test_no_desktop_shortcut_is_named(self) -> None:
        source = _source()
        self.assertNotIn("바탕 화면", source)
        self.assertIn("const LAUNCHER = `압축을 푼 폴더의 '${PRODUCT}.cmd'(macOS: '${PRODUCT}.command')`;", source)
        self.assertIn("다시 쓰려면 ${LAUNCHER}를 다시 실행하십시오.`;", source.split("const SHUTDOWN_QUESTION = ", 1)[1].split("\n", 1)[0])

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_a_confirm_answered_at_once_after_a_click_is_blocked(self) -> None:
        body = "var CONFIRM_BLOCKED_MS = 50;\n" + _function("confirmLooksBlocked") + (
            "var out = JSON.stringify([confirmLooksBlocked(false, 3, true), confirmLooksBlocked(false, 900, true),"
            " confirmLooksBlocked(true, 3, true), confirmLooksBlocked(false, 3, false)]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [True, False, False, False])
        source = _source()
        self.assertIn("const CONFIRM_BLOCKED_MS = 50;", source)
        self.assertIn("const CONFIRM_BLOCKED = '확인 창이 차단되어 있습니다 — 브라우저 설정에서 이 사이트의 대화상자 차단을 해제하거나 콘솔 창을 닫아 종료하십시오';", source)
        handler = _handler(source, "$('shutdown-btn').addEventListener('click', async e => {")
        self.assertLess(handler.index("const asked = performance.now();"), handler.index("window.confirm(SHUTDOWN_QUESTION)"))
        self.assertIn("if (confirmLooksBlocked(answer, performance.now() - asked, e.isTrusted) && !$('confirm-blocked')) {", handler)
        # lasting: the error region (until closed), said once
        self.assertIn("showError(CONFIRM_BLOCKED, '종료하지 못했습니다').id = 'confirm-blocked';", handler)
        self.assertIn("return box;", _function("showError"))


class RiskB25ImeEnterTest(unittest.TestCase):
    """RISK B25: the Enter that ends a Korean IME composition (isComposing, keyCode 229) must not submit a form — the
    last syllable was lost and the scan could start twice."""

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_guard(self) -> None:
        body = _function("isImeEnter") + (
            "var out = JSON.stringify(["
            "isImeEnter({key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229}),"
            "isImeEnter({key: 'Process', code: 'Enter', isComposing: true, keyCode: 229}),"
            "isImeEnter({key: 'Enter', code: 'Enter', isComposing: false, keyCode: 229}),"  # Safari, after compositionend
            "isImeEnter({key: 'Enter', code: 'NumpadEnter', isComposing: true, keyCode: 13}),"
            "isImeEnter({key: 'Enter', code: 'Enter', isComposing: false, keyCode: 13}),"  # a plain Enter submits
            "isImeEnter({key: 'a', code: 'KeyA', isComposing: true, keyCode: 229})]);"  # typing is never blocked
        )
        self.assertEqual(json.loads(_run_js(body)), [True, True, True, True, False, False])

    def test_every_form_ignores_it(self) -> None:
        source = _source()
        forms = re.findall(r'<form\b[^>]*\bid="([^"]+)"', _html())
        self.assertEqual(sorted(forms), ["scan-form", "token-form"])
        for form in forms:
            with self.subTest(form=form):
                self.assertIn(f"$('{form}').addEventListener('keydown', e => {{ if (isImeEnter(e)) e.preventDefault(); }});", source)
        # a keydown handler that acts on Enter itself must check the guard too — today only the guard reads Enter
        for match in re.finditer(r"addEventListener\('keydown', (?:async )?e => \{", source):
            handler = source[match.start():source.index("\n        });", match.start())]
            if "Enter" in handler:
                with self.subTest(handler=handler[:80]):
                    self.assertIn("isImeEnter(e)", handler)
        self.assertNotIn("Enter'", source.replace(_function("isImeEnter"), ""), "an Enter action outside isImeEnter")


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


def _run_node_async(body: str) -> str:
    """Run ``body`` in node; it ends with a promise ``done`` whose value is printed (JXA has no event loop)."""
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rev2.js"
        path.write_text(body + "\ndone.then(v => console.log(v), e => { console.error(e); process.exit(1); });", encoding="utf-8")
        run = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    if run.returncode != 0:
        raise AssertionError(run.stderr)
    return run.stdout.strip()


def _has_node() -> bool:
    import shutil

    return shutil.which("node") is not None


class Rev2D1PickRowOnTopTest(unittest.TestCase):
    """REV2-D1 (High, second hands-on review): 「검사할 폴더 고르기」 stood under the whole case-information block (y≈827
    at 1366×768 — off-screen). It now stands under the first-use steps, before the case fields (DOM and tab order);
    the list of folders that can be scanned is moved next to it in the desktop mode (applyDesktopStats). The browser
    check of "visible without scrolling" at 1366×768 and 1280×720 is in scripts/gui_smoke.py."""

    def test_the_row_precedes_the_case_fields(self) -> None:
        html = _html()
        row = html.index('id="pick-row"')
        self.assertLess(html.index('id="scan-guide"'), row)
        self.assertLess(row, html.index('id="case-slot-scan"'))
        self.assertLess(row, html.index('id="scan-h"'))
        self.assertLess(html.index('id="pick-folder-btn"'), html.index('id="case-no"'))  # tab order: the button first
        self.assertLess(row, html.index('<div class="scan-grid">'))
        # the path box stays in the scan form, under the case fields
        self.assertLess(html.index('id="case-slot-scan"'), html.index('id="folder-path"'))
        self.assertIn("hidden", _element(html, "pick-row"))
        self.assertNotIn("tabindex", _element(html, "pick-folder-btn"))

    def test_the_roots_list_goes_with_it_in_the_desktop_mode(self) -> None:
        helper = _function("applyDesktopStats")
        self.assertLess(helper.index("if (!desktopMode) return;"), helper.index("$('pick-row').appendChild($('folder-roots'));"))
        self.assertLess(helper.index("$('pick-row').hidden = false;"), helper.index("$('pick-row').appendChild($('folder-roots'));"))
        self.assertIn(".pick-row { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin: 0 0 24px; }", _css())


class Rev2D2ConnectionLostAtOnceTest(unittest.TestCase):
    """REV2-D2 (High): after the server died every button said 「deepfake-lens web 프로세스가 실행 중인지 확인하세요」
    and 「프로그램 종료」 said 「종료하지 못했습니다」. In the desktop mode a request that cannot reach the server puts
    the lost-connection box up at once and says CONNECTION_LOST; 프로그램 종료 then shows "이미 종료되었습니다"."""

    def test_source(self) -> None:
        fetch = _async_function("apiFetch")
        self.assertIn("if (desktopMode && !(e && e.name === 'AbortError')) {", fetch)
        self.assertIn("markConnectionLost();", fetch)
        self.assertIn("const lost = new Error(CONNECTION_LOST);", fetch)
        self.assertIn("lost.connectionLost = true;", fetch)
        # the plain web server keeps its text
        self.assertIn("throw new Error('서버에 연결할 수 없습니다 — deepfake-lens web 프로세스가 실행 중인지 확인하세요');", fetch)
        mark = _function("markConnectionLost")
        self.assertIn("heartbeatFailures = Math.max(heartbeatFailures, 2);", mark)
        self.assertIn("showConnectionLost(true);", mark)
        self.assertIn("if (appShutDown) return;", mark)
        handler = _handler(_source(), "$('shutdown-btn').addEventListener('click', async e => {")
        self.assertIn("if (e && e.connectionLost) { showShutdownPage(true); return; }", handler)
        self.assertIn("const ALREADY_SHUT_DOWN = '이미 종료되었습니다 — 이 탭을 닫으십시오.';", _source())

    @unittest.skipUnless(_has_node(), "node is needed for the async run")
    def test_a_failed_request_in_each_mode(self) -> None:
        body = (
            "var CONNECTION_LOST = '서버와 연결이 끊겼습니다 — 다시 실행하십시오';\n"
            "var marked = 0, beats = 0, heartbeatFailures = 0, desktopMode = true;\n"
            "function markConnectionLost() { marked += 1; heartbeatFailures = 2; }\n"
            "function connectionLost(n) { return n >= 2; }\n"
            "function sendHeartbeat() { beats += 1; }\n"
            "var sessionStorage = { getItem: () => '' };\n"
            "var answer = 'down';\n"
            "async function fetch() { if (answer === 'down') throw new TypeError('Failed to fetch');"
            " if (answer === 'abort') { const e = new Error('aborted'); e.name = 'AbortError'; throw e; } return { status: 200, ok: true }; }\n"
            + _async_function("apiFetch") +
            "async function tryIt() { try { await apiFetch('/api/pick-folder'); return 'ok'; } catch (e) { return [e.message, e.connectionLost === true]; } }\n"
            "var done = (async () => {\n"
            "  const seen = [];\n"
            "  seen.push(await tryIt(), marked);\n"
            "  answer = 'abort'; seen.push(await tryIt(), marked);\n"
            "  answer = 'up'; seen.push(await tryIt(), beats);\n"  # an answer while the box is up: the heartbeat checks
            "  desktopMode = false; answer = 'down'; seen.push(await tryIt(), marked);\n"
            "  return JSON.stringify(seen);\n"
            "})();"
        )
        seen = json.loads(_run_node_async(body))
        self.assertEqual(seen[0], ["서버와 연결이 끊겼습니다 — 다시 실행하십시오", True])
        self.assertEqual(seen[1], 1)
        self.assertEqual(seen[2][1], False)  # an abort is not a lost connection
        self.assertEqual(seen[3], 1)
        self.assertEqual(seen[4:6], ["ok", 1])
        self.assertEqual(seen[6], ["서버에 연결할 수 없습니다 — deepfake-lens web 프로세스가 실행 중인지 확인하세요", False])
        self.assertEqual(seen[7], 1)


class Rev2D3RefreshAfterRestartTest(unittest.TestCase):
    """REV2-D3 (Med): after a restart on the same port the list of folders was stale and the 403 named the old folder
    without "같은 폴더를 다시 고르십시오". The heartbeat's recovery reads /api/stats again; a scan-start 403 in the
    desktop mode reads the list again and says "pick it again" for a folder this page saw registered."""

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_heartbeat_step(self) -> None:
        body = _function("heartbeatFailuresAfter") + _function("connectionLost") + _function("heartbeatStep") + (
            "var out = JSON.stringify([heartbeatStep(0, true), heartbeatStep(1, true), heartbeatStep(2, true), heartbeatStep(3, false), heartbeatStep(0, false)]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [
            {"failures": 0, "back": False, "said": False},
            {"failures": 0, "back": True, "said": False},
            {"failures": 0, "back": True, "said": True},
            {"failures": 4, "back": False, "said": False},
            {"failures": 1, "back": False, "said": False},
        ])

    def test_recovery_reads_the_stats_again(self) -> None:
        beat = _async_function("sendHeartbeat")
        self.assertIn("const step = heartbeatStep(heartbeatFailures, ok);", beat)
        self.assertIn("if (step.back) serverIsBack(step.said);", beat)
        back = _async_function("serverIsBack")
        self.assertIn("if (said) toast(RECONNECTED);", back)
        self.assertIn("if ($('pick-note').textContent === CONNECTION_LOST) setPickNote('', false);", back)
        self.assertIn("apiJson('/api/stats')", back)
        self.assertLess(back.index("readRoots = d.roots.filter(root => typeof root === 'string');"), back.index("renderRoots();"))
        self.assertIn("renderStatusChips();", back)

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_folder_under_roots(self) -> None:
        body = _function("folderUnderRoots") + (
            "var out = JSON.stringify(["
            "folderUnderRoots('/srv/사건', ['/srv/사건']), folderUnderRoots('/srv/사건/a', ['/srv/사건/']),"
            "folderUnderRoots('/srv/사건2', ['/srv/사건']), folderUnderRoots('C:\\\\Evidence\\\\x', ['c:\\\\evidence']),"
            "folderUnderRoots('/', ['/']), folderUnderRoots('/a', ['/']), folderUnderRoots('/a', [])]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [True, True, False, True, True, True, False])

    def test_a_scan_start_refusal_reads_the_list_first(self) -> None:
        submit = _handler(_source(), "$('scan-form').addEventListener('submit', async e => {")
        catch = submit[submit.index("} catch (error) {"):]
        self.assertIn("if (desktopMode && /허용되지 않은 경로/.test(error.message)) {", catch)
        self.assertLess(catch.index("await refreshRoots();"), catch.index("const hint = deniedFolderHint("))
        self.assertIn("earlier = folderUnderRoots(folderPath, Array.from(seenRoots)) && !folderUnderRoots(folderPath, readRoots);", catch)
        # every list the page reads is remembered
        self.assertIn("rememberRoots(readRoots);", _function("applyDesktopStats"))
        self.assertIn("rememberRoots(readRoots);", _async_function("refreshRoots"))
        self.assertIn("rememberRoots(readRoots);", _async_function("serverIsBack"))


class Rev2D4ScanStartFailureSeenTest(unittest.TestCase):
    """REV2-D4 (Med): a failed 「검사 시작」 only added a box in the error region at the top (off-screen). The box is
    scrolled into view when it is outside the viewport and a one-line summary is written under the path box."""

    def test_source(self) -> None:
        helper = _function("scanStartFailed")
        self.assertIn("const box = showError(text, '', true);", helper)
        self.assertIn("setFolderNote(summary, true, folder);", helper)
        self.assertIn("if (!boxInView(box.getBoundingClientRect(), window.innerHeight)) box.scrollIntoView({ block: 'nearest' });", helper)
        submit = _handler(_source(), "$('scan-form').addEventListener('submit', async e => {")
        self.assertIn("scanStartFailed('검사를 시작하지 못했습니다: ' + error.message + said, '검사를 시작하지 못했습니다: ' + error.message, folderPath);", submit)
        self.assertIn("if (job.error) { scanStartFailed(", submit)
        self.assertNotIn("toast('검사를 시작하지 못했습니다: '", submit)
        # a refusal said before goes once a scan starts; a OneDrive caution (not an error) stays
        self.assertIn("if (folderNote.isError) setFolderNote('', false, '');", submit)

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_box_in_view(self) -> None:
        body = _function("boxInView") + (
            "var out = JSON.stringify([boxInView({top: 10, bottom: 60}, 768), boxInView({top: -40, bottom: 10}, 768), boxInView({top: 700, bottom: 800}, 768)]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [True, False, False])


class Rev2D5OneLastingNoteTest(unittest.TestCase):
    """REV2-D5 (Low/Med): the same sentence stood in #pick-note and #folder-note. The lasting text is in #folder-note
    only; #pick-note is a short status."""

    def test_the_status_line_is_short(self) -> None:
        source = _source()
        self.assertIn("const PICK_REFUSED = '폴더를 쓸 수 없습니다 — 아래 안내를 보십시오';", source)
        self.assertIn("const PICK_NOTED = '폴더를 골랐습니다 — 아래 안내를 보십시오';", source)
        handler = _handler(source, "$('pick-folder-btn').addEventListener('click', async () => {")
        # the server's text reaches the status line only when there is no server (the lost-connection words)
        self.assertEqual(handler.count("setPickNote(outcome.text, true);"), 1)
        self.assertIn("if (outcome.status === 0) { setPickNote(outcome.text, true); return; }", handler)
        self.assertNotIn("setPickNote(outcome.note, false);", handler)


class Rev2D6ShutdownPageTest(unittest.TestCase):
    """REV2-D6 (Low): the shutdown page names itself in the tab and is the main landmark."""

    def test_title_and_role(self) -> None:
        self.assertIn("const SHUT_DOWN_TITLE = `종료됨 — ${PRODUCT}`;", _source())
        page = _function("showShutdownPage")
        self.assertLess(page.index("$('shutdown-alert').replaceChildren(head, next);"), page.index("document.title = SHUT_DOWN_TITLE;"))
        self.assertIn('role="main"', _element(_html(), "shutdown-page"))
        self.assertEqual(_html().count("<main"), 1)


class Rev2D7RefusalAndReconnectTest(unittest.TestCase):
    """REV2-D7 (Low): the 409 「검사가 진행 중입니다」 box of 프로그램 종료 goes when the scan ends; the recovery is
    announced in #toasts."""

    def test_source(self) -> None:
        handler = _handler(_source(), "$('shutdown-btn').addEventListener('click', async e => {")
        self.assertIn("const refusal = showError(await apiError(res), '종료하지 못했습니다');", handler)
        self.assertIn("if (res.status === 409) refusal.id = 'shutdown-refused';", handler)
        self.assertIn("dropShutdownRefusal();", handler)  # a second try replaces it
        busy = _function("setBusy")
        self.assertIn("if (!busy) dropShutdownRefusal();", busy)
        drop = _function("dropShutdownRefusal")
        self.assertIn("const box = $('shutdown-refused');", drop)
        self.assertIn("if (hadFocus) $('main').focus();", drop)
        self.assertIn("const RECONNECTED = '서버와 다시 연결되었습니다';", _source())
        self.assertIn('<div id="toasts" role="status" aria-live="polite"></div>', _html())


class Rev2D8NoteFollowsTheBoxTest(unittest.TestCase):
    """REV2-D8 (Low): the OneDrive note went on any input event and a cancelled pick lost it. It is hidden only while
    the box names another folder and comes back when the box names that folder again."""

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_shown_for_its_folder_only(self) -> None:
        body = _function("folderNoteShown") + (
            "var note = {text: 'OneDrive 폴더입니다', isError: false, folder: '/u/OneDrive/증거'};\n"
            "var out = JSON.stringify([folderNoteShown(note, '/u/OneDrive/증거'), folderNoteShown(note, '/u/OneDrive/증'),"
            " folderNoteShown(note, ' /u/OneDrive/증거 '), folderNoteShown({text: '', isError: false, folder: '/a'}, '/a'),"
            " folderNoteShown({text: '폴더 경로를 입력하세요', isError: true, folder: ''}, '')]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [True, False, True, False, True])

    def test_a_cancel_keeps_it(self) -> None:
        handler = _handler(_source(), "$('pick-folder-btn').addEventListener('click', async () => {")
        cancel = handler[handler.index("if (outcome.kind === 'cancel')"):]
        cancel = cancel[:cancel.index("\n")]
        self.assertNotIn("setFolderNote", cancel)
        self.assertNotIn("setFolderNote", handler[:handler.index("apiFetch('/api/pick-folder'")])
        self.assertIn("folderNote = { text: text || '', isError: Boolean(isError), folder: typeof folder === 'string' ? folder.trim() : '' };", _function("setFolderNote"))


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
