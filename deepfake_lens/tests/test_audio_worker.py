"""R19-2 (round 19): librosa's numba disk cache and native crashes no longer kill a scan.

Four concurrent scans of a video with an audio track (main venv: librosa +
numba) corrupted ``site-packages/librosa/__pycache__/*.nbi``/``*.nbc``, and
every later scan of that file died with SIGSEGV (rc 139, no output, no
report) until the cache files were deleted.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from deepfake_lens import audio_worker, shutdown
from deepfake_lens.result_types import CoverageStatus

PACKAGE = Path(__file__).resolve().parents[1]
HAVE_LIBROSA = importlib.util.find_spec("librosa") is not None


def _write_tone(path: Path, *, seconds: int = 2, hz: float = 330.0) -> None:
    rate = 16000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", int(0.4 * math.sin(2 * math.pi * hz * n / rate) * 32767)) for n in range(rate * seconds)))


def _clean_env(home: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("DEEPFAKE_LENS_", "NUMBA_"))}
    env.update(PYTHONPATH=str(PACKAGE.parent), HOME=home, PYTHONDONTWRITEBYTECODE="1")
    return env


@unittest.skipIf(os.name != "posix", "POSIX signals")
class NativeCrashIsAFailedCheckTest(unittest.TestCase):
    """A native crash inside the feature extraction fails the audio_features check; the scanning process lives on."""

    def test_a_segfault_in_the_extraction_is_failed_audio_features(self) -> None:
        from deepfake_lens.core import analyze_file

        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp, "stub", "librosa")
            stub.mkdir(parents=True)
            # Harmless in this process (the dependency probe imports it); a real
            # SIGSEGV (a read of address 0) in any other process — the worker.
            (stub / "__init__.py").write_text(
                f"import ctypes, os\nif os.getpid() != {os.getpid()}:\n    ctypes.string_at(0)\n", encoding="utf-8"
            )
            wav = Path(tmp, "tone.wav")
            _write_tone(wav)
            # The stub is found first by this process (the dependency probe) and by the worker (PYTHONPATH).
            pythonpath = os.pathsep.join([str(stub.parent), *filter(None, [os.environ.get("PYTHONPATH")])])
            with mock.patch.object(sys, "path", [str(stub.parent), *sys.path]), mock.patch.dict(sys.modules), \
                    mock.patch.dict(os.environ, {"PYTHONPATH": pythonpath}):
                sys.modules.pop("librosa", None)
                item = analyze_file(wav)
        assert item.result is not None
        (entry,) = [entry for entry in item.result.coverage if entry.check == "audio_features"]
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertTrue(entry.reason.startswith("NativeCrashError: "), entry.reason)
        self.assertIn("SIGSEGV", entry.reason)
        self.assertEqual(shutdown.running_children(), [])  # the dead worker was reaped and forgotten

    def test_the_worker_does_not_inherit_a_runtime_sys_path(self) -> None:
        """A folder this process put on sys.path at run time (cv2 adds site-packages/cv2, whose typing/ then
        shadowed the standard library's in a worker given that path: exit 1 at start-up) is not the worker's."""
        with tempfile.TemporaryDirectory() as tmp:
            shadow = Path(tmp, "shadow")
            shadow.mkdir()
            (shadow / "typing.py").write_text("raise ImportError('shadowed typing')\n", encoding="utf-8")
            wav = Path(tmp, "tone.wav")
            _write_tone(wav, seconds=1)
            with mock.patch.object(sys, "path", [str(shadow), *sys.path]):
                reply = audio_worker.extract_features(str(wav), segment_seconds=5)
        self.assertTrue("features" in reply or "missing" in reply, reply)
        self.assertEqual(shutdown.running_children(), [])  # outside a scope: one request, then ended

    def test_a_decode_error_in_the_worker_keeps_its_class_and_message(self) -> None:
        error = {"class": "LibsndfileError", "message": "Error opening '<x>': Format not recognised.", "traceback": ""}
        rebuilt = audio_worker._rebuilt_exception(error)
        self.assertEqual(type(rebuilt).__name__, "LibsndfileError")
        self.assertEqual(str(rebuilt), error["message"])
        missing = audio_worker._rebuilt_exception({"class": "FileNotFoundError", "message": "m", "errno": 2, "strerror": "No such file", "filename": "/a/b.wav"})
        self.assertIsInstance(missing, OSError)
        self.assertEqual((missing.errno, missing.filename), (2, "/a/b.wav"))


class PrivateNumbaCacheTest(unittest.TestCase):
    """No process writes numba's shared cache: each compiles into its own folder, seeded from a published copy."""

    def test_the_process_cache_is_private_and_in_the_session_folder(self) -> None:
        script = textwrap.dedent(
            """
            import json, os, sys
            from deepfake_lens import audio_worker
            from deepfake_lens.native_path import scratch_dir
            audio_worker.prepare_process_numba_cache()
            print(json.dumps({"dir": os.environ.get("NUMBA_CACHE_DIR"), "scratch": scratch_dir()}))
            """
        )
        with tempfile.TemporaryDirectory() as home:
            env = _clean_env(home)
            env["NUMBA_CACHE_DIR"] = home  # an operator's shared folder is replaced
            done = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120, cwd=home)
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.loads(done.stdout)
        self.assertTrue(seen["dir"].startswith(seen["scratch"] + os.sep), seen)

    def test_publish_is_one_rename_and_never_overwrites(self) -> None:
        # R22-9: the environment's cache root is left out here (this test's content must never be published there)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(audio_worker, "shared_cache_root", return_value=Path(tmp, "shared")), \
                mock.patch.object(audio_worker, "environment_cache_root", return_value=None):
            one, two = Path(tmp, "one"), Path(tmp, "two")
            for folder, text in ((one, "first"), (two, "second")):
                (folder / "sub").mkdir(parents=True)
                (folder / "sub" / "f.nbi").write_text(text, encoding="utf-8")
            self.assertTrue(audio_worker.publish_cache(str(one)))
            self.assertFalse(audio_worker.publish_cache(str(two)))
            self.assertEqual(Path(tmp, "shared", "published", "sub", "f.nbi").read_text(encoding="utf-8"), "first")
            self.assertEqual(sorted(os.listdir(Path(tmp, "shared"))), ["published"])  # no staging folder left
            with mock.patch("deepfake_lens.native_path.scratch_dir", return_value=tmp):
                seeded, was_seeded = audio_worker.private_cache_dir()
            self.assertTrue(was_seeded)
            self.assertEqual(Path(seeded, "sub", "f.nbi").read_text(encoding="utf-8"), "first")

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX uid")
    def test_a_new_home_is_seeded_from_the_environments_cache_and_publishes_its_own(self) -> None:
        """R22-9 (round 22): a published cache only under HOME made every new HOME compile librosa's numba functions
        again in its first worker (web upload of 20 WAV files 7.4 s against 4.8 s at 2130892, whose cache sat in
        site-packages). The environment's root seeds it; HOME's root still gets its own published copy."""
        from deepfake_lens import native_path

        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp, "venv")
            source = Path(tmp, "compiled")
            (source / "sub").mkdir(parents=True)
            (source / "sub" / "f.nbi").write_text("compiled", encoding="utf-8")
            native_path.set_tool_cache_base(None)
            with mock.patch.object(sys, "prefix", str(prefix)), mock.patch.dict(os.environ, {"HOME": str(Path(tmp, "home1"))}):
                self.assertTrue(audio_worker.publish_cache(str(source)))
            base = prefix / "var" / "deepfake-lens"
            self.assertEqual(oct(base.stat().st_mode & 0o777), oct(0o700))
            with mock.patch.object(sys, "prefix", str(prefix)), mock.patch.dict(os.environ, {"HOME": str(Path(tmp, "home2"))}), \
                    mock.patch("deepfake_lens.native_path.scratch_dir", return_value=tmp):
                seeded, all_published = audio_worker.private_cache_dir()
                self.assertFalse(all_published)  # home2 has none yet: the worker publishes there after its first file
                self.assertEqual(Path(seeded, "sub", "f.nbi").read_text(encoding="utf-8"), "compiled")
                self.assertTrue(audio_worker.publish_cache(seeded))
                self.assertTrue(Path(tmp, "home2", ".cache", "deepfake-lens", "numba").is_dir())
            # a folder others can write, or one inside an examined folder, is never used
            base.chmod(0o777)
            with mock.patch.object(sys, "prefix", str(prefix)):
                self.assertIsNone(audio_worker.environment_cache_root())
            base.chmod(0o700)
            with mock.patch.object(sys, "prefix", str(prefix)), mock.patch.object(native_path, "_EXAMINED_FOLDERS", [os.path.realpath(tmp)]):
                self.assertIsNone(audio_worker.environment_cache_root())

    def test_no_shared_cache_under_a_relative_home(self) -> None:
        with mock.patch.object(Path, "home", return_value=Path("relative-home")):
            self.assertIsNone(audio_worker.shared_cache_root())


