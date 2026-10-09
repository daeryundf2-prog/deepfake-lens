"""Phase-0 fix 1: standalone commands, API endpoints and GUI speak the
three-verdict contract or the layer-diagnostic shape — never a band (D1-D4,
D14, D16).

Every standalone subcommand's JSON is checked for:

* no ``band`` / ``band_label`` key anywhere, and no ``verdict`` key with an
  old band value;
* either ``verdict_code`` in the three verdicts (analysis_result) or
  ``kind == "layer_diagnostic"`` with ``measured: false``, the fixed notice
  and a ``reference_band`` of "reference"/"unavailable";
* none of the old cutoff sentences ("의심 신호가 강합니다/적습니다" …).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from deepfake_lens.cli import main
from deepfake_lens.layer_diagnostic import (
    LAYER_DIAGNOSTIC_NOTICE,
    REFERENCE_BANDS,
    format_layer_diagnostic,
    to_layer_diagnostic,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
A1111 = REPO_ROOT / "fixtures" / "benchmark" / "a1111-metadata-marker.png"
GRADIENT = REPO_ROOT / "fixtures" / "benchmark" / "ai-like-gradient.png"
TEXT_SAMPLE = REPO_ROOT / "fixtures" / "deepfake-lens-sample" / "train" / "ai" / "flux" / "sample-ai.txt"
VERDICT_CODES = {"manipulation_evidence", "authenticity_evidence", "undetermined"}
OLD_BAND_VALUES = {"high", "medium", "low", "높음", "주의", "낮음"}
OLD_SENTENCES = (
    "의심 신호가 강합니다",
    "의심 신호는 적습니다",
    "의심 신호가 적습니다",
    "신호가 거의 없습니다",
    "가능성이 높습니다",
    "전체 신뢰도",
)
HAVE_CV2 = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None
HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}.{key}", str(key), child
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def assert_no_old_contract(test: unittest.TestCase, payload: Any, label: str) -> None:
    """No band key, no old-valued verdict, no old cutoff sentence — anywhere."""
    for path, key, value in _walk(payload):
        test.assertNotIn(key, {"band", "band_label"}, f"{label}: {path} carries a band")
        if key == "verdict":
            test.assertNotIn(value, OLD_BAND_VALUES, f"{label}: {path} is an old band value")
        if isinstance(value, str):
            for sentence in OLD_SENTENCES:
                test.assertNotIn(sentence, value, f"{label}: {path} has an old cutoff sentence")


def assert_contract_shape(test: unittest.TestCase, payload: dict[str, Any], label: str) -> str:
    """analysis_result with a three-valued verdict_code, or a layer diagnostic."""
    assert_no_old_contract(test, payload, label)
    if payload.get("kind") == "layer_diagnostic":
        test.assertIs(payload["measured"], False, label)
        test.assertEqual(payload["notice"], LAYER_DIAGNOSTIC_NOTICE, label)
        test.assertIn(payload["reference_band"], REFERENCE_BANDS, label)
        test.assertNotIn("verdict_code", payload, label)
        return "layer_diagnostic"
    verdict_code = payload.get("verdict_code")
    if verdict_code is None and isinstance(payload.get("conclusion"), dict):
        verdict_code = payload["conclusion"].get("verdict_code")
    test.assertIn(verdict_code, VERDICT_CODES, f"{label}: no three-valued verdict_code")
    return "analysis_result"


def run_json(args: list[str]) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(args)
    return code, json.loads(out.getvalue())


def write_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> None:
    import math

    frames = bytearray()
    for index in range(int(seconds * rate)):
        sample = int(8000 * math.sin(2 * math.pi * 220 * index / rate))
        frames += sample.to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def write_photo(path: Path) -> None:
    """A deterministic textured image (not necessarily photo-class)."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(3)
    base = np.linspace(40, 200, 256)[None, :, None] * np.ones((192, 1, 3))
    noisy = np.clip(base + rng.normal(0, 12, base.shape), 0, 255).astype("uint8")
    Image.fromarray(noisy).save(path, quality=90)


