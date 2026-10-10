"""R15-2 (round 15): no third-party telemetry — the opt-outs are set before any dependency is imported.

The verifier saw ``import onnxruntime`` (reached from ``doctor`` and
``scan --deep-signals``) create ``~/.cache/Microsoft/DeveloperTools/
.onnxruntime/deviceid`` and try ``mobile.events.data.microsoft.com:443``.
``deepfake_lens/__init__.py`` now sets ``ORT_DISABLE_TELEMETRY`` and the
other known opt-outs first (``telemetry_opt_out``). The onnxruntime test
runs a control without the package (the device id *is* written there — the
check is not vacuous) and then ``doctor`` + ``import onnxruntime`` after
``import deepfake_lens``: no device id, no database, no outbound connect
(Python-level audit hook; ``strace`` where it is installed — any IP
connect counts, loopback too, since the way out may be a local proxy).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from deepfake_lens.telemetry_opt_out import TELEMETRY_OPT_OUT

REPO = Path(__file__).resolve().parents[2]
HAVE_ONNXRUNTIME = importlib.util.find_spec("onnxruntime") is not None
CHILD_TIMEOUT_SECONDS = 300
# How long a child keeps running after importing onnxruntime: its telemetry
# provider writes the device id / database asynchronously (~1 s here).
TELEMETRY_WINDOW_SECONDS = 4
ORT_STATE = Path("Microsoft") / "DeveloperTools" / ".onnxruntime"

AUDIT_PRELUDE = textwrap.dedent(
    """
    import json, os, socket, sys
    CONNECTS = []
    def hook(event, args):
        # Any IP connect counts — loopback too: outbound traffic may go
        # through a local proxy (HTTPS_PROXY=http://127.0.0.1:…).
        if event == "socket.connect" and isinstance(args[1], tuple):
            CONNECTS.append(repr(args[1]))
    sys.addaudithook(hook)
    """
)


def _env(root: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name not in TELEMETRY_OPT_OUT}
    env.update(
        {
            "HOME": str(root / "home"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "DEEPFAKE_LENS_LOG_DIR": str(root / "logs"),
            "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }
    )
    return env


class TelemetryOptOutTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def test_importing_the_package_sets_every_opt_out_first(self) -> None:
        script = (
            "import json, os\n"
            f"names = {sorted(TELEMETRY_OPT_OUT)!r}\n"
            "before = {name: os.environ.get(name) for name in names}\n"
            "import deepfake_lens\n"
            "after = {name: os.environ.get(name) for name in names}\n"
            "print(json.dumps({'before': before, 'after': after}))\n"
        )
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, env=_env(self.root), timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        seen = json.loads(done.stdout)
        self.assertEqual(seen["before"], {name: None for name in TELEMETRY_OPT_OUT})
        self.assertEqual(seen["after"], TELEMETRY_OPT_OUT)
        self.assertEqual(TELEMETRY_OPT_OUT["ORT_DISABLE_TELEMETRY"], "1")

    def test_the_opt_outs_are_set_before_any_other_package_module_is_imported(self) -> None:
        source = (REPO / "deepfake_lens" / "__init__.py").read_text(encoding="utf-8")
        first_import = next(line for line in source.splitlines() if line.startswith(("from ", "import ")))
        self.assertEqual(first_import, "from . import telemetry_opt_out as _telemetry_opt_out  # R15-2: before every other import")
        self.assertLess(source.index("_telemetry_opt_out.apply()"), source.index("from .core import"))
        leaf = (REPO / "deepfake_lens" / "telemetry_opt_out.py").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"^(?:from|import) (\S+)", leaf, re.M), ["__future__", "os"])  # imports nothing else

    def test_an_operator_value_is_kept(self) -> None:
        env = {**_env(self.root), "DO_NOT_TRACK": "yes"}
        done = subprocess.run(
            [sys.executable, "-c", "import os, deepfake_lens; print(os.environ['DO_NOT_TRACK'], os.environ['ORT_DISABLE_TELEMETRY'])"],
            capture_output=True, text=True, env=env, timeout=CHILD_TIMEOUT_SECONDS,
        )
        self.assertEqual(done.stdout.split(), ["yes", "1"], done.stderr[-800:])

    @unittest.skipUnless(HAVE_ONNXRUNTIME, "onnxruntime not installed")
    def test_doctor_and_onnxruntime_write_no_device_id_and_connect_nowhere(self) -> None:
        control = self.root / "control"
        control.mkdir()
        control_run = subprocess.run(
            [sys.executable, "-c", f"import onnxruntime, time; time.sleep({TELEMETRY_WINDOW_SECONDS})"],
            capture_output=True, env=_env(control), timeout=CHILD_TIMEOUT_SECONDS,
        )
        self.assertEqual(control_run.returncode, 0, control_run.stderr[-800:])
        if not (control / "cache" / ORT_STATE).exists():
            self.skipTest("대조군: 이 onnxruntime은 텔레메트리 파일을 만들지 않음")
        self.assertTrue((control / "cache" / ORT_STATE / "deviceid").exists())  # the control writes it

        case = self.root / "case"
        case.mkdir()
        script = AUDIT_PRELUDE + textwrap.dedent(
            f"""
            import contextlib, io, time
            import deepfake_lens
            from deepfake_lens.cli import main
            with contextlib.redirect_stdout(io.StringIO()):
                main(["doctor"])
            import onnxruntime
            time.sleep({TELEMETRY_WINDOW_SECONDS})
            print(json.dumps(CONNECTS))
            """
        )
        command = [sys.executable, "-c", script]
        trace = case / "connect.strace"
        strace = shutil.which("strace")
        if strace is not None:
            command = [strace, "-f", "-qq", "-e", "trace=connect", "-o", str(trace), *command]
        done = subprocess.run(command, capture_output=True, text=True, env=_env(case), timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), [])  # no Python-level connect
        self.assertFalse((case / "cache" / ORT_STATE).exists(), sorted(str(p) for p in (case / "cache").rglob("*")))
        self.assertFalse((case / "home" / ".cache" / ORT_STATE).exists())
        if strace is not None and trace.exists():
            # Any IP connect counts (a local proxy is still the way out).
            outbound = [line for line in trace.read_text(encoding="utf-8", errors="replace").splitlines() if re.search(r"AF_INET6?", line)]
            self.assertEqual(outbound, [])  # no native connect either (onnxruntime's C++ client)


if __name__ == "__main__":
    unittest.main()
