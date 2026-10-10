"""Exception text for user-facing reasons (N1): no file-system paths, Korean where known.

An exception message is copied into a coverage ``reason`` (and from there
into limitations, reports and the evidence statement), so it must not carry
the examiner's file-system layout. :func:`scrub_paths` replaces the scan
root (registered with :func:`path_scrub_root` for the duration of a scan)
with ``<root>`` and every other absolute path with its base name;
:func:`failure_reason` and :func:`exception_text` apply it to every
exception message, and well-known library messages are given in Korean
(:func:`korean_exception_message`).

A leaf module (standard library only, plus the equally leaf
:mod:`deepfake_lens.native_path`) so every analyzer can import it without an
import cycle through ``result_types``.

R13-1: a staged ASCII name that a native decoder quoted in its message
(``native_path.native_safe_path``) is first put back to the original path
(:func:`native_path.restore_original_names`), then scrubbed like any other
path — so the reason is the one an ASCII-named copy would get.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterator

from .native_path import restore_original_names

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
# An absolute POSIX path: a "/" at the start or after whitespace or the
# punctuation libraries put around a path — never after a character that can
# end a path segment (a word character, another separator, a placeholder
# ">", URI punctuation "://", "#…=/", "1/125"). R13-1: the test is "allowed
# before", not "forbidden before": a lone surrogate (an undecodable byte of a
# POSIX name, "sub\udcb0\udcc5/a.wav") or "!" / "&" ending a folder name is
# part of the path, so "<root>/sub\udcb0\udcc5/a.wav" keeps its folder.
_POSIX_ABSOLUTE = re.compile(r"(?<![^\s'\"`;,()\[\]{}|])/" + rf"(?:[^/\\{_PATH_STOP}]+/)*[^/\\{_PATH_STOP}]+")
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
    # R13-1: a staged temp name never reaches a reason — the original path does.
    text = restore_original_names(text)
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
        # P9 (round 8): every json.JSONDecodeError message — the position is
        # kept as numbers only ("JSON 형식 오류: 값이 필요합니다(1행 2열)").
        *((rf"^{re.escape(english)}: line (\d+) column (\d+) \(char (\d+)\)$", rf"JSON 형식 오류: {korean}(\1행 \2열)")
          for english, korean in (
              ("Expecting value", "값이 필요합니다"),
              ("Expecting property name enclosed in double quotes", "속성 이름은 큰따옴표로 감싸야 합니다"),
              ("Expecting ':' delimiter", "':' 구분자가 필요합니다"),
              ("Expecting ',' delimiter", "',' 구분자가 필요합니다"),
              ("Unterminated string starting at", "문자열이 닫히지 않았습니다"),
              ("Invalid control character at", "허용되지 않는 제어 문자가 있습니다"),
              ("Invalid \\escape", "잘못된 이스케이프 문자"),
              ("Invalid \\uXXXX escape", "잘못된 유니코드 이스케이프"),
              ("Extra data", "JSON 값 뒤에 남는 데이터가 있습니다"),
              ("Illegal trailing comma before end of object", "객체 끝 앞에 쉼표가 있습니다"),
              ("Illegal trailing comma before end of array", "배열 끝 앞에 쉼표가 있습니다"),
              ("Unexpected UTF-8 BOM (decode using utf-8-sig)", "파일 앞에 UTF-8 BOM이 있습니다"),
          )),
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
        # N17: libsndfile says this for an existing file it cannot decode (a
        # truncated MP3, an unsupported codec) — the analyzers only open files
        # that exist (missing inputs are refused before, cli_inputs/N4).
        (r"File does not exist or is not a regular file \(possibly a pipe\?\)\.?", "오디오 디코드 실패(파일 손상 또는 미지원 코덱)"),
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
    TXT MD DOCX XLSX PPTX DOC XLS PPT MKV AVI WEBM FLAC OGG AAC WMA TAR GZ RAR
""".split())
# R11-5: the file-format names above (TXT … RAR) — "PNG·JPG·TXT·MD·MKV·TAR 등"
# is read word by word now that "·" is a token boundary.
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


