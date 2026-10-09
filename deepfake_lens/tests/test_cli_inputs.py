"""N4/N7: every subcommand refuses a bad input path the same way.

Round 6: ``text-advanced <missing|folder>`` and ``3d --file <missing>`` died
with an English traceback (exit 1); ``audio``, ``face``, ``video-analysis``,
``inpaint``, ``rppg``, ``prnu``, ``evidence``, ``batch``, ``eval``, ``collect``,
``dataset``… printed a report about nothing with exit 0; ``perf <file>``
raised ``ScanFolderError``. The matrix below runs every declared input of
every subcommand (``cli_inputs.INPUT_SPECS`` plus the shared configuration
options) with a nonexistent path, a folder where a file is expected, a file
where a folder is expected and an unsupported extension, and requires exit 2
(verify-report: its usage code 4), a Korean stderr starting with "오류:", no
traceback and nothing on stdout.

Y12 (round 7): the matrix ran ~190 subprocesses (34.7 s). It now calls
``cli.main`` in process with stdout/stderr captured — the input check runs
before any work, so a bad path never gets past argument parsing — and a
small sample still runs as real subprocesses so the process-level contract
(exit status, no traceback on the real stderr) stays covered.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from unittest import mock

from deepfake_lens.cli_inputs import (
    COMMON_INPUT_EXEMPT,
    COMMON_INPUT_SPECS,
    INPUT_SPECS,
    InputSpec,
    UsageError,
    require_input_path,
)
from deepfake_lens.cli_parser import build_parser, cli_path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Path-typed options that are outputs or state the command creates, not inputs.
OUTPUT_DESTS = frozenset({
    "out", "output", "output_dir", "frame_root", "heatmap_dir", "to", "bundle_to", "cache", "hash_db",
    "json_out", "csv_out", "html_out", "pdf_out", "md_out", "forensic_pdf_out", "evidence_statement_out",
    "evidence_statement_pdf_out", "manifest_out", "audit_out", "split_out", "robustness_out", "profile_out",
    "false_positive_out", "false_negative_out", "mapping_out",
})
# --key-file is checked by its own rule (N8: missing/empty key file -> exit 2).
SEPARATELY_CHECKED_DESTS = frozenset({"key_file"})
WORKERS = 8
# Y12: rows of the matrix that also run as real subprocesses (process-level check).
SUBPROCESS_SAMPLE = 6


class Fixtures:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.folder = root / "case"
        self.folder.mkdir()
        (self.folder / "a.txt").write_text("메모", encoding="utf-8")
        self.png = self._write(root / "photo.png", b"\x89PNG\r\n\x1a\n")
        self.wav = self._write(root / "tone.wav", b"RIFF")
        self.mp4 = self._write(root / "clip.mp4", b"\x00\x00\x00\x18ftyp")
        self.txt = self._write(root / "note.txt", "사람이 쓴 글입니다.".encode("utf-8"))
        self.txt2 = self._write(root / "note2.txt", "다른 글입니다.".encode("utf-8"))
        self.json = self._write(root / "data.json", b"{}")
        self.jsonl = self._write(root / "labels.jsonl", b"")
        self.ckpt = self._write(root / "model.onnx", b"\x00")
        self.xyz = self._write(root / "file.xyz", b"???")
        self.missing = root / "missing" / "nothing.png"
        self.out = root / "out"
        self.out.mkdir()
        self.home = root / "home"
        self.home.mkdir()

    @staticmethod
    def _write(path: Path, data: bytes) -> Path:
        path.write_bytes(data)
        return path


Builder = Callable[[Fixtures, dict[str, str]], list[str]]


def _o(fx: Fixtures, name: str) -> str:
    return str(fx.out / name)


# Valid argv for each INPUT_SPECS key; ``bad`` overrides one input.
BUILDERS: dict[str, Builder] = {
    "scan": lambda fx, bad: ["scan", bad.get("folder", str(fx.folder))],
    "collect": lambda fx, bad: ["collect", bad.get("folder", str(fx.folder)), "--out", _o(fx, "c.json")],
    "dataset": lambda fx, bad: ["dataset", bad.get("folder", str(fx.folder)), "--manifest-out", _o(fx, "m.json")],
    "eval": lambda fx, bad: ["eval", bad.get("folder", str(fx.folder))],
    "benchmark": lambda fx, bad: ["benchmark", bad.get("folder", str(fx.folder)), "--json-out", _o(fx, "b.json")],
    "fusion": lambda fx, bad: ["fusion", bad.get("folder", str(fx.folder)), "--out", _o(fx, "f.json")],
    "calibrate": lambda fx, bad: ["calibrate", bad.get("folder", str(fx.folder)), "--out", _o(fx, "k.json")],
    "feedback": lambda fx, bad: ["feedback", bad.get("labels", str(fx.jsonl)), "--scan-json", bad.get("scan_json", str(fx.json))],
    "train": lambda fx, bad: ["train", bad.get("folder", str(fx.folder)), "--out", _o(fx, "t.json")],
    "train-neural-plan": lambda fx, bad: ["train-neural-plan", bad.get("folder", str(fx.folder)), "--out", _o(fx, "n.json"), "--output-dir", _o(fx, "nd")],
    "video": lambda fx, bad: ["video", bad.get("folder", str(fx.folder)), "--out", _o(fx, "v.json"), "--frame-root", _o(fx, "fr")],
    "audio": lambda fx, bad: ["audio", bad.get("file", str(fx.wav))],
    "face": lambda fx, bad: ["face", bad.get("file", str(fx.png))],
    "video-analysis": lambda fx, bad: ["video-analysis", bad.get("file", str(fx.mp4))],
    "inpaint": lambda fx, bad: ["inpaint", bad.get("file", str(fx.png))],
    "text-advanced": lambda fx, bad: ["text-advanced", bad.get("file", str(fx.txt))],
    "compare": lambda fx, bad: ["compare", bad.get("file_a", str(fx.txt)), bad.get("file_b", str(fx.txt2))],
    "watermark": lambda fx, bad: ["watermark", bad.get("file", str(fx.txt)), "--secret", "s"],
    "forensic": lambda fx, bad: ["forensic", bad.get("file", str(fx.png))],
    "classify": lambda fx, bad: ["classify", bad.get("file", str(fx.png))],
    "multimodal": lambda fx, bad: ["multimodal", bad.get("files", str(fx.png)), "--av-sync", bad.get("av_sync", str(fx.mp4))],
    "rppg": lambda fx, bad: ["rppg", bad.get("file", str(fx.mp4))],
    "prnu": lambda fx, bad: ["prnu", bad.get("file", str(fx.png)), "--reference", bad.get("reference", str(fx.png))],
    "evidence": lambda fx, bad: ["evidence", bad.get("file", str(fx.png))],
    "batch": lambda fx, bad: ["batch", bad.get("folder", str(fx.folder))],
    "explain": lambda fx, bad: ["explain", bad.get("file", str(fx.png))],
    "agent": lambda fx, bad: ["agent", "--file", bad.get("file", str(fx.txt))],
    "3d": lambda fx, bad: ["3d", "--file", bad.get("file", str(fx.txt))],
    "avatar": lambda fx, bad: ["avatar", "--file", bad.get("file", str(fx.mp4))],
    "pixel-analysis": lambda fx, bad: ["pixel-analysis", bad.get("file", str(fx.png))],
    "ml-classify": lambda fx, bad: ["ml-classify", bad.get("file", str(fx.png))],
    "legal-report": lambda fx, bad: ["legal-report", bad.get("file", str(fx.png))],
    "verify-report": lambda fx, bad: ["verify-report", bad.get("report", str(fx.json))],
    "perf": lambda fx, bad: ["perf", bad.get("folder", str(fx.folder)), "--out", _o(fx, "p.json")],
    "faceswap-seam": lambda fx, bad: ["faceswap-seam", bad.get("file", str(fx.png))],
    "evidence-statement": lambda fx, bad: ["evidence-statement", bad.get("target", str(fx.folder))],
    "models": lambda fx, bad: ["models", "--checkpoint", bad.get("checkpoint", str(fx.ckpt)), "--profile-out", _o(fx, "prof.json")],
    "web": lambda fx, bad: ["web", "--folder", bad.get("folder", str(fx.folder)), "--allow-root", bad.get("allow_root", str(fx.folder))],
    "api-serve": lambda fx, bad: ["api-serve", "--allow-root", bad.get("allow_root", str(fx.folder))],
    "vendor-weights": lambda fx, bad: ["vendor-weights", "--install", bad.get("install", str(fx.folder))],
    "corpus build": lambda fx, bad: ["corpus", "build", bad.get("folder", str(fx.folder)), "--out", _o(fx, "cm.json")],
    "corpus split": lambda fx, bad: ["corpus", "split", "--manifest", bad.get("manifest", str(fx.json)), "--seed", "1"],
    "corpus verify": lambda fx, bad: ["corpus", "verify", "--manifest", bad.get("manifest", str(fx.json)), "--root", bad.get("root", str(fx.folder))],
    "doctor": lambda fx, bad: ["doctor"],
}


def _subcommand_parsers() -> dict[str, argparse.ArgumentParser]:
    _, parsers = build_parser()
    out: dict[str, argparse.ArgumentParser] = {}
    for name, parser in parsers.items():
        if name == "corpus":
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for sub, sub_parser in action.choices.items():
                        out[f"corpus {sub}"] = sub_parser
            continue
        out[name] = parser
    return out


def _path_actions(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    # Y3: path arguments use cli_parser.cli_path (refuses an empty string).
    return {action.dest: action for action in parser._actions if action.type in (Path, cli_path)}


def _option_string(action: argparse.Action) -> str | None:
    return next((s for s in action.option_strings if s.startswith("--")), None)


def _bad_cases(spec: InputSpec, fx: Fixtures) -> list[tuple[str, str]]:
    cases = [("없는 경로", str(fx.missing))]
    if spec.kind == "file":
        cases.append(("파일 대신 폴더", str(fx.folder)))
    if spec.kind == "folder":
        cases.append(("폴더 대신 파일", str(fx.txt)))
    if spec.kind in ("file", "either") and spec.suffixes is not None:
        cases.append(("미지원 형식", str(fx.xyz)))
    return cases


EXPECTED_MESSAGE = {
    "없는 경로": "찾을 수 없습니다: ",
    "파일 대신 폴더": "파일이 아니라 폴더입니다: ",
    "폴더 대신 파일": "폴더가 아니라 파일입니다: ",
    "미지원 형식": "지원되지 않는 형식입니다: ",
}


class InputPathMatrixTest(unittest.TestCase):
    """N4/N7: subprocess matrix over every subcommand's input paths."""

    _tmp: tempfile.TemporaryDirectory[str]
    fx: Fixtures
    parsers: dict[str, argparse.ArgumentParser]

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = Fixtures(Path(cls._tmp.name).resolve())
        cls.parsers = _subcommand_parsers()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _matrix(self) -> list[tuple[str, str, str, list[str]]]:
        """(command key, attr, case, argv) for every declared input and bad case."""
        rows: list[tuple[str, str, str, list[str]]] = []
        for key, specs in INPUT_SPECS.items():
            build = BUILDERS[key]
            for spec in specs:
                for case, value in _bad_cases(spec, self.fx):
                    rows.append((key, spec.attr, case, build(self.fx, {spec.attr: value})))
        for key, parser in self.parsers.items():
            dests = _path_actions(parser)
            exempt = COMMON_INPUT_EXEMPT.get(key, frozenset())
            for spec in COMMON_INPUT_SPECS:
                action = dests.get(spec.attr)
                if action is None or spec.attr in exempt:
                    continue
                flag = _option_string(action)
                assert flag is not None, (key, spec.attr)
                for case, value in _bad_cases(spec, self.fx):
                    rows.append((key, spec.attr, case, [*BUILDERS[key](self.fx, {}), flag, value]))
        return rows

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), HOME=str(self.fx.home), DEEPFAKE_LENS_LOG_DIR=str(self.fx.home / "logs"))
        env.pop("DEEPFAKE_LENS_REPORT_KEY", None)
        return subprocess.run(
            [sys.executable, "-m", "deepfake_lens", *argv],
            capture_output=True, text=True, env=env, timeout=300, cwd=str(self.fx.root),
        )

    def _run_in_process(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Y12: ``cli.main(argv)`` with stdout/stderr captured — same contract, no interpreter start."""
        from deepfake_lens import cli

        out, err = io.StringIO(), io.StringIO()
        saved = {key: os.environ.get(key) for key in ("HOME", "DEEPFAKE_LENS_LOG_DIR", "DEEPFAKE_LENS_REPORT_KEY")}
        os.environ["HOME"] = str(self.fx.home)
        os.environ["DEEPFAKE_LENS_LOG_DIR"] = str(self.fx.home / "logs")
        os.environ.pop("DEEPFAKE_LENS_REPORT_KEY", None)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cli.main(list(argv))
                except SystemExit as exc:  # argparse usage errors
                    code = exc.code if isinstance(exc.code, int) else 2
                except Exception:  # noqa: BLE001 - reported as a traceback like the subprocess run
                    import traceback

                    err.write("Traceback (most recent call last):\n" + traceback.format_exc())
                    code = 1
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return subprocess.CompletedProcess(argv, code, out.getvalue(), err.getvalue())

    def _problems(self, key: str, case: str, result: subprocess.CompletedProcess[str]) -> list[str]:
        expected_rc = 4 if key == "verify-report" else 2
        stderr = result.stderr.strip()
        problems = []
        if result.returncode != expected_rc:
            problems.append(f"exit {result.returncode} (기대 {expected_rc})")
        if not stderr.startswith("오류:"):
            problems.append(f"stderr {stderr[:120]!r}")
        elif EXPECTED_MESSAGE[case] not in stderr:
            problems.append(f"메시지 {stderr[:120]!r}")
        if "Traceback" in result.stderr:
            problems.append("traceback")
        if result.stdout.strip():
            problems.append(f"stdout {result.stdout.strip()[:80]!r}")
        return problems

    def test_every_input_of_every_subcommand(self) -> None:
        """N4/N7: exit 2 (verify-report 4), "오류: …" on stderr, no traceback, no stdout report."""
        rows = self._matrix()
        self.assertGreater(len(rows), 100)
        failures: list[str] = []
        for key, attr, case, argv in rows:
            problems = self._problems(key, case, self._run_in_process(argv))
            if problems:
                failures.append(f"{key} {attr} [{case}] {' '.join(argv)}: {'; '.join(problems)}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_matrix_sample_as_subprocesses(self) -> None:
        """Y12: an evenly spread sample of the matrix as real processes (exit status, real stderr)."""
        rows = self._matrix()
        step = max(1, len(rows) // SUBPROCESS_SAMPLE)
        sample = rows[::step][:SUBPROCESS_SAMPLE]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(lambda row: self._run(row[3]), sample))
        failures = [
            f"{key} {attr} [{case}] {' '.join(argv)}: {'; '.join(problems)}"
            for (key, attr, case, argv), result in zip(sample, results)
            if (problems := self._problems(key, case, result))
        ]
        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_path_input_of_the_parser_is_declared(self) -> None:
        """Every Path-typed input argument of cli_parser is in INPUT_SPECS or the shared config specs."""
        common = {spec.attr for spec in COMMON_INPUT_SPECS}
        undeclared: list[str] = []
        for key, parser in self.parsers.items():
            declared = {spec.attr for spec in INPUT_SPECS.get(key, ())}
            for dest in _path_actions(parser):
                if dest in OUTPUT_DESTS or dest in SEPARATELY_CHECKED_DESTS or dest in declared:
                    continue
                if dest in common:
                    continue  # checked by COMMON_INPUT_SPECS, or exempt on purpose (COMMON_INPUT_EXEMPT)
                undeclared.append(f"{key}: {dest}")
            if declared:
                self.assertIn(key, BUILDERS, key)
        self.assertEqual(undeclared, [])
        self.assertEqual(set(BUILDERS) - {"doctor"}, set(INPUT_SPECS))

    def test_valid_inputs_pass_the_check(self) -> None:
        """The valid argv of every builder passes check_command_inputs (the matrix's control)."""
        from deepfake_lens.cli_inputs import check_command_inputs

        parser, _ = build_parser()
        for key, build in BUILDERS.items():
            with self.subTest(command=key):
                check_command_inputs(parser.parse_args(build(self.fx, {})))


class RequireInputPathTest(unittest.TestCase):
    """N4: the helper's rules, in process."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.file = self.root / "a.png"
        self.file.write_bytes(b"x")
        self.archive = self.root / "bundle.tar.gz"
        self.archive.write_bytes(b"x")

    def test_rules(self) -> None:
        images = frozenset({".png"})
        self.assertEqual(require_input_path(self.file, "file", supported_suffixes=images), self.file)
        self.assertEqual(require_input_path(self.root, "folder"), self.root)
        self.assertEqual(require_input_path(self.root, "either"), self.root)
        cases: list[tuple[Path, str, frozenset[str] | None, str]] = [
            (self.root / "none.png", "file", None, "파일을 찾을 수 없습니다: "),
            (self.root / "none", "folder", None, "폴더를 찾을 수 없습니다: "),
            (self.root / "none", "either", None, "파일이나 폴더를 찾을 수 없습니다: "),
            (self.root, "file", None, "파일이 아니라 폴더입니다: "),
            (self.file, "folder", None, "폴더가 아니라 파일입니다: "),
            (self.file, "file", frozenset({".wav"}), "지원되지 않는 형식입니다: "),
        ]
        for path, kind, suffixes, message in cases:
            with self.subTest(path=path.name, kind=kind):
                with self.assertRaises(UsageError) as ctx:
                    require_input_path(path, kind, supported_suffixes=suffixes)  # type: ignore[arg-type]
                self.assertTrue(str(ctx.exception).startswith(message), str(ctx.exception))

    def test_compound_suffix_and_symlinks(self) -> None:
        from deepfake_lens.cli_inputs import SCAN_SUFFIXES

        self.assertEqual(require_input_path(self.archive, "file", supported_suffixes=SCAN_SUFFIXES), self.archive)
        link = self.root / "link.png"
        try:
            os.symlink(self.file, link)
        except OSError as exc:  # pragma: no cover - no symlink support
            self.skipTest(f"cannot create symlinks: {exc}")
        self.assertEqual(require_input_path(link, "file", symlinks="allow"), link)
        with self.assertRaises(UsageError) as ctx:
            require_input_path(link, "file")
        self.assertTrue(str(ctx.exception).startswith("심볼릭 링크는 따라가지 않습니다: "))

    def test_suffix_sets_match_core(self) -> None:
        from deepfake_lens import cli_inputs
        from deepfake_lens.core import SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_TEXT_EXTENSIONS

        self.assertEqual(cli_inputs.IMAGE_SUFFIXES, frozenset(SUPPORTED_IMAGE_EXTENSIONS))
        self.assertEqual(cli_inputs.TEXT_SUFFIXES, frozenset(SUPPORTED_TEXT_EXTENSIONS))



class _UsageErrorCase(unittest.TestCase):
    """Shared fixtures of the round-7/round-8 usage-error tests: ``cli.main`` in process."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.folder = self.root / "case"
        self.folder.mkdir()
        (self.folder / "a.txt").write_text("사람이 쓴 메모입니다.", encoding="utf-8")
        self.garbage = self.root / "garbage.json"
        self.garbage.write_text("{not json", encoding="utf-8")
        self.listed = self.root / "list.json"
        self.listed.write_text("[1, 2, 3]", encoding="utf-8")
        self.no_items = self.root / "noitems.json"
        self.no_items.write_text('{"schema_version": 2, "summary": {}}', encoding="utf-8")
        self.empty_items = self.root / "emptyitems.json"
        self.empty_items.write_text('{"items": []}', encoding="utf-8")
        self.cwd = os.getcwd()
        os.chdir(self.folder)  # `scan ""` must not scan the working folder
        self.addCleanup(os.chdir, self.cwd)

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        from deepfake_lens import cli

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        return code, out.getvalue(), err.getvalue()

    def _assert_usage(self, argv: list[str], message: str) -> None:
        code, stdout, stderr = self._run(argv)
        self.assertEqual(code, 2, (argv, stderr, stdout[:200]))
        self.assertTrue(stderr.startswith("오류:"), stderr)
        self.assertIn(message, stderr)
        self.assertEqual(stdout.strip(), "")
        self.assertNotIn("Traceback", stderr)


class RoundSevenUsageErrorsTest(_UsageErrorCase):
    """Y1/Y2/Y3/Y7 (round 7): input/option problems found before any work — exit 2, "오류: …"."""

    def test_y1_scan_json_without_items(self) -> None:
        for path in (self.no_items, self.empty_items):
            with self.subTest(path=path.name):
                self._assert_usage(["evidence-statement", str(path)], "검사 JSON에 items가 없습니다")
        self._assert_usage(["evidence-statement", str(self.listed)], "검사 JSON을 해석할 수 없습니다")

    def test_y2_unreadable_thresholds_profile(self) -> None:
        for path in (self.garbage, self.listed, self.no_items):
            png = self.root / "x.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n")
            for argv in (["scan", str(self.folder)], ["faceswap-seam", str(png)]):
                with self.subTest(path=path.name, command=argv[0]):
                    self._assert_usage([*argv, "--thresholds", str(path)], "임계값 프로필을 읽을 수 없거나 버전이 맞지 않습니다: ")

    def test_y3_empty_path_is_refused(self) -> None:
        for argv in (["scan", ""], [""], ["scan", "  "], ["forensic", ""], ["scan", str(self.folder), "--json-out", ""]):
            with self.subTest(argv=argv):
                self._assert_usage(argv, "경로가 비어 있습니다")

    def test_y7_output_path_is_a_folder(self) -> None:
        for flag in ("--html-out", "--json-out", "--csv-out", "--evidence-statement-out"):
            with self.subTest(flag=flag):
                self._assert_usage(["scan", str(self.folder), flag, str(self.root)], f"출력 경로가 폴더입니다: {self.root}")
        self._assert_usage(["evidence-statement", str(self.folder), "--md-out", str(self.root)], "출력 경로가 폴더입니다: ")
        self._assert_usage(["legal-report", str(self.folder / "a.txt"), "--output", str(self.root)], "출력 경로가 폴더입니다: ")

    def test_y4_cache_never_overwrites_another_file(self) -> None:
        """Y4: --cache naming an existing non-cache JSON is refused (exit 2) and left untouched."""
        import json

        from deepfake_lens.core import scan_directory
        from deepfake_lens.scan_cache import SCAN_CACHE_FORMAT, CacheFileError

        report = self.root / "report.json"
        report.write_text('{"schema_version": 2, "items": []}', encoding="utf-8")
        for target in (report, self.garbage, self.listed):
            before = target.read_bytes()
            with self.subTest(target=target.name):
                self._assert_usage(["scan", str(self.folder), "--cache", str(target)], f"캐시 파일이 아닙니다: {target}")
                self.assertEqual(target.read_bytes(), before)
        with self.assertRaises(CacheFileError):
            scan_directory(self.folder, cache_path=report)
        self.assertEqual(report.read_text(encoding="utf-8"), '{"schema_version": 2, "items": []}')

        cache = self.root / "cache.json"
        for _ in range(2):  # new cache, then reuse of the same cache
            code, _, stderr = self._run(["scan", str(self.folder), "--cache", str(cache), "--format", "json"])
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["format"], SCAN_CACHE_FORMAT)
        legacy = self.root / "legacy.json"
        legacy.write_text('{"version": 1, "items": {}}', encoding="utf-8")
        empty = self.root / "empty.json"
        empty.write_bytes(b"")
        for accepted in (legacy, empty):
            with self.subTest(accepted=accepted.name):
                code, _, stderr = self._run(["scan", str(self.folder), "--cache", str(accepted), "--format", "json"])
                self.assertEqual(code, 0, stderr)
                self.assertEqual(json.loads(accepted.read_text(encoding="utf-8"))["format"], SCAN_CACHE_FORMAT)


