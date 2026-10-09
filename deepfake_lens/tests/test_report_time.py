"""R12-9 (round 12): every report timestamp is ISO 8601 with the UTC offset.

Before R12-9 the legal report's ``generated_at`` and "분석 일시", the PDF
report's "감정 일시" (always labelled "(KST)"), the forensic and evidence
records, the vendor-weights report and batch jobs wrote local wall-clock
time without a zone. ``report_time.report_timestamp`` is the one format:
``2026-10-10T16:04:22+09:00``.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deepfake_lens.report_time import report_timestamp

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
A1111 = REPO / "fixtures" / "benchmark" / "a1111-metadata-marker.png"
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None
ISO_WITH_OFFSET = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d")
# Zones with distinct offsets (the verifier's TZ sweep): (TZ, offsets it can have).
ZONES = (
    ("Asia/Seoul", {"+09:00"}),
    ("UTC", {"+00:00"}),
    ("America/St_Johns", {"-03:30", "-02:30"}),
    ("Pacific/Kiritimati", {"+14:00"}),
)
# Naive clock reads that are not report timestamps, with the reason.
NAIVE_NOW_ALLOWED = {
    "report_time.py": "the helper itself (made aware with astimezone())",
    "evidence_rules.py": "compared with the naive EXIF DateTimeOriginal (camera-local clock), never printed",
    "evidence_statement.py": "제출일자 — the court filing date \"YYYY. MM. DD.\" (a date, no time of day)",
}


class ReportTimestampUnitTest(unittest.TestCase):
    def test_format(self) -> None:
        seoul = timezone(timedelta(hours=9))
        moment = datetime(2026, 10, 10, 16, 4, 22, 123456, tzinfo=seoul)
        self.assertEqual(report_timestamp(moment), moment.astimezone().isoformat(timespec="seconds"))
        for value in (None, moment, datetime(2026, 1, 2, 3, 4, 5), 1_791_000_000.5):
            with self.subTest(value=value):
                self.assertRegex(report_timestamp(value), f"^{ISO_WITH_OFFSET.pattern}$")
        self.assertEqual(datetime.fromisoformat(report_timestamp(moment)), moment.replace(microsecond=0))


class ReportTimestampZonesTest(unittest.TestCase):
    """The legal report under several TZ settings (child processes, POSIX TZ)."""

    def test_legal_report_generated_at_has_the_offset(self) -> None:
        if not hasattr(__import__("time"), "tzset"):
            self.skipTest("TZ needs time.tzset (POSIX)")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for zone, offsets in ZONES:
                with self.subTest(zone=zone):
                    out_json, out_text = base / f"{zone.replace('/', '_')}.json", base / f"{zone.replace('/', '_')}.txt"
                    env = {**os.environ, "TZ": zone, "HOME": str(base / "home"), "PYTHONPATH": str(REPO)}
                    env.pop("DEEPFAKE_LENS_REPORT_KEY", None)
                    done = subprocess.run(
                        [sys.executable, "-m", "deepfake_lens", "legal-report", str(A1111), "--json-out", str(out_json), "--output", str(out_text)],
                        capture_output=True, env=env, timeout=600, cwd=tmp,
                    )
                    self.assertEqual(done.returncode, 0, done.stderr[-600:])
                    report = json.loads(out_json.read_text(encoding="utf-8"))
                    stamp = report["generated_at"]
                    self.assertRegex(stamp, f"^{ISO_WITH_OFFSET.pattern}$")
                    self.assertIn(stamp[-6:], offsets, stamp)
                    self.assertIn(f"분석 일시: {stamp}", out_text.read_text(encoding="utf-8"))


@unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF generation")
class PdfReportTimestampTest(unittest.TestCase):
    def test_forensic_pdf_header_has_the_offset_and_no_fixed_zone_label(self) -> None:
        from deepfake_lens.core import scan_directory
        from deepfake_lens.pdf_backend import import_pymupdf
        from deepfake_lens.reports import write_forensic_pdf_report

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "case"
            folder.mkdir()
            (folder / "a.png").write_bytes(A1111.read_bytes())
            summary, items = scan_directory(folder)
            pdf = Path(tmp) / "r.pdf"
            write_forensic_pdf_report(pdf, summary, items)
            pymupdf = import_pymupdf()
            with pymupdf.open(str(pdf)) as doc:
                text = "".join(page.get_text() for page in doc)
        match = re.search(r"감정 일시:\s*(\S+)", text)
        self.assertIsNotNone(match, text[:400])
        assert match is not None
        self.assertRegex(match.group(1), f"^{ISO_WITH_OFFSET.pattern}$")
        self.assertNotIn("(KST)", text)


class NoNaiveReportClockTest(unittest.TestCase):
    """No module builds a report time from a naive clock read (AST)."""

    def test_only_listed_modules_read_a_naive_clock(self) -> None:
        offenders: list[str] = []
        for source in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                owner = node.func.value
                name = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else ""
                call = f"{name}.{node.func.attr}"
                naive_now = call in ("datetime.now", "datetime.today", "date.today") and not node.args and not node.keywords
                if call in ("time.localtime", "time.strftime", "time.ctime", "time.asctime") or (naive_now and source.name not in NAIVE_NOW_ALLOWED):
                    offenders.append(f"{source.name}:{node.lineno} {call}()")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
