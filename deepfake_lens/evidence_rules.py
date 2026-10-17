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
| XMP DigitalSourceType / generator name in XMP tool | deterministic | synthetic | strong   |
| C2PA valid + trusted + digitalCapture, hash match | deterministic | authentic | strong   |
| camera EXIF consistent, no recompression (D7)     | deterministic | authentic | moderate |
| square generator resolution / missing metadata     | deterministic | neutral   | weak     |
| external model output                              | statistical   | synthetic | probability only when calibrated |
| deep layers (face seam, inpaint, tracking …)       | reference_signals only until calibrated (D13) |
| AI identity phrases, template connectors, style    | lexical       | synthetic | weak     |
| pixel ensemble / frequency / legacy heuristics     | reference_signals only |
| image class (photo / screenshot / pattern …)      | deterministic | neutral   | weak     |
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta

from .digital_source_type import (
    CATEGORY_CAPTURE,
    CATEGORY_GENERATIVE,
    CATEGORY_NEUTRAL,
    CATEGORY_SCREEN,
    CATEGORY_UNKNOWN,
    C2PA_SOURCE_TYPES,
    IPTC_SOURCE_TYPES,
    IPTC_VOCABULARY_RELEASE,
    DeclaredSourceType,
    classify_source_type,
    split_declared,
)
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

# IPTC digital source type vocabulary (cv.iptc.org/newscodes/digitalsourcetype)
# — derived from the full table in digital_source_type.py (techniques-2026).
C2PA_SYNTHETIC_SOURCE_TYPES = frozenset(entry.code for entry in (*IPTC_SOURCE_TYPES, *C2PA_SOURCE_TYPES) if entry.category == CATEGORY_GENERATIVE)
C2PA_CAPTURE_SOURCE_TYPES = frozenset(entry.code for entry in IPTC_SOURCE_TYPES if entry.category == CATEGORY_CAPTURE)

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


# --- XMP generator fields and camera EXIF (D7) ------------------------------

# Generator names that, written into an XMP tool/creator/credit field, are a
# deterministic statement by the file that a generator produced it (phase-0
# verification D7). Matched case-insensitively on word boundaries; a space
# also matches "-"/"_" ("AI-generated").
# TB-7: the names are the versioned table models/generator-strings.json (xmp_generator_names, unchanged).
from .generator_strings import xmp_generator_names  # noqa: E402

GENERATOR_NAMES = xmp_generator_names()
_GENERATOR_PATTERN = re.compile(
    r"(?<![0-9a-z])(?:"
    + "|".join(re.escape(name.lower()).replace(r"\ ", r"[\s_-]+").replace(r"\-", r"[\s_-]?") for name in GENERATOR_NAMES)
    + r")(?![0-9a-z])",
    re.IGNORECASE,
)
# XMP fields read by image_metadata.parse_xmp_fields that name the producing
# tool or the credited author.
XMP_GENERATOR_FIELDS = (
    ("xmp.CreatorTool", "xmp:CreatorTool"),
    ("xmp.creator", "dc:creator"),
    ("xmp.Credit", "photoshop:Credit"),
)
XMP_DIGITAL_SOURCE_TYPE_KEY = "xmp.DigitalSourceType"

# Camera EXIF consistency (WP-A table: deterministic/authentic/moderate).
# EXIF 1.0 was published in 1995 — an earlier DateTimeOriginal is not a
# camera clock value.
EXIF_EARLIEST_YEAR = 1995
EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"
EXIF_GPS_DATE_FORMAT = "%Y:%m:%d"
# GPSDateStamp is UTC and DateTimeOriginal local time (offsets up to ±14 h),
# so their calendar dates may differ by one day.
GPS_DATE_TOLERANCE_DAYS = 1
# Messenger/web recompression proxy until phase 1's fingerprint table
# (G14): camera encoders write quality ~92-98; KakaoTalk/Telegram/Instagram
# re-encode around 70-85 (libjpeg QF estimated by jpegq).
CAMERA_MIN_JPEG_QUALITY = 90
# Software values that mean the file passed through an editor (not the
# camera firmware). Lower-case substrings.
EDITOR_SOFTWARE_MARKERS = (
    "photoshop", "lightroom", "gimp", "snapseed", "picsart", "meitu", "canva",
    "affinity", "pixelmator", "luminar", "facetune", "capture one", "paint.net",
    "fotor", "remini", "lensa", "photoroom", "picmonkey", "befunky",
)
# Camera firmware version strings ("Ver.1.10", "17.4.1", "v3.01",
# "Firmware Version 1.0.0", "G998NKSU5EWA3").
FIRMWARE_SOFTWARE_PATTERN = re.compile(
    r"^(?:(?:firmware\s+)?(?:ver(?:sion)?\.?\s*|v)?\d+(?:[.\-_ ]\w+)*|[A-Z0-9]{8,24})$",
    re.IGNORECASE,
)
# Make suffixes dropped before matching Software against the vendor.
_MAKE_NOISE = {"corporation", "corp", "co", "ltd", "inc", "imaging", "electronics", "company", "digital", "camera"}


