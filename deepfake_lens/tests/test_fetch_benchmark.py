"""scripts/fetch_benchmark.py — manifest parsing, checksum fetch, layout audit."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_fetch_benchmark():
    spec = importlib.util.spec_from_file_location("fetch_benchmark", REPO_ROOT / "scripts" / "fetch_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_file(root: Path, name: str, payload: bytes) -> Path:
    path = root / name
    path.write_bytes(payload)
    return path


class FetchBenchmarkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fetch = _load_fetch_benchmark()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.fetch.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_fetch_with_checksum_and_label_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = _source_file(root, "cam.png", b"real-bytes")
            fake = _source_file(root, "gen.png", b"fake-bytes")
            manifest = root / "bench.json"
            manifest.write_text(
                json.dumps(
                    {
                        "name": "toy-set",
                        "license": "test license",
                        "files": [
                            {"url": real.as_uri(), "dest": "0_real/cam.png", "sha256": hashlib.sha256(b"real-bytes").hexdigest()},
                            {"url": fake.as_uri(), "dest": "1_fake/gen.png", "sha256": hashlib.sha256(b"fake-bytes").hexdigest()},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            dest = root / "dataset"

            rc, out, _ = self._run(["--manifest", str(manifest), "--dest", str(dest)])

            self.assertEqual(rc, 0)
            self.assertEqual((dest / "0_real" / "cam.png").read_bytes(), b"real-bytes")
            self.assertEqual((dest / "1_fake" / "gen.png").read_bytes(), b"fake-bytes")
            # Label-folder convention is picked up by the dataset audit line.
            self.assertIn("positive=1", out)
            self.assertIn("negative=1", out)
            self.assertIn("test license", out)

    def test_sha256_mismatch_fails_and_removes_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _source_file(root, "gen.png", b"payload")
            manifest = root / "bench.json"
            manifest.write_text(
                json.dumps({"name": "bad", "files": [{"url": source.as_uri(), "dest": "1_fake/gen.png", "sha256": "0" * 64}]}),
                encoding="utf-8",
            )
            dest = root / "dataset"

            rc, _, _ = self._run(["--manifest", str(manifest), "--dest", str(dest)])

            self.assertEqual(rc, 1)
            self.assertFalse((dest / "1_fake" / "gen.png").exists())
            self.assertFalse((dest / "1_fake" / "gen.png.part").exists())

    def test_verify_only_reports_missing_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = _source_file(root, "good.png", b"good")
            dest = root / "dataset"
            (dest / "0_real").mkdir(parents=True)
            (dest / "0_real" / "good.png").write_bytes(b"good")
            (dest / "0_real" / "stale.png").write_bytes(b"stale")
            manifest = root / "bench.json"
            manifest.write_text(
                json.dumps(
                    {
                        "name": "verify",
                        "files": [
                            {"url": good.as_uri(), "dest": "0_real/good.png", "sha256": hashlib.sha256(b"good").hexdigest()},
                            {"url": good.as_uri(), "dest": "0_real/stale.png", "sha256": hashlib.sha256(b"different").hexdigest()},
                            {"url": good.as_uri(), "dest": "1_fake/absent.png", "sha256": hashlib.sha256(b"x").hexdigest()},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            rc, out, _ = self._run(["--manifest", str(manifest), "--dest", str(dest), "--verify-only"])

            self.assertEqual(rc, 1)
            self.assertIn("ok", out)
            self.assertIn("mismatch", out)
            self.assertIn("missing", out)

    def test_dest_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _source_file(root, "a.png", b"a")
            manifest = root / "bench.json"
            manifest.write_text(
                json.dumps({"name": "evil", "files": [{"url": source.as_uri(), "dest": "../escape.png", "sha256": ""}]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                self.fetch.safe_dest(root, "../escape.png")
            rc, out, _ = self._run(["--manifest", str(manifest), "--dest", str(root / "dataset")])
            # Traversal raises inside fetch_entry's safe_dest -> failed status, not a crash.
            self.assertEqual(rc, 1)
            self.assertFalse((root / "escape.png").exists())

    def test_manifest_validation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "empty.json"
            manifest.write_text(json.dumps({"name": "empty"}), encoding="utf-8")
            rc, _, err = self._run(["--manifest", str(manifest)])
            self.assertEqual(rc, 2)
            self.assertIn("files", err)

            rc, _, _ = self._run(["--manifest", str(root / "nonexistent.json")])
            self.assertEqual(rc, 2)

    def test_max_files_caps_and_keeps_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [_source_file(root, f"{i}.png", f"bytes-{i}".encode()) for i in range(3)]
            manifest = root / "bench.json"
            manifest.write_text(
                json.dumps(
                    {
                        "name": "cap",
                        "files": [
                            {"url": f.as_uri(), "dest": f"1_fake/{f.name}", "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                            for f in files
                        ],
                    }
                ),
                encoding="utf-8",
            )
            dest = root / "dataset"

            rc, out, _ = self._run(["--manifest", str(manifest), "--dest", str(dest), "--max-files", "2"])
            self.assertEqual(rc, 0)
            self.assertEqual(len(list((dest / "1_fake").iterdir())), 2)

            # Second run without --force keeps existing files.
            rc, out, _ = self._run(["--manifest", str(manifest), "--dest", str(dest), "--max-files", "2"])
            self.assertEqual(rc, 0)
            self.assertIn("kept", out)

    def test_base_url_joining(self) -> None:
        entries = self.fetch.manifest_entries(
            {"base_url": "https://example.com/data/", "files": [{"path": "a.png", "dest": "1_fake/a.png", "sha256": "ab" * 32}]}
        )
        self.assertEqual(entries[0]["url"], "https://example.com/data/a.png")

        with self.assertRaises(ValueError):
            self.fetch.manifest_entries({"files": [{"path": "a.png", "dest": "x"}]})


if __name__ == "__main__":
    unittest.main()
