"""GUI-REV7 (docs/gui/ux_ax_review_17b3a15.md §2): the GUI fixes of work package WP-A.

Each class names the review finding (F-n) it fixes. Like the other ``test_gui_*`` modules these are source
assertions on gui.html / gui.css / gui.js plus, where the behaviour is a function, that function run in node
(or JXA on macOS) through ``test_gui_model_score._function``. The browser-level checks (the skip link, the
forced-colors capture) are in ``scripts/gui_smoke.py`` and the commit bodies.
"""

from __future__ import annotations

import json
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


class F2ArchiveQuickCheckAsksBeforeReplacingTest(unittest.TestCase):
    """F-2 (High): a quick check of an archive replaced a folder scan's rows (and unlinked its server job, so no
    evidence statement could be made) without asking, under a hint that read like the rows were added."""

    def test_the_hint_says_the_rows_are_replaced(self) -> None:
        html = _html()
        self.assertNotIn("안의 파일이 사건 대장에 행으로 실립니다.", html)
        self.assertIn("압축 파일을 고르면 지금 보이는 검사 결과 대신 안의 파일을 사건 대장에 행으로 바꿔 싣습니다(폴더 검사 결과는 지워짐 — 그 전에 묻습니다).", html)

    def test_the_archive_branch_asks_before_it_touches_the_rows(self) -> None:
        source = _source()
        start = source.index("if (data.mode === 'files') {")
        branch = source[start:source.index("renderQuickCheck(data);", start)]
        ask = branch.index("const question = archiveReplaceQuestion(results.length, lastScanJob);")
        self.assertIn("if (question && !window.confirm(question)) {", branch)
        # nothing on screen or in the job link changes before the answer
        for change in ("lastScanRoot = '';", "lastScanJob = '';", "retireErrors();", "processResults(data);"):
            self.assertLess(ask, branch.index(change), change)
        refused = branch[ask:branch.index("return;", ask)]
        self.assertIn("압축 파일의 결과를 싣지 않았습니다 — 지금 보이는 폴더 검사 결과를 그대로 둡니다.", refused)
        self.assertNotIn("processResults", refused)

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_only_a_folder_scan_on_screen_is_asked_about(self) -> None:
        body = _function("archiveReplaceQuestion")
        body += "var out = JSON.stringify([archiveReplaceQuestion(0, ''), archiveReplaceQuestion(3, ''), archiveReplaceQuestion(0, 'job'), archiveReplaceQuestion(51, 'job')]);"
        self.assertEqual(json.loads(_run_js(body)), [
            "", "", "",
            "지금 보이는 검사 결과 51개를 이 압축 파일의 결과로 바꿉니다. 바꾼 뒤에는 폴더를 다시 검사하기 전까지 증거설명서를 만들 수 없습니다. 바꿀까요?",
        ])


class F3DefaultFolderDoesNotOverwriteTypingTest(unittest.TestCase):
    """F-3 (Med): the /api/stats answer wrote the server's folder into the folder box whenever it was empty —
    also while the examiner had clicked into it and started typing; the mixed path was refused (403) with no
    word on which folder is allowed."""

    def test_the_stats_answer_goes_through_the_helper(self) -> None:
        source = _source()
        self.assertNotIn("if (!$('folder-path').value) $('folder-path').value = d.default_folder;", source)
        self.assertIn("applyDefaultFolder(d.default_folder, typeof d.default_folder_b64 === 'string' ? d.default_folder_b64 : '');", source)
        self.assertIn("const hint = deniedFolderHint(error.message, defaultFolder);", source)

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_a_focused_or_filled_box_is_left_alone(self) -> None:
        body = (
            "var defaultFolder = '', defaultFolderB64 = '';\n"
            "var field = {value: ''};\nvar document = {activeElement: null};\n"
            "function $(id) { return field; }\n" + _function("applyDefaultFolder")
            + "var got = [];\n"
            "field.value = ''; document.activeElement = field; applyDefaultFolder('/srv/case', 'L3Nydi9jYXNl'); got.push(field.value);\n"
            "document.activeElement = null; field.value = '/mine'; applyDefaultFolder('/srv/case', 'x'); got.push(field.value);\n"
            "field.value = ''; applyDefaultFolder('/srv/case', 'x'); got.push(field.value);\n"
            "got.push(defaultFolder + '|' + defaultFolderB64);\n"
            "var out = JSON.stringify(got);"
        )
        # the remembered default is set in every case (the submit handler sends its bytes when the box holds it)
        self.assertEqual(json.loads(_run_js(body)), ["", "/mine", "/srv/case", "/srv/case|x"])

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_refusal_names_the_servers_folder(self) -> None:
        body = _function("deniedFolderHint") + (
            "var out = JSON.stringify([deniedFolderHint('허용되지 않은 경로', '/srv/case'), deniedFolderHint('허용되지 않은 경로', ''),"
            " deniedFolderHint('폴더를 찾을 수 없습니다', '/srv/case')]);"
        )
        base = " — 서버를 시작할 때 --folder 또는 --allow-root로 등록한 폴더 안만 검사할 수 있습니다."
        self.assertEqual(json.loads(_run_js(body)), [base + " 이 서버의 기본 폴더: /srv/case", base, ""])


if __name__ == "__main__":
    unittest.main()