def generator_name_in(value: str) -> str | None:
    """The first generator name in ``value`` (see GENERATOR_NAMES), or None."""
    match = _GENERATOR_PATTERN.search(value or "")
    return match.group(0) if match else None


def xmp_evidence(metadata: Mapping[str, str]) -> list[EvidenceItem]:
    """Deterministic synthetic items from XMP fields (D7).

    ``Iptc4xmpExt:DigitalSourceType`` naming algorithmic/trained media and a
    generator name in ``xmp:CreatorTool``/``dc:creator``/``photoshop:Credit``
    are statements the file makes about its own origin — deterministic,
    synthetic, strong (WP-A table, "XMP 생성 도구 필드"). XMP can be edited,
    which the detail says; it can never support authenticity.
    """
    items: list[EvidenceItem] = []
    # techniques-2026 item 2: values are mapped with the full IPTC vocabulary
    # (digital_source_type.py, retired values included); an unsigned XMP value
    # is a self-declaration (자기 신고) — the ToolMetadata grade of the WP-A table.
    declared = [classify_source_type(value) for value in split_declared(metadata.get(XMP_DIGITAL_SOURCE_TYPE_KEY, ""))]
    synthetic_types = sorted({d.display for d in declared if d.category == CATEGORY_GENERATIVE})
    if synthetic_types:
        items.append(EvidenceItem(
            "XMP 디지털 출처 유형: 생성형 AI",
            f"Iptc4xmpExt:DigitalSourceType={', '.join(synthetic_types)} — 파일이 스스로 생성형/알고리즘 미디어임을 선언합니다(서명 없는 자기 신고). "
            "XMP는 편집으로 지우거나 바꿀 수 있어 부재는 아무것도 증명하지 않습니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "metadata",
        ))
    screen = sorted({d.display for d in declared if d.category == CATEGORY_SCREEN})
    if screen:
        items.append(EvidenceItem(
            "XMP 디지털 출처 유형: 화면 캡처(자기 신고)",
            f"Iptc4xmpExt:DigitalSourceType={', '.join(screen)} — 화면에 표시된 콘텐츠를 캡처·재촬영한 파일이라는 서명 없는 자기 신고입니다. "
            "카메라 실사 촬영 근거가 아니며, 생성 근거도 아닙니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE, "metadata",
        ))
    for key, label in XMP_GENERATOR_FIELDS:
        value = metadata.get(key, "")
        name = generator_name_in(value)
        if name:
            items.append(EvidenceItem(
                "XMP 생성 도구 필드",
                f"{label}={value[:120]} — 생성 도구명 '{name}'이(가) 도구/작성자 필드에 기록되어 있습니다. "
                "XMP는 편집으로 바꿀 수 있습니다.",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "metadata",
            ))
    return items


def _parse_exif_datetime(value: str | None, fmt: str) -> datetime | None:
    """EXIF "YYYY:MM:DD HH:MM:SS" (or GPS "YYYY:MM:DD"); None when unusable.

    Trailing sub-second/offset text is ignored; dates before EXIF 1.0
    (including the "0000:00:00" placeholder) are unusable.
    """
    if not value:
        return None
    width = 19 if fmt == EXIF_DATETIME_FORMAT else 10
    try:
        parsed = datetime.strptime(value.strip()[:width], fmt)
    except ValueError:
        return None
    return parsed if parsed.year >= EXIF_EARLIEST_YEAR else None


def _vendor_token(make: str) -> str:
    words = [re.sub(r"[^0-9a-z]", "", word) for word in make.lower().split()]
    words = [word for word in words if word and word not in _MAKE_NOISE]
    return words[0] if words else ""