# ---------------------------------------------------------------------------
# Round 5 (G9): dictionary rule. The run rule above is bypassed by one-word
# sentences ("Unverified. Unreliable. Ignore."), English words separated by
# Hangul ("Ignore 이 점수, 참고 only"), separators ("do-not-use-as-evidence",
# "not/for/court/use"), quoting ("「Do not trust this score」") and leetspeak
# ("Th1s sc0re 1s n0t pr00f"). The dictionary rule tokenizes every line on
# spaces and on - / · 「」 『』 quotes, brackets and punctuation, maps digits
# inside letter tokens back to letters (0→o, 1→i, 3→e, 4→a, 5→s, 7→t), and
# flags a line holding two or more distinct common English words
# (COMMON_ENGLISH_WORDS) — or one of the disclaimer/verdict words
# (STANDALONE_ENGLISH_WORDS) standing as a word of its own. Only whole
# identifier tokens are removed first: URLs, file names and paths, model ids,
# exception class names, CLI flags, key=value, snake/camel case, hex, the
# acronym / product / module lists above and the packaged profile names
# (IDENTIFIER_ALLOWLIST). Hyphenated tokens are split unless they carry a
# digit (version ids such as layer-thresholds-v1) or are on the allowlist.
# ---------------------------------------------------------------------------
COMMON_ENGLISH_WORDS = frozenset("""
    about above according across after again against all almost alone along already also although always am among an
    and another any anything are around as ask at authentic available avoid away back bad based be because been before
    being below best better between both but by calibrated calibration can cannot careful case caution certain check
    checked claim clean clear come conclusion confidence confirm consider could court current data decide decision
    definitely detect detected did different do does doing done don down during each easy either else enough error
    especially even ever every evidence exact example fail failed failure false far few file final find fine first for
    found free from full further genuine get give given go good great had has have having he help her here high him his
    how however if ignore ignored important in indeed instead into is it its itself just keep know known label large
    last later least less let like likely little long look low made main make many may maybe me might more most much
    must my need needed never new next no none nor not note nothing now of off often ok on once one only open or order
    other others our out over own part passed perhaps place please possible possibly probably proof proven quite rather
    real really reason record reference related reliable report result results right run safe same say says score scores
    second see seems seen set several shall she should show signal since so some something sometimes soon still such
    sure suspicious take tell than that the their them then there therefore these they thing things this those though
    through thus to too treat true trust trusted trustworthy untrustworthy try two uncalibrated under unknown unless unlikely unreliable unsafe
    until untrusted unverified up upon us use used useful using usually valid validated value verified verify very
    via want was way we well were what when where whether which while who whole why will with within without would
    wrong yes yet you your always applied applies apply assessment beware cannot carefully cautiously certainly
    consult courtroom determined disclaimer doubt exactly false fake guaranteed hint indicative ineligible
    inadmissible manual meaning mistake only otherwise please probable proves prove prohibited purpose purposes
    question reasonable recommend refer regard reject rejected review risky screening should solely suggest
    tampered unconfirmed unsigned unvalidated warning whatever
""".split())
# Words that alone, as a word of their own, are an English verdict or
# disclaimer in Korean output ("결론: Uncalibrated — 참고용").
STANDALONE_ENGLISH_WORDS = frozenset("""
    uncalibrated unverified unreliable untrusted unvalidated unconfirmed unsigned inconclusive inadmissible
    ignore ignored likely unlikely probably possibly suspicious fake genuine tampered beware caution warning disclaimer
""".split())
# P13 (round 8): conclusion words. One of them alone in any case
# ("AUTHENTIC", "fake") or as a part of a code-shaped token next to another
# English word ("ProbablyFake", "FakeImageDetected", "probably_fake") is an
# English verdict in Korean output — the camelCase/snake split used to need
# three dictionary words, and ALL-CAPS "AUTHENTIC" passed as an acronym.
VERDICT_WORDS = frozenset("""
    fake real authentic synthetic detected generated manipulated deepfake genuine likely probably suspicious clean safe
""".split())
# Whole identifier tokens that contain dictionary-word parts (G9): the
# phase-0 terms printed as identifiers. The packaged runtime profile names
# are added at first use (profile_names()).
IDENTIFIER_ALLOWLIST = frozenset({
    "in-sample", "out-of-sample", "pre-screen", "fail-closed", "read-root", "allow-root", "key-file",
    # hyphenated deepfake-lens subcommands (cli.COMMANDS; test_error_text checks the list stays complete)
    "legal-report", "evidence-statement", "verify-report", "vendor-weights", "video-analysis", "text-advanced",
    "pixel-analysis", "ml-classify", "faceswap-seam", "train-neural-plan", "api-serve", "deepfake-lens",
})
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_DICT_SEPARATORS = re.compile("[\\s\\-–—/·\u300c\u300d\u300e\u300f\"'“”‘’()\\[\\]{}<>|,;:!?.…*+=~^`#&%]+")
_LETTERS_AND_DIGITS = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9@$]+$")
_HANGUL = re.compile("[\uac00-\ud7a3]")
_LATIN_RUN = re.compile(r"[A-Za-z0-9@$]+")
_HYPHENATED = re.compile(r"[A-Za-z0-9@$]+(?:-[A-Za-z0-9@$]+)+")
_DICT_IDENTIFIERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://\S+"),  # URLs
    # backticked code: a shell command, at most two tokens, or code punctuation
    re.compile(r"`((?:pip|python|deepfake-lens|experiments/)[^`]*|[^`\s]+(?: [^`\s]+)?|[^`]*[-_/.=<>:\[\]{}][^`]*)`"),
    re.compile("|".join(re.escape(name) for name in sorted(KNOWN_PROPER_NOUNS, key=len, reverse=True))),
    re.compile(r"<root>(?:[\\/][^\s'\"]*)?"),  # the scrubbed scan root and paths under it
    re.compile("(?<![\\w])(?:[A-Za-z]:[\\\\/]|/|~/)[^\\s'\"\u300c\u300d]*"),  # absolute paths
    # file names / paths ending in a file extension (archive members "a.zip::in/x.png" included)
    re.compile(
        r"[\w.\-~<>:/\\]*\.(?:py|js|json|md|pth|pt|onnx|torchscript|png|jpe?g|gif|webp|bmp|tiff?|heic|txt|wav|mp3|m4a|flac|ogg|"
        r"mp4|mov|mkv|avi|webm|zip|tar|gz|7z|rar|xml|html?|pdf|csv|docx|xlsx|pptx|hwpx?|doc|xls|ppt|log|bin|exe|so|dll|unpacked)\b"
        r"(?:::[\w.\-/]+)?",
        re.IGNORECASE,
    ),
    re.compile(r"\b[\w.]*[\w]-?[\w.-]*/[\w.-]+"),  # model ids org/name — only kept when a part has a digit or hyphen (checked below)
    # C2PA SDK validation codes (before the camelCase rule takes their first half)
    re.compile(r"\b(?:assertion|signingCredential|claimSignature|claim|manifest|timeStamp|general|algorithm|ingredient)(?:\.[A-Za-z]\w*)+"),
    re.compile(r"\b_?[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning)\b"),  # exception classes
    re.compile(r"(?<![\w-])--?[A-Za-z][\w-]*"),  # CLI flags
    re.compile(r"\b[\w.:\-]+=\S*"),  # key=value
    re.compile(r"\b\w+(?::[\w\-]+)+"),  # colon-joined ids (model:<name>)
)
# R9-6: the key=value and colon-joined patterns (read by the conclusion-word rule).
_PAIR_IDENTIFIERS = _DICT_IDENTIFIERS[-2:]
# Code-shaped identifiers, stripped after the sentence-identifier check (N12):
# snake_case / camelCase / PascalCase tokens are identifiers unless they spell
# a sentence (``doNotUseAsEvidence``, ``This_score_is_not_evidence``).
_DICT_CODE_IDENTIFIERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\w*_\w*\b"),  # snake_case identifiers
    re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),  # camelCase
    re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b"),  # PascalCase / product ids (EfficientNet)
    re.compile(r"\b[0-9a-fA-F]{8,}\b"),  # hex digests / ids
    re.compile(r"\b(?:" + "|".join(sorted(KNOWN_MODULE_NAMES)) + r")\b"),
    # dotted names inside this package (decision.decide, analysis_api.analyze_path)
    re.compile(
        r"\b(?:deepfake_lens\.)?(?:"
        + "|".join(sorted(path.stem for path in Path(__file__).resolve().parent.glob("*.py") if not path.stem.startswith("_")))
        + r")(?:\.\w+)+"
    ),
)
_PROFILE_NAMES: frozenset[str] | None = None


