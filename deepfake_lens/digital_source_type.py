"""Digital source type vocabulary: IPTC NewsCodes + the C2PA namespace (techniques-2026 item 2).

Source: IPTC NewsCodes "Digital Source Type" controlled vocabulary,
https://cv.iptc.org/newscodes/digitalsourcetype/ (CC BY 4.0, IPTC), release
2024-10-23 — fetched 2026-10-15 as JSON (``?format=json&lang=en-GB``); the
research note (new_techniques_2026-10.md §1.2) found no change in 2025–2026.
All 20 concepts are listed, the retired ones (``minorHumanEdits``,
``digitalArt`` retired 2024-09-17, ``softwareImage`` retired 2022-06-14)
included so an old file's value is still recognised. The C2PA specification
adds its own namespace ``http://c2pa.org/digitalsourcetype/`` (2.2, May
2025): ``trainedAlgorithmicData`` (replacing the 2.0/2.1 bare value
``c2pa.trainedAlgorithmicData``) and ``empty``.

Each value has a *category* that decides how a declaration is used:

- ``generative`` — trainedAlgorithmicMedia, compositeWithTrainedAlgorithmicMedia,
  compositeSynthetic, trainedAlgorithmicData: the asset is (or contains)
  generative-AI output. ``algorithmicMedia`` (pure algorithmic, no training
  data) is in the same set — it was already a synthetic type before this
  table (C2PA_SYNTHETIC_SOURCE_TYPES) and keeps that meaning (generated,
  not captured).
- ``capture`` — digitalCapture: the only value that can support authenticity
  (and only under a valid, trusted C2PA signature with a hash match).
- ``screen`` — screenCapture: a capture of a screen (a re-shot of other
  content) — recorded as a fact, never as camera capture.
- everything else (``computationalCapture``, film/print digitisation, human
  edits, algorithmic enhancement, digital creation, data-driven media,
  virtual recording, composites of captures or of unknown elements, empty) —
  a neutral fact.

A value whose namespace is neither IPTC's nor C2PA's (or that is not in the
table) is ``unknown`` and is reported verbatim — never mapped by its last
path segment alone.
"""

from __future__ import annotations

from dataclasses import dataclass

IPTC_NAMESPACES = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/",
    "https://cv.iptc.org/newscodes/digitalsourcetype/",
)
C2PA_NAMESPACES = (
    "http://c2pa.org/digitalsourcetype/",
    "https://c2pa.org/digitalsourcetype/",
)
# C2PA 2.0/2.1 wrote the C2PA-specific value without a namespace.
C2PA_LEGACY_PREFIX = "c2pa."

CATEGORY_GENERATIVE = "generative"
CATEGORY_CAPTURE = "capture"
CATEGORY_SCREEN = "screen"
CATEGORY_NEUTRAL = "neutral"
CATEGORY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceType:
    code: str
    namespace: str  # "iptc" or "c2pa"
    label_en: str
    label_ko: str
    category: str
    retired: str = ""  # retirement date (YYYY-MM-DD), "" when current

    @property
    def display(self) -> str:
        retired = f", {self.retired} 퇴역 값" if self.retired else ""
        return f"{self.code}({self.label_ko}{retired})"