class LayerDiagnosticHelperTest(unittest.TestCase):
    def test_legacy_payload_is_stripped_at_every_depth(self) -> None:
        legacy = {
            "score": 80,
            "band": "high",
            "band_label": "높음",
            "verdict": "얼굴 조작 의심 신호가 강합니다.",
            "nested": {"band": "low", "verdict": "x", "keep": 1},
            "signals": [{"title": "t", "detail": "d", "weight": 10}],
        }
        diag = to_layer_diagnostic("face", legacy, layer_label="얼굴")
        assert_contract_shape(self, diag, "legacy face")
        self.assertEqual(diag["raw_score"], 80)
        self.assertEqual(diag["reference_band"], "reference")
        self.assertEqual(diag["diagnostic"]["nested"], {"keep": 1})

    def test_legacy_unknown_becomes_unavailable_with_reason(self) -> None:
        diag = to_layer_diagnostic("forensic", {"score": 0, "band": "unknown", "verdict": "파일이 없습니다"})
        self.assertEqual(diag["reference_band"], "unavailable")
        self.assertEqual(diag["reference_note"], "파일이 없습니다")

    def test_text_rendering_has_notice_and_no_band_words(self) -> None:
        text = format_layer_diagnostic(to_layer_diagnostic("audio", {"score": 12, "reference_band": "reference", "reference_note": "n", "signals": []}))
        self.assertIn(LAYER_DIAGNOSTIC_NOTICE, text)
        for word in ("높음", "주의", "낮음"):
            self.assertNotIn(word, text)


