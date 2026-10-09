"""Exception text for user-facing reasons (N1): no file-system paths, Korean where known.

An exception message is copied into a coverage ``reason`` (and from there
into limitations, reports and the evidence statement), so it must not carry
the examiner's file-system layout. :func:`scrub_paths` replaces the scan
root (registered with :func:`path_scrub_root` for the duration of a scan)
with ``<root>`` and every other absolute path with its base name;
:func:`failure_reason` and :func:`exception_text` apply it to every
exception message, and well-known library messages are given in Korean
(:func:`korean_exception_message`).

A leaf module (standard library only) so every analyzer can import it
without an import cycle through ``result_types``.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import logging
import os
import re
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# Upper bound on the exception message kept in a coverage reason — enough
# for the examiner to identify the failure, short enough for a report cell.
FAILURE_MESSAGE_MAX_CHARS = 200


# Placeholder for the scanned folder in a scrubbed message.
ROOT_PLACEHOLDER = "<root>"
# Scan roots registered for the current scan (per thread / task: contextvars
# are copied into each analysis call, so concurrent server scans do not mix).
_SCRUB_ROOTS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("deepfake_lens_scrub_roots", default=())
# Characters that end a path inside a message: whitespace, quotes and the
# punctuation libraries put around a path ("'…'", "(…)", "[…]", "…:").
_PATH_STOP = r"\s'\"`:;,()\[\]{}<>|"
# An absolute POSIX path: a "/" not preceded by a word character, another
# separator, a placeholder ">" or URI punctuation ("://", "#…=/", "1/125").
_POSIX_ABSOLUTE = re.compile(rf"(?<![\w.~<>/\\:=#@%+-])/(?:[^/\\{_PATH_STOP}]+/)*[^/\\{_PATH_STOP}]+")
# An absolute Windows path: drive letter + separator, or a UNC share.
_WINDOWS_ABSOLUTE = re.compile(rf"(?<![\w])(?:[A-Za-z]:|\\\\[^\\/{_PATH_STOP}]+)[\\/](?:[^\\/{_PATH_STOP}]+[\\/])*[^\\/{_PATH_STOP}]+")


# A quoted absolute path ('/home/u/My Docs/a.wav') — quotes let a path
# contain spaces, so the whole quoted string is reduced to its base name.
_QUOTED_ABSOLUTE = re.compile(r"(['\"])((?:/|[A-Za-z]:[\\/]|\\\\)[^'\"\n]*)\1")


def _root_variants(root: Path | str) -> set[str]:
    raw = str(root)
    variants = {raw}
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        variants.add(os.path.abspath(raw))
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        variants.add(str(Path(raw).resolve()))
    return {variant.rstrip("/\\") for variant in variants if variant.rstrip("/\\") not in ("", ".")}


@contextlib.contextmanager
def path_scrub_root(*roots: Path | str | None) -> Iterator[None]:
    """Register scan roots whose paths :func:`scrub_paths` shows as ``<root>``."""
    variants: set[str] = set(_SCRUB_ROOTS.get())
    for root in roots:
        if root is not None:
            variants |= _root_variants(root)
    token = _SCRUB_ROOTS.set(tuple(sorted(variants, key=len, reverse=True)))
    try:
        yield
    finally:
        _SCRUB_ROOTS.reset(token)


def _basename(match: re.Match[str]) -> str:
    text = match.group(0)
    return re.split(r"[\\/]", text)[-1] or text


def scrub_paths(text: str) -> str:
    """``text`` with the scan root as ``<root>`` and other absolute paths as base names (N1)."""
    if not text:
        return text
    for root in _SCRUB_ROOTS.get():
        # A root is replaced only as a whole path component: "/x/case" must
        # not turn "/x/case2/a.jpg" into "<root>2/a.jpg".
        pattern = rf"(?<![\w.~/\\-]){re.escape(root)}(?=[/\\]|$|[{_PATH_STOP}])"
        if not os.path.isabs(root):
            pattern = rf"(?<![\w.~/\\-]){re.escape(root)}(?=[/\\])"
        text = re.sub(pattern, ROOT_PLACEHOLDER, text)
    text = _QUOTED_ABSOLUTE.sub(lambda m: m.group(1) + (re.split(r"[\\/]", m.group(2))[-1] or m.group(2)) + m.group(1), text)
    text = _WINDOWS_ABSOLUTE.sub(_basename, text)
    return _POSIX_ABSOLUTE.sub(_basename, text)


# Korean wording for errno values an OSError reports when a file cannot be
# read. The errno number is kept so the examiner can look the exact cause up.
_ERRNO_KO = {
    errno.ENOENT: "파일 또는 폴더가 없습니다",
    errno.EACCES: "접근 권한이 없습니다",
    errno.EPERM: "허용되지 않은 작업입니다",
    errno.EISDIR: "폴더입니다(파일이 아님)",
    errno.ENOTDIR: "폴더가 아닙니다",
    errno.ELOOP: "심볼릭 링크가 순환합니다",
    errno.EIO: "입출력 오류",
    errno.ENAMETOOLONG: "경로가 너무 깁니다",
    errno.EINVAL: "잘못된 인수",
    errno.ENOSPC: "디스크 공간이 부족합니다",
}

# (pattern, Korean replacement) for library messages seen on unreadable or
# damaged evidence files (Pillow, zipfile, soundfile/libsndfile, c2pa-python,
# OpenCV, json). Applied after path scrubbing; a message that still carries
# English prose after both tables is replaced by :data:`LIBRARY_ERROR_FALLBACK`
# (B6) and the raw text goes to the log only.
_MESSAGE_KO: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in (
        (r"^cannot identify image file (.+)$", r"이미지 형식을 인식할 수 없습니다: \1"),
        (r"^image file is truncated \((\d+) bytes not processed\)$", r"이미지 파일이 잘려 있습니다(처리하지 못한 바이트 \1개)"),
        (r"^image file is truncated$", "이미지 파일이 잘려 있습니다"),
        (r"^broken data stream when reading image file$", "이미지 데이터 스트림이 손상되었습니다"),
        (r"^Truncated File Read$", "파일이 잘려 끝까지 읽지 못했습니다"),
        (r"^unrecognized data stream contents when reading image file$", "이미지 데이터 스트림을 해석할 수 없습니다"),
        (r"^(?:File is not a zip file|Bad magic number for central directory)$", "ZIP 형식이 아닙니다"),
        (r"^Error opening (.+): Format not recogni[sz]ed\.?$", r"오디오 파일을 열 수 없습니다(형식 인식 불가): \1"),
        (r"^Error opening (.+): File contains data in an unknown format\.?$", r"오디오 파일을 열 수 없습니다(알 수 없는 데이터 형식): \1"),
        (r"^Error opening (.+): (.+)$", r"오디오 파일을 열 수 없습니다: \1 (\2)"),
        (r"^Expecting value: line (\d+) column (\d+) \(char (\d+)\)$", r"JSON 형식 오류: \1행 \2열"),
        (r"^No data left in file$", "파일에 더 읽을 데이터가 없습니다"),
        (r"^Unexpected end of data$", "데이터가 예상보다 일찍 끝났습니다"),
        (r"^Input signal length=(\d+) is too small to resample.*$", r"오디오 신호가 너무 짧습니다(길이 \1)"),
    )
)


# Library message fragments translated wherever they occur inside a longer
# message (c2pa-python wraps them as "_C2paOther: Other: <fragment>",
# libsndfile as "Error opening '<file>': <fragment>").
_FRAGMENT_KO: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in (
        (r"asset could not be parsed: ", "파일을 해석할 수 없음: "),
        (r"invalid header signature: expected \"([^\"]*)\", found \"([^\"]*)\"", r"헤더 서명 불일치(예상 \1, 실제 \2)"),
        (r"Invalid block id: (\d+)", r"잘못된 블록 ID \1"),
        (r"Could not parse input (\w+)", r"\1 입력을 해석할 수 없음"),
        (r"\btype is unsupported\b", "지원하지 않는 형식"),
        (r"\b(\w+) out of range\b", r"\1 범위 초과"),
        (r"File does not exist or is not a regular file \(possibly a pipe\?\)\.?", "파일이 없거나 일반 파일이 아님"),
        (r"Error in WAV(?:/W64/RF64)? file\. ([^()]*?)\.?(?=\)|$)", r"WAV 파일 오류(\1)"),
        (r"No '(\w+) ?' chunk marker", r"'\1' 청크 표식 없음"),
        (r"Malformed '(\w+) ?' chunk", r"'\1' 청크 손상"),
        (r"Format not recogni[sz]ed\.?", "형식 인식 불가"),
        (r"File contains data in an unknown format\.?", "알 수 없는 데이터 형식"),
        (r"Unspecified internal error\.?", "내부 오류(세부 정보 없음)"),
        (r"\bfailed to fill whole buffer\b", "데이터가 예상보다 짧습니다(파일 잘림)"),
        # B6: c2pa-python (c2pa-rs) messages seen on damaged manifests.
        (r"could not create valid JUMBF for claim", "클레임의 JUMBF 구조를 만들 수 없음(매니페스트 손상)"),
        (r"invalid embedded file box", "내장 파일 박스가 손상됨"),
        (r"unexpected end of file", "파일이 예상보다 일찍 끝남(파일 잘림)"),
        (r"claim missing", "클레임 없음"),
        (r"JUMBF box not found", "JUMBF 박스 없음"),
        (r"\bmanifest not found\b", "매니페스트 없음"),
        (r"\bno JUMBF data found\b", "JUMBF 데이터 없음"),
        # A c2pa-python class name already rendered into a wrapped message
        # ("_C2paOther: Other: …", "_C2paVerify: Verify: …").
        (r"\b_C2pa(\w+): (?:\1: )?", r"C2PA SDK 오류(\1): "),
        (r"(?<![\w(])Verify: ", "검증 단계: "),
    )
)


# B6: what replaces a library message the tables above do not translate —
# the examiner sees the exception class, the raw English text goes to the
# log (INFO, the CLI log file) and never into a coverage reason or report.
LIBRARY_ERROR_FALLBACK = "라이브러리 오류({cls}) — 상세는 로그 참조"
# c2pa-python raises private subclasses named ``_C2pa<Kind>`` whose message
# repeats the kind ("Other: …"); shown as "C2PA SDK 오류(<Kind>)".
_C2PA_CLASS = re.compile(r"^_C2pa(\w+)$")
C2PA_SDK_ERROR = "C2PA SDK 오류({kind})"

# English-prose detector (B6, shared with the R4 output tests). A text has
# English prose when, after identifier tokens are removed, some sentence
# still holds two consecutive English words. Identifiers are a closed set of
# token shapes that cannot carry a sentence: snake_case / camelCase /
# PascalCase-with-inner-capital, tokens with a digit, hyphenated compounds,
# key=value, --flags, paths and URLs, file names, exception class names,
# backticked code, metadata values quoted verbatim in corner brackets, the acronyms
# below, and the product / generator names below. ALL-CAPS words that are
# not listed acronyms count as words, so shouted prose is caught.
_SENTENCE_END = re.compile(r"[.!?;\n]+(?:\s|$)|[!?;\n]+")
_EN_WORD = r"(?:[A-Za-z]+(?:['’][A-Za-z]+)?)"
_EN_GAP = r"[ \t,:'\"’()\[\]\-–—/]+"
_EN_RUN = re.compile(rf"(?<![\w'’]){_EN_WORD}(?:{_EN_GAP}{_EN_WORD})+(?![\w'’])")
# Acronyms and short codes that stay in English (formats, standards, units,
# status codes printed by doctor). Any other ALL-CAPS word is a word.
KNOWN_ACRONYMS = frozenset("""
    AI API ASGI AUROC AV BMP C2PA CI CLI CNN CPU CSV CUDA DCT DQT ECAPA ECFS EER EXIF FPR FPS GAN GB GIF GPS GPU GUI
    HEIC HF HMAC HTML HTTP HTTPS HWP HWPX ICC ICLR ID IDAT IPTC JPEG JPG JSON JUMBF KB KGW KR KST LAN LBP MB MFCC MISS
    MLM MOV MP3 MP4 MPS OK ONNX OCR PDF PNG PRNU RAM REST RGB ROC SBI SDK SDXL SHA SSIM TB TIFF TPR TSV UI URL USB USM
    UTC UTF UUID VIT WAV WARN WEBP XMP XLSR ZIP EOS EF IS RF SD TTS LR DFL NA