def profile_names() -> frozenset[str]:
    """Packaged runtime-profile names and file stems — identifiers, never prose (G9)."""
    global _PROFILE_NAMES
    if _PROFILE_NAMES is None:
        import json

        names: set[str] = set()
        for path in (Path(__file__).resolve().parent / "models").glob("*-runtime.json"):
            names.add(path.stem)
            names.add(path.stem.removesuffix("-runtime"))
            try:
                name = json.loads(path.read_text(encoding="utf-8")).get("name")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(name, str) and not re.search(r"\s", name):
                names.add(name)  # a name with spaces is not an identifier token
            # Latin name parts of the Korean display name (architecture,
            # vendor and dataset ids: Swin-large, umm-maybe, In-the-Wild).
            try:
                display = json.loads(path.read_text(encoding="utf-8")).get("display_name")
            except (OSError, ValueError, AttributeError):
                display = None
            if isinstance(display, str):
                names.update(re.findall(r"[A-Za-z0-9][A-Za-z0-9.]*(?:-[A-Za-z0-9.]+)+", display))
        names.add("WP-I")  # the phase-0 work package id
        _PROFILE_NAMES = frozenset(names)
    return _PROFILE_NAMES


_CHOICE_SET = re.compile(r"\{([\w-]+(?:,[\w-]+)+)\}")