class StandaloneCommandContractTest(unittest.TestCase):
    """D1: every standalone subcommand — no band, three verdicts or a layer diagnostic."""

    _tmp: tempfile.TemporaryDirectory[str]
    root: Path
    wav: Path
    wav2: Path
    text: Path
    photo: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.wav = cls.root / "clip.wav"
        write_wav(cls.wav)
        cls.wav2 = cls.root / "clip2.wav"
        write_wav(cls.wav2, seconds=1.5)
        cls.text = cls.root / "essay.txt"
        cls.text.write_text("As an AI language model, I cannot browse. 언어 모델에 대한 사람의 글입니다. " * 6, encoding="utf-8")
        cls.photo = cls.root / "photo.jpg"
        if HAVE_CV2:
            write_photo(cls.photo)
        else:
            cls.photo.write_bytes(A1111.read_bytes())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def commands(self) -> list[tuple[str, list[str], str]]:
        photo, wav, text = str(self.photo), str(self.wav), str(self.text)
        # N4/N7: a missing input is now a usage error (exit 2) — these ran on
        # a nonexistent "missing.mp4" and printed a diagnostic about nothing
        # with exit 0. An undecodable video file gives the same diagnostic.
        missing_video = str(self.root / "undecodable.mp4")
        Path(missing_video).write_bytes(b"\x00\x00\x00\x18ftypisom-not-a-real-video")
        return [
            ("audio", ["audio", wav, "--no-default-engine"], "layer_diagnostic"),
            ("video-analysis", ["video-analysis", missing_video], "layer_diagnostic"),
            ("text-advanced", ["text-advanced", text], "layer_diagnostic"),
            ("pixel-analysis", ["pixel-analysis", photo], "layer_diagnostic"),
            ("inpaint", ["inpaint", photo], "layer_diagnostic"),
            ("prnu", ["prnu", photo, "--reference", photo, "--reference", photo, "--reference", photo], "layer_diagnostic"),
            ("rppg", ["rppg", missing_video], "layer_diagnostic"),
            ("face", ["face", photo], "layer_diagnostic"),
            ("avatar", ["avatar", "--file", missing_video], "layer_diagnostic"),
            ("3d", ["3d", "--text", "a generated low-poly mesh with procedural texture"], "layer_diagnostic"),
            ("realtime", ["realtime", "--scores", "10,80,95", "--alert-threshold", "50"], "layer_diagnostic"),
            ("faceswap-seam", ["faceswap-seam", photo, "--format", "json"], "layer_diagnostic"),
            ("compare", ["compare", text, str(TEXT_SAMPLE)], "layer_diagnostic"),
            ("multimodal scores", ["multimodal", "--image-score", "90", "--text-score", "85"], "layer_diagnostic"),
            ("explain --score", ["explain", "--score", "70", "--format", "json"], "layer_diagnostic"),
            ("forensic", ["forensic", str(A1111)], "analysis_result"),
            ("classify", ["classify", str(A1111)], "analysis_result"),
            ("agent --text", ["agent", "--text", "As an AI language model, I cannot help."], "analysis_result"),
            ("agent --file", ["agent", "--file", text], "analysis_result"),
            ("multimodal files", ["multimodal", str(A1111), text], "analysis_result"),
            ("explain FILE", ["explain", str(A1111), "--format", "json"], "analysis_result"),
            ("legal-report", ["legal-report", str(A1111), "--format", "json"], "analysis_result"),
        ]

    def test_every_standalone_command_speaks_the_new_contract(self) -> None:
        for label, args, expected_kind in self.commands():
            with self.subTest(command=label):
                code, payload = run_json(args)
                self.assertEqual(code, 0, label)
                self.assertEqual(assert_contract_shape(self, payload, label), expected_kind, label)

    @unittest.skipUnless(HAVE_CV2, "opencv/numpy required")
    def test_ml_classify_is_a_layer_diagnostic_without_label_or_probability(self) -> None:
        code, payload = run_json(["ml-classify", str(self.photo)])
        self.assertEqual(code, 0)
        assert_contract_shape(self, payload, "ml-classify")
        self.assertNotIn("prediction", payload["diagnostic"])
        self.assertNotIn("probability_ai", payload["diagnostic"])

    def test_forensic_matches_scan_verdict_for_generator_metadata(self) -> None:
        """D1/D2: the a1111 PNG is manipulation_evidence in forensic, as in scan."""
        _, payload = run_json(["forensic", str(A1111)])
        self.assertEqual(payload["verdict_code"], "manipulation_evidence")
        kinds = {(e["kind"], e["direction"], e["strength"]) for e in payload["evidence"]}
        self.assertIn(("deterministic", "synthetic", "strong"), kinds)
        self.assertEqual(payload["layer_diagnostics"]["provenance_metadata"]["kind"], "layer_diagnostic")
        _, scan = run_json(["scan", str(A1111.parent), "--format", "json"])
        scan_item = next(item for item in scan["items"] if item["name"] == A1111.name)
        self.assertEqual(scan_item["result"]["verdict_code"], payload["verdict_code"])
        self.assertEqual(scan_item["result"]["evidence"], payload["evidence"])

    def test_text_commands_are_reference_grade(self) -> None:
        _, payload = run_json(["agent", "--text", "As an AI language model, I cannot help."])
        self.assertEqual(payload["verdict_code"], "undetermined")
        self.assertEqual(payload["grade"], "reference")

    def test_pixel_analysis_respects_the_photo_gate(self) -> None:
        """D3: a non-photo image is not pre-screened at all."""
        _, payload = run_json(["pixel-analysis", str(GRADIENT)])
        self.assertEqual(payload["reference_band"], "unavailable")
        self.assertIn("사진 아님", payload["reference_note"])
        self.assertEqual(payload["raw_score"], 0)

    def test_explain_names_the_decision_rule(self) -> None:
        _, payload = run_json(["explain", str(A1111), "--format", "json"])
        self.assertEqual(payload["rule_number"], 2)
        self.assertIn("규칙 2", payload["rule"])

    def test_multimodal_files_combine_by_rule_order(self) -> None:
        _, payload = run_json(["multimodal", str(A1111), str(self.text)])
        self.assertEqual(payload["verdict_code"], "manipulation_evidence")
        self.assertEqual([item["verdict_code"] for item in payload["items"]], ["manipulation_evidence", "undetermined"])

    def test_realtime_has_no_default_cutoff(self) -> None:
        _, payload = run_json(["realtime", "--scores", "90,95"])
        self.assertFalse(payload["diagnostic"]["above_alert_threshold"])
        self.assertEqual(payload["diagnostic"]["alerts"], [])

    def test_table_output_has_notice_and_no_band(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(["text-advanced", str(self.text), "--format", "table"])
        text = out.getvalue()
        self.assertIn("계층 진단(참고 신호 · 미측정)", text)
        self.assertIn(LAYER_DIAGNOSTIC_NOTICE, text)
        self.assertNotIn("Verdict:", text)


class LegalReportTest(unittest.TestCase):
    """D4: legal-report is built from the scan result."""

    def test_a1111_report_lists_scan_evidence_and_package_version(self) -> None:
        from deepfake_lens.core import TOOL_VERSION

        code, report = run_json(["legal-report", str(A1111), "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(report["conclusion"]["verdict_code"], "manipulation_evidence")
        self.assertEqual(report["tool_version"], TOOL_VERSION)
        self.assertTrue(report["evidence"], "evidence list must not be empty")
        self.assertTrue(any(e["kind"] == "deterministic" and e["direction"] == "synthetic" for e in report["evidence"]))
        self.assertTrue(report["coverage"])
        self.assertEqual(len(report["file"]["sha256"]), 64)

    def test_text_report_has_no_legacy_phrases(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(["legal-report", str(A1111)])
        text = out.getvalue()
        self.assertIn("조작·생성 근거 있음", text)
        self.assertNotIn("출처 표준 메타데이터가 발견되지 않았습니다", text)
        self.assertNotIn("전체 신뢰도", text)
        self.assertNotIn("도구 버전: 2.0", text)

    def test_signed_report_verifies(self) -> None:
        from deepfake_lens.signing import verify_report

        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "key"
            key.write_text("legal-key", encoding="utf-8")
            out_json = Path(tmp) / "report.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                main(["legal-report", str(A1111), "--json-out", str(out_json), "--key-file", str(key)])
            self.assertTrue(verify_report(out_json, b"legal-key").verified)


class VerifyReportCommandTest(unittest.TestCase):
    """D14: verify-report prints 검증됨/변조됨/키 ID 불일치/서명 없음, exit 0/1/2/3."""

    def setUp(self) -> None:
        from deepfake_lens.signing import sign_report

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.key = self.root / "key"
        self.key.write_text("report-key-1", encoding="utf-8")
        self.other_key = self.root / "other"
        self.other_key.write_text("report-key-2", encoding="utf-8")
        body = {"schema_version": 2, "summary": {"total": 1}, "items": [{"path": "a.png", "sha256": "0" * 64}]}
        self.signed = self.root / "signed.json"
        self.signed.write_text(json.dumps(sign_report(body, b"report-key-1"), ensure_ascii=False), encoding="utf-8")
        self.unsigned = self.root / "unsigned.json"
        self.unsigned.write_text(json.dumps(sign_report(body, None), ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, args: list[str], env_key: str | None = None) -> tuple[int, str]:
        out = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "DEEPFAKE_LENS_REPORT_KEY"}
        if env_key is not None:
            env["DEEPFAKE_LENS_REPORT_KEY"] = env_key
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["verify-report", *args])
        return code, out.getvalue()

    def test_verified(self) -> None:
        code, text = self._run([str(self.signed), "--key-file", str(self.key)])
        self.assertEqual((code, text.split(":")[0]), (0, "검증됨"))

    def test_env_key(self) -> None:
        code, text = self._run([str(self.signed)], env_key="report-key-1")
        self.assertEqual(code, 0)
        self.assertIn("검증됨", text)

    def test_tampered(self) -> None:
        data = json.loads(self.signed.read_text(encoding="utf-8"))
        data["items"][0]["sha256"] = "1" + "0" * 63
        self.signed.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        code, text = self._run([str(self.signed), "--key-file", str(self.key)])
        self.assertEqual((code, text.split(":")[0]), (1, "변조됨"))

    def test_key_mismatch(self) -> None:
        code, text = self._run([str(self.signed), "--key-file", str(self.other_key)])
        self.assertEqual((code, text.split(":")[0]), (2, "키 ID 불일치"))

    def test_unsigned(self) -> None:
        code, text = self._run([str(self.unsigned), "--key-file", str(self.key)])
        self.assertEqual((code, text.split(":")[0]), (3, "서명 없음"))

    def test_no_key_and_unreadable_are_usage_errors(self) -> None:
        self.assertEqual(self._run([str(self.signed)])[0], 4)
        self.assertEqual(self._run([str(self.root / "nope.json"), "--key-file", str(self.key)])[0], 4)

    def test_json_format(self) -> None:
        code, text = self._run([str(self.signed), "--key-file", str(self.key), "--format", "json"])
        payload = json.loads(text)
        self.assertEqual((code, payload["status"], payload["reason"]), (0, "verified", "검증됨"))

    def test_documented_in_cli_docs(self) -> None:
        docs = (REPO_ROOT / "docs" / "deepfake-lens-cli.md").read_text(encoding="utf-8")
        self.assertIn("`verify-report", docs)
        for word in ("검증됨", "변조됨", "키 ID 불일치", "서명 없음"):
            self.assertIn(word, docs)


class WebAnalyzeFileTest(unittest.TestCase):
    """D3: /api/analyze-file is the scan result, no ungated pixel band."""

    def test_analyze_file_payload_is_three_verdict(self) -> None:
        from deepfake_lens import webapp_api

        with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            webapp_api.configure_read_roots(A1111.parent)
            payload = webapp_api._analyze_file_payload(f"file={A1111}")
        self.assertEqual(assert_contract_shape(self, payload, "analyze-file"), "analysis_result")
        self.assertEqual(payload["verdict_code"], "manipulation_evidence")
        self.assertNotIn("pixel_analysis", payload)
        self.assertNotIn("forensic", payload)
        layers: Any = payload["layer_diagnostics"]
        for layer in layers.values():
            self.assertEqual(layer["kind"], "layer_diagnostic")



@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ApiEndpointContractTest(unittest.TestCase):
    """D2/D16: api-serve analysis endpoints and the /api/stats header rule."""

    HEADERS = {"host": "localhost", "X-Deepfake-Lens-Client": "test"}

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from deepfake_lens import api_server, webapp_api

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.png = self.root / A1111.name
        self.png.write_bytes(A1111.read_bytes())
        self.wav = self.root / "clip.wav"
        write_wav(self.wav)
        self._roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        self._roots.start()
        webapp_api.configure_read_roots(self.root)
        self.client = TestClient(api_server.create_app(default_folder=self.root))

    def tearDown(self) -> None:
        self._roots.stop()
        self._tmp.cleanup()

    def _post(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(path, params=params, headers=self.HEADERS)
        self.assertEqual(response.status_code, 200, response.text[:300])
        return response.json()["data"]

    def test_analysis_endpoints_speak_the_new_contract(self) -> None:
        cases: list[tuple[str, dict[str, Any], str]] = [
            ("/api/analyze/image", {"file_path": str(self.png)}, "analysis_result"),
            ("/api/analyze/forensic", {"file_path": str(self.png)}, "analysis_result"),
            ("/api/analyze/audio", {"file_path": str(self.wav)}, "analysis_result"),
            ("/api/analyze/text", {"text": "As an AI language model, I cannot help with that request."}, "analysis_result"),
            ("/api/classify", {"file_path": str(self.png)}, "analysis_result"),
            ("/api/analyze/face", {"file_path": str(self.png)}, "layer_diagnostic"),
            ("/api/multimodal", {"image_score": 90, "text_score": 80}, "layer_diagnostic"),
        ]
        for path, params, expected in cases:
            with self.subTest(endpoint=path):
                data = self._post(path, params)
                self.assertEqual(assert_contract_shape(self, data, path), expected)

    def test_forensic_endpoint_matches_scan(self) -> None:
        data = self._post("/api/analyze/forensic", {"file_path": str(self.png)})
        self.assertEqual(data["verdict_code"], "manipulation_evidence")

    def test_check_layers_are_diagnostics(self) -> None:
        response = self.client.post("/api/check", params={"file_path": str(self.png)}, headers=self.HEADERS)
        data = response.json()["data"]
        self.assertEqual(data["forensic"]["kind"], "layer_diagnostic")
        assert_no_old_contract(self, data["forensic"], "check.forensic")


if __name__ == "__main__":
    unittest.main()
