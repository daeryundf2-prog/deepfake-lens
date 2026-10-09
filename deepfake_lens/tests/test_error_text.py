"""N1: exception messages in user-facing fields carry no file-system paths.

A coverage ``reason`` (and the limitations, reports and evidence statement
built from it) used to copy an exception message verbatim — Pillow,
soundfile, zipfile and c2pa put the full path of the evidence file in it.
``error_text.failure_reason`` now replaces the scan root with ``<root>`` and
any other absolute path with its base name, and gives well-known library
messages in Korean. These tests pin the scrubber and scan a folder of
unreadable files end to end: no ``reason``/``limitations``/``detail``/
``error``/``verdict`` string may contain an absolute path, and the
``--redact-paths`` HTML report contains no absolute path at all.
"""

from __future__ import annotations

import errno
import importlib.util
import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Iterator

from deepfake_lens.error_text import (
    ROOT_PLACEHOLDER,
    exception_text,
    failure_reason,
    path_scrub_root,
    scrub_paths,
)

HAVE_PIL = importlib.util.find_spec("PIL") is not None
# Fields an examiner reads; none may carry a path of the examiner's machine.
TEXT_KEYS = frozenset({"reason", "limitations", "detail", "error", "verdict", "title", "reference_note", "next_checks"})
# An absolute POSIX path with at least two components, or a Windows drive path.
ABSOLUTE_PATH = re.compile(r"(?<![\w.~<>/:=#-])/(?:[^\s/'\"`:;,()\[\]]+/)+[^\s/'\"`:;,()\[\]]+|\b[A-Za-z]:\\")


