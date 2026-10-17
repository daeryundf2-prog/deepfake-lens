"""techniques-2026 item 6: the legal-marking context of every analyzed media / document row."""

from __future__ import annotations

import importlib.util
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from deepfake_lens.core import analyze_file
from deepfake_lens.marking_context import ABSENCE_STATEMENT, MARKING_TITLE, ONLINE_CHECK
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength, Verdict

REPO = Path(__file__).resolve().parents[2]


def _wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 330 * i / 8000))) for i in range(8000)))
    return path


class MarkingContextTest(unittest.TestCase):
    def _result(self, path: Path):
        item = analyze_file(path)
        assert item.result is not None
        return item.result

    def test_image_row_states_the_checked_marks_and_the_online_only_services(self) -> None:
        result = self._result(REPO / "fixtures" / "benchmark" / "real-like-texture.png")
        [item] = [e for e in result.evidence if e.title == MARKING_TITLE]
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
        self.assertIn(ABSENCE_STATEMENT, item.detail)
        self.assertIn("SynthID", item.detail)
        context = result.marking_context
        assert context is not None
        checks = [entry["check"] for entry in context["checked"]]
        self.assertEqual(checks[:4], ["c2pa", "watermark_sdxl", "watermark_trustmark", "watermark_videoseal"])
        self.assertTrue(all(entry["status"] == "skipped" for entry in context["online_only"]))
        [online] = [e for e in result.coverage if e.check == ONLINE_CHECK]
        self.assertEqual(online.status, CoverageStatus.SKIPPED)
        self.assertIn("네트워크", online.reason)

    def test_absence_never_concludes_originality(self) -> None:
        result = self._result(REPO / "fixtures" / "benchmark" / "real-like-texture.png")
        self.assertNotEqual(result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)

    def test_audio_and_text_rows_carry_their_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = self._result(_wav(Path(tmp) / "tone.wav"))
            note = Path(tmp) / "note.txt"
            note.write_text("사람이 직접 쓴 메모입니다. " * 8, encoding="utf-8")
            text = self._result(note)
        assert audio.marking_context is not None and text.marking_context is not None
        # TB-2: an audio row also lists its PerTh check.
        self.assertEqual([e["check"] for e in audio.marking_context["checked"]], ["c2pa", "watermark_audioseal", "watermark_perth"])
        # TB-1: a text row also lists its invisible-Unicode-mark check (found / not_found / skipped).
        self.assertEqual([e["check"] for e in text.marking_context["checked"]], ["c2pa", "text_invisible_marks"])
        self.assertEqual(text.marking_context["checked"][1]["outcome"], "not_found")
        self.assertEqual(text.marking_context["checked"][0]["outcome"], "skipped")

    def test_context_is_in_the_scan_json(self) -> None:
        result = self._result(REPO / "fixtures" / "benchmark" / "real-like-texture.png")
        payload = json.loads(json.dumps(result.to_json(), ensure_ascii=False))
        self.assertEqual(payload["marking_context"]["absence_statement"], ABSENCE_STATEMENT)

    @unittest.skipUnless(importlib.util.find_spec("c2pa") is not None, "c2pa-python and fixtures/c2pa-2.4 required")
    def test_a_found_manifest_is_listed_as_found(self) -> None:
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_C2PA_TRUST_ANCHORS": str(REPO / "fixtures" / "c2pa-2.4" / "test-ca-cert.pem")}):
            result = self._result(REPO / "fixtures" / "c2pa-2.4" / "ai-generated.png")
        assert result.marking_context is not None
        self.assertEqual(result.marking_context["checked"][0]["outcome"], "found")
        self.assertEqual(result.marking_context["marks_found"], 1)


if __name__ == "__main__":
    unittest.main()