def _software_matches_camera(software: str, make: str, model: str) -> bool:
    lowered = software.lower()
    if any(marker in lowered for marker in EDITOR_SOFTWARE_MARKERS) or generator_name_in(software):
        return False
    vendor = _vendor_token(make)
    if vendor and re.search(rf"(?<![0-9a-z]){re.escape(vendor)}(?![0-9a-z])", lowered):
        return True
    if model and model.lower() in lowered:
        return True
    return bool(FIRMWARE_SOFTWARE_PATTERN.match(software.strip()))


# R9: titles of the camera-EXIF note when the image did not decode, and
# the prefix of a decode_problem that comes from a failed decode check.
EXIF_UNEVALUATED_DAMAGED_TITLE = "EXIF 존재(파일 손상으로 일관성 미평가)"
EXIF_UNEVALUATED_UNDECODED_TITLE = "EXIF 존재(디코드 검사 미실행으로 일관성 미평가)"
EXIF_DECODE_FAILED_PREFIX = "이미지 디코드 실패"


def camera_exif_evidence(
    metadata: Mapping[str, str],
    *,
    image_format: str | None,
    jpeg_quality: float | None,
    now: datetime | None = None,
    decode_problem: str | None = None,
) -> list[EvidenceItem]:
    """"카메라 EXIF 일관" (WP-A table: deterministic/authentic/moderate, D7).

    R9: the rule applies only when the image itself decoded (the
    ``image_class`` check ran). ``decode_problem`` — set when that check
    failed (truncated/corrupt file) or did not run — turns any camera EXIF
    into a neutral/weak "EXIF 존재(파일 손상으로 일관성 미평가)" note: EXIF
    read from a damaged file says nothing about the pixels it came with.

    All of: Make and Model present; DateTimeOriginal parseable (not before
    EXIF 1.0, not in the future); Software absent or the camera's own
    (vendor/model name or a firmware version, never an editor or a
    generator); GPS, when present, in range and dated within a day of the
    capture; no generator name in an XMP field; a JPEG whose estimated
    quality is >= CAMERA_MIN_JPEG_QUALITY (no messenger recompression —
    proxy until phase 1). Moderate on purpose: EXIF can be copied into any
    file, so it never concludes authenticity alone (decision rule 5 needs
    a strong item). A camera EXIF that fails a condition is listed as a
    neutral note naming the failed conditions.
    """
    make = metadata.get("exif.Make", "").strip()
    model = metadata.get("exif.Model", "").strip()
    if not make and not model:
        return []
    # R4: values copied from the file are shown verbatim inside 「…」.
    camera = f"{make} {model}".strip()
    if decode_problem is not None:
        damaged = decode_problem.startswith(EXIF_DECODE_FAILED_PREFIX)
        return [EvidenceItem(
            EXIF_UNEVALUATED_DAMAGED_TITLE if damaged else EXIF_UNEVALUATED_UNDECODED_TITLE,
            f"기종 「{camera}」"
            + f". {decode_problem} — 이미지 본체를 확인하지 못해 EXIF 일관성 조건을 평가하지 않았습니다. 원본성 근거로 쓰지 않습니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "metadata",
        )]
    unmet: list[str] = []
    if not make or not model:
        unmet.append("기종(Make/Model) 일부 누락")
    original = _parse_exif_datetime(metadata.get("exif.DateTimeOriginal"), EXIF_DATETIME_FORMAT)
    current = now or datetime.now()
    if original is None:
        unmet.append("촬영시각(DateTimeOriginal) 없음 또는 해석 불가")
    elif original > current + timedelta(days=1):
        unmet.append("촬영시각이 미래")
    software = metadata.get("exif.Software", "").strip()
    if software and not _software_matches_camera(software, make, model):
        unmet.append(f"Software=「{software[:60]}」(카메라 펌웨어가 아닌 편집·생성 도구)")
    if metadata.get("exif.GPSInfo"):
        latitude, longitude = metadata.get("exif.GPSLatitude"), metadata.get("exif.GPSLongitude")
        try:
            in_range = latitude is not None and longitude is not None and abs(float(latitude)) <= 90 and abs(float(longitude)) <= 180
        except ValueError:
            in_range = False
        if not in_range:
            unmet.append("GPS 좌표 누락 또는 범위 밖")
        gps_date = _parse_exif_datetime(metadata.get("exif.GPSDateStamp"), EXIF_GPS_DATE_FORMAT)
        if gps_date is not None and original is not None and abs((gps_date.date() - original.date()).days) > GPS_DATE_TOLERANCE_DAYS:
            unmet.append("GPS 날짜와 촬영시각 불일치")
    if any(generator_name_in(metadata.get(key, "")) for key, _ in XMP_GENERATOR_FIELDS):
        unmet.append("XMP에 생성 도구명")
    if image_format != "jpeg":
        unmet.append("재압축 흔적 검사는 JPEG에만 적용(0단계)")
    elif jpeg_quality is None:
        unmet.append("JPEG 품질 추정 불가")
    elif jpeg_quality < CAMERA_MIN_JPEG_QUALITY:
        unmet.append(f"JPEG 품질 {jpeg_quality:.0f} < {CAMERA_MIN_JPEG_QUALITY}(메신저·웹 재압축 가능성)")
    facts = [f"기종 「{camera}」"]
    if metadata.get("exif.LensModel"):
        facts.append(f"렌즈 「{metadata['exif.LensModel']}」")
    if original is not None:
        facts.append(f"촬영시각 {original:%Y-%m-%d %H:%M:%S}")
    if metadata.get("exif.GPSInfo"):
        facts.append("GPS 있음")
    if jpeg_quality is not None:
        facts.append(f"JPEG 품질 추정 {jpeg_quality:.0f}")
    if unmet:
        return [EvidenceItem(
            "카메라 EXIF 있음(일관성 조건 미충족)",
            f"{', '.join(facts)}. 미충족: {'; '.join(unmet)}. 원본성 근거로 쓰지 않습니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "metadata",
        )]
    return [EvidenceItem(
        "카메라 EXIF 일관",
        f"{', '.join(facts)}; Software {'없음' if not software else f'「{software[:60]}」'}; 재압축 흔적 없음(품질 ≥ {CAMERA_MIN_JPEG_QUALITY}). "
        "EXIF는 다른 파일로 복사될 수 있어 단독으로 원본성 결론을 내리지 않습니다(렌즈-기종 호환표·메신저 지문 대조는 1단계).",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.AUTHENTIC, EvidenceStrength.MODERATE, "metadata",
    )]


