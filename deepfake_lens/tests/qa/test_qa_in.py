"""QA-IN-1 / QA-IN-2 / QA-IN-4 — read-only evidence, deterministic rescans and a
content-keyed scan cache (WP-G: G11, G31, G32; WP-J).

QA-IN-1 scans one sample of every supported format (``samples.py``) in a
folder made read-only (0o555 / files 0o444) with every analysis layer on and
every report written elsewhere, then checks that no byte, mtime, mode or
directory entry in the evidence folder changed. Tests run as root in some
CI containers, where 0o555 does not stop a write — so the assertion is on
the folder's contents, not on a PermissionError.

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

from .samples import UNMADE_REASONS, supported_extensions, write_samples

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
    # N1: unreadable files whose reader errors used to carry the full path
    # (Pillow, soundfile) — a rename must not change their coverage reasons.
    (root / "empty.jpg").write_bytes(b"")
    (root / "fake.gif").write_bytes(b"not a gif, only text\n" * 4)
    (root / "fake.mp3").write_bytes(bytes((index * 37 + 11) % 256 for index in range(3000)))


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


def _snapshot(folder: Path) -> dict[str, tuple[str, int, int, int]]:
    """relpath -> (sha256, size, mtime_ns, mode) for every entry, dirs included."""
    entries: dict[str, tuple[str, int, int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            stat = path.lstat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else "dir"
            entries[path.relative_to(folder).as_posix()] = (digest, stat.st_size if digest != "dir" else 0, stat.st_mtime_ns, stat.st_mode)
    root = folder.stat()
    entries["."] = ("dir", 0, root.st_mtime_ns, root.st_mode)
    return entries


def _make_read_only(folder: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in filenames:
            os.chmod(Path(dirpath) / name, 0o444)
        for name in dirnames:
            os.chmod(Path(dirpath) / name, 0o555)
    os.chmod(folder, 0o555)


def _make_writable(folder: Path) -> None:
    os.chmod(folder, 0o755)
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in dirnames:
            os.chmod(Path(dirpath) / name, 0o755)
        for name in filenames:
            os.chmod(Path(dirpath) / name, 0o644)


class QaIn1ReadOnlyEvidenceTest(unittest.TestCase):
    """QA-IN-1: read-only evidence folder, every supported format, every layer on."""

    def test_full_scan_leaves_read_only_folder_untouched(self) -> None:
        """QA-IN-1: 지원 형식 전부의 샘플 1개씩을 읽기 전용 폴더에 두고 전체 검사 → 모든 파일의 검사 전후 SHA-256 동일, mtime 불변, 폴더에 새 파일 0개.

        "전체 검사" = CLI scan with --recursive --dedupe --pixel deep
        --deep-signals --heatmaps, a cache, a hash DB and JSON/CSV/HTML/PDF/
        evidence-statement reports — all outputs outside the evidence folder.
        Formats no encoder here can write are named with a reason from the
        closed set in samples.UNMADE_REASONS, never silently dropped. Binary
        formats without a writer (.doc .xls .ppt .hwp .rar, and .7z without
        py7zr) are scanned as magic-header-only samples (samples.MAGIC_ONLY)
        and must come back 미지원 or 판단 불가 with a reason (D6).
        """
        magic_only: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            evidence = base / "evidence"
            made, unmade = write_samples(evidence, magic_only)
            self.assertEqual(set(made) | set(unmade), supported_extensions())
            self.assertTrue(set(unmade.values()) <= UNMADE_REASONS, unmade)
            # Always synthesizable (stdlib only): the floor of this test —
            # including the magic-only binary formats (all 43 with Pillow +
            # ffmpeg installed).
            self.assertTrue({".png", ".txt", ".md", ".wav", ".docx", ".pdf", ".zip", ".tar"} <= set(made))
            self.assertTrue({".doc", ".xls", ".ppt", ".hwp", ".rar", ".7z"} <= set(made))
            out = base / "out"
            out.mkdir()
            _make_read_only(evidence)
            try:
                before = _snapshot(evidence)
                code, payload = self._full_scan(evidence, out)
                after = _snapshot(evidence)
            finally:
                _make_writable(evidence)

        self.assertEqual(code, 0)
        self.assertEqual(sorted(after), sorted(before), "the scan created or removed entries in the evidence folder")
        for name, (digest, size, mtime_ns, mode) in before.items():
            with self.subTest(entry=name):
                self.assertEqual(after[name][0], digest, "SHA-256 changed")
                self.assertEqual(after[name][1], size)
                self.assertEqual(after[name][2], mtime_ns, "mtime changed")
                self.assertEqual(after[name][3], mode, "permissions changed")
        statuses = {item["path"]: item["status"] for item in payload["items"]}
        rows = {item["path"]: item for item in payload["items"]}
        for ext, path in made.items():
            with self.subTest(sample=ext):
                self.assertIn(path.name, statuses, f"{path.name} was not scanned")
                if ext in magic_only:
                    # 미지원, or 판단 불가 with the reason it could not be read.
                    row = rows[path.name]
                    result = row.get("result") or {}
                    if row["status"] == "unsupported":
                        self.assertTrue(row.get("error"))
                        continue
                    self.assertEqual(result.get("verdict_code"), "undetermined", row)
                    gaps = [c for c in result.get("coverage", []) if c["status"] != "ran"]
                    self.assertTrue(gaps, f"{path.name}: 판단 불가 must name the check that could not run")
                    self.assertTrue(all(c["reason"] for c in gaps))
                    continue
                # Every format is examined itself (samples are distinct, so
                # --dedupe marks none of them as a twin).
                self.assertIn(statuses[path.name], {"analyzed", "expanded"})
        hashed = {item["path"]: item.get("sha256") for item in payload["items"] if "::" not in item["path"]}
        for name, (digest, *_rest) in before.items():
            if digest != "dir" and hashed.get(name):
                self.assertEqual(hashed[name], digest, f"reported sha256 of {name} differs from the file")

    def _full_scan(self, evidence: Path, out: Path) -> tuple[int, dict[str, Any]]:
        from deepfake_lens.cli import main

        args = [
            "scan", str(evidence), "--recursive", "--dedupe", "--pixel", "deep", "--deep-signals", "--heatmaps",
            "--cache", str(out / "cache.json"), "--hash-db", str(out / "hashes.json"),
            "--json-out", str(out / "scan.json"), "--csv-out", str(out / "scan.csv"),
            "--html-out", str(out / "scan.html"), "--evidence-statement-out", str(out / "statement.md"),
            "--format", "json", "--max-files", "500",
        ]
        env = {key: value for key, value in os.environ.items() if key != "DEEPFAKE_LENS_REPORT_KEY"}
        # Heatmaps take the default (no --heatmap-dir) path logic; only the
        # tool-owned root is redirected so the test does not write to $HOME.
        env["DEEPFAKE_LENS_HEATMAP_DIR"] = str(out / "heatmaps")
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = main(args)
        return code, json.loads((out / "scan.json").read_text(encoding="utf-8"))

    def test_default_heatmap_dir_is_outside_the_evidence_folder(self) -> None:
        """QA-IN-1 (heatmaps): without --heatmap-dir heatmaps go to the tool-owned root, never into the folder."""
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence"
            env = {key: value for key, value in os.environ.items() if key != core.HEATMAP_DIR_ENV}
            with patch.dict(os.environ, env, clear=True):
                target = core._heatmap_path_for(evidence / "sub" / "a.png", root=evidence, heatmap_dir=None)
                self.assertTrue(core.is_default_heatmap_output(target))
            self.assertFalse(target.resolve().is_relative_to(evidence.resolve()))
            self.assertTrue(target.is_relative_to(Path.home() / ".cache" / "deepfake-lens" / "heatmaps"))
            other = core._heatmap_path_for(Path(tmp) / "other" / "sub" / "a.png", root=Path(tmp) / "other", heatmap_dir=None)
            self.assertNotEqual(target, other, "two case folders must not share heatmap files")
            self.assertFalse(core.is_default_heatmap_output(evidence / "a.heatmap.png"))


class QaIn2DeterministicRescanTest(unittest.TestCase):
    """QA-IN-2: 같은 폴더를 3회 검사(중간에 프로세스 재시작, 폴더 이름 변경) → 타임스탬프·절대경로 필드를 제외한 JSON이 바이트 단위로 동일. 파일 순서 동일."""

    def test_three_scans_across_restart_and_rename_are_byte_identical(self) -> None:
        """QA-IN-2: 같은 폴더를 3회 검사(중간에 프로세스 재시작, 폴더 이름 변경) → 타임스탬프·절대경로 필드를 제외한 JSON이 바이트 단위로 동일. 파일 순서 동일.

        In-process, subprocess, and re-imported-after-rename scans give
        identical JSON and the same file order.
        """
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
            # N1: the unreadable files failed checks with a reason that names
            # the file relative to the scan root, never by its absolute path.
            reasons = [
                entry["reason"]
                for item in payloads[0]["items"] if item["path"] in {"empty.jpg", "fake.gif", "fake.mp3"}
                for entry in (item.get("result") or {}).get("coverage", []) if entry["status"] == "failed"
            ]
            self.assertTrue(reasons)
            for text in [*reasons, *(json.dumps(payload, ensure_ascii=False) for payload in payloads)]:
                self.assertNotIn(str(base), text)
                self.assertNotIn(str(base.resolve()), text)

    def test_walk_order_is_sorted_and_independent_of_os_listing_order(self) -> None:
        """QA-IN-2: file order is the sorted path order even when the OS lists entries reversed.

        W1: one global sort by the full POSIX relative path string — a
        subfolder's files sit where their path sorts ("a-dir/inner.txt"
        before "a-first.txt", "sub/y.txt" before "z-last.txt"), not after
        every file of the parent folder.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            _build_case_folder(root)
            expected = [
                "B-upper.txt", "a-dir/inner.txt", "a-first.txt", "copy-of-a.txt", "empty.jpg", "fake.gif",
                "fake.mp3", "m-bundle.zip", "photo.png", "sub/c.txt", "sub/y.txt", "z-last.txt",
            ]
            self.assertEqual(expected, sorted(expected), "the expected order is plain string order")
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
            self.assertEqual(flat, [name for name in expected if "/" not in name])

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
        """QA-IN-4: 마지막 바이트만 바꾼 동일 크기 파일을 같은 경로에 넣고 touch -r로 mtime 복원 후 재검사 → 캐시 미사용, 새로 분석, 해시가 다르게 기록.

        Last byte changed, size equal, mtime restored → cache miss, new
        analysis, new hash.
        """
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
