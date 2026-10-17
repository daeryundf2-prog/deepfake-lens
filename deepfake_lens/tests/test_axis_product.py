"""P0R-3 (user decision 2026-10-17, risk-based exit): the path axes as one product table (HANDOFF §8.1, §8.3).

R23-1, R24-3, R25-4, R26-2 and R26-3 were all the same kind of regression: a fix tested one axis (a relative path,
a deep working folder, a non-ASCII name, four workers, an archive member) and broke another combination. Each test
fixed one axis. This table scans one case folder under every combination of

- the folder argument: absolute, relative to the working folder, or ``.`` (the working folder is the case folder) —
  an absolute name longer than PATH_MAX is not a usable name, so that one combination must be refused (exit 2,
  Korean reason) instead of scanned;
- the working folder: shallow, or deeper than PATH_MAX (entered one folder at a time; POSIX only);
- the case folder's name: ASCII or non-ASCII (``사건 폴더``);
- ``--workers`` 1 or 4;
- the files in a folder, or the same files inside a zip in the folder;

and requires every combination's rows — path, status, verdict, reason, coverage (check, status, reason) and evidence
titles — to be identical to the first combination of its folder/archive group. Scans run in-process through the CLI
(``cli.main``) with ``--pixel off`` to keep the table fast; the audio row runs where librosa is installed and is the
same skipped row everywhere it is not.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import shutil
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from deepfake_lens.cli import main

DEEP_LEVELS = 22  # 22 folders of 200 characters: > 4096 bytes (Linux PATH_MAX) and > 1024 (macOS)


def _png(path: Path, text: dict[str, str]) -> None:
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    Image.new("RGB", (64, 48), (40, 90, 160)).save(path, pnginfo=info)


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x10\x00\xf0" * 4000)


def _fill(folder: Path) -> None:
    folder.mkdir()
    _png(folder / "a1111.png", {"parameters": "a cat\nNegative prompt: x\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1"})
    _png(folder / "한글 사진.png", {})
    _wav(folder / "소리 a.wav")
    (folder / "note.txt").write_text("평범한 메모입니다. " * 20, encoding="utf-8")
    (folder / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)


def _rows(payload: dict) -> list[dict]:
    rows = []
    for item in payload["items"]:
        result = item.get("result") or {}
        rows.append({
            "path": item.get("path"), "member": item.get("member"), "status": item.get("status"),
            "error": item.get("error"), "verdict": result.get("verdict_code"),
            "coverage": [(c.get("check"), c.get("status"), c.get("reason")) for c in result.get("coverage") or []],
            "evidence": [e.get("title") for e in result.get("evidence") or []],
        })
    return sorted(rows, key=lambda row: (str(row["path"]), str(row["member"])))


class AxisProductTableTest(unittest.TestCase):
    maxDiff = None

    def _cd(self, base: Path, parts: list[str]) -> None:
        """Enter ``base/parts…`` one folder at a time (a folder deeper than PATH_MAX has no usable absolute name)."""
        os.chdir(base)
        for part in parts:
            os.chdir(part)

    def _scan(self, base: Path, cwd_parts: list[str], folder_arg: str | None, workers: int, out: Path) -> list[dict]:
        previous = os.getcwd()
        try:
            self._cd(base, cwd_parts)
            arg = folder_arg if folder_arg is not None else os.getcwd()
            if folder_arg is None:  # the absolute form: named from the parent
                os.chdir("..")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = main(["scan", arg, "--pixel", "off", "--workers", str(workers), "--format", "json", "--json-out", str(out)])
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0, err.getvalue()[:300])
        return _rows(json.loads(out.read_text(encoding="utf-8")))

    def _assert_refused(self, base: Path, cwd_parts: list[str]) -> None:
        previous = os.getcwd()
        try:
            self._cd(base, cwd_parts)
            arg = os.getcwd()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = main(["scan", arg, "--pixel", "off", "--format", "json"])
        finally:
            os.chdir(previous)
        self.assertEqual(code, 2)
        self.assertTrue(err.getvalue().startswith("오류: "), err.getvalue()[:200])
        self.assertNotIn("Traceback", err.getvalue())

    def _make(self, base: Path, parent_parts: list[str], name: str, layout: str) -> None:
        previous = os.getcwd()
        try:
            os.chdir(base)
            for part in parent_parts:
                os.mkdir(part)
                os.chdir(part)
            if layout == "folder":
                _fill(Path(name))
            else:
                _fill(Path("staging"))
                Path(name).mkdir()
                with zipfile.ZipFile(Path(name) / "evidence.zip", "w") as archive:
                    for file in sorted(Path("staging").iterdir()):
                        archive.write(file, file.name)
                shutil.rmtree("staging")
        finally:
            os.chdir(previous)

    def test_every_axis_combination_gives_the_same_rows(self) -> None:
        depths = ["shallow", "deep"] if os.name == "posix" else ["shallow"]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            out = base / "out.json"
            groups: dict[str, list[tuple[tuple, list[dict]]]] = {"folder": [], "archive": []}
            for index, (depth, name, layout) in enumerate(itertools.product(depths, ["case", "사건 폴더"], ["folder", "archive"])):
                parent = [f"p{index}"]
                if depth == "deep":
                    parent += [f"{level:02d}" + "d" * 198 for level in range(DEEP_LEVELS)]
                self._make(base, parent, name, layout)
                case = [*parent, name]
                forms = [("absolute", case, None), ("relative", parent, name), ("dot", case, ".")]
                for (form, cwd_parts, arg), workers in itertools.product(forms, [1, 4]):
                    if depth == "deep" and form == "absolute":
                        # An absolute name longer than PATH_MAX names no file the OS can open (ENAMETOOLONG): the
                        # combination is a usage error — exit 2 with the Korean reason, never a traceback or a scan.
                        self._assert_refused(base, cwd_parts)
                        continue
                    rows = self._scan(base, cwd_parts, arg, workers, out)
                    groups[layout].append(((depth, name, form, workers), rows))
            for layout, scans in groups.items():
                first_key, first_rows = scans[0]
                self.assertTrue(first_rows, layout)
                for key, rows in scans[1:]:
                    with self.subTest(layout=layout, combination=key, against=first_key):
                        self.assertEqual(rows, first_rows)


if __name__ == "__main__":
    unittest.main()