def image_metadata_evidence(
    metadata: Mapping[str, str],
    dimensions: tuple[int, int] | None,
    *,
    metadata_read_error: str | None = None,
    c2pa_present: bool = False,
    image_format: str | None = None,
    jpeg_quality: float | None = None,
    decode_problem: str | None = None,
) -> list[EvidenceItem]:
    """Deterministic items from parsed image metadata and header.

    ``decode_problem`` (R9): the image did not decode — the camera-EXIF
    rule is not evaluated (see camera_exif_evidence).

    "메타데이터 부재" is only stated when the read completed
    (``metadata_read_error`` is None), no C2PA manifest is present and no
    field was found (D16) — a failed read is a failed ``metadata`` check,
    not an absence.
    """
    items: list[EvidenceItem] = []
    # XMP fields have their own rules (xmp_evidence) — keep them out of the
    # generic guess so one field never yields two items.
    generic = {key: value for key, value in metadata.items() if not key.startswith("xmp.")}
    tool_guess = guess_image_source(_tool_field_metadata(generic))
    any_guess = guess_image_source(generic)
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
    items.extend(xmp_evidence(metadata))
    items.extend(camera_exif_evidence(metadata, image_format=image_format, jpeg_quality=jpeg_quality, decode_problem=decode_problem))
    if not metadata and metadata_read_error is None and not c2pa_present:
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


def _c2pa_declarations(validation: Mapping[str, object], where: str) -> list[DeclaredSourceType]:
    """The active manifest's declared source types (``action``) or its ingredients' (``ingredient``).

    techniques-2026 item 2: values are mapped with the IPTC/C2PA vocabulary
    table (digital_source_type.py) — a value of another namespace is unknown,
    never mapped by its last path segment. A validation record without
    ``source_type_declarations`` (older callers, test records) falls back to
    ``digital_source_types`` for ``action``.
    """
    raw = validation.get("source_type_declarations")
    if isinstance(raw, (list, tuple)):
        values = [str(item.get("value", "")) for item in raw if isinstance(item, Mapping) and item.get("where") == where]
    elif where == "action":
        legacy = validation.get("digital_source_types")
        values = [str(item) for item in legacy if isinstance(item, str)] if isinstance(legacy, (list, tuple, set)) else []
    else:
        values = []
    seen: dict[str, DeclaredSourceType] = {}
    for value in values:
        declared = classify_source_type(value)
        seen.setdefault(declared.code, declared)
    return [seen[key] for key in sorted(seen)]


def _codes(raw: object) -> set[str]:
    return {str(code) for code in raw} if isinstance(raw, (list, tuple, set)) else set()


