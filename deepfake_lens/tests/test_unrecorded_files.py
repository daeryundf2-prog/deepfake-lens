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

    def test_p5_refused_archive_members_are_unrecorded_files(self) -> None:
        """P5 (round 8): legal-report on a zip bomb said "없음 — 모든 파일에 분석 결과가 있습니다";
        refused members (bomb, path escape, link) are counted with their categories."""
        import stat
        import zipfile

        arc = self.out / "evil.zip"
        with zipfile.ZipFile(arc, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.writestr("zeros.bin", bytes(8 * 1024 * 1024))
            zf.writestr("ok.txt", "정상 구성 파일입니다. 사람이 쓴 짧은 메모입니다.")
            zf.writestr("../escape.txt", "밖으로 나가려는 멤버")
            link = zipfile.ZipInfo("link.txt")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(link, "ok.txt")
        out = self.out / "lr-zip.json"
        code, stdout, _ = _run(["legal-report", str(arc), "--json-out", str(out)])
        self.assertEqual(code, 0)
        body = json.loads(out.read_text(encoding="utf-8"))["unrecorded_files"]
        counts = {entry["code"]: entry["count"] for entry in body["reasons"]}
        self.assertEqual(counts["archive_rejected"], 3)
        self.assertEqual(body["total_files"], 3)
        self.assertEqual(body["archive_rejected_by_category"], {"압축 폭탄·예산 초과": 1, "경로 이탈": 1, "링크·특수 파일": 1})
        self.assertNotIn("없음", "\n".join(body["lines"]))
        self.assertIn("압축 거부 멤버: 압축 파일에서 거부되어 추출·분석하지 않은 구성 파일 3개", stdout)
        self.assertNotIn("모든 파일에 분석 결과가 있습니다", stdout)

    def test_p5_flat_scan_counts_the_files_inside_skipped_subfolders(self) -> None:
        """P5 (round 8): a flat scan counted the subfolders it skipped but not the files inside;
        each subfolder's files are counted recursively (symlinks neither followed nor counted)."""
        (self.folder / "sub" / "deeper").mkdir()
        (self.folder / "sub" / "deeper" / "a.txt").write_text("더 깊은 메모", encoding="utf-8")
        (self.folder / "sub2").mkdir()
        for name in ("b.txt", "c.txt"):
            (self.folder / "sub2" / name).write_text("두 번째 하위 폴더", encoding="utf-8")
        outside = self.out / "elsewhere"
        outside.mkdir()
        (outside / "not-counted.txt").write_text("링크 대상", encoding="utf-8")
        with contextlib.suppress(OSError, NotImplementedError):
            os.symlink(outside, self.folder / "sub2" / "linked")
            os.symlink("b.txt", self.folder / "sub2" / "b-link.txt")
        scan_json = self.out / "flat.json"
        code, stdout, _ = _run(["scan", str(self.folder), "--json-out", str(scan_json), "--max-files", "500"])
        self.assertEqual(code, 0)
        payload = json.loads(scan_json.read_text(encoding="utf-8"))
        summary = payload["summary"]
        self.assertEqual(summary["subfolders_skipped"], 2)
        self.assertEqual(summary["subfolder_files_skipped"], 4)  # sub: inner.txt, deeper/a.txt; sub2: b.txt, c.txt
        self.assertEqual(summary["subfolders_skipped_detail"], [
            {"path": "sub", "files": 2, "complete": True}, {"path": "sub2", "files": 2, "complete": True},
        ])
        lines = "\n".join(payload["unrecorded_files"]["lines"])
        self.assertIn("하위 폴더 안 파일: 검사하지 않은 하위 폴더 안의 파일 4개(심볼릭 링크 제외)", lines)
        self.assertIn("폴더별: sub 2개, sub2 2개", lines)
        self.assertIn("폴더별: sub 2개, sub2 2개", stdout)
        self.assertIn("그 안의 파일 4개 미검사", stdout)
        statement = self.out / "flat-es.json"
        self.assertEqual(_run(["evidence-statement", str(scan_json), "--json-out", str(statement)])[0], 0)
        body = json.loads(statement.read_text(encoding="utf-8"))["unrecorded_files"]
        self.assertEqual({entry["code"]: entry["count"] for entry in body["reasons"]}["subfolder_files"], 4)
        code, _, _ = _run(["scan", str(self.folder), "--recursive", "--json-out", str(scan_json), "--max-files", "500"])
        self.assertEqual(json.loads(scan_json.read_text(encoding="utf-8"))["summary"]["subfolder_files_skipped"], 0)

    def test_p5_subfolder_count_stops_at_the_walk_limit(self) -> None:
        """P5 (round 8): the subfolder count is bounded by the walk limits and says so."""
        from unittest import mock

        from deepfake_lens import scan_cache

        (self.folder / "sub" / "deeper").mkdir()
        (self.folder / "sub" / "deeper" / "a.txt").write_text("더 깊은 메모", encoding="utf-8")
        with mock.patch.object(scan_cache, "MAX_WALK_DIRS", 1):
            detail = scan_cache.subfolder_file_counts(self.folder)
        self.assertEqual(detail, [{"path": "sub", "files": 1, "complete": False}])
        text = "\n".join(unrecorded_files([], {"subfolders_skipped": 1, "subfolder_files_skipped": 1, "subfolders_skipped_detail": detail}).lines())
        self.assertIn("sub 1개 이상(탐색 상한으로 일부만 셈)", text)

    @unittest.skipIf(os.name == "nt", "symbolic links to folders need privileges on Windows")
    def test_r9_2_flat_allow_symlinks_counts_each_folder_once(self) -> None:
        """R9-2 (round 9): a flat --allow-symlinks scan's subfolder count followed every folder
        link — "/" walked the whole file system (146 800 files, a different number each run),
        "..", "." and a link to the folder itself re-counted it, 50 links to one folder counted
        it 50 times. The count now applies the P3 walker's rules and is deterministic."""
        from deepfake_lens import scan_cache
        from deepfake_lens.core import scan_directory

        (self.folder / "sub" / "deeper").mkdir()
        (self.folder / "sub" / "deeper" / "a.txt").write_text("더 깊은 메모", encoding="utf-8")
        outside = self.out / "elsewhere"
        (outside / "inner").mkdir(parents=True)
        for name in ("x.txt", "y.txt", "inner/z.txt"):
            (outside / name).write_text("링크 대상 폴더의 파일", encoding="utf-8")
        ancestors = {"to-root": "/", "to-parent": "..", "to-dot": ".", "to-self": str(self.folder)}
        for name, target in ancestors.items():
            os.symlink(target, self.folder / name)
        os.symlink("self-loop", self.folder / "self-loop")
        os.symlink(self.folder / "sub" / "deeper", self.folder / "into-sub")
        # a link into a folder another link also reaches: links are counted in
        # name order, every folder once — "into-linked" (inner) before "many00"
        os.symlink(outside / "inner", self.folder / "into-linked")
        for index in range(50):
            os.symlink(outside, self.folder / f"many{index:02d}")
        expected_detail = [
            {"path": "into-linked", "files": 1, "complete": True},
            # x.txt, y.txt — inner/ already counted. R10-8: the entry says so
            # (already_counted_folders, shown "(이미 센 폴더 1개 중복 제외)"); it
            # read "many00 2개" for a folder of 3 files with no hint.
            {"path": "many00", "files": 2, "complete": True, "already_counted_folders": 1},
            {"path": "sub", "files": 2, "complete": True},
        ]
        refused = {name: scan_cache.SYMLINK_ANCESTOR_REASON for name in ancestors}
        refused.update({f"many{index:02d}": scan_cache.SYMLINK_DUPLICATE_REASON for index in range(1, 50)})
        refused["into-sub"] = scan_cache.SYMLINK_DUPLICATE_REASON  # sub/deeper: counted with the real "sub"
        survey = scan_cache.flat_subfolder_survey(self.folder, follow_links=True)
        self.assertEqual(survey.detail, expected_detail)
        self.assertEqual({path.name: reason for path, reason in survey.refused}, refused)
        self.assertEqual(scan_cache.count_subfolders(self.folder, follow_links=True), 3)
        self.assertEqual(scan_cache.subfolder_file_counts(self.folder, follow_links=True), expected_detail)
        # without --allow-symlinks no link is a subfolder (each is a symlink row)
        self.assertEqual(scan_cache.flat_subfolder_survey(self.folder).detail, [{"path": "sub", "files": 2, "complete": True}])
        runs = []
        for _ in range(2):
            summary, items = scan_directory(self.folder, recursive=False, allow_symlinks=True, max_files=500)
            rows = {item.path: (item.status, item.error) for item in items}
            for name, reason in refused.items():
                self.assertEqual(rows[name], ("skipped", reason), name)
            self.assertEqual(rows["self-loop"], ("skipped", scan_cache.SYMLINK_LOOP_REASON))
            self.assertEqual((summary.subfolders_skipped, summary.subfolder_files_skipped), (3, 5))
            self.assertEqual(summary.subfolders_skipped_detail, expected_detail)
            counts = unrecorded_files(list(items), summary).counts
            self.assertEqual(counts["symlink"], len(refused) + 1)  # + self-loop (link.txt is followed: analyzed)
            runs.append([item.to_json() for item in items])
        self.assertEqual(runs[0], runs[1], "the same folder gives the same rows on every run")
        scan_json = self.out / "flat-links.json"
        self.assertEqual(_run(["scan", str(self.folder), "--allow-symlinks", "--max-files", "500", "--json-out", str(scan_json)])[0], 0)
        cli_summary = json.loads(scan_json.read_text(encoding="utf-8"))["summary"]
        self.assertEqual(cli_summary["subfolder_files_skipped"], 5)
        self.assertEqual(cli_summary["subfolders_skipped_detail"], expected_detail)
        lines = "\n".join(unrecorded_files([], cli_summary).lines())
        self.assertIn("into-linked 1개, many00 2개(이미 센 폴더 1개 중복 제외), sub 2개", lines)

    def test_p5_none_only_when_nothing_is_missing(self) -> None:
        """P5 (round 8): "없음" only when every count is 0 — a failed row without a result counts."""
        failed = unrecorded_files([{"path": "locked", "status": "failed", "result": None, "error": "읽기 실패"}])
        self.assertFalse(failed.empty)
        self.assertEqual(failed.total_files, 1)
        self.assertIn("처리 실패: 읽기 실패 등으로 분석 결과가 없는 행 1개", "\n".join(failed.lines()))
        self.assertTrue(unrecorded_files([{"path": "a.txt", "status": "analyzed", "result": {"coverage": []}}]).empty)
        roundtrip = UnrecordedFiles.from_json(failed.to_json())
        assert roundtrip is not None
        self.assertEqual(roundtrip.to_json(), failed.to_json())

    def test_old_scan_json_without_count(self) -> None:
        """A capped summary without files_over_cap (pre-X1 JSON) still says the cap was reached."""
        unrecorded = unrecorded_files([], {"capped": True})
        self.assertTrue(unrecorded.cap_reached)
        self.assertIn("개수 미기록", unrecorded.headline())
        self.assertIn("파일 수 상한(--max-files)에 도달", "\n".join(unrecorded.reason_lines()))
        self.assertTrue(unrecorded_files([]).empty)


if __name__ == "__main__":
    unittest.main()