def _strip_choice_set(match: re.Match[str]) -> str:
    """An argparse choice set ({build,split,verify}) is an identifier — unless it is words in braces."""
    members = match.group(1).split(",")
    return " " if sum(member.lower() in COMMON_ENGLISH_WORDS for member in members) < 2 else " ".join(members)


def _strip_allowlisted(text: str) -> str:
    """Remove whole identifier tokens: choice sets, IDENTIFIER_ALLOWLIST and the packaged profile names."""
    text = _CHOICE_SET.sub(_strip_choice_set, text)
    for token in sorted(IDENTIFIER_ALLOWLIST | profile_names(), key=len, reverse=True):
        if token in text:
            # N12: a Korean particle after the identifier ("verify-report로")
            # still ends it — only Latin letters, digits, "_" and "-" continue a token.
            text = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])", " ", text)
    return text


def _strip_dictionary_identifiers(line: str) -> str:
    return _strip_code_identifiers(_strip_word_identifiers(line))


def _strip_word_identifiers(line: str, *, keep_pairs: bool = False) -> str:
    """Allowlisted tokens, URLs, backticked code, names, paths, flags, key=value… (not code-shaped ids).

    ``keep_pairs`` (R9-6) keeps ``key=value`` and colon-joined tokens for the
    conclusion-word rule, which reads their parts.
    """
    line = _strip_allowlisted(line)
    for pattern in _DICT_IDENTIFIERS:
        if keep_pairs and pattern in _PAIR_IDENTIFIERS:
            continue
        if pattern.pattern.startswith(r"\b[\w.]*[\w]-?[\w.-]*/"):
            # A model id (org/name) only when a part carries a digit or a
            # hyphen — "not/for/court/use" is words, not an id.
            line = pattern.sub(lambda m: " " if re.search(r"[\d-]", m.group(0)) and m.group(0).count("/") == 1 else m.group(0), line)
            continue
        line = pattern.sub(" ", line)
    return line


def _strip_code_identifiers(line: str) -> str:
    for pattern in _DICT_CODE_IDENTIFIERS:
        line = pattern.sub(" ", line)
    return _ACRONYM.sub(lambda m: " " if m.group(0).rstrip("s") in KNOWN_ACRONYMS else m.group(0), line)


# N12: a snake_case / camelCase / PascalCase token that spells a sentence is
# prose in disguise. It is one when it splits into at least
# SENTENCE_ID_MIN_PARTS parts of which at least SENTENCE_ID_MIN_WORDS are
# common English words — or into SENTENCE_ID_MIN_WORDS common words one of
# which negates or instructs (not, should, must, ignore…). Ordinary
# identifiers (``verdict_code``, ``score_is_calibrated``, ``allow_symlinks``,
# ``trainedAlgorithmicMedia``) stay identifiers.
SENTENCE_ID_MIN_PARTS = 4
SENTENCE_ID_MIN_WORDS = 3
SENTENCE_ID_MARKERS = frozenset({"not", "never", "dont", "don", "cannot", "should", "must", "ignore", "ignored", "avoid"})
_CODE_TOKEN = re.compile(r"\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b|\b[a-z]+(?:[A-Z][a-z0-9]*)+\b|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*){2,}\b")
_CODE_PARTS = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


_SNAKE_TOKEN = re.compile(r"\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b")


# P13: code-shaped tokens read for conclusion words — snake_case in any case
# (lowercase included), camelCase and PascalCase with two or more humps.
_VERDICT_CODE_TOKEN = re.compile(r"\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b|\b[a-z]+(?:[A-Z][a-z0-9]*)+\b|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b|\b[A-Z]{2,}(?:[A-Z][a-z]+)+\b")


def _verdict_identifier_hit(line: str) -> str | None:
    """The parts of the first code-shaped token holding a conclusion word next to another English word (P13)."""
    vocabulary = COMMON_ENGLISH_WORDS | STANDALONE_ENGLISH_WORDS | VERDICT_WORDS
    for match in _VERDICT_CODE_TOKEN.finditer(line):
        if match.group(0).lower().startswith("deepfake_lens"):
            continue  # the package's own identifiers (DEEPFAKE_LENS_REPORT_KEY, deepfake_lens.cli)
        parts = [part.lower() for chunk in match.group(0).split("_") for part in _CODE_PARTS.findall(chunk)]
        verdicts = [part for part in parts if part in VERDICT_WORDS]
        others = [part for part in parts if part in vocabulary or part in IMAGE_WORDS]
        if verdicts and len(others) >= 2:
            return " ".join(parts)
    return None


