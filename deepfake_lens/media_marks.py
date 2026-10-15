"""Machine-readable mark checks — invisible watermark decoders (techniques-2026 items 3–5).

Every decoder runs through :func:`checks.run_check`, so each appears in
``coverage`` (ran / skipped "의존성 부재: …" / failed). What a decoder may
say is fixed here:

- a *presence* (a watermark of algorithm X, its payload / message / bit
  accuracy) is a **fact** — a deterministic, neutral item. A watermark
  marks content; it does not say the content was generated (TrustMark and
  VideoSeal are used on photographs too).
- a link to a **generator** (deterministic, synthetic, strong) exists only
  on an exact match with an entry of the versioned registry
  ``models/watermark-registry.json`` (SDXL's fixed 48-bit message), or —
  TrustMark — when the decoded payload equals the ``c2pa.soft-binding``
  value of a C2PA manifest whose signature validated (then the manifest's
  own declaration is what decides).
- **absence is never evidence**: no decoder result means "original". Marks
  are removed by resizing, cropping, re-encoding or simply never applied.

The registry's version and sha256 are part of every finding's detail.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .check_skipped import CheckSkipped
from .result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

REGISTRY_PATH = Path(__file__).resolve().parent / "models" / "watermark-registry.json"
REGISTRY_SCHEMA = "watermark-registry-v1"
WATERMARK_LAYER = "watermark"

SDXL_CHECK = "watermark_sdxl"
TRUSTMARK_CHECK = "watermark_trustmark"
VIDEOSEAL_CHECK = "watermark_videoseal"
AUDIOSEAL_CHECK = "watermark_audioseal"

ABSENCE_LIMITATION = (
    "비가시 워터마크가 검출되지 않은 것은 원본성 근거가 아닙니다 — 워터마크는 크기 조정·자르기·재압축으로 사라지거나 "
    "처음부터 적용되지 않았을 수 있습니다."
)
NO_INFORMATION = "무정보(워터마크 부재를 뜻하지 않음)"


class RegistryError(RuntimeError):
    """The watermark registry cannot be read (Korean) — checks that need it fail."""


@dataclass(frozen=True)
class Registry:
    version: str
    sha256: str
    entries: tuple[dict[str, Any], ...]

    def for_algorithm(self, algorithm: str) -> list[dict[str, Any]]:
        return [entry for entry in self.entries if entry.get("algorithm") == algorithm]

    @property
    def label(self) -> str:
        return f"워터마크 등록표 {self.version}(sha256 {self.sha256[:12]}…)"


_REGISTRY: dict[tuple[str, int, int], Registry] = {}


def load_registry(path: Path | None = None) -> Registry:
    target = path or REGISTRY_PATH
    try:
        info = target.stat()
        key = (str(target), info.st_mtime_ns, info.st_size)
        cached = _REGISTRY.get(key)
        if cached is not None:
            return cached
        data = target.read_bytes()
        payload = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RegistryError(f"워터마크 등록표를 읽을 수 없습니다: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA or not isinstance(payload.get("entries"), list):
        raise RegistryError(f"워터마크 등록표 형식이 {REGISTRY_SCHEMA}가 아닙니다")
    registry = Registry(str(payload.get("version", "")), hashlib.sha256(data).hexdigest(),
                        tuple(entry for entry in payload["entries"] if isinstance(entry, dict)))
    _REGISTRY[key] = registry
    return registry


def registry_cache_tokens() -> list[str]:
    """Scan-cache token of the registry (a new table version re-analyzes); never raises."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return [f"watermark-registry:error:{exc}"]
    return [f"watermark-registry:{registry.version}:{registry.sha256}"]


@dataclass
class MarkFinding:
    """One mark check as the marking-context section lists it (item 6)."""

    check: str
    algorithm: str
    outcome: str  # found / not_found / skipped / failed
    detail: str = ""


@dataclass
class MarkLayers:
    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: list[CoverageEntry] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    findings: list[MarkFinding] = field(default_factory=list)


# --- SDXL imwatermark (item 3) --------------------------------------------------

SDXL_ALGORITHM_LABEL = "imwatermark dwtDct(SDXL 고정 48비트 메시지)"
SDXL_FOUND_TITLE = "SDXL 공식 파이프라인 워터마크 존재"


@dataclass(frozen=True)
class SdxlOutcome:
    matched: str  # "" when no registered message matched, else "BGR" / "RGB"
    entry: dict[str, Any] | None
    registry: Registry
    width: int
    height: int