class RoundEightUsageErrorsTest(_UsageErrorCase):
    """Z1–Z5: usage errors that ran the command or used the wrong exit code — "오류: …" on stderr, no stdout."""

    def _assert_usage_code(self, argv: list[str], message: str, code: int) -> None:
        got, stdout, stderr = self._run(argv)
        self.assertEqual(got, code, (argv, stderr, stdout[:200]))
        self.assertTrue(stderr.startswith("오류:"), stderr)
        self.assertIn(message, stderr)
        self.assertEqual(stdout.strip(), "")
        self.assertNotIn("Traceback", stderr)

    def test_z1_realtime_scores_must_be_integers(self) -> None:
        import json

        for raw in ("abc", "10,x,30", "12.5"):
            with self.subTest(scores=raw):
                self._assert_usage(["realtime", "--scores", raw], "--scores에는 쉼표로 구분한 정수만 쓸 수 있습니다")
        code, stdout, stderr = self._run(["realtime", "--scores", "10, 20,,30", "--format", "json"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["diagnostic"]["summary"]["frame_count"], 3)

    def test_z1_realtime_as_subprocess(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), HOME=str(self.root), DEEPFAKE_LENS_LOG_DIR=str(self.root / "logs"))
        result = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "realtime", "--scores", "abc"],
            capture_output=True, text=True, env=env, timeout=300, cwd=str(self.root),
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stderr.startswith("오류: --scores"), result.stderr)
        self.assertEqual(result.stdout, "")

    def test_z2_vendor_weights_pin_missing_profile(self) -> None:
        models = self.root / "models"
        models.mkdir()
        for profile in ("nonexistent-profile", str(self.root / "no.json")):
            with self.subTest(profile=profile):
                self._assert_usage(
                    ["vendor-weights", "pin", profile, "--models-dir", str(models)],
                    f"파일을 찾을 수 없습니다: 「{profile}」 — 프로필 파일 경로도, 모델 디렉터리 ",  # Z2 leftover: echoed input is quoted
                )

    def test_z3_trailing_separator_on_a_file(self) -> None:
        from deepfake_lens.cli_inputs import TRAILING_SEPARATOR_HINT

        png = self.root / "photo.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        note = self.folder / "a.txt"
        for argv in (
            ["forensic", f"{png}/"],
            ["classify", f"{png}/"],
            ["face", f"{png}/"],
            ["legal-report", f"{note}/"],
            ["scan", f"{note}/"],
        ):
            with self.subTest(argv=argv):
                self._assert_usage(argv, f"폴더가 아니라 파일입니다: {argv[1]}{'' if argv[0] != 'scan' else ' (단일 파일은'}")
                _, _, stderr = self._run(argv)
                self.assertIn(TRAILING_SEPARATOR_HINT.strip(" —"), stderr)
        self._assert_usage_code(["verify-report", f"{self.garbage}/"], "폴더가 아니라 파일입니다: ", 4)
        # A folder with a trailing separator is still a folder.
        code, _, stderr = self._run(["scan", f"{self.folder}/", "--format", "json"])
        self.assertEqual(code, 0, stderr)

    def test_z3_require_input_path_reads_the_typed_text(self) -> None:
        from deepfake_lens.cli_parser import cli_path

        png = self.root / "photo.png"
        png.write_bytes(b"x")
        for value in (f"{png}/", cli_path(f"{png}/")):
            with self.subTest(value=repr(value)):
                with self.assertRaises(UsageError) as ctx:
                    require_input_path(value, "file", symlinks="allow")
                self.assertTrue(str(ctx.exception).startswith(f"폴더가 아니라 파일입니다: {png}/ — "), str(ctx.exception))
        self.assertEqual(require_input_path(cli_path(f"{self.folder}/"), "folder"), self.folder)
        with self.assertRaises(UsageError) as ctx:
            require_input_path(f"{self.root / 'none.png'}/", "file")
        self.assertTrue(str(ctx.exception).startswith("파일을 찾을 수 없습니다: "), str(ctx.exception))
        self.assertEqual(require_input_path(png, "file"), png)  # a plain Path (no typed text) is unchanged

    def test_z4_verify_report_unparseable_json(self) -> None:
        # P9 (round 8): the detail was the English json message ("Expecting …")
        # and the Python type name ("list"); it is Korean, position as numbers.
        from deepfake_lens.error_text import english_prose

        for path, detail in ((self.garbage, "JSON 형식 오류: 속성 이름은 큰따옴표로 감싸야 합니다(1행 2열)"), (self.listed, "JSON 객체가 아니라 배열입니다")):
            with self.subTest(path=path.name):
                self._assert_usage_code(["verify-report", str(path)], f"보고서 JSON을 해석할 수 없습니다: {path} (", 4)
                _, _, stderr = self._run(["verify-report", str(path), "--format", "json"])
                self.assertIn(detail, stderr)
                self.assertNotIn("Expecting", stderr)
                self.assertNotIn("list", stderr.replace(str(path), ""))
                self.assertIsNone(english_prose(stderr.replace(str(path), "")))

    def _assert_korean_only(self, stderr: str, *paths: Path) -> None:
        from deepfake_lens.error_text import english_prose

        text = stderr
        for path in paths:
            text = text.replace(str(path), "")
        self.assertIsNone(english_prose(text), stderr)
        for english in ("Expecting", "codec", "decode", "Errno", "list", "dict"):
            self.assertNotIn(english, text, stderr)

    def test_p4_every_file_input_reports_encoding_and_syntax_in_korean(self) -> None:
        """P4/P9 (round 8): a non-UTF-8 --thresholds file was a UnicodeDecodeError traceback
        (exit 1); every file input (thresholds, fusion/calibration/model profile, scan
        JSON, report JSON, manifest, labels, hash DB, config file) now says what could not
        be read or parsed, in Korean, before any work — exit 2 (verify-report: 4)."""
        binary = self.root / "binary.json"
        binary.write_bytes(b"\x85\xff\xfe binary \x00 not utf-8")
        binary_jsonl = self.root / "labels.jsonl"
        binary_jsonl.write_bytes(b"\x85\xff\xfe")
        encoding = "(UTF-8 텍스트가 아닙니다(바이트 위치 0))"
        png = self.root / "photo.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        cases: list[tuple[list[str], str, int]] = [
            (["scan", str(self.folder), "--thresholds", str(binary)], f"임계값 파일을 읽을 수 없습니다(인코딩): {binary} {encoding}", 2),
            (["faceswap-seam", str(png), "--thresholds", str(binary)], f"임계값 파일을 읽을 수 없습니다(인코딩): {binary} {encoding}", 2),
            (["scan", str(self.folder), "--fusion-profile", str(binary)], f"융합 프로필 파일을 읽을 수 없습니다(인코딩): {binary}", 2),
            (["scan", str(self.folder), "--fusion-profile", str(self.garbage)], f"융합 프로필 파일을 해석할 수 없습니다: {self.garbage} (JSON 형식 오류: 속성 이름은 큰따옴표로 감싸야 합니다(1행 2열))", 2),
            (["scan", str(self.folder), "--fusion-profile", str(self.listed)], f"융합 프로필 파일을 해석할 수 없습니다: {self.listed} (JSON 객체가 아니라 배열입니다)", 2),
            (["scan", str(self.folder), "--model-path", str(binary)], f"모델 프로필 파일을 읽을 수 없습니다(인코딩): {binary}", 2),
            (["scan", str(self.folder), "--model-path", str(self.garbage)], f"모델 프로필 파일을 해석할 수 없습니다: {self.garbage}", 2),
            (["scan", str(self.folder), "--hash-db", str(binary)], f"해시 DB 파일을 읽을 수 없습니다(인코딩): {binary}", 2),
            (["eval", str(self.folder), "--calibration", str(binary)], f"보정 프로필 파일을 읽을 수 없습니다(인코딩): {binary}", 2),
            (["evidence-statement", str(binary)], f"검사 JSON을 읽을 수 없습니다(인코딩): {binary} {encoding}", 2),
            (["evidence-statement", str(self.garbage)], f"검사 JSON을 해석할 수 없습니다: {self.garbage} (JSON 형식 오류: 속성 이름은 큰따옴표로 감싸야 합니다(1행 2열))", 2),
            (["evidence-statement", str(self.listed)], f"검사 JSON을 해석할 수 없습니다: {self.listed} (JSON 객체가 아니라 배열입니다)", 2),
            (["verify-report", str(binary)], f"보고서 JSON을 읽을 수 없습니다(인코딩): {binary} {encoding}", 4),
            (["feedback", str(binary_jsonl)], f"라벨 파일을 읽을 수 없습니다(인코딩): {binary_jsonl}", 2),
            (["feedback", str(self.garbage), "--scan-json", str(binary)], f"검사 JSON을 읽을 수 없습니다(인코딩): {binary}", 2),
            (["corpus", "verify", "--manifest", str(binary)], f"매니페스트 파일을 읽을 수 없습니다(인코딩): {binary}", 2),
            (["corpus", "split", "--manifest", str(self.garbage), "--seed", "1"], f"매니페스트 파일을 해석할 수 없습니다: {self.garbage}", 2),
        ]
        for argv, message, code in cases:
            with self.subTest(argv=" ".join(argv[:1] + argv[-2:])):
                got, stdout, stderr = self._run(argv)
                self.assertEqual(got, code, (argv, stderr))
                self.assertTrue(stderr.startswith("오류:"), stderr)
                self.assertIn(message, stderr)
                self.assertNotIn("Traceback", stderr)
                self._assert_korean_only(stderr, binary, binary_jsonl, self.garbage, self.listed)

    def test_p4_broken_config_file_and_scan_json_rows(self) -> None:
        """P4/P9 (round 8): a broken config file is an error for the commands that print the
        office identity; a scan JSON row that is not a scan row is named by its position."""
        config = self.root / "config.json"
        config.write_bytes(b"\xff\xfe{")
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_CONFIG": str(config)}):
            for argv in (["scan", str(self.folder)], ["evidence-statement", str(self.folder)]):
                with self.subTest(argv=argv[0]):
                    self._assert_usage(argv, f"설정 파일을 읽을 수 없습니다(인코딩): {config}")
            config.write_text('{"law_firm": ', encoding="utf-8")
            self._assert_usage(["scan", str(self.folder)], f"설정 파일을 해석할 수 없습니다: {config} (JSON 형식 오류: 값이 필요합니다(1행 14열))")
            code, _, stderr = self._run(["realtime", "--scores", "10,20"])  # no office identity: not read
            self.assertEqual(code, 0, stderr)
        bad_rows = self.root / "badrows.json"
        bad_rows.write_text('{"items": [{"path": "a.txt", "result": {"verdict_code": "zzz"}}]}', encoding="utf-8")
        code, _, stderr = self._run(["evidence-statement", str(bad_rows)])
        self.assertEqual(code, 2, stderr)
        self.assertIn(f"검사 JSON을 해석할 수 없습니다: {bad_rows} — 1번째 행의 형식이 맞지 않습니다", stderr)
        self._assert_korean_only(stderr, bad_rows)

    def test_p9_key_file_reason_is_korean_only(self) -> None:
        """P9 (round 8): "([Errno 2] 파일 또는 폴더가 없습니다: nokey)" → Korean reason with the number."""
        missing = self.root / "nokey"
        self._assert_usage(["scan", str(self.folder), "--sign", "--json-out", str(self.root / "r.json"), "--key-file", str(missing)],
                           "서명 키 파일을 읽을 수 없습니다: nokey (파일 또는 폴더가 없습니다(오류 번호 2))")

    def test_p11_output_never_inside_the_examined_folder_or_on_an_input(self) -> None:
        """P11 (round 8): --json-out & co. inside the scanned folder wrote into the evidence
        (a report over t1.png), and an output equal to the input JSON overwrote it."""
        photo = self.folder / "photo.png"
        photo.write_bytes(b"\x89PNG\r\n\x1a\n")
        scan_json = self.root / "scan.json"
        code, _, stderr = self._run(["scan", str(self.folder), "--json-out", str(scan_json)])
        self.assertEqual(code, 0, stderr)
        before = {path.name: path.read_bytes() for path in self.folder.iterdir()}
        scan_before = scan_json.read_bytes()
        inside = [
            ["scan", str(self.folder), "--json-out", str(self.folder / "r.json")],
            ["scan", str(self.folder), "--html-out", str(self.folder / "r.html")],
            ["scan", str(self.folder), "--cache", str(self.folder / "c.json")],
            ["scan", str(self.folder), "--heatmap-dir", str(self.folder / "maps")],
            ["scan", ".", "--json-out", "r.json"],  # relative, cwd = the folder
            ["evidence-statement", str(self.folder), "--md-out", str(self.folder / "s.md")],
        ]
        for argv in inside:
            with self.subTest(argv=" ".join(argv[-2:])):
                self._assert_usage(argv, "출력 경로가 검사 대상 폴더 안에 있습니다: ")
        same = [
            (["scan", str(self.folder), "--json-out", str(photo)], photo),  # also inside
            (["forensic", str(photo), "--json-out", str(photo)], photo),
            (["evidence-statement", str(scan_json), "--json-out", str(scan_json)], scan_json),
            (["evidence-statement", str(scan_json), "--md-out", str(scan_json)], scan_json),
        ]
        for argv, target in same:
            with self.subTest(argv=" ".join(argv[:1] + argv[-2:])):
                code, stdout, stderr = self._run(argv)
                self.assertEqual(code, 2, stderr)
                self.assertTrue(
                    f"출력 경로가 입력 파일과 같습니다: {target}" in stderr or "출력 경로가 검사 대상 폴더 안에 있습니다: " in stderr, stderr
                )
        self.assertEqual({path.name: path.read_bytes() for path in self.folder.iterdir()}, before)
        self.assertEqual(scan_json.read_bytes(), scan_before)
        code, _, stderr = self._run(["forensic", str(photo), "--json-out", str(self.root / "f.json")])
        self.assertEqual(code, 0, stderr)  # next to the evidence, not on it: fine

    def test_p10_unwritable_output_folder_is_refused_before_the_scan(self) -> None:
        """P10 (round 8): a read-only output folder failed after the scan ("처리 오류 N건")."""
        import errno
        import tempfile as tempfile_module

        out_dir = self.root / "out"
        out_dir.mkdir()
        denied = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(tempfile_module, "mkstemp", side_effect=denied), \
                mock.patch("deepfake_lens.cli.scan_folder_run") as scan:
            self._assert_usage(["scan", str(self.folder), "--json-out", str(out_dir / "r.json")],
                               f"출력 폴더에 쓸 수 없습니다: {out_dir} (접근 권한이 없습니다(오류 번호 13)) — 검사 전에 확인했습니다")
            scan.assert_not_called()
        with mock.patch("deepfake_lens.cli_inputs.os.access", return_value=False):
            self._assert_usage(["evidence-statement", str(self.folder), "--md-out", str(out_dir / "s.md")],
                               f"출력 폴더에 쓸 수 없습니다: {out_dir} (쓰기 권한이 없습니다)")
        self.assertEqual(list(out_dir.iterdir()), [], "the probe file is removed")
        if sys.platform.startswith("linux") and Path("/proc/self").exists():
            self._assert_usage(["scan", str(self.folder), "--json-out", "/proc/deepfake-lens-p10.json"], "출력 폴더에 쓸 수 없습니다: /proc (")
        code, _, stderr = self._run(["scan", str(self.folder), "--json-out", str(out_dir / "r.json")])
        self.assertEqual(code, 0, stderr)
        self.assertEqual([path.name for path in out_dir.iterdir()], ["r.json"])

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "root ignores folder modes; POSIX modes")
    def test_p10_read_only_folder_mode(self) -> None:
        """P10 (round 8): a 0o555 output folder (non-root) is refused before the scan."""
        out_dir = self.root / "ro"
        out_dir.mkdir()
        out_dir.chmod(0o555)
        self.addCleanup(out_dir.chmod, 0o755)
        self._assert_usage(["scan", str(self.folder), "--json-out", str(out_dir / "r.json")], f"출력 폴더에 쓸 수 없습니다: {out_dir} (")

    def test_z5_output_into_missing_folder(self) -> None:
        missing = self.root / "nodir" / "x"
        png = self.root / "photo.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        cases = [
            ["scan", str(self.folder), "--json-out", str(missing / "r.json")],
            ["scan", str(self.folder), "--csv-out", str(missing / "r.csv")],
            ["scan", str(self.folder), "--html-out", str(missing / "r.html")],
            ["scan", str(self.folder), "--pdf-out", str(missing / "r.pdf")],
            ["scan", str(self.folder), "--evidence-statement-out", str(missing / "s.md")],
            ["forensic", str(png), "--json-out", str(missing / "r.json")],
            ["evidence-statement", str(self.folder), "--md-out", str(missing / "s.md")],
            ["dataset", str(self.folder), "--manifest-out", str(missing / "m.json")],
        ]
        for argv in cases:
            with self.subTest(argv=argv[0] + " " + argv[-2]):
                self._assert_usage(argv, f"출력 폴더가 없습니다: {missing} — ")
                self.assertFalse((self.root / "nodir").exists())
        # --out / --cache keep creating their folder (only --*-out is checked).
        code, _, stderr = self._run(["scan", str(self.folder), "--cache", str(missing / "c.json"), "--format", "json"])
        self.assertEqual(code, 0, stderr)


