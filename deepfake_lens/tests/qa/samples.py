"""Minimal samples for every supported input extension (QA-IN-1).

``write_samples(folder)`` writes one small, structurally valid file per
supported extension it can synthesize in this environment and returns
``(made, unmade)``: ``made`` maps extension -> path, ``unmade`` maps
extension -> the reason it could not be produced here. The reasons come
from a closed set (``UNMADE_REASONS``) so a test can tell "this
environment lacks Pillow/ffmpeg" apart from a format that was silently
forgotten.

Everything is deterministic (fixed seeds, fixed content), small (a few KB
per file; videos are 1 s at 128x96) and distinct per extension, so a
``--dedupe`` scan analyzes every format instead of marking twins.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import math
import shutil
import struct
import subprocess
import tarfile
import wave
import zipfile
import zlib
from pathlib import Path

from deepfake_lens.archives import SUPPORTED_ARCHIVE_EXTENSIONS
from deepfake_lens.audio import SUPPORTED_AUDIO_EXTENSIONS
from deepfake_lens.core import SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_TEXT_EXTENSIONS
from deepfake_lens.documents import SUPPORTED_DOCUMENT_EXTENSIONS
from deepfake_lens.video_analysis import SUPPORTED_VIDEO_EXTENSIONS

# Binary formats no stdlib/Pillow/ffmpeg encoder writes (D6/QA-IN-1): they
# are still scanned, as minimal byte samples that carry the format's magic
# header — an OLE2 compound-file header for .doc/.xls/.ppt, the same plus a
# "HWP Document File" FileHeader signature for HWP 5.0, the RAR5 marker and
# (without py7zr) the 7z signature header. The tool must answer them with
# "미지원" or "판단 불가 + 이유", never a conclusion; MAGIC_ONLY names them.
MAGIC_ONLY = {
    ".hwp": "HWP 5.0 OLE 헤더 + FileHeader 서명만(본문 스트림 없음)",
    ".doc": "OLE2 복합 문서 헤더만(Word 97 스트림 없음)",
    ".xls": "OLE2 복합 문서 헤더만(Excel 97 스트림 없음)",
    ".ppt": "OLE2 복합 문서 헤더만(PowerPoint 97 스트림 없음)",
    ".rar": "RAR5 시그니처만(RAR 압축기는 독점 소프트웨어)",
}
SEVEN_ZIP_MAGIC_ONLY = "7z 시그니처 헤더만(py7zr 없음)"
NO_PILLOW = "Pillow 없음"
NO_FFMPEG = "ffmpeg 없음"
NO_FFMPEG_ENCODER = "ffmpeg 인코더 없음"
UNMADE_REASONS = frozenset({NO_PILLOW, NO_FFMPEG, NO_FFMPEG_ENCODER})
# OLE2 / Compound File Binary signature ([MS-CFB] 2.2) and its header fields
# for a version-3 file with 512-byte sectors and an empty FAT chain.
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RAR5_SIGNATURE = b"Rar!\x1a\x07\x01\x00"
SEVEN_ZIP_SIGNATURE = b"7z\xbc\xaf\x27\x1c"

SAMPLE_TEXT = "증거 메모: 2026년 10월 9일 오전 회의록 초안. 참석자 3명, 안건 2건."
FFMPEG_TIMEOUT_SECONDS = 60


def supported_extensions() -> set[str]:
    """Every extension the scanner dispatches on (archives incl. ``.tar.gz``)."""
    return (
        set(SUPPORTED_IMAGE_EXTENSIONS) | set(SUPPORTED_TEXT_EXTENSIONS) | set(SUPPORTED_DOCUMENT_EXTENSIONS)
        | set(SUPPORTED_AUDIO_EXTENSIONS) | set(SUPPORTED_VIDEO_EXTENSIONS) | set(SUPPORTED_ARCHIVE_EXTENSIONS)
    )


def _png_bytes(width: int = 96, height: int = 64) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(
        b"\x00" + bytes(v for x in range(width) for v in ((x * 3 + y) % 256, (y * 5) % 256, (x * y) % 256))
        for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )


def _wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(
            struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 220 * n / rate))) for n in range(int(rate * seconds))
        ))
    return buffer.getvalue()


def _ooxml(main_part: str, content_type: str, body: str) -> bytes:
    """A minimal OOXML package: content types, root rels, one main part."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/{main_part}" ContentType="{content_type}"/>'
            '</Types>'
        ))
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            f'Target="{main_part}"/></Relationships>'
        ))
        zf.writestr(main_part, body)
    return buffer.getvalue()


