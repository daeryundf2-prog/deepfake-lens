"""GUI-REV7 (docs/gui/ux_ax_review_17b3a15.md §2): the GUI fixes of work package WP-A.

Each class names the review finding (F-n) it fixes. Like the other ``test_gui_*`` modules these are source
assertions on gui.html / gui.css / gui.js plus, where the behaviour is a function, that function run in node
(or JXA on macOS) through ``test_gui_model_score._function``. The browser-level checks (the skip link, the
forced-colors capture) are in ``scripts/gui_smoke.py`` and the commit bodies.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.tests.test_gui_model_score import GUI_JS, _function, _js_engine

GUI_DIR = GUI_JS.parent
GUI_HTML = GUI_DIR / "gui.html"
GUI_CSS = GUI_DIR / "gui.css"


def _source() -> str:
    return GUI_JS.read_text(encoding="utf-8")


def _html() -> str:
    return GUI_HTML.read_text(encoding="utf-8")


def _css() -> str:
    return GUI_CSS.read_text(encoding="utf-8")


def _run_js(body: str) -> str:
    """Run ``body`` (which sets ``var out``, a string) in node or JXA and return what it printed."""
    engine = _js_engine()
    assert engine is not None
    script = f"{body}\nconsole.log(out);" if engine[0] == "node" else f"function run() {{\n{body}\nreturn out;\n}}"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rev7.js"
        path.write_text(script, encoding="utf-8")
        done = subprocess.run([*engine, str(path)], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


class F1SkipLinkKeepsTheScreenTest(unittest.TestCase):
    """F-1 (High): 「본문으로 건너뛰기」 (href="#main") fired hashchange, and the handler showed
    ``viewFromHash() || (results.length ? 'ledger' : 'scan')`` — the report screen was hidden."""

    def test_a_hash_that_names_no_screen_changes_nothing(self) -> None:
        source = _source()
        self.assertNotIn("showView(viewFromHash() || (results.length ? 'ledger' : 'scan'), pendingFocus);", source)
        start = source.index("window.addEventListener('hashchange', () => {")
        handler = source[start:source.index("});", start)]
        self.assertIn("if (!view) return;", handler)
        self.assertIn("showView(view, focusTarget);", handler)
        # the queued focus is dropped whatever the hash, so a later screen change does not inherit it
        self.assertLess(handler.index("pendingFocus = null;"), handler.index("if (!view) return;"))

    def test_the_skip_link_focuses_main_without_a_hash_change(self) -> None:
        source = _source()
        self.assertIn('<a class="skip-link" href="#main">', _html())
        start = source.index("document.querySelector('.skip-link').addEventListener('click', event => {")
        handler = source[start:source.index("});", start)]
        self.assertIn("event.preventDefault();", handler)
        self.assertIn("$('main').focus();", handler)
        self.assertIn('<main id="main" tabindex="-1">', _html())

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_view_from_hash_names_no_screen_for_main_and_empty(self) -> None:
        body = "var location = {hash: ''};\nvar VIEWS = ['scan', 'ledger', 'report', 'tools'];\n" + _function("viewFromHash")
        body += "var out = ['#main', '', '#/', '#/report', '#/nope'].map(function (h) { location.hash = h; return String(viewFromHash()); }).join(',');"
        self.assertEqual(_run_js(body), "null,null,null,report,null")


if __name__ == "__main__":
    unittest.main()
