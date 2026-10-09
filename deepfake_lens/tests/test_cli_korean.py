"""B8: scan PDF reports and the CLI's help/usage/errors are Korean.

- ``scan --pdf-out`` / ``--forensic-pdf-out`` without pymupdf: a Korean
  message naming the package, exit 2, checked before the scan starts (the
  R6 pattern of ``--evidence-statement-pdf-out``) — never an English
  Latin-1 PDF. With pymupdf ``--pdf-out`` writes the Korean forensic PDF.
- Every help string (description, epilog, subcommands, arguments) has no
  English prose; argparse's own words are Korean.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator
from unittest import mock

from deepfake_lens.cli import main
from deepfake_lens.cli_parser import build_parser, korean_argparse_error
from deepfake_lens.error_text import english_prose
from deepfake_lens.evidence_statement import PDF_REPORT_DEPENDENCY_MESSAGE
from deepfake_lens.pdf_backend import pymupdf_available

BENCHMARK = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark"


class ScanPdfDependencyTest(unittest.TestCase):
    """B8: pymupdf/fitz imports are blocked so this holds where pymupdf is installed too."""

    def setUp(self) -> None:
        patcher = mock.patch.dict(sys.modules, {"pymupdf": None, "fitz": None})
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        # N15: the CLI scans through scan_folder_run (scan_folder is the 2-tuple API).
        with mock.patch("deepfake_lens.cli.scan_folder_run", side_effect=AssertionError("scan must not start")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_pdf_out_and_forensic_pdf_out_refuse_before_scanning(self) -> None:
        for flag in ("--pdf-out", "--forensic-pdf-out"):
            with self.subTest(flag=flag):
                pdf = self.out / f"{flag.strip('-')}.pdf"
                code, stdout, stderr = self._run(["scan", str(BENCHMARK), flag, str(pdf)])
                self.assertEqual(code, 2)
                self.assertIn(PDF_REPORT_DEPENDENCY_MESSAGE, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertIsNone(english_prose(stderr), stderr)
                self.assertFalse(pdf.exists())
                self.assertEqual(stdout, "")


@unittest.skipUnless(pymupdf_available(), "pymupdf not installed")
class ScanPdfOutIsKoreanTest(unittest.TestCase):
    def test_pdf_out_writes_the_korean_report(self) -> None:
        from deepfake_lens.pdf_backend import import_pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "r.pdf"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["scan", str(BENCHMARK), "--pdf-out", str(pdf)]), 0)
            with import_pymupdf().open(str(pdf)) as doc:
                text = "\n".join(page.get_text() for page in doc)
        self.assertIn("디지털 포렌식 AI 감정보고서", text)
        self.assertIn("판정 임계값", text)
        for english in ("Latin-1", "UNDETERMINED", "Verdicts:", "Decision thresholds", "Signature:"):
            self.assertNotIn(english, text)


def _help_strings(parser: argparse.ArgumentParser, name: str) -> Iterator[tuple[str, str]]:
    yield f"{name} description", parser.description or ""
    yield f"{name} epilog", parser.epilog or ""
    for action in parser._actions:
        if action.help and action.help != argparse.SUPPRESS:
            yield f"{name} {'/'.join(action.option_strings) or action.dest}", action.help
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                yield f"{name} {choice.dest}", choice.help or ""
            for child_name, child in action.choices.items():
                yield from _help_strings(child, f"{name} {child_name}")


class CliHelpIsKoreanTest(unittest.TestCase):
    def test_every_help_string_is_korean(self) -> None:
        parser, subs = build_parser()
        strings = list(_help_strings(parser, "deepfake-lens"))
        self.assertGreater(len(strings), 400)
        self.assertGreaterEqual(len(subs), 45)
        offenders = [f"{where}: {text}" for where, text in strings if english_prose(text)]
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_rendered_help_uses_korean_argparse_words(self) -> None:
        parser, subs = build_parser()
        for name, sub in [("deepfake-lens", parser), *subs.items()]:
            with self.subTest(command=name):
                text = sub.format_help()
                self.assertTrue(text.startswith("사용법: "), text[:40])
                self.assertIn("이 도움말을 보여 주고 끝냄", text)
                for english in ("usage:", "show this help message", "positional arguments", "options:", "optional arguments"):
                    self.assertNotIn(english, text)

    def test_argparse_errors_are_korean(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            main(["scan", str(BENCHMARK), "--pixel", "bogus"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("오류: 인수 --pixel: 허용되지 않은 값 'bogus'", err.getvalue())
        self.assertNotIn("invalid choice", err.getvalue())
        for message, expected in (
            ("the following arguments are required: folder", "다음 인수가 필요합니다: folder"),
            ("unrecognized arguments: --nope", "알 수 없는 인수: --nope"),
            ("argument --max-files: invalid int value: 'x'", "인수 --max-files: 올바르지 않은 int 값 'x'"),
            ("argument --out: expected one argument", "인수 --out: 값 하나가 필요합니다"),
        ):
            with self.subTest(message=message):
                self.assertEqual(korean_argparse_error(message), expected)


class RoundNinePlaceholdersTest(unittest.TestCase):
    """R9-7 (round 9): `--install BUNDLE_DIR`, positional placeholders shown by their English
    dest (folder, file, file_a, report) and echoed input with raw newlines."""

    def _run(self, argv: list[str]) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        return code, err.getvalue()

    def test_usage_lines_have_no_latin_placeholder(self) -> None:
        import re

        parser, subs = build_parser()
        for name, sub in [("deepfake-lens", parser), *subs.items()]:
            with self.subTest(command=name):
                usage = sub.format_usage()
                tokens = re.findall(r"(?<![-\w{,])([A-Za-z][A-Za-z0-9_]*)(?![\w}-])", usage.replace(sub.prog, " "))
                # every remaining Latin token is an option flag (stripped above by the lookbehind on "-")
                self.assertEqual(tokens, [], usage)
        expected = {"scan": "<폴더>", "audio": "<파일>", "compare": "<파일A> <파일B>", "verify-report": "<보고서>",
                    "feedback": "<라벨 파일>", "evidence-statement": "<대상>", "multimodal": "[<파일> ...]"}
        for name, placeholder in expected.items():
            with self.subTest(command=name):
                self.assertIn(placeholder, subs[name].format_usage())
        self.assertIn("--install <묶음 폴더>", subs["vendor-weights"].format_help())
        self.assertNotIn("BUNDLE_DIR", subs["vendor-weights"].format_help())

    def test_missing_positional_is_named_in_korean(self) -> None:
        for argv, name in ((["scan"], "<폴더>"), (["compare", "a.txt"], "<파일B>"), (["verify-report"], "<보고서>")):
            with self.subTest(argv=argv):
                code, stderr = self._run(argv)
                self.assertEqual(code, 2)
                self.assertIn(f"오류: 다음 인수가 필요합니다: {name}", stderr)
                self.assertIsNone(english_prose(stderr), stderr)

    def test_echoed_input_is_one_line(self) -> None:
        from deepfake_lens.cli_parser import escape_echo

        self.assertEqual(escape_echo("a\nb\r\tc\x07d\u2028e"), "a\\nb\\r\\tc\\x07d\\u2028e")
        for argv, shown in (
            (["scan", "없는\n폴더"], "오류: 폴더를 찾을 수 없습니다: 없는\\n폴더"),
            (["scan", str(BENCHMARK), "--pixel", "tur\nbo"], "허용되지 않은 값 'tur\\nbo'"),
            (["forensic", "x\r\ny.png"], "x\\r\\ny.png"),
        ):
            with self.subTest(argv=argv):
                code, stderr = self._run(argv)
                self.assertEqual(code, 2)
                self.assertIn(shown, stderr)
                error_lines = [line for line in stderr.splitlines() if "오류:" in line]
                self.assertEqual(len(error_lines), 1, stderr)
                self.assertTrue(error_lines[0].rstrip().endswith(shown.split(": ")[-1]) or shown in error_lines[0], stderr)


if __name__ == "__main__":
    unittest.main()
