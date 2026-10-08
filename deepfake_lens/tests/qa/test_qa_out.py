"""QA-OUT scenarios for the unified entry point (WP-F, G7/G8) — phase 0.

Runs without neural weights: the packaged models dir carries only
``supported: false`` profiles, so every model check is recorded as skipped
and verdicts come from deterministic evidence alone.

QA-OUT-4 scans the same folder through the CLI (``deepfake-lens scan``,
which calls ``analysis_api.scan_folder``), the stdlib web server
(``webapp.build_server`` + a real HTTP GET /api/scan) and — when fastapi and
httpx are installed — the FastAPI server, and requires identical verdicts,
evidence, coverage and threshold provenance.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import struct
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import webapp_api
from deepfake_lens.analysis_api import AnalysisOptions, scan_folder, scan_payload
from deepfake_lens.cli import main as cli_main
from deepfake_lens.core import analyze_file, build_classification_result
from deepfake_lens.result_text import TEXT_LEGAL_LIMITATION
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Grade,
    RiskBand,
    ScanItem,
    SourceGuess,
    Verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK = REPO_ROOT / "fixtures" / "benchmark"
TEXT_CORPORA = (REPO_ROOT / "experiments" / "text-corpus", REPO_ROOT / "fixtures" / "adversarial-text")
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
CLIENT_HEADERS = {"X-Deepfake-Lens-Client": "qa"}


def _png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Minimal 8-bit RGB PNG (stdlib zlib) from packed row-major pixels."""
    rows = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")


