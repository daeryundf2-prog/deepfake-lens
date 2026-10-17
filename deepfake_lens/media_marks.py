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
PERTH_CHECK = "watermark_perth"  # techniques-2026b TB-2

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

SDXL_ALGORITHM_LABEL = "imwatermark dwtDct(등록된 고정 메시지: SDXL·FLUX.1 참조 구현·SD 1/2 공식 스크립트)"
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
        raise CheckSkipped(f"측정 범위 밖: 해상도 {width}x{height} — SDXL 워터마커는 {MIN_SIDE}px 미만 이미지에 표식을 넣지 않음(다른 등록 파이프라인의 기본 출력도 그 이상)")
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
    n = len(str(outcome.entry.get("message_bits", "")))
    # TB-8: every registered fixed message names its own pipeline (the SDXL entry keeps its phase title).
    return [EvidenceItem(
        str(outcome.entry.get("title") or SDXL_FOUND_TITLE),
        f"imwatermark dwtDct로 읽은 {n}비트가 등록된 메시지({outcome.entry.get('id')}: {outcome.entry.get('generator')})와 "
        f"{n}/{n} 정확히 일치합니다({order}, {outcome.width}x{outcome.height}). 메시지는 공개되어 있어 같은 라이브러리로 흉내 낼 수 있으므로 "
        f"생성 도구 메타데이터와 같은 수준의 근거입니다. 기준: {outcome.registry.label}.",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, WATERMARK_LAYER,
    )]


def _sdxl_layer(path: Path, layers: MarkLayers) -> None:
    from .checks import run_check

    outcome, entry = run_check(SDXL_CHECK, lambda: sdxl_check(path))
    if outcome is not None:
        if outcome.matched:
            entry = CoverageEntry(SDXL_CHECK, CoverageStatus.RAN, f"등록 메시지 정확 일치({outcome.registry.label})")
            bits_n = len(str((outcome.entry or {}).get("message_bits", "")))
            layers.findings.append(MarkFinding(SDXL_CHECK, SDXL_ALGORITHM_LABEL, "found", f"{(outcome.entry or {}).get('generator')} 고정 메시지 {bits_n}/{bits_n} 일치"))
        else:
            known = len(outcome.registry.for_algorithm("imwatermark-dwtDct"))
            entry = CoverageEntry(SDXL_CHECK, CoverageStatus.RAN, f"등록 고정 메시지 {known}개와 정확 일치 없음 — {NO_INFORMATION}")
            layers.findings.append(MarkFinding(SDXL_CHECK, SDXL_ALGORITHM_LABEL, "not_found", NO_INFORMATION))
        layers.evidence.extend(sdxl_evidence(outcome))
    else:
        layers.findings.append(MarkFinding(SDXL_CHECK, SDXL_ALGORITHM_LABEL, entry.status.value, entry.reason))
    layers.coverage.append(entry)


# --- Adobe TrustMark (item 4) ----------------------------------------------------

TRUSTMARK_ALGORITHM_LABEL = "TrustMark(어도비, 변형 Q/P/C)"
TRUSTMARK_FOUND_TITLE = "TrustMark 워터마크 판독"
TRUSTMARK_CROSS_MATCH_TITLE = "TrustMark–C2PA 소프트 바인딩 교차검증 일치"
TRUSTMARK_CROSS_LINKED_TITLE = "C2PA 소프트 바인딩으로 확인된 생성형 AI 출처 선언"
# Measured on 240 Wikimedia Commons camera photos (scripts/measure_watermark_fpr.py,
# docs/WATERMARK-MEASUREMENTS.md). Quoted in every TrustMark finding.
TRUSTMARK_FPR_NOTE = (
    "워터마크가 없는 실사 사진 240장에서 BCH 검사를 통과한 오탐: Q 5장(2.1%), P 3장(1.3%), C 6장(2.5%) — 대부분 BCH_3 스키마"
    "(측정 2026-10-15, docs/WATERMARK-MEASUREMENTS.md); 이 판독만으로는 표식 존재도 확정되지 않으며 매니페스트 교차검증 일치가 있을 때만 결합 근거입니다"
)
# Failure codes that only say the asset bytes changed (hard binding) — the claim
# signature and the assertions (the soft-binding value among them) are intact.
HARD_BINDING_MISMATCH_CODES = (
    "assertion.dataHash.mismatch", "assertion.bmffHash.mismatch", "assertion.boxesHash.mismatch", "assertion.collectionHash.mismatch",
)