# N6: C2PA status codes that mean the signed manifest no longer matches the
# bytes it covers — assertion.hashedURI.mismatch (an assertion changed),
# assertion.dataHash/bmffHash/boxesHash/collectionHash.mismatch (asset bytes
# changed), claimSignature.mismatch (claim changed). C2PA 2.x spec, §15
# "Validation" status codes.
C2PA_INTEGRITY_TITLE = "C2PA 무결성 불일치"
C2PA_INTEGRITY_DETAIL = "매니페스트 해시 불일치 — 서명 이후 내용이 변경됨"
# techniques-2026: the content hard bindings a validator reports a match for
# (data hash for JPEG/PNG/PDF…, BMFF hash for MP4/MOV/HEIF, box hash, collection hash).
C2PA_HARD_BINDING_MATCH = ("assertion.dataHash.match", "assertion.bmffHash.match", "assertion.boxesHash.match", "assertion.collectionHash.match")


def c2pa_integrity_mismatch_codes(failure_codes: object) -> list[str]:
    """The failure codes that say signed content was altered (N6)."""
    return sorted(code for code in _codes(failure_codes) if code.lower().endswith(".mismatch"))


def _disclosure_text(validation: Mapping[str, object]) -> str:
    raw = validation.get("ai_disclosures")
    parts = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if isinstance(item, Mapping):
            fields = [f"{key}=「{item[key]}」" for key in ("modelType", "modelName", "modelIdentifier", "humanOversightLevel") if item.get(key)]
            parts.append(", ".join(fields) or "필드 없음")
    return "; ".join(parts)


def c2pa_basis_text(validation: Mapping[str, object]) -> str:
    """techniques-2026: "검증 기준: C2PA <ver> 검증기(<sdk>), 신뢰 목록 <id/date/sha>" — "" for a record without it."""
    if not validation.get("trust_basis") and not validation.get("spec_version"):
        return ""
    from .c2pa import trust_basis_text

    spec = str(validation.get("spec_version") or "") or "미기재"
    sdk = str(validation.get("sdk_version") or "")
    return f" 검증 기준: C2PA {spec} 검증기" + (f"({sdk})" if sdk else "") + f", 신뢰 목록 {trust_basis_text(validation.get('trust_basis'))}."


def _c2pa_fact_items(validation: Mapping[str, object], *, usable: bool) -> list[EvidenceItem]:
    """techniques-2026 item 1: the C2PA 2.x facts as neutral items (never a conclusion by themselves).

    ``usable``: the manifest's content binding held (no hash mismatch) — a
    declaration of a tampered manifest is said to be one.
    """
    items: list[EvidenceItem] = []
    caveat = "" if usable else " 단, 서명 이후 내용이 변경된 매니페스트의 선언입니다."
    screen = [d for d in _c2pa_declarations(validation, "action") if d.category == CATEGORY_SCREEN]
    if screen:
        items.append(EvidenceItem(
            "C2PA: 화면 캡처 출처 선언",
            f"매니페스트가 digitalSourceType={', '.join(d.display for d in screen)}를 선언합니다 — 화면에 표시된 다른 콘텐츠를 재촬영한 자산이라는 뜻이며 "
            "카메라 실사 촬영 근거가 아닙니다." + caveat,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE, "c2pa",
        ))
    other = [d for d in _c2pa_declarations(validation, "action") if d.category in (CATEGORY_NEUTRAL, CATEGORY_UNKNOWN)]
    if other:
        items.append(EvidenceItem(
            "C2PA: 그 밖의 디지털 출처 유형 선언",
            f"digitalSourceType={', '.join(d.display for d in other)} — 촬영·생성 어느 쪽 결론에도 쓰지 않는 출처 유형입니다(IPTC {IPTC_VOCABULARY_RELEASE} 어휘)." + caveat,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "c2pa",
        ))
    ingredient_ai = [d for d in _c2pa_declarations(validation, "ingredient") if d.category == CATEGORY_GENERATIVE]
    if ingredient_ai:
        items.append(EvidenceItem(
            "C2PA 구성요소(ingredient)의 생성형 AI 출처 선언",
            f"매니페스트의 구성요소(ingredient)가 digitalSourceType={', '.join(d.display for d in ingredient_ai)}로 선언되어 있습니다(C2PA 2.4). "
            "입력 자료의 출처이며 이 파일 자체의 출처 선언이 아니므로 단독 결론 근거로 쓰지 않습니다." + caveat,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "c2pa",
        ))
    bindings = validation.get("soft_bindings")
    actions = validation.get("watermark_actions")
    binding_parts = [
        f"alg=「{b.get('alg')}」 값=" + ", ".join(f"「{v}」" for v in (b.get("values") or [])[:4])
        for b in (bindings if isinstance(bindings, (list, tuple)) else []) if isinstance(b, Mapping)
    ]
    action_list = [str(a) for a in actions] if isinstance(actions, (list, tuple)) else []
    if binding_parts or action_list:
        items.append(EvidenceItem(
            "C2PA 소프트 바인딩·워터마크 선언",
            ("소프트 바인딩 assertion: " + "; ".join(binding_parts) + ". " if binding_parts else "소프트 바인딩 assertion 없음. ")
            + ("워터마크 액션: " + ", ".join(action_list) + ". " if action_list else "")
            + "매니페스트가 기록한 사실이며, 워터마크가 실제로 들어 있는지는 해당 알고리즘의 디코더 검사(있을 때)가 확인합니다."
            " 소프트 바인딩 ID로 매니페스트를 찾는 온라인 조회는 하지 않습니다(분석 중 네트워크 금지)." + caveat,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "c2pa",
        ))
    embedded = validation.get("embedded_data")
    if isinstance(embedded, (list, tuple)) and embedded:
        listed = ", ".join(f"{e.get('label')}" + (f"({e.get('format')})" if e.get("format") else "") for e in embedded if isinstance(e, Mapping))
        items.append(EvidenceItem(
            "C2PA 내장 데이터(embedded data) assertion",
            f"매니페스트에 내장 데이터 assertion이 있습니다: {listed}. 내용은 해석하지 않고 존재만 기록합니다." + caveat,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "c2pa",
        ))
    return items


