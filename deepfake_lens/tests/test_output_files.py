"""R18-15 (round 18): output files are written whole, and output failures are Korean lines with an exit code.

R19-6 (round 19): the docs said every report/JSON output was written whole, but
benchmark --md-out, corpus build --out, dataset --manifest-out/--audit-out/…,
batch --output, eval --false-positive-out/--false-negative-out and the
calibration/fusion/training/perf/release/security/registry outputs were still
written in place (a full disk: "예기치 않은 오류(OSError)"), and the shared
writer removed its temp file on OSError only — a Ctrl-C during the write left
".<name>.<pid>.<id>.tmp" beside the output. Every writer goes through
output_files now (a meta-test keeps it so) and any exception removes the temp file.

The verifier saw ``scan … | head`` end with "예기치 않은 오류(BrokenPipeError)"
(exit 1), ``--json-out /dev/full`` fail after the whole scan with "예기치
않은 오류(OSError)", a closed standard output (``>&-``) lose the JSON with
exit 0, and ``--json-out`` written in place (an interruption left the old
report truncated).
"""

from __future__ import annotations

import ast
import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import output_files
from deepfake_lens.output_files import BROKEN_PIPE_EXIT, OutputWriteError, write_output_text

REPO = Path(__file__).resolve().parents[2]
TIMEOUT = 300


class WholeFileTest(unittest.TestCase):
    def test_a_failed_write_leaves_the_old_report_and_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "report.json")
            target.write_text('{"old": true}\n', encoding="utf-8")
            with mock.patch.object(output_files.os, "fsync", side_effect=OSError(errno.ENOSPC, "No space left on device")):
                with self.assertRaises(OutputWriteError) as caught:
                    write_output_text(target, '{"new": true}\n')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(sorted(os.listdir(tmp)), ["report.json"])
        self.assertEqual(str(caught.exception), f"출력 파일을 쓸 수 없습니다: {target} (디스크 공간이 부족합니다(오류 번호 28))")

    def test_an_interrupted_write_leaves_the_old_report_and_no_temp_file(self) -> None:
        """R19-6: KeyboardInterrupt (Ctrl-C) and SystemExit (SIGTERM's handler) during the write."""
        for raised in (KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(raised=type(raised).__name__), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp, "r.json")
                target.write_text('{"old": true}\n', encoding="utf-8")
                with mock.patch.object(output_files.os, "fsync", side_effect=raised):
                    with self.assertRaises(type(raised)):
                        write_output_text(target, '{"new": true}\n')
                self.assertEqual(target.read_text(encoding="utf-8"), '{"old": true}\n')
                self.assertEqual(sorted(os.listdir(tmp)), ["r.json"])

    def test_a_link_is_followed_and_a_new_file_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp, "real.json")
            real.write_text("old", encoding="utf-8")
            link = Path(tmp, "link.json")
            link.symlink_to(real)
            write_output_text(link, "new")
            self.assertTrue(link.is_symlink())
            self.assertEqual(real.read_text(encoding="utf-8"), "new")
            write_output_text(Path(tmp, "sub", "fresh.json"), "x")
            self.assertEqual(Path(tmp, "sub", "fresh.json").read_text(encoding="utf-8"), "x")


# R19-6 (round 19): the only places in the package that open a file for writing outside output_files —
# each a temp file renamed into place or removed on any exception, a file in the session folder, or an
# append-only log. {module: number of such calls}.
WRITE_SITES_ALLOWED = {
    "output_files.py": 2,  # the writer: a special file in place, the temp file
    "archives.py": 1,  # extracted members, in the session folder
    "native_path.py": 1,  # the copy marker of a staged name (session folder)
    "model_assets.py": 2,  # the verified asset copy (session folder); assets.json temp + os.replace
    "vendor_weights.py": 2,  # the download ".part" and the pin temp, both + os.replace
    "webapp_api.py": 2,  # an upload's temp copy (session folder); feedback.jsonl append
    "audio_worker.py": 2,  # native-stderr.log append (the worker's stderr); the worker's reply pipe (fd 1)
    "security.py": 2,  # the security self-check's fixture files in its temp folder
}
WRITE_MODES = ("w", "a", "x", "+")


def write_sites(tree: ast.AST) -> int:
    """Calls that write a file: .write_text / .write_bytes, open / .open / os.fdopen with a write mode."""
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in ("write_text", "write_bytes"):
            count += 1
            continue
        if name not in ("open", "fdopen"):
            continue
        position = 0 if isinstance(func, ast.Attribute) and name == "open" and not (isinstance(func.value, ast.Name) and func.value.id in ("os", "io")) else 1
        mode = next((kw.value for kw in node.keywords if kw.arg == "mode"), node.args[position] if len(node.args) > position else None)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(flag in mode.value for flag in WRITE_MODES):
            count += 1
    return count