@unittest.skipUnless(hasattr(os, "getuid"), "POSIX uid")
class EnvironmentCacheOutsideAnalyzedFileFoldersTest(unittest.TestCase):
    """R23-2 (round 23): the per-environment numba cache (R22-9, ``<sys.prefix>/var/deepfake-lens``) was skipped only
    inside a scanned folder. ``cd proj; .venv/bin/python -m deepfake_lens classify a.wav`` — a project folder holding
    its ``.venv`` and the sample — made ``proj/.venv/var/deepfake-lens/numba/<key>/published`` next to the evidence
    (QA-IN-1). The folders of the analyzed files are examined for it too, for every single-file command."""

    COMMANDS = (
        ["classify", "{a}"], ["forensic", "{a}"], ["explain", "{a}"], ["legal-report", "{a}"], ["audio", "{a}"],
        ["evidence", "{a}"], ["evidence-statement", "{a}"], ["compare", "{a}", "{b}"], ["multimodal", "{a}", "{b}"],
        ["scan", "{case}"], ["batch", "{case}"],
    )

    def setUp(self) -> None:
        from deepfake_lens import native_path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(native_path.set_examined_folders, [])
        self.addCleanup(native_path.set_tool_cache_base, None)
        self.root = Path(self._tmp.name).resolve()
        self.case = self.root / "proj"
        self.case.mkdir()
        _write_tone(self.case / "a.wav", seconds=1)
        _write_tone(self.case / "b.wav", seconds=1, hz=440.0)

    def test_no_single_file_command_uses_an_environment_inside_the_files_folder(self) -> None:
        import contextlib
        import io

        from deepfake_lens import native_path
        from deepfake_lens.cli import build_parser
        from deepfake_lens.cli_inputs import check_command_inputs

        parser, _ = build_parser()
        inside = self.case / ".venv"
        elsewhere = self.root / "venv-elsewhere"
        names = {"a": str(self.case / "a.wav"), "b": str(self.case / "b.wav"), "case": str(self.case)}
        with mock.patch.dict(os.environ, {"HOME": str(self.root / "home")}):
            for template in self.COMMANDS:
                argv = [part.format(**names) for part in template]
                with self.subTest(argv=argv[0]):
                    shutil.rmtree(inside, ignore_errors=True)  # each command on its own
                    with contextlib.redirect_stderr(io.StringIO()):
                        check_command_inputs(parser.parse_args(argv))
                    with mock.patch.object(sys, "prefix", str(inside)):
                        self.assertIsNone(audio_worker.environment_cache_root())
                        self.assertNotIn(inside / "var" / "deepfake-lens" / "numba" / audio_worker._runtime_key(), audio_worker._cache_roots())
                    self.assertFalse(inside.exists())  # not even the folder (claim_tool_base would have made it)
                    # an environment outside the files' folders is still used (R22-9)
                    with mock.patch.object(sys, "prefix", str(elsewhere)):
                        self.assertEqual(audio_worker.environment_cache_root(), elsewhere / "var" / "deepfake-lens" / "numba" / audio_worker._runtime_key())
                    native_path.set_examined_folders([])

    @unittest.skipUnless(HAVE_LIBROSA, "librosa not installed")
    def test_a_project_venv_next_to_the_sample_gets_nothing(self) -> None:
        """The verifier's layout, end to end: a real venv in the sample's folder runs classify / evidence / audio."""
        import sysconfig

        venv = self.case / ".venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
        purelib = Path(subprocess.run([str(venv / "bin" / "python"), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                      capture_output=True, text=True, check=True, timeout=60).stdout.strip())
        # R24-10 (round 24): every site folder this interpreter imports from, in its order — not only its own purelib /
        # platlib. In HANDOFF §5's qa-main side venv the purelib holds a .pth naming /venv's and /venv-api's
        # site-packages (a .pth inside a folder named by a .pth is not processed), so the project venv found no
        # librosa: audio_features skipped, 3/3 failures there (it passed in /venv and /venv-api).
        site_dirs = [entry for entry in sys.path if entry and os.path.isdir(entry) and Path(entry).name in ("site-packages", "dist-packages")]
        paths = list(dict.fromkeys([*site_dirs, sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"], str(PACKAGE.parent)]))
        (purelib / "deepfake-lens-test.pth").write_text("\n".join(paths) + "\n", encoding="utf-8")
        env = _clean_env(str(self.root / "home"))
        env.pop("PYTHONPATH")

        def snapshot() -> list[tuple[str, int, int]]:
            return sorted((str(p.relative_to(self.case)), p.lstat().st_size, p.lstat().st_mtime_ns) for p in self.case.rglob("*"))

        before = snapshot()
        for argv in (["classify", "a.wav", "--format", "json"], ["evidence", "a.wav"], ["audio", "b.wav"]):
            with self.subTest(argv=argv[0]):
                done = subprocess.run([str(venv / "bin" / "python"), "-m", "deepfake_lens", *argv], env=env, cwd=self.case,
                                      capture_output=True, text=True, timeout=600)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                if argv[0] == "classify":
                    coverage = {entry["check"]: entry["status"] for entry in json.loads(done.stdout)["coverage"]}
                    self.assertEqual(coverage["audio_features"], "ran")
        self.assertEqual(snapshot(), before)
        self.assertFalse((venv / "var").exists())
        # the worker still published its cache — under HOME's tool folder
        self.assertEqual(len(list(Path(self.root, "home", ".cache", "deepfake-lens", "numba").glob("*/published"))), 1)


@unittest.skipUnless(HAVE_LIBROSA, "librosa not installed")
class ConcurrentScansTest(unittest.TestCase):
    """Four scans of the same audio at once, from an empty numba cache, then one more: all finish with the same rows."""

    def _scan(self, folder: Path, env: dict[str, str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-m", "deepfake_lens", "scan", str(folder), "--format", "json"],
            env=env, cwd=env["HOME"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def test_four_concurrent_scans_and_a_fifth(self) -> None:
        import librosa

        shared_before = sorted(p.name for p in Path(librosa.__file__).parent.rglob("*.nb[ic]"))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "evidence")
            folder.mkdir()
            _write_tone(folder / "voice.wav")
            env = _clean_env(tmp)
            procs = [self._scan(folder, env) for _ in range(4)]
            outputs = [proc.communicate(timeout=600) for proc in procs]
            for proc, (out, err) in zip(procs, outputs):
                self.assertEqual(proc.returncode, 0, err[-2000:])
            fifth = self._scan(folder, env)
            out5, err5 = fifth.communicate(timeout=600)
            self.assertEqual(fifth.returncode, 0, err5[-2000:])
            rows = [json.loads(out)["items"] for out, _ in outputs] + [json.loads(out5)["items"]]
            published = list(Path(tmp, ".cache", "deepfake-lens", "numba").glob("*/published"))
            self.assertEqual(sorted(os.listdir(folder)), ["voice.wav"])  # QA-IN-1: nothing written next to the evidence
        coverage = [{entry["check"]: entry["status"] for entry in items[0]["result"]["coverage"]} for items in rows]
        self.assertEqual(coverage[0]["audio_features"], "ran")
        for other in coverage[1:]:
            self.assertEqual(other, coverage[0])
        self.assertEqual(len(published), 1)  # the first finished worker published its cache, once
        # Nothing was compiled into librosa's own folder (the shared cache the scans used to corrupt).
        self.assertEqual(sorted(p.name for p in Path(librosa.__file__).parent.rglob("*.nb[ic]")), shared_before)

    def test_threads_of_one_scan_use_their_own_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp, "a.wav")
            _write_tone(wav, seconds=1)
            with audio_worker.worker_scope() as scope:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    replies = list(pool.map(scope.bind(lambda _: audio_worker.extract_features(str(wav), segment_seconds=5)), range(4)))
                self.assertLessEqual(len(scope._workers), 2)
            self.assertEqual(shutdown.running_children(), [])  # the scope ended its workers
        self.assertEqual(len({json.dumps(reply, sort_keys=True) for reply in replies}), 1)
        self.assertIsNotNone(replies[0]["features"])


def _crash_stub(tmp: str) -> Path:
    """A ``librosa`` whose ``load`` crashes the process (SIGSEGV) for a "crash*" file and raises ValueError otherwise."""
    stub = Path(tmp, "stub", "librosa")
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text(
        "import ctypes, os\n"
        "def load(path, **kw):\n"
        "    if os.path.basename(str(path)).startswith('crash'):\n"
        "        ctypes.string_at(0)\n"
        "    raise ValueError('stub decode ' + os.path.basename(str(path)))\n",
        encoding="utf-8",
    )
    return stub.parent


class OneWorkerPerScopeTest(unittest.TestCase):
    """R20-2 (round 20): outside a scan the worker was started per file (about 2 s each, 7 s without a published
    numba cache) — batch of 100 WAV files 11.8 s → 87 s, a web upload of 30 WAV files 3.2 s → 30 s, eval /
    benchmark / calibration the same. Every multi-file command and request now runs in one scope: one worker
    per thread for all of its files."""

    def _counting(self):
        started: list[audio_worker._Worker] = []
        real = audio_worker._start

        def start() -> audio_worker._Worker:
            worker = real()
            started.append(worker)
            return worker

        return started, mock.patch.object(audio_worker, "_start", start)

    def test_a_scope_asks_one_worker_for_many_files_and_ends_it(self) -> None:
        started, counting = self._counting()
        with tempfile.TemporaryDirectory() as tmp, counting:
            wav = Path(tmp, "tone.wav")
            _write_tone(wav, seconds=1)
            with audio_worker.worker_scope() as scope:
                replies = [audio_worker.extract_features(str(wav), segment_seconds=5) for _ in range(4)]
                with audio_worker.worker_scope() as inner:  # a scan inside the command: the same scope
                    self.assertIs(inner, scope)
                    audio_worker.extract_features(str(wav), segment_seconds=5)
                self.assertEqual(scope.size(), 1)
                self.assertEqual(len(shutdown.running_children()), 1)  # the inner scope did not end it
            self.assertEqual(shutdown.running_children(), [])  # the outermost scope ended it
        self.assertEqual(len(started), 1)
        self.assertEqual(len({json.dumps(reply, sort_keys=True) for reply in replies}), 1)

    def test_a_worker_runs_its_numeric_libraries_on_one_thread(self) -> None:
        """Each worker's OpenMP / OpenBLAS pool spun on every core (a scan of 300 audio files: 200 s CPU for 50 s)."""
        seen: dict[str, str] = {}

        def start(argv, **kwargs):
            seen.update(kwargs["env"])
            raise OSError("not started")

        variables = {name: value for name, value in os.environ.items() if name not in audio_worker.WORKER_THREAD_ENV}
        with mock.patch.dict(os.environ, {**variables, "OMP_NUM_THREADS": "3"}, clear=True), \
                mock.patch.object(shutdown, "start_tracked", start), self.assertRaises(OSError):
            audio_worker._Worker()
        # R21-2 (round 21): the operator's value was kept ("3" here) and no cache key recorded it — a worker's
        # features then depended on it (the last float32 digits of an MP4's MFCC standard deviations, 1 thread vs
        # 4). A worker now always runs its numeric libraries on one thread.
        self.assertEqual({name: seen[name] for name in audio_worker.WORKER_THREAD_ENV},
                         {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMBA_NUM_THREADS": "1"})

    @unittest.skipUnless(HAVE_LIBROSA, "librosa not installed")
    def test_an_operators_thread_count_does_not_change_a_workers_features(self) -> None:
        """R21-2 (round 21): the same file's features with OMP_NUM_THREADS=4 set by the operator and without it."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp, "tone.wav")
            _write_tone(wav, seconds=3, hz=211.0)
            replies = []
            for value in ("4", None):
                variables = {name: value for name, value in os.environ.items() if name not in audio_worker.WORKER_THREAD_ENV}
                if value is not None:
                    variables.update({name: value for name in audio_worker.WORKER_THREAD_ENV})
                with mock.patch.dict(os.environ, variables, clear=True):
                    replies.append(audio_worker.extract_features(str(wav), segment_seconds=5))
        self.assertIsNotNone(replies[0].get("features"))
        self.assertEqual(json.dumps(replies[0], sort_keys=True), json.dumps(replies[1], sort_keys=True))

    def test_outside_a_scope_a_worker_serves_one_request(self) -> None:
        started, counting = self._counting()
        with tempfile.TemporaryDirectory() as tmp, counting:
            wav = Path(tmp, "tone.wav")
            _write_tone(wav, seconds=1)
            for _ in range(2):
                audio_worker.extract_features(str(wav), segment_seconds=5)
                self.assertEqual(shutdown.running_children(), [])
        self.assertEqual(len(started), 2)

    def test_a_worker_of_an_ended_thread_is_never_reused(self) -> None:
        """A finished pool thread's ident may be reused; its worker's supervisor died with the thread (Linux)."""
        started, counting = self._counting()
        with tempfile.TemporaryDirectory() as tmp, counting:
            wav = Path(tmp, "tone.wav")
            _write_tone(wav, seconds=1)
            with audio_worker.worker_scope() as scope:
                for _ in range(2):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(scope.bind(audio_worker.extract_features), str(wav), segment_seconds=5).result()
                self.assertLessEqual(scope.size(), 1)  # the first thread's worker was ended when the second started
            self.assertEqual(shutdown.running_children(), [])
        self.assertEqual(len(started), 2)
        self.assertFalse(started[0].alive())

    @unittest.skipIf(os.name != "posix", "POSIX signals")
    def test_a_crash_in_a_scope_fails_that_file_and_the_next_file_gets_a_new_worker(self) -> None:
        """R19-2's crash isolation holds inside a long-lived scope: the crash fails one file, nothing else."""
        started, counting = self._counting()
        with tempfile.TemporaryDirectory() as tmp, counting:
            stub = _crash_stub(tmp)
            pythonpath = os.pathsep.join([str(stub), *filter(None, [os.environ.get("PYTHONPATH")])])
            names = ["a.wav", "crash.wav", "b.wav"]
            for name in names:
                Path(tmp, name).write_bytes(b"RIFF")
            outcomes: list[str] = []
            with mock.patch.dict(os.environ, {"PYTHONPATH": pythonpath}), audio_worker.worker_scope():
                for name in names:
                    try:
                        audio_worker.extract_features(str(Path(tmp, name)), segment_seconds=5)
                    except Exception as exc:  # noqa: BLE001
                        outcomes.append(f"{type(exc).__name__}: {exc}")
            self.assertEqual(shutdown.running_children(), [])
        self.assertEqual(outcomes[0], "ValueError: stub decode a.wav")
        self.assertTrue(outcomes[1].startswith("NativeCrashError: "), outcomes[1])
        self.assertIn("SIGSEGV", outcomes[1])
        self.assertEqual(outcomes[2], "ValueError: stub decode b.wav")  # its own answer, from a new worker
        self.assertEqual(len(started), 2)


class FramedProtocolTest(unittest.TestCase):
    """R20-3 (round 20): one line written to the worker's fd 1 before it moved fd 1 to the log (a sitecustomize,
    a .pth file, a native initializer) made the parent's ``json.loads`` fail for the first file — and the worker
    was kept: every later file got the reply meant for the file before it (s1 carried s0's harmonic stability,
    s2 "high mean pitch (900Hz)" of s1 …), with no warning. Replies are now framed: a ready line after the
    redirect, the worker's token and the request id in every reply; anything else stops the worker and fails
    that one file's check."""

    def _fake(self, output: bytes, *, ready: bool = True) -> audio_worker._Worker:
        import io
        from types import SimpleNamespace

        worker = audio_worker._Worker.__new__(audio_worker._Worker)
        worker.token, worker.next_id, worker.ready, worker.expired = "t0k", 0, ready, False
        worker.proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(output))  # type: ignore[assignment]
        return worker

    def test_a_framed_reply_to_this_request_is_accepted(self) -> None:
        worker = self._fake(b'{"missing": "librosa", "token": "t0k", "id": 1}\n')
        self.assertEqual(worker.ask({"path": "a"}, 5), {"missing": "librosa"})
        self.assertEqual(json.loads(worker.proc.stdin.getvalue())["id"], 1)

    def test_start_up_output_before_the_ready_line_is_skipped(self) -> None:
        worker = self._fake(b'hello from a native init\n{"ready": "t0k"}\n{"missing": "x", "token": "t0k", "id": 1}\n', ready=False)
        self.assertEqual(worker.ask({"path": "a"}, 5), {"missing": "x"})
        self.assertTrue(worker.ready)

    def test_endless_start_up_output_is_a_protocol_error(self) -> None:
        worker = self._fake(b"noise\n" * (audio_worker._MAX_STARTUP_LINES + 1), ready=False)
        with self.assertRaises(audio_worker.WorkerProtocolError):
            worker.ask({"path": "a"}, 5)

    def test_every_other_reply_is_a_protocol_error(self) -> None:
        replies = {
            "not json": b"hello\n",
            "a list": b"[1]\n",
            "another id": b'{"features": null, "token": "t0k", "id": 7}\n',
            "no id": b'{"features": null, "token": "t0k"}\n',
            "another token": b'{"features": null, "token": "zzz", "id": 1}\n',
            "no newline": b'{"features": null, "token": "t0k", "id": 1}',
        }
        for name, output in replies.items():
            with self.subTest(name), self.assertRaises(audio_worker.WorkerProtocolError) as caught:
                self._fake(output).ask({"path": "a"}, 5)
            self.assertIn("이 검사만 실패로 기록했습니다", str(caught.exception))

    def test_a_desynchronized_worker_is_stopped_and_the_next_file_gets_a_new_one(self) -> None:
        bad = self._fake(b'{"features": null, "token": "t0k", "id": 99}\n')
        bad.owner = threading.current_thread()
        good = self._fake(b'{"missing": "librosa", "token": "t0k", "id": 1}\n')
        good.owner = threading.current_thread()
        pending = [bad, good]
        stopped: list[object] = []
        with mock.patch.object(audio_worker, "_start", lambda: pending.pop(0)), \
                mock.patch.object(shutdown, "stop_tracked", stopped.append), \
                mock.patch.object(audio_worker._Worker, "close", lambda self: None), \
                mock.patch.object(audio_worker._Worker, "alive", lambda self: True), \
                audio_worker.worker_scope() as scope:
            with self.assertRaises(audio_worker.WorkerProtocolError):
                audio_worker.extract_features("/a.wav", segment_seconds=5)
            self.assertEqual(stopped, [bad.proc])
            self.assertEqual(audio_worker.extract_features("/b.wav", segment_seconds=5), {"missing": "librosa"})
            self.assertEqual(scope.size(), 1)
        self.assertEqual(pending, [])

    @unittest.skipIf(os.name != "posix", "POSIX signals")
    def test_start_up_output_of_a_real_worker_does_not_shift_the_replies(self) -> None:
        """The verifier's case end to end: each file gets its own reply (the stub's decode error names the file)."""
        with tempfile.TemporaryDirectory() as tmp:
            stub = _crash_stub(tmp)
            marker = Path(tmp, "printed")
            (stub / "sitecustomize.py").write_text(
                "import os, sys\n"
                "if 'audio_worker' in ''.join(getattr(sys, 'orig_argv', [])):\n"
                "    os.write(1, b'hello from a native init\\n')\n"
                f"    open({str(marker)!r}, 'a').close()\n",
                encoding="utf-8",
            )
            pythonpath = os.pathsep.join([str(stub), *filter(None, [os.environ.get("PYTHONPATH")])])
            names = ["s0.wav", "s1.wav", "s2.wav", "s3.wav"]
            for name in names:
                Path(tmp, name).write_bytes(b"RIFF")
            reasons: list[str] = []
            with mock.patch.dict(os.environ, {"PYTHONPATH": pythonpath}), audio_worker.worker_scope() as scope:
                for name in names:
                    try:
                        audio_worker.extract_features(str(Path(tmp, name)), segment_seconds=5)
                    except Exception as exc:  # noqa: BLE001
                        reasons.append(f"{type(exc).__name__}: {exc}")
                self.assertEqual(scope.size(), 1)
            self.assertEqual(shutdown.running_children(), [])
            self.assertTrue(marker.exists())  # the worker did write to its fd 1 before the ready line
        self.assertEqual(reasons, [f"ValueError: stub decode {name}" for name in names])


class StartUpFramingTest(unittest.TestCase):
    """R21-3 (round 21): start-up output on the worker's fd 1 *without a newline* ("partial line without newline",
    then the ready line on the same line) made the ready line unreadable. The request had already been sent, so the
    worker's reply was skipped as start-up output as well and the parent waited 600 s per file for another line —
    one WAV file took 603 s and its check was "NativeCrashError: … 비정상 종료했습니다(종료 코드 0)". The ready marker
    is now found in the byte stream, before any request is sent, within its own short time limit; a worker that never
    sends it is a start-up error (:class:`audio_worker.WorkerStartError`), never a native crash."""

    _fake = FramedProtocolTest._fake

    def test_a_ready_marker_after_output_without_a_newline_is_found(self) -> None:
        worker = self._fake(b'partial line without newline{"ready": "t0k"}\n{"missing": "x", "token": "t0k", "id": 1}\n', ready=False)
        self.assertEqual(worker.ask({"path": "a"}, 5), {"missing": "x"})

    def test_no_request_is_sent_before_the_ready_marker(self) -> None:
        worker = self._fake(b"partial line without newline", ready=False)
        with self.assertRaises(audio_worker.WorkerStartError) as caught:
            worker.ask({"path": "a"}, 5)
        self.assertEqual(worker.proc.stdin.getvalue(), b"")
        self.assertIn("준비 신호", str(caught.exception))
        self.assertIsInstance(caught.exception, audio_worker.WorkerProtocolError)  # stopped and dropped like one

    def test_a_worker_ended_by_the_shutdown_before_it_is_ready_is_the_shutdown(self) -> None:
        worker = self._fake(b"partial", ready=False)
        with mock.patch.object(shutdown, "active", return_value=True), self.assertRaises(shutdown.ShuttingDown):
            worker.ask({"path": "a"}, 5)

    def test_endless_output_without_a_newline_is_a_start_up_error(self) -> None:
        worker = self._fake(b"x" * (audio_worker._MAX_STARTUP_BYTES + 10), ready=False)
        with self.assertRaises(audio_worker.WorkerStartError):
            worker.ask({"path": "a"}, 5)

    def _stub_with_sitecustomize(self, tmp: str, body: str) -> str:
        stub = _crash_stub(tmp)
        (stub / "sitecustomize.py").write_text(
            "import os, sys, time\n"
            "if 'audio_worker' in ''.join(getattr(sys, 'orig_argv', [])):\n" + textwrap.indent(body, "    "),
            encoding="utf-8",
        )
        return os.pathsep.join([str(stub), *filter(None, [os.environ.get("PYTHONPATH")])])

    def _outcomes(self, pythonpath: str, names: list[str], tmp: str) -> list[str]:
        outcomes: list[str] = []
        for name in names:
            Path(tmp, name).write_bytes(b"RIFF")
        with mock.patch.dict(os.environ, {"PYTHONPATH": pythonpath}), audio_worker.worker_scope():
            for name in names:
                try:
                    audio_worker.extract_features(str(Path(tmp, name)), segment_seconds=5)
                except Exception as exc:  # noqa: BLE001
                    outcomes.append(f"{type(exc).__name__}: {exc}")
        self.assertEqual(shutdown.running_children(), [])
        return outcomes

    @unittest.skipIf(os.name != "posix", "POSIX signals")
    def test_a_real_worker_with_output_without_a_newline_answers_each_file(self) -> None:
        """The verifier's case (proto.sh): 4 WAV files did not finish in 150 s; each now gets its own reply at once."""
        import time

        names = ["s0.wav", "s1.wav", "s2.wav", "s3.wav"]
        with tempfile.TemporaryDirectory() as tmp:
            pythonpath = self._stub_with_sitecustomize(tmp, "os.write(1, b'partial line without newline')\n")
            started = time.monotonic()
            outcomes = self._outcomes(pythonpath, names, tmp)
        self.assertEqual(outcomes, [f"ValueError: stub decode {name}" for name in names])
        self.assertLess(time.monotonic() - started, 60)

    @unittest.skipIf(os.name != "posix", "POSIX signals")
    def test_a_worker_that_never_gets_ready_is_a_start_up_error_within_its_limit(self) -> None:
        import time

        with tempfile.TemporaryDirectory() as tmp:
            pythonpath = self._stub_with_sitecustomize(tmp, "os.write(1, b'partial line without newline')\ntime.sleep(120)\n")
            started = time.monotonic()
            with mock.patch.object(audio_worker, "WORKER_READY_TIMEOUT_SECONDS", 2.0):
                [outcome] = self._outcomes(pythonpath, ["a.wav"], tmp)
        self.assertLess(time.monotonic() - started, 30)
        self.assertTrue(outcome.startswith("WorkerStartError: "), outcome)
        self.assertIn("2초 안에 준비 신호가 오지 않음", outcome)

    @unittest.skipIf(os.name != "posix", "POSIX signals")
    def test_a_worker_that_ends_before_it_is_ready_is_not_a_native_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pythonpath = self._stub_with_sitecustomize(tmp, "os.write(1, b'partial')\nos._exit(0)\n")
            outcomes = self._outcomes(pythonpath, ["a.wav", "b.wav"], tmp)
        for outcome in outcomes:
            self.assertTrue(outcome.startswith("WorkerStartError: "), outcome)
            self.assertIn("준비 신호 전에 끝남 — 종료 코드 0", outcome)


class StartFailureBreakerTest(unittest.TestCase):
    """R22-5 (round 22): a worker that never sends its ready marker cost WORKER_READY_TIMEOUT_SECONDS (60 s) per
    file — the failed worker is dropped and the next file starts another (4 WAVs: 240 s; 300 audio files: about
    5 hours), with nothing to stop it. After WORKER_START_BREAKER_FAILURES start failures in a row, a scope starts
    no more workers and every later file's audio check fails at once with the breaker's reason."""

    _stub_with_sitecustomize = StartUpFramingTest._stub_with_sitecustomize
    _outcomes = StartUpFramingTest._outcomes

    @unittest.skipIf(os.name != "posix", "POSIX signals")
    def test_a_scope_stops_starting_workers_that_never_get_ready(self) -> None:
        import time

        started_workers: list[object] = []
        real_start = audio_worker._start

        def counting_start() -> object:
            worker = real_start()
            started_workers.append(worker)
            return worker

        names = [f"s{index}.wav" for index in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            pythonpath = self._stub_with_sitecustomize(tmp, "os.write(1, b'partial line without newline')\ntime.sleep(120)\n")
            started = time.monotonic()
            with mock.patch.object(audio_worker, "WORKER_READY_TIMEOUT_SECONDS", 2.0), \
                    mock.patch.object(audio_worker, "_start", counting_start):
                outcomes = self._outcomes(pythonpath, names, tmp)
            elapsed = time.monotonic() - started
        self.assertEqual(len(started_workers), audio_worker.WORKER_START_BREAKER_FAILURES)
        self.assertLess(elapsed, 2.0 * audio_worker.WORKER_START_BREAKER_FAILURES + 20)
        for outcome in outcomes[:2]:
            self.assertIn("2초 안에 준비 신호가 오지 않음", outcome)
        for outcome in outcomes[2:]:
            self.assertTrue(outcome.startswith("WorkerStartError: 오디오 특징 추출 작업자가 이 검사에서 연속 2번 준비 신호를 보내지 않아"), outcome)
            self.assertIn("처음 사유: 오디오 특징 추출 작업자가 준비 신호를 보내지 않아 중단했습니다(2초 안에 준비 신호가 오지 않음)", outcome)

    def test_a_worker_that_gets_ready_ends_the_run_of_failures(self) -> None:
        scope = audio_worker.WorkerScope()
        failure = audio_worker.WorkerStartError("x")
        scope.start_failed(failure)
        scope.start_succeeded()
        scope.start_failed(failure)
        self.assertIsNone(scope.tripped)
        scope.start_failed(failure)
        self.assertIsNotNone(scope.tripped)
        scope.start_succeeded()
        self.assertIsNotNone(scope.tripped)  # a tripped scope stays tripped until it ends

    @unittest.skipIf(os.name != "posix", "POSIX signals")
    def test_outside_a_scope_each_file_gets_its_own_worker(self) -> None:
        """One file (classify, analyze-file) has no breaker: the next request starts a worker again."""
        with tempfile.TemporaryDirectory() as tmp:
            pythonpath = self._stub_with_sitecustomize(tmp, "os.write(1, b'partial')\nos._exit(0)\n")
            Path(tmp, "a.wav").write_bytes(b"RIFF")
            with mock.patch.dict(os.environ, {"PYTHONPATH": pythonpath}):
                for _ in range(3):
                    with self.assertRaises(audio_worker.WorkerStartError) as caught:
                        audio_worker.extract_features(str(Path(tmp, "a.wav")), segment_seconds=5)
                    self.assertIn("준비 신호 전에 끝남", str(caught.exception))
            self.assertEqual(shutdown.running_children(), [])


class MultiFileCommandsRunInOneScopeTest(unittest.TestCase):
    """R20-2: each function that analyzes many files runs inside a worker scope (the analysis sees an active scope)."""

    def _scope_seen(self, target: str, call) -> list[object]:
        seen: list[object] = []

        def record(*args, **kwargs):
            seen.append(getattr(audio_worker._LOCAL, "scope", None))
            raise OSError("stop")

        with mock.patch(target, record):
            try:
                call()
            except Exception:  # noqa: BLE001 — the recorder stops the analysis
                pass
        return seen

    def test_eval_calibration_feedback_fusion_upload_and_report_are_scoped(self) -> None:
        from deepfake_lens import evaluate, feedback, fusion, webapp_api

        with tempfile.TemporaryDirectory() as tmp:
            for label in ("real", "fake"):
                Path(tmp, label).mkdir()
                _write_tone(Path(tmp, label, "x.wav"), seconds=1)
                Path(tmp, label, "x.png").write_bytes(b"not decoded: the analysis is replaced")  # datasets list images and text only
            entry = feedback.FeedbackEntry(path=str(Path(tmp, "real", "x.wav")), expected_label="real")
            boundary = "b0"
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"x.wav\"\r\n\r\n").encode() + Path(tmp, "real", "x.wav").read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            cases = {
                "evaluate_dataset": ("deepfake_lens.evaluate.analyze_file", lambda: evaluate.evaluate_dataset(tmp)),
                "_score_dataset": ("deepfake_lens.evaluate.analyze_file", lambda: evaluate.calibrate_dataset(tmp)),
                "observations_live": ("deepfake_lens.feedback.analyze_file", lambda: feedback.observations_live([entry])),
                "calibrate_fusion_profile": ("deepfake_lens.fusion.analyze_file", lambda: fusion.calibrate_fusion_profile(tmp)),
                "_analyze_upload_payload": ("deepfake_lens.webapp_api.analyze_path", lambda: webapp_api._analyze_upload_payload(f"multipart/form-data; boundary={boundary}", body)),
            }
            for name, (target, call) in cases.items():
                with self.subTest(name):
                    seen = self._scope_seen(target, call)
                    self.assertTrue(seen, "the analysis was not reached")
                    self.assertIsInstance(seen[0], audio_worker.WorkerScope)
            self.assertIsNone(getattr(audio_worker._LOCAL, "scope", None))  # every scope ended
        self.assertTrue(getattr(webapp_api._rederive_report_items, "__wrapped__", None))  # in_worker_scope

    def test_batch_binds_its_thread_pool_to_one_scope(self) -> None:
        import contextlib
        import io

        from deepfake_lens.cli import main as cli_main

        seen: list[object] = []

        def record(path, *args, **kwargs):
            seen.append(getattr(audio_worker._LOCAL, "scope", None))
            raise OSError("stop")

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out, mock.patch("deepfake_lens.cli.analyze_path", record):
            for name in ("a.wav", "b.wav", "c.wav"):
                _write_tone(Path(tmp, name), seconds=1)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli_main(["batch", tmp, "--workers", "2", "--output", str(Path(out, "b.json"))]), 0)
        self.assertEqual(len(seen), 3)
        self.assertEqual(len({id(scope) for scope in seen}), 1)
        self.assertIsInstance(seen[0], audio_worker.WorkerScope)


@unittest.skipUnless(HAVE_LIBROSA, "librosa not installed")
class MultiFileCommandsStartOneWorkerTest(unittest.TestCase):
    """R20-2 end to end (librosa installed): batch and upload of three WAV files start one worker, not three."""

    def _count_starts(self, call) -> int:
        started = 0
        real = audio_worker._start

        def start() -> audio_worker._Worker:
            nonlocal started
            started += 1
            return real()

        with mock.patch.object(audio_worker, "_start", start):
            call()
        self.assertEqual(shutdown.running_children(), [])
        return started

    def test_batch_upload_and_eval(self) -> None:
        import contextlib
        import io

        from deepfake_lens import evaluate, webapp_api
        from deepfake_lens.cli import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "wav")
            for label in ("real", "fake"):
                Path(folder, label).mkdir(parents=True)
            names = [Path(folder, "real", "a.wav"), Path(folder, "real", "b.wav"), Path(folder, "fake", "c.wav")]
            for index, path in enumerate(names):
                _write_tone(path, seconds=1, hz=220.0 + 110 * index)
            flat = Path(tmp, "flat")
            flat.mkdir()
            for path in names:
                (flat / path.name).write_bytes(path.read_bytes())
            boundary = "b0"
            body = b"".join(
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{path.name}\"\r\n\r\n").encode() + path.read_bytes() + b"\r\n"
                for path in names
            ) + f"--{boundary}--\r\n".encode()

            def batch() -> None:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli_main(["batch", str(flat), "--workers", "1", "--output", str(Path(tmp, "b.json"))]), 0)

            def upload() -> None:
                payload = webapp_api._analyze_upload_payload(f"multipart/form-data; boundary={boundary}", body)
                coverage = [{entry["check"]: entry["status"] for entry in row["result"]["coverage"]} for row in payload["items"]]
                self.assertEqual([entry["audio_features"] for entry in coverage], ["ran"] * 3)

            self.assertEqual(self._count_starts(batch), 1)
            self.assertEqual(self._count_starts(upload), 1)
            # eval / benchmark / calibration datasets list images and text only (datasets.SUPPORTED_DATASET_EXTENSIONS):
            # no worker at all — the scope starts one lazily, on the first audio file.
            self.assertEqual(self._count_starts(lambda: evaluate.evaluate_dataset(folder)), 0)


@unittest.skipUnless(HAVE_LIBROSA, "librosa not installed")
class RelativePathAudioParityTest(unittest.TestCase):
    """R23-1 (round 23): a relative audio path reached the worker unchanged and the worker (cwd = its cache folder)
    resolved it there — every audio check of ``scan case``, ``cd case; scan .``, ``classify case/a.wav``,
    ``batch case`` and ``web --folder case`` failed ("LibsndfileError … System error") since R19-2 (de59997),
    while the absolute path ran (QA-OUT-4: one file, one result). 2130892 (in-process extraction) ran in every case.
    Each relative form now gives exactly the absolute run's rows, with ``audio_features`` ``ran``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.project = Path(cls._tmp.name, "proj")
        cls.case = cls.project / "case"
        cls.case.mkdir(parents=True)
        _write_tone(cls.case / "a.wav", seconds=1)
        _write_tone(cls.case / "b.wav", seconds=1, hz=440.0)
        cls.env = _clean_env(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _cli(self, *args: str, cwd: Path) -> str:
        done = subprocess.run([sys.executable, "-m", "deepfake_lens", *args], env=self.env, cwd=cwd,
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        return done.stdout

    @staticmethod
    def _without(value: object, keys: set[str]) -> object:
        """``value`` without the given keys at any depth (the path as typed; a batch job's id and clock times)."""
        if isinstance(value, dict):
            return {key: RelativePathAudioParityTest._without(item, keys) for key, item in value.items() if key not in keys}
        if isinstance(value, list):
            return [RelativePathAudioParityTest._without(item, keys) for item in value]
        return value

    @staticmethod
    def _audio(items: list[dict]) -> list[str]:
        return [{entry["check"]: entry["status"] for entry in item["result"]["coverage"]}["audio_features"] for item in items]

    def test_scan_relative_and_from_inside_the_folder_equal_the_absolute_scan(self) -> None:
        absolute = json.loads(self._cli("scan", str(self.case), "--format", "json", cwd=self.project))["items"]
        relative = json.loads(self._cli("scan", "case", "--format", "json", cwd=self.project))["items"]
        inside = json.loads(self._cli("scan", ".", "--format", "json", cwd=self.case))["items"]
        self.assertEqual(self._audio(absolute), ["ran", "ran"])
        self.assertEqual(relative, absolute)
        self.assertEqual(inside, absolute)

    def test_classify_relative_equals_classify_absolute(self) -> None:
        absolute = json.loads(self._cli("classify", str(self.case / "a.wav"), "--format", "json", cwd=self.project))
        relative = json.loads(self._cli("classify", "case/a.wav", "--format", "json", cwd=self.project))
        inside = json.loads(self._cli("classify", "a.wav", "--format", "json", cwd=self.case))
        coverage = {entry["check"]: entry["status"] for entry in absolute["coverage"]}
        self.assertEqual(coverage["audio_features"], "ran")
        self.assertEqual((relative["path"], inside["path"]), ("case/a.wav", "a.wav"))  # the path as typed
        self.assertEqual(self._without(relative, {"path"}), self._without(absolute, {"path"}))
        self.assertEqual(self._without(inside, {"path"}), self._without(absolute, {"path"}))

    def test_batch_relative_equals_batch_absolute(self) -> None:
        out = Path(self._tmp.name, "out")
        out.mkdir(exist_ok=True)
        self._cli("batch", str(self.case), "--workers", "1", "--output", str(out / "abs.json"), cwd=self.project)
        self._cli("batch", "case", "--workers", "1", "--output", str(out / "rel.json"), cwd=self.project)
        absolute = json.loads((out / "abs.json").read_text(encoding="utf-8"))
        relative = json.loads((out / "rel.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(self._audio([row["result"] for row in absolute["results"]])), ["ran", "ran"])
        self.assertEqual(sorted(row["file_path"] for row in relative["results"]), ["case/a.wav", "case/b.wav"])
        # the path as typed (and its base64), the job's id and clock times differ; every other byte is the same
        volatile = {"path", "path_b64", "file_path", "job_id", "start_time", "end_time", "processing_time"}
        self.assertEqual(self._without(relative, volatile), self._without(absolute, volatile))

    def test_web_with_a_relative_folder_equals_an_absolute_folder(self) -> None:
        import socket
        import time
        import urllib.request

        from deepfake_lens.webapp import CLIENT_HEADER

        def scan(folder: str) -> list[dict]:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            proc = subprocess.Popen([sys.executable, "-m", "deepfake_lens", "web", "--folder", folder, "--port", str(port)],
                                    env=self.env, cwd=self.project, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{port}/api/scan", headers={CLIENT_HEADER: "gui"})
                deadline = time.monotonic() + 60
                while True:
                    try:
                        with urllib.request.urlopen(request, timeout=300) as response:
                            return json.loads(response.read())["items"]
                    except OSError:
                        if time.monotonic() > deadline or proc.poll() is not None:
                            raise
                        time.sleep(0.1)
            finally:
                proc.terminate()
                proc.communicate(timeout=60)

        absolute = scan(str(self.case))
        relative = scan("case")
        self.assertEqual(self._audio(absolute), ["ran", "ran"])
        self.assertEqual(relative, absolute)
        self.assertEqual(sorted(os.listdir(self.case)), ["a.wav", "b.wav"])  # QA-IN-1

    def test_the_worker_is_sent_an_absolute_path_and_answers_in_the_callers_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_tone(Path(tmp, "c.wav"), seconds=1)
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                reply = audio_worker.extract_features("c.wav", segment_seconds=5)
                with self.assertRaises(Exception) as missing:
                    audio_worker.extract_features("absent.wav", segment_seconds=5)
            finally:
                os.chdir(previous)
        self.assertIsNotNone(reply["features"])
        # the error text names the file as the caller gave it (as the in-process extraction did), not the absolute path
        self.assertIn("absent.wav", str(missing.exception))
        self.assertNotIn(os.path.realpath(tmp), str(missing.exception))
        self.assertNotIn(tmp, str(missing.exception))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_LIBROSA, "librosa not installed")
@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 없음")
@unittest.skipUnless(os.name == "posix", "a working folder deeper than PATH_MAX is entered one folder at a time (POSIX)")
class WorkingFolderDeeperThanPathMaxTest(unittest.TestCase):
    """R24-3 (round 24): R23-1 joined the working folder to every relative name handed to the audio worker and to
    ffmpeg; in a working folder deeper than PATH_MAX (22 folders of 200 characters: 4436 bytes) that absolute name is
    refused (ENAMETOOLONG), so `scan case` there had audio_features failed (LibsndfileError) for a.wav and av_audio
    failed (`FfmpegError: … 종료 코드 220`) for clip.mp4 — 2130892 (relative name, inherited working folder) ran both.
    The relative name is now kept ("./"-prefixed) and the worker is told the working folder."""

    LEVELS = 22

    def test_scan_and_classify_in_a_folder_deeper_than_path_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parts = [f"{index:02d}" + "d" * 198 for index in range(self.LEVELS)]
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                for part in parts:
                    os.mkdir(part)
                    os.chdir(part)
                self.assertGreater(len(os.fsencode(os.getcwd())), 4096)
                os.mkdir("case")
                _write_tone(Path("case/a.wav"), seconds=1)
                made = subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-f", "lavfi",
                     "-i", "testsrc=size=64x64:rate=10:duration=1", "-shortest", "-y", "case/clip.mp4"],
                    capture_output=True, timeout=120, check=False,
                )
                self.assertEqual(made.returncode, 0, made.stderr[-500:])
                # R25-4 (round 25): non-ASCII names there too — staged for the native decoders from a name that is
                # not too long (it was 「비디오 열기 오류: 비ASCII 이름의 파일을 열 수 없습니다」 for 한글:v.mp4)
                shutil.copyfile("case/clip.mp4", "case/한글:v.mp4")
                shutil.copyfile("case/a.wav", "case/한글 소리.wav")
            finally:
                os.chdir(previous)
            env = _clean_env(tmp)

            def cli(*args: str) -> dict:
                # a child cannot be started in such a folder by name (chdir: ENAMETOOLONG); a launcher enters it step by step
                launcher = (
                    "import os, sys\n"
                    "for part in sys.argv[1].split('/'):\n    os.chdir(part)\n"
                    "os.execv(sys.executable, [sys.executable, '-m', 'deepfake_lens', *sys.argv[2:]])\n"
                )
                done = subprocess.run([sys.executable, "-c", launcher, "/".join(parts), *args, "--format", "json"], cwd=tmp, env=env,
                                      capture_output=True, text=True, timeout=600)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                payload: dict = json.loads(done.stdout)
                return payload

            def statuses(result: dict, check: str) -> list[str]:
                return [entry["status"] for entry in result["coverage"] if entry["check"] == check]

            rows = {row["path"]: row for row in cli("scan", "case")["items"]}
            self.assertEqual(statuses(rows["a.wav"]["result"], "audio_features"), ["ran"], rows["a.wav"]["result"]["coverage"])
            self.assertEqual(statuses(rows["clip.mp4"]["result"], "av_audio"), ["ran"], rows["clip.mp4"]["result"]["coverage"])
            self.assertEqual(statuses(rows["clip.mp4"]["result"], "video_analysis"), ["ran"])
            self.assertEqual(statuses(rows["한글:v.mp4"]["result"], "video_analysis"), ["ran"], rows["한글:v.mp4"]["result"]["coverage"])
            self.assertEqual(statuses(rows["한글:v.mp4"]["result"], "av_audio"), ["ran"], rows["한글:v.mp4"]["result"]["coverage"])
            self.assertEqual(statuses(rows["한글 소리.wav"]["result"], "audio_features"), ["ran"], rows["한글 소리.wav"]["result"]["coverage"])
            classified = cli("classify", "case/a.wav")
            self.assertEqual(statuses(classified, "audio_features"), ["ran"], classified["coverage"])