# Nouns that turn a conclusion word into a sentence-shaped identifier
# ("FakeImageDetected", "real_photo") without being dictionary words.
IMAGE_WORDS = frozenset({"image", "photo", "picture", "video", "audio", "voice", "face", "text", "media", "content"})


def _sentence_identifier_hit(line: str) -> str | None:
    """The words of the first code-shaped token that spells an English sentence, or None (N12).

    Y11: a snake token with any capital letter is also read with its case
    normalized — ``proBABLY_fAKE`` is "probably fake": two dictionary words,
    one of them a verdict word, flag it (a lowercase identifier such as
    ``warning_threshold`` keeps the N12 rule).
    """
    for match in _SNAKE_TOKEN.finditer(line):
        token = match.group(0)
        if token == token.lower():
            continue
        chunks = [chunk.lower() for chunk in token.split("_") if chunk]
        words = [chunk for chunk in chunks if chunk in COMMON_ENGLISH_WORDS or chunk in STANDALONE_ENGLISH_WORDS]
        if len(words) >= 2 and any(word in STANDALONE_ENGLISH_WORDS for word in words):
            return " ".join(chunks)
    for match in _CODE_TOKEN.finditer(line):
        parts = [part.lower() for chunk in match.group(0).split("_") for part in _CODE_PARTS.findall(chunk)]
        words = [part for part in parts if part in COMMON_ENGLISH_WORDS or part in STANDALONE_ENGLISH_WORDS]
        if len(words) >= SENTENCE_ID_MIN_WORDS and (
            len(parts) >= SENTENCE_ID_MIN_PARTS or any(word in SENTENCE_ID_MARKERS for word in words)
        ):
            return " ".join(parts)
    return None


def _dictionary_words(line: str) -> tuple[list[str], list[str]]:
    """(dictionary words, standalone verdict words) of one line, distinct, in order."""
    found: list[str] = []
    standalone: list[str] = []

    def note(word: str) -> None:
        if word in COMMON_ENGLISH_WORDS and word not in found:
            found.append(word)
        if word in STANDALONE_ENGLISH_WORDS and word not in standalone:
            standalone.append(word)

    for raw in line.split():
        whole = raw.strip("\"'“”‘’()[]{}<>|,;:!?.…*\u300c\u300d\u300e\u300f").lower()
        if (whole in STANDALONE_ENGLISH_WORDS or whole in VERDICT_WORDS) and whole not in standalone:
            standalone.append(whole)  # P13: a conclusion word alone, any case
        # N12: an English word with a Korean particle glued on
        # ("trustworthy하지", "evidence로", "inadmissible입니다") is the word.
        if _HANGUL.search(raw):
            for run in _LATIN_RUN.findall(raw):
                if _LETTERS_AND_DIGITS.match(run):
                    note(run.translate(_LEET).lower() if re.search(r"\d", run) else run.lower())
        # N12: hyphens inside a word ("non-cal-ib-rat-ed", "ig-nore") — the
        # joined letters are looked up too, with a "non"/"un" prefix dropped.
        for hyphenated in _HYPHENATED.findall(raw):
            joined = hyphenated.replace("-", "").translate(_LEET).lower()
            for candidate in (joined, joined.removeprefix("non"), joined.removeprefix("un")):
                if candidate:
                    note(candidate)
        parts = [part for part in _DICT_SEPARATORS.split(raw) if part]
        if any(re.search(r"\d", part) and re.search(r"[A-Za-z]", part) for part in parts) and "-" in raw and len(parts) > 1:
            # A hyphenated token with a digit part is a version id (layer-thresholds-v1).
            continue
        for part in parts:
            if not _LETTERS_AND_DIGITS.match(part):
                continue
            word = part.translate(_LEET).lower() if re.search(r"\d", part) else part.lower()
            if word in COMMON_ENGLISH_WORDS and word not in found:
                found.append(word)
    return found, standalone


# Y11: words glued together without spaces ("probablyfakeimage") are split
# by greedy longest match against the dictionary; a run of at least
# GLUED_MIN_RUN letters that yields GLUED_MIN_WORDS words of at least
# GLUED_MIN_WORD letters — one of at least GLUED_MIN_LONG letters — covering
# GLUED_MIN_COVER of the run is prose. The floors keep ordinary compound
# identifiers ("dataset" = data+set, "checkpoint") out.
GLUED_MIN_RUN = 7
GLUED_MIN_WORDS = 2
GLUED_MIN_WORD = 3
GLUED_MIN_LONG = 5
GLUED_MIN_COVER = 0.6
_GLUED_RUN = re.compile(r"[A-Za-z]{%d,}" % GLUED_MIN_RUN)


