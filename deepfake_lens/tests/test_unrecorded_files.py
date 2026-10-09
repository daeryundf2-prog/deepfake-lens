"""X1 (round 7): files left out of a scan are never omitted silently.

``evidence-statement <folder>`` used a hard-coded 100-file, flat scan (a
105-file folder produced 100 entries, exit 0, no notice), and the HTML
report, the forensic PDF and the evidence-statement PDF never said that the
file cap was reached or that subfolders were skipped. Every rendered output
now carries a "기록되지 않은 파일" section with the count and the reasons,
and the evidence statement scans a folder with scan's options.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from deepfake_lens import cli
from deepfake_lens.result_text import UNRECORDED_SECTION_TITLE, UnrecordedFiles, unrecorded_files

FILES = 105


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class UnrecordedFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.folder = root / "case"
        (self.folder / "sub").mkdir(parents=True)
        for index in range(1, FILES + 1):
            (self.folder / f"n{index:03d}.txt").write_text(f"사람이 쓴 메모 {index}번입니다.", encoding="utf-8")
        (self.folder / "sub" / "inner.txt").write_text("하위 폴더의 메모입니다.", encoding="utf-8")
        (self.folder / "file.xyz").write_bytes(b"?")
        self.links = 0
        try:
            os.symlink("n001.txt", self.folder / "link.txt")
            self.links = 1
        except (OSError, NotImplementedError):
            pass
        self.out = root / "out"
        self.out.mkdir()
        home = root / "home"
        home.mkdir()
        saved = {key: os.environ.get(key) for key in ("HOME", "DEEPFAKE_LENS_LOG_DIR", "DEEPFAKE_LENS_REPORT_KEY")}
        os.environ["HOME"] = str(home)
        os.environ["DEEPFAKE_LENS_LOG_DIR"] = str(home / "logs")
        os.environ.pop("DEEPFAKE_LENS_REPORT_KEY", None)

        def restore() -> None:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)

    def _assert_section(self, text: str, *, over_cap: int, label: str) -> None:
        self.assertIn(UNRECORDED_SECTION_TITLE, text, label)
        self.assertIn("하위 폴더 미포함", text, label)
        self.assertIn("미지원", text, label)
        if over_cap:
            self.assertIn(f"검사하지 않은 파일 {over_cap}개", text, label)
        if self.links:
            self.assertIn("심볼릭 링크", text, label)

    def test_evidence_statement_folder_uses_scan_options(self) -> None:
        """X1: a 105-file folder yields 105 + rows (scan's default cap), flat scan noted; --max-files/--recursive apply."""
        md, js = self.out / "es.md", self.out / "es.json"
        code, stdout, _ = _run(["evidence-statement", str(self.folder), "--md-out", str(md), "--json-out", str(js)])
        self.assertEqual(code, 0)
        body = json.loads(js.read_text(encoding="utf-8"))
        self.assertEqual(len(body["entries"]), FILES + 1 + self.links)  # every txt + .xyz + link row
        self._assert_section(md.read_text(encoding="utf-8"), over_cap=0, label="md")
        self._assert_section(stdout, over_cap=0, label="table")
        self.assertEqual(body["unrecorded_files"]["reasons"][1], {"code": "subfolders", "label": "하위 폴더 미포함", "count": 1})

        capped = self.out / "capped.json"
        code, _, _ = _run(["evidence-statement", str(self.folder), "--max-files", "100", "--json-out", str(capped)])
        self.assertEqual(code, 0)
        body = json.loads(capped.read_text(encoding="utf-8"))
        self.assertEqual(len(body["entries"]), 100 + self.links)
        unrecorded = UnrecordedFiles.from_json(body["unrecorded_files"])
        assert unrecorded is not None
        self.assertEqual(unrecorded.counts["file_cap"], FILES + 1 - 100)
        self._assert_section("\n".join(body["unrecorded_files"]["lines"]), over_cap=FILES + 1 - 100, label="capped json")

        recursive = self.out / "rec.json"
        code, _, _ = _run(["evidence-statement", str(self.folder), "--recursive", "--json-out", str(recursive)])
        self.assertEqual(code, 0)
        body = json.loads(recursive.read_text(encoding="utf-8"))
        self.assertIn("sub/inner.txt", [entry["file_path"] for entry in body["entries"]])
        self.assertEqual(body["unrecorded_files"]["reasons"][1]["count"], 0)

        code, _, stderr = _run(["evidence-statement", str(self.folder), "--max-files", "0"])
        self.assertEqual(code, 2)
        self.assertTrue(stderr.startswith("오류:"), stderr)

    def test_every_scan_rendering_has_the_section(self) -> None:
        """X1: table, JSON, CSV, HTML, forensic/scan PDF and the evidence statement all state the cap and the reasons."""
        from deepfake_lens.pdf_backend import pymupdf_available

        outputs = {name: self.out / name for name in ("r.json", "r.csv", "r.html", "es.md", "es.json")}
        argv = [
            "scan", str(self.folder), "--max-files", "50",
            "--json-out", str(outputs["r.json"]), "--csv-out", str(outputs["r.csv"]), "--html-out", str(outputs["r.html"]),
            "--evidence-statement-out", str(outputs["es.md"]),
        ]
        pdf = pymupdf_available()
        if pdf:
            argv += ["--forensic-pdf-out", str(self.out / "f.pdf"), "--pdf-out", str(self.out / "p.pdf")]
        code, stdout, _ = _run(argv)
        self.assertEqual(code, 0)
        over_cap = FILES + 1 - 50
        payload = json.loads(outputs["r.json"].read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["files_over_cap"], over_cap)
        self.assertTrue(payload["summary"]["capped"])
        self._assert_section("\n".join(payload["unrecorded_files"]["lines"]), over_cap=over_cap, label="json")
        self._assert_section(stdout, over_cap=over_cap, label="table")
        csv_text = outputs["r.csv"].read_text(encoding="utf-8")
        header = [line for line in csv_text.splitlines() if line.startswith("#")]
        self.assertTrue(header[0].startswith(f"# {UNRECORDED_SECTION_TITLE}"), header)
        self.assertIn(f"파일 상한 {over_cap}", header[0])
        self.assertEqual(len(list(csv.DictReader(line for line in csv_text.splitlines() if not line.startswith("#")))), 50 + self.links)
        html = outputs["r.html"].read_text(encoding="utf-8")
        self.assertIn('id="unrecorded-files"', html)
        self._assert_section(html, over_cap=over_cap, label="html")
        self._assert_section(outputs["es.md"].read_text(encoding="utf-8"), over_cap=over_cap, label="statement md")
        if pdf:
            from deepfake_lens.pdf_backend import import_pymupdf

            pymupdf = import_pymupdf()
            for name in ("f.pdf", "p.pdf"):
                with pymupdf.open(self.out / name) as document:
                    text = "".join(page.get_text() for page in document)
                self._assert_section(text, over_cap=over_cap, label=name)
            statement_pdf = self.out / "es.pdf"
            code, _, _ = _run(["evidence-statement", str(outputs["r.json"]), "--pdf-out", str(statement_pdf)])
            self.assertEqual(code, 0)
            with pymupdf.open(statement_pdf) as document:
                text = "".join(page.get_text() for page in document)
            self._assert_section(text, over_cap=over_cap, label="statement pdf from scan json")

    def test_statement_from_scan_json_keeps_the_cap(self) -> None:
        """X1: a statement built from a capped scan JSON reports the over-cap count from its summary."""
        scan_json = self.out / "scan.json"
        self.assertEqual(_run(["scan", str(self.folder), "--max-files", "10", "--json-out", str(scan_json)])[0], 0)
        statement = self.out / "from-json.json"
        code, _, _ = _run(["evidence-statement", str(scan_json), "--json-out", str(statement)])
        self.assertEqual(code, 0)
        body = json.loads(statement.read_text(encoding="utf-8"))
        self.assertEqual(body["unrecorded_files"]["reasons"][0]["count"], FILES + 1 - 10)

    def test_legal_report_has_the_section(self) -> None:
        """X1: the legal report (JSON and text) carries the section — a symlink or unsupported file is counted."""
        out = self.out / "lr.json"
        code, stdout, _ = _run(["legal-report", str(self.folder / "n001.txt"), "--json-out", str(out)])
        self.assertEqual(code, 0)
        self.assertIn(f"=== {UNRECORDED_SECTION_TITLE} ===", stdout)
        self.assertIn("없음", stdout)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["unrecorded_files"]["total_files"], 0)
        if self.links:
            code, stdout, _ = _run(["legal-report", str(self.folder / "link.txt")])
            self.assertEqual(code, 0)
            self.assertIn("심볼릭 링크: ", stdout)

    def test_old_scan_json_without_count(self) -> None:
        """A capped summary without files_over_cap (pre-X1 JSON) still says the cap was reached."""
        unrecorded = unrecorded_files([], {"capped": True})
        self.assertTrue(unrecorded.cap_reached)
        self.assertIn("개수 미기록", unrecorded.headline())
        self.assertIn("파일 수 상한(--max-files)에 도달", "\n".join(unrecorded.reason_lines()))
        self.assertTrue(unrecorded_files([]).empty)


if __name__ == "__main__":
    unittest.main()
