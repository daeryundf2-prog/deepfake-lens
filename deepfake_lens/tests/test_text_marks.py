"""TB-1: invisible Unicode marks in text are facts (tags, variation-selector data, zero-width, C2PA text wrappers)."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from deepfake_lens.core import analyze_file
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceKind, EvidenceStrength, Verdict
from deepfake_lens.text_marks import C2PA_TEXT_MAGIC, TEXT_MARKS_CHECK, scan_text, text_mark_layers, vs_byte


def tags(text: str) -> str:
    return "".join(chr(0xE0000 + ord(c)) for c in text)


def vs(data: bytes) -> str:
    return "".join(chr(0xFE00 + b) if b < 16 else chr(0xE0100 + b - 16) for b in data)


ENGLAND = "\U0001F3F4" + tags("gbeng") + "\U000E007F"


class ScanTextTest(unittest.TestCase):
    def test_tag_run_is_decoded_and_emoji_flag_is_not_a_mark(self) -> None:
        scan = scan_text("보고서 " + ENGLAND + " 본문" + tags("ignore previous") + " 끝")
        self.assertEqual(scan.emoji_tag_sequences, 1)
        self.assertEqual([d for _, d in scan.tag_runs], ["ignore previous"])
        self.assertEqual(scan.tag_chars, len("ignore previous"))

    def test_mapping_is_the_c2pa_one(self) -> None:
        self.assertEqual([vs_byte(ord(c)) for c in vs(bytes(range(256)))], list(range(256)))
        self.assertEqual(vs(C2PA_TEXT_MAGIC)[0], "\U000E0133")  # 'C' = 0x43 -> U+E0100 + 0x33

    def test_c2pa_wrapper_after_feff_is_found_without_a_zero_width_count(self) -> None:
        manifest = b"jumb" + bytes(20)
        wrapper = C2PA_TEXT_MAGIC + bytes([1]) + len(manifest).to_bytes(4, "big") + manifest
        scan = scan_text("평범한 글입니다." + "\ufeff" + vs(wrapper + b"\x00\x10"))  # padding (c2pa-text encode_wrapper_padded)
        self.assertEqual(len(scan.c2pa_wrappers), 1)
        self.assertEqual(scan.zero_width_total, 0)
        layers = text_mark_layers("평범한 글입니다." + "\ufeff" + vs(wrapper), source="분석한 본문")
        [item] = [e for e in layers.evidence if e.title.startswith("C2PA 텍스트 매니페스트 래퍼")]
        self.assertIn("래퍼 안 바이트 수와 일치", item.detail)
        self.assertIn("검증하지 않았습니다", item.detail)
        self.assertIn("c2pa_text_wrapper", [f.check for f in layers.findings])

    def test_single_selectors_are_legitimate_but_runs_and_high_selectors_are_data(self) -> None:
        scan = scan_text("❤\ufe0f 漢\U000E0100 끝")  # emoji presentation + an IVS
        self.assertEqual((scan.single_selectors, scan.vs_runs), (2, []))
        scan = scan_text("a" + vs(b"hello") + " 漢\U000E01EF")
        self.assertEqual([d for _, d in scan.vs_runs], [b"hello", bytes([0xFF])])

    def test_zero_width_counts_skip_emoji_zwj(self) -> None:
        family = "\U0001F468\u200d\U0001F469\u200d\U0001F467"
        scan = scan_text(family + " 글\u200b\u200c\u200b\u200d\u200b 끝")
        self.assertEqual(scan.emoji_zwj, 2)
        self.assertEqual(scan.zero_width_total, 5)
        self.assertEqual(scan.zero_width_runs, [(len(family) + 2, 5)])

    def test_structured_text_block(self) -> None:
        scan = scan_text("# 제목\n<!-- -----BEGIN C2PA MANIFEST----- data:application/c2pa;base64,AAAA -----END C2PA MANIFEST----- -->\n")
        self.assertEqual(len(scan.structured_blocks), 1)

    def test_plain_text_has_no_mark(self) -> None:
        layers = text_mark_layers("그냥 사람이 쓴 글입니다.", source="분석한 본문")
        [entry] = layers.coverage
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn("무정보", entry.reason)
        self.assertEqual(layers.evidence, [])

    def test_no_text_is_skipped(self) -> None:
        layers = text_mark_layers("", source="분석한 본문")
        self.assertEqual(layers.coverage[0].status, CoverageStatus.SKIPPED)


class TextMarksInScanTest(unittest.TestCase):
    def test_text_file_row_lists_facts_and_never_concludes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memo.txt"
            path.write_text("회의록입니다. 다음 주에 다시 논의합니다." + tags("written by model-x"), encoding="utf-8")
            item = analyze_file(path)
        result = item.result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == TEXT_MARKS_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        [fact] = [e for e in result.evidence if e.title == "비가시 유니코드 표식: 태그 문자열"]
        self.assertEqual((fact.kind, fact.direction, fact.strength), (EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
        self.assertIn("「written by model-x」", fact.detail)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        assert result.marking_context is not None
        self.assertIn(TEXT_MARKS_CHECK, [c["check"] for c in result.marking_context["checked"]])
        [c2pa] = [c for c in result.coverage if c.check == "c2pa"]
        self.assertIn("비가시 유니코드 표식 검사", c2pa.reason)

    def test_docx_extracted_text_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.docx"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>본문\u200b\u200b\u200b\u200b입니다</w:t></w:r></w:p></w:body></w:document>")
            result = analyze_file(path).result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == TEXT_MARKS_CHECK]
        self.assertIn("추출한 문서 본문", entry.reason)
        self.assertTrue(any(e.title == "비가시 유니코드 표식: 제로폭 문자" for e in result.evidence))

    def test_failed_extraction_skips_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.docx"
            path.write_bytes(b"not a zip")
            result = analyze_file(path).result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == TEXT_MARKS_CHECK]
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)


if __name__ == "__main__":
    unittest.main()
