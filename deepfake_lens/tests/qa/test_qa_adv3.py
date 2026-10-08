"""QA-ADV-3 (phase 0, WP-E, G4): keywords never promote a text.

Moved from tests/test_qa_adv3_keywords.py into the QA package (WP-J).

통과 기준: AI에 대해 쓴 사람 글 30건("언어 모델", "as an AI" 포함) → 키워드는
근거 목록에 나타나되 결론은 "참고 — 근거 부족".
"""

from __future__ import annotations

import unittest
from pathlib import Path

from deepfake_lens.core import AI_IDENTITY_PHRASES, analyze_file, analyze_text, scan_directory
from deepfake_lens.result_text import TEXT_LEGAL_LIMITATION
from deepfake_lens.result_types import EvidenceDirection, EvidenceKind, Grade, RiskBand, Verdict

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "adversarial-text" / "human-about-ai"


class HumanTextsAboutAiTest(unittest.TestCase):
    """QA-ADV-3: keywords in human texts about AI are listed, never decisive."""

    paths: list[Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = sorted(FIXTURES.glob("human-*.txt"))

    def test_corpus_has_thirty_texts_with_identity_phrases(self) -> None:
        self.assertEqual(len(self.paths), 30)
        for path in self.paths:
            text = " ".join(path.read_text(encoding="utf-8").lower().split())
            self.assertTrue(any(phrase in text for phrase in AI_IDENTITY_PHRASES), path.name)

    def test_keyword_is_listed_but_never_concludes(self) -> None:
        """QA-ADV-3: AI에 대해 쓴 사람 글 30건("언어 모델", "as an AI" 포함) → 키워드는 근거 목록에 나타나되 결론은 "참고 — 근거 부족".

        The product renders the verdict as "참고: 근거 부족 …" (G24/G4).
        """
        self.assertEqual(len(self.paths), 30)
        for path in self.paths:
            with self.subTest(path.name):
                item = analyze_file(path)
                result = item.result
                assert result is not None
                titles = [e.title for e in result.evidence if e.kind == EvidenceKind.LEXICAL]
                self.assertIn("AI 자기표현 문구", titles)
                self.assertEqual(result.grade, Grade.REFERENCE)
                self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                self.assertEqual(result.band, RiskBand.UNKNOWN)
                self.assertEqual(result.score, 0)
                self.assertTrue(result.verdict.startswith("참고: 근거 부족"), result.verdict)
                self.assertEqual(result.limitations[0], TEXT_LEGAL_LIMITATION)
                # Nothing but lexical items point toward synthesis.
                self.assertFalse([
                    e for e in result.evidence
                    if e.direction == EvidenceDirection.SYNTHETIC and e.kind != EvidenceKind.LEXICAL
                ])
                for e in result.evidence:
                    if e.kind == EvidenceKind.LEXICAL:
                        self.assertEqual(e.strength.value, "weak")

    def test_folder_scan_counts_no_conclusions(self) -> None:
        summary, items = scan_directory(FIXTURES)
        texts = [item for item in items if item.name.startswith("human-")]
        self.assertEqual(len(texts), 30)
        self.assertEqual(summary.manipulation_evidence, 0)
        self.assertEqual(summary.high, 0)
        self.assertEqual(summary.medium, 0)

    def test_keyword_stacking_still_cannot_conclude(self) -> None:
        stacked = ("As an AI language model, I cannot browse. 인공지능으로서 언어 모델로서 답합니다. "
                   "결론적으로 요약하자면 다음과 같습니다. 균형 잡힌 다양한 관점이 중요합니다.\n"
                   + "\n".join(f"{i}. 항목" for i in range(1, 9)))
        result = analyze_text(stacked * 3)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertEqual(result.score, 0)
        self.assertTrue(all(signal.weight <= 5 for signal in result.signals))


if __name__ == "__main__":
    unittest.main()