def _glued_words(run: str) -> list[str]:
    """Dictionary words found in ``run`` by greedy longest match, left to right (Y11)."""
    vocabulary = COMMON_ENGLISH_WORDS | STANDALONE_ENGLISH_WORDS
    lowered = run.lower()
    found: list[str] = []
    index = 0
    while index < len(lowered):
        for end in range(len(lowered), index + GLUED_MIN_WORD - 1, -1):
            if lowered[index:end] in vocabulary:
                found.append(lowered[index:end])
                index = end
                break
        else:
            index += 1
    return found


def _glued_hit(line: str) -> str | None:
    vocabulary = COMMON_ENGLISH_WORDS | STANDALONE_ENGLISH_WORDS
    for match in _GLUED_RUN.finditer(line):
        run = match.group(0)
        if run.lower() in vocabulary:
            continue
        words = _glued_words(run)
        if (
            len(words) >= GLUED_MIN_WORDS
            and max(len(word) for word in words) >= GLUED_MIN_LONG
            and sum(len(word) for word in words) >= GLUED_MIN_COVER * len(run)
        ):
            return " ".join(words)
    return None


# R9-6 (round 9): conclusion words that passed the detector — "verdict=fake",
# "result:fake", "결과=fake", "#fake" (key=value, colon and hash tokens were
# identifiers), "fakes"/"faked" (inflected), "authentic입니다" (a Korean
# ending on a conclusion word), "fake-image", "deep-fake", "AI-generated",
# "AIGenerated", "ai_generated" (compounds). Tokens are split on "=", ":"
# and "#"; each piece, each Latin run glued to Hangul and each compound part
# is looked up with English inflections removed (s/es/ed/d/ing).
_PAIR_SEPARATORS = re.compile(r"[=:#]+")
_TOKEN_EDGE = "\"'“”‘’()[]{}<>|,;!?.…*\u300c\u300d\u300e\u300f"
# Parts that make a compound with a conclusion word a phrase ("ai_generated",
# "deep-fake") without being dictionary words.
COMPOUND_PARTS = frozenset({"ai", "deep", "not", "very", "highly", "most"})


def conclusion_word(word: str) -> str | None:
    """The conclusion/disclaimer word ``word`` spells, its inflection removed (R9-6), or None."""
    word = word.lower()
    vocabulary = VERDICT_WORDS | STANDALONE_ENGLISH_WORDS
    candidates = [word]
    if word.endswith("s"):
        candidates += [word[:-1], word[:-2] if word.endswith("es") else ""]
    if word.endswith("ed"):
        candidates += [word[:-2], word[:-1]]
    if word.endswith("ing"):
        candidates += [word[:-3], word[:-3] + "e"]
    return next((candidate for candidate in candidates if len(candidate) >= 3 and candidate in vocabulary), None)


def _compound_parts(token: str) -> list[str]:
    """Lowercase parts of a hyphen/underscore/camel compound ("AIGenerated" -> ["ai", "generated"])."""
    return [part.lower() for chunk in re.split(r"[-_]+", token) for part in _CODE_PARTS.findall(chunk)]


def _verdict_compound_hit(token: str) -> str | None:
    """A compound holding a conclusion word next to another word ("fake-image", "deep-fake", "ai_generated")."""
    if token.lower().startswith(("deepfake_lens", "deepfake-lens")):
        return None  # the package's own identifiers
    parts = _compound_parts(token)
    if len(parts) < 2:
        return None
    vocabulary = COMMON_ENGLISH_WORDS | STANDALONE_ENGLISH_WORDS | VERDICT_WORDS | IMAGE_WORDS | COMPOUND_PARTS
    verdicts = [part for part in parts if conclusion_word(part)]
    others = [part for part in parts if part in vocabulary or conclusion_word(part)]
    if verdicts and len(others) >= 2:
        return " ".join(parts)
    return None


# A Latin token glued directly to Hangul, identifiers included
# ("authentic입니다", "DEEPFAKE_LENS_REPORT_KEY로"; not "폴더(ai/real").
_LATIN_TOKEN = re.compile(
    "(?<=[\uac00-\ud7a3])[A-Za-z0-9@$]+(?:[-_][A-Za-z0-9@$]+)*|[A-Za-z0-9@$]+(?:[-_][A-Za-z0-9@$]+)*(?=[\uac00-\ud7a3])"
)
# "<name> (<name>-runtime.json)": a token that names a file on the same line
# (a model profile in doctor/models output) is that file's identifier.
_FILE_STEM = re.compile(r"([A-Za-z0-9][\w.-]*?)(?:-runtime)?\.(?:json|onnx|pt|pth|safetensors|bin|torchscript)\b")


