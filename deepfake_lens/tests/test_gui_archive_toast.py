"""R31-5 (round 31): the quick check's archive toast says what was analyzed.

``runQuickCheck`` (gui.js) toasted 「압축 해제 — N개 파일 분석됨」 with N = the number of rows of the ``/api/check``
answer: the container row and unsupported members were counted, and a corrupt archive (PNG bytes named
``corrupt.zip``) or an empty one read 「압축 해제 — 1개 파일 분석됨」 — nothing was extracted or analyzed. The toast
now comes from ``archiveCheckToast(items)``: the member rows whose status is ``analyzed``, and when there are none
whether the archive could not be opened (its ``archive`` check failed or was skipped) or held nothing analyzed.

The rows are the server's own (``webapp_api._check_file_payload`` on real archives), run through the GUI function
in node (or JXA on macOS).
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from deepfake_lens.tests.test_gui_model_score import GUI_JS, _function, _js_engine


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return out.getvalue()


def _check_rows(name: str, data: bytes) -> list[dict]:
    from deepfake_lens.webapp_api import _check_file_payload

    body = (b'--r31toast\r\nContent-Disposition: form-data; name="file"; filename="' + name.encode() + b'"\r\n\r\n'
            + data + b"\r\n--r31toast--\r\n")
    payload = _check_file_payload("multipart/form-data; boundary=r31toast", body)
    assert payload.get("mode") == "files", payload
    return payload["items"]


class ArchiveToastSourceTest(unittest.TestCase):
    def test_the_toast_is_not_the_row_count(self) -> None:
        source = GUI_JS.read_text(encoding="utf-8")
        self.assertNotIn("(data.items || []).length}개 파일 분석됨", source)
        self.assertIn("toast(archiveCheckToast(data.items || []));", source)


@unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
class ArchiveToastBehaviourTest(unittest.TestCase):
    def test_the_toast_counts_analyzed_members_and_names_an_archive_that_did_not_open(self) -> None:
        from deepfake_lens.tests.png_fixture import write_png

        with tempfile.TemporaryDirectory() as tmp:
            write_png(Path(tmp) / "p.png", {})
            png = (Path(tmp) / "p.png").read_bytes()
        text = "평범한 글입니다. 여러 문장이 있습니다.".encode("utf-8")
        cases = {
            "corrupt.zip": (png, "압축 파일을 열지 못했습니다 — 원장에서 사유를 보십시오"),
            "empty.zip": (_zip([]), "압축 파일에서 분석한 구성 파일이 없습니다 — 원장에서 사유를 보십시오"),
            "eml-only.zip": (_zip([("mail.eml", b"From: a\n\nhi")]), "압축 파일에서 분석한 구성 파일이 없습니다 — 원장에서 사유를 보십시오"),
            "two.zip": (_zip([("a.png", png), ("t.txt", text)]), "압축 해제 — 구성 파일 2개 분석됨"),
            "png-eml.zip": (_zip([("a.png", png), ("mail.eml", b"From: a\n\nhi")]),
                            "압축 해제 — 구성 파일 1개 분석됨, 1개 분석 못 함 — 원장에서 사유를 보십시오"),
            "nested.zip": (_zip([("inner.zip", _zip([("b.png", png)])), ("a.png", png)]), "압축 해제 — 구성 파일 2개 분석됨"),
        }
        rows = {name: _check_rows(name, data) for name, (data, _toast) in cases.items()}
        # The rows the old toast counted: corrupt/empty 1, two-member 3 (the bug's 「3개 파일 분석됨」).
        self.assertEqual((len(rows["corrupt.zip"]), len(rows["empty.zip"]), len(rows["two.zip"])), (1, 1, 3))
        engine = _js_engine()
        assert engine is not None
        body = _function("archiveCheckToast") + f"var rows = {json.dumps(rows, ensure_ascii=False)};\n"
        body += "var out = Object.keys(rows).map(function (k) { return k + '\\t' + archiveCheckToast(rows[k]); });\n"
        script = f"{body}console.log(out.join('\\n'));" if engine[0] == "node" else f"function run() {{\n{body}return out.join('\\n');\n}}"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "toast.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([*engine, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        got = dict(line.split("\t", 1) for line in done.stdout.strip().splitlines())
        self.assertEqual(got, {name: toast for name, (_data, toast) in cases.items()})


if __name__ == "__main__":
    unittest.main()
