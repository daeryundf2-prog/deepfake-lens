"""DESK-2..7 (docs/gui/plan_desktop_mode.md §4): the GUI of the desktop mode (`deepfake-lens desktop`).

Like the other ``test_gui_*`` modules these are source assertions on gui.html / gui.css / gui.js plus, where the
behaviour is a function, that function run in node (or JXA on macOS) through ``test_gui_model_score._function``.
The browser-level run (folder dialog stub -> scan -> roots list, the lost-connection box, the shutdown page) is the
desktop step of ``scripts/gui_smoke.py``. The server routes themselves are tested in ``test_desktop_mode``.
"""

from __future__ import annotations

import re
import unittest

from deepfake_lens.tests.test_gui_model_score import _function
from deepfake_lens.tests.test_gui_rev7 import _html, _source


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


if __name__ == "__main__":
    unittest.main()
