"""Platform / messenger traces (TB-4; threat catalogue M-IMG-08, M-VID-09, M-AUD-06).

Files that passed through a messenger, a social network, a screenshot tool or a
phone line carry traits of that path. This module records the documented ones
as **facts** — "이 파일의 X가 플랫폼 P를 거친 파일의 X와 일치" — and never
"came from P", never manipulation, never authenticity: another program can
write the same table, the same name or the same band limit. Their value is the
limitation they add: a file that went through such a path has usually lost
its original metadata, C2PA manifest and watermarks, so their absence is no
information.

Checks (each in coverage, ``ran`` / ``skipped`` with a Korean reason):

- ``platform_recompression`` (JPEG images): the DQT tables (zigzag → natural
  order), chroma subsampling, baseline/progressive and EXIF presence, compared
  exactly with the pinned library ``models/platform-traces.json`` (every table
  with its source; platforms without a citable table have none).
- ``file_name_pattern`` (images, video, audio): the analyzed file's own name
  against the library's name formats (messenger saves, screenshots, screen
  and call recordings). The name is an analysis input, so it is part of the
  scan-cache key as the ids of the formats it matches (scan_cache.CACHE_KEY_VERSION
  content-v5; the row never quotes the name, so same-format names share a row).
- ``audio_channel_trace`` (audio): raw AMR / AMR-WB storage magic (RFC 4867
  §5), the Ogg Opus identification and comment headers (RFC 7845: input
  sample rate, channels, vendor string — e.g. WhatsApp's), and the measured
  effective bandwidth of the first :data:`BANDWIDTH_SECONDS` seconds (ffmpeg
  decode; the highest frequency whose smoothed power is within
  :data:`BANDWIDTH_DB` dB of the peak) with the documented bands it matches.
  MP4/3GP codec boxes are the container-metadata layer's (not read here).
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .check_skipped import CheckSkipped
from .result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

LIBRARY_PATH = Path(__file__).resolve().parent / "models" / "platform-traces.json"
LIBRARY_SCHEMA = "platform-traces-v1"
PLATFORM_LAYER = "platform"
RECOMPRESSION_CHECK = "platform_recompression"
FILE_NAME_CHECK = "file_name_pattern"
AUDIO_CHANNEL_CHECK = "audio_channel_trace"
JPEG_SCAN_BYTES = 4 * 1024 * 1024
BANDWIDTH_SECONDS = 60
BANDWIDTH_DB = 60.0
BANDWIDTH_BIN_HZ = 12.0
MIN_BANDWIDTH_SECONDS = 1.0

NO_INFORMATION = "무정보"
PATH_LIMITATION = (
    "메신저·SNS·화면 캡처·통화 경로의 흔적이 관찰되었습니다 — 이런 경로는 원본 메타데이터·C2PA 매니페스트·워터마크를 지우는 일이 많아 "
    "그 부재는 무정보이며, 원본 파일(메신저의 '파일로 보내기' 전송본 등)을 확보하면 더 많은 사실을 확인할 수 있습니다."
)
FACT_CAVEAT = "다른 프로그램도 같은 흔적을 만들 수 있어 경로를 확정하지 않으며, 조작·원본 어느 쪽의 근거도 아닙니다."

ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
)


class LibraryError(RuntimeError):
    """The platform-trace library cannot be read (Korean) — the checks that need it fail."""


@dataclass(frozen=True)
class Library:
    version: str
    sha256: str
    data: dict[str, Any]

    @property
    def label(self) -> str:
        return f"플랫폼 흔적 표 {self.version}(sha256 {self.sha256[:12]}…)"


_LIBRARY: dict[tuple[int, int], Library] = {}


def load_library(path: Path | None = None) -> Library:
    target = path or LIBRARY_PATH
    try:
        info = target.stat()
        key = (info.st_mtime_ns, info.st_size)
        if path is None and key in _LIBRARY:
            return _LIBRARY[key]
        raw = target.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise LibraryError(f"플랫폼 흔적 표를 읽을 수 없습니다: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("schema") != LIBRARY_SCHEMA:
        raise LibraryError(f"플랫폼 흔적 표 형식이 {LIBRARY_SCHEMA}가 아닙니다")
    library = Library(str(data.get("version", "")), hashlib.sha256(raw).hexdigest(), data)
    if path is None:
        _LIBRARY[key] = library
    return library


def library_cache_tokens() -> list[str]:
    try:
        library = load_library()
    except LibraryError as exc:
        return [f"platform-traces:error:{exc}"]
    return [f"platform-traces:{library.version}:{library.sha256}"]


@dataclass
class TraceLayers:
    evidence: list[EvidenceItem] = field(default_factory=list)
    coverage: list[CoverageEntry] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    matched: bool = False

    def extend(self, other: "TraceLayers") -> None:
        self.evidence.extend(other.evidence)
        self.coverage.extend(other.coverage)
        self.matched = self.matched or other.matched


def _item(title: str, detail: str) -> EvidenceItem:
    return EvidenceItem(title, detail, EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, PLATFORM_LAYER)


# --- JPEG structure ----------------------------------------------------------


@dataclass
class JpegStructure:
    tables: dict[int, list[int]] = field(default_factory=dict)  # table id -> 64 values, natural order
    components: list[tuple[int, int, int, int]] = field(default_factory=list)  # (id, h, v, table id)
    progressive: bool = False
    exif: bool = False
    width: int = 0
    height: int = 0

    @property
    def subsampling(self) -> str:
        if len(self.components) < 3:
            return "흑백" if self.components else "알 수 없음"
        (_, h0, v0, _), (_, h1, v1, _) = self.components[0], self.components[1]
        if (h1, v1) != (1, 1):
            return f"비표준({h0}x{v0},{h1}x{v1})"
        return {(1, 1): "4:4:4", (2, 1): "4:2:2", (2, 2): "4:2:0", (1, 2): "4:4:0", (4, 1): "4:1:1"}.get((h0, v0), f"비표준({h0}x{v0})")

    def table_for(self, component: int) -> list[int] | None:
        if component >= len(self.components):
            return None
        return self.tables.get(self.components[component][3])


def parse_jpeg(data: bytes) -> JpegStructure:
    """DQT/SOF/APP1 of a JPEG prefix (raises CheckSkipped when it is not a JPEG or has no tables)."""
    if not data.startswith(b"\xff\xd8"):
        raise CheckSkipped("JPEG 아님 — 양자화표 대조 대상 아님")
    out = JpegStructure()
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = struct.unpack_from(">H", data, pos + 2)[0]
        body = data[pos + 4:pos + 2 + length]
        if marker == 0xDB:
            i = 0
            while i < len(body):
                precision, table_id = body[i] >> 4, body[i] & 0x0F
                size = 128 if precision else 64
                raw = body[i + 1:i + 1 + size]
                if len(raw) < size:
                    break
                values = list(struct.unpack(">64H", raw)) if precision else list(raw)
                natural = [0] * 64
                for k, index in enumerate(ZIGZAG):
                    natural[index] = values[k]
                out.tables[table_id] = natural
                i += 1 + size
        elif marker in (0xC0, 0xC1, 0xC2) and len(body) >= 6:
            out.progressive = marker == 0xC2
            out.height, out.width = struct.unpack_from(">HH", body, 1)
            count = body[5]
            for c in range(count):
                base = 6 + 3 * c
                if base + 3 > len(body):
                    break
                out.components.append((body[base], body[base + 1] >> 4, body[base + 1] & 0x0F, body[base + 2]))
        elif marker == 0xE1 and body.startswith(b"Exif\x00\x00"):
            out.exif = True
        elif marker == 0xDA:
            break
        pos += 2 + length
    if not out.tables:
        raise CheckSkipped("JPEG 양자화표(DQT)를 찾지 못함 — 대조하지 않음")
    return out


def recompression_layers(path: Path) -> TraceLayers:
    from .checks import run_check
    from .scan_cache import _read_prefix

    layers = TraceLayers()

    def check() -> tuple[JpegStructure, Library]:
        return parse_jpeg(_read_prefix(path, JPEG_SCAN_BYTES)), load_library()

    outcome, entry = run_check(RECOMPRESSION_CHECK, check)
    if outcome is None:
        layers.coverage.append(entry)
        return layers
    structure, library = outcome
    luma, chroma = structure.table_for(0), structure.table_for(1)
    matches = []
    for table in library.data.get("jpeg_tables", []):
        if not isinstance(table, dict) or luma != table.get("luma"):
            continue
        expected_chroma = table.get("chroma")
        if expected_chroma == "same_as_luma" and chroma is not None and chroma != luma:
            continue
        if isinstance(expected_chroma, list) and chroma != expected_chroma:
            continue
        matches.append(table)
    shape = (
        f"양자화표 {len(structure.tables)}개, 크로마 {structure.subsampling}, {'점진(progressive)' if structure.progressive else '기저(baseline)'}, "
        f"EXIF {'있음' if structure.exif else '없음'}, {structure.width}x{structure.height}"
    )
    if matches:
        names = ", ".join(str(m.get("platform")) for m in matches)
        layers.matched = True
        layers.coverage.append(CoverageEntry(RECOMPRESSION_CHECK, CoverageStatus.RAN, f"{shape} — 일치: {names}({library.label})"))
        for match in matches:
            compared = "광도·색차 표" if match.get("chroma") != "not_published" else "광도 표(색차 표는 출처에 없음)"
            layers.evidence.append(_item(
                "플랫폼 재압축 양자화표와 일치",
                f"JPEG {compared}가 「{match.get('platform')}」의 재압축 표와 64개 값 모두 같습니다({shape}). 출처는 플랫폼 흔적 표의 해당 항목에 있습니다"
                f"(신뢰 수준: {match.get('confidence')}, 확인 {match.get('checked')}). "
                + ("EXIF가 없어 촬영 메타데이터가 전송 중 지워졌을 수 있습니다. " if not structure.exif else "")
                + FACT_CAVEAT + f" 기준: {library.label}.",
            ))
    else:
        known = len(library.data.get("jpeg_tables", []))
        layers.coverage.append(CoverageEntry(
            RECOMPRESSION_CHECK, CoverageStatus.RAN, f"{shape} — 등록 표 {known}개와 일치 없음 — {NO_INFORMATION}(표가 없는 플랫폼이 더 많음)",
        ))
    return layers


# --- file names ----------------------------------------------------------------


def _matching_patterns(path: Path, library: Library) -> list[dict[str, Any]]:
    name = unicodedata.normalize("NFC", path.name)
    return [p for p in library.data.get("filename_patterns", []) if isinstance(p, dict) and re.match(str(p.get("regex", "$^")), name, re.IGNORECASE)]


def name_pattern_ids(path: Path) -> list[str]:
    """The ids of the formats ``path``'s name matches — the scan-cache key token (raises LibraryError)."""
    return sorted(str(p.get("id")) for p in _matching_patterns(path, load_library()))