def _docx() -> bytes:
    return _ooxml(
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{SAMPLE_TEXT}</w:t></w:r></w:p></w:body></w:document>',
    )


def _xlsx() -> bytes:
    return _ooxml(
        "xl/workbook.xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>',
    )


def _pptx() -> bytes:
    return _ooxml(
        "ppt/presentation.xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
    )


def _pdf() -> bytes:
    """One-page PDF 1.4 with a text stream and a correct xref table."""
    stream = b"BT /F1 12 Tf 72 720 Td (Evidence memo 2026-10-09) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Producer (deepfake-lens QA-IN-1) /Title (QA sample) >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 6 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _tar(compression: str, tag: str) -> bytes:
    """A one-member tar, then compressed with the stdlib codec (gz/bz2/xz)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tf:
        data = f"{SAMPLE_TEXT} ({tag})".encode("utf-8")
        info = tarfile.TarInfo("inner/memo.txt")
        info.size = len(data)
        info.mtime = 1_760_000_000
        tf.addfile(info, io.BytesIO(data))
    raw = buffer.getvalue()
    if compression == "gz":
        return gzip.compress(raw, mtime=0)
    if compression == "bz2":
        return bz2.compress(raw)
    if compression == "xz":
        return lzma.compress(raw)
    return raw


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inner/memo.txt", SAMPLE_TEXT)
        zf.writestr("inner/pixel.png", _png_bytes(32, 32))
    return buffer.getvalue()


def _pillow_image(ext: str, variant: int) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    image = Image.new("RGB", (160, 120))
    image.putdata([((x * 7 + y + variant) % 256, (y * 3 + variant) % 256, (x * y) % 256) for y in range(120) for x in range(160)])
    fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".bmp": "BMP", ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF"}[ext]
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


# ext -> ffmpeg output arguments (codec/container). Inputs are lavfi test
# sources, so no file is read.
_FFMPEG_VIDEO = {
    ".mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
    ".m4v": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mp4"],
    ".mov": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
    ".mkv": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
    ".avi": ["-c:v", "mpeg4"],
    ".webm": ["-c:v", "libvpx", "-b:v", "200k"],
    ".flv": ["-c:v", "flv1"],
}
_FFMPEG_AUDIO = {
    ".mp3": ["-c:a", "libmp3lame"],
    ".flac": ["-c:a", "flac"],
    ".ogg": ["-c:a", "libvorbis"],
    ".m4a": ["-c:a", "aac"],
    ".aac": ["-c:a", "aac", "-f", "adts"],
    ".wma": ["-c:a", "wmav2", "-f", "asf"],
    ".opus": ["-c:a", "libopus"],
}


def _ffmpeg(ext: str, out: Path, variant: int) -> str | None:
    """Write ``out`` with ffmpeg; None on success, else an UNMADE reason.

    ``variant`` changes the source signal so two containers of the same
    codec (.mp4/.m4v) are not byte-identical duplicates."""
    binary = shutil.which("ffmpeg")
    if binary is None:
        return NO_FFMPEG
    if ext in _FFMPEG_VIDEO:
        source = ["-f", "lavfi", "-i", f"testsrc=size=128x96:rate={10 + variant}", "-t", "1", *_FFMPEG_VIDEO[ext]]
    else:
        source = ["-f", "lavfi", "-i", f"sine=frequency={220 + 20 * variant}:sample_rate=48000", "-t", "1", *_FFMPEG_AUDIO[ext]]
    done = subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-y", *source, "-map_metadata", "-1", str(out)],
        capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False,
    )
    if done.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        return NO_FFMPEG_ENCODER
    return None


def _seven_zip(out: Path) -> str | None:
    """A real 7z with py7zr; otherwise the 32-byte signature header only.

    Returns the magic-only note when no real archive could be written.
    """
    try:
        import py7zr
    except ImportError:
        # Signature, format version 0.4, then a start header (CRC, next
        # header offset/size/CRC) pointing at nothing.
        start = struct.pack("<QQI", 0, 0, 0)
        out.write_bytes(SEVEN_ZIP_SIGNATURE + b"\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start)
        return SEVEN_ZIP_MAGIC_ONLY
    with py7zr.SevenZipFile(out, "w") as archive:
        archive.writestr(SAMPLE_TEXT.encode("utf-8"), "inner/memo.txt")
    return None


def _ole_header(tag: bytes) -> bytes:
    """A 512-byte OLE2 header ([MS-CFB] 2.2: v3, sector shift 9, mini sector
    shift 6, no FAT/directory sectors) plus one sector holding ``tag`` so
    each sample's bytes differ."""
    header = bytearray(512)
    header[0:8] = OLE_SIGNATURE
    struct.pack_into("<HHHHH", header, 0x18, 0x003E, 0x0003, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 0x30, 0xFFFFFFFE)  # first directory sector: end of chain
    struct.pack_into("<I", header, 0x38, 4096)  # mini stream cutoff
    struct.pack_into("<III", header, 0x3C, 0xFFFFFFFE, 0, 0xFFFFFFFE)  # no mini FAT, no DIFAT
    for index in range(109):  # DIFAT array: all free
        struct.pack_into("<I", header, 0x4C + 4 * index, 0xFFFFFFFF)
    sector = tag.ljust(512, b"\x00")
    return bytes(header) + sector


