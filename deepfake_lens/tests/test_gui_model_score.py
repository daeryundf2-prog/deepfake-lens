"""R18-17 (round 18): the GUI shows no score for a model that did not run.

``model_analysis {available: false, score: 0}`` was shown "외부 모델
원점수(미보정, 결론 불참여) — 0" (gui.js result card and layer view): an
unmeasured 0 that reads like a measurement (G1, QA-OUT-5 "확률 없음"). It is
now "없음 — 미실행(<사유>)"; a member that did not run says the same.

R19-10 (round 19): the layer diagnostics (style/fingerprint, metadata/C2PA)
said "원점수 0" when ``raw_score`` was null — the same unmeasured 0. They say
"원점수 없음" now (rawScoreText).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

GUI_JS = Path(__file__).resolve().parents[1] / "gui.js"


def _function(name: str) -> str:
    source = GUI_JS.read_text(encoding="utf-8")
    start = source.index(f"        function {name}(")
    end = source.index("\n        }\n", start) + len("\n        }\n")
    return source[start:end]


class ModelScoreSourceTest(unittest.TestCase):
    def test_every_model_score_goes_through_the_helper(self) -> None:
        source = GUI_JS.read_text(encoding="utf-8")
        helper = _function("modelScoreText")
        self.assertIn("entry.available === false", helper)
        self.assertIn("없음 — 미실행(", helper)
        self.assertEqual(source.count("외부 모델 원점수(미보정, 결론 불참여) — ${modelScoreText(ma)}"), 2)
        # GUI redesign (U-10): members are rendered by one helper, modelMembersHtml, used by
        # both views (evidence panel and quick check); members that share one score text are
        # listed once — the text still comes from modelScoreText(m) for every member.
        self.assertEqual(len(re.findall(r"\$\{modelScoreText\(m\)\}", source)), 1)
        self.assertIn("const texts = members.map(m => modelScoreText(m));", source)
        self.assertEqual(source.count("? modelMembersHtml(ma) :"), 2)
        self.assertNotIn("${ma.score != null ? ma.score : '없음'}", source)
        self.assertNotIn("(사용 불가)", source)


class LayerRawScoreSourceTest(unittest.TestCase):
    def test_a_missing_layer_score_is_not_zero(self) -> None:
        source = GUI_JS.read_text(encoding="utf-8")
        self.assertNotRegex(source, r"raw_score != null \? \w+\.raw_score : 0")
        self.assertIn("계층 진단(참고 신호 · 미측정) — 원점수 ${rawScoreText(a.raw_score)}", source)
        self.assertIn("계층 진단(참고 신호 · 미측정) — 원점수 ${rawScoreText(fr.raw_score)}", source)
        self.assertIn("function rawScoreText(value) { return value != null ? String(value) : '없음'; }", source)


def _js_engine() -> list[str] | None:
    if shutil.which("node"):
        return ["node"]
    if shutil.which("osascript"):
        return ["osascript", "-l", "JavaScript"]
    return None


@unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
class ModelScoreBehaviourTest(unittest.TestCase):
    def test_a_model_that_did_not_run_has_no_score(self) -> None:
        engine = _js_engine()
        assert engine is not None
        body = (
            "function shown(x) { return String(x); }\nfunction escapeHtml(x) { return String(x); }\n" + _function("modelScoreText")
            + "var out = [modelScoreText({available: false, score: 0, detail: '모델 프로필 미지정'}),"
            " modelScoreText({available: true, score: 0}), modelScoreText({available: true, score: 73}), modelScoreText({available: false, score: 0})];\n"
        )
        script = f"{body}console.log(out.join('\\n'));" if engine[0] == "node" else f"function run() {{\n{body}return out.join('\\n');\n}}"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "score.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([*engine, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip().splitlines(), ["없음 — 미실행(모델 프로필 미지정)", "0", "73", "없음 — 미실행(사유 기록 없음)"])


    def test_raw_score_text(self) -> None:
        engine = _js_engine()
        assert engine is not None
        line = next(line for line in GUI_JS.read_text(encoding="utf-8").splitlines() if "function rawScoreText(" in line)
        body = line + "\nvar out = [rawScoreText(null), rawScoreText(undefined), rawScoreText(0), rawScoreText(12)];\n"
        script = f"{body}console.log(out.join('\\n'));" if engine[0] == "node" else f"function run() {{\n{body}return out.join('\\n');\n}}"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([*engine, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip().splitlines(), ["없음", "없음", "0", "12"])


    def test_an_unmeasured_comparison_shows_no_score_and_no_distance(self) -> None:
        # R30-4 (round 30): a comparison whose side had no audio features showed 「0」 and 「거리 1」 (the server sent 0 and
        # 1.0); it now sends null and the card says 「없음」 for both. A measured one still shows its numbers.
        engine = _js_engine()
        assert engine is not None
        line = next(line for line in GUI_JS.read_text(encoding="utf-8").splitlines() if "function rawScoreText(" in line)
        stubs = (
            "function shown(x) { return String(x); }\nfunction escapeHtml(x) { return String(x); }\n"
            "function provenanceNoteHtml() { return ''; }\nfunction layer(t, h) { return '[' + t + ']' + h; }\n"
            "function listItems(t, xs) { return '[' + t + ']' + (xs || []).join('|'); }\n"
            "var box = {innerHTML: ''}; function $(id) { return box; }\n" + line + "\n"
        )
        # GUI-REV7 F-12: the large number is shown only for a comparison the server marks measured (measured: true);
        # an unmeasured one leads with 「참고 신호 — 미측정」 and keeps the number in a sentence. The head is drawn by
        # compareHeadHtml and notices pass through guiText — both extracted with renderCompare. The R30-4 payloads are
        # kept; the measured one now says measured: true, and the same numbers unmeasured are checked too.
        body = (
            stubs + _function("guiText") + _function("compareHeadHtml") + _function("renderCompare")
            + "var out = [];\n"
            "renderCompare({raw_score: null, reference_band: 'unavailable', reference_note: '파일 B에서 오디오 특징을 추출하지 못해',"
            " diagnostic: {kind: 'speaker', method: 'mfcc', distance: null, limitations: []}}); out.push(box.innerHTML);\n"
            "renderCompare({raw_score: 42, measured: true, reference_band: 'reference', diagnostic: {kind: 'speaker', method: 'mfcc', distance: 0.123,"
            " limitations: []}}); out.push(box.innerHTML);\n"
            "renderCompare({raw_score: null, reference_band: 'unavailable',"
            " diagnostic: {kind: 'speaker', method: 'mfcc', distance: null, limitations: []}}); out.push(box.innerHTML);\n"
            "renderCompare({raw_score: 42, reference_band: 'reference', diagnostic: {kind: 'speaker', method: 'mfcc', distance: 0.123,"
            " limitations: []}}); out.push(box.innerHTML);\n"
        )
        script = f"{body}console.log(out.join('\\n=====\\n'));" if engine[0] == "node" else f"function run() {{\n{body}return out.join('\\n=====\\n');\n}}"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compare.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([*engine, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        unmeasured, measured, unmeasured_no_note, unmeasured_42 = done.stdout.strip().split("\n=====\n")
        self.assertIn("파일 B에서 오디오 특징을 추출하지 못해", unmeasured)
        self.assertIn("<b>거리</b><span>없음 — 측정하지 않음</span>", unmeasured)
        self.assertNotIn('<span class="big">0</span>', unmeasured)
        self.assertNotIn("원점수 0", unmeasured)
        self.assertIn("참고 원점수 없음 — 미측정", unmeasured)
        self.assertIn("참고 원점수 없음 — 미측정", unmeasured_no_note)
        self.assertIn("<b>거리</b><span>없음 — 측정하지 않음</span>", unmeasured_no_note)
        self.assertNotIn("원점수 0", unmeasured_no_note)
        self.assertIn('<span class="big">42</span>', measured)
        self.assertIn("<b>거리</b><span>0.123</span>", measured)
        self.assertIn("참고 원점수 42/100 — 미측정", unmeasured_42)
        self.assertIn("<b>거리</b><span>0.123</span>", unmeasured_42)
        for card in (unmeasured, unmeasured_no_note, unmeasured_42):
            self.assertNotIn('class="big"', card)
            self.assertIn("참고 신호 — 미측정", card)

if __name__ == "__main__":
    unittest.main()
