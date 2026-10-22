"""GUI-REV7 (docs/gui/ux_ax_review_17b3a15.md §2): the GUI fixes of work package WP-A.

Each class names the review finding (F-n) it fixes. Like the other ``test_gui_*`` modules these are source
assertions on gui.html / gui.css / gui.js plus, where the behaviour is a function, that function run in node
(or JXA on macOS) through ``test_gui_model_score._function``. The browser-level checks (the skip link, the
forced-colors capture) are in ``scripts/gui_smoke.py`` and the commit bodies.
"""

from __future__ import annotations

import json
import re
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


class F4RetiredErrorsAreRemovedTest(unittest.TestCase):
    """F-4 (Med): an earlier scan's error box stayed above every screen after the next scan succeeded, and
    retireErrors rewrote its title inside the aria-live="assertive" region (the old error was read out again)."""

    def test_the_mark_is_an_aria_hidden_node_and_the_title_is_not_rewritten(self) -> None:
        source = _source()
        retire = _function("retireErrors")
        self.assertNotIn("head.textContent = '이전 검사 · ' + head.textContent;", source)
        self.assertNotIn("textContent =", retire.replace("tag.textContent = '이전 검사 · ';", ""))
        self.assertIn("tag.setAttribute('aria-hidden', 'true');", retire)
        self.assertIn('<div id="error-region" class="error-region" aria-live="assertive"></div>', _html())
        self.assertIn(".err-item.err-old .err-old-tag {", _css())

    def test_a_finished_scan_and_an_archive_check_drop_the_retired_errors(self) -> None:
        source = _source()
        finish = _function("progressFinish")
        self.assertIn("if (kind === 'done' || kind === 'cancelled') dropRetiredErrors();", finish)
        start = source.index("if (data.mode === 'files') {")
        branch = source[start:source.index("renderQuickCheck(data);", start)]
        self.assertLess(branch.index("processResults(data);"), branch.index("dropRetiredErrors();"))

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_only_retired_boxes_go_and_focus_inside_one_moves_to_main(self) -> None:
        body = (
            "var focused = '';\n"
            "function Box(id, old) { this.id = id; this.old = old; this.removed = false; }\n"
            "Box.prototype.remove = function () { this.removed = true; };\n"
            "Box.prototype.contains = function (el) { return el === this.id + ':close'; };\n"
            "var boxes = [new Box('a', true), new Box('b', false), new Box('c', true)];\n"
            "var document = {activeElement: 'c:close', querySelectorAll: function (sel) {\n"
            "  if (sel !== '#error-region .err-item.err-old') throw new Error(sel);\n"
            "  return boxes.filter(function (b) { return b.old; }); }};\n"
            "function $(id) { return {focus: function () { focused = id; }}; }\n"
            + _function("dropRetiredErrors")
            + "dropRetiredErrors();\n"
            "var out = JSON.stringify([boxes.map(function (b) { return b.removed; }), focused]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [[True, False, True], "main"])


class F5ViewResultsButtonIsNamedBackTest(unittest.TestCase):
    """F-5 (Med, false text): after one cancelled scan, the button of every later complete scan said
    「부분 결과 보기」 — progressFinish renamed it and nothing named it back."""

    def test_progress_start_names_the_button_back(self) -> None:
        self.assertIn('<button class="btn btn-primary" id="view-results-btn" type="button" hidden>결과 보기</button>', _html())
        self.assertIn("$('view-results-btn').textContent = '결과 보기';", _function("progressStart"))
        self.assertIn("$('view-results-btn').textContent = '부분 결과 보기';", _function("progressFinish"))

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_cancelled_then_done_says_results(self) -> None:
        stubs = (
            "var els = {};\nfunction $(id) { if (!els[id]) els[id] = {id: id, hidden: false, textContent: '', style: {},"
            " classList: {add: function () {}, remove: function () {}}, setAttribute: function () {}, removeAttribute: function () {},"
            " getAttribute: function () { return null; }}; return els[id]; }\n"
            "var document = {activeElement: null};\n"
            "var prog = {serverElapsed: 0, at: 0, done: 0, total: null, timer: null, quarter: 0, running: false};\n"
            "function setStatusText() {} function paintCounts() {} function paintTime() {} function announce() {}\n"
            "function setBusyButton() {} function elapsedNow() { return 0; } function focusSafely() {} function dropRetiredErrors() {}\n"
            "function partialResultNote() { return '부분'; }\nfunction setInterval() { return 1; } function clearInterval() {}\n"
        )
        body = stubs + _function("progressStart") + _function("progressFinish") + (
            "var got = [];\n"
            "progressStart('검사 중', true); progressFinish('cancelled', {items: [], summary: {}}); got.push($('view-results-btn').textContent);\n"
            "progressStart('검사 중', true); progressFinish('done', {items: [1]}); got.push($('view-results-btn').textContent);\n"
            "var out = JSON.stringify(got);"
        )
        self.assertEqual(json.loads(_run_js(body)), ["부분 결과 보기", "결과 보기"])


class F6ScanSubmitIsNotDoubledTest(unittest.TestCase):
    """F-6 (Med): 「검사 시작」 checked only scanBusy, which is set after /api/scan answers — a second Enter or a
    double click while the request was out started a second job."""

    def test_the_submit_handler_returns_while_the_button_is_busy(self) -> None:
        source = _source()
        start = source.index("$('scan-form').addEventListener('submit', async e => {")
        handler = source[start:source.index("\n        });\n", start)]
        guard = handler.index("if (scanBusy || isBusyButton($('analyze-btn'))) return;")
        busy = handler.index("setBusyButton($('analyze-btn'), true);")
        request = handler.index("await apiJson('/api/scan?'")
        self.assertLess(guard, busy)
        self.assertLess(busy, request)
        # nothing between the guard and the busy mark waits (the second event cannot slip in between)
        self.assertNotIn("await", handler[guard:busy])
        self.assertIn("setBusyButton($('analyze-btn'), false);", handler[request:])


class F7MaxFilesFollowsTheServerCeilingTest(unittest.TestCase):
    """F-7 (Med): 「최대 파일 수」 allowed up to 10000 while the server scans at most WEB_MAX_SCAN_FILES (2000; more is
    lowered without a word), the over-cap banner said to raise it, and 0 or an empty box reached the server
    (novalidate) — its 400 shown only at the top, not tied to the box."""

    def test_the_gui_ceiling_equals_the_servers(self) -> None:
        from deepfake_lens.analysis_api import WEB_MAX_SCAN_FILES

        tag = re.search(r'<input[^>]*id="max-files"[^>]*>', _html())
        assert tag is not None
        self.assertIn(f'max="{WEB_MAX_SCAN_FILES}"', tag.group(0))
        self.assertIn('min="1"', tag.group(0))
        self.assertIn(f"<span class=\"hint\">서버 상한 {WEB_MAX_SCAN_FILES}개</span>", _html())
        self.assertIn(f"const SERVER_MAX_SCAN_FILES = {WEB_MAX_SCAN_FILES};", _source())

    def test_the_banner_no_longer_says_raise_it_unconditionally(self) -> None:
        source = _source()
        self.assertNotIn("— '최대 파일 수'를 늘려 다시 검사하십시오.`", source)
        self.assertIn("${escapeHtml(overCapAdvice(lastScanOptions.max_files))}", _function("provenanceBannerHtml"))

    def test_the_submit_handler_stops_at_a_bad_value_next_to_the_box(self) -> None:
        source = _source()
        start = source.index("$('scan-form').addEventListener('submit', async e => {")
        handler = source[start:source.index("\n        });\n", start)]
        check = handler.index("const maxFilesNote = maxFilesProblem($('max-files').value);")
        self.assertLess(check, handler.index("setBusyButton($('analyze-btn'), true);"))
        self.assertIn("$('max-files').focus();", handler[check:handler.index("const params", check)])
        self.assertIn('<p class="field-error" id="max-files-err" hidden></p>', _html())
        show = _function("showMaxFilesProblem")
        self.assertIn("field.setAttribute('aria-invalid', 'true');", show)
        self.assertIn("field.setAttribute('aria-describedby', 'max-files-err');", show)

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_advice_and_problems(self) -> None:
        body = "var SERVER_MAX_SCAN_FILES = 2000;\n" + _function("overCapAdvice") + _function("maxFilesProblem") + (
            "var out = JSON.stringify({advice: [overCapAdvice('1000'), overCapAdvice('2000'), overCapAdvice(undefined)],"
            " problems: ['', '  ', '0', '-3', '1.5', 'abc', '1', '2000', '2001', '10000'].map(maxFilesProblem)});"
        )
        got = json.loads(_run_js(body))
        self.assertEqual(got["advice"], [
            "'최대 파일 수'(이번 검사 1000개)를 늘리거나 폴더를 나누어 다시 검사하십시오(서버 상한 2000개).",
            "서버 상한 2000개 — 폴더를 나누어 검사하십시오.",
            "서버 상한 2000개 — 폴더를 나누어 검사하십시오.",
        ])
        self.assertEqual(got["problems"], [
            "최대 파일 수를 입력하십시오(1~2000).", "최대 파일 수를 입력하십시오(1~2000).",
            "최대 파일 수는 1 이상이어야 합니다(서버 상한 2000개).", "최대 파일 수는 1 이상이어야 합니다(서버 상한 2000개).",
            "최대 파일 수는 1~2000 사이의 정수여야 합니다.", "최대 파일 수는 1~2000 사이의 정수여야 합니다.",
            "", "",
            "최대 파일 수는 서버 상한 2000개를 넘을 수 없습니다 — 폴더를 나누어 검사하십시오.",
            "최대 파일 수는 서버 상한 2000개를 넘을 수 없습니다 — 폴더를 나누어 검사하십시오.",
        ])


def _forced_colors_blocks(css: str) -> list[str]:
    """The bodies of every ``@media (forced-colors: active) { … }`` block of ``css``."""
    blocks = []
    for match in re.finditer(r"@media \(forced-colors: active\) \{", css):
        depth, index = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[index], 0)
            index += 1
        blocks.append(css[match.end():index - 1])
    return blocks