def _magic_only(ext: str) -> bytes:
    if ext == ".rar":
        return RAR5_SIGNATURE + b"QA-IN-1 magic-only RAR sample".ljust(56, b"\x00")
    if ext == ".hwp":
        # HWP 5.0: an OLE2 file whose "FileHeader" stream starts with this
        # 32-byte signature (HWP 5.0 file format spec, 3.2.1).
        return _ole_header(b"HWP Document File".ljust(32, b"\x00") + b"\x00\x00\x05\x05")
    return _ole_header(f"QA-IN-1 magic-only {ext} sample".encode("ascii"))


def write_samples(folder: Path, magic_only: dict[str, str] | None = None) -> tuple[dict[str, Path], dict[str, str]]:
    """One sample per supported extension; returns (made, unmade-with-reason).

    ``magic_only``, when given, is filled with the extensions whose sample
    is only a magic header (see MAGIC_ONLY) and why — they are in ``made``.
    """
    folder.mkdir(parents=True, exist_ok=True)
    made: dict[str, Path] = {}
    unmade: dict[str, str] = {}
    if magic_only is None:
        magic_only = {}
    fixed: dict[str, bytes] = {
        ".txt": SAMPLE_TEXT.encode("utf-8"),
        ".md": f"# 메모\n\n{SAMPLE_TEXT}\n".encode("utf-8"),
        ".wav": _wav_bytes(),
        ".docx": _docx(),
        ".xlsx": _xlsx(),
        ".pptx": _pptx(),
        ".pdf": _pdf(),
        ".zip": _zip(),
        ".tar": _tar("", ".tar"),
        ".tar.gz": _tar("gz", ".tar.gz"),
        ".tgz": _tar("gz", ".tgz"),
        ".tar.bz2": _tar("bz2", ".tar.bz2"),
        ".tbz2": _tar("bz2", ".tbz2"),
        ".tar.xz": _tar("xz", ".tar.xz"),
        ".txz": _tar("xz", ".txz"),
    }
    for variant, ext in enumerate(sorted(supported_extensions())):
        target = folder / f"sample{ext}"
        if ext in fixed:
            target.write_bytes(fixed[ext])
        elif ext in SUPPORTED_IMAGE_EXTENSIONS:
            data = _pillow_image(ext, variant)
            if data is None:
                # PNG needs no Pillow; every other raster format does.
                if ext != ".png":
                    unmade[ext] = NO_PILLOW
                    continue
                data = _png_bytes()
            target.write_bytes(data)
        elif ext in _FFMPEG_VIDEO or ext in _FFMPEG_AUDIO:
            reason = _ffmpeg(ext, target, variant)
            if reason:
                unmade[ext] = reason
                continue
        elif ext == ".7z":
            note = _seven_zip(target)
            if note:
                magic_only[ext] = note
        elif ext in MAGIC_ONLY:
            target.write_bytes(_magic_only(ext))
            magic_only[ext] = MAGIC_ONLY[ext]
        else:
            raise AssertionError(f"no QA-IN-1 sample recipe for {ext}")
        made[ext] = target
    return made, unmade
