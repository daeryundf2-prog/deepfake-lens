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

The heuristic (round 4, S8) is ``error_text.english_prose`` — the same
detector that keeps untranslated library messages out of coverage reasons
(B6). Every string is split into sentences (on ``.``, ``!``, ``?``, ``;``
and newlines) and each sentence is checked on its own — Hangul elsewhere in
the string exempts nothing. Identifier tokens are removed first; a sentence
fails when two English words remain next to each other. A contraction
(``Don't``) is one word; numeric and slash tokens (``1/4``, ``3/4``) are
removed so they cannot split a run; an ALL-CAPS word is an acronym only when
it is in the closed ``KNOWN_ACRONYMS`` list, otherwise it is a word.

The allow-list is identifiers only, and none of them can carry a sentence:

- ``snake_case``, ``camelCase``/``PascalCase`` with an inner capital,
  tokens containing a digit, hyphenated compounds (``roberta-base``),
  colon-joined ids (``model:<name>``), ``key=value``, ``--cli-flags``,
  hex digests;
- `` `code` `` spans that look like code (a shell command, at most two
  tokens, or code punctuation) — a backticked English sentence is checked;
- URLs, model ids and paths (anything with a ``/``), file names
  (``name.ext``), exception class names (``…Error``/``…Exception``);
- the closed lists in error_text: acronyms, Python module / tool names and
  product / generator names (``Stable Diffusion``, ``Deepfake Lens`` …);
- values copied verbatim from the evidence file's metadata, which the tool
  always prints inside ``「…」`` — and only there: the package source may
  open ``「`` solely around an interpolated value (``「{value}」``), never
  around literal text (SourceQuotesTest).

Coverage (S8): scan JSON text fields, packaged profiles, and the rendered
outputs — HTML report text (tags stripped), forensic PDF text (pymupdf),
legal-report text, evidence statement Markdown, the doctor table and its
JSON, the CLI scan table, and the gui.js / gui.html strings.

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
from deepfake_lens.error_text import english_prose as shared_english_prose

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "deepfake_lens" / "models"
CHECKED_KEYS = frozenset({
    "limitations", "reason", "reasons", "verdict", "detail", "title", "label", "error", "display_name",
    "notes", "reference_note", "next_checks", "verdict_label", "grade_label", "band_label",
    "source_attribution_label", "warnings", "message", "signals", "evidence_chain",
})

# S8: one detector for the tests and for error_text's runtime fallback.
english_prose = shared_english_prose

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


# S8: the round-4 verifier's bypasses of the round-3 heuristic — each must
# be flagged (two-word sentences, shouted words, contractions, numeric and
# slash tokens between words).
S8_NEGATIVES = (
    "Uncalibrated score. Treat cautiously. Not proof.",
    "UNVERIFIED SCORE, ignore it",
    "Don't trust it! Isn't proof!",
    "Caught 1/4 DALL-E fakes, missed 3/4",
)


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

    def test_round4_bypasses_fail(self) -> None:
        """S8: the four strings that passed the round-3 heuristic."""
        for text in S8_NEGATIVES:
            with self.subTest(text=text):
                self.assertIsNotNone(english_prose(text))
        # Each sentence on its own, too.
        for text in ("Not proof.", "Treat cautiously!", "Isn't proof?", "Don't trust", "SCORE IGNORED", "UNVERIFIED score"):
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
            "Stable Diffusion / A1111 추정",
            "[ MISS] fake-audio (fake-audio-runtime.json)",
            "의존성: import 가능 (cv2, onnxruntime, PIL, numpy)",
            "C2PA SDK 오류(Other): 클레임의 JUMBF 구조를 만들 수 없음(매니페스트 손상)",
            "실행 1·미실행 3·실패 0",
            "evil.zip::inner/a1111.png",
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


def _html_visible_text(html: str) -> str:
    """Visible text of an HTML document (script/style dropped, one line per text node)."""
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.skip = 0
            self.parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag in ("script", "style"):
                self.skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style"):
                self.skip -= 1

        def handle_data(self, data: str) -> None:
            if not self.skip and data.strip():
                self.parts.append(data.strip())

    parser = _Text()
    parser.feed(html)
    return "\n".join(parser.parts)


def js_string_literals(source: str) -> Iterator[tuple[int, str]]:
    """(line, text) of every string literal in JavaScript ``source``.

    Comments are skipped; a template literal yields its static text with each
    ``${…}`` expression removed (nested strings and templates inside the
    expression are scanned as literals of their own).
    """
    i, n = 0, len(source)

    def line_of(pos: int) -> int:
        return source.count("\n", 0, pos) + 1

    def read_quoted(pos: int, quote: str) -> tuple[str, int]:
        out = []
        pos += 1
        while pos < n and source[pos] != quote:
            if source[pos] == "\\":
                out.append(source[pos:pos + 2])
                pos += 2
                continue
            out.append(source[pos])
            pos += 1
        return "".join(out), pos + 1

    def read_template(pos: int) -> tuple[str, int, list[tuple[int, str]]]:
        out: list[str] = []
        nested: list[tuple[int, str]] = []
        pos += 1
        while pos < n and source[pos] != "`":
            if source[pos] == "\\":
                out.append(source[pos:pos + 2])
                pos += 2
            elif source.startswith("${", pos):
                pos, inner = skip_expression(pos + 2)
                nested.extend(inner)
                out.append(" ")
            else:
                out.append(source[pos])
                pos += 1
        return "".join(out), pos + 1, nested

    def skip_expression(pos: int) -> tuple[int, list[tuple[int, str]]]:
        depth, found = 1, []
        while pos < n and depth:
            char = source[pos]
            if char in "'\"":
                text, end = read_quoted(pos, char)
                found.append((line_of(pos), text))
                pos = end
            elif char == "`":
                text, end, nested = read_template(pos)
                found.append((line_of(pos), text))
                found.extend(nested)
                pos = end
            else:
                depth += {"{": 1, "}": -1}.get(char, 0)
                pos += 1
        return pos, found

    while i < n:
        if source.startswith("//", i):
            i = source.find("\n", i)
            i = n if i == -1 else i
        elif source.startswith("/*", i):
            i = source.find("*/", i)
            i = n if i == -1 else i + 2
        elif source[i] in "'\"":
            text, end = read_quoted(i, source[i])
            yield line_of(i), text
            i = end
        elif source[i] == "`":
            start = i
            text, i, nested = read_template(i)
            yield line_of(start), text
            yield from nested
        elif source[i] == "/" and re.match(r"[=(,:!&|?{};\[>]\s*$", source[max(0, i - 20):i].rstrip()[-1:] or ";"):
            # A regex literal: skip to its closing slash (not a string).
            match = re.match(r"/(?:\\.|\[(?:\\.|[^\]])*\]|[^/\\\n])+/[a-z]*", source[i:])
            i += match.end() if match else 1
        else:
            i += 1


def _offending_lines(where: str, text: str) -> list[str]:
    offenders = []
    for number, line in enumerate(text.splitlines(), 1):
        run = english_prose(line)
        if run:
            offenders.append(f"{where}:{number}: {run!r} in {line.strip()[:160]!r}")
    return offenders


@unittest.skipUnless(_have("numpy", "PIL"), "numpy + Pillow needed for the mixed fixture")
class RenderedOutputsAreKoreanTest(unittest.TestCase):
    """S8: the rendered outputs an examiner reads, not just scan JSON."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.folder = write_mixed_folder(Path(cls._tmp.name).resolve() / "mixed")
        cls.options = AnalysisOptions(recursive=True)
        cls.summary, cls.items, cls.thresholds = scan_folder(cls.folder, cls.options)
        cls.out = Path(cls._tmp.name) / "out"
        cls.out.mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_html_report_text(self) -> None:
        from deepfake_lens.reports import write_html_report

        offenders: list[str] = []
        for redact in (False, True):
            path = self.out / f"r{int(redact)}.html"
            write_html_report(path, self.summary, self.items, redact_paths=redact, thresholds=self.thresholds)
            offenders += _offending_lines(path.name, _html_visible_text(path.read_text(encoding="utf-8")))
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))

    @unittest.skipUnless(_have("pymupdf") or _have("fitz"), "pymupdf not installed")
    def test_forensic_pdf_text(self) -> None:
        from deepfake_lens.pdf_backend import import_pymupdf
        from deepfake_lens.reports import write_forensic_pdf_report, write_pdf_report

        offenders: list[str] = []
        for name, writer in (("forensic.pdf", write_forensic_pdf_report), ("simple.pdf", write_pdf_report)):
            path = self.out / name
            writer(path, self.summary, self.items, thresholds=self.thresholds)
            with import_pymupdf().open(str(path)) as doc:
                text = "\n".join(page.get_text() for page in doc)
            self.assertIn("문서 번호", text)
            offenders += _offending_lines(name, text)
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))

    def test_evidence_statement_markdown(self) -> None:
        from deepfake_lens.core import _thresholds_json
        from deepfake_lens.evidence_statement import build_evidence_statement, write_evidence_statement_markdown

        statement = build_evidence_statement(self.items, thresholds=_thresholds_json(self.thresholds), scan_root=self.folder)
        path = self.out / "statement.md"
        write_evidence_statement_markdown(path, statement)
        text = path.read_text(encoding="utf-8").replace("<br>", "\n")
        offenders = _offending_lines(path.name, text)
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))

    def test_legal_report_text(self) -> None:
        from deepfake_lens.enhanced_forensics import build_legal_report, legal_report_text

        offenders: list[str] = []
        for name in ("a1111.png", "bundle.zip", "truncated_nikon.jpg", "empty.jpg", "garbage.wav", "essay_en.txt", "contract_chatgpt.docx"):
            text = legal_report_text(build_legal_report(self.folder / name, AnalysisOptions()))
            offenders += _offending_lines(f"legal-report {name}", text)
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))

    def test_cli_scan_table(self) -> None:
        import contextlib

        from deepfake_lens.vendor_weights import weights_coverage
        from deepfake_lens.cli_render import _print_table

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table(self.summary, self.items, include_low=True, coverage=weights_coverage(None), thresholds=self.thresholds)
        offenders = _offending_lines("scan table", out.getvalue())
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))

    def test_standalone_command_text(self) -> None:
        """G1 (round 5): forensic/classify/explain/agent/multimodal text output — labels only.

        The verifier found ``(manipulation_evidence)``,
        ``[deterministic/synthetic/strong]``, ``metadata: ran``,
        ``model:<raw profile name>: skipped`` and ``등급: reference`` in these
        renderings; the JSON codes belong in ``--format json`` only.
        """
        import contextlib

        from deepfake_lens.cli import main

        raw_codes = re.compile(
            r"\b(?:manipulation_evidence|authenticity_evidence|undetermined|deterministic|statistical|lexical"
            r"|synthetic|authentic|neutral|strong|moderate|weak)\b|: (?:ran|skipped|failed)\b"
            r"|등급: (?:evidence|reference)\b|\bmodel:|\] [a-z]+_[a-z_]+$"
        )
        commands: list[list[str]] = []
        for name in ("a1111.png", "exif_canon.jpg", "bundle.zip", "empty.jpg", "garbage.wav", "essay_en.txt", "contract_chatgpt.docx"):
            path = str(self.folder / name)
            commands += [["forensic", path, "--format", "table"], ["classify", path, "--format", "table"], ["explain", path]]
        commands += [
            ["agent", "--file", str(self.folder / "essay_en.txt"), "--format", "table"],
            ["agent", "--text", "As an AI language model, I cannot do that.", "--format", "table"],
            ["multimodal", str(self.folder / "a1111.png"), str(self.folder / "essay_ko.txt"), "--format", "table"],
            ["explain", "--score", "50"],
        ]
        offenders: list[str] = []
        for argv in commands:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                main(argv)
            text = out.getvalue()
            self.assertTrue(text.strip(), argv)
            where = " ".join([argv[0], Path(argv[1]).name if len(argv) > 1 else ""])
            offenders += _offending_lines(where, text)
            for number, line in enumerate(text.splitlines(), 1):
                if line.startswith("대상:"):
                    continue  # the path the examiner named, verbatim
                match = raw_codes.search(line)
                if match:
                    offenders.append(f"{where}:{number}: raw code {match.group(0)!r} in {line.strip()[:160]!r}")
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))

    def test_doctor_table_and_json(self) -> None:
        from deepfake_lens.doctor import format_report, run_diagnostics

        report = run_diagnostics()
        offenders = _offending_lines("doctor table", format_report(report))
        payload = json.loads(json.dumps(report.to_json(), ensure_ascii=False))
        for where, text in _doctor_text_fields(payload):
            run = english_prose(text)
            if run:
                offenders.append(f"doctor json {where}: {run!r} in {text[:160]!r}")
        self.assertEqual(offenders, [], "\n".join(offenders[:40]))


def _doctor_text_fields(node: Any, path: str = "", key: str | None = None) -> Iterator[tuple[str, str]]:
    """Examiner-facing doctor JSON strings (identifiers ``name``/``file``/``runtime`` and the status codes excluded)."""
    if isinstance(node, dict):
        for child_key, value in node.items():
            yield from _doctor_text_fields(value, f"{path}/{child_key}", child_key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _doctor_text_fields(value, f"{path}[{index}]", key)
    elif isinstance(node, str) and key in {"detail", "display_name", "pin_detail", "runtime_deps_detail", "checkpoint_detail"}:
        yield path, node


class GuiStringsAreKoreanTest(unittest.TestCase):
    """S8: gui.js label tables and notice strings, gui.html visible text."""

    def test_js_literal_scanner(self) -> None:
        source = "const a = 'x'; // 'not a literal'\nconst b = `안녕 ${f('in')} <b>끝</b>`; /* 'no' */ const c = /a'b/g;"
        self.assertEqual([text for _, text in js_string_literals(source)], ["x", "안녕   <b>끝</b>", "in"])

    def test_gui_js_strings(self) -> None:
        source = (REPO_ROOT / "deepfake_lens" / "gui.js").read_text(encoding="utf-8")
        offenders = []
        literals = list(js_string_literals(source))
        self.assertGreater(len(literals), 300)
        for line, text in literals:
            visible = re.sub(r"<[^>]*>", " ", text)
            run = english_prose(visible)
            if run:
                offenders.append(f"gui.js:{line}: {run!r} in {text[:120]!r}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_gui_html_text(self) -> None:
        html = (REPO_ROOT / "deepfake_lens" / "gui.html").read_text(encoding="utf-8")
        offenders = _offending_lines("gui.html", _html_visible_text(html))
        for attribute in re.findall(r'(?:placeholder|title|aria-label|alt)="([^"]*)"', html):
            if english_prose(attribute):
                offenders.append(f"gui.html attribute: {attribute!r}")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