def file_name_layers(path: Path) -> TraceLayers:
    from .checks import run_check

    layers = TraceLayers()

    def check() -> tuple[list[dict[str, Any]], Library]:
        library = load_library()
        return _matching_patterns(path, library), library

    outcome, entry = run_check(FILE_NAME_CHECK, check)
    if outcome is None:
        layers.coverage.append(entry)
        return layers
    found, library = outcome
    if found:
        layers.matched = True
        meanings = ", ".join(str(p.get("meaning")) for p in found)
        layers.coverage.append(CoverageEntry(FILE_NAME_CHECK, CoverageStatus.RAN, f"이름 형식 일치: {meanings}({library.label})"))
        for pattern in found:
            layers.evidence.append(_item(
                "파일 이름 형식: " + str(pattern.get("meaning")),
                f"파일 이름이 「{pattern.get('meaning')}」과 같은 형식입니다(출처는 플랫폼 흔적 표의 해당 항목, 신뢰 수준: {pattern.get('confidence')}). "
                "이름은 누구나 바꿀 수 있으므로 경로를 확정하지 않습니다. " + FACT_CAVEAT,
            ))
    else:
        known = len(library.data.get("filename_patterns", []))
        layers.coverage.append(CoverageEntry(FILE_NAME_CHECK, CoverageStatus.RAN, f"등록된 이름 형식 {known}개와 일치 없음 — {NO_INFORMATION}"))
    return layers


