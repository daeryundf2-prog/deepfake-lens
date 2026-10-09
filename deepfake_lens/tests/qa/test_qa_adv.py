"""QA-ADV-1 / QA-ADV-2 — the photo/non-photo gate (WP-D: G3, G13, G17).

Images are generated into a temporary directory by
``scripts/make_adversarial_fixtures.py`` (seed 0, deterministic; nothing is
committed). Every detector entry point that the gate must block is patched
with a mock so the test proves the detector was *not called*, not merely
that its output was ignored. A mandatory positive control (photo-like
JPEGs and, when available, real photographs) proves the gate does not
swallow photographs.

Needs numpy + Pillow (the gate's own dependencies); skipped without them.

Moved here from ``test_qa_adv3.py`` (W2, verify round 4 — one file per QA area):

QA-ADV-3 (phase 0, WP-E, G4): keywords never promote a text.

Moved from tests/test_qa_adv3_keywords.py into the QA package (WP-J).

통과 기준: AI에 대해 쓴 사람 글 30건("언어 모델", "as an AI" 포함) → 키워드는
근거 목록에 나타나되 결론은 "참고 — 근거 부족".
"""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import deepfake_lens
from deepfake_lens.core import AI_IDENTITY_PHRASES, analyze_file, analyze_text, scan_directory
from deepfake_lens.result_text import TEXT_LEGAL_LIMITATION
from deepfake_lens.result_types import (
    ClassificationResult,
    CoverageStatus,
    EvidenceDirection,
    EvidenceKind,
    EvidenceStrength,
    Grade,
    RiskBand,
    SourceConfidence,
    Verdict,
)


try:
    import numpy  # noqa: F401
    from PIL import Image

    HAVE_IMAGING = True
except ImportError:  # pragma: no cover - no-extras CI job
    HAVE_IMAGING = False