def write_unreadable_folder(folder: Path) -> Path:
    """Files every reader rejects, plus one nested copy (scanned recursively)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "empty.jpg").write_bytes(b"")
    (folder / "fake_ext.gif").write_bytes(b"this is not a gif at all\n" * 8)
    (folder / "fake.mp3").write_bytes(bytes((i * 37 + 11) % 256 for i in range(3000)))
    (folder / "garbage.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunkjunk")
    (folder / "broken.docx").write_bytes(b"PK\x03\x04 not really a zip")
    (folder / "broken.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 10)
    (folder / "empty.png").write_bytes(b"")
    if HAVE_PIL:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (300, 200), (120, 90, 60)).save(buffer, format="JPEG", quality=90)
        (folder / "truncated.jpg").write_bytes(buffer.getvalue()[: len(buffer.getvalue()) // 3])
    with zipfile.ZipFile(folder / "bundle.zip", "w") as archive:
        archive.writestr("inner/empty.jpg", b"")
        archive.writestr("inner/fake.gif", b"GIF89a junk")
    sub = folder / "sub dir"
    sub.mkdir(exist_ok=True)
    (sub / "empty.jpg").write_bytes(b"")
    return folder


def text_fields(node: Any, key: str | None = None, path: str = "") -> Iterator[tuple[str, str]]:
    """(json path, string) for every string under a TEXT_KEYS key."""
    if isinstance(node, dict):
        for child_key, value in node.items():
            yield from text_fields(value, child_key, f"{path}/{child_key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from text_fields(value, key, f"{path}[{index}]")
    elif isinstance(node, str) and key in TEXT_KEYS:
        yield path, node


class ScrubPathsTest(unittest.TestCase):
    def test_root_becomes_placeholder_and_other_paths_their_base_name(self) -> None:
        with path_scrub_root("/srv/evidence/case-7"):
            self.assertEqual(
                scrub_paths("cannot open '/srv/evidence/case-7/sub/a.jpg'"),
                f"cannot open '{ROOT_PLACEHOLDER}/sub/a.jpg'",
            )
            # A sibling folder sharing the prefix is not the root.
            self.assertEqual(scrub_paths("open /srv/evidence/case-70/b.jpg failed"), "open b.jpg failed")
            self.assertEqual(scrub_paths("tmp /tmp/dflens-arc-x1/gen/m.png bad"), "tmp m.png bad")
            self.assertEqual(scrub_paths("Error opening '/home/u/My Docs/v.wav': x"), "Error opening 'v.wav': x")
            self.assertEqual(scrub_paths(r"C:\Users\kim\Desktop\x.png bad"), "x.png bad")

    def test_non_paths_are_left_alone(self) -> None:
        for text in (
            "https://huggingface.co/org/model",
            "노출 1/125초, image/jpeg",
            "self#jumbf=/c2pa/urn:uuid:1/c2pa.assertions",
            "<root>/sub/a.jpg",
            "fakespot-ai/roberta-base",
        ):
            with self.subTest(text=text):
                self.assertEqual(scrub_paths(text), text)

    def test_failure_reason_keeps_the_class_and_drops_the_path(self) -> None:
        with path_scrub_root("/data/case"):
            reason = failure_reason(FileNotFoundError(errno.ENOENT, "No such file or directory", "/data/case/x/y.jpg"))
            self.assertEqual(reason, f"FileNotFoundError: [Errno {errno.ENOENT}] 파일 또는 폴더가 없습니다: {ROOT_PLACEHOLDER}/x/y.jpg")
            self.assertNotIn("/data/case", failure_reason(RuntimeError("bad file /data/case/z.png")))
        self.assertEqual(exception_text(OSError("image file is truncated (37 bytes not processed)")), "이미지 파일이 잘려 있습니다(처리하지 못한 바이트 37개)")
        self.assertEqual(failure_reason(ValueError()), "ValueError")

    def test_c2pa_sdk_messages_are_korean(self) -> None:
        """B6: the c2pa-python class names and messages seen on damaged manifests."""

        class _C2paOther(Exception):
            pass

        class _C2paVerify(Exception):
            pass

        self.assertEqual(
            failure_reason(_C2paOther("Other: could not create valid JUMBF for claim")),
            "C2PA SDK 오류(Other): 클레임의 JUMBF 구조를 만들 수 없음(매니페스트 손상)",
        )
        self.assertEqual(failure_reason(_C2paVerify("Verify: invalid embedded file box")), "C2PA SDK 오류(Verify): 내장 파일 박스가 손상됨")
        self.assertEqual(failure_reason(_C2paOther("Other: unexpected end of file")), "C2PA SDK 오류(Other): 파일이 예상보다 일찍 끝남(파일 잘림)")
        # A reason that already embeds the SDK class (wrapped by core) is translated too.
        self.assertEqual(
            exception_text(RuntimeError("C2PA 판독 실패: _C2paOther: Other: could not create valid JUMBF for claim")),
            "C2PA 판독 실패: C2PA SDK 오류(Other): 클레임의 JUMBF 구조를 만들 수 없음(매니페스트 손상)",
        )
        self.assertEqual(exception_text(RuntimeError("Verify: unexpected end of file")), "검증 단계: 파일이 예상보다 일찍 끝남(파일 잘림)")

    def test_untranslated_english_message_is_replaced_and_logged(self) -> None:
        """B6: an unknown English library message never reaches a reason; the raw text goes to the log."""
        with self.assertLogs("deepfake_lens.error_text", level="INFO") as logs:
            reason = failure_reason(RuntimeError("the decoder hit a malformed segment"))
        self.assertEqual(reason, "RuntimeError: 라이브러리 오류(RuntimeError) — 상세는 로그 참조")
        self.assertTrue(any("the decoder hit a malformed segment" in line for line in logs.output), logs.output)

        class _C2paSignature(Exception):
            pass

        with self.assertLogs("deepfake_lens.error_text", level="INFO"):
            self.assertEqual(
                failure_reason(_C2paSignature("Signature: certificate chain is not trusted here")),
                "C2PA SDK 오류(Signature): 라이브러리 오류(_C2paSignature) — 상세는 로그 참조",
            )
        # The Korean context before the English part is kept.
        with self.assertLogs("deepfake_lens.error_text", level="INFO"):
            self.assertEqual(
                exception_text(RuntimeError("C2PA 판독 실패: something entirely new")),
                "C2PA 판독 실패: 라이브러리 오류(RuntimeError) — 상세는 로그 참조",
            )
        # Korean, identifiers and a single word pass unchanged.
        for text in ("이미지가 손상되었습니다", "boom", "failed:pymupdf:RuntimeError", "C2PA SDK 오류(Io): 잘못된 블록 ID 101"):
            with self.subTest(text=text):
                self.assertEqual(exception_text(RuntimeError(text)), text)

    def test_root_registration_is_scoped(self) -> None:
        with path_scrub_root("/data/case"):
            pass
        self.assertEqual(scrub_paths("/data/case/a.jpg"), "a.jpg")


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class UnreadableFolderScanTest(unittest.TestCase):
    """N1: a scan of unreadable files reports causes without any absolute path."""

    def test_no_absolute_path_in_text_fields_or_redacted_report(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run, scan_payload
        from deepfake_lens.reports import write_html_report

        with tempfile.TemporaryDirectory() as tmp:
            folder = write_unreadable_folder(Path(tmp).resolve() / "evidence case")
            options = AnalysisOptions(recursive=True)
            run = scan_folder_run(folder, options)
            summary, items, thresholds = run.summary, run.items, run.thresholds
            payload = json.loads(json.dumps(scan_payload(summary, items, thresholds, options), ensure_ascii=False))
            fields = list(text_fields(payload["items"]))
            self.assertTrue(fields)
            offenders = [
                f"{where}: {text[:160]}"
                for where, text in fields
                if ABSOLUTE_PATH.search(text) or tmp in text or str(folder) in text
            ]
            self.assertEqual(offenders, [], "\n".join(offenders))
            # The unreadable files do fail, with the cause in Korean or the class name.
            reasons = [text for where, text in fields if where.endswith("/reason")]
            self.assertTrue(any("이미지 형식을 인식할 수 없습니다" in reason for reason in reasons), reasons)
            self.assertTrue(any(ROOT_PLACEHOLDER in reason for reason in reasons), reasons)

            report = Path(tmp) / "report.html"
            write_html_report(report, summary, items, redact_paths=True, thresholds=thresholds)
            html = report.read_text(encoding="utf-8")
            self.assertNotIn(str(folder), html)
            self.assertNotIn(tmp, html)


if __name__ == "__main__":
    unittest.main()


class EnglishDetectorBypassTest(unittest.TestCase):
    """Y11 (round 7): spellings that passed the English detector are caught."""

    def test_round_seven_bypasses_are_flagged(self) -> None:
        from deepfake_lens.error_text import english_prose

        cases = {
            "결론: proBABLY_fAKE": "probably fake",  # case-mangled snake token
            "PROBABLY_FAKE": "probably fake",
            "ｕｎｒｅｌｉａｂｌｅ ｒｅｓｕｌｔ": "unreliable result",  # fullwidth Latin (NFKC)
            "이것은AI가만든것같음probablyfakeimage": "probably fake",  # glued without spaces
            "결과notreliable입니다": "not reliable",
            "Thе rеsult is fаkе": "The result is fake",  # Cyrillic е/а look-alikes
            "саution: fаke": None,  # checked below: flagged, wording may vary
            "sc­ore un­re­li­able": "score unreliable",  # soft hyphens
            "un​reliable re​sult": "unreliable result",  # zero-width spaces
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                hit = english_prose(text)
                self.assertIsNotNone(hit, text)
                if expected is not None:
                    self.assertEqual(hit, expected)

    def test_round_eight_conclusion_words_are_flagged(self) -> None:
        """P13 (round 8): ProbablyFake, FakeImageDetected, probably_fake and AUTHENTIC passed;
        a conclusion word alone (any case) or in a code token next to another word fails."""
        from deepfake_lens.error_text import VERDICT_WORDS, english_prose

        self.assertEqual(VERDICT_WORDS, frozenset({
            "fake", "real", "authentic", "synthetic", "detected", "generated", "manipulated", "deepfake",
            "genuine", "likely", "probably", "suspicious", "clean", "safe",
        }))
        for text, expected in {
            "결론: ProbablyFake": "probably fake",
            "FakeImageDetected": "fake image detected",
            "probably_fake": "probably fake",
            "AUTHENTIC": "authentic",
            "결론: AUTHENTIC 입니다": "authentic",
            "isRealPhoto": "is real photo",
            "SYNTHETICImage": "synthetic image",
        }.items():
            with self.subTest(text=text):
                self.assertEqual(english_prose(text), expected)
        for word in sorted(VERDICT_WORDS):
            for spelled in (word, word.upper(), word.capitalize(), f"결론: {word}", f"판정 「{word.upper()}」"):
                with self.subTest(word=spelled):
                    self.assertIsNotNone(english_prose(spelled), spelled)

    def test_round_eight_identifiers_still_pass(self) -> None:
        """P13 (round 8): contract identifiers and the package's own names are not verdicts."""
        from deepfake_lens.error_text import english_prose

        for text in (
            "verdict_code", "authenticity_evidence", "manipulation_evidence", "score_is_calibrated",
            "DEEPFAKE_LENS_REPORT_KEY 환경 변수", "deepfake_lens.cli", "real-like-texture.png", "label=real",
            "deepfake-lens scan", "expected_label", "trainedAlgorithmicMedia",
        ):
            with self.subTest(text=text):
                self.assertIsNone(english_prose(text), text)

    def test_identifiers_and_korean_still_pass(self) -> None:
        from deepfake_lens.error_text import english_prose

        for text in (
            "dataset 지문", "checkpoint 파일", "warning_threshold=0.5", "verdict_code", "score_is_calibrated",
            "SHA-256: 0a1b", "C2PA 매니페스트", "AUROC 95% CI 하한 0.85", "Stable Diffusion / A1111 추정",
            "`--redact-paths`", "LibsndfileError: 오디오", "BadZipFile", "faceswap 경계면", "deepfake-lens scan",
            "결론은 세 가지뿐입니다: 조작·생성 근거 있음 / 원본성 근거 있음 / 판단 불가",
            "ＳＨＡ－２５６ 해시", "Ｃ２ＰＡ 매니페스트",
        ):
            with self.subTest(text=text):
                self.assertIsNone(english_prose(text), text)


