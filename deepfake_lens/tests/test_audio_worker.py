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
import struct
import subprocess
import sys
import tempfile
import textwrap
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
        self.assertEqual(shutdown.running_children(), [])

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
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(audio_worker, "shared_cache_root", return_value=Path(tmp, "shared")):
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

    def test_no_shared_cache_under_a_relative_home(self) -> None:
        with mock.patch.object(Path, "home", return_value=Path("relative-home")):
            self.assertIsNone(audio_worker.shared_cache_root())


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


if __name__ == "__main__":
    unittest.main()