def _conclusion_token_hit(line: str, *, file_names: str = "") -> str | None:
    """A conclusion word as a token piece (split on "=", ":", "#"), inflected, glued to Hangul or in a compound (R9-6).

    ``file_names`` is the line before identifier stripping: a token that is
    the stem of a file name on it is an identifier.
    """
    stems = {match.group(1).lower() for match in _FILE_STEM.finditer(file_names)}
    for raw in line.split():
        for piece in _PAIR_SEPARATORS.split(raw):
            whole = piece.strip(_TOKEN_EDGE)
            if not whole:
                continue
            if _LETTERS_AND_DIGITS.match(whole) and not re.search(r"\d", whole):
                if conclusion_word(whole) and whole.lower() not in stems:
                    return whole.lower()
            if _HANGUL.search(whole):
                for run in _LATIN_TOKEN.findall(whole):
                    if conclusion_word(run):
                        return run.lower()
            for compound in re.findall(r"[A-Za-z]+(?:[-_][A-Za-z]+)*", whole):
                hit = None if compound.lower() in stems else _verdict_compound_hit(compound)
                if hit:
                    return hit
    return None


def english_dictionary_hit(text: str) -> str | None:
    """The offending words when a line holds two common English words (or a verdict word), else None (G9).

    Y11: the text is normalized first (:func:`normalize_for_detection`) and
    words glued without spaces count (:func:`_glued_words`).
    """
    text = normalize_for_detection(text)
    for line in text.splitlines() or [text]:
        words_only = _strip_word_identifiers(line)
        sentence = _sentence_identifier_hit(words_only) or _verdict_identifier_hit(words_only)
        if sentence:
            return sentence
        conclusion = _conclusion_token_hit(_strip_word_identifiers(line, keep_pairs=True), file_names=line)
        if conclusion:
            return conclusion
        stripped = _strip_code_identifiers(words_only)
        glued = _glued_hit(stripped)
        if glued:
            return glued
        words, standalone = _dictionary_words(stripped)
        if len(words) >= 2:
            return " ".join(words)
        if standalone:
            return standalone[0]
    return None


# Y11 (round 7): spellings that slipped past the detector. Text is
# normalized before both rules: NFKC (fullwidth "ｕｎｒｅｌｉａｂｌｅ" ->
# "unreliable"), invisible characters dropped (soft hyphen, zero-width
# space/joiners, word joiner, BOM) and Cyrillic/Greek letters that look like
# Latin ones mapped to them ("Thе rеsult is fаkе" with Cyrillic е/а).
_INVISIBLE_CHARS = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"), None)
_HOMOGLYPHS = str.maketrans({
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t",
    "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S", "Ԁ": "D", "Һ": "H", "Ӏ": "I", "Ԛ": "Q", "Ԝ": "W",
    # Greek
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
})


# R10-7 (round 10): "【fake】" and "verdict→fake" passed the detector — CJK
# brackets and arrows glued the conclusion word to its neighbours.
# R11-5 (round 11): so did every other symbol and punctuation mark
# ("fake✓", "▶fake◀", "판정★real", "✔real", "fake⚠", "결론●real") and a CJK
# ideograph suffix ("fake的"). Every non-ASCII character of a Unicode
# symbol (S*) or punctuation (P*) category, every CJK ideograph and every
# kana is now a token boundary (read as a space). ASCII punctuation keeps
# its meaning for the identifier rules (snake_case "_", key=value "=",
# paths "/", flags "-", "model:<name>" …). Kept as they are: 「」
# (U+300C/300D — they quote metadata copied verbatim from the evidence file,
# which the identifier rules strip as one unit) and the typographic
# apostrophes ‘ ’ (U+2018/2019 — "Don’t" is one word, as "Don't").
_BOUNDARY_KEEP = frozenset("\u300c\u300d\u2018\u2019")
_CJK_LETTERS = re.compile(
    "[\u2e80-\u2fdf\u3005-\u3007\u3021-\u3029\u3038-\u303b\u3040-\u30ff\u31f0-\u31ff"
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]"
)


def _is_token_boundary(char: str) -> bool:
    """A non-ASCII symbol/punctuation character, CJK ideograph or kana (R10-7, R11-5)."""
    if char < "\x80" or char in _BOUNDARY_KEEP:
        return False
    return unicodedata.category(char)[0] in "SP" or _CJK_LETTERS.match(char) is not None


def _token_boundaries(text: str) -> str:
    """``text`` with every token-boundary character (:func:`_is_token_boundary`) as a space
    and every combining mark (Mn, Me) dropped — a spacing accent such as "´" is a space
    plus a combining mark after NFKC, and a mark glued to a word ("fa\u0331ke") hid it (R11-5)."""
    if text.isascii():
        return text
    out: list[str] = []
    for char in text:
        if _is_token_boundary(char):
            out.append(" ")
        elif char >= "\u0300" and unicodedata.category(char) in ("Mn", "Me"):
            continue
        else:
            out.append(char)
    return "".join(out)