def c2pa_evidence(validation: Mapping[str, object] | None) -> list[EvidenceItem]:
    """Deterministic items from ``c2pa.validate_c2pa_manifest`` output.

    N6: a hash mismatch (content changed after signing) is reported as
    "C2PA 무결성 불일치" — deterministic, neutral, strong — never with the
    untrusted-signer wording, whatever the signer's trust status.

    techniques-2026 item 1: a generative declaration is a generative
    ``digitalSourceType`` of the active manifest's actions (IPTC/C2PA
    vocabulary) **or** a ``c2pa.ai-disclosure`` assertion (C2PA 2.4). Under a
    valid signature by a trusted signer with a content-hash match it is
    deterministic/synthetic/strong; otherwise moderate. The other 2.x facts
    (screen capture, other source types, ingredient-level types, soft
    bindings and watermark actions, embedded data) follow as neutral items;
    the main item's detail names the validator's spec version and trust list.
    """
    if not validation or not validation.get("present"):
        return []
    basis = c2pa_basis_text(validation)
    disclosure = _disclosure_text(validation)
    generative = [d for d in _c2pa_declarations(validation, "action") if d.category == CATEGORY_GENERATIVE]
    parts = [f"digitalSourceType={', '.join(d.code for d in generative)}"] if generative else []
    if disclosure:
        parts.append(f"AI 공개(c2pa.ai-disclosure) assertion({disclosure})")
    declared_text = " 및 ".join(parts)
    mismatches = c2pa_integrity_mismatch_codes(validation.get("failure_codes"))
    if mismatches:
        items = [EvidenceItem(
            C2PA_INTEGRITY_TITLE,
            f"{C2PA_INTEGRITY_DETAIL} ({', '.join(mismatches)}). 매니페스트가 서명한 내용과 현재 파일이 다르므로 "
            "매니페스트의 출처 선언을 이 파일의 근거로 쓸 수 없습니다." + basis,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.STRONG, "c2pa",
        )]
        if declared_text:
            items.append(EvidenceItem(
                "C2PA 매니페스트의 생성형 AI 출처 선언(무결성 불일치)",
                f"매니페스트가 {declared_text}를 선언했으나 서명 이후 내용이 변경되어(해시 불일치) 결론 근거로 쓰지 않습니다.",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "c2pa",
            ))
        return items + _c2pa_fact_items(validation, usable=False)
    capture = any(d.category == CATEGORY_CAPTURE for d in _c2pa_declarations(validation, "action"))
    success = _codes(validation.get("success_codes"))
    failure = _codes(validation.get("failure_codes"))
    hash_ok = any(code in success for code in C2PA_HARD_BINDING_MATCH) and not any(code.endswith("Hash.mismatch") for code in failure)
    verified = validation.get("status") == "valid" and bool(validation.get("trusted")) and hash_ok
    signature = validation.get("signature")
    signer = str(signature.get("common_name", "알 수 없음")) if isinstance(signature, dict) else "알 수 없음"
    from .c2pa import c2pa_failure_codes_text, c2pa_state_label

    state = c2pa_state_label(validation.get("state"))  # G4: 무효/유효/신뢰됨, never the SDK's English word
    codes = c2pa_failure_codes_text(sorted(failure))
    facts = _c2pa_fact_items(validation, usable=True)
    if verified and declared_text:
        main = EvidenceItem(
            "C2PA 서명: 생성형 AI 출처 선언",
            f"신뢰 서명자 {signer}의 유효한 매니페스트가 {declared_text}를 선언합니다(해시 일치)." + basis,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "c2pa",
        )
    elif verified and capture:
        main = EvidenceItem(
            "C2PA 서명: 카메라 촬영 출처",
            f"신뢰 서명자 {signer}의 유효한 매니페스트가 digitalCapture를 선언하며 콘텐츠 해시가 일치합니다." + basis,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.AUTHENTIC, EvidenceStrength.STRONG, "c2pa",
        )
    elif verified and _c2pa_declarations(validation, "action"):
        main = EvidenceItem(
            "C2PA 매니페스트 검증됨(촬영·생성 외 출처 유형 선언)",
            f"신뢰 서명자 {signer}의 유효한 매니페스트이며(해시 일치) 선언한 출처 유형은 아래 항목과 같습니다 — 촬영(digitalCapture)도 생성형 AI도 아닙니다." + basis,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE, "c2pa",
        )
    elif verified:
        main = EvidenceItem(
            "C2PA 매니페스트 검증됨(출처 유형 미선언)",
            f"신뢰 서명자 {signer}의 유효한 매니페스트이나 촬영/생성 출처 유형을 선언하지 않았습니다." + basis,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE, "c2pa",
        )
    elif declared_text:
        main = EvidenceItem(
            "C2PA 매니페스트의 생성형 AI 출처 선언(검증 미완료)",
            f"매니페스트가 {declared_text}를 선언하나 검증 상태: {state} ({codes}) — 서명 신뢰 또는 해시 검증이 완료되지 않았습니다." + basis,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "c2pa",
        )
    else:
        main = EvidenceItem(
            "C2PA 매니페스트 존재(검증 미완료)",
            f"서명자 {signer}, 검증 상태: {state} ({codes}). 신뢰 저장소에 없는 서명자이거나 무결성 검증이 끝나지 않아 결론 근거로 쓰지 않습니다." + basis,
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "c2pa",
        )
    return [main, *facts]


