"""R15-8 / R16-4: importing MediaPipe spawns nothing and writes no file outside the session folder.

In the API venv ``scan --deep-signals`` imported MediaPipe, which imports
``sounddevice`` (microphone capture), whose import runs
``ctypes.util.find_library`` → ``ldconfig``/``gcc``/``ld`` children that wrote
``cc*``/``tmp*`` files in TMPDIR. Every MediaPipe import of the package now
goes through ``mediapipe_import.import_mediapipe``, which keeps
``sounddevice`` from being imported meanwhile.

R16-4 (round 16): ``drawing_utils`` imports ``matplotlib.pyplot``, whose
first import spawned two ``fc-list`` children and wrote the font cache in
``~/.cache/matplotlib`` (or ``$TMPDIR/matplotlib-*``). The R15-8 test only
looked for children named ldconfig/gcc/cc/ld; the tests below look at the
whole process tree and every file.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

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
# R16-4: what mediapipe.tasks.python.vision.drawing_utils does at module level.
FAKE_MEDIAPIPE_VISION = textwrap.dedent(
    """
    import matplotlib.pyplot as plt
    PLT = plt
    """
)
FAKE_MATPLOTLIB = textwrap.dedent(
    """
    import os
    open(os.environ["MATPLOTLIB_MARKER"], "a").write("imported\\n")  # stands for the font cache and fc-list
    """
)
FAKE_SOUNDDEVICE = textwrap.dedent(
    """
    import os
    open(os.environ["SOUNDDEVICE_MARKER"], "a").write("imported\\n")  # stands for find_library's ldconfig/gcc/ld
    """
)
# R16-4: every way Python starts a process (Popen, posix_spawn, exec, fork, system).
SPAWN_EVENTS = ("subprocess.Popen", "os.posix_spawn", "os.exec", "os.fork", "os.forkpty", "os.system", "os.spawn")
TREE_PROBE = textwrap.dedent(
    f"""
    import glob, json, os, resource, sys
    SPAWNED = []
    LEFT = []
    sys.addaudithook(lambda event, args: SPAWNED.append(event + ":" + str(args[:1])) if event in {SPAWN_EVENTS!r} else None)

    def report():
        live = []
        for path in glob.glob("/proc/self/task/*/children"):
            try:
                live += open(path).read().split()
            except OSError:
                pass
        print(json.dumps({{"spawned": SPAWNED, "children_maxrss": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss, "live_children": live, "left": LEFT}}))
    """
)
STRACE = shutil.which("strace")


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

    def test_matplotlib_is_an_inert_stub_during_the_import_and_real_afterwards(self) -> None:
        """R16-4: drawing_utils' module-level ``import matplotlib.pyplot`` gets a stub; nothing of matplotlib runs."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fake = root / "fake"
            (fake / "mediapipe").mkdir(parents=True)
            (fake / "mediapipe" / "__init__.py").write_text("", encoding="utf-8")
            (fake / "mediapipe" / "vision.py").write_text(FAKE_MEDIAPIPE_VISION, encoding="utf-8")
            (fake / "matplotlib").mkdir()
            (fake / "matplotlib" / "__init__.py").write_text(FAKE_MATPLOTLIB, encoding="utf-8")
            (fake / "matplotlib" / "pyplot.py").write_text("", encoding="utf-8")
            marker = root / "marker"
            script = textwrap.dedent(
                """
                import json, os, sys
                from deepfake_lens.mediapipe_import import import_mediapipe
                vision = import_mediapipe("mediapipe.vision")
                marker_after_import = os.path.exists(os.environ["MATPLOTLIB_MARKER"])
                left = sorted(name for name in sys.modules if name.startswith("matplotlib"))
                try:
                    vision.PLT.figure
                    used = "no error"
                except AttributeError as exc:
                    used = str(exc)
                import matplotlib.pyplot  # another user of it in the process: the real package
                print(json.dumps([marker_after_import, left, used, os.path.exists(os.environ["MATPLOTLIB_MARKER"])], ensure_ascii=False))
                """
            )
            env = {**_env(root, str(fake)), "MATPLOTLIB_MARKER": str(marker)}
            done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        marker_after_import, left, used, marker_at_end = json.loads(done.stdout)
        self.assertFalse(marker_after_import)
        self.assertEqual(left, [])
        self.assertIn("R16-4", used)
        self.assertTrue(marker_at_end)

    def test_an_already_loaded_matplotlib_is_used_as_is(self) -> None:
        import types
        from unittest import mock

        from deepfake_lens.mediapipe_import import import_mediapipe

        real = types.ModuleType("matplotlib")
        fake = types.ModuleType("mediapipe")
        with mock.patch.dict(sys.modules, {"matplotlib": real, "mediapipe": fake}):
            self.assertIs(import_mediapipe(), fake)
            self.assertIs(sys.modules["matplotlib"], real)

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
    """R16-4 (round 16): the whole process tree, whatever the children are called.

    R15-8 counted only children named ldconfig/gcc/cc/ld, so the two
    ``fc-list`` children of matplotlib's font cache (and the cache file in
    ``~/.cache/matplotlib``) went unseen. Now: no child process at all —
    the audit hook sees every Python-level spawn and fork,
    ``RUSAGE_CHILDREN`` is non-zero once any descendant (named anything, at
    any depth) was reaped, ``/proc/self/task/*/children`` lists live ones,
    and under ``strace -f`` (when ptrace is allowed) every ``execve`` of a
    descendant is counted — and no file anywhere under the child's HOME,
    XDG folders and TMPDIR.
    """

    def _run(self, root: Path, body: str, *, strace: bool = False) -> dict[str, Any]:
        script = TREE_PROBE + textwrap.dedent(body) + "\nreport()\n"
        env = _env(root)
        for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "MPLCONFIGDIR"):
            env.pop(name, None)
        argv = [sys.executable, "-c", script]
        trace = root.parent / f"{root.name}.strace"
        if strace:
            argv = [str(STRACE), "-f", "-qq", "-e", "trace=execve", "-o", str(trace), *argv]
        done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(root))
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        report = json.loads(done.stdout.strip().splitlines()[-1])
        report["files"] = sorted(
            str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and path.name != "case" and "case" not in path.relative_to(root).parts
        )
        if strace:
            lines = trace.read_text(encoding="utf-8", errors="replace").splitlines()
            pids = {line.split(None, 1)[0] for line in lines if "execve(" in line}
            report["execve_pids"] = len(pids)
        return report

    def _control(self, tmp: Path) -> dict[str, Any]:
        control_root = tmp / "control"
        control_root.mkdir()
        return self._run(control_root, "import mediapipe\nimport mediapipe.tasks.python.vision\n")

    def test_importing_mediapipe_starts_no_process_and_writes_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            control = self._control(Path(tmp).resolve())
            if not (control["spawned"] or control["children_maxrss"] or control["files"]):
                self.skipTest("대조군: 이 환경의 mediapipe 가져오기는 자식 프로세스도 파일도 만들지 않음")
            root = Path(tmp).resolve() / "import"
            root.mkdir()
            report = self._run(
                root,
                """
                from deepfake_lens.mediapipe_import import import_mediapipe
                import_mediapipe()
                import_mediapipe("mediapipe.tasks.python.vision")
                import sys
                LEFT = sorted(name for name in sys.modules if name == "matplotlib" or name.startswith("matplotlib.") or name == "sounddevice")
                """,
            )
        self.assertEqual(report["spawned"], [])
        self.assertEqual(report["children_maxrss"], 0)  # no descendant at all was reaped
        self.assertEqual(report["live_children"], [])
        self.assertEqual(report["files"], [])  # no ~/.cache/matplotlib, ~/.config/matplotlib, $TMPDIR/matplotlib-*, cc*/tmp*
        self.assertEqual(report["left"], [])  # the stubs and the block are gone from sys.modules

    @unittest.skipUnless(STRACE is not None, "strace not installed")
    def test_no_descendant_is_executed_under_strace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "strace"
            root.mkdir()
            probe = subprocess.run([str(STRACE), "-f", "-qq", "-e", "trace=execve", "-o", os.devnull, sys.executable, "-c", "pass"], capture_output=True, timeout=CHILD_TIMEOUT_SECONDS)
            if probe.returncode != 0:
                self.skipTest("strace를 쓸 수 없음(ptrace 거부)")
            report = self._run(
                root,
                """
                from deepfake_lens.mediapipe_import import import_mediapipe
                import_mediapipe()
                import_mediapipe("mediapipe.tasks.python.vision")
                """,
                strace=True,
            )
        self.assertEqual(report["execve_pids"], 1)  # only the interpreter itself

    def test_doctor_and_a_deep_scan_spawn_nothing_either(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            control = self._control(Path(tmp).resolve())
            if not (control["spawned"] or control["children_maxrss"] or control["files"]):
                self.skipTest("대조군: 이 환경의 mediapipe 가져오기는 자식 프로세스도 파일도 만들지 않음")
            root = Path(tmp).resolve() / "case"
            root.mkdir()
            from deepfake_lens.native_path import imwrite_any

            import numpy as np

            case = root / "case"
            case.mkdir()
            rng = np.random.default_rng(5)
            self.assertTrue(imwrite_any(case / "photo.png", rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)))
            report = self._run(
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
        # R16-4: every spawn, not only ldconfig/gcc/cc/ld (R15-8 filtered by name).
        self.assertEqual(report["spawned"], [])
        self.assertEqual(report["children_maxrss"], 0)
        self.assertEqual([name for name in report["files"] if not name.startswith("logs/")], [])  # session folder removed at exit; nothing else


if __name__ == "__main__":
    unittest.main()
