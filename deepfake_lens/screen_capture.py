"""Screenshot / recapture facts (TB-5; threat catalogue M-IMG-07).

Facts only (moiré / recapture detection is class B and not here):

- the "Screenshot" user comment iOS and macOS write into their screenshots —
  XMP ``exif:UserComment`` and the EXIF ``UserComment`` tag (0x9286) — read
  from the image's own XMP packet and EXIF block;
- the image size equals a phone/tablet's native screen size (either
  orientation) in ``models/screen-traits.json`` (makers' spec pages; a size is
  shared by many models, so a match names a set and identifies nothing);
- IPTC ``screenCapture`` is already read by the XMP and C2PA rules.

All are neutral, weak facts. A screenshot is legitimate evidence of what a
screen showed, so none of this is manipulation; and when a signed C2PA
manifest declares a camera capture, the limitation reminds that a camera
manifest proves the device photographed *this scene*, not that the scene was
real (a re-photographed screen gets genuine camera metadata).
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

SCREEN_CAPTURE_CHECK = "screen_capture"
SCREEN_LAYER = "screen_capture"
TRAITS_PATH = Path(__file__).resolve().parent / "models" / "screen-traits.json"
TRAITS_SCHEMA = "screen-traits-v1"
EXIF_NS = "http://ns.adobe.com/exif/1.0/"
USER_COMMENT_TAG = 0x9286
EXIF_IFD = 0x8769
USER_COMMENT_HANDOFF = "exif.UserComment"  # versioned_cache kind (R27-5)
MAX_XMP = 1024 * 1024
CAMERA_C2PA_LIMITATION = (
    "카메라 C2PA 서명은 이 기기가 이 장면을 찍었다는 것만 증명하며, 장면이 실제였다는 것(화면·인쇄물을 다시 찍은 것이 아님)은 증명하지 않습니다."
)
_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.I)


@dataclass(frozen=True)
class Traits:
    version: str
    sha256: str
    data: dict[str, Any]

    @property
    def label(self) -> str:
        return f"화면 특성 표 {self.version}(sha256 {self.sha256[:12]}…)"


def load_traits() -> Traits:
    """The screen-traits table — parsed and hashed once per file version (R27-5 pattern, versioned_cache)."""
    from .versioned_cache import load_versioned

    return load_versioned(TRAITS_PATH, _load_traits_file)


def _load_traits_file() -> Traits:
    raw = TRAITS_PATH.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema") != TRAITS_SCHEMA:
        raise ValueError(f"화면 특성 표 형식이 {TRAITS_SCHEMA}가 아닙니다")
    return Traits(str(data.get("version", "")), hashlib.sha256(raw).hexdigest(), data)


def traits_cache_tokens() -> list[str]:
    try:
        traits = load_traits()
    except (OSError, ValueError) as exc:
        return [f"screen-traits:error:{type(exc).__name__}"]
    return [f"screen-traits:{traits.version}:{traits.sha256}"]


def _xmp_packet(path: Path, metadata: Mapping[str, str]) -> bytes | None:
    from .image_metadata import PNG_XMP_KEY, _scan_jpeg_segments, _walk_webp_xmp
    from .versioned_cache import read_prefix

    if metadata.get(PNG_XMP_KEY):
        return metadata[PNG_XMP_KEY].encode("utf-8", errors="replace")
    data = read_prefix(path, 4 * 1024 * 1024)  # R27-5: the metadata layer's prefix of this file version
    if data.startswith(b"\xff\xd8"):
        return _scan_jpeg_segments(data)[0]
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _walk_webp_xmp(path, budget=4 * 1024 * 1024)[0]
    return None


def xmp_user_comments(packet: bytes | None) -> list[str]:
    if not packet or len(packet) > MAX_XMP or _DECLARATION.search(packet):
        return []
    start, end = packet.find(b"<x:xmpmeta"), packet.rfind(b"</x:xmpmeta>")
    if 0 <= start < end:
        packet = packet[start:end + len(b"</x:xmpmeta>")]
    try:
        root = ElementTree.fromstring(packet)
    except ElementTree.ParseError:
        return []
    values: list[str] = []
    for element in root.iter():
        for name, value in element.attrib.items():
            if name == f"{{{EXIF_NS}}}UserComment" and value.strip():
                values.append(value.strip())
        if element.tag == f"{{{EXIF_NS}}}UserComment":
            values.extend(text.strip() for text in element.itertext() if text and text.strip())
    return list(dict.fromkeys(values))


def exif_user_comment(path: Path) -> str | None:
    """EXIF UserComment (8-byte character-code prefix removed); None without Pillow or the tag.

    R27-5: the image metadata layer reads the same tag from the same Pillow ``getexif()`` it already made
    (image_metadata._read_exif_into) and hands it over (versioned_cache); for a PNG that call decodes the whole
    image. Without a hand-off for this path and file version the file is opened here as before.
    """
    from .versioned_cache import MISSING, take

    raw = take(USER_COMMENT_HANDOFF, path)
    if raw is MISSING:
        try:
            from PIL import Image
        except ImportError:
            return None
        try:
            with Image.open(path) as image:
                raw = image.getexif().get_ifd(EXIF_IFD).get(USER_COMMENT_TAG)
        except Exception:  # noqa: BLE001 - a damaged file is the metadata check's failure, not this fact's
            return None
    if isinstance(raw, bytes):
        code, body = raw[:8], raw[8:]
        text = body.decode("utf-16" if code.startswith(b"UNICODE") else "latin-1", errors="replace")
    elif isinstance(raw, str):
        text = raw
    else:
        return None
    text = text.strip("\x00 ").strip()
    return text or None


@dataclass
class ScreenLayers:
    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: list[CoverageEntry] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def _camera_capture_signed(validation: Mapping[str, object] | None) -> bool:
    if not validation or not validation.get("present") or not validation.get("trusted"):
        return False
    raw = validation.get("source_type_declarations")
    values = [str(item.get("value", "")) for item in raw if isinstance(item, Mapping)] if isinstance(raw, (list, tuple)) else []
    if not values:
        legacy = validation.get("digital_source_types")
        values = [str(v) for v in legacy] if isinstance(legacy, (list, tuple)) else []
    return any(value.rsplit("/", 1)[-1] in ("digitalCapture", "computationalCapture") for value in values)


def _evaluate(path: Path, metadata: Mapping[str, str], dimensions: tuple[int, int] | None) -> tuple[list[EvidenceItem], str]:
    traits = load_traits()
    expected = str(traits.data.get("user_comment", {}).get("value", "Screenshot")).casefold()
    items: list[EvidenceItem] = []
    parts: list[str] = []
    comments = [c for c in xmp_user_comments(_xmp_packet(path, metadata)) if c.casefold() == expected]
    exif_comment = exif_user_comment(path)
    where = (["XMP exif:UserComment"] if comments else []) + (["EXIF UserComment"] if exif_comment and exif_comment.casefold() == expected else [])
    if where:
        parts.append(f"화면 캡처 주석({', '.join(where)})")
        items.append(EvidenceItem(
            "화면 캡처 표시(운영체제가 쓴 주석)",
            f"{' · '.join(where)} 값이 「{comments[0] if comments else exif_comment}」입니다 — iOS·macOS가 스크린샷에 쓰는 주석과 같습니다"
            f"(신뢰 수준: {traits.data.get('user_comment', {}).get('confidence')}). 화면에 표시된 내용을 캡처한 파일이라는 서명 없는 자기 신고이며 "
            "카메라 촬영 근거도, 조작 근거도 아닙니다.",
            EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, SCREEN_LAYER, self_declared=True,
        ))
    else:
        parts.append("화면 캡처 주석 없음")
    if dimensions:
        width, height = dimensions
        hits = [r for r in traits.data.get("screen_resolutions", [])
                if isinstance(r, dict) and sorted((width, height)) == sorted((int(r.get("width", 0)), int(r.get("height", 0))))]
        if hits:
            devices = "; ".join(str(r.get("devices")) for r in hits)
            parts.append(f"기기 화면 크기 일치 {width}x{height}")
            items.append(EvidenceItem(
                "기기 화면 해상도와 같은 크기",
                f"이미지 크기 {width}x{height}가 기기의 기본 화면 해상도와 같습니다(표의 기종: 「{devices}」 — 제조사 사양 페이지). 스크린샷 크기와 맞는다는 사실일 뿐이며, "
                "같은 크기를 쓰는 기기가 많고 다른 이미지도 이 크기일 수 있어 기기를 특정하지 않습니다.",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, SCREEN_LAYER,
            ))
        else:
            parts.append("기기 화면 크기와 일치 없음")
    return items, "; ".join(parts) + f"({traits.label})"


def screen_capture_layers(
    path: Path, metadata: Mapping[str, str], dimensions: tuple[int, int] | None, c2pa_validation: Mapping[str, object] | None = None,
) -> ScreenLayers:
    from .checks import run_check

    layers = ScreenLayers()
    outcome, entry = run_check(SCREEN_CAPTURE_CHECK, lambda: _evaluate(path, metadata, dimensions))
    if outcome is not None:
        items, reason = outcome
        layers.evidence.extend(items)
        entry = CoverageEntry(SCREEN_CAPTURE_CHECK, CoverageStatus.RAN, reason)
    layers.coverage.append(entry)
    if _camera_capture_signed(c2pa_validation):
        layers.limitations.append(CAMERA_C2PA_LIMITATION)
    return layers


__all__ = ["CAMERA_C2PA_LIMITATION", "SCREEN_CAPTURE_CHECK", "exif_user_comment", "screen_capture_layers", "traits_cache_tokens", "xmp_user_comments"]
