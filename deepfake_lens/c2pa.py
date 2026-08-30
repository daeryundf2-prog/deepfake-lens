"""C2PA and metadata forensics module.

Detects Content Authenticity Initiative (CAI) markers, C2PA manifests,
SynthID watermarks, and other provenance signals in image/video files.
"""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ForensicEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class ProvenanceRecord:
    standard: str
    provider: str
    signed: bool
    claim_url: str | None
    details: dict[str, str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MetadataForensicAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[ForensicEvidenceSignal]
    limitations: list[str]
    provenance_records: list[ProvenanceRecord]
    has_c2pa: bool
    has_synthid: bool
    has_watermark: bool

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_metadata_forensic(path: Path | str) -> MetadataForensicAnalysis:
    """Analyze file metadata for C2PA, SynthID, and other provenance signals."""
    file_path = Path(path)
    if not file_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {file_path}")

    try:
        data = file_path.read_bytes()
    except OSError as exc:
        return _error_analysis(f"파일 읽기 오류: {exc}")

    if len(data) == 0:
        return _error_analysis("파일이 비어 있습니다.")

    signals: list[ForensicEvidenceSignal] = []
    limitations: list[str] = []
    provenance_records: list[ProvenanceRecord] = []
    has_c2pa = False
    has_synthid = False
    has_watermark = False

    # Check C2PA manifest
    c2pa_record = _check_c2pa(data)
    if c2pa_record:
        provenance_records.append(c2pa_record)
        has_c2pa = True
        signals.append(ForensicEvidenceSignal(
            "C2PA 매니페스트 발견",
            f"C2PA 표준 콘텐츠 출처 정보가 발견되었습니다 ({c2pa_record.provider}).",
            30,
        ))

    # Check SynthID watermark
    synthid_record = _check_synthid(data)
    if synthid_record:
        provenance_records.append(synthid_record)
        has_synthid = True
        signals.append(ForensicEvidenceSignal(
            "SynthID 워터마크 발견",
            "Google SynthID 워터마크가 감지되었습니다.",
            25,
        ))

    # Check other watermarks
    watermark_record = _check_watermarks(data)
    if watermark_record:
        provenance_records.append(watermark_record)
        has_watermark = True
        signals.append(ForensicEvidenceSignal(
            "워터마크 발견",
            f"{watermark_record.provider} 워터마크가 감지되었습니다.",
            20,
        ))

    # Check ExifTool JSON embedding
    exif_record = _check_exiftool_json(data)
    if exif_record:
        provenance_records.append(exif_record)
        signals.append(ForensicEvidenceSignal(
            "ExifTool JSON 임베딩",
            "ExifTool 형식의 메타데이터가 발견되었습니다.",
            10,
        ))

    # Check PNG chunks
    png_signals = _check_png_chunks(data)
    signals.extend(png_signals)

    # Check JPEG markers
    jpeg_signals = _check_jpeg_markers(data)
    signals.extend(jpeg_signals)

    # Limitations
    if not provenance_records:
        limitations.append("출처 표준 메타데이터가 발견되지 않았습니다.")
    limitations.append("로컬 포렌식 분석 결과이며, 공식 검증이 필요합니다.")

    score = min(100, sum(signal.weight for signal in signals))

    if score >= 50:
        band = "high"
        band_label = "높음"
        verdict = "출처 표준 메타데이터가 강하게 감지됩니다."
    elif score >= 20:
        band = "medium"
        band_label = "주의"
        verdict = "일부 출처 표준 신호가 감지됩니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "출처 표준 메타데이터가 거의 없습니다."

    return MetadataForensicAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        provenance_records=provenance_records,
        has_c2pa=has_c2pa,
        has_synthid=has_synthid,
        has_watermark=has_watermark,
    )


def _error_analysis(message: str) -> MetadataForensicAnalysis:
    return MetadataForensicAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        provenance_records=[],
        has_c2pa=False,
        has_synthid=False,
        has_watermark=False,
    )


def _check_c2pa(data: bytes) -> ProvenanceRecord | None:
    """Check for C2PA manifest in file."""
    # C2PA manifest is typically in a JUMBF box
    # Look for C2PA signature box or CAI markers
    c2pa_markers = [b"c2pa", b"jumbf", b"Content Credentials", b"cai manifest"]
    
    for marker in c2pa_markers:
        if marker in data:
            idx = data.find(marker)
            # Try to find claim URL
            claim_url = None
            search_region = data[max(0, idx-200):min(len(data), idx+500)]
            
            # Look for HTTP URLs
            if b"http" in search_region:
                url_start = search_region.find(b"http")
                url_region = search_region[url_start:url_start+200]
                # Find URL end (null byte, space, or quote)
                for end_char in [b"\x00", b" ", b'"', b"'", b">", b"<"]:
                    url_end = url_region.find(end_char)
                    if url_end > 0:
                        claim_url = url_region[:url_end].decode("utf-8", errors="ignore")
                        break
            
            # Look for signature information
            signed = b"signature" in search_region.lower() or b"signed" in search_region.lower()
            
            return ProvenanceRecord(
                standard="C2PA",
                provider="CAI",
                signed=signed,
                claim_url=claim_url,
                details={"marker": marker.decode("utf-8", errors="ignore"), "position": str(idx)},
            )
    
    return None