def sdxl_check(path: Path) -> SdxlOutcome:
    """Decode the dwtDct message and compare it with the registry's SDXL entries (exact match only)."""
    import numpy  # noqa: F401 — dependency probe: absent numpy/cv2/PIL is "skipped"
    import cv2  # noqa: F401
    import PIL  # noqa: F401

    from .wm_imwatermark import ALGORITHM, MAX_PIXELS, MIN_SIDE, decode_array, load_rgb

    registry = load_registry()
    entries = registry.for_algorithm(ALGORITHM)
    rgb = load_rgb(path)
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    if min(width, height) < MIN_SIDE:
        raise CheckSkipped(f"측정 범위 밖: 해상도 {width}x{height} — SDXL 워터마커는 {MIN_SIDE}px 미만 이미지에 표식을 넣지 않음")
    if width * height > MAX_PIXELS:
        raise CheckSkipped(f"측정 범위 밖: 해상도 {width}x{height}(화소 {MAX_PIXELS:,}개 초과)")
    lengths = sorted({len(str(entry.get("message_bits", ""))) for entry in entries})
    for length in lengths:
        reading = decode_array(rgb, n_bits=length)
        for entry in entries:
            bits = str(entry.get("message_bits", ""))
            if len(bits) != length:
                continue
            if reading.bits_bgr == bits:
                return SdxlOutcome("BGR", entry, registry, width, height)
            if reading.bits_rgb == bits:
                return SdxlOutcome("RGB", entry, registry, width, height)
    return SdxlOutcome("", None, registry, width, height)


def sdxl_evidence(outcome: SdxlOutcome) -> list[EvidenceItem]:
    if not outcome.matched or outcome.entry is None:
        return []
    order = "문서화된 BGR 순서" if outcome.matched == "BGR" else "R·B 채널을 바꾼 순서(RGB 배열을 그대로 넘긴 파이프라인)"
    return [EvidenceItem(
        SDXL_FOUND_TITLE,
        f"imwatermark dwtDct로 읽은 48비트가 등록된 메시지({outcome.entry.get('id')}: {outcome.entry.get('generator')})와 "
        f"48/48 정확히 일치합니다({order}, {outcome.width}x{outcome.height}). 메시지는 공개되어 있어 같은 라이브러리로 흉내 낼 수 있으므로 "
        f"생성 도구 메타데이터와 같은 수준의 근거입니다. 기준: {outcome.registry.label}.",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, WATERMARK_LAYER,
    )]


def _sdxl_layer(path: Path, layers: MarkLayers) -> None:
    from .checks import run_check

    outcome, entry = run_check(SDXL_CHECK, lambda: sdxl_check(path))
    if outcome is not None:
        if outcome.matched:
            entry = CoverageEntry(SDXL_CHECK, CoverageStatus.RAN, f"등록 메시지 정확 일치({outcome.registry.label})")
            layers.findings.append(MarkFinding(SDXL_CHECK, SDXL_ALGORITHM_LABEL, "found", "SDXL 고정 메시지 48/48 일치"))
        else:
            entry = CoverageEntry(SDXL_CHECK, CoverageStatus.RAN, f"48비트 정확 일치 없음 — {NO_INFORMATION}")
            layers.findings.append(MarkFinding(SDXL_CHECK, SDXL_ALGORITHM_LABEL, "not_found", NO_INFORMATION))
        layers.evidence.extend(sdxl_evidence(outcome))
    else:
        layers.findings.append(MarkFinding(SDXL_CHECK, SDXL_ALGORITHM_LABEL, entry.status.value, entry.reason))
    layers.coverage.append(entry)


def image_mark_layers(path: Path, c2pa_validation: dict[str, object] | None = None) -> MarkLayers:
    """The watermark checks of an image file (after its C2PA check)."""
    layers = MarkLayers()
    _sdxl_layer(path, layers)
    if layers.findings:
        layers.limitations.append(ABSENCE_LIMITATION)
    return layers


__all__ = [
    "ABSENCE_LIMITATION",
    "AUDIOSEAL_CHECK",
    "MarkFinding",
    "MarkLayers",
    "REGISTRY_PATH",
    "Registry",
    "RegistryError",
    "SDXL_CHECK",
    "SDXL_FOUND_TITLE",
    "TRUSTMARK_CHECK",
    "VIDEOSEAL_CHECK",
    "image_mark_layers",
    "load_registry",
    "registry_cache_tokens",
    "sdxl_check",
    "sdxl_evidence",
]
