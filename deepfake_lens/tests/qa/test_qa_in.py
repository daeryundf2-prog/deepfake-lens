"""QA-IN-2 / QA-IN-4 — deterministic rescans and a content-keyed scan cache (WP-G: G11, G32).

Runs without network weights (``--no-default-engine``). QA-IN-2 crosses a
real process boundary (``python -m deepfake_lens scan`` in a subprocess) and
also simulates one in-process by dropping every cached ``deepfake_lens``
module and re-importing the package before the third scan.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import deepfake_lens
from deepfake_lens import core
from deepfake_lens.core import scan_directory
from deepfake_lens.scan_cache import _iter_files

REPO_ROOT = Path(deepfake_lens.__file__).resolve().parent.parent
# Keys whose values are wall-clock timestamps (stripped before comparing).
TIMESTAMP_KEYS = {"measured_at", "generated_at", "created_at", "timestamp", "scanned_at", "fitted_at"}


def _build_case_folder(root: Path) -> None:
    """Files created deliberately out of name order, nested, archived, duplicated."""
    root.mkdir(parents=True)
    for name, text in (
        ("z-last.txt", "마지막 파일입니다. 사건 기록 메모."),
        ("a-first.txt", "첫 번째 파일입니다. 회의록 초안."),
        ("B-upper.txt", "대문자로 시작하는 파일 이름."),
        ("copy-of-a.txt", "첫 번째 파일입니다. 회의록 초안."),
    ):
        (root / name).write_text(text, encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "y.txt").write_text("하위 폴더 y", encoding="utf-8")
    (sub / "c.txt").write_text("하위 폴더 c", encoding="utf-8")
    deeper = root / "a-dir"
    deeper.mkdir()
    (deeper / "inner.txt").write_text("깊은 폴더", encoding="utf-8")
    with zipfile.ZipFile(root / "m-bundle.zip", "w") as archive:
        archive.writestr("second.txt", "압축 안 두 번째")
        archive.writestr("first.txt", "압축 안 첫 번째")
    from PIL import Image

    image = Image.new("RGB", (64, 64))
    image.putdata([((x * 3) % 256, (y * 9) % 256, 77) for y in range(64) for x in range(64)])
    image.save(root / "photo.png")


def _normalize(payload: Any, roots: list[Path]) -> Any:
    """Drop timestamp fields and absolute paths under the scanned folders."""
    prefixes = [str(root) for root in roots] + [str(root.resolve()) for root in roots]
    if isinstance(payload, dict):
        return {key: _normalize(value, roots) for key, value in payload.items() if key not in TIMESTAMP_KEYS}
    if isinstance(payload, list):
        return [_normalize(value, roots) for value in payload]
    if isinstance(payload, str) and any(payload.startswith(prefix) for prefix in prefixes):
        return "<ABSOLUTE_PATH>"
    return payload


def _scan_args(folder: Path, out: Path) -> list[str]:
    return ["scan", str(folder), "--recursive", "--dedupe", "--no-default-engine", "--json-out", str(out), "--format", "json"]


def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "DEEPFAKE_LENS_REPORT_KEY"}
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


@contextlib.contextmanager
def _fresh_package_import() -> Any:
    """Simulate a process restart: forget every deepfake_lens module, re-import.

    The original module objects are restored afterwards so the rest of the
    test run (and its patch targets) is unaffected.
    """
    def ours(name: str) -> bool:
        return name == "deepfake_lens" or name.startswith("deepfake_lens.")

    saved = {name: module for name, module in sys.modules.items() if ours(name)}
    for name in saved:
        del sys.modules[name]
    try:
        yield importlib.import_module("deepfake_lens.cli")
    finally:
        for name in [name for name in sys.modules if ours(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


class QaIn2DeterministicRescanTest(unittest.TestCase):
    """QA-IN-2: 같은 폴더를 3회 검사(중간에 프로세스 재시작, 폴더 이름 변경) → 타임스탬프·절대경로 필드를 제외한 JSON이 바이트 단위로 동일. 파일 순서 동일."""

    def test_three_scans_across_restart_and_rename_are_byte_identical(self) -> None:
        """QA-IN-2: in-process, subprocess, and re-imported-after-rename scans give identical JSON."""
        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / "case-original"
            _build_case_folder(folder)
            outs = [base / f"scan{index}.json" for index in range(3)]

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(_scan_args(folder, outs[0])), 0)

            completed = subprocess.run(
                [sys.executable, "-m", "deepfake_lens", *_scan_args(folder, outs[1])],
                cwd=str(base), env=_clean_env(), capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])

            renamed = base / "case-renamed"
            folder.rename(renamed)
            with _fresh_package_import() as fresh_cli, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fresh_cli.main(_scan_args(renamed, outs[2])), 0)

            payloads = [json.loads(out.read_text(encoding="utf-8")) for out in outs]
            texts = [
                json.dumps(_normalize(payload, [folder, renamed]), ensure_ascii=False, indent=2).encode("utf-8")
                for payload in payloads
            ]
            self.assertEqual(texts[0], texts[1], "process restart changed the scan JSON")
            self.assertEqual(texts[0], texts[2], "folder rename / re-import changed the scan JSON")
            orders = [[item["path"] for item in payload["items"]] for payload in payloads]
            self.assertEqual(orders[0], orders[1])
            self.assertEqual(orders[0], orders[2])
            self.assertEqual(payloads[0]["summary"]["total"], len(orders[0]))
            # Sorted walk: "a-first.txt" precedes "copy-of-a.txt", so the copy
            # is always the duplicate, whatever order the OS lists them in.
            duplicate = next(item for item in payloads[0]["items"] if item["status"] == "duplicate")
            self.assertEqual(duplicate["path"], "copy-of-a.txt")
            self.assertEqual(duplicate["duplicate_of"], "a-first.txt")
            hashed = [item for item in payloads[0]["items"] if item.get("sha256")]
            self.assertTrue(hashed)

    def test_walk_order_is_sorted_and_independent_of_os_listing_order(self) -> None:
        """QA-IN-2: file order is the sorted path order even when the OS lists entries reversed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            _build_case_folder(root)
            expected = [
                "B-upper.txt", "a-first.txt", "copy-of-a.txt", "m-bundle.zip", "photo.png", "z-last.txt",
                "a-dir/inner.txt", "sub/c.txt", "sub/y.txt",
            ]
            plain = [path.relative_to(root).as_posix() for path in _iter_files(root, recursive=True)]
            self.assertEqual(plain, expected)

            real_walk = os.walk

            def reversed_walk(top: Any, *args: Any, **kwargs: Any) -> Any:
                for dirpath, dirs, files in real_walk(top, *args, **kwargs):
                    dirs.reverse()
                    files.reverse()
                    yield dirpath, dirs, files

            with patch("os.walk", reversed_walk):
                shuffled = [path.relative_to(root).as_posix() for path in _iter_files(root, recursive=True)]
            self.assertEqual(shuffled, expected)

            real_iterdir = Path.iterdir
            with patch.object(Path, "iterdir", lambda self: reversed(list(real_iterdir(self)))):
                flat = [path.name for path in _iter_files(root, recursive=False)]
            self.assertEqual(flat, expected[:6])

    def test_max_files_cap_keeps_the_same_files(self) -> None:
        """QA-IN-2: a capped scan keeps the first N files in sorted order on every run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            _build_case_folder(root)
            first = [item.path for item in scan_directory(root, max_files=3)[1]]
            second = [item.path for item in scan_directory(root, max_files=3)[1]]
        self.assertEqual(first, second)
        self.assertEqual(sorted(path.split("::")[0] for path in first if "::" not in path), ["B-upper.txt", "a-first.txt", "copy-of-a.txt"])


class QaIn4ContentKeyedCacheTest(unittest.TestCase):
    """QA-IN-4: 마지막 바이트만 바꾼 동일 크기 파일을 같은 경로에 넣고 touch -r로 mtime 복원 후 재검사 → 캐시 미사용, 새로 분석, 해시가 다르게 기록."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "case"
        self.root.mkdir()
        self.cache = base / "cache" / "scan-cache.json"
        self.evidence = self.root / "evidence.txt"
        self.evidence.write_bytes("진술서 원문: 피의자는 범행을 부인하였다.A".encode("utf-8"))
        (self.root / "other.txt").write_text("관련 없는 메모", encoding="utf-8")

    def _scan(self, root: Path | None = None) -> tuple[Any, dict[str, Any], int]:
        with patch("deepfake_lens.core.analyze_file", wraps=core.analyze_file) as analyze:
            summary, items = scan_directory(root or self.root, cache_path=self.cache)
        return summary, {item.name: item for item in items}, analyze.call_count

    def test_same_size_edit_with_restored_mtime_is_reanalyzed(self) -> None:
        """QA-IN-4: last byte changed, size equal, mtime restored → cache miss, new analysis, new hash."""
        summary, items, calls = self._scan()
        self.assertEqual((summary.cached, calls), (0, 2))
        old_hash = items["evidence.txt"].sha256
        self.assertEqual(old_hash, hashlib.sha256(self.evidence.read_bytes()).hexdigest())

        summary, _, calls = self._scan()
        self.assertEqual((summary.cached, calls), (2, 0), "unchanged files must be served from cache")

        before = self.evidence.stat()
        data = self.evidence.read_bytes()
        self.evidence.write_bytes(data[:-1] + b"B")
        os.utime(self.evidence, ns=(before.st_atime_ns, before.st_mtime_ns))  # touch -r
        after = self.evidence.stat()
        self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))

        summary, items, calls = self._scan()
        self.assertEqual(summary.cached, 1, "only the untouched file may come from cache")
        self.assertEqual(calls, 1, "the edited file must be analyzed again")
        new_hash = items["evidence.txt"].sha256
        self.assertNotEqual(new_hash, old_hash)
        self.assertEqual(new_hash, hashlib.sha256(self.evidence.read_bytes()).hexdigest())
        keys = list(json.loads(self.cache.read_text(encoding="utf-8"))["items"])
        self.assertTrue(any(f"sha256:{new_hash}" in key for key in keys))
        self.assertTrue(any(f"sha256:{old_hash}" in key for key in keys))

    def test_cache_key_has_no_path_size_or_mtime(self) -> None:
        """QA-IN-4: keys carry content hash, options, tool version and pins — not path/size/mtime."""
        self._scan()
        keys = list(json.loads(self.cache.read_text(encoding="utf-8"))["items"])
        stat = self.evidence.stat()
        for key in keys:
            self.assertNotIn("evidence", key)
            self.assertNotIn(self.root.name, key)
            self.assertNotIn(str(stat.st_mtime_ns), key)
            self.assertIn("sha256:", key)
            self.assertIn(f"tool:{core.TOOL_VERSION}", key)
            self.assertIn("pins:", key)

    def test_renamed_folder_hits_cache_with_current_paths(self) -> None:
        """QA-IN-4: content keys survive a folder rename; rows report the current paths."""
        self._scan()
        renamed = self.root.with_name("case-renamed")
        self.root.rename(renamed)
        summary, items, calls = self._scan(renamed)
        self.assertEqual((summary.cached, calls), (2, 0))
        self.assertEqual(sorted(items), ["evidence.txt", "other.txt"])
        self.assertEqual(items["evidence.txt"].path, "evidence.txt")

    def test_identical_content_rows_keep_their_own_paths(self) -> None:
        """QA-IN-4: two files with the same bytes share a cache entry but not a path."""
        (self.root / "twin.txt").write_bytes(self.evidence.read_bytes())
        self._scan()
        summary, items, _ = self._scan()
        self.assertEqual(summary.cached, 3)
        self.assertEqual(items["twin.txt"].path, "twin.txt")
        self.assertEqual(items["evidence.txt"].path, "evidence.txt")

    def test_profile_pin_change_invalidates_cache(self) -> None:
        """QA-IN-4: pinning a model profile changes every key — no replay under other weights."""
        models = Path(self._tmp.name) / "models"
        models.mkdir()
        profile = models / "fake-runtime.json"
        profile.write_text(json.dumps({"name": "fake", "supported": False}), encoding="utf-8")
        with patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": str(models)}):
            self.assertEqual(self._scan()[0].cached, 0)
            self.assertEqual(self._scan()[0].cached, 2)
            profile.write_text(json.dumps({"name": "fake", "supported": False, "pin": {"sha256": "ab" * 32}}), encoding="utf-8")
            summary, _, calls = self._scan()
        self.assertEqual((summary.cached, calls), (0, 2))

    def test_dedupe_hash_is_reused_not_recomputed(self) -> None:
        """QA-IN-4: with dedupe on, each file is hashed once per scan (shared memo)."""
        from deepfake_lens import scan_cache

        with patch.object(scan_cache, "_file_fingerprint", wraps=scan_cache._file_fingerprint) as fingerprint:
            scan_directory(self.root, cache_path=self.cache, dedupe=True)
        hashed = [call.args[0].name for call in fingerprint.call_args_list]
        self.assertEqual(sorted(hashed), ["evidence.txt", "other.txt"])


if __name__ == "__main__":
    unittest.main()