""".split())
# Product, vendor and generator names (identifiers, not prose) that are
# written as separate capitalized words.
KNOWN_PROPER_NOUNS = (
    "Stable Diffusion", "Hugging Face", "Community Forensics", "Deepfake Lens", "Adobe Firefly", "Adobe Photoshop",
    "Microsoft Office Word", "Microsoft Word", "Microsoft Office", "Google Gemini", "Apple Silicon", "Content Credentials",
    "Hemg", "Gustking", "In the Wild", "NAVER Corp",
)
# Python modules, packages and external tools named in dependency messages
# (doctor, install hints) — identifiers, never prose.
KNOWN_MODULE_NAMES = frozenset("""
    torch torchvision transformers onnxruntime numpy librosa soundfile speechbrain mediapipe timm cv2 PIL Pillow
    c2pa pymupdf fitz fastapi uvicorn syhwp ffmpeg ffprobe opencv scipy sklearn
""".split())
_IDENTIFIER_TOKENS: tuple[re.Pattern[str], ...] = (
    re.compile("\u300c[^\u300d]*\u300d"),  # metadata values copied verbatim from the evidence file (corner brackets)
    re.compile(r"https?://\S+"),  # URLs
    # backticked code: a shell command, at most two tokens, or code punctuation
    re.compile(r"`((?:pip|python|deepfake-lens|experiments/)[^`]*|[^`\s]+(?: [^`\s]+)?|[^`]*[-_/.=<>:\[\]{}][^`]*)`"),
    re.compile("|".join(re.escape(name) for name in sorted(KNOWN_PROPER_NOUNS, key=len, reverse=True))),
    re.compile(r"<root>(?:[\\/][^\s'\"]*)?"),  # the scrubbed scan root and paths under it
    re.compile(r"[\w.\-~<>]*[\\/][\w.\-/\\~<>]*"),  # paths, model ids (org/name), fractions (1/4)
    re.compile(
        r"\b[\w\-]+\.(?:py|js|json|md|pth|pt|onnx|torchscript|png|jpe?g|gif|webp|bmp|tiff?|heic|txt|wav|mp3|m4a|flac|ogg|"
        r"mp4|mov|mkv|avi|webm|zip|tar|gz|7z|rar|xml|html?|pdf|csv|docx|xlsx|pptx|hwpx?|doc|xls|ppt|log|bin|exe|so|dll)\b",
        re.IGNORECASE,
    ),  # file names
    re.compile(r"\b_?[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning)\b"),  # exception classes
    re.compile(r"(?<![\w-])--?[A-Za-z][\w-]*"),  # CLI flags
    re.compile(r"\b[\w.:\-]+=\S*"),  # key=value
    re.compile(r"\b\w+(?::[\w\-]+)+"),  # colon-joined ids (model:<name>, failed:pymupdf:RuntimeError)
    re.compile(r"\b\w*_\w*\b"),  # snake_case identifiers
    re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),  # camelCase
    re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b"),  # PascalCase / product ids (EfficientNet)
    re.compile(r"\b\w*\d\w*\b"),  # tokens with a digit (B0, v2, sha256, 1/125)
    re.compile(r"\b[A-Za-z]+(?:-[A-Za-z0-9]+)+\b"),  # hyphenated identifiers (roberta-base)
    re.compile(r"\b[0-9a-fA-F]{8,}\b"),  # hex digests / ids
    # C2PA SDK validation codes (assertion.action.malformed, signingCredential.untrusted; G4)
    re.compile(r"\b(?:assertion|signingCredential|claimSignature|claim|manifest|timeStamp|general|algorithm|ingredient)(?:\.[A-Za-z]\w*)+"),
    re.compile(r"\b(?:pin|sha256)\b"),
    re.compile(r"\b(?:" + "|".join(sorted(KNOWN_MODULE_NAMES)) + r")\b"),
)
_ACRONYM = re.compile(r"\b[A-Z]{2,}s?\b")


def english_prose(text: str) -> str | None:
    """The first English run (two or more consecutive English words) in ``text``, or None.

    Every sentence (split on ``.``, ``!``, ``?``, ``;`` and newlines) is
    checked on its own; Hangul elsewhere in the text exempts nothing. A
    contraction (``Don't``) is one word, a lone English word is not prose.
    """
    stripped = text
    for pattern in _IDENTIFIER_TOKENS:
        stripped = pattern.sub(" ", stripped)
    stripped = _ACRONYM.sub(lambda m: " " if m.group(0).rstrip("s") in KNOWN_ACRONYMS else m.group(0), stripped)
    for sentence in _SENTENCE_END.split(stripped):
        match = _EN_RUN.search(sentence)
        if match:
            return match.group(0)
    return None


def _untranslated_fallback(message: str, cls: str) -> str:
    """``message`` with its first English-prose segment (and the rest) replaced (B6).

    Segments are the ``": "``-separated parts; the Korean context before the
    English part ("C2PA 판독 실패: …") is kept.
    """
    fallback = LIBRARY_ERROR_FALLBACK.format(cls=cls)
    parts = message.split(": ")
    for index, part in enumerate(parts):
        if english_prose(part):
            head = ": ".join(parts[:index])
            return f"{head}: {fallback}" if head else fallback
    return fallback


def korean_exception_message(exc: BaseException) -> str:
    """The exception's message, path-scrubbed and in Korean where known (N1, B6).

    A message the translation tables leave with English prose in it is
    replaced by "라이브러리 오류(<ExceptionClass>) — 상세는 로그 참조"; the raw
    (path-scrubbed) text is logged at INFO — the CLI log file — only.
    """
    if isinstance(exc, OSError) and exc.errno in _ERRNO_KO:
        target = f": {scrub_paths(exc.filename)}" if isinstance(exc.filename, str) and exc.filename else ""
        return f"[Errno {exc.errno}] {_ERRNO_KO[exc.errno]}{target}"
    message = scrub_paths(str(exc)).strip()
    c2pa = _C2PA_CLASS.match(type(exc).__name__)
    if c2pa:
        # "_C2paOther('Other: …')": the kind is already in the class label.
        message = re.sub(rf"^{re.escape(c2pa.group(1))}: ", "", message)
    for pattern, replacement in _MESSAGE_KO:
        if pattern.search(message):
            message = pattern.sub(replacement, message)
            break
    for pattern, replacement in _FRAGMENT_KO:
        message = pattern.sub(replacement, message)
    if english_prose(message):
        logger.info("번역되지 않은 라이브러리 오류 메시지(%s): %s", type(exc).__name__, message)
        message = _untranslated_fallback(message, type(exc).__name__)
    return message


def exception_label(exc: BaseException) -> str:
    """The class part of a failure reason: the class name, or "C2PA SDK 오류(<kind>)" (B6)."""
    c2pa = _C2PA_CLASS.match(type(exc).__name__)
    return C2PA_SDK_ERROR.format(kind=c2pa.group(1)) if c2pa else type(exc).__name__


def exception_text(exc: BaseException, limit: int = FAILURE_MESSAGE_MAX_CHARS) -> str:
    """Path-scrubbed (Korean where known) message of ``exc``, at most ``limit`` chars."""
    return korean_exception_message(exc)[:limit]


def failure_reason(exc: BaseException) -> str:
    """``"<ExcType>: <message>"`` for a failed coverage entry — never a full path (N1)."""
    message = exception_text(exc)
    label = exception_label(exc)
    return f"{label}: {message}" if message else label