EVIDENCE_CONFLICT_TITLE = "근거 충돌: 서명된 원본성 근거와 생성 근거가 함께 있음"


def evidence_conflicts(items: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    """techniques-2026 (coordinator addition): surface, never hide, a strong authentic/synthetic conflict.

    When a strong deterministic authenticity item (a trusted C2PA camera
    signature) and a strong deterministic synthetic item (e.g. a
    self-declared XMP generator field) are both present, decision rule 2
    lets the synthetic one decide. This adds a neutral item that names both
    so the examiner sees the contradiction; it does not change the rule.
    """
    listed = list(items)
    if any(item.title == EVIDENCE_CONFLICT_TITLE for item in listed):
        return []  # already named (a result rebuilt from its own evidence)
    strong = [i for i in listed if i.kind == EvidenceKind.DETERMINISTIC and i.strength == EvidenceStrength.STRONG]
    authentic = [i.title for i in strong if i.direction == EvidenceDirection.AUTHENTIC]
    synthetic = [i.title for i in strong if i.direction == EvidenceDirection.SYNTHETIC]
    if not authentic or not synthetic:
        return []
    return [EvidenceItem(
        EVIDENCE_CONFLICT_TITLE,
        f"원본성 근거({', '.join(authentic)})와 생성·조작 근거({', '.join(synthetic)})가 같은 파일에 함께 있습니다. "
        "현재 결정 규칙은 생성·조작 근거를 우선하므로 결론은 그쪽으로 정해지지만, 두 근거가 모순되므로 "
        "어느 쪽이 위조·편집되었는지(서명 이후 메타데이터 추가 등) 별도로 확인해야 합니다.",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "metadata",
    )]


def calibrated_direction(
    probability: float | None,
    calibration_id: str | None,
    thresholds: Mapping[str, float] | None,
) -> EvidenceDirection:
    """Direction of a calibrated statistical item: its profile threshold decides (N1, G8).

    ``synthetic`` iff ``probability >= thresholds[calibration_id]``; with no
    probability or no threshold for the calibration id it is ``neutral``.
    The former fixed 0.5 cut made decision rule 4 unreachable for any
    profile whose measured threshold is below 0.5 (p=0.40 with threshold
    0.30 stayed "판단 불가"). There is no default cut: a calibration id
    no profile has measured a threshold for is never synthetic.
    """
    if probability is None or not calibration_id or not thresholds or calibration_id not in thresholds:
        return EvidenceDirection.NEUTRAL
    return EvidenceDirection.SYNTHETIC if probability >= float(thresholds[calibration_id]) else EvidenceDirection.NEUTRAL


def with_calibrated_directions(
    items: Iterable[EvidenceItem], thresholds: Mapping[str, float] | None,
) -> list[EvidenceItem]:
    """Re-derive the direction of every calibrated statistical item from
    ``thresholds`` (N1) — ``core.build_classification_result`` applies this
    so rule 4 and rule 5 always see the profile's threshold, whoever built
    the item."""
    out: list[EvidenceItem] = []
    for item in items:
        if item.kind == EvidenceKind.STATISTICAL and item.is_calibrated:
            direction = calibrated_direction(item.probability, item.calibration_id, thresholds)
            if direction != item.direction:
                item = replace(item, direction=direction)
        out.append(item)
    return out


def model_evidence(
    model: ExternalModelAnalysis | None,
    thresholds: Mapping[str, float] | None = None,
) -> EvidenceItem | None:
    """Statistical item for an external model result that produced a score.

    Calibration fields come from the model profile once WP-I measures it;
    until then ``probability`` stays None and the raw score is kept as
    ``raw_score`` so it cannot be read as a probability (QA-OUT-5).
    A calibrated item's direction comes from the profile threshold for its
    calibration id (``thresholds``, N1), never from a fixed 0.5 cut.
    """
    if model is None or not model.available:
        return None
    calibrated = model.probability is not None and bool(model.calibration_id) and bool(model.measured_on)
    if calibrated:
        direction = calibrated_direction(model.probability, model.calibration_id, thresholds)
        threshold = thresholds.get(model.calibration_id or "") if thresholds else None
        cut = f"프로필 임계값 {threshold:.2f}" if threshold is not None else "프로필 임계값 없음 — 결론에 참여하지 않습니다"
        detail = (
            f"{model.label}: 보정 확률 {model.probability:.2f} ({cut}; "
            f"보정 {model.calibration_id}, 측정 {model.measured_on})."
        )
    else:
        direction = EvidenceDirection.SYNTHETIC if model.score >= MODEL_RAW_SCORE_MIDPOINT else EvidenceDirection.NEUTRAL
        detail = f"{model.label}: 원점수 {model.score}/100 — 보정되지 않은 값이며 확률이 아닙니다. 결론에 참여하지 않습니다."
    return EvidenceItem(
        "외부 모델 출력" if calibrated else "외부 모델 원점수(미보정)",
        detail,
        EvidenceKind.STATISTICAL,
        direction,
        EvidenceStrength.MODERATE if calibrated else EvidenceStrength.WEAK,
        "model",
        probability=model.probability if calibrated else None,
        probability_ci=model.probability_ci if calibrated else None,
        calibration_id=model.calibration_id if calibrated else None,
        measured_on=model.measured_on if calibrated else None,
        raw_score=model.score,
    )


DEEP_LAYER_REFERENCE_SUFFIX = "(참고, 미보정)"


def deep_layer_reference(title: str, detail: str, raw_score: int) -> EvidenceSignal:
    """An uncalibrated deep-layer output (face, inpaint, face-swap seam,
    rPPG, avatar, lip-sync, face track) as a reference signal (D13).

    Deep layers have no calibration, so they take no part in the decision
    (WP-A table: "결정 불참여"). As evidence items they carried a synthetic
    direction even when their own detail said "의심 신호는 적습니다", which
    blocked decision rule 5 on clean photos; as reference signals they are
    shown and never counted.
    """
    return EvidenceSignal(
        f"{title}{DEEP_LAYER_REFERENCE_SUFFIX}",
        f"{detail} (원점수 {raw_score}/100, 미보정 — 결론에 참여하지 않습니다)",
        int(raw_score),
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
