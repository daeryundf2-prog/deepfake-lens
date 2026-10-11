"""GUI redesign: the ledger's one-line reasons, grouped coverage reasons and
environment notice (gui.js), and the accessibility invariants of the page.

The ledger shows one line per file and the evidence panel collapses detail;
nothing may be dropped on the way (spec rules on honest coverage reasons):

* a row's reason names its decisive evidence, its failed checks, the
  reference-only grade or the scope that kept detection off (U-10), and a
  row that was not analyzed says why — 미지원 is not 처리 실패 (U-9);
* skipped checks that share one reason are listed once with every check
  name (the per-model measurement-gate sentence was repeated per model);
* model members whose score text differs only by their own name are listed
  once — each member's text still comes from ``modelScoreText`` (R18-17);
* the environment notice is derived from the scan rows (measurement-gated
  detectors, a decisive check that could not run for a missing dependency,
  U-6).

The suite has no JavaScript engine in the reference environment's main
venv; where ``node`` is present the helpers are run. Source checks pin the
accessibility rules (A11Y-4/5/6) and the U-7 polling rule.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
GUI_JS = PACKAGE / "gui.js"


def _section(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end)]


def _function(source: str, name: str) -> str:
    start = source.index(f"        function {name}(")
    end = source.index("\n        }\n", start) + len("\n        }\n")
    return source[start:end]


def _helpers() -> str:
    source = GUI_JS.read_text(encoding="utf-8")
    return "\n".join([
        "var results = []; var lastProvenance = { coverage: null, thresholds: null, summary: null }; var changedPaths = new Set();",
        _section(source, "        function displayName(value)", "        /* ── verdict vocabulary"),
        _function(source, "modelScoreText"),
        _function(source, "partialResultNote"),
        _section(source, "        /* ── verdict vocabulary", "        /* ── views"),
        _function(source, "evItemHtml"),
        _function(source, "undeterminedCounts"),
        _function(source, "undeterminedSplitText"),
        _section(source, "        // Evidence-bearing rows first", "        function filteredResults("),
        _function(source, "skippedHtml"),
    ])


def _run(body: str) -> object:
    node = shutil.which("node")
    assert node is not None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.js"
        path.write_text(_helpers() + "\n" + body, encoding="utf-8")
        done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


GATE = "0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — 2차 계획 측정 단계 전까지 비활성."
MODELS = ["Swin-large 생성 이미지 탐지기(umm-maybe)", "AIDE 생성 이미지 탐지기(ICLR 2025, progan_train)", "SD-Turbo 생성 이미지 탐지기"]


def _row(verdict: str = "undetermined", status: str = "analyzed", **result: object) -> dict[str, object]:
    base: dict[str, object] = {"verdict_code": verdict, "grade": "evidence", "evidence": [], "coverage": [], "limitations": []}
    base.update(result)
    return {"path": "a.png", "name": "a.png", "kind": "image", "status": status, "result": base}


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class LedgerReasonTest(unittest.TestCase):
    def test_reasons_and_short_verdicts(self) -> None:
        rows = {
            "manip": _row("manipulation_evidence", evidence=[
                {"kind": "deterministic", "direction": "neutral", "strength": "weak", "title": "이미지 유형: 저해상도", "detail": ""},
                {"kind": "deterministic", "direction": "synthetic", "strength": "moderate", "title": "약한 기록", "detail": "x"},
                {"kind": "deterministic", "direction": "synthetic", "strength": "strong", "title": "생성 도구 메타데이터", "detail": "A1111 파라미터"},
            ]),
            "failed": _row(coverage=[
                {"check": "metadata", "status": "failed", "reason": "AnalyzerError: 잘림"},
                {"check": "c2pa", "status": "failed", "reason": "AnalyzerError: 해석 불가"},
                {"check": "image_class", "status": "ran"},
            ]),
            "text": {**_row(grade="reference"), "kind": "text"},
            "scope": _row(coverage=[{"check": "pixel", "status": "skipped", "reason": "사진 아님: pattern"}]),
            # GUI-REV G-2: "plain" has no record evidence at all — only then is it "근거 없음".
            "plain": _row(),
            # G-2: a 판단 불가 row that carries record evidence below a conclusion is never "근거 없음".
            "cue": _row(evidence=[{"kind": "deterministic", "direction": "synthetic", "strength": "moderate",
                                   "title": "생성 도구 메타데이터 단서", "detail": "Description: Midjourney v6"}],
                        coverage=[{"check": "pixel", "status": "skipped", "reason": "측정 범위 밖: 해상도 64x64"}]),
            # G-3: a decisive check that did not run leads ahead of the scope reason.
            "c2pa_missing": _row(coverage=[{"check": "c2pa", "status": "skipped", "reason": "의존성 부재: c2pa"},
                                           {"check": "pixel", "status": "skipped", "reason": "측정 범위 밖: 해상도 32x32"}]),
            "cue_failed": _row(evidence=[{"kind": "deterministic", "direction": "synthetic", "strength": "moderate",
                                          "title": "생성 도구 메타데이터 단서", "detail": ""}],
                               coverage=[{"check": "metadata", "status": "failed", "reason": "잘림"}]),
            "reference_cue": _row(grade="reference", evidence=[{"kind": "deterministic", "direction": "synthetic", "strength": "moderate",
                                                                "title": "문서 메타데이터의 AI 도구명", "detail": ""}]),
            "c2pa_unverified": _row(evidence=[{"kind": "deterministic", "direction": "neutral", "strength": "weak", "layer": "c2pa",
                                               "title": "C2PA 매니페스트 존재(검증 미완료)", "detail": ""}],
                                    coverage=[{"check": "pixel", "status": "skipped", "reason": "측정 범위 밖: 해상도 32x32"}]),
            "unsupported": {"path": "n.xyz", "name": "n.xyz", "kind": "unsupported", "status": "unsupported", "result": None, "error": "지원 형식이 아닙니다."},
            "failed_row": {"path": "f.png", "name": "f.png", "kind": "image", "status": "failed", "result": None, "error": None},
        }
        rows["text"]["result"]["grade"] = "reference"  # type: ignore[index]
        out = _run(
            f"var rows = {json.dumps(rows, ensure_ascii=False)};\n"
            "var out = {}; for (var k in rows) out[k] = [rowReason(rows[k]), shortVerdict(rows[k]), verdictLine(rows[k]), limitAdvice(rows[k])];\n"
            "console.log(JSON.stringify(out));"
        )
        assert isinstance(out, dict)
        self.assertEqual(out["manip"][0], "생성 도구 메타데이터 — A1111 파라미터")
        self.assertEqual(out["manip"][1], "조작·생성 근거 있음")
        self.assertEqual(out["failed"][0], "검사 실패 — 메타데이터, C2PA 출처 검증")
        self.assertEqual(out["failed"][1], "판단 불가 — 검사 실패")
        self.assertEqual(out["text"][0], "참고 전용 — 텍스트 생성 판별은 결론을 내지 않음")
        self.assertEqual(out["scope"][0], "근거 없음 · 사진 아님: pattern")
        self.assertEqual(out["plain"][0], "근거 없음 — 원본이라는 뜻 아님")
        self.assertIn("원본", out["plain"][0])
        # GUI-REV2 H-6: the short verdict counts the cues ("단서 1건", was "단서만 있음").
        self.assertEqual(out["cue"][:3], ["결론에 못 미친 근거 1건 — 생성 도구 메타데이터 단서", "판단 불가 — 단서 1건",
                                          "기록에 단서가 있지만 결론을 낼 만큼은 아닙니다."])
        self.assertNotIn("근거 없음", out["cue"][0] + out["cue"][1])
        self.assertEqual(out["c2pa_missing"][0], "결정 검사 미실행 — C2PA 출처 검증: 의존성 부재: c2pa")
        self.assertEqual(out["c2pa_missing"][2], "결정 검사를 실행하지 못해 결론을 내리지 않았습니다.")
        # GUI-REV2 H-6 (G-3 residual): an unverified C2PA record is its own kind, never "근거 없음".
        self.assertEqual(out["c2pa_unverified"][:3], ["확인 안 된 기록 — C2PA 매니페스트 존재(검증 미완료)", "판단 불가 — 확인 안 된 기록",
                                                      "서명을 확인하지 못한 출처 기록이 있습니다 — 결론을 내리지 않았습니다."])
        # H-6: a cue is never hidden behind a failure or the reference grade.
        self.assertEqual(out["cue_failed"][:3], ["검사 실패 — 메타데이터 · 결론에 못 미친 근거 1건 — 생성 도구 메타데이터 단서",
                                                 "판단 불가 — 단서 1건 · 검사 실패", "단서가 있지만 검사가 실패해 결론을 내리지 않았습니다."])
        self.assertEqual(out["reference_cue"][:2], ["결론에 못 미친 근거 1건 — 문서 메타데이터의 AI 도구명", "판단 불가 — 단서 1건 · 참고 전용"])
        self.assertIn("조작·생성되었다는 뜻도", out["cue"][3])
        self.assertNotIn("근거가 없다", out["cue"][3])
        # U-9: not analyzed rows say why, and 미지원 is not 처리 실패.
        self.assertEqual(out["unsupported"][:2], ["지원 형식이 아닙니다.", "분석 못 함 — 미지원 형식"])
        self.assertEqual(out["failed_row"][1], "분석 못 함 — 처리 실패")
        self.assertNotEqual(out["unsupported"][2], out["failed_row"][2])

    def test_headline_split_and_cue_rows_sort_before_rows_without_evidence(self) -> None:
        """GUI-REV G-2/G-3: 판단 불가 counts by kind; cue rows sort ahead of evidence-less ones."""
        cue = _row(evidence=[{"kind": "deterministic", "direction": "synthetic", "strength": "moderate", "title": "단서", "detail": ""}])
        cue["path"] = "z-cue.png"
        failed = _row(coverage=[{"check": "metadata", "status": "failed", "reason": "잘림"}])
        failed["path"] = "b-failed.png"
        text = {**_row(grade="reference"), "path": "c.txt"}
        plain = {**_row(), "path": "a-plain.png"}
        rows = [plain, text, failed, cue]
        out = _run(
            f"results = {json.dumps(rows, ensure_ascii=False)}.map(function (item) {{ return {{item: item}}; }});\n"
            "var order = results.slice().sort(byVerdict).map(function (e) { return e.item.path; });\n"
            "console.log(JSON.stringify([undeterminedCounts(), order]));"
        )
        assert isinstance(out, list)
        self.assertEqual(out[0], {"cue": 1, "c2pa": 0, "none": 1, "failed": 1, "reference": 1})
        self.assertEqual(out[1], ["z-cue.png", "b-failed.png", "a-plain.png", "c.txt"])

    def test_split_order_and_overlap_match_the_limit_line(self) -> None:
        """GUI-REV3 J-9: the headline split is in the ledger's order and says the cue/failed overlap."""
        cue_failed = _row(evidence=[{"kind": "deterministic", "direction": "synthetic", "strength": "moderate", "title": "단서", "detail": ""}],
                          coverage=[{"check": "metadata", "status": "failed", "reason": "잘림"}])
        failed = _row(coverage=[{"check": "metadata", "status": "failed", "reason": "잘림"}])
        plain = _row()
        out = _run(
            f"results = {json.dumps([plain, failed, cue_failed], ensure_ascii=False)}.map(function (item) {{ return {{item: item}}; }});\n"
            "console.log(JSON.stringify([undeterminedSplitText(), reportLimitLines(), results.slice().sort(byVerdict).map(function (e) { return undeterminedKind(e.item); })]));"
        )
        assert isinstance(out, list)
        self.assertEqual(out[0], "단서 있음 1(검사 실패가 겹친 1개 포함) · 검사 실패 1 · 근거 없음 1")
        self.assertIn("검사가 실패한 파일 2개(그중 단서가 있는 1개) — 해당 파일은 판단 불가", out[1])
        self.assertEqual(out[2], ["cue", "failed", "none"])

    def test_report_limits_come_from_the_scan(self) -> None:
        """GUI-REV G-9(c): the preview never says "특이 사항 없음" while thresholds are in-sample."""
        out = _run(
            f"results = [{{item: {json.dumps(_row(), ensure_ascii=False)}}}];\n"
            "lastProvenance.coverage = {weights_total: 0, weights_available: 1};\n"
            "lastProvenance.thresholds = {in_sample: true};\n"
            "var inSample = reportLimitLines();\n"
            "lastProvenance.thresholds = {};\n"
            "console.log(JSON.stringify([inSample, reportLimitLines()]));"
        )
        assert isinstance(out, list)
        self.assertEqual(out[0], ["판정 임계값은 적합에 쓴 같은 표본에서 평가된 값(in-sample)이라 감정 근거가 아님"])
        self.assertEqual(out[1], ["기록된 검사 한계 없음 — 결론은 파일에 남은 기록에만 근거함"])
        self.assertNotIn("특이 사항 없음", json.dumps(out, ensure_ascii=False))

    def test_a_duplicate_shows_its_originals_conclusion(self) -> None:
        """GUI-REV3 J-2: a dedupe duplicate of a manipulated file is never just "중복"."""
        original = _row("manipulation_evidence", evidence=[{"kind": "deterministic", "direction": "synthetic", "strength": "strong", "title": "생성 도구 메타데이터", "detail": ""}])
        original["path"] = "a_dalle.jpg"
        duplicate = {"path": "b_dalle_copy.jpg", "name": "b_dalle_copy.jpg", "kind": "image", "status": "duplicate", "duplicate_of": "a_dalle.jpg", "result": None}
        out = _run(
            f"results = {json.dumps([original, duplicate], ensure_ascii=False)}.map(function (item) {{ return {{item: item}}; }});\n"
            "console.log(JSON.stringify([shortVerdict(results[1].item), rowReason(results[1].item), verdictOf(results[1].item)]));"
        )
        self.assertEqual(out, ["분석 못 함 — 중복 · 원본: 조작·생성 근거 있음",
                               "같은 내용의 파일: a_dalle.jpg — 원본 결론: 조작·생성 근거 있음", "other"])

    def test_skipped_checks_are_grouped_by_reason_and_every_name_stays(self) -> None:
        coverage = [{"check": f"model:{name}", "status": "skipped", "reason": f"모델 실행 불가: {name}: {GATE}"} for name in MODELS]
        coverage += [{"check": check, "status": "skipped", "reason": "비활성화(심층 신호 검사를 켜지 않음)"} for check in ("face_manipulation", "inpaint")]
        coverage += [{"check": "metadata", "status": "ran"}]
        html = _run(f"console.log(JSON.stringify(skippedHtml({{coverage: {json.dumps(coverage, ensure_ascii=False)}}})));")
        assert isinstance(html, str)
        self.assertEqual(html.count("측정 게이트"), 1, html)
        self.assertIn("신경망 탐지기 3종", html)
        for name in MODELS:
            self.assertIn(name, html)
        self.assertIn("검사 2개", html)
        self.assertIn("얼굴 검사, 인페인팅 검사", html)
        self.assertIn("실행한 검사: 메타데이터", html)

    def test_model_members_sharing_a_reason_are_listed_once(self) -> None:
        same = {"available": False, "models": [{"display_name": name, "available": False, "detail": f"{name}: {GATE}"} for name in MODELS]}
        mixed = {"available": True, "models": [{"display_name": "A", "available": True, "score": 12}, {"display_name": "B", "available": False, "detail": "없음"}]}
        out = _run(
            f"console.log(JSON.stringify([modelMembersHtml({json.dumps(same, ensure_ascii=False)}), modelMembersHtml({json.dumps(mixed)})]));"
        )
        assert isinstance(out, list)
        self.assertEqual(out[0].count("측정 게이트"), 1, out[0])
        self.assertIn("3종 모두 원점수 없음 — 미실행(", out[0])
        for name in MODELS:
            self.assertIn(name, out[0])
        self.assertIn("A — 원점수 12", out[1])
        self.assertIn("B — 원점수 없음 — 미실행(없음)", out[1])

    def test_environment_notice_is_derived_from_the_rows(self) -> None:
        rows = [
            _row(coverage=[{"check": f"model:{name}", "status": "skipped", "reason": f"모델 실행 불가: {name}: {GATE}"} for name in MODELS]
                 + [{"check": "c2pa", "status": "skipped", "reason": "의존성 부재: c2pa"}]),
            _row(coverage=[{"check": "c2pa", "status": "skipped", "reason": "의존성 부재: c2pa"}]),
        ]
        out = _run(
            f"results = {json.dumps(rows, ensure_ascii=False)}.map(function (item) {{ return {{item: item}}; }});\n"
            "lastProvenance.coverage = {weights_total: 0, weights_available: 0};\n"
            "console.log(JSON.stringify(envFacts()));"
        )
        assert isinstance(out, dict)
        self.assertEqual(sorted(out["gated"]), sorted(MODELS))
        self.assertEqual(out["missing"], [["c2pa", 2]])
        self.assertIn("신경망 탐지기 3종은 측정 기준에 못 미쳐 꺼져 있습니다", out["short"])
        self.assertIn("C2PA 출처 검증 미설치 — 2개 파일에서 실행 못 함", out["short"])
        self.assertIn("결론은 파일에 남은 기록만으로 냈습니다", out["short"])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ReviewMarkRowsTest(unittest.TestCase):
    def test_a_mark_shows_on_every_row_with_the_same_content(self) -> None:
        """GUI-REV G-8: two rows with one sha256 in one folder share the mark."""
        source = GUI_JS.read_text(encoding="utf-8")
        key_block = source[source.index("        const REVIEW_KEY_VERSION"):source.index("        let reviewPushTimer")]
        script = (
            "var lastScanRoot = '/case'; var reviewStore = {};\n" + key_block + _function(source, "refreshReviewMarks")
            + "function row() { var cell = {textContent: ''}; return {cell: cell, querySelector: function () { return cell; }}; }\n"
            + "var sha = 'a'.repeat(64), other = 'b'.repeat(64);\n"
            + "var results = [{item: {path: 'x/1.png', sha256: sha}, row: row()}, {item: {path: 'y/1.png', sha256: sha}, row: row()},"
            + " {item: {path: 'z.png', sha256: other}, row: row()}];\n"
            + "reviewStore[itemKey(results[0].item)] = {star: true}; refreshReviewMarks(itemKey(results[0].item));\n"
            + "var on = results.map(function (e) { return e.row.cell.textContent; });\n"
            + "reviewStore[itemKey(results[0].item)] = {star: false}; refreshReviewMarks(itemKey(results[0].item));\n"
            + "console.log(JSON.stringify([on, results.map(function (e) { return e.row.cell.textContent; })]));"
        )
        node = shutil.which("node")
        assert node is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marks.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [["검토함", "검토함", ""], ["", "", ""]])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class CaseFolderTest(unittest.TestCase):
    def test_case_fields_of_another_folder_need_a_confirmation(self) -> None:
        """GUI-REV2 H-8: kept case fields belong to the folder they were entered for."""
        source = GUI_JS.read_text(encoding="utf-8")
        script = _function(source, "caseNeedsConfirm") + (
            "console.log(JSON.stringify([caseNeedsConfirm('root:/a', 'root:/b', true), caseNeedsConfirm('root:/a', 'root:/a', true),"
            " caseNeedsConfirm('', 'root:/b', true), caseNeedsConfirm('root:/a', 'root:/b', false), caseNeedsConfirm('root:/a', 'upload', true)]));"
        )
        node = shutil.which("node")
        assert node is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [True, False, False, False, True])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ExportErrorTextTest(unittest.TestCase):
    def test_refused_export_names_its_rows(self) -> None:
        """GUI-REV3 J-7: the unknown / not re-analyzable paths are shown (up to five), with reasons."""
        source = GUI_JS.read_text(encoding="utf-8")
        body = {"error": "서증으로 고른 파일 1개를 서버가 다시 분석할 수 없습니다", "code": "exhibit_not_rederived",
                "not_rederived_exhibit_paths": [{"path": "dalle.jpg", "reason": "파일을 찾을 수 없습니다"}],
                "unknown_exhibit_paths": ["dalle.jpg", *(f"x{i}.png" for i in range(7))]}
        script = ("const EXPORT_ERROR_PATHS = 5;\nconst VERDICT_LABELS = {manipulation_evidence: '조작·생성 근거 있음', undetermined: '판단 불가'};\n"
                  "const STATUS_LABELS = {duplicate: '중복'};\n") + _function(source, "changeText") + _function(source, "exportErrorText") + f"console.log(JSON.stringify(exportErrorText({json.dumps(body, ensure_ascii=False)}, 400)));"
        node = shutil.which("node")
        assert node is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "err.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        text = json.loads(done.stdout)
        self.assertIn("다시 분석할 수 없는 서증: dalle.jpg(파일을 찾을 수 없습니다)", text)
        self.assertIn("이 보고서의 행이 아닌 경로: x0.png, x1.png, x2.png, x3.png, x4.png 외 2개", text)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ChangedRowsTest(unittest.TestCase):
    def test_a_409_names_the_changed_files_and_marks_their_rows(self) -> None:
        """Merge of R19-13: the refused export lists posted vs server verdicts; the rows say 검사 뒤 바뀜."""
        source = GUI_JS.read_text(encoding="utf-8")
        body = {"error": "검사 뒤 바뀐 파일이 있습니다 — 다시 검사하십시오(보고서를 만들지 않았습니다)", "changed_rows": [
            {"path": "dalle.jpg", "posted_verdict": "manipulation_evidence", "server_verdict": "undetermined", "posted_status": "analyzed", "server_status": "analyzed"},
            {"path": "gone.png", "posted_verdict": "undetermined", "server_verdict": None, "posted_status": "analyzed", "server_status": None, "reason": "파일을 찾을 수 없음"},
            *({"path": f"x{i}.png", "posted_verdict": "undetermined", "server_verdict": "undetermined"} for i in range(5)),
        ]}
        script = ("const EXPORT_ERROR_PATHS = 5;\nconst VERDICT_LABELS = {manipulation_evidence: '조작·생성 근거 있음', undetermined: '판단 불가'};\n"
                  "const STATUS_LABELS = {};\n") + _function(source, "changeText") + _function(source, "exportErrorText") + f"console.log(JSON.stringify(exportErrorText({json.dumps(body, ensure_ascii=False)}, 409)));"
        node = shutil.which("node")
        assert node is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "changed.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        text = json.loads(done.stdout)
        self.assertIn("검사 뒤 바뀐 파일: dalle.jpg(조작·생성 근거 있음 → 판단 불가), gone.png(판단 불가 → 다시 분석 못 함: 파일을 찾을 수 없음)", text)
        self.assertIn("외 2개", text)
        out = _run(
            f"results = [{{item: {json.dumps(_row(), ensure_ascii=False)}}}];\n"
            "changedPaths = new Set(['a.png']);\n"
            "console.log(JSON.stringify(rowReason(results[0].item)));"
        )
        self.assertTrue(str(out).startswith("검사 뒤 바뀜 — 다시 검사 · "), out)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ChangeTextTest(unittest.TestCase):
    def test_what_changed_is_named(self) -> None:
        """GUI-REV4 K-4: content-only changes and a duplicate whose original changed are told apart."""
        source = GUI_JS.read_text(encoding="utf-8")
        rows = [
            {"path": "plain.png", "posted_verdict": "undetermined", "server_verdict": "undetermined", "posted_status": "analyzed",
             "server_status": "analyzed", "posted_sha256": "a" * 64, "server_sha256": "b" * 64},
            {"path": "copy.png", "posted_verdict": None, "server_verdict": "manipulation_evidence", "posted_status": "duplicate",
             "server_status": "analyzed", "posted_sha256": "c" * 64, "server_sha256": "c" * 64},
            {"path": "dalle.jpg", "posted_verdict": "manipulation_evidence", "server_verdict": "undetermined", "posted_status": "analyzed",
             "server_status": "analyzed", "posted_sha256": "d" * 64, "server_sha256": "e" * 64},
            {"path": "gone.png", "posted_verdict": "undetermined", "server_verdict": None, "posted_status": "analyzed", "server_status": None,
             "posted_sha256": "f" * 64, "server_sha256": None, "reason": "파일을 찾을 수 없음"},
        ]
        script = ("const VERDICT_LABELS = {manipulation_evidence: '조작·생성 근거 있음', undetermined: '판단 불가'};\n"
                  "const STATUS_LABELS = {duplicate: '중복'};\n") + _function(source, "changeText") + (
            f"const rows = {json.dumps(rows)};\n"
            "const items = {'copy.png': {duplicate_of: 'a1111.png'}};\n"
            "console.log(JSON.stringify(rows.map(r => changeText(r, items[r.path] || null))));")
        node = shutil.which("node")
        assert node is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "change.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [
            "내용(SHA-256) 바뀜 · 결론은 그대로 판단 불가",
            "원본(a1111.png) 바뀜",
            "조작·생성 근거 있음 → 판단 불가",
            "판단 불가 → 다시 분석 못 함: 파일을 찾을 수 없음",
        ])