# IPTC NewsCodes digitalsourcetype, release 2024-10-23 — 20 concepts in the
# vocabulary's own order; English labels are the en-GB prefLabel.
IPTC_SOURCE_TYPES: tuple[SourceType, ...] = (
    SourceType("digitalCapture", "iptc", "Digital capture sampled from real life", "실사 디지털 촬영", CATEGORY_CAPTURE),
    SourceType("computationalCapture", "iptc", "Multi-frame computational capture sampled from real life", "다중 프레임 연산 촬영(스마트폰 합성 등)", CATEGORY_NEUTRAL),
    SourceType("negativeFilm", "iptc", "Digitised from a transparent negative", "필름 음화 디지털화", CATEGORY_NEUTRAL),
    SourceType("positiveFilm", "iptc", "Digitised from a transparent positive", "필름 양화 디지털화", CATEGORY_NEUTRAL),
    SourceType("print", "iptc", "Digitised from a non-transparent medium", "인화물 디지털화", CATEGORY_NEUTRAL),
    SourceType("minorHumanEdits", "iptc", "Original media with minor human edits", "원본에 사람의 경미한 편집", CATEGORY_NEUTRAL, "2024-09-17"),
    SourceType("humanEdits", "iptc", "Human-edited media", "사람이 편집한 미디어(비생성 도구)", CATEGORY_NEUTRAL),
    SourceType("compositeWithTrainedAlgorithmicMedia", "iptc", "Edited using Generative AI", "생성형 AI로 편집", CATEGORY_GENERATIVE),
    SourceType("algorithmicallyEnhanced", "iptc", "Algorithmically-altered media", "알고리즘 보정(주 내용 불변)", CATEGORY_NEUTRAL),
    SourceType("softwareImage", "iptc", "Created by software", "소프트웨어로 생성", CATEGORY_NEUTRAL, "2022-06-14"),
    SourceType("digitalArt", "iptc", "Digital art", "디지털 아트", CATEGORY_NEUTRAL, "2024-09-17"),
    SourceType("digitalCreation", "iptc", "Digital creation", "사람의 디지털 창작(비생성 도구)", CATEGORY_NEUTRAL),
    SourceType("dataDrivenMedia", "iptc", "Data-driven media", "데이터 기반 미디어", CATEGORY_NEUTRAL),
    SourceType("trainedAlgorithmicMedia", "iptc", "Created using Generative AI", "생성형 AI로 생성", CATEGORY_GENERATIVE),
    SourceType("algorithmicMedia", "iptc", "Pure algorithmic media", "순수 알고리즘 생성 미디어", CATEGORY_GENERATIVE),
    SourceType("screenCapture", "iptc", "Screen capture", "화면 캡처(재촬영)", CATEGORY_SCREEN),
    SourceType("virtualRecording", "iptc", "Virtual event recording", "가상 행사 녹화(생성형 AI 포함 가능)", CATEGORY_NEUTRAL),
    SourceType("composite", "iptc", "Composite of elements", "요소 합성(생성형 AI 포함 여부 불명)", CATEGORY_NEUTRAL),
    SourceType("compositeCapture", "iptc", "Composite of captured elements", "촬영 요소만의 합성", CATEGORY_NEUTRAL),
    SourceType("compositeSynthetic", "iptc", "Composite including generative AI elements", "생성형 AI 요소를 포함한 합성", CATEGORY_GENERATIVE),
)
# C2PA 2.2+ namespace (C2PA Technical Specification 2.2, "Digital Source Type").
C2PA_SOURCE_TYPES: tuple[SourceType, ...] = (
    SourceType("trainedAlgorithmicData", "c2pa", "Trained algorithmic data", "생성형 AI가 만든 비매체 데이터", CATEGORY_GENERATIVE),
    SourceType("empty", "c2pa", "Empty (created with no digital content)", "빈 자산으로 생성", CATEGORY_NEUTRAL),
)
IPTC_VOCABULARY_RELEASE = "2024-10-23"

_IPTC = {entry.code: entry for entry in IPTC_SOURCE_TYPES}
_C2PA = {entry.code: entry for entry in C2PA_SOURCE_TYPES}
_IPTC_LOWER = {code.lower(): entry for code, entry in _IPTC.items()}
_C2PA_LOWER = {code.lower(): entry for code, entry in _C2PA.items()}


@dataclass(frozen=True)
class DeclaredSourceType:
    """One declared value: the text as written and what it maps to (None = unknown)."""

    raw: str
    entry: SourceType | None

    @property
    def category(self) -> str:
        return self.entry.category if self.entry is not None else CATEGORY_UNKNOWN

    @property
    def code(self) -> str:
        return self.entry.code if self.entry is not None else self.raw

    @property
    def display(self) -> str:
        if self.entry is None:
            return f"「{self.raw[:120]}」(알 수 없는 값)"
        return self.entry.display


def classify_source_type(value: str) -> DeclaredSourceType:
    """Map one declared digitalSourceType value (IRI, bare code or legacy ``c2pa.*``)."""
    raw = (value or "").strip()
    lowered = raw.lower()
    for namespace in IPTC_NAMESPACES:
        if lowered.startswith(namespace):
            return DeclaredSourceType(raw, _IPTC_LOWER.get(lowered[len(namespace):].strip("/")))
    for namespace in C2PA_NAMESPACES:
        if lowered.startswith(namespace):
            return DeclaredSourceType(raw, _C2PA_LOWER.get(lowered[len(namespace):].strip("/")))
    if lowered.startswith(C2PA_LEGACY_PREFIX):
        return DeclaredSourceType(raw, _C2PA_LOWER.get(lowered[len(C2PA_LEGACY_PREFIX):]))
    if "/" in raw or ":" in raw:
        # Another namespace: never mapped by its last segment (".../trainedAlgorithmicMedia" of an unknown scheme).
        return DeclaredSourceType(raw, None)
    return DeclaredSourceType(raw, _IPTC_LOWER.get(lowered) or _C2PA_LOWER.get(lowered))


def codes_in_category(values: list[str] | tuple[str, ...] | set[str], category: str) -> list[str]:
    """The sorted, de-duplicated codes of ``values`` whose category is ``category``."""
    return sorted({declared.code for declared in map(classify_source_type, values) if declared.category == category})


def split_declared(text: str) -> list[str]:
    """XMP may hold several values (bag / ``;``-joined by image_metadata) — the non-empty parts."""
    return [part.strip() for part in (text or "").replace("\n", ";").split(";") if part.strip()]


__all__ = [
    "CATEGORY_CAPTURE",
    "CATEGORY_GENERATIVE",
    "CATEGORY_NEUTRAL",
    "CATEGORY_SCREEN",
    "CATEGORY_UNKNOWN",
    "C2PA_SOURCE_TYPES",
    "DeclaredSourceType",
    "IPTC_SOURCE_TYPES",
    "IPTC_VOCABULARY_RELEASE",
    "SourceType",
    "classify_source_type",
    "codes_in_category",
    "split_declared",
]
