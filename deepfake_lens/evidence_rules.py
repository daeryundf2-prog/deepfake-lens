"""Mapping from analyzer outputs to :class:`EvidenceItem` (WP-A table).

Every function here is pure: it takes an analyzer's output and returns
evidence items or reference signals. ``core.py`` wires them to the
analyzers; ``decision.decide`` draws the conclusion. Adding a new kind of
evidence means adding a function here and a call in ``core.py`` — never a
score.

Classification (phase-0 spec, WP-A):

| source                                            | kind          | direction | strength |
| C2PA valid + trusted + AI digitalSourceType       | deterministic | synthetic | strong   |
| generator metadata in a tool-identifying field     | deterministic | synthetic | strong   |
| C2PA valid + trusted + digitalCapture, hash match | deterministic | authentic | strong   |
| square generator resolution / missing metadata     | deterministic | neutral   | weak     |
| external model output                              | statistical   | synthetic | probability only when calibrated |
| deep layers (face seam, inpaint, tracking …)       | statistical   | synthetic | weak, no probability |
| AI identity phrases, template connectors, style    | lexical       | synthetic | weak     |
| pixel ensemble / frequency / legacy heuristics     | reference_signals only |
| image class (photo / screenshot / pattern …)      | deterministic | neutral   | weak     |
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .image_class import DIRECTION_NEUTRAL_NOTE, ImageClass
from .image_metadata import guess_image_source
from .result_types import (
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceSignal,
    EvidenceStrength,
    ExternalModelAnalysis,
    SourceConfidence,
)

# Metadata keys whose values name the producing tool. A generator name in
# one of these is a deterministic statement by the file; the same word in a
# free-text field ("airport runway", "flux capacitor") is not.
TOOL_FIELD_MARKERS = (
    "software", "creator", "generator", "tool", "producer", "parameters",
    "prompt", "workflow", "model", "credit", "source", "comment",
)

# IPTC digital source type vocabulary (cv.iptc.org/newscodes/digitalsourcetype).
C2PA_SYNTHETIC_SOURCE_TYPES = frozenset({
    "trainedAlgorithmicMedia",
    "compositeWithTrainedAlgorithmicMedia",
    "algorithmicMedia",
    "compositeSynthetic",
})
C2PA_CAPTURE_SOURCE_TYPES = frozenset({"digitalCapture"})

# An uncalibrated model score at or above the midpoint of the runtimes'
# 0-100 scale (probability 0.5 x 100, see model_runtimes._normalize_score)
# is recorded as pointing toward synthesis; below it, as neutral. The
# direction only blocks an authenticity conclusion (rule 5); it never
# produces a manipulation conclusion without calibration (rule 4).
MODEL_RAW_SCORE_MIDPOINT = 50

# Square sizes on the 64-px latent grid (SD/SDXL/Flux defaults are
# 512/768/1024) — a weak, non-directional workflow hint.
GENERATOR_SQUARE_MIN_SIDE = 512
GENERATOR_LATENT_GRID = 64


def _tool_field_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in metadata.items()
        if key != "header.text" and any(marker in key.lower() for marker in TOOL_FIELD_MARKERS)
    }


def image_metadata_evidence(
    metadata: Mapping[str, str],
    dimensions: tuple[int, int] | None,
) -> list[EvidenceItem]:
    """Deterministic items from parsed image metadata and header."""
    items: list[EvidenceItem] = []
    tool_guess = guess_image_source(_tool_field_metadata(metadata))
    any_guess = guess_image_source(dict(metadata))
    if tool_guess.confidence == SourceConfidence.HIGH:
        reason = tool_guess.reasons[0] if tool_guess.reasons else "생성 도구 단서가 발견되었습니다."
        items.append(EvidenceItem(
            "생성 도구 메타데이터",
            f"{tool_guess.label}: {reason}",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "metadata",
        ))
    elif any_guess.confidence in {SourceConfidence.HIGH, SourceConfidence.MEDIUM}:
        reason = any_guess.reasons[0] if any_guess.reasons else "생성 도구 단서가 발견되었습니다."
        items.append(EvidenceItem(
            "생성 도구 메타데이터 단서",
            f"{any_guess.label}: {reason} 도구 식별 필드가 아닌 위치(헤더 문자열·일반 필드)에서 발견되어 단독 결론 근거로 쓰지 않습니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "metadata",
        ))
    if not metadata:
        items.append(EvidenceItem(
            "메타데이터 부재",
            "메타데이터가 없거나 읽지 못했습니다. 이는 사람이 만든 파일이라는 뜻도, 생성 파일이라는 뜻도 아닙니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "metadata",
        ))
    if dimensions:
        width, height = dimensions
        if width == height and width >= GENERATOR_SQUARE_MIN_SIDE and width % GENERATOR_LATENT_GRID == 0:
            items.append(EvidenceItem(
                "생성 모델에 흔한 정사각 해상도",
                f"{width}x{height} 해상도는 생성 이미지 워크플로에서 자주 쓰이지만 카메라·편집 도구도 만들 수 있습니다.",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "metadata",
            ))
    return items


def _c2pa_source_types(validation: Mapping[str, object]) -> set[str]:
    raw = validation.get("digital_source_types")
    return {str(item).rsplit("/", 1)[-1] for item in raw} if isinstance(raw, (list, tuple, set)) else set()


def _codes(raw: object) -> set[str]:
    return {str(code) for code in raw} if isinstance(raw, (list, tuple, set)) else set()


def c2pa_evidence(validation: Mapping[str, object] | None) -> list[EvidenceItem]:
    """Deterministic items from ``c2pa.validate_c2pa_manifest`` output."""
    if not validation or not validation.get("present"):
        return []
    source_types = _c2pa_source_types(validation)
    synthetic_types = sorted(source_types & C2PA_SYNTHETIC_SOURCE_TYPES)
    capture = bool(source_types & C2PA_CAPTURE_SOURCE_TYPES)
    success = _codes(validation.get("success_codes"))
    failure = _codes(validation.get("failure_codes"))
    hash_ok = "assertion.dataHash.match" in success and not any("dataHash.mismatch" in code for code in failure)
    verified = validation.get("status") == "valid" and bool(validation.get("trusted")) and hash_ok
    signature = validation.get("signature")
    signer = str(signature.get("common_name", "알 수 없음")) if isinstance(signature, dict) else "알 수 없음"
    state = str(validation.get("state", ""))
    codes = ", ".join(sorted(failure)) or "세부 코드 없음"
    if verified and synthetic_types:
        return [EvidenceItem(
            "C2PA 서명: 생성형 AI 출처 선언",
            f"신뢰 서명자 {signer}의 유효한 매니페스트가 digitalSourceType={', '.join(synthetic_types)}를 선언합니다(해시 일치).",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "c2pa",
        )]
    if verified and capture:
        return [EvidenceItem(
            "C2PA 서명: 카메라 촬영 출처",
            f"신뢰 서명자 {signer}의 유효한 매니페스트가 digitalCapture를 선언하며 콘텐츠 해시가 일치합니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.AUTHENTIC, EvidenceStrength.STRONG, "c2pa",
        )]
    if verified:
        return [EvidenceItem(
            "C2PA 매니페스트 검증됨(출처 유형 미선언)",
            f"신뢰 서명자 {signer}의 유효한 매니페스트이나 촬영/생성 출처 유형을 선언하지 않았습니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE, "c2pa",
        )]
    if synthetic_types:
        return [EvidenceItem(
            "C2PA 매니페스트의 생성형 AI 출처 선언(검증 미완료)",
            f"매니페스트가 digitalSourceType={', '.join(synthetic_types)}를 선언하나 검증 상태 {state} ({codes}) — 서명 신뢰 또는 해시 검증이 완료되지 않았습니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "c2pa",
        )]
    return [EvidenceItem(
        "C2PA 매니페스트 존재(검증 미완료)",
        f"서명자 {signer}, 검증 상태 {state} ({codes}). 신뢰 저장소에 없는 서명자이거나 무결성 검증이 끝나지 않아 결론 근거로 쓰지 않습니다.",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "c2pa",
    )]


def model_evidence(model: ExternalModelAnalysis | None) -> EvidenceItem | None:
    """Statistical item for an external model result that produced a score.

    Calibration fields come from the model profile once WP-I measures it;
    until then ``probability`` stays None and the raw score is kept as
    ``raw_score`` so it cannot be read as a probability (QA-OUT-5).
    """
    if model is None or not model.available:
        return None
    calibrated = model.probability is not None and bool(model.calibration_id) and bool(model.measured_on)
    synthetic = (model.probability >= 0.5) if calibrated and model.probability is not None else model.score >= MODEL_RAW_SCORE_MIDPOINT
    if calibrated:
        detail = f"{model.model}: 보정 확률 {model.probability:.2f} (보정 {model.calibration_id}, 측정 {model.measured_on})."
    else:
        detail = f"{model.model}: 원점수 {model.score}/100 — 보정되지 않은 값이며 확률이 아닙니다. 결론에 참여하지 않습니다."
    return EvidenceItem(
        "외부 모델 출력" if calibrated else "외부 모델 원점수(미보정)",
        detail,
        EvidenceKind.STATISTICAL,
        EvidenceDirection.SYNTHETIC if synthetic else EvidenceDirection.NEUTRAL,
        EvidenceStrength.MODERATE if calibrated else EvidenceStrength.WEAK,
        "model",
        probability=model.probability if calibrated else None,
        probability_ci=model.probability_ci if calibrated else None,
        calibration_id=model.calibration_id if calibrated else None,
        measured_on=model.measured_on if calibrated else None,
        raw_score=model.score,
    )


def deep_layer_evidence(title: str, detail: str, layer: str, raw_score: int) -> EvidenceItem:
    """Uncalibrated deep-layer flag: statistical, no probability, no vote."""
    return EvidenceItem(
        title,
        f"{detail} (원점수 {raw_score}/100, 미보정 — 결론에 참여하지 않습니다)",
        EvidenceKind.STATISTICAL, EvidenceDirection.SYNTHETIC, EvidenceStrength.WEAK, layer,
        raw_score=raw_score,
    )


def lexical_evidence(signals: Iterable[EvidenceSignal], *, layer: str = "text") -> list[EvidenceItem]:
    """Keyword/phrase/style signals — always lexical, weak, non-deciding (G4)."""
    return [
        EvidenceItem(signal.title, signal.detail, EvidenceKind.LEXICAL, EvidenceDirection.SYNTHETIC, EvidenceStrength.WEAK, layer)
        for signal in signals
    ]


def document_metadata_evidence(ai_tool_value: str | None) -> list[EvidenceItem]:
    if not ai_tool_value:
        return []
    return [EvidenceItem(
        "문서 메타데이터의 AI 도구명",
        f"문서 작성/생성 도구 필드에 AI 도구명이 기록되어 있습니다: {ai_tool_value}. 메타데이터는 편집으로 바뀔 수 있습니다.",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "document_metadata",
    )]


def reference_signal(title: str, detail: str, raw_score: int) -> EvidenceSignal:
    """An unmeasured heuristic kept for display only (never decides)."""
    return EvidenceSignal(title, detail, int(raw_score))


def image_class_evidence(image_class: ImageClass | None) -> list[EvidenceItem]:
    """The photo/non-photo gate's result (WP-D, G13): deterministic, neutral, weak.

    It records *which* detectors apply, never a direction — a screenshot is
    not evidence of manipulation and a "photo" is not evidence of
    authenticity.
    """
    if image_class is None:
        return []
    reasons = "; ".join(image_class.reasons)
    if image_class.is_photo:
        detail = f"분류: 사진 — 생성 탐지 검사 적용 대상. 판별 근거: {reasons}. {DIRECTION_NEUTRAL_NOTE}"
    else:
        detail = (
            f"분류: {image_class.label}({image_class.kind}) — 생성·조작 탐지 검사를 적용하지 않았습니다. "
            f"판별 근거: {reasons}. {DIRECTION_NEUTRAL_NOTE}"
        )
    return [EvidenceItem(
        f"이미지 유형: {image_class.label}",
        detail,
        EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "image_class",
    )]