class EveryWriterIsWholeMetaTest(unittest.TestCase):
    def test_no_other_module_writes_a_file(self) -> None:
        package = REPO / "deepfake_lens"
        found = {}
        for source in sorted(package.rglob("*.py")):
            if "tests" in source.relative_to(package).parts:
                continue
            count = write_sites(ast.parse(source.read_text(encoding="utf-8")))
            if count:
                found[source.relative_to(package).as_posix()] = count
        self.assertEqual(found, WRITE_SITES_ALLOWED)

    def test_the_visitor_sees_each_form(self) -> None:
        tree = ast.parse(
            "p.write_text('x')\nopen(p, 'wb')\nopen(p)\nPath(p).open('a')\nos.fdopen(fd, 'w')\nos.fdopen(fd, 'rb')\nopen(p, mode='x')\n"
        )
        self.assertEqual(write_sites(tree), 5)


def _cli(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(REPO))
    return subprocess.run([sys.executable, "-m", "deepfake_lens", *args], text=True, env=env, timeout=TIMEOUT, **kwargs)  # type: ignore[call-overload]


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/dev/full").exists(), "Linux /dev/full needed")
class CliOutputFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name, "case")
        self.folder.mkdir()
        (self.folder / "note.txt").write_text("메모", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_json_out_on_a_full_disk(self) -> None:
        done = _cli(["scan", str(self.folder), "--json-out", "/dev/full", "--format", "json"], capture_output=True)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("오류: 출력 파일을 쓸 수 없습니다: /dev/full (디스크 공간이 부족합니다(오류 번호 28))", done.stderr)
        self.assertNotIn("예기치 않은 오류", done.stderr)
        self.assertTrue(Path("/dev/full").is_char_device())  # written in place, never renamed over

    def test_standard_output_on_a_full_disk(self) -> None:
        with open("/dev/full", "w") as full:
            done = _cli(["scan", str(self.folder), "--format", "json"], stdout=full, stderr=subprocess.PIPE)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("오류: 결과를 표준 출력에 쓸 수 없습니다(디스크 공간이 부족합니다(오류 번호 28))", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertNotIn("Exception ignored", done.stderr)

    def test_a_reader_that_left_is_a_quiet_141(self) -> None:
        read_end, write_end = os.pipe()
        os.close(read_end)  # `| head` that has already exited
        try:
            done = _cli(["scan", str(self.folder), "--format", "json"], stdout=write_end, stderr=subprocess.PIPE)
        finally:
            os.close(write_end)
        self.assertEqual(done.returncode, BROKEN_PIPE_EXIT, done.stderr)
        for word in ("오류", "Traceback", "Exception ignored", "BrokenPipe"):
            self.assertNotIn(word, done.stderr)  # only the usual notes (engine profiles, thresholds)

    def test_every_output_option_on_a_full_disk(self) -> None:
        """R19-6: the outputs that were still "예기치 않은 오류(OSError)" on a full disk."""
        dataset = Path(self.tmp.name, "ds")
        for label in ("real", "fake"):
            (dataset / label).mkdir(parents=True)
            for index in range(2):
                (dataset / label / f"{label}{index}.txt").write_text(f"{label} 메모 {index}입니다.", encoding="utf-8")
        bench_json = Path(self.tmp.name, "bench.json")
        for args in (
            ["benchmark", str(dataset), "--json-out", str(bench_json), "--md-out", "/dev/full"],
            ["corpus", "build", str(dataset), "--out", "/dev/full"],
            ["dataset", str(dataset), "--manifest-out", "/dev/full"],
            ["batch", str(self.folder), "--output", "/dev/full"],
        ):
            with self.subTest(args=args[:2]):
                done = _cli(args, capture_output=True, cwd=self.tmp.name)
                self.assertEqual(done.returncode, 1, done.stderr[-600:])
                self.assertIn("오류: 출력 파일을 쓸 수 없습니다: /dev/full (디스크 공간이 부족합니다(오류 번호 28))", done.stderr)
                self.assertNotIn("예기치 않은 오류", done.stderr)
        # eval --false-positive-out / --false-negative-out (an empty list writes nothing a full disk refuses).
        from deepfake_lens.evaluate import write_cases_jsonl

        with self.assertRaises(OutputWriteError):
            write_cases_jsonl("/dev/full", [{"path": "a.png", "label": "real", "score": 90}])

    def test_a_closed_standard_output_is_an_error(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(REPO))
        done = subprocess.run(
            ["sh", "-c", 'exec "$0" -m deepfake_lens scan "$1" --format json >&-', sys.executable, str(self.folder)],
            capture_output=False, stderr=subprocess.PIPE, text=True, env=env, timeout=TIMEOUT,
        )
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("오류: 표준 출력이 닫혀 있어 결과를 출력할 수 없습니다", done.stderr)


if __name__ == "__main__":
    unittest.main()