class F8ForcedColorsShowTheStatesTest(unittest.TestCase):
    """F-8 (Med): in a Windows contrast theme (forced colors) the current row, a pressed filter, a pressed review star,
    the progress fill, a changed row, the drop zone and the active studio mode were told only by backgrounds and
    box-shadows, which forced colors drop. Checked by a Playwright forced_colors="active" capture (commit body)."""

    def test_each_state_has_a_forced_colors_rule_that_is_not_a_background(self) -> None:
        css = _css()
        blocks = _forced_colors_blocks(css)
        self.assertGreaterEqual(len(blocks), 2)
        rules = blocks[-1]
        # the block comes after every rule it overrides (same specificity — source order decides)
        self.assertGreater(css.rindex("@media (forced-colors: active) {"), css.index(".row.row-changed { box-shadow"))
        wanted = {
            '.row[aria-current="true"]': "border-left: 6px solid Highlight",
            ".row.row-changed": "border-left: 6px dashed CanvasText",
            '.seg[aria-pressed="true"]': "outline: 3px solid Highlight",
            '.rev-star[aria-pressed="true"]': "border: 2px solid Highlight",
            ".progressbar .fill": "forced-color-adjust: none; background: Highlight",
            ".progressbar": "border: 1px solid CanvasText",
            ".drop.over": "border: 3px solid Highlight",
            ".studio-btn.active": "border: 2px solid Highlight",
        }
        for selector, declaration in wanted.items():
            with self.subTest(selector=selector):
                rule = re.search(re.escape(selector) + r" \{([^}]*)\}", rules)
                assert rule is not None, selector
                self.assertIn(declaration, rule.group(1))
        # the earlier block (verdict marks) is kept
        self.assertIn(".mk-m, .mk-a, .mk-x { background: CanvasText; forced-color-adjust: none; }", blocks[0])


