"""R4: user-facing result text is Korean.

Phase 0 added English sentences to fields the examiner reads — profile
``limitations``/``notes`` (shown verbatim in model_analysis and result
limitations), model adapter details and coverage reasons, CLI warnings.
These tests walk real scan JSON and the packaged profiles and fail on any
``limitations``/``reason``/``verdict``/``detail`` (and ``notes``/
``reference_note``) string that is ASCII-English prose: at least three
consecutive ASCII words of three or more letters and no Hangul, after
removing tokens that legitimately stay in English — file names and paths,
URLs, model ids (``org/name``), exception class names, ``sha256``, ``pin``.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, Iterator

from deepfake_lens.analysis_api import AnalysisOptions, scan_folder, scan_payload

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "deepfake_lens" / "models"
CHECKED_KEYS = frozenset({"limitations", "reason", "verdict", "detail", "notes", "reference_note"})

HANGUL = re.compile(r"[가-힣]")
# Three consecutive ASCII words of >= 3 letters (separated by spaces or light punctuation).
ENGLISH_PROSE = re.compile(r"\b[A-Za-z]{3,}\b(?:[ ,;:'\"()\-]+\b[A-Za-z]{3,}\b){2,}")
ALLOWED_TOKENS = (
    re.compile(r"https?://\S+"),  # URLs
    re.compile(r"[\w.\-]+/[\w.\-/]+"),  # model ids and paths: org/name, models/x.json
    re.compile(r"\b[\w\-]+\.(?:py|json|md|pth|pt|onnx|png|jpe?g|txt|wav|mp4|zip|xml|html|pdf|csv)\b"),  # file names
    re.compile(r"\b_?[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Missing|Skipped|Denied)\b"),  # exception classes
    re.compile(r"\b(?:sha256|pin)\b"),
)


def english_prose(text: str) -> str | None:
    """The offending English run in ``text``, or None (R4 heuristic)."""
    if HANGUL.search(text):
        return None
    stripped = text
    for pattern in ALLOWED_TOKENS:
        stripped = pattern.sub(" ", stripped)
    match = ENGLISH_PROSE.search(stripped)
    return match.group(0) if match else None


def checked_strings(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """(json path, string) for every string under a checked key."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}/{key}"
            if key in CHECKED_KEYS:
                values = value if isinstance(value, list) else [value]
                for index, item in enumerate(values):
                    if isinstance(item, str):
                        yield (f"{child}[{index}]" if isinstance(value, list) else child), item
            yield from checked_strings(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from checked_strings(item, f"{path}[{index}]")


class EnglishProseHeuristicTest(unittest.TestCase):
    """The detector itself: flags prose, passes Korean and allowed tokens."""

    def test_flags_english_sentences(self) -> None:
        for text in (
            "Scores are a prioritization signal, not a truth label.",
            "note: report written unsigned (no key; set DEEPFAKE_LENS_REPORT_KEY or --key-file)",
            "pymupdf is required for PDF evidence statements; install it",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(english_prose(text))

    def test_allows_korean_and_identifiers(self) -> None:
        for text in (
            "점수는 보정 전 원점수이며 진위 판정이 아닙니다.",
            "AnalyzerError: C2PA 판독 실패: _C2paVerify: Verify: invalid embedded file box",
            "fakespot-ai/roberta-base-ai-text-detection-v1",
            "https://huggingface.co/umm-maybe/AI-image-detector",
            "experiments/RECOMPRESSION_EVAL.md",
            "RuntimeError",
            "pin sha256",
        ):
            with self.subTest(text=text):
                self.assertIsNone(english_prose(text))


class ScanOutputIsKoreanTest(unittest.TestCase):
    """R4: no English prose in scan JSON of fixtures/benchmark + experiments/text-corpus."""

    def test_scan_json_text_fields_are_korean(self) -> None:
        offenders: list[str] = []
        for folder in (REPO_ROOT / "fixtures" / "benchmark", REPO_ROOT / "experiments" / "text-corpus"):
            options = AnalysisOptions(recursive=True)
            summary, items, thresholds = scan_folder(folder, options)
            self.assertGreater(len(items), 0, folder)
            payload = json.loads(json.dumps(scan_payload(summary, items, thresholds, options), ensure_ascii=False))
            for where, text in checked_strings(payload):
                run = english_prose(text)
                if run:
                    offenders.append(f"{folder.name}{where}: {run!r} in {text[:120]!r}")
        self.assertEqual(offenders, [], "\n".join(sorted(set(offenders))[:40]))

    def test_packaged_profiles_limitations_and_notes_are_korean(self) -> None:
        """Profile limitations/notes reach users verbatim (model_analysis, result limitations)."""
        offenders: list[str] = []
        for profile_path in sorted(MODELS_DIR.glob("*-runtime.json")):
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            for key in ("limitations", "notes", "reason"):
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
        import io
        import os
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
