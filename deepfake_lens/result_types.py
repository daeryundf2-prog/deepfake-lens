"""Result type definitions shared across the scan pipeline.

These dataclasses/enums are the schema of every scan artifact (JSON reports,
review stores, evidence statements). They live in a leaf module so both the
pipeline (``core.py``) and the deserializers (``serialization.py``) can
import them without a cycle. Public names are re-exported from ``core``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from .pixel import PixelAnalysis


@dataclass(frozen=True)
class ExternalModelAnalysis:
    """Score supplied by an external model profile (onnx/torch/HF runtime).

    Lives in this leaf module rather than ``model_adapter`` so the runtime
    executors in ``model_runtimes`` can build results without importing the
    dispatcher — keeping the adapter → runtimes dependency one-directional.
    Re-exported from ``model_adapter`` for existing callers.
    """

    available: bool
    score: int
    confidence: str
    model: str
    detail: str
    limitations: list[str] = field(default_factory=list)
    # Per-member results when several profiles ran (model zoo / profile set).
    models: list[dict[str, object]] = field(default_factory=list)
    # Calibration provenance (contract v2). Set only by a profile whose
    # score mapping was measured on a held-out corpus (WP-I); otherwise all
    # None and ``score`` is an uncalibrated 0-100 value, not a probability.
    probability: float | None = None
    probability_ci: tuple[float, float] | None = None
    calibration_id: str | None = None
    measured_on: str | None = None


class RiskBand(str, Enum):
    """Legacy triage band, now derived from :class:`Verdict` (G5/G6).

    ``MEDIUM`` is kept only so stored reports from schema v1 still load;
    no code path produces it any more — see ``band_for_verdict``.
    """

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


RISK_LABELS = {
    RiskBand.UNKNOWN: "판단 어려움",
    RiskBand.LOW: "낮음",
    RiskBand.MEDIUM: "주의",
    RiskBand.HIGH: "높음",
}


class SourceConfidence(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


SOURCE_CONFIDENCE_LABELS = {
    SourceConfidence.UNKNOWN: "알 수 없음",
    SourceConfidence.LOW: "낮음",
    SourceConfidence.MEDIUM: "중간",
    SourceConfidence.HIGH: "높음",
}


@dataclass(frozen=True)
class SourceGuess:
    label: str
    confidence: SourceConfidence
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def unknown(cls, reason: str = "출처를 판단할 메타데이터나 명시적 단서가 없습니다.") -> "SourceGuess":
        return cls("출처 단서 없음", SourceConfidence.UNKNOWN, [reason])


@dataclass(frozen=True)
class EvidenceSignal:
    title: str
    detail: str
    weight: int


class Verdict(str, Enum):
    """The only three conclusions a result may state (G6)."""

    MANIPULATION_EVIDENCE = "manipulation_evidence"
    AUTHENTICITY_EVIDENCE = "authenticity_evidence"
    UNDETERMINED = "undetermined"


VERDICT_LABELS = {
    Verdict.MANIPULATION_EVIDENCE: "조작·생성 근거 있음",
    Verdict.AUTHENTICITY_EVIDENCE: "원본성 근거 있음",
    Verdict.UNDETERMINED: "판단 불가",
}


class Grade(str, Enum):
    """Whether a conclusion may be cited in an expert opinion (G24)."""

    EVIDENCE = "evidence"
    REFERENCE = "reference"


GRADE_LABELS = {
    Grade.EVIDENCE: "감정 근거로 사용 가능",
    Grade.REFERENCE: "참고",
}


class EvidenceKind(str, Enum):
    """How an evidence item was produced — never mixed in display."""

    DETERMINISTIC = "deterministic"  # metadata, C2PA, recompression fingerprints
    STATISTICAL = "statistical"  # model outputs
    LEXICAL = "lexical"  # keywords / phrase lists / style statistics


EVIDENCE_KIND_LABELS = {
    EvidenceKind.DETERMINISTIC: "결정적 근거",
    EvidenceKind.STATISTICAL: "통계적 근거",
    EvidenceKind.LEXICAL: "어휘적 근거",
}


class EvidenceDirection(str, Enum):
    SYNTHETIC = "synthetic"
    AUTHENTIC = "authentic"
    NEUTRAL = "neutral"


EVIDENCE_DIRECTION_LABELS = {
    EvidenceDirection.SYNTHETIC: "조작·생성 방향",
    EvidenceDirection.AUTHENTIC: "원본성 방향",
    EvidenceDirection.NEUTRAL: "중립",
}


class EvidenceStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


EVIDENCE_STRENGTH_LABELS = {
    EvidenceStrength.STRONG: "강",
    EvidenceStrength.MODERATE: "중",
    EvidenceStrength.WEAK: "약",
}

# Legacy ``signals[].weight`` derived from an evidence item so v1 consumers
# that rank or sum weights keep working (G5). Only synthetic-direction items
# carry a positive weight — an authentic or neutral item must never raise a
# legacy consumer's suspicion total. Values are ordinal placeholders, not
# measured likelihoods.
LEGACY_WEIGHT_BY_STRENGTH = {
    EvidenceStrength.STRONG: 60,
    EvidenceStrength.MODERATE: 25,
    EvidenceStrength.WEAK: 5,
}


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence with its provenance class (G5/G12).

    ``probability`` is only ever set for a *calibrated* statistical output
    (``calibration_id`` and ``measured_on`` present); an uncalibrated model
    output is carried as ``raw_score`` instead so no consumer can mistake
    it for a probability (QA-OUT-5).
    """

    title: str
    detail: str
    kind: EvidenceKind
    direction: EvidenceDirection
    strength: EvidenceStrength
    layer: str
    probability: float | None = None
    probability_ci: tuple[float, float] | None = None
    calibration_id: str | None = None
    measured_on: str | None = None
    raw_score: int | None = None

    @property
    def is_calibrated(self) -> bool:
        return self.probability is not None and bool(self.calibration_id) and bool(self.measured_on)

    def legacy_signal(self) -> "EvidenceSignal":
        weight = LEGACY_WEIGHT_BY_STRENGTH[self.strength] if self.direction == EvidenceDirection.SYNTHETIC else 0
        return EvidenceSignal(self.title, self.detail, weight)

    def to_json(self) -> dict[str, object]:
        return {
            "title": self.title,
            "detail": self.detail,
            "kind": self.kind.value,
            "direction": self.direction.value,
            "strength": self.strength.value,
            "layer": self.layer,
            "probability": self.probability,
            "probability_ci": list(self.probability_ci) if self.probability_ci is not None else None,
            "calibration_id": self.calibration_id,
            "measured_on": self.measured_on,
            "raw_score": self.raw_score,
        }