class F9ExportNoteIsNotRereadWhileTypingTest(unittest.TestCase):
    """F-9 (Med): each keystroke in a case field re-rendered the report screen, rewrote the aria-live #export-note
    (read out again even with the same text) and sent a preview POST 250 ms after the key."""

    def test_render_report_leaves_the_note_alone_while_typing(self) -> None:
        source = _source()
        self.assertIn('<p class="hint" id="export-note" aria-live="polite"></p>', _html())
        render = _function("renderReport")
        self.assertNotIn("$('export-note').textContent =", render)
        self.assertIn("if (!typing) setExportNote([exportNote(sel), exportStickyNote].filter(Boolean).join(' '));", render)
        self.assertIn("renderA4(sel, typing ? 500 : 250);", render)
        self.assertIn("a4Timer = setTimeout(() => loadA4(seq), delay || 250);", _function("renderA4"))
        self.assertIn("if (currentView === 'report') renderReport(Boolean(event && event.type === 'input'));", source)
        self.assertIn("$(id).addEventListener('change', onCaseChange);", source)
        # no handler passes an event object as `typing`
        self.assertNotRegex(source, r"addEventListener\('[a-z]+', renderReport\)")

    @unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
    def test_the_note_is_written_only_when_its_text_changes(self) -> None:
        body = (
            "var writes = 0;\nvar note = {_t: '', get textContent() { return this._t; }, set textContent(v) { writes += 1; this._t = v; }};\n"
            "function $(id) { return note; }\n" + _function("setExportNote")
            + "setExportNote('a'); setExportNote('a'); setExportNote('b'); setExportNote('b');\n"
            "var out = JSON.stringify([writes, note.textContent]);"
        )
        self.assertEqual(json.loads(_run_js(body)), [2, "b"])


if __name__ == "__main__":
    unittest.main()