class RoundNineOutputFolderTest(_UsageErrorCase):
    """R9-4 (round 9): output-folder options other than --heatmap-dir could create folders
    inside the examined folder (`video --frame-root <folder>/frames --extract`,
    `train-neural-plan --output-dir`)."""

    def test_every_output_like_option_is_registered(self) -> None:
        import argparse
        import re

        from deepfake_lens import cli_inputs
        from deepfake_lens.cli_parser import build_parser

        registered = {
            *cli_inputs.OUTPUT_FILE_ATTRS, *cli_inputs.OUTPUT_FOLDER_ATTRS, *cli_inputs.PATH_OPTIONS_NOT_OUTPUT,
        }
        seen: dict[str, str] = {}

        def walk(parser: argparse.ArgumentParser, name: str) -> None:
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for sub_name, sub in action.choices.items():
                        walk(sub, f"{name} {sub_name}".strip())
                    continue
                for option in action.option_strings:
                    if re.fullmatch(r"--.*-(dir|root)|--out.*", option):
                        seen[f"{name} {option}"] = action.dest

        walk(build_parser()[0], "")
        self.assertIn("video --frame-root", seen)
        self.assertIn("train-neural-plan --output-dir", seen)
        unregistered = {where: dest for where, dest in seen.items() if dest not in registered}
        self.assertEqual(unregistered, {}, "register each in cli_inputs (output file/folder, or not an output)")
        self.assertEqual(set(cli_inputs.OUTPUT_FOLDER_ATTRS) & set(cli_inputs.PATH_OPTIONS_NOT_OUTPUT), set())

    def test_every_path_argument_is_a_registered_output_or_declared_read_only(self) -> None:
        """R10-9 (round 10): the name pattern above ("*-dir", "*-root", "--out*") misses a
        future write option such as --frames or --dest. Every Path-typed argument of
        every subcommand must be a registered write target or declared read-only."""
        import argparse
        from pathlib import Path

        from deepfake_lens import cli_inputs
        from deepfake_lens.cli_parser import build_parser

        self.assertEqual(cli_inputs.unclassified_path_arguments(build_parser()[0]), {})
        write = {*cli_inputs.OUTPUT_FILE_ATTRS, *cli_inputs.OUTPUT_FOLDER_ATTRS}
        read_only = {*cli_inputs.PATH_OPTIONS_NOT_OUTPUT, *cli_inputs.PATH_OPTIONS_READ_ONLY}
        self.assertEqual(write & read_only, set(), "an argument is either written or only read")
        # A new Path option the registries do not know is reported, whatever its name.
        parser, _ = build_parser()
        subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        video = subparsers.choices["video"]
        video.add_argument("--frames", type=Path)
        corpus_build = next(
            action for action in subparsers.choices["corpus"]._actions if isinstance(action, argparse._SubParsersAction)
        ).choices["build"]
        corpus_build.add_argument("--dest", type=Path)
        subparsers.choices["scan"].add_argument("export", type=Path, nargs="?")
        self.assertEqual(
            cli_inputs.unclassified_path_arguments(parser),
            {"video --frames": "frames", "corpus build --dest": "dest", "scan export": "export"},
        )

    def test_output_folders_inside_the_examined_folder_are_refused(self) -> None:
        bundle = self.root / "bundle"
        bundle.mkdir()
        cases = [
            ["video", str(self.folder), "--out", str(self.root / "v.json"), "--frame-root", str(self.folder / "frames"), "--extract"],
            ["video", str(self.folder), "--out", str(self.root / "v.json"), "--frame-root", str(self.folder / "frames")],
            ["video", str(self.folder), "--out", str(self.root / "v.json"), "--frame-root", str(self.folder)],
            ["train-neural-plan", str(self.folder), "--out", str(self.root / "n.json"), "--output-dir", str(self.folder / "train")],
            ["vendor-weights", "--install", str(bundle), "--to", str(bundle / "models")],
            ["vendor-weights", "--install", str(bundle), "--models-dir", str(bundle / "models")],
        ]
        for argv in cases:
            with self.subTest(argv=" ".join(argv[:1] + argv[-3:])):
                self._assert_usage(argv, "출력 경로가 검사 대상 폴더 안에 있습니다: ")
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["a.txt"])
        self.assertEqual(list(bundle.iterdir()), [])
        # beside the examined folder: accepted
        code, _, stderr = self._run(["train-neural-plan", str(self.folder), "--out", str(self.root / "n.json"), "--output-dir", str(self.root / "train")])
        self.assertEqual(code, 0, stderr)


if __name__ == "__main__":
    unittest.main()