class JsonErrorTranslationTest(unittest.TestCase):
    """P9 (round 8): json.JSONDecodeError messages reached the CLI in English
    ("Expecting property name enclosed in double quotes: line 1 column 2 (char 1)")."""

    def test_every_json_error_is_korean_with_numeric_position(self) -> None:
        import json

        from deepfake_lens.error_text import english_prose, read_error_ko

        samples = ["{bad", "[1,]", '{"a":1,}', '{"a" 1}', "[1 2]", '"abc', '{"a":"\x01"}', '"\\q"', '"\\u12"', "1 2", "﻿{}", "", "["]
        seen = set()
        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaises(json.JSONDecodeError) as caught:
                    json.loads(sample)
                text = read_error_ko(caught.exception)
                seen.add(caught.exception.msg)
                self.assertTrue(text.startswith("JSON 형식 오류: "), text)
                self.assertTrue(text.endswith(f"({caught.exception.lineno}행 {caught.exception.colno}열)"), text)
                self.assertIsNone(english_prose(text), text)
                self.assertNotIn(caught.exception.msg, text)
        self.assertGreaterEqual(len(seen), 12, seen)

    def test_decode_and_os_errors(self) -> None:
        from deepfake_lens.error_text import read_error_ko

        with self.assertRaises(UnicodeDecodeError) as caught:
            b"ok\x85".decode("utf-8")
        self.assertEqual(read_error_ko(caught.exception), "UTF-8 텍스트가 아닙니다(바이트 위치 2)")
        with self.assertRaises(OSError) as missing:
            open(Path(tempfile.gettempdir()) / "deepfake-lens-p9-missing" / "x.json", encoding="utf-8")
        self.assertEqual(read_error_ko(missing.exception), "파일 또는 폴더가 없습니다(오류 번호 2)")
