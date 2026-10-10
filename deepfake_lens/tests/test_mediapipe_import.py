"""R15-8 (round 15): importing MediaPipe spawns nothing and writes nothing to TMPDIR.

In the API venv ``scan --deep-signals`` imported MediaPipe, which imports
``sounddevice`` (microphone capture), whose import runs
``ctypes.util.find_library`` → ``ldconfig``/``gcc``/``ld`` children that wrote
``cc*``/``tmp*`` files in TMPDIR. Every MediaPipe import of the package now
goes through ``mediapipe_import.import_mediapipe``, which keeps
``sounddevice`` from being imported meanwhile.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
HAVE_MEDIAPIPE = importlib.util.find_spec("mediapipe") is not None
CHILD_TIMEOUT_SECONDS = 600

FAKE_MEDIAPIPE = textwrap.dedent(
    """
    try:
        import sounddevice  # what mediapipe.tasks.python.audio.core.audio_record does
        SOUNDDEVICE = True
    except ImportError:
        SOUNDDEVICE = False
    """
)
FAKE_SOUNDDEVICE = textwrap.dedent(
    """
    import os
    open(os.environ["SOUNDDEVICE_MARKER"], "a").write("imported\\n")  # stands for find_library's ldconfig/gcc/ld
    """
)
SPAWN_AUDIT = textwrap.dedent(
    """
    import sys
    SPAWNED = []
    sys.addaudithook(lambda event, args: SPAWNED.append(str(args[0])) if event in ("subprocess.Popen", "os.posix_spawn", "os.exec") else None)
    """
)


def _env(root: Path, extra_path: str = "") -> dict[str, str]:
    env = dict(os.environ)
    tmp = root / "tmp"
    tmp.mkdir(exist_ok=True)
    env.update(
        {
            "HOME": str(root / "home"),
            "TMPDIR": str(tmp),
            "DEEPFAKE_LENS_LOG_DIR": str(root / "logs"),
            "PYTHONPATH": os.pathsep.join(filter(None, (extra_path, str(REPO), os.environ.get("PYTHONPATH")))),
        }
    )
    env.pop("DEEPFAKE_LENS_REPORT_KEY", None)
    return env


class ImportHelperTest(unittest.TestCase):
    def test_sounddevice_is_not_imported_with_mediapipe_and_is_usable_afterwards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fake = root / "fake"
            (fake / "mediapipe").mkdir(parents=True)
            (fake / "mediapipe" / "__init__.py").write_text(FAKE_MEDIAPIPE, encoding="utf-8")
            (fake / "sounddevice.py").write_text(FAKE_SOUNDDEVICE, encoding="utf-8")
            marker = root / "marker"
            script = textwrap.dedent(
                """
                import json, os, sys
                from deepfake_lens.mediapipe_import import import_mediapipe
                mp = import_mediapipe()
                during = mp.SOUNDDEVICE
                blocked_left = "sounddevice" in sys.modules
                marker_after_import = os.path.exists(os.environ["SOUNDDEVICE_MARKER"])
                import sounddevice  # another user of it in the process: unaffected
                print(json.dumps([during, blocked_left, marker_after_import, os.path.exists(os.environ["SOUNDDEVICE_MARKER"])]))
                """
            )
            env = {**_env(root, str(fake)), "SOUNDDEVICE_MARKER": str(marker)}
            done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertEqual(json.loads(done.stdout), [False, False, False, True])

    def test_a_present_sounddevice_is_left_alone(self) -> None:
        import types
        from unittest import mock

        from deepfake_lens.mediapipe_import import import_mediapipe

        already = types.ModuleType("sounddevice")
        fake = types.ModuleType("mediapipe")
        with mock.patch.dict(sys.modules, {"sounddevice": already, "mediapipe": fake}):
            self.assertIs(import_mediapipe(), fake)
            self.assertIs(sys.modules["sounddevice"], already)
        with self.assertRaises(ValueError):
            import_mediapipe("os")

    def test_every_mediapipe_import_goes_through_the_helper(self) -> None:
        offenders: list[str] = []
        for source in sorted(PACKAGE.rglob("*.py")):
            relative = source.relative_to(PACKAGE)
            if "tests" in relative.parts or relative.name == "mediapipe_import.py":
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    names = [node.args[0].value]
                if any(name == "mediapipe" or name.startswith("mediapipe.") for name in names):
                    offenders.append(f"{relative.as_posix()}:{getattr(node, 'lineno', 0)}")
        self.assertEqual(offenders, [])
        users = sorted(p.name for p in PACKAGE.glob("*.py") if "import_mediapipe(" in p.read_text(encoding="utf-8") and p.name != "mediapipe_import.py")
        self.assertEqual(users, ["doctor.py", "face.py"])  # non-vacuous


@unittest.skipUnless(HAVE_MEDIAPIPE, "mediapipe not installed")
class RealMediapipeSpawnsNothingTest(unittest.TestCase):
    def _run(self, root: Path, body: str) -> tuple[list[str], list[str], str]:
        script = SPAWN_AUDIT + textwrap.dedent(body) + "\nimport json\nprint(json.dumps(SPAWNED))\n"
        env = _env(root)
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(root))
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        spawned = json.loads(done.stdout.strip().splitlines()[-1])
        return spawned, sorted(os.listdir(env["TMPDIR"])), done.stdout

    def test_import_doctor_and_a_deep_scan_spawn_no_linker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            control_root = Path(tmp).resolve() / "control"
            control_root.mkdir()
            control, _, _ = self._run(control_root, "import mediapipe\n")
            if not control:
                self.skipTest("대조군: 이 환경의 mediapipe 가져오기는 자식 프로세스를 띄우지 않음")
            root = Path(tmp).resolve() / "case"
            root.mkdir()
            from deepfake_lens.native_path import imwrite_any

            import numpy as np

            case = root / "case"
            case.mkdir()
            rng = np.random.default_rng(5)
            self.assertTrue(imwrite_any(case / "photo.png", rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)))
            spawned, left, _ = self._run(
                root,
                f"""
                import contextlib, io
                from deepfake_lens.mediapipe_import import import_mediapipe
                import_mediapipe()
                import_mediapipe("mediapipe.tasks.python.vision")
                from deepfake_lens.cli import main
                with contextlib.redirect_stdout(io.StringIO()):
                    main(["doctor"])
                    main(["scan", {str(case)!r}, "--deep-signals", "--include-low", "--format", "json"])
                """,
            )
        linkers = [exe for exe in spawned if os.path.basename(exe) in ("ldconfig", "gcc", "cc", "ld", "x86_64-linux-gnu-gcc")]
        self.assertEqual(linkers, [], spawned)
        self.assertEqual(left, [])  # no cc*/tmp* in TMPDIR (and the session folder removed at exit)


if __name__ == "__main__":
    unittest.main()
