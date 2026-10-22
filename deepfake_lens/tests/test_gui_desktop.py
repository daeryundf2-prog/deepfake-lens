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
from deepfake_lens.tests.test_gui_rev7 import _html, _run_js, _source


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


if __name__ == "__main__":
    unittest.main()