class CoverageStatus(str, Enum):
    RAN = "ran"
    SKIPPED = "skipped"
    FAILED = "failed"


COVERAGE_STATUS_LABELS = {
    CoverageStatus.RAN: "실행",
    CoverageStatus.SKIPPED: "미실행",
    CoverageStatus.FAILED: "실패",
}

# Korean display names for coverage check identifiers. Unknown names fall
# back to the identifier itself (e.g. per-member "model:<name>" entries).
CHECK_LABELS = {
    "metadata": "메타데이터",
    "c2pa": "C2PA 출처 검증",
    "image_class": "이미지 유형 판별(사진/비사진)",
    "pixel": "픽셀 휴리스틱(참고)",
    "external_model": "외부 모델",
    "face_manipulation": "얼굴 검사",
    "inpaint": "인페인팅 검사",
    "faceswap_seam": "페이스스왑 경계면 검사",
    "rppg": "rPPG 맥박 검사",
    "avatar": "아바타 검사",
    "lipsync": "립싱크 검사",
    "face_track": "얼굴 트랙 검사",
    "audio_analysis": "오디오 분석",
    "audio_features": "오디오 특징 추출",
    "video_analysis": "영상 분석",
    "document_text": "문서 텍스트 추출",
    "text_lexical": "어휘·문체 신호",
    "archive": "압축 해제",
    "archive_member": "압축 구성 파일",
}


def check_label(check: str) -> str:
    if check.startswith("model:"):
        return f"외부 모델({check.split(':', 1)[1]})"
    return CHECK_LABELS.get(check, check)


@dataclass(frozen=True)
class CoverageEntry:
    """What was checked for one file, and why a check did not run (G12).

    ``reason`` is mandatory for skipped/failed entries; a failed entry's
    reason starts with the exception class name (see ``checks.run_check``).
    """

    check: str
    status: CoverageStatus
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status != CoverageStatus.RAN and not self.reason.strip():
            raise ValueError(f"coverage entry {self.check!r} with status {self.status.value} needs a reason")

    def describe(self) -> str:
        """Korean one-liner, e.g. "얼굴 검사 미실행: 얼굴 미검출"."""
        head = f"{check_label(self.check)} {COVERAGE_STATUS_LABELS[self.status]}"
        return f"{head}: {self.reason}" if self.reason else head

    def to_json(self) -> dict[str, object]:
        return {"check": self.check, "status": self.status.value, "reason": self.reason}