class GuiAccessibilitySourceTest(unittest.TestCase):
    source: str
    html: str
    css: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = GUI_JS.read_text(encoding="utf-8")
        cls.html = (PACKAGE / "gui.html").read_text(encoding="utf-8")
        cls.css = (PACKAGE / "gui.css").read_text(encoding="utf-8")

    def test_no_page_wide_single_key_shortcuts(self) -> None:
        # A11Y-5: arrows/j/k were taken page-wide; they belong to the ledger list only.
        self.assertNotIn("document.addEventListener('keydown'", self.source)
        self.assertIn("$('res-list').addEventListener('keydown'", self.source)
        self.assertIn("$('kb-toggle').checked", self.source)
        self.assertIn('id="kb-toggle"', self.html)

    def test_scan_polling_has_no_deadline_and_reconnects(self) -> None:
        # U-7: the GUI gave up after 30 minutes while the job kept running.
        self.assertNotIn("30 * 60 * 1000", self.source)
        self.assertNotIn("분석 시간 초과", self.source)
        self.assertIn("sessionStorage.setItem(JOB_STORE", self.source)
        self.assertIn("resumeJob();", self.source)

    def test_progressbar_and_live_regions(self) -> None:
        # A11Y-6: a progressbar with a value range; live regions only on short lines.
        self.assertRegex(self.html, r'role="progressbar"[^>]*aria-valuemin="0"[^>]*aria-valuemax="0"[^>]*aria-valuenow="0"')
        self.assertIn("bar.ariaValueNow = ", self.source)
        for container in ("qc-out", "res-list", "stat-pills", "cmp-result"):
            tag = re.search(rf'<[^>]*id="{container}"[^>]*>', self.html)
            assert tag is not None
            self.assertNotIn("aria-live", tag.group(0), container)
        self.assertIn('id="error-region"', self.html)

    def test_landmarks_skip_link_and_dialog(self) -> None:
        self.assertIn('<a class="skip-link" href="#main">', self.html)
        self.assertIn('<main id="main"', self.html)
        self.assertIn('<nav class="viewnav" aria-label="화면">', self.html)
        self.assertIn('<dialog id="token-modal"', self.html)
        self.assertIn("modal.showModal();", self.source)

    def test_nothing_is_fetched_from_the_network(self) -> None:
        # The tool runs offline: no web fonts, no CDN.
        for name, text in (("gui.html", self.html), ("gui.css", self.css), ("gui.js", self.source)):
            with self.subTest(file=name):
                self.assertNotRegex(text, r"https?://")
                self.assertNotIn("@import", text)
                self.assertNotIn("@font-face", text)

    def test_verdict_marks_are_shapes_and_undetermined_is_neutral(self) -> None:
        # A11Y-1: 판단 불가 is a neutral hollow ring, never orange.
        self.assertIn(".mk-u { border: 2px solid var(--neutral); border-radius: 50%; }", self.css)
        self.assertNotIn("orange", self.css.lower())
        self.assertIn("prefers-color-scheme: dark", self.css)
        self.assertIn("prefers-reduced-motion: reduce", self.css)

    def test_evidence_bearing_rows_sort_first(self) -> None:
        self.assertIn("const BAND_ORDER = { manipulation_evidence: 0, authenticity_evidence: 1, undetermined: 2, other: 3 };", self.source)


if __name__ == "__main__":
    unittest.main()