# --- audio channel ---------------------------------------------------------------


@dataclass
class AudioChannel:
    codec: str = ""
    detail: str = ""
    vendor: str = ""
    bandwidth_hz: float | None = None
    sample_rate: int = 0
    seconds: float = 0.0


def _ogg_opus(head: bytes) -> tuple[str, str] | None:
    """(detail, vendor) from the first Ogg pages of an Ogg Opus stream (RFC 7845 §5.1, §5.2)."""
    if not head.startswith(b"OggS"):
        return None
    at = head.find(b"OpusHead")
    if at < 0 or at + 19 > len(head):
        return None
    channels = head[at + 9]
    rate = struct.unpack_from("<I", head, at + 12)[0]
    vendor = ""
    tags = head.find(b"OpusTags", at)
    if tags >= 0 and tags + 12 <= len(head):
        size = struct.unpack_from("<I", head, tags + 8)[0]
        vendor = head[tags + 12:tags + 12 + min(size, 200)].decode("utf-8", errors="replace")
    return f"Ogg 컨테이너의 Opus 코덱(채널 {channels}, 입력 표본화 {rate} Hz)", vendor


def codec_trace(head: bytes) -> tuple[str, str, str]:
    """(codec, detail, vendor) of the stream magic in ``head`` — "" when none is known."""
    for magic, name in ((b"#!AMR-WB_MC1.0\n", "AMR-WB(다채널)"), (b"#!AMR-WB\n", "AMR-WB"), (b"#!AMR_MC1.0\n", "AMR(다채널)"), (b"#!AMR\n", "AMR-NB")):
        if head.startswith(magic):
            band = "광대역 50–7000 Hz" if "WB" in name else "협대역 약 200–3400 Hz"
            return name, f"{name} 원시 저장 형식(RFC 4867 §5 머리 「{magic.decode().strip()}」, {band})", ""
    opus = _ogg_opus(head)
    if opus is not None:
        return "Opus", opus[0], opus[1]
    return "", "", ""