REPO_ROOT = Path(deepfake_lens.__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts" / "make_adversarial_fixtures.py"
BENCHMARK_DIR = REPO_ROOT / "fixtures" / "benchmark"
GATED_CHECKS = ("pixel", "external_model", "face_manipulation", "inpaint", "faceswap_seam")
NON_PHOTO_PREFIX = "사진 아님"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_adversarial_fixtures", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_profile(root: Path) -> Path:
    """A score-sidecar profile: without the gate the model check would run."""
    profile = root / "sidecar-profile.json"
    profile.write_text(json.dumps({"type": "score-sidecar-v1", "name": "qa sidecar"}), encoding="utf-8")
    return profile


def _with_sidecar(image: Path, score: float = 0.9) -> Path:
    image.with_name(image.name + ".model.json").write_text(json.dumps({"score": score}), encoding="utf-8")
    return image


def _analyze_gated(path: Path, profile: Path) -> tuple[ClassificationResult, dict[str, Any]]:
    """Run the full image path with every gated detector replaced by a mock."""
    with patch("deepfake_lens.core.analyze_external_model") as model, \
            patch("deepfake_lens.core.analyze_image_pixels") as pixel, \
            patch("deepfake_lens.core._deep_image_layers") as deep:
        item = analyze_file(path, pixel_mode="fast", model_path=profile, deep_signals=True)
    assert item.result is not None, item.error
    return item.result, {"model": model, "pixel": pixel, "deep": deep}


@unittest.skipUnless(HAVE_IMAGING, "numpy/Pillow 미설치 — 사진/비사진 게이트 비활성")
class AdversarialGateTest(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    generator: ModuleType
    paths: dict[str, list[Path]]
    profile: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.generator = _load_generator()
        cls.paths = cls.generator.generate_all(root / "adversarial", seed=0)
        cls.profile = _model_profile(root)
        for paths in cls.paths.values():
            for path in paths:
                _with_sidecar(path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _assert_gated(self, path: Path, expected_kind: str) -> None:
        result, mocks = _analyze_gated(path, self.profile)
        name = f"{path.parent.name}/{path.name}"
        for mock in mocks.values():
            mock.assert_not_called()
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED, name)
        self.assertNotEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE, name)
        self.assertIn("사진 아님 — 생성 탐지 비적용", result.verdict, name)
        self.assertTrue(result.limitations[0].startswith("사진 아님 — 생성 탐지 비적용"), name)
        coverage = {entry.check: entry for entry in result.coverage}
        self.assertEqual(coverage["image_class"].status, CoverageStatus.RAN, name)
        self.assertEqual(coverage["metadata"].status, CoverageStatus.RAN, name)
        self.assertIn("c2pa", coverage, name)  # metadata + C2PA still run
        for check in GATED_CHECKS:
            entry = coverage[check]
            self.assertEqual(entry.status, CoverageStatus.SKIPPED, f"{name}: {check}")
            self.assertTrue(entry.reason.startswith(NON_PHOTO_PREFIX), f"{name}: {check} {entry.reason}")
            self.assertEqual(entry.reason, f"사진 아님: {expected_kind}", f"{name}: {check}")
        class_items = [item for item in result.evidence if item.layer == "image_class"]
        self.assertEqual(len(class_items), 1, name)
        item = class_items[0]
        self.assertTrue(item.title.startswith("이미지 유형: "), name)
        self.assertEqual(
            (item.kind, item.direction, item.strength),
            (EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK),
            name,
        )
        self.assertIsNone(result.pixel_analysis, name)
        self.assertIsNone(result.model_analysis, name)
        self.assertFalse(any(signal.weight > 0 for signal in result.signals), name)
        self.assertEqual(result.reference_signals, [], name)

    def test_qa_adv_1_pattern_images_are_not_photos(self) -> None:
        """QA-ADV-1: 그라데이션, 랜덤 노이즈, 단색, 체커보드, 블러 노이즈 각 10장 → 전부 "사진 아님 — 생성 탐지 비적용". 의심 판정 0."""
        checked = 0
        for cls in self.generator.PATTERN_CLASSES:
            self.assertEqual(len(self.paths[cls]), 10, cls)
            for path in self.paths[cls]:
                with self.subTest(image=f"{cls}/{path.name}"):
                    # The skip reason names the class: "사진 아님: pattern".
                    self._assert_gated(path, "pattern")
                    checked += 1
        self.assertEqual(checked, 50)

    def test_qa_adv_2_screenshots_are_classified_and_gated(self) -> None:
        """QA-ADV-2: 스크린샷 50장(카톡 대화, 웹페이지, 문서 뷰어) → 스크린샷으로 분류, 생성 탐지 미적용, 의심 판정 0.

        50 synthetic screenshots of the three named kinds: 17 phone chat
        screens (KakaoTalk-style bubbles), 17 desktop browser pages (tab
        strip, address bar, article, sidebar cards) and 16 desktop document
        viewers (title bar, toolbar, thumbnail pane, page of text) — plus a
        JPEG q85 re-encode of every third one, as messengers send them. A
        real-capture set is corpus work (phase 1).
        """
        from deepfake_lens.image_class import classify_image

        expected = {"screenshot": 17, "screenshot_web": 17, "screenshot_viewer": 16}
        checked = 0
        for cls, count in expected.items():
            paths = self.paths[cls]
            self.assertEqual(len(paths), count, cls)
            for path in paths:
                with self.subTest(image=f"{cls}/{path.name}"):
                    self._assert_gated(path, "screenshot")
                    checked += 1
                    if int(path.stem) % 3:
                        continue  # JPEG re-encode check on every third image
                    with Image.open(path) as image:
                        buffer = io.BytesIO()
                        image.convert("RGB").save(buffer, format="JPEG", quality=85)
                    jpeg = path.with_suffix(".jpg")
                    jpeg.write_bytes(buffer.getvalue())
                    self.assertEqual(classify_image(jpeg).kind, "screenshot", "JPEG q85 re-encode")
        self.assertEqual(checked, 50)

    def test_document_scans_are_not_photos(self) -> None:
        """QA-ADV-1: 보조 검사 — 합성 문서 스캔 이미지는 document_scan(사진 아님)으로 분류되어 생성 탐지가 적용되지 않는다."""
        for path in self.paths["document_scan"]:
            with self.subTest(image=path.name):
                self._assert_gated(path, "document_scan")

    def test_generation_is_deterministic(self) -> None:
        """QA-ADV-1: 보조 검사 — 같은 시드로 노이즈·스크린샷 3종을 다시 만들면 바이트 단위로 같은 파일이 나온다(재현 가능한 픽스처)."""
        with tempfile.TemporaryDirectory() as other:
            again = self.generator.generate_all(Path(other), seed=0, classes=("noise", "screenshot", "screenshot_web", "screenshot_viewer"))
            for cls, paths in again.items():
                for fresh, original in zip(paths, self.paths[cls]):
                    self.assertEqual(fresh.read_bytes(), original.read_bytes(), f"{cls}/{fresh.name}")


@unittest.skipUnless(HAVE_IMAGING, "numpy/Pillow 미설치 — 사진/비사진 게이트 비활성")
class PhotoPositiveControlTest(unittest.TestCase):
    """The gate must not swallow photographs (mandatory control)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.generator = _load_generator()
        self.profile = _model_profile(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _photo_like(self, seed: int, size: tuple[int, int]) -> Path:
        path = self.root / f"photo-like-{seed}-{size[0]}x{size[1]}.jpg"
        path.write_bytes(self.generator.photo_like(seed, size, quality=90))
        return _with_sidecar(path)

    def test_photo_like_jpeg_is_photo_and_detectors_run(self) -> None:
        """QA-ADV-1: 보조 검사 — 양성 대조 — 사진 같은 JPEG는 사진으로 분류되고 생성 탐지 검사가 실행된다."""
        from deepfake_lens.image_class import classify_image

        for seed, size in ((0, (512, 512)), (1, (768, 576)), (2, (1600, 1200))):
            with self.subTest(seed=seed, size=size):
                path = self._photo_like(seed, size)
                image_class = classify_image(path)
                self.assertEqual(image_class.kind, "photo", image_class.reasons)
        for seed, size in ((3, (512, 512)), (4, (1024, 768))):
            with self.subTest(seed=seed, size=size, path="analyze_file"):
                path = self._photo_like(seed, size)
                item = analyze_file(path, pixel_mode="fast", model_path=self.profile)
                result = item.result
                assert result is not None
                coverage = {entry.check: entry for entry in result.coverage}
                self.assertEqual(coverage["image_class"].status, CoverageStatus.RAN)
                for check in ("pixel", "external_model"):
                    self.assertFalse(coverage[check].reason.startswith(NON_PHOTO_PREFIX), f"{check}: {coverage[check].reason}")
                self.assertEqual(coverage["pixel"].status, CoverageStatus.RAN)
                self.assertEqual(coverage["external_model"].status, CoverageStatus.RAN)
                self.assertIsNotNone(result.model_analysis)
                self.assertTrue(any(item.title == "이미지 유형: 사진" for item in result.evidence))
                self.assertNotIn("사진 아님", result.verdict)
                # Uncalibrated detectors still cannot decide (WP-A rule 4).
                self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)

    def test_photo_like_deep_layers_are_attempted(self) -> None:
        """QA-ADV-1: 보조 검사 — 양성 대조 — 사진에는 심층 계층(얼굴·인페인팅 등)이 시도된다."""
        path = self._photo_like(4, (640, 480))
        with patch("deepfake_lens.core._deep_image_layers") as deep:
            deep.return_value = deepfake_lens.core.DeepLayers()
            analyze_file(path, deep_signals=True)
        deep.assert_called_once()

    def test_real_photographs_are_photo(self) -> None:
        """QA-ADV-1: 보조 검사 — Real camera photos (scikit-learn's bundled china/flower JPEGs)."""
        try:
            import sklearn.datasets
        except ImportError:
            self.skipTest("scikit-learn 미설치 — 실사진 샘플 없음")
        from deepfake_lens.image_class import classify_image

        images = Path(sklearn.datasets.__file__).parent / "images"
        for name in ("china.jpg", "flower.jpg"):
            with self.subTest(image=name):
                image_class = classify_image(images / name)
                self.assertEqual(image_class.kind, "photo", image_class.reasons)


@unittest.skipUnless(HAVE_IMAGING, "numpy/Pillow 미설치 — 사진/비사진 게이트 비활성")
class BenchmarkFixtureClassTest(unittest.TestCase):
    """fixtures/benchmark: whatever the class, no detector-driven verdict."""

    def test_benchmark_fixture_classes(self) -> None:
        """QA-ADV-1: 보조 검사 — fixtures/benchmark 세 이미지의 유형 분류와 결론 — 작은 이미지·패턴은 판단 불가, A1111 메타데이터는 결정적 근거로 조작·생성 근거 있음."""
        from deepfake_lens.image_class import classify_image

        expected = {
            # 96x96: below the 128 px floor. Spec WP-D: whether photo or
            # pattern, the verdict must be undetermined — it is too_small.
            "real-like-texture.png": ("too_small", Verdict.UNDETERMINED),
            # Noise-free synthetic gradient.
            "ai-like-gradient.png": ("pattern", Verdict.UNDETERMINED),
            # 64x64 — gated, but its A1111 metadata still decides.
            "a1111-metadata-marker.png": ("too_small", Verdict.MANIPULATION_EVIDENCE),
        }
        for name, (kind, verdict) in expected.items():
            with self.subTest(image=name):
                path = BENCHMARK_DIR / name
                self.assertEqual(classify_image(path).kind, kind)
                result = analyze_file(path, pixel_mode="fast").result
                assert result is not None
                self.assertEqual(result.verdict_code, verdict)
                coverage = {entry.check: entry for entry in result.coverage}
                expected_prefix = "측정 범위 밖: 해상도" if kind == "too_small" else NON_PHOTO_PREFIX
                for check in GATED_CHECKS:
                    self.assertTrue(coverage[check].reason.startswith(expected_prefix), f"{check}: {coverage[check].reason}")


# ============================================================================
# Moved from deepfake_lens/tests/qa/test_qa_adv3.py (W2): module docstring above.
# ============================================================================


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "adversarial-text" / "human-about-ai"


class HumanTextsAboutAiTest(unittest.TestCase):
    """QA-ADV-3: keywords in human texts about AI are listed, never decisive."""

    paths: list[Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = sorted(FIXTURES.glob("human-*.txt"))

    def test_corpus_has_thirty_texts_with_identity_phrases(self) -> None:
        """QA-ADV-3: 보조 검사 — AI에 대해 쓴 사람 글 픽스처 30건에 정체성 문구가 실제로 들어 있다."""
        self.assertEqual(len(self.paths), 30)
        for path in self.paths:
            text = " ".join(path.read_text(encoding="utf-8").lower().split())
            self.assertTrue(any(phrase in text for phrase in AI_IDENTITY_PHRASES), path.name)

    def test_keyword_is_listed_but_never_concludes(self) -> None:
        """QA-ADV-3: AI에 대해 쓴 사람 글 30건("언어 모델", "as an AI" 포함) → 키워드는 근거 목록에 나타나되 결론은 "참고 — 근거 부족".

        The product renders the verdict as "참고: 근거 부족 …" (G24/G4).
        """
        self.assertEqual(len(self.paths), 30)
        for path in self.paths:
            with self.subTest(path.name):
                item = analyze_file(path)
                result = item.result
                assert result is not None
                titles = [e.title for e in result.evidence if e.kind == EvidenceKind.LEXICAL]
                self.assertIn("AI 자기표현 문구", titles)
                self.assertEqual(result.grade, Grade.REFERENCE)
                self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                self.assertEqual(result.band, RiskBand.UNKNOWN)
                self.assertEqual(result.score, 0)
                self.assertTrue(result.verdict.startswith("참고: 근거 부족"), result.verdict)
                self.assertEqual(result.limitations[0], TEXT_LEGAL_LIMITATION)
                # Nothing but lexical items point toward synthesis.
                self.assertFalse([
                    e for e in result.evidence
                    if e.direction == EvidenceDirection.SYNTHETIC and e.kind != EvidenceKind.LEXICAL
                ])
                for e in result.evidence:
                    if e.kind == EvidenceKind.LEXICAL:
                        self.assertEqual(e.strength.value, "weak")

    def test_folder_scan_counts_no_conclusions(self) -> None:
        """QA-ADV-3: 보조 검사 — 폴더 검사 요약에 사람 글 30건이 모두 있고 조작·생성 근거 있음은 0건이다."""
        summary, items = scan_directory(FIXTURES)
        texts = [item for item in items if item.name.startswith("human-")]
        self.assertEqual(len(texts), 30)
        self.assertEqual(summary.manipulation_evidence, 0)
        self.assertEqual(summary.high, 0)
        self.assertEqual(summary.medium, 0)

    def test_lexical_source_hint_is_reference_only(self) -> None:
        """QA-ADV-3: 보조 검사 — D11: the text source guess from words in the text is "참고: …" with
        confidence unknown — never a medium/high attribution."""
        hinted = 0
        for path in self.paths:
            with self.subTest(path.name):
                item = analyze_file(path)
                assert item.result is not None
                guess = item.result.source_guess
                self.assertEqual(guess.confidence, SourceConfidence.UNKNOWN, guess)
                self.assertTrue(guess.label.startswith("참고:") or guess.label == "출처 단서 없음", guess.label)
                hinted += guess.label.startswith("참고:")
        self.assertGreater(hinted, 0)
        for text, label in (
            ("ChatGPT에 대해 사람이 쓴 칼럼입니다. openai 발표를 인용했습니다.", "참고: 원문에 ChatGPT/OpenAI 언급"),
            ("As an AI language model은 흔한 밈이다.", "참고: AI 어시스턴트 문체 유사"),
        ):
            guess = analyze_text(text).source_guess
            self.assertEqual((guess.label, guess.confidence), (label, SourceConfidence.UNKNOWN))

    def test_keyword_stacking_still_cannot_conclude(self) -> None:
        """QA-ADV-3: 보조 검사 — 키워드를 여러 개 쌓아도 어휘적 근거는 결론을 만들지 못한다."""
        stacked = ("As an AI language model, I cannot browse. 인공지능으로서 언어 모델로서 답합니다. "
                   "결론적으로 요약하자면 다음과 같습니다. 균형 잡힌 다양한 관점이 중요합니다.\n"
                   + "\n".join(f"{i}. 항목" for i in range(1, 9)))
        result = analyze_text(stacked * 3)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertEqual(result.score, 0)
        self.assertTrue(all(signal.weight <= 5 for signal in result.signals))


if __name__ == "__main__":
    unittest.main()
