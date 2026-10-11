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
        self.assertEqual(len(re.findall(r"\$\{modelScoreText\(m\)\}", source)), 2)
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


if __name__ == "__main__":
    unittest.main()
