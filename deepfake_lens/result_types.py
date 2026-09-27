"""Result type definitions shared across the scan pipeline.

These dataclasses/enums are the schema of every scan artifact (JSON reports,
review stores, evidence statements). They live in a leaf module so both the
pipeline (``core.py``) and the deserializers (``serialization.py``) can
import them without a cycle. Public names are re-exported from ``core``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from .model_adapter import ExternalModelAnalysis
from .pixel import PixelAnalysis


class RiskBand(str, Enum):
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

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["band"] = self.band.value
        data["source_guess"]["confidence"] = self.source_guess.confidence.value
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

    def to_json(self) -> dict[str, object]:
        return asdict(self)