def _signature_ok(validation: dict[str, object] | None) -> bool:
    """The manifest's claim and assertions validated under a trusted signer (asset hash may differ)."""
    if not validation or not validation.get("present") or not validation.get("trusted"):
        return False
    return all(code in HARD_BINDING_MISMATCH_CODES for code in _failure_codes(validation))


def _failure_codes(validation: dict[str, object]) -> list[str]:
    raw = validation.get("failure_codes")
    return [code for code in raw if isinstance(code, str)] if isinstance(raw, (list, tuple)) else []


def _hard_binding_changed(validation: dict[str, object]) -> bool:
    return any(code in HARD_BINDING_MISMATCH_CODES for code in _failure_codes(validation))


def _trustmark_bindings(validation: dict[str, object] | None) -> list[tuple[str, list[str]]]:
    raw = (validation or {}).get("soft_bindings")
    out: list[tuple[str, list[str]]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and str(item.get("alg", "")).startswith("com.adobe.trustmark."):
            out.append((str(item["alg"]), [str(value) for value in item.get("values") or []]))
    return out


def trustmark_evidence(readings: list[Any], validation: dict[str, object] | None) -> list[EvidenceItem]:
    """Facts for decoded TrustMark packets and the C2PA soft-binding cross-check (item 4).

    - every BCH-valid packet: a neutral, weak fact (presence + payload);
    - payload == the ``c2pa.soft-binding`` value of the same variant in a manifest
      whose signature validated under a trusted signer: a neutral, moderate
      "교차검증 일치" — and, when the asset bytes changed after signing (hard
      binding mismatch, so the manifest's own C2PA item cannot decide) while the
      manifest declares generative AI, a deterministic synthetic strong item:
      the watermark binds this content to that signed declaration;
    - a TrustMark soft binding whose value no packet matches: a neutral fact.
    """
    from .evidence_rules import CATEGORY_GENERATIVE, _c2pa_declarations, _disclosure_text

    items: list[EvidenceItem] = []
    for reading in readings:
        items.append(EvidenceItem(
            f"{TRUSTMARK_FOUND_TITLE}(변형 {reading.variant})",
            f"TrustMark {reading.variant} 디코더가 BCH 검사를 통과한 패킷을 읽었습니다: 스키마 {reading.schema_name}, "
            f"페이로드 {reading.payload}(소프트 바인딩 표기 「{reading.soft_binding_value}」). 워터마크가 들어 있다는 사실이며 "
            "AI 생성 사실이 아닙니다(TrustMark는 사진 출처 표시에도 쓰임). 페이로드가 가리키는 매니페스트를 찾는 온라인 조회는 하지 않습니다. "
            f"{TRUSTMARK_FPR_NOTE}.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, WATERMARK_LAYER,
        ))
    bindings = _trustmark_bindings(validation)
    if not bindings or validation is None:
        return items
    matched = False
    for reading in readings:
        alg = f"com.adobe.trustmark.{reading.variant}"
        if not any(binding_alg == alg and reading.soft_binding_value in values for binding_alg, values in bindings):
            continue
        matched = True
        signed = _signature_ok(validation)
        items.append(EvidenceItem(
            TRUSTMARK_CROSS_MATCH_TITLE if signed else f"{TRUSTMARK_CROSS_MATCH_TITLE}(서명 검증 미완료)",
            f"디코드한 TrustMark {reading.variant} 값 「{reading.soft_binding_value}」이(가) C2PA 매니페스트의 소프트 바인딩"
            f"(alg {alg}) 값과 같습니다. "
            + ("신뢰 서명자의 서명이 유효한 매니페스트이므로 워터마크가 이 콘텐츠를 그 매니페스트에 결합합니다."
               if signed else "매니페스트 서명이 신뢰·검증되지 않아 결합 근거로 쓰지 않습니다."),
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE if signed else EvidenceStrength.WEAK,
            WATERMARK_LAYER,
        ))
        generative = [d.code for d in _c2pa_declarations(validation, "action") if d.category == CATEGORY_GENERATIVE]
        disclosure = _disclosure_text(validation)
        if signed and _hard_binding_changed(validation) and (generative or disclosure):
            declared = ", ".join(generative) + ("; " if generative and disclosure else "") + (f"AI 공개 assertion({disclosure})" if disclosure else "")
            items.append(EvidenceItem(
                TRUSTMARK_CROSS_LINKED_TITLE,
                f"파일 바이트는 서명 이후 바뀌었으나(하드 바인딩 불일치), 신뢰 서명자의 유효한 매니페스트가 선언한 소프트 바인딩 값과 "
                f"이 이미지에서 디코드한 TrustMark {reading.variant} 값이 정확히 같습니다. 그 매니페스트는 {declared}를 선언합니다 — "
                "워터마크로 결합된 서명된 생성형 AI 선언입니다(C2PA 2.x 소프트 바인딩 검증).",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, WATERMARK_LAYER,
            ))
    if not matched:
        listed = "; ".join(f"{alg}: " + ", ".join(f"「{v}」" for v in values[:3]) for alg, values in bindings)
        decoded = ", ".join(f"{r.variant} 「{r.soft_binding_value}」" for r in readings) or "판독된 TrustMark 없음"
        items.append(EvidenceItem(
            "TrustMark–C2PA 소프트 바인딩 불일치",
            f"매니페스트가 TrustMark 소프트 바인딩({listed})을 선언하나 이 이미지에서 같은 값을 읽지 못했습니다({decoded}). "
            "재압축·크기 조정으로 워터마크가 손상되었거나, 매니페스트가 다른 콘텐츠의 것일 수 있습니다 — 사실로만 기록합니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, WATERMARK_LAYER,
        ))
    return items


def _trustmark_layer(path: Path, validation: dict[str, object] | None, layers: MarkLayers) -> None:
    from .checks import run_check
    from .wm_trustmark import decode_image

    outcome, entry = run_check(TRUSTMARK_CHECK, lambda: decode_image(path))
    if outcome is None:
        layers.findings.append(MarkFinding(TRUSTMARK_CHECK, TRUSTMARK_ALGORITHM_LABEL, entry.status.value, entry.reason))
        # A manifest that declares a TrustMark binding is still listed (as a fact) when the decoder could not run.
        layers.coverage.append(entry)
        return
    readings, ran, missing = outcome
    note = f"변형 {'/'.join(ran)} 실행" + (f"(가중치 없음: {', '.join(missing)})" if missing else "")
    if readings:
        found = ", ".join(f"{r.variant} {r.schema_name}" for r in readings)
        entry = CoverageEntry(TRUSTMARK_CHECK, CoverageStatus.RAN, f"{note} — 판독됨: {found}")
        layers.findings.append(MarkFinding(TRUSTMARK_CHECK, TRUSTMARK_ALGORITHM_LABEL, "found", found))
    else:
        entry = CoverageEntry(TRUSTMARK_CHECK, CoverageStatus.RAN, f"{note} — BCH 통과 패킷 없음 — {NO_INFORMATION}")
        layers.findings.append(MarkFinding(TRUSTMARK_CHECK, TRUSTMARK_ALGORITHM_LABEL, "not_found", NO_INFORMATION))
    layers.coverage.append(entry)
    layers.evidence.extend(trustmark_evidence(readings, validation))


# --- Meta Video Seal / AudioSeal (item 5) ---------------------------------------

VIDEOSEAL_ALGORITHM_LABEL = "VideoSeal 1.0(메타, 256비트)"
AUDIOSEAL_ALGORITHM_LABEL = "AudioSeal(메타, 16비트)"
AUDIOSEAL_FOUND_TITLE = "AudioSeal 워터마크 검출"
# Measured on real speech (scripts/measure_watermark_fpr.py --audio; docs/WATERMARK-MEASUREMENTS.md).
AUDIOSEAL_FPR_NOTE = "워터마크가 없는 실제 음성 240개(LibriSpeech dev-clean)에서 검출 0건(95% 상한 1.5%, 측정 2026-10-15, docs/WATERMARK-MEASUREMENTS.md)"
REGISTERED_MATCH_TITLE = "등록된 생성기 워터마크 메시지 일치"


def _registered_match(algorithm: str, bits: str, registry: Registry, *, min_agreement: int | None = None) -> tuple[dict[str, Any], int] | None:
    """The registry entry ``bits`` matches: exact, or (neural decoders) at least ``min_agreement`` equal bits."""
    for entry in registry.for_algorithm(algorithm):
        message = str(entry.get("message_bits", ""))
        if len(message) != len(bits):
            continue
        agreement = sum(a == b for a, b in zip(message, bits))
        required = int(entry.get("min_agreement_bits") or min_agreement or len(message))
        if agreement >= required:
            return entry, agreement
    return None


def _registered_item(label: str, entry: dict[str, Any], agreement: int, total: int, registry: Registry) -> EvidenceItem:
    return EvidenceItem(
        REGISTERED_MATCH_TITLE,
        f"{label}로 읽은 메시지가 등록된 생성기 메시지({entry.get('id')}: {entry.get('generator')})와 {agreement}/{total}비트 일치합니다. "
        f"기준: {registry.label}.",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, WATERMARK_LAYER,
    )


def _videoseal_layer(path: Path, layers: MarkLayers, *, video: bool) -> None:
    from .checks import run_check
    from . import wm_meta

    def check() -> Any:
        import torch  # noqa: F401 — dependency probe: without torch the check is "의존성 부재: torch"

        registry = load_registry()
        reading = wm_meta.videoseal_read_video(path) if video else wm_meta.videoseal_read_image(path)
        return reading, registry

    outcome, entry = run_check(VIDEOSEAL_CHECK, check)
    if outcome is None:
        layers.findings.append(MarkFinding(VIDEOSEAL_CHECK, VIDEOSEAL_ALGORITHM_LABEL, entry.status.value, entry.reason))
        layers.coverage.append(entry)
        return
    reading, registry = outcome
    message = wm_meta.bits_hex(reading.bits)
    match = _registered_match("videoseal-1.0", reading.bits, registry, min_agreement=wm_meta.VIDEOSEAL_MATCH_BITS)
    frames = f"프레임 {reading.frames}개 평균" if video else "정지 영상"
    if match is not None:
        entry = CoverageEntry(VIDEOSEAL_CHECK, CoverageStatus.RAN, f"{frames} — 등록 메시지 일치({registry.label})")
        layers.evidence.append(_registered_item(VIDEOSEAL_ALGORITHM_LABEL, match[0], match[1], wm_meta.VIDEOSEAL_BITS, registry))
        layers.findings.append(MarkFinding(VIDEOSEAL_CHECK, VIDEOSEAL_ALGORITHM_LABEL, "found", f"등록 메시지 {match[1]}/256비트 일치"))
    else:
        known = len(registry.for_algorithm("videoseal-1.0"))
        entry = CoverageEntry(
            VIDEOSEAL_CHECK, CoverageStatus.RAN,
            f"{frames} — 등록 메시지(현재 {known}건)와 일치 없음 — VideoSeal은 존재 확률을 내지 않아 디코드 메시지만으로는 {NO_INFORMATION}",
        )
        layers.findings.append(MarkFinding(
            VIDEOSEAL_CHECK, VIDEOSEAL_ALGORITHM_LABEL, "not_found",
            f"디코드 메시지 {message}(평균 |logit| {reading.mean_abs_logit:.2f}) — 등록 메시지 없음, 존재 판단 불가",
        ))
    layers.coverage.append(entry)


def _audioseal_layer(path: Path, layers: MarkLayers) -> None:
    from .checks import run_check
    from . import wm_meta

    def check() -> Any:
        import torch  # noqa: F401 — dependency probes
        import audioseal  # noqa: F401

        return wm_meta.audioseal_read(path), load_registry()

    outcome, entry = run_check(AUDIOSEAL_CHECK, check)
    if outcome is None:
        layers.findings.append(MarkFinding(AUDIOSEAL_CHECK, AUDIOSEAL_ALGORITHM_LABEL, entry.status.value, entry.reason))
        layers.coverage.append(entry)
        return
    reading, registry = outcome
    present = reading.probability >= wm_meta.AUDIOSEAL_PRESENCE_THRESHOLD
    span = f"처음 {reading.seconds:.1f}초 분석"
    if present:
        layers.evidence.append(EvidenceItem(
            AUDIOSEAL_FOUND_TITLE,
            f"AudioSeal 검출기가 워터마크 확률 {reading.probability:.2f}(기준 {wm_meta.AUDIOSEAL_PRESENCE_THRESHOLD:.2f} 이상, {span})로 "
            f"워터마크를 검출했습니다. 16비트 메시지 {reading.bits}. 워터마크가 들어 있다는 사실이며 삽입 주체(Meta 제품인지 제3자인지)나 "
            f"AI 생성 여부를 뜻하지 않습니다. {AUDIOSEAL_FPR_NOTE}.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, WATERMARK_LAYER,
        ))
        match = _registered_match("audioseal-16", reading.bits, registry)
        if match is not None:
            layers.evidence.append(_registered_item(AUDIOSEAL_ALGORITHM_LABEL, match[0], match[1], wm_meta.AUDIOSEAL_BITS, registry))
        entry = CoverageEntry(AUDIOSEAL_CHECK, CoverageStatus.RAN, f"{span} — 검출됨(확률 {reading.probability:.2f})")
        layers.findings.append(MarkFinding(AUDIOSEAL_CHECK, AUDIOSEAL_ALGORITHM_LABEL, "found", f"확률 {reading.probability:.2f}, 메시지 {reading.bits}"))
    else:
        entry = CoverageEntry(AUDIOSEAL_CHECK, CoverageStatus.RAN, f"{span} — 검출 안 됨(확률 {reading.probability:.2f}) — {NO_INFORMATION}")
        layers.findings.append(MarkFinding(AUDIOSEAL_CHECK, AUDIOSEAL_ALGORITHM_LABEL, "not_found", NO_INFORMATION))
    layers.coverage.append(entry)


# --- Resemble AI PerTh implicit watermark (techniques-2026b TB-2) -------------------

PERTH_ALGORITHM_LABEL = "PerTh 암묵 워터마크(Resemble 제작, 1비트 존재 표식)"
PERTH_FOUND_TITLE = "PerTh 워터마크 검출"
PERTH_REGISTERED_TITLE = "등록된 생성기 워터마크 일치(PerTh 1비트 존재 표식 — 결론 근거 아님)"
# Measured with wm_perth on real speech (scripts/measure_watermark_fpr.py --audio --only perth; docs/WATERMARK-MEASUREMENTS.md).
PERTH_FPR_NOTE = (
    "워터마크가 없는 실제 음성 240개(LibriSpeech의 dev-clean 세트, 화자 40명×6발화)에서 신뢰도 0.5 이상 3건(1.25%, 95% 상한 3.6%, 최대 0.78), "
    "등록표 일치 기준 0.9 이상 0건(95% 상한 1.5%)(측정 2026-10-17, docs/WATERMARK-MEASUREMENTS.md)"
)


def _perth_layer(path: Path, layers: MarkLayers) -> None:
    from .checks import run_check
    from . import wm_perth

    def check() -> Any:
        import torch  # noqa: F401 — dependency probe: without torch the check is "의존성 부재: torch"

        return wm_perth.perth_read(path), load_registry()

    outcome, entry = run_check(PERTH_CHECK, check)
    if outcome is None:
        layers.findings.append(MarkFinding(PERTH_CHECK, PERTH_ALGORITHM_LABEL, entry.status.value, entry.reason))
        layers.coverage.append(entry)
        return
    reading, registry = outcome
    span = f"처음 {reading.seconds:.1f}초 분석(원 표본화 {reading.source_rate}Hz를 32kHz로 변환)"
    if reading.present:
        layers.evidence.append(EvidenceItem(
            PERTH_FOUND_TITLE,
            f"PerTh 암묵 디코더의 신뢰도 {reading.confidence:.3f}(기준 {wm_perth.PRESENCE_THRESHOLD:.1f} 이상, {span}) — Resemble의 PerTh 워터마크가 "
            "들어 있다는 사실입니다. 이 표식은 메시지 없이 존재만 나타내며, 공개 라이브러리(MIT)로 누구나 어떤 오디오에도 넣을 수 있습니다. "
            "음성에서만 측정되었으며 순음 위주 신호는 검사하지 않습니다. "
            f"{PERTH_FPR_NOTE}.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, WATERMARK_LAYER,
        ))
        for registered in registry.for_algorithm("perth-implicit"):
            if str(registered.get("expected_payload", "")) == "1" and reading.confidence >= float(registered.get("min_confidence", 0.9)):
                layers.evidence.append(EvidenceItem(
                    PERTH_REGISTERED_TITLE,
                    f"검출된 PerTh 표식(신뢰도 {reading.confidence:.3f} ≥ 등록 기준 {float(registered.get('min_confidence', 0.9)):.1f})이 등록표 항목"
                    f"({registered.get('id')}: {registered.get('generator')})의 기대값(존재 = 1)과 일치합니다. "
                    "1비트 표식이라 특정 생성기를 확정하지 않으며(Resemble의 서비스와 라이브러리 사용자도 같은 표식을 넣음), 원본성 결론만 막습니다. "
                    f"기준: {registry.label}.",
                    EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, WATERMARK_LAYER,
                ))
                break
        entry = CoverageEntry(PERTH_CHECK, CoverageStatus.RAN, f"{span} — 검출됨(신뢰도 {reading.confidence:.3f})")
        layers.findings.append(MarkFinding(PERTH_CHECK, PERTH_ALGORITHM_LABEL, "found", f"신뢰도 {reading.confidence:.3f}"))
    else:
        entry = CoverageEntry(PERTH_CHECK, CoverageStatus.RAN, f"{span} — 검출 안 됨(신뢰도 {reading.confidence:.3f}) — {NO_INFORMATION}")
        layers.findings.append(MarkFinding(PERTH_CHECK, PERTH_ALGORITHM_LABEL, "not_found", NO_INFORMATION))
    layers.coverage.append(entry)


def audio_mark_layers(path: Path) -> MarkLayers:
    layers = MarkLayers()
    _audioseal_layer(path, layers)
    _perth_layer(path, layers)
    layers.limitations.append(ABSENCE_LIMITATION)
    return layers


def video_mark_layers(path: Path) -> MarkLayers:
    layers = MarkLayers()
    _videoseal_layer(path, layers, video=True)
    _audioseal_layer(path, layers)  # the video's audio track (skipped "오디오 트랙 없음" without one)
    _perth_layer(path, layers)
    layers.limitations.append(ABSENCE_LIMITATION)
    return layers


def image_mark_layers(path: Path, c2pa_validation: dict[str, object] | None = None) -> MarkLayers:
    """The watermark checks of an image file (after its C2PA check)."""
    layers = MarkLayers()
    _sdxl_layer(path, layers)
    _trustmark_layer(path, c2pa_validation, layers)
    _videoseal_layer(path, layers, video=False)
    if layers.findings:
        layers.limitations.append(ABSENCE_LIMITATION)
    return layers


__all__ = [
    "ABSENCE_LIMITATION",
    "AUDIOSEAL_CHECK",
    "PERTH_CHECK",
    "PERTH_FOUND_TITLE",
    "PERTH_REGISTERED_TITLE",
    "MarkFinding",
    "MarkLayers",
    "REGISTRY_PATH",
    "Registry",
    "RegistryError",
    "SDXL_CHECK",
    "SDXL_FOUND_TITLE",
    "TRUSTMARK_CHECK",
    "TRUSTMARK_CROSS_LINKED_TITLE",
    "TRUSTMARK_CROSS_MATCH_TITLE",
    "TRUSTMARK_FOUND_TITLE",
    "trustmark_evidence",
    "VIDEOSEAL_CHECK",
    "audio_mark_layers",
    "image_mark_layers",
    "video_mark_layers",
    "load_registry",
    "registry_cache_tokens",
    "sdxl_check",
    "sdxl_evidence",
]