def _check_synthid(data: bytes) -> ProvenanceRecord | None:
    """Check for Google SynthID watermark."""
    # SynthID is embedded in image pixels but also has metadata markers
    # Check for Google-specific metadata markers
    google_markers = [
        b"Google", b"SynthID", b"GenerativeAI", b"AI.Generated",
        b"google.com/synthid", b"deepmind", b"gemini",
    ]
    
    for marker in google_markers:
        if marker in data:
            idx = data.find(marker)
            # Verify it's in a metadata context (not random pixel data)
            context = data[max(0, idx-50):min(len(data), idx+100)]
            
            # Check if it's in a text-readable area
            try:
                context_str = context.decode("utf-8", errors="ignore")
                # If it contains mostly printable characters, it's likely metadata
                printable_ratio = sum(1 for c in context_str if c.isprintable()) / max(1, len(context_str))
                if printable_ratio > 0.5:
                    return ProvenanceRecord(
                        standard="SynthID",
                        provider="Google",
                        signed=False,
                        claim_url=None,
                        details={"marker": marker.decode("utf-8", errors="ignore"), "position": str(idx)},
                    )
            except Exception:
                continue
    
    return None


def _check_watermarks(data: bytes) -> ProvenanceRecord | None:
    """Check for other watermarks."""
    watermark_markers = {
        b"Adobe Firefly": "Adobe",
        b"Content Credentials": "CAI",
        b"Stability AI": "Stability",
        b"Midjourney": "Midjourney",
        b"Microsoft MAI": "Microsoft",
        b"Getty Generative": "Getty",
        b"Luma Uni": "Luma",
        b"Krea AI": "Krea",
        b"Gamma Imagine": "Gamma",
        b"Monica AI": "Monica",
        b"Recraft": "Recraft",
        b"Ideogram": "Ideogram",
        b"Leonardo": "Leonardo",
    }

    for marker, provider in watermark_markers.items():
        if marker in data:
            return ProvenanceRecord(
                standard="Watermark",
                provider=provider,
                signed=False,
                claim_url=None,
                details={"marker": marker.decode("utf-8", errors="ignore")},
            )

    return None


def _check_exiftool_json(data: bytes) -> ProvenanceRecord | None:
    """Check for ExifTool JSON embedding."""
    # Look for ExifTool JSON in JPEG COM marker
    if data[:2] == b"\xff\xd8":  # JPEG
        offset = 2
        while offset < len(data) - 1:
            if data[offset] != 0xFF:
                break
            marker = data[offset + 1]
            if marker == 0xFE:  # COM marker
                length = struct.unpack(">H", data[offset+2:offset+4])[0]
                com_data = data[offset+4:offset+2+length]
                if b"ExifTool" in com_data or b"{" in com_data:
                    return ProvenanceRecord(
                        standard="ExifTool",
                        provider="ExifTool",
                        signed=False,
                        claim_url=None,
                        details={"marker": "JPEG COM"},
                    )
                offset += 2 + length
            elif marker in (0xD8, 0xD9):
                break
            else:
                length = struct.unpack(">H", data[offset+2:offset+4])[0]
                offset += 2 + length

    return None


def _check_png_chunks(data: bytes) -> list[ForensicEvidenceSignal]:
    """Check PNG chunks for provenance signals."""
    signals = []
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return signals

    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset+4])[0]
        chunk_type = data[offset+4:offset+8]

        # Check for text chunks with provenance info
        if chunk_type in (b"tEXt", b"iTXt", b"zTXt"):
            chunk_data = data[offset+8:offset+8+length]
            if b"Author" in chunk_data or b"Copyright" in chunk_data:
                signals.append(ForensicEvidenceSignal(
                    "PNG 출처 청크",
                    f"PNG {chunk_type.decode()} 청크에 저작권/저자 정보가 있습니다.",
                    8,
                ))

        offset += 12 + length
        if chunk_type == b"IEND":
            break

    return signals


def _check_jpeg_markers(data: bytes) -> list[ForensicEvidenceSignal]:
    """Check JPEG markers for provenance signals."""
    signals = []
    if data[:2] != b"\xff\xd8":
        return signals

    offset = 2
    while offset < len(data) - 1:
        if data[offset] != 0xFF:
            break
        marker = data[offset + 1]

        if marker == 0xE1:  # APP1 (EXIF)
            length = struct.unpack(">H", data[offset+2:offset+4])[0]
            app_data = data[offset+4:offset+2+length]
            if b"Exif" in app_data:
                signals.append(ForensicEvidenceSignal(
                    "EXIF 메타데이터",
                    "EXIF 메타데이터가 포함되어 있습니다.",
                    5,
                ))
            offset += 2 + length
        elif marker in (0xD8, 0xD9):
            break
        else:
            if offset + 3 < len(data):
                length = struct.unpack(">H", data[offset+2:offset+4])[0]
                offset += 2 + length
            else:
                break

    return signals
