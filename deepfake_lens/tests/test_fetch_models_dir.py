"""scripts/fetch_*.py write weights where the scanner reads them (G27).

The scanner resolves profiles and checkpoints through
``deepfake_lens.cli.default_models_dir()`` ($DEEPFAKE_LENS_MODELS_DIR, else
the packaged ``deepfake_lens/models``). The fetch scripts used to default to
a repo-root ``models/`` that nothing loads from; without ``--dest`` they must
now land in that same directory. Downloads use ``file://`` URLs or a mocked
``urlopen`` — no network.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens.cli import default_models_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = b"tiny fake checkpoint payload for G27"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_g27", REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchScriptsUseModelsDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.models = base / "models-env"
        self.source = base / "source.bin"
        self.source.write_bytes(PAYLOAD)
        env = mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": str(self.models)})
        env.start()
        self.addCleanup(env.stop)

    def test_scanner_and_fetchers_agree_on_the_directory(self) -> None:
        self.assertEqual(default_models_dir(), self.models.resolve())
        for name in ("fetch_aasist", "fetch_aide", "fetch_facelandmarker", "fetch_syncnet"):
            with self.subTest(script=name):
                module = _load(name)
                self.assertFalse(hasattr(module, "DEFAULT_DEST_DIR"), "repo-root default must be gone")
                self.assertFalse(hasattr(module, "MODELS_DIR"), "repo-root default must be gone")
                self.assertIs(module.default_models_dir, default_models_dir)

    def test_default_destination_is_default_models_dir(self) -> None:
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        for name, filename in (("fetch_aasist", "aasist.pth"), ("fetch_aide", "x.pth"), ("fetch_facelandmarker", "face_landmarker.task")):
            with self.subTest(script=name):
                module = _load(name)
                argv = ["--url", self.source.as_uri(), "--sha256", digest, "--name", filename, "--force"]
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.main(argv), 0)
                self.assertEqual((self.models.resolve() / filename).read_bytes(), PAYLOAD)
                self.assertFalse((REPO_ROOT / "models" / filename).exists())

    def test_syncnet_default_destination(self) -> None:
        module = _load("fetch_syncnet")
        body = b"s" * 1_100_000

        def fake_urlopen(url: str, timeout: int) -> Any:
            return contextlib.nullcontext(io.BytesIO(body))

        with mock.patch.object(module.urllib.request, "urlopen", fake_urlopen), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main([]), 0)
        for name in module.FILES:
            self.assertEqual((self.models.resolve() / name).stat().st_size, len(body))
        other = Path(self._tmp.name) / "explicit"
        with mock.patch.object(module.urllib.request, "urlopen", fake_urlopen), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(["--dest", str(other)]), 0)
        self.assertTrue(all((other / name).is_file() for name in module.FILES))


if __name__ == "__main__":
    unittest.main()
