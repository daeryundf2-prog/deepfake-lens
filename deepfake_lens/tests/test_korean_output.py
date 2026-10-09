"""R4: user-facing result text is Korean.

Phase 0 added English sentences to fields the examiner reads — profile
``limitations``/``notes`` (shown verbatim in model_analysis and result
limitations), model adapter details and coverage reasons, CLI warnings,
library exception messages. These tests walk real scan JSON and the
packaged profiles and fail on any examiner-facing string (``limitations``,
``reason``, ``reasons``, ``verdict``, ``detail``, ``title``, ``label``,
``error``, ``display_name``, ``notes``, ``reference_note``, ``next_checks``,
the ``*_label`` fields, ``warnings``, ``message``, the pixel layer's
``signals`` and ``evidence_chain``) that contains an English sentence.
Identifier fields (``model``, ``check``, ``profile``, ``path``) and the pixel
experts' bibliographic ``reference`` (paper titles, cited as published) are
not prose and are not checked; metadata values copied from the file
(``document_metadata``, ``docx.application`` …) are evidence, shown as is.

The heuristic (round 3): every string is split into sentences (on ``.``,
``;`` and newlines) and each sentence is checked on its own — a string
with Hangul in it may still carry an English sentence, so Hangul exempts
nothing. A sentence fails when, after the allowed tokens are removed, it
still has three consecutive ASCII words (two or more letters, or the
one-letter words ``a``/``I``), or three consecutive ALL-CAPS words. Allowed
tokens are a tight list of things that legitimately stay in English, and
none of them can carry a sentence:

- identifiers — ``snake_case``, ``camelCase``/``PascalCase`` with an inner
  capital, ALL-CAPS acronyms (never an English function word such as NOT,
  AND, THE, USE), tokens containing a digit, hyphenated compounds
  (``roberta-base``), ``key=value``, ``--cli-flags``;
- `` `code` `` spans that look like code: a shell command (``pip …``,
  ``python …``, ``deepfake-lens …``), at most two words, or containing
  code punctuation — a backticked English sentence is still checked;
- URLs and model ids / paths (anything with a ``/``);
- exception class names (``…Error``/``…Exception``/``…Warning``);
- file names (``name.ext``), ``sha256``, ``pin``;
- values copied verbatim from the evidence file's metadata, which the tool
  always prints inside ``「…」`` — and only there: the package source may
  open ``「`` solely around an interpolated value (``「{value}」``), never
  around literal text (SourceQuotesTest).

The mixed fixture covers images with camera EXIF / generator EXIF / XMP
DigitalSourceType / Photoshop CreatorTool / A1111 and NovelAI PNG text,
non-photos, unreadable files (empty, fake extension, truncated, fake mp3,
garbage wav, broken docx/zip), docx with ChatGPT and Word application
metadata, a PDF, wav, mp4 (when ffmpeg is installed), an archive with a
generated member and hostile members, and in-folder and out-of-folder
symlinks, scanned with and without the deep layers.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Iterator

from deepfake_lens.analysis_api import AnalysisOptions, scan_folder, scan_payload

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "deepfake_lens" / "models"
CHECKED_KEYS = frozenset({
    "limitations", "reason", "reasons", "verdict", "detail", "title", "label", "error", "display_name",
    "notes", "reference_note", "next_checks", "verdict_label", "grade_label", "band_label",
    "source_attribution_label", "warnings", "message", "signals", "evidence_chain",
})

SENTENCE_SPLIT = re.compile(r"[.;\n]+")
# Three consecutive ASCII words of >= 2 letters, or the articles a / I
# (spaces or light punctuation between).
_WORD = r"\b(?:[A-Za-z]{2,}|[aAI])\b"
_GAP = r"[ ,:'\"()\[\]\-–—]+"
ENGLISH_PROSE = re.compile(rf"{_WORD}(?:{_GAP}{_WORD}){{2,}}")
# Three consecutive ALL-CAPS words: shouted prose, checked before acronyms are removed.
ALL_CAPS_PROSE = re.compile(rf"\b[A-Z]{{2,}}\b(?:{_GAP}\b[A-Z]{{2,}}\b){{2,}}")
# English function words that are never an acronym, even in capitals.
_FUNCTION_WORDS = (
    "A|AN|AND|ARE|AS|AT|BE|BUT|BY|DO|FOR|FROM|HAS|IF|IN|IS|IT|NO|NOT|OF|ON|OR|SO|THE|THIS|TO|USE|WAS|WITH"
)
# A backticked span counts as code only when it looks like code.
_CODE_SPAN = re.compile(r"`((?:pip|python|deepfake-lens|experiments/)[^`]*|[^`\s]+(?: [^`\s]+)?|[^`]*[-_/.=<>:\[\]{}][^`]*)`")
_QUOTED = (
    re.compile(r"「[^」]*」"),  # metadata values copied verbatim from the evidence file
    re.compile(r"https?://\S+"),  # URLs
    _CODE_SPAN,  # code / parameter names / shell commands
)
ALLOWED_TOKENS = (
    *_QUOTED,
    re.compile(r"[\w.\-]+/[\w.\-/]+"),  # model ids and paths: org/name, models/x.json, <root>/a
    re.compile(
        r"\b[\w\-]+\.(?:py|json|md|pth|pt|onnx|torchscript|png|jpe?g|gif|webp|bmp|tiff?|heic|txt|wav|mp3|m4a|flac|ogg|"
        r"mp4|mov|mkv|avi|webm|zip|tar|gz|7z|rar|xml|html|pdf|csv|docx|xlsx|pptx|hwpx?|doc|xls|ppt|log)\b",
        re.IGNORECASE,
    ),  # file names
    re.compile(r"\b_?[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning)\b"),  # exception classes
    re.compile(r"--[a-z][\w-]*"),  # CLI flags
    re.compile(r"\b\w+=\S+"),  # key=value
    re.compile(r"\b\w*_\w*\b"),  # snake_case identifiers
    re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),  # camelCase identifiers
    re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b"),  # PascalCase identifiers / product ids (EfficientNet)
    re.compile(rf"\b(?!(?:{_FUNCTION_WORDS})\b)[A-Z0-9]{{2,}}s?\b"),  # acronyms (EXIF, JPEG, C2PA, AUROC)
    re.compile(r"\b\w*\d\w*\b"),  # tokens with a digit (B0, v2, Wav2Vec2)
    re.compile(r"\b[A-Za-z]+(?:-[A-Za-z0-9]+)+\b"),  # hyphenated identifiers (roberta-base)
    re.compile(r"\b(?:sha256|pin)\b"),
)

# R4: examples of English that reached the examiner in round 3 — the
# heuristic must flag every one of them.
VERIFIER_EXAMPLES = (
    "모델 실행 불가: Local EfficientNet-B0 trained on SD-Turbo fakes vs Hemg reals: 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
    "SBI EfficientNet-B0 on per-frame face crops (video-frames runtime): 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
    "Wav2Vec2-XLSR deepfake audio classifier (Gustking, In-the-Wild): 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
    "UnidentifiedImageError: cannot identify image file '/tmp/tmpzd7syld_/evidence/empty.png'",
)

# Round-3 audit: English sentences the round-2 heuristic let through
# (Hangul elsewhere in the string, two-letter words, articles) — each
# must be flagged.
AUDIT_NEGATIVES = (
    "참고: Scores are a prioritization signal, not a truth label.",
    "Do not use as evidence in court.",
    "It is a signal, not a label; do not use as evidence.",
    "Measured locally on 4 DALL-E samples: caught 1 of 4, missed 3.",
)


def english_prose(text: str) -> str | None:
    """The first English run in any sentence of ``text``, or None (R4 heuristic)."""
    unquoted = text
    for pattern in _QUOTED:
        unquoted = pattern.sub(" ", unquoted)
    shouted = ALL_CAPS_PROSE.search(unquoted)
    if shouted:
        return shouted.group(0)
    stripped = text
    for pattern in ALLOWED_TOKENS:
        stripped = pattern.sub(" ", stripped)
    for sentence in SENTENCE_SPLIT.split(stripped):
        match = ENGLISH_PROSE.search(sentence)
        if match:
            return match.group(0)
    return None


def checked_strings(node: Any, path: str = "", key: str | None = None) -> Iterator[tuple[str, str]]:
    """(json path, string) for every string under a checked key (lists included)."""
    if isinstance(node, dict):
        for child_key, value in node.items():
            yield from checked_strings(value, f"{path}/{child_key}", child_key)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from checked_strings(item, f"{path}[{index}]", key)
    elif isinstance(node, str) and key in CHECKED_KEYS:
        yield path, node


def _photo(width: int, height: int, seed: int) -> Any:
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    base = np.stack([
        120 + 50 * np.sin(xx / (30 + seed) + yy / 90.0),
        100 + 45 * np.cos(xx / 47.0 - yy / (33 + seed)),
        80 + 35 * np.sin((xx * yy) / 9000.0 + seed),
    ], -1)
    return Image.fromarray(np.clip(base + rng.normal(0, 7, (height, width, 3)), 0, 255).astype(np.uint8))


def _xmp(attribute: str) -> bytes:
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
        'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        f'{attribute}/></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    ).encode("utf-8")


def _docx(application: str, creator: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>계약서 초안입니다. 갑과 을은 다음과 같이 합의한다.</w:t></w:r></w:p></w:body></w:document>')
        archive.writestr("docProps/app.xml", f'<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>{application}</Application></Properties>')
        archive.writestr("docProps/core.xml", f'<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>{creator}</dc:creator></cp:coreProperties>')
    return buffer.getvalue()


def write_mixed_folder(folder: Path) -> Path:
    """The R4 fixture (see module docstring). Needs numpy + Pillow."""
    import stat

    from PIL import Image, PngImagePlugin

    from deepfake_lens.tests.qa.samples import write_samples

    folder.mkdir(parents=True, exist_ok=True)
    samples = folder / "formats"
    write_samples(samples)  # one sample per supported extension (mp4 etc. via ffmpeg when installed)

    def save(name: str, image: Any, **kwargs: Any) -> None:
        image.save(folder / name, **kwargs)

    for index, (make, model) in enumerate((("Canon", "Canon EOS R5"), ("Apple", "iPhone 15 Pro"))):
        exif = Image.Exif()
        exif[0x010F], exif[0x0110], exif[0x0132] = make, model, f"2025:03:0{index + 1} 10:11:12"
        ifd = exif.get_ifd(0x8769)
        ifd[0x9003], ifd[0x829A], ifd[0x8827] = f"2025:03:0{index + 1} 10:11:12", (1, 125), 200
        save(f"exif_{make.lower()}.jpg", _photo(600, 400, 20 + index), quality=93, exif=exif.tobytes())
    for name, software in (("exif_firefly.jpg", "Adobe Firefly"), ("exif_midjourney.jpg", "Midjourney")):
        exif = Image.Exif()
        exif[0x0131] = software
        save(name, _photo(300, 300, 31), exif=exif.tobytes())
    save("xmp_ai.jpg", _photo(300, 300, 33), xmp=_xmp('Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"'))
    save("xmp_photoshop.jpg", _photo(300, 300, 34), xmp=_xmp('xmp:CreatorTool="Adobe Photoshop 25.0"'))
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a cat astronaut\nNegative prompt: blurry\nSteps: 28, Sampler: DPM++ 2M Karras, CFG scale: 6.5, Seed: 1234567, Size: 256x256, Model: sdxl")
    save("a1111.png", _photo(256, 256, 35), pnginfo=info)
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "NovelAI")
    save("novelai.png", _photo(256, 256, 36), pnginfo=info)
    import numpy as np

    Image.fromarray(np.tile(np.linspace(0, 255, 512, dtype=np.uint8), (512, 1))).save(folder / "gradient.png")
    save("tiny.png", _photo(60, 60, 37))
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "NIKON CORPORATION", "NIKON Z 6"
    _photo(600, 400, 38).save(buffer, format="JPEG", quality=92, exif=exif.tobytes())
    (folder / "truncated_nikon.jpg").write_bytes(buffer.getvalue()[: len(buffer.getvalue()) // 3])
    (folder / "empty.jpg").write_bytes(b"")
    (folder / "fake_ext.jpg").write_bytes(b"this is not a jpeg at all\n" * 20)
    (folder / "fake.gif").write_bytes(b"not a gif")
    (folder / "fake.mp3").write_bytes(bytes((i * 37 + 11) % 256 for i in range(3000)))
    (folder / "garbage.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunkjunk")
    (folder / "broken.docx").write_bytes(b"PK\x03\x04 not really a zip")
    (folder / "essay_en.txt").write_text("As an AI language model, it is important to note that, furthermore, in conclusion. " * 6, encoding="utf-8")
    (folder / "essay_ko.txt").write_text("나는 어제 도서관에서 언어 모델에 관한 책을 읽었다. 친구와 떡볶이를 먹었다.", encoding="utf-8")
    (folder / "contract_chatgpt.docx").write_bytes(_docx("ChatGPT", "OpenAI"))
    (folder / "contract_word.docx").write_bytes(_docx("Microsoft Office Word", "김변호사"))
    a1111 = (folder / "a1111.png").read_bytes()
    with zipfile.ZipFile(folder / "bundle.zip", "w") as archive:
        archive.writestr("in/a1111_inner.png", a1111)
        archive.writestr("../escape.txt", "traversal member")
        archive.writestr("/abs/root.txt", "absolute member")
        link = zipfile.ZipInfo("link_member")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/etc/passwd")
    if hasattr(os, "symlink"):
        try:
            (folder / "link.jpg").symlink_to("exif_canon.jpg")
            outside = folder.parent / "outside.txt"
            outside.write_text("폴더 밖 파일", encoding="utf-8")
            (folder / "outside_link.txt").symlink_to(outside)
        except OSError:
            pass
    return folder


def _wav_bytes() -> bytes:
    import math
    import struct
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 330 * i / 16000))) for i in range(16000)))
    return buffer.getvalue()


def _have_ffmpeg() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


def _have(*modules: str) -> bool:
    import importlib.util

    return all(importlib.util.find_spec(module) is not None for module in modules)


class EnglishProseHeuristicTest(unittest.TestCase):
    """The detector itself: flags prose, passes Korean and allowed tokens."""

    def test_flags_english_sentences(self) -> None:
        for text in (
            "Scores are a prioritization signal, not a truth label.",
            "note: report written unsigned (no key; set DEEPFAKE_LENS_REPORT_KEY or --key-file)",
            "pymupdf is required for PDF evidence statements; install it",
            "image file is truncated (37 bytes not processed)",
            "결과입니다. The file is not a regular file.",  # Hangul elsewhere exempts nothing
            "오디오: File does not exist or is not a regular file",
            "It is a signal.",  # articles count as words
            "DO NOT USE AS EVIDENCE.",  # capitals are not acronyms
            "증거로 쓰지 마십시오(NOT FOR COURT USE).",
            "It is NOT evidence.",
            "결론: `do not use this as evidence`",  # a backticked sentence is not code
            "Warning: this is unverified",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(english_prose(text))

    def test_round3_audit_negatives_fail(self) -> None:
        for text in AUDIT_NEGATIVES:
            with self.subTest(text=text):
                self.assertIsNotNone(english_prose(text))

    def test_the_verifiers_examples_fail(self) -> None:
        for text in VERIFIER_EXAMPLES:
            with self.subTest(text=text[:60]):
                self.assertIsNotNone(english_prose(text))

    def test_allows_korean_and_identifiers(self) -> None:
        for text in (
            "점수는 보정 전 원점수이며 진위 판정이 아닙니다.",
            "AnalyzerError: C2PA 판독 실패: _C2paVerify: Verify: 잘못된 블록 ID 101",
            "fakespot-ai/roberta-base-ai-text-detection-v1",
            "https://huggingface.co/umm-maybe/AI-image-detector",
            "experiments/RECOMPRESSION_EVAL.md",
            "RuntimeError",
            "pin sha256",
            "작성 애플리케이션: 「Microsoft Office Word」",
            "프롬프트와 `Steps`, `Sampler`, `CFG scale`, `Seed` 같은 A1111 생성 파라미터가 발견되었습니다.",
            "digitalSourceType=trainedAlgorithmicMedia를 선언합니다",
            "SD-Turbo 생성 이미지 탐지기(로컬 EfficientNet-B0)",
            "opencv가 설치되어 있지 않습니다. `pip install opencv-python`로 설치하세요.",
            "numpy가 설치되어 있지 않습니다. `pip install numpy`로 설치하세요.",
            "EXIF·XMP·C2PA 메타데이터가 없습니다.",
            "AUROC 95% CI 하한 0.85 미충족",
        ):
            with self.subTest(text=text):
                self.assertIsNone(english_prose(text))


def _scan_offenders(test: unittest.TestCase, folder: Path, options: AnalysisOptions) -> list[str]:
    summary, items, thresholds = scan_folder(folder, options)
    test.assertGreater(len(items), 0, folder)
    payload = json.loads(json.dumps(scan_payload(summary, items, thresholds, options), ensure_ascii=False))
    offenders = []
    for where, text in checked_strings(payload):
        run = english_prose(text)
        if run:
            offenders.append(f"{folder.name}{where}: {run!r} in {text[:160]!r}")
    return offenders


class ScanOutputIsKoreanTest(unittest.TestCase):
    """R4: no English prose in examiner-facing fields of real scan JSON."""

    def test_scan_json_text_fields_are_korean(self) -> None:
        offenders: list[str] = []
        for folder in (REPO_ROOT / "fixtures" / "benchmark", REPO_ROOT / "experiments" / "text-corpus"):
            offenders.extend(_scan_offenders(self, folder, AnalysisOptions(recursive=True)))
        self.assertEqual(offenders, [], "\n".join(sorted(set(offenders))[:40]))

    @unittest.skipUnless(_have("numpy", "PIL"), "numpy + Pillow needed for the mixed fixture")
    def test_mixed_folder_scan_is_korean(self) -> None:
        offenders: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_mixed_folder(Path(tmp).resolve() / "mixed")
            # The fixture really spans the media kinds: audio, video (when
            # ffmpeg made one), documents (docx, pdf) and archives.
            summary, items, _ = scan_folder(folder, AnalysisOptions(recursive=True))
            kinds = {(item.kind, Path(item.path.split("::")[0]).suffix.lower()) for item in items}
            for expected in (("audio", ".wav"), ("text", ".docx"), ("text", ".pdf"), ("archive", ".zip")):
                self.assertIn(expected, kinds)
            if _have_ffmpeg():
                self.assertIn(("video", ".mp4"), kinds)
            for options in (
                AnalysisOptions(recursive=True),
                AnalysisOptions(recursive=True, deep_signals=True, pixel_mode="deep", dedupe=True),
            ):
                offenders.extend(_scan_offenders(self, folder, options))
        self.assertEqual(offenders, [], "\n".join(sorted(set(offenders))[:60]))

    def test_packaged_profiles_limitations_and_notes_are_korean(self) -> None:
        """Profile limitations/notes/reason/display_name reach users verbatim."""
        offenders: list[str] = []
        for profile_path in sorted(MODELS_DIR.glob("*-runtime.json")):
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertTrue(str(profile.get("display_name") or "").strip(), f"{profile_path.name}: no display_name")
            for key in ("limitations", "notes", "reason", "display_name"):
                values = profile.get(key) or []
                for text in values if isinstance(values, list) else [values]:
                    run = english_prose(str(text))
                    if run:
                        offenders.append(f"{profile_path.name}:{key}: {run!r}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_profiles_carry_no_unverified_recall_or_fpr_figures(self) -> None:
        """R4: unverified recall/FPR/AUROC figures live in experiments/*.md under the 미검증 banner, not in profiles."""
        figure = re.compile(r"(?i)\b(?:recall|fpr|auroc|eer)\b[^.;]*?\d")
        for profile_path in sorted(MODELS_DIR.glob("*-runtime.json")):
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            for key in ("limitations", "notes"):
                for text in profile.get(key) or []:
                    with self.subTest(profile=profile_path.name, key=key):
                        self.assertIsNone(figure.search(str(text)), text)


class SourceQuotesTest(unittest.TestCase):
    """「…」 exempts verbatim file metadata from the check — so the package
    source may only open 「 around an interpolated value, never around
    literal (possibly English) text."""

    def test_corner_brackets_only_wrap_interpolated_values(self) -> None:
        offenders: list[str] = []
        package = REPO_ROOT / "deepfake_lens"
        for source in sorted(package.rglob("*")):
            if source.suffix not in {".py", ".js", ".json", ".html"} or "tests" in source.relative_to(package).parts:
                continue
            for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for match in re.finditer(r"「([^」]*)」", line):
                    inner = match.group(1)
                    if not re.fullmatch(r"\{[^{}]+\}|\$\{[^{}]+\}", inner):
                        offenders.append(f"{source.relative_to(REPO_ROOT)}:{number}: {match.group(0)}")
        self.assertEqual(offenders, [], "\n".join(offenders))


class ModelDisplayNameTest(unittest.TestCase):
    """R4: profiles' Korean ``display_name`` in every examiner-facing string;
    the raw ``name`` only in the ``model``/``profile`` identifier fields."""

    @unittest.skipUnless(_have("numpy"), "numpy needed for the photo-like fixture")
    def test_default_engine_rows_use_display_names(self) -> None:
        from deepfake_lens.result_types import check_label

        profiles = {
            json.loads(path.read_text(encoding="utf-8"))["name"]: json.loads(path.read_text(encoding="utf-8"))["display_name"]
            for path in MODELS_DIR.glob("*-runtime.json")
        }
        from deepfake_lens.tests.qa.test_qa_out import write_photo_like_png

        options = AnalysisOptions()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_photo_like_png(folder / "photo.png", seed=7)  # a photo: the image engines are consulted
            (folder / "voice.wav").write_bytes(_wav_bytes())  # the audio engines
            summary, items, thresholds = scan_folder(folder, options)
        payload = json.loads(json.dumps(scan_payload(summary, items, thresholds, options), ensure_ascii=False))
        seen_members = 0
        for item in payload["items"]:
            result = item.get("result") or {}
            model = result.get("model_analysis") or {}
            for member in model.get("models", []):
                seen_members += 1
                self.assertIn(member["model"], profiles)  # identifier kept
                self.assertEqual(member["display_name"], profiles[member["model"]])
                self.assertNotIn(member["model"], member["detail"])
                self.assertTrue(member["detail"].startswith(profiles[member["model"]]), member["detail"])
            for entry in result.get("coverage", []):
                if entry["check"].startswith("model:"):
                    name = entry["check"].split(":", 1)[1]
                    self.assertIn(name, profiles)  # the check id stays the identifier
                    self.assertNotIn(name, entry["reason"])
                    self.assertIn(profiles[name], entry["reason"])
                    self.assertEqual(check_label(entry["check"]), f"외부 모델({profiles[name]})")
        self.assertGreater(seen_members, 0)


class CliMessagesAreKoreanTest(unittest.TestCase):
    """R4: the three phase-0 English messages named by the verifier."""

    def test_in_sample_threshold_warning(self) -> None:
        from deepfake_lens.analysis_api import load_thresholds

        messages: list[str] = []
        load_thresholds(AnalysisOptions(), warn=messages.append)
        self.assertTrue(messages, "packaged thresholds.json is in-sample and must warn")
        for message in messages:
            self.assertIsNone(english_prose(message), message)
            self.assertIn("경고", message)

    def test_unsigned_report_note(self) -> None:
        import contextlib
        from unittest import mock

        from deepfake_lens.cli_render import _maybe_sign

        err = io.StringIO()
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_REPORT_KEY": ""}), contextlib.redirect_stderr(err):
            _maybe_sign({"items": []}, sign=True, key_file=None)
        self.assertIn("서명 없이", err.getvalue())
        self.assertIsNone(english_prose(err.getvalue()), err.getvalue())

    def test_pdf_dependency_message(self) -> None:
        from deepfake_lens.evidence_statement import PDF_DEPENDENCY_MESSAGE

        self.assertIsNone(english_prose(PDF_DEPENDENCY_MESSAGE))


if __name__ == "__main__":
    unittest.main()