def effective_bandwidth(samples: Any, rate: int) -> float | None:
    """Highest frequency whose average power (Blackman-Harris, ~12 Hz bins) is within BANDWIDTH_DB of the peak (None for silence)."""
    import numpy as np

    n_fft = 1 << max(10, int(np.ceil(np.log2(rate / BANDWIDTH_BIN_HZ))))
    if len(samples) < n_fft:
        return None
    hop = n_fft // 2
    # 4-term Blackman-Harris window (sidelobes about -92 dB) — a Hann window's leakage reaches -60 dB only ~5 bins out.
    k = np.arange(n_fft) * 2 * np.pi / (n_fft - 1)
    window = 0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k) - 0.01168 * np.cos(3 * k)
    frames = (len(samples) - n_fft) // hop + 1
    power = np.zeros(n_fft // 2 + 1)
    for index in range(frames):
        chunk = samples[index * hop:index * hop + n_fft] * window
        power += np.abs(np.fft.rfft(chunk)) ** 2
    power /= max(1, frames)
    if not np.any(power > 0):
        return None
    db = 10 * np.log10(np.maximum(power, 1e-30))
    above = np.nonzero(db >= db.max() - BANDWIDTH_DB)[0]
    return float(above[-1] * rate / n_fft) if len(above) else None


SOUNDFILE_EXTENSIONS = frozenset({".wav", ".flac", ".ogg"})


def _soundfile_mono(path: Path) -> tuple[Any, int] | None:
    """The first BANDWIDTH_SECONDS read in-process with soundfile (libsndfile formats) — None to fall back to ffmpeg.

    Spawning ffmpeg for every WAV made an audio scan 30% slower (TB-4 perf probe); libsndfile reads these formats.
    """
    if path.suffix.lower() not in SOUNDFILE_EXTENSIONS:
        return None
    try:
        import soundfile
    except ImportError:
        return None
    from .native_path import native_safe_path

    try:
        with native_safe_path(path) as native:
            with soundfile.SoundFile(native) as handle:
                rate = int(handle.samplerate)
                data = handle.read(frames=rate * BANDWIDTH_SECONDS, dtype="float64", always_2d=True)
    except Exception:  # noqa: BLE001 - any libsndfile refusal falls back to the ffmpeg decode (which reports its own failure)
        return None
    return data, rate


def _decode_mono(path: Path) -> tuple[Any, int, float]:
    import numpy as np

    from .audio import AudioDecoderMissing, NoAudioStream, ffmpeg_audio_wav
    from .float_wav import read_float_wav

    direct = _soundfile_mono(path)
    if direct is not None and len(direct[0]):
        frames, rate = direct
        mono = np.asarray(frames.mean(axis=1), dtype=np.float64)
        return mono, rate, len(mono) / rate if rate else 0.0
    try:
        with ffmpeg_audio_wav(path, seconds=BANDWIDTH_SECONDS) as wav:
            frames, rate = read_float_wav(wav)
    except AudioDecoderMissing as exc:
        raise CheckSkipped(str(exc)) from exc
    except NoAudioStream as exc:
        raise CheckSkipped("오디오 트랙 없음") from exc
    mono = np.asarray(frames.mean(axis=1), dtype=np.float64)
    return mono, rate, len(mono) / rate if rate else 0.0


def audio_channel_layers(path: Path) -> TraceLayers:
    from .checks import run_check
    from .scan_cache import _read_prefix

    layers = TraceLayers()

    def check() -> tuple[AudioChannel, Library]:
        library = load_library()
        trace = AudioChannel()
        trace.codec, trace.detail, trace.vendor = codec_trace(_read_prefix(path, 64 * 1024))
        import numpy  # noqa: F401 — dependency probe for the bandwidth measurement

        samples, trace.sample_rate, trace.seconds = _decode_mono(path)
        if trace.seconds < MIN_BANDWIDTH_SECONDS:
            raise CheckSkipped(f"측정 범위 밖: 길이 {trace.seconds:.2f}초(1초 미만)")
        trace.bandwidth_hz = effective_bandwidth(samples, trace.sample_rate)
        return trace, library

    outcome, entry = run_check(AUDIO_CHANNEL_CHECK, check)
    if outcome is None:
        layers.coverage.append(entry)
        return layers
    trace, library = outcome
    traces = library.data.get("audio_codec_traces", {}) if isinstance(library.data.get("audio_codec_traces"), dict) else {}
    parts = []
    if trace.codec:
        parts.append(trace.detail)
    if trace.codec.startswith("AMR"):
        layers.matched = True
        layers.evidence.append(_item(
            f"통화·메신저 코덱 흔적: {trace.codec}",
            f"오디오 스트림 머리가 {trace.detail}입니다. 휴대전화 통화·음성 녹음이 쓰는 음성 코덱이라는 사실이며 "
            "녹음 장치나 내용의 진위는 뜻하지 않습니다. " + FACT_CAVEAT,
        ))
    vendor_match = next((v for v in traces.get("opus_vendors", []) if isinstance(v, dict) and trace.vendor and trace.vendor == v.get("vendor")), None)
    if vendor_match is not None:
        layers.matched = True
        layers.evidence.append(_item(
            "메신저 음성 인코더 문자열과 일치",
            f"Opus 주석 머리의 인코더 문자열 「{trace.vendor}」이(가) 「{vendor_match.get('meaning')}」과 같습니다"
            f"(출처는 플랫폼 흔적 표의 해당 항목, 신뢰 수준: {vendor_match.get('confidence')}). 문자열은 누구나 쓸 수 있습니다. " + FACT_CAVEAT,
        ))
    elif trace.vendor:
        parts.append(f"인코더 문자열 「{trace.vendor}」")
    if trace.bandwidth_hz is not None:
        nyquist = trace.sample_rate / 2
        band = next(
            (b for b in traces.get("bands", [])
             if isinstance(b, dict) and float(b.get("min_hz", 0)) <= trace.bandwidth_hz <= float(b.get("max_hz", 0)) < nyquist * 0.98),
            None,
        )
        measured = f"유효 대역폭 약 {trace.bandwidth_hz / 1000:.1f} kHz(표본화 {trace.sample_rate} Hz, 처음 {trace.seconds:.1f}초, 최대 대비 −{BANDWIDTH_DB:.0f} dB 기준)"
        parts.append(measured)
        if band is not None:
            layers.matched = True
            layers.evidence.append(_item(
                "음성 유효 대역폭 제한",
                f"{measured} — 신호가 표본화 상한({nyquist / 1000:.1f} kHz)보다 훨씬 낮은 대역에서 끝납니다: 「{band.get('meaning')}」(출처는 플랫폼 흔적 표의 해당 항목). "
                "전화·코덱·저가 마이크·낮은 표본화 원본을 올린 경우 모두 같은 결과를 내므로 경로·생성 여부를 판단하지 않습니다. " + FACT_CAVEAT,
            ))
    reason = "; ".join(parts) if parts else "코덱 머리·대역 측정 결과 없음(무음)"
    layers.coverage.append(CoverageEntry(AUDIO_CHANNEL_CHECK, CoverageStatus.RAN, f"{reason}({library.label})"))
    return layers


# --- per-modality entry points -------------------------------------------------------


def _finish(layers: TraceLayers) -> TraceLayers:
    if layers.matched:
        layers.limitations.append(PATH_LIMITATION)
    return layers


def image_trace_layers(path: Path) -> TraceLayers:
    layers = recompression_layers(path)
    layers.extend(file_name_layers(path))
    return _finish(layers)


def video_trace_layers(path: Path) -> TraceLayers:
    return _finish(file_name_layers(path))


def audio_trace_layers(path: Path) -> TraceLayers:
    layers = file_name_layers(path)
    layers.extend(audio_channel_layers(path))
    return _finish(layers)


__all__ = [
    "AUDIO_CHANNEL_CHECK",
    "FILE_NAME_CHECK",
    "JpegStructure",
    "LIBRARY_PATH",
    "PATH_LIMITATION",
    "RECOMPRESSION_CHECK",
    "audio_trace_layers",
    "codec_trace",
    "effective_bandwidth",
    "image_trace_layers",
    "library_cache_tokens",
    "load_library",
    "parse_jpeg",
    "video_trace_layers",
]