def write_photo_like_png(path: Path, *, seed: int, width: int = 192, height: int = 144) -> Path:
    """A deterministic photo-like image: smooth colored blobs + sensor-like noise.

    Many unique colors, moderate noise residual and row correlation well
    inside the natural-image range — not a gradient, flat fill or white
    noise. Requires numpy.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    image = np.zeros((height, width, 3), dtype=np.float64) + rng.uniform(40, 120, size=3)
    for _ in range(6):
        cx, cy = rng.uniform(0, width), rng.uniform(0, height)
        sigma = rng.uniform(width / 10, width / 3)
        amplitude = rng.uniform(-90, 120, size=3)
        blob = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2))
        image += blob[..., None] * amplitude
    image += rng.normal(0, 7.0, size=image.shape)
    pixels = np.clip(image, 0, 255).astype(np.uint8)
    path.write_bytes(_png_bytes(width, height, pixels.tobytes()))
    return path


def _norm_item(item: dict[str, Any]) -> dict[str, Any]:
    """The comparable core of one scan item (QA-OUT-4)."""
    result = item.get("result") or {}
    return {
        "status": item.get("status"),
        "kind": item.get("kind"),
        "verdict_code": result.get("verdict_code"),
        "grade": result.get("grade"),
        "probability": result.get("probability"),
        "evidence": [(e.get("title"), e.get("kind"), e.get("direction"), e.get("strength")) for e in result.get("evidence", [])],
        "coverage": [(c.get("check"), c.get("status"), c.get("reason")) for c in result.get("coverage", [])],
    }


def _norm_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "items": {item["path"]: _norm_item(item) for item in payload["items"]},
        "thresholds": payload["thresholds"],
        "verdicts": {key: payload["summary"][key] for key in ("manipulation_evidence", "authenticity_evidence", "undetermined", "checks_failed")},
    }


class QaOut4SameResultEverywhereTest(unittest.TestCase):
    """QA-OUT-4: 같은 폴더를 CLI, GUI(/api/scan), API 서버로 각각 검사 → 세 결과의 결론·근거·확률·임계값 출처가 동일."""

    def setUp(self) -> None:
        # Read roots and the server models dir are process globals; isolate.
        patches: list[Any] = [
            mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()),
            mock.patch.object(webapp_api, "_MODELS_DIR", None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _cli_payload(self, folder: Path) -> dict[str, Any]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["scan", str(folder), "--format", "json"]), 0)
        return json.loads(out.getvalue())

    def _web_payload(self, folder: Path) -> dict[str, Any]:
        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=folder)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            query = urllib.parse.urlencode({"folder": str(folder)})
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/scan?{query}", headers=CLIENT_HEADERS)
            with urllib.request.urlopen(request, timeout=60) as response:
                self.assertEqual(response.status, 200)
                return json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()

    def _api_payload(self, folder: Path) -> dict[str, Any]:
        from fastapi.testclient import TestClient

        from deepfake_lens.api_server import create_app

        webapp_api.configure_read_roots(folder)
        client = TestClient(create_app(default_folder=folder))
        response = client.get(
            "/api/scan", params={"folder": str(folder)}, headers={"host": "localhost", **CLIENT_HEADERS},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_cli_gui_api_identical_on_benchmark_fixtures(self) -> None:
        """QA-OUT-4: identical verdict/grade/evidence/coverage/thresholds across entry points."""
        folder = BENCHMARK.resolve()
        cli = _norm_payload(self._cli_payload(folder))
        options = AnalysisOptions.from_cli_args(type("Args", (), {})())
        summary, items, thresholds = scan_folder(folder, options)
        direct = _norm_payload(scan_payload(summary, items, thresholds, options))
        web = _norm_payload(self._web_payload(folder))

        self.assertEqual(len(cli["items"]), 4)  # 3 PNG fixtures + README.md
        self.assertEqual(cli, direct)
        self.assertEqual(cli, web)
        # G7: the GUI used to report builtin_defaults while the CLI loaded the
        # measured profile; both now report the packaged profile.
        self.assertEqual(web["thresholds"]["source"], "threshold_profile")
        self.assertTrue(web["thresholds"]["measured"])
        if HAVE_FASTAPI:
            self.assertEqual(cli, _norm_payload(self._api_payload(folder)))

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed — API-server leg of QA-OUT-4")
    def test_api_server_leg(self) -> None:
        """QA-OUT-4 (API leg): FastAPI /api/scan matches the CLI."""
        folder = BENCHMARK.resolve()
        self.assertEqual(_norm_payload(self._cli_payload(folder)), _norm_payload(self._api_payload(folder)))

    def test_upload_reports_same_threshold_provenance(self) -> None:
        """QA-OUT-4 (GUI upload): /api/analyze-upload reports the thresholds it used, not builtin defaults."""
        boundary = "----qaout4"
        data = (BENCHMARK / "ai-like-gradient.png").read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"ai-like-gradient.png\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        payload: dict[str, Any] = webapp_api._analyze_upload_payload(f"multipart/form-data; boundary={boundary}", body)
        cli = self._cli_payload(BENCHMARK.resolve())
        self.assertEqual(payload["thresholds"], cli["thresholds"])
        uploaded = _norm_item(payload["items"][0])
        scanned = _norm_item(next(item for item in cli["items"] if item["path"] == "ai-like-gradient.png"))
        self.assertEqual(uploaded, scanned)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (photo-like fixture generator)")
class QaOut1NoWeightsTest(unittest.TestCase):
    """QA-OUT-1: 신경망 가중치를 제거한 상태에서 사진 100장 검사 → 결론이 "판단 불가" 또는 결정적 근거에 의한 결론뿐. "낮음/깨끗함"이 통계적 근거 없이 나오는 건 0개."""

    def test_hundred_photos_without_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index in range(100):
                write_photo_like_png(folder / f"photo-{index:03d}.png", seed=index, width=160 + (index % 5) * 16, height=128 + (index % 3) * 16)
            summary, items, _ = scan_folder(folder, AnalysisOptions(max_files=200))
        self.assertEqual(summary.analyzed, 100)
        for item in items:
            with self.subTest(path=item.path):
                result = item.result
                assert result is not None
                deciding = [
                    e for e in result.evidence
                    if e.kind == EvidenceKind.DETERMINISTIC and e.strength == EvidenceStrength.STRONG
                ]
                if result.verdict_code == Verdict.MANIPULATION_EVIDENCE:
                    self.assertTrue(any(e.direction == EvidenceDirection.SYNTHETIC for e in deciding), result.evidence)
                elif result.verdict_code == Verdict.AUTHENTICITY_EVIDENCE:
                    self.assertTrue(any(e.direction == EvidenceDirection.AUTHENTIC for e in deciding), result.evidence)
                else:
                    self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                if result.band == RiskBand.LOW:
                    self.assertTrue(any(e.direction == EvidenceDirection.AUTHENTIC for e in deciding), result.evidence)
                self.assertNotEqual(result.band, RiskBand.MEDIUM)
                # No weights: no statistical evidence may carry a probability.
                self.assertFalse(any(e.kind == EvidenceKind.STATISTICAL and e.probability is not None for e in result.evidence))
                self.assertFalse(result.score_is_calibrated)


def _probability_provenance_gaps(result: Any) -> list[str]:
    """Statistical evidence items whose probability lacks provenance (QA-OUT-5)."""
    gaps: list[str] = []
    for item in result.evidence:
        if item.kind != EvidenceKind.STATISTICAL or item.probability is None:
            continue
        missing = [
            name for name, value in (
                ("calibration_id", item.calibration_id),
                ("measured_on", item.measured_on),
                ("probability_ci", item.probability_ci),
            ) if not value
        ]
        if missing:
            gaps.append(f"{item.title}: {', '.join(missing)}")
    return gaps


class QaOut5ProbabilityProvenanceTest(unittest.TestCase):
    """QA-OUT-5: 모델 확률이 표시된 모든 결과 → 각 확률에 보정 코퍼스 ID, 측정 조건, 95% CI가 붙어 있음. 측정 범위 밖 입력(64 px 이하)은 "범위 밖"으로 표시되고 확률 없음."""

    def _result(self, evidence: list[EvidenceItem]) -> Any:
        return build_classification_result(
            subject="이미지",
            evidence=evidence,
            coverage=[CoverageEntry("model:calibrated-fake", CoverageStatus.RAN)],
            source_guess=SourceGuess.unknown("테스트"),
            limitations=[],
            next_checks=[],
        )

    def test_synthetic_calibrated_probability_carries_provenance(self) -> None:
        """QA-OUT-5: a calibrated statistical item (built synthetically) carries id, conditions and CI."""
        calibrated = EvidenceItem(
            title="보정된 모델 확률",
            detail="합성 입력",
            kind=EvidenceKind.STATISTICAL,
            direction=EvidenceDirection.SYNTHETIC,
            strength=EvidenceStrength.MODERATE,
            layer="model",
            probability=0.91,
            probability_ci=(0.86, 0.95),
            calibration_id="calib-qa-out-5",
            measured_on="corpus-qa@test n=400/400",
        )
        result = self._result([calibrated])
        self.assertEqual(_probability_provenance_gaps(result), [])
        self.assertEqual(result.probability, 0.91)
        self.assertEqual(result.probability_ci, (0.86, 0.95))
        self.assertTrue(result.score_is_calibrated)
        payload = result.to_json()
        [row] = [e for e in payload["evidence"] if e["kind"] == "statistical"]
        self.assertEqual(row["calibration_id"], "calib-qa-out-5")
        self.assertEqual(row["measured_on"], "corpus-qa@test n=400/400")
        self.assertEqual(list(row["probability_ci"]), [0.86, 0.95])

    def test_assertion_catches_probability_without_provenance(self) -> None:
        """The QA-OUT-5 check itself fails on a probability with no calibration record."""
        bare = EvidenceItem(
            title="보정 없는 확률",
            detail="합성 입력",
            kind=EvidenceKind.STATISTICAL,
            direction=EvidenceDirection.SYNTHETIC,
            strength=EvidenceStrength.WEAK,
            layer="model",
            probability=0.7,
        )
        result = self._result([bare])
        self.assertEqual(len(_probability_provenance_gaps(result)), 1)
        # Uncalibrated: never a probability-driven verdict or score.
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertFalse(result.score_is_calibrated)

    def test_scanned_results_have_no_unprovenanced_probability(self) -> None:
        """QA-OUT-5 on real scans: every displayed probability has provenance (none exist without weights)."""
        summary, items, _ = scan_folder(BENCHMARK, AnalysisOptions())
        self.assertGreater(summary.analyzed, 0)
        for item in items:
            if item.result is not None:
                self.assertEqual(_probability_provenance_gaps(item.result), [], item.path)

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed (photo-like fixture generator)")
    def test_64px_image_is_out_of_range_without_probability(self) -> None:
        """QA-OUT-5: a 64 px image gets external_model skipped "측정 범위 밖" and no probability."""
        with tempfile.TemporaryDirectory() as tmp:
            image = write_photo_like_png(Path(tmp) / "thumb.png", seed=64, width=64, height=64)
            item = analyze_file(image, model_path=REPO_ROOT / "deepfake_lens" / "models")
        result = item.result
        assert result is not None
        entries = [entry for entry in result.coverage if entry.check == "external_model"]
        self.assertEqual(len(entries), 1, result.coverage)
        self.assertEqual(entries[0].status, CoverageStatus.SKIPPED)
        self.assertIn("측정 범위 밖", entries[0].reason)
        self.assertIsNone(result.probability)
        self.assertIsNone(result.model_analysis)
        self.assertFalse(any(e.probability is not None for e in result.evidence))


class QaOut6TextIsReferenceTest(unittest.TestCase):
    """QA-OUT-6: 텍스트 파일 50개 검사 → 모든 결론 등급이 "참고", 보고서에 법적 한계 문구 존재."""

    def test_text_corpora_are_reference_grade(self) -> None:
        texts: list[ScanItem] = []
        for corpus in TEXT_CORPORA:
            _, items, _ = scan_folder(corpus, AnalysisOptions(recursive=True))
            texts.extend(item for item in items if item.kind == "text" and item.status == "analyzed")
        self.assertGreaterEqual(len(texts), 50)
        for item in texts:
            with self.subTest(path=item.path):
                result = item.result
                assert result is not None
                self.assertEqual(result.grade, Grade.REFERENCE)
                self.assertEqual(result.limitations[0], TEXT_LEGAL_LIMITATION)
                self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                self.assertTrue(result.verdict.startswith("참고"), result.verdict)

        from deepfake_lens.core import summarize
        from deepfake_lens.reports import write_html_report

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.html"
            write_html_report(report, summarize(texts, capped=False), texts)
            self.assertIn(TEXT_LEGAL_LIMITATION, report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