def _spells_letters(char: str) -> bool:
    """A symbol/punctuation character whose NFKC form holds letters or digits ("™" -> "TM", "㎏" -> "kg")."""
    if char < "\x80" or unicodedata.category(char)[0] not in "SP":
        return False
    return any(part.isalnum() for part in unicodedata.normalize("NFKC", char))


def normalize_for_detection(text: str) -> str:
    """``text`` as the English detector reads it (Y11): NFKC, no invisible characters, no Latin
    look-alikes, non-ASCII symbols/punctuation and CJK ideographs as spaces (R10-7, R11-5).

    A symbol that NFKC would spell as letters ("fake™" -> "fakeTM", one
    camelCase identifier) is a boundary before NFKC as well (R11-5).
    R12-12 (round 12): every format character (category Cf — TAG characters
    U+E0000–U+E007F, invisible operators, bidi marks, Mongolian vowel
    separator, interlinear annotation marks, …) is dropped first; only the
    soft hyphen, zero-width space/joiners, word joiner and BOM were, so
    "fa<TAG a>ke" or "<TAG>fake" hid the word.
    """
    if not text.isascii():
        text = "".join(char for char in text if unicodedata.category(char) != "Cf")
        text = "".join(" " if _spells_letters(char) else char for char in text)
    normalized = unicodedata.normalize("NFKC", text).translate(_INVISIBLE_CHARS).translate(_HOMOGLYPHS)
    return _token_boundaries(normalized)


def english_prose(text: str) -> str | None:
    """English in ``text``, or None (S8, strengthened in round 5 / G9, round 7 / Y11).

    The text is first normalized (:func:`normalize_for_detection`: NFKC,
    invisible characters, Cyrillic/Greek look-alikes). Two rules, either flags:

    * run rule (round 4): after identifier tokens are removed, a sentence
      (split on ``.``, ``!``, ``?``, ``;`` and newlines) holds two consecutive
      English words; a contraction (``Don't``) is one word;
    * dictionary rule (round 5): a line holds two distinct common English
      words anywhere — split on ``-`` ``/`` ``·``, corner brackets and punctuation,
      digits read as letters — or one English verdict/disclaimer word
      (:func:`english_dictionary_hit`).

    The dictionary rule also reads a code-shaped token in any letter case
    (``proBABLY_fAKE``) and a run of words glued together without spaces
    (``probablyfakeimage``, Y11).

    Hangul elsewhere in the text exempts nothing.
    """
    text = normalize_for_detection(text)
    stripped = _strip_allowlisted(text)
    for pattern in _IDENTIFIER_TOKENS:
        stripped = pattern.sub(" ", stripped)
    stripped = _ACRONYM.sub(lambda m: " " if m.group(0).rstrip("s") in KNOWN_ACRONYMS else m.group(0), stripped)
    for sentence in _SENTENCE_END.split(stripped):
        match = _EN_RUN.search(sentence)
        if match:
            return match.group(0)
    return english_dictionary_hit(text)


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


def json_error_ko(exc: ValueError) -> str:
    """Korean text of a JSON parse error — position as numbers only (P9)."""
    return korean_exception_message(exc)


def decode_error_ko(exc: UnicodeDecodeError) -> str:
    """Korean text of a text-decoding error (P4): the byte offset, no codec prose."""
    return f"{exc.encoding.upper() if exc.encoding else '텍스트'} 텍스트가 아닙니다(바이트 위치 {exc.start})"


# R10-5 (round 10): a JSON input nested deeper than Python's recursion limit
# (json.loads raises RecursionError) is an unreadable input, not an internal error.
JSON_TOO_DEEP_KO = "JSON 중첩이 너무 깊습니다(파이썬 재귀 한도 초과)"


def read_error_ko(exc: BaseException) -> str:
    """Korean reason a file input could not be read or parsed (P4/P9)."""
    import json

    if isinstance(exc, UnicodeDecodeError):
        return decode_error_ko(exc)
    if isinstance(exc, json.JSONDecodeError):
        return json_error_ko(exc)
    if isinstance(exc, RecursionError):
        # R10-5: "[[[[…]]]]" nested past Python's recursion limit.
        return JSON_TOO_DEEP_KO
    if isinstance(exc, OSError) and exc.errno in _ERRNO_KO:
        # The CLI names the file itself; "[Errno 2]" is not repeated (P9).
        return f"{_ERRNO_KO[exc.errno]}(오류 번호 {exc.errno})"
    return korean_exception_message(exc)