def band_for_verdict(verdict: Verdict) -> RiskBand:
    """Legacy band derived from the verdict — never MEDIUM (G5)."""
    return {
        Verdict.MANIPULATION_EVIDENCE: RiskBand.HIGH,
        Verdict.AUTHENTICITY_EVIDENCE: RiskBand.LOW,
        Verdict.UNDETERMINED: RiskBand.UNKNOWN,
    }[verdict]


@dataclass(frozen=True)
class ClassificationResult:
    score: int
    band: RiskBand
    band_label: str
    verdict: str
    signals: list[EvidenceSignal]
    limitations: list[str]
    source_guess: SourceGuess
    next_checks: list[str]
    pixel_analysis: PixelAnalysis | None = None
    model_analysis: ExternalModelAnalysis | None = None
    ai_score: int = 0
    source_attribution_label: str = ""
    # Video-only: extracted audio track scored by the audio pipeline
    # (ffmpeg + AASIST etc); None when not requested or unavailable.
    # Appended last: positional constructions predate this field.
    av_audio: dict | None = None
    # Office-document provenance fields preserved verbatim for the
    # forensic record (pdf.producer, docx.creator, ...); None for non-doc
    # kinds. Appended last for the same positional-construction reason.
    document_metadata: dict | None = None
    # --- Result contract v2 (phase 0, G5/G6/G12/G24). Appended with
    # defaults so positional v1 constructions and v1 JSON still load; new
    # code builds results through ``core.build_classification_result`` which
    # derives band/score/signals from these fields.
    verdict_code: Verdict = Verdict.UNDETERMINED
    grade: Grade = Grade.EVIDENCE
    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: list[CoverageEntry] = field(default_factory=list)
    probability: float | None = None
    probability_ci: tuple[float, float] | None = None
    score_is_calibrated: bool = False
    # Unmeasured heuristics (pixel ensemble, frequency, legacy audio/video
    # heuristics, fusion score). Shown for reference; never decide.
    reference_signals: list[EvidenceSignal] = field(default_factory=list)

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS[self.verdict_code]

    @property
    def grade_label(self) -> str:
        return GRADE_LABELS[self.grade]

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["band"] = self.band.value
        data["source_guess"]["confidence"] = self.source_guess.confidence.value
        data["verdict_code"] = self.verdict_code.value
        data["verdict_label"] = self.verdict_label
        data["grade"] = self.grade.value
        data["grade_label"] = self.grade_label
        data["evidence"] = [item.to_json() for item in self.evidence]
        data["coverage"] = [entry.to_json() for entry in self.coverage]
        data["probability_ci"] = list(self.probability_ci) if self.probability_ci is not None else None
        return data


@dataclass(frozen=True)
class ScanItem:
    path: str
    name: str
    kind: str
    status: str
    size_bytes: int
    result: ClassificationResult | None = None
    error: str | None = None
    duplicate_of: str | None = None
    # SHA-256 of the file content as analyzed (G11/G30). Set by the folder
    # scanner, which also keys the scan cache on it; None when the file was
    # not hashed (single-file paths, oversize skips, unreadable files).
    sha256: str | None = None

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["result"] = self.result.to_json() if self.result else None
        return data


@dataclass(frozen=True)
class BatchScanSummary:
    total: int
    analyzed: int
    high: int
    medium: int
    unknown: int
    low: int
    unsupported_or_failed: int
    capped: bool
    cached: int = 0
    duplicates: int = 0
    skipped: int = 0
    external_model_active: int = 0
    # Verdict counts over analyzed items (contract v2). ``high``/``low``/
    # ``unknown`` above are the same counts under legacy band names and
    # ``medium`` is always 0.
    manipulation_evidence: int = 0
    authenticity_evidence: int = 0
    undetermined: int = 0
    # Analyzed items whose coverage records at least one failed check.
    checks_failed: int = 0

    def to_json(self) -> dict[str, object]:
        # D16: the summary JSON counts verdicts only. The legacy band counts
        # (high/medium/unknown/low) stay readable as attributes for library
        # callers, but no front end (gui.js counts verdicts itself, reports
        # use the verdict attributes) reads them from JSON any more.
        return {key: value for key, value in asdict(self).items() if key not in LEGACY_SUMMARY_KEYS}


# Band-count fields of BatchScanSummary that are not serialized (D16).
LEGACY_SUMMARY_KEYS = frozenset({"high", "medium", "unknown", "low"})
