"""The GUI's evidence-statement preview on the server-held scan job (merge of phase0 P0R-6).

P0R-6 builds every report from the scan job the server ran and holds — the request names ``scan_job``,
an ordered ``exhibit_paths`` selection of the job's rows, ``side`` (plaintiff only, BL-15) and the case
fields. ``format=evidence-preview`` (GUI redesign) is the evidence statement's PDF texts as JSON, built
from the same job, the same selection and the same statement build as ``format=evidence`` — so the
preview the examiner reads is the PDF that is exported: every text as the PDF draws it (R23-4 escapes,
R24-7/R25-3 case fields), the numbered exhibits in the chosen order and every other job row after them
unnumbered (「서증 외(검사 행)」). The tests of the earlier posted-row design (appendix, submitter_side,
evidence-json, excluded-rows headers, posted-row validation) are listed in docs/TEST-DELETIONS.md.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import webapp_api
from deepfake_lens.evidence_statement import NOT_AN_EXHIBIT
from deepfake_lens.tests.report_jobs import scan_job_for


def _folder(tmp: str) -> Path:
    folder = Path(tmp) / "case"
    folder.mkdir()
    for index in range(4):
        (folder / f"note{index}.txt").write_text(f"사람이 쓴 글 {index}번입니다.", encoding="utf-8")
    (folder / "odd.xyz").write_bytes(b"?")
    import zipfile

    with zipfile.ZipFile(folder / "evid.zip", "w") as archive:
        archive.writestr("inner/memo.txt", "압축 안의 글입니다.")
    return folder


class EvidencePreviewOnTheJobTest(unittest.TestCase):
    def setUp(self) -> None:
        roots = mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = _folder(self._tmp.name)
        webapp_api.configure_read_roots(self.folder)

    def _job(self) -> str:
        return scan_job_for(self.folder, query="&no_default_engine=true")

    def _report(self, fmt: str, **fields: object) -> Any:
        return webapp_api._report_payload(json.dumps(fields, ensure_ascii=False).encode("utf-8"), fmt, default_folder=self.folder)

    def _rows(self, job: str) -> list[str]:
        signed = self._report("json", scan_job=job)
        assert isinstance(signed, dict), signed
        return [row["path"] for row in signed["items"]]

    def test_the_preview_numbers_the_chosen_rows_in_order_and_keeps_every_other_row(self) -> None:
        job = self._job()
        rows = self._rows(job)
        chosen = [path for path in rows if path.endswith(".txt")][:2][::-1]
        preview = self._report("evidence-preview", scan_job=job, exhibit_paths=chosen, side="plaintiff")
        assert isinstance(preview, dict), preview
        entries = preview["entries"]
        self.assertEqual([entry["file_path"] for entry in entries[:2]], chosen)
        self.assertEqual([entry["exhibit_no"] for entry in entries[:2]], ["갑 제1호증", "갑 제2호증"])
        self.assertEqual(sorted(entry["file_path"] for entry in entries), sorted(rows))
        self.assertTrue(all(entry["exhibit_no"] == NOT_AN_EXHIBIT for entry in entries[2:]), [entry["exhibit_no"] for entry in entries])
        self.assertEqual(preview["unrecorded_listing"]["source"], "scan_job")

    def test_the_preview_is_the_exported_pdf(self) -> None:
        """Every case line, table cell, box line and signoff line of the preview is in the evidence PDF."""
        from deepfake_lens.pdf_backend import import_pymupdf, pymupdf_available

        if not pymupdf_available():
            self.skipTest("pymupdf not installed")
        job = self._job()
        name = "note עברית.txt"  # R23-4: a name the PDF font cannot draw
        (self.folder / name).write_text("히브리 이름 파일입니다.", encoding="utf-8")
        job = self._job()
        chosen = [name, "note0.txt"]
        fields = {"scan_job": job, "exhibit_paths": chosen, "side": "plaintiff", "case_no": "2026가단1",
                  "case_name": "시험 x|y", "plaintiff": "\\u05e2 corp", "law_firm": "법무법인 ע"}
        preview = self._report("evidence-preview", **fields)
        pdf = self._report("evidence", **fields)
        assert isinstance(preview, dict) and isinstance(pdf, bytes), (preview, pdf)
        self.assertTrue(preview["pdf_glyph_escapes"])
        self.assertEqual(preview["entries"][0]["file_path"], name)  # the row identity stays the real name
        self.assertIn("\\u05e2", preview["entries"][0]["document_name"])
        self.assertIn("\\\\u05e2 corp", preview["case_lines"][1])  # R24-7: a typed backslash is doubled
        self.assertIn("x|y", preview["case_lines"][0])  # R25-3: "|" as typed
        with import_pymupdf().open(stream=pdf, filetype="pdf") as doc:
            text = "".join("".join(page.get_text().split()) for page in doc)
        lines = [*preview["case_lines"], *(line for _, box in preview["boxes"] for line in box), *preview["signoff"], preview["court"]]
        cells = [cell for entry in preview["entries"] for cell in (entry["exhibit_no"], entry["document_name"], entry["author_date"], entry["purpose_of_proof"])]
        for line in [*lines, *cells]:
            self.assertIn("".join(line.split()), text, line)

    def test_the_preview_does_not_rehash_but_the_export_refuses_a_changed_file(self) -> None:
        """The preview runs on every selection change, so it does not re-hash the folder; the export does (409)."""
        job = self._job()
        (self.folder / "note1.txt").write_text("검사 뒤에 바뀐 글", encoding="utf-8")
        preview = self._report("evidence-preview", scan_job=job, side="plaintiff")
        assert isinstance(preview, dict), preview
        self.assertNotIn("error", preview)
        refused = self._report("json", scan_job=job)
        assert isinstance(refused, dict)
        self.assertEqual(webapp_api.report_error_status(refused), 409)
        self.assertEqual([row["path"] for row in refused["changed_rows"]], ["note1.txt"])
        self.assertTrue(refused["changed_rows"][0]["reason"])

    def test_the_preview_takes_the_requests_rules(self) -> None:
        """P0R-6's request rules hold for the preview: posted rows are an unknown field, the defendant side
        waits for BL-15, and an unknown job is 404."""
        job = self._job()
        cases = [
            ({"scan_job": job, "items": []}, 400),
            ({"scan_job": job, "side": "defendant"}, 400),
            ({"scan_job": "0" * 32}, 404),
            ({"scan_job": job, "exhibit_paths": ["nope.txt"]}, 400),
        ]
        for fields, status in cases:
            with self.subTest(fields=fields):
                out = self._report("evidence-preview", **fields)
                assert isinstance(out, dict), out
                self.assertEqual(webapp_api.report_error_status(out), status, out)

    def test_an_upload_only_preview_lists_the_uploads_unsigned(self) -> None:
        upload = {"path": "올린.txt", "name": "올린.txt", "kind": "text", "status": "failed", "source": "upload", "error": "x"}
        preview = self._report("evidence-preview", uploads=[upload])
        assert isinstance(preview, dict), preview
        self.assertEqual(preview["entries"], [])
        self.assertIn("올린.txt", "\n".join(preview["unsigned"]))
        self.assertEqual(preview["unrecorded_listing"]["source"], "unknown")

    def test_unsupported_format_lists_every_format(self) -> None:
        out = webapp_api.report_format(b"{}", "nope")
        assert isinstance(out, dict)
        for name in webapp_api.REPORT_FORMATS:
            self.assertIn(f"`{name}`", out["error"])
        self.assertIn("evidence-preview", webapp_api.REPORT_FORMATS)


if __name__ == "__main__":
    unittest.main()
