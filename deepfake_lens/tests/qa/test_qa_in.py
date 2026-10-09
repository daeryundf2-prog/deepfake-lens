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

Moved here from ``test_qa_in_robustness.py`` (W2, verify round 4 — one file per QA area):

QA-IN-5 (WP-H, G34): damaged and hostile inputs never take the scan down.

Twenty damaged inputs sit in one folder next to two valid files. The scan
must survive, every damaged input must come back as "판단 불가 + 이유" or
"미지원"/"실패" with a reason, archive extraction must stay inside its
aggregate budget on disk (no symlinks, nothing outside the temp dir), and
the valid files must get exactly the result they get when scanned alone.

Runs without weights; stdlib-only fixtures (no numpy/Pillow needed).
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import patch

import deepfake_lens
from deepfake_lens import archives, core
from deepfake_lens.analysis_api import AnalysisOptions, scan_folder
from deepfake_lens.core import scan_directory
from deepfake_lens.result_types import ScanItem, Verdict
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
        """QA-IN-1: 보조 검사 — heatmaps: without --heatmap-dir heatmaps go to the tool-owned root, never into the folder."""
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
        """QA-IN-2: 보조 검사 — file order is the sorted path order even when the OS lists entries reversed.

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
        """QA-IN-2: 보조 검사 — a capped scan keeps the first N files in sorted order on every run."""
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
        """QA-IN-4: 보조 검사 — keys carry content hash, options, tool version and pins — not path/size/mtime."""
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
        """QA-IN-4: 보조 검사 — content keys survive a folder rename; rows report the current paths."""
        self._scan()
        renamed = self.root.with_name("case-renamed")
        self.root.rename(renamed)
        summary, items, calls = self._scan(renamed)
        self.assertEqual((summary.cached, calls), (2, 0))
        self.assertEqual(sorted(items), ["evidence.txt", "other.txt"])
        self.assertEqual(items["evidence.txt"].path, "evidence.txt")

    def test_identical_content_rows_keep_their_own_paths(self) -> None:
        """QA-IN-4: 보조 검사 — two files with the same bytes share a cache entry but not a path."""
        (self.root / "twin.txt").write_bytes(self.evidence.read_bytes())
        self._scan()
        summary, items, _ = self._scan()
        self.assertEqual(summary.cached, 3)
        self.assertEqual(items["twin.txt"].path, "twin.txt")
        self.assertEqual(items["evidence.txt"].path, "evidence.txt")

    def test_profile_pin_change_invalidates_cache(self) -> None:
        """QA-IN-4: 보조 검사 — pinning a model profile changes every key — no replay under other weights."""
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
        """QA-IN-4: 보조 검사 — with dedupe on, each file is hashed once per scan (shared memo)."""
        from deepfake_lens import scan_cache

        with patch.object(scan_cache, "_file_fingerprint", wraps=scan_cache._file_fingerprint) as fingerprint:
            scan_directory(self.root, cache_path=self.cache, dedupe=True)
        hashed = [call.args[0].name for call in fingerprint.call_args_list]
        self.assertEqual(sorted(hashed), ["evidence.txt", "other.txt"])


# ============================================================================
# Moved from deepfake_lens/tests/qa/test_qa_in_robustness.py (W2): module docstring above.
# ============================================================================


VALID_PNG = REPO_ROOT / "fixtures" / "benchmark" / "ai-like-gradient.png"
A1111_PNG = REPO_ROOT / "fixtures" / "benchmark" / "a1111-metadata-marker.png"
CONCLUSIVE_CONTROL = "a1111-metadata-marker.png"
# Small aggregate budget so the byte cap is exercised by a few MB of
# fixtures instead of 2 GiB (the production default).
TEST_BUDGET_BYTES = 4 * 1024 * 1024
ESCAPE_NAME = "qa-in-5-escape.txt"


def _zip_bytes(members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _random_bytes(size: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(size)


def _declared_bomb() -> bytes:
    """1 MiB stored member whose headers declare 1 GiB (forged sizes)."""
    raw = bytearray(_zip_bytes({"bomb.bin": _random_bytes(1024 * 1024, 1)}, compression=zipfile.ZIP_STORED))
    declared = struct.pack("<I", 1 << 30)
    local = raw.index(b"PK\x03\x04")
    raw[local + 22:local + 26] = declared
    central = raw.index(b"PK\x01\x02")
    raw[central + 24:central + 28] = declared
    return bytes(raw)


def _deflate_bomb() -> bytes:
    """Real deflate bomb: 64 MiB of zeros compressed to ~64 KB."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        with zf.open("zeros.bin", "w") as handle:
            chunk = bytes(1024 * 1024)
            for _ in range(64):
                handle.write(chunk)
    return buffer.getvalue()


def _nested_hundred() -> bytes:
    """An outer zip holding 100 inner zips (one short text each)."""
    inner = {
        f"inner-{index:03d}.zip": _zip_bytes({f"note-{index:03d}.txt": f"내부 문서 {index}번입니다. 평범한 사람의 글입니다.".encode()})
        for index in range(100)
    }
    return _zip_bytes(inner, compression=zipfile.ZIP_STORED)


def _deeply_nested(levels: int = 8) -> bytes:
    payload = _zip_bytes({"deep.txt": "가장 깊은 곳의 문서입니다.".encode()})
    for level in range(levels):
        payload = _zip_bytes({f"level-{level}.zip": payload})
    return payload


def _symlink_zip() -> bytes:
    """A directory symlink member (-> /etc) plus a file 'inside' it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        link = zipfile.ZipInfo("linkdir")
        link.create_system = 3  # unix
        link.external_attr = (0o120777 << 16)
        zf.writestr(link, "/etc")
        file_link = zipfile.ZipInfo("passwd-link")
        file_link.create_system = 3
        file_link.external_attr = (0o120777 << 16)
        zf.writestr(file_link, "/etc/passwd")
        zf.writestr("linkdir/inside.txt", "심볼릭 링크 디렉터리 안쪽 파일입니다.")
    return buffer.getvalue()


def _absolute_path_zip() -> bytes:
    return _zip_bytes({
        f"/tmp/{ESCAPE_NAME}": b"absolute",
        f"../../{ESCAPE_NAME}": b"traversal",
        f"C:\\{ESCAPE_NAME}": b"drive",
        "ok-member.txt": "정상 멤버입니다.".encode(),
    })


def _corrupt_crc_zip() -> bytes:
    raw = bytearray(_zip_bytes({"data.txt": ("손상된 압축 데이터 " * 200).encode()}))
    local = raw.index(b"PK\x03\x04")
    name_len, extra_len = struct.unpack("<HH", raw[local + 26:local + 30])
    data_start = local + 30 + name_len + extra_len
    for offset in range(data_start + 4, data_start + 24):
        raw[offset] ^= 0xFF
    return bytes(raw)


def _fat_zip() -> bytes:
    """Ten 1 MiB incompressible members — 10 MiB > the test byte budget."""
    return _zip_bytes({f"blob-{index}.bin": _random_bytes(1024 * 1024, 100 + index) for index in range(10)}, compression=zipfile.ZIP_STORED)


def _png_header_only(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"\x78\x9c\x00") + chunk(b"IEND", b"")


def damaged_inputs() -> dict[str, bytes]:
    """The 20 damaged/hostile inputs of QA-IN-5 (name -> bytes)."""
    jpeg_head = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00\x43\x00" + bytes(20)
    return {
        "truncated.jpg": jpeg_head,
        "broken-moov.mp4": b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x7f\xff\xff\xffmoov" + _random_bytes(512, 2),
        "empty.png": b"",
        "empty.wav": b"",
        "text-as.jpg": "이것은 이미지가 아니라 텍스트입니다.".encode(),
        "zip-as.png": _zip_bytes({"x.txt": b"zip disguised as png"}),
        "png-as.wav": VALID_PNG.read_bytes()[:4096],
        "truncated.png": VALID_PNG.read_bytes()[:60],
        "huge-dims.png": _png_header_only(60000, 60000),
        "garbage.wav": b"RIFF\xff\xff\xff\x7fWAVEfmt " + _random_bytes(256, 3),
        "bad.pdf": b"%PDF-1.7\n" + _random_bytes(2048, 4),
        "bad.docx": _random_bytes(2048, 5),
        "bomb-declared.zip": _declared_bomb(),
        "bomb-deflate.zip": _deflate_bomb(),
        "nested-100.zip": _nested_hundred(),
        "deep-nested.zip": _deeply_nested(),
        "dir-symlink.zip": _symlink_zip(),
        "absolute-path.zip": _absolute_path_zip(),
        "corrupt-crc.zip": _corrupt_crc_zip(),
        "truncated.zip": _fat_zip()[:300_000],
        "bad.tar.gz": b"\x1f\x8b\x08\x00" + _random_bytes(1024, 6),
        "fat.zip": _fat_zip(),
    }


def write_valid_files(folder: Path) -> list[str]:
    (folder / "valid.png").write_bytes(VALID_PNG.read_bytes())
    (folder / "valid.txt").write_text("오늘은 공원에서 산책을 하고 친구와 점심을 먹었다. 날씨가 좋아서 오래 걸었다.", encoding="utf-8")
    # Control with a conclusion (D6): A1111 generator metadata must stay
    # manipulation_evidence next to the damaged files — "undetermined" alone
    # would not show that the damaged files leave other results untouched.
    (folder / CONCLUSIVE_CONTROL).write_bytes(A1111_PNG.read_bytes())
    return ["valid.png", "valid.txt", CONCLUSIVE_CONTROL]


# The specific reason each damaged input must be answered with (D6): one of
# these substrings must appear in the row's error, verdict, limitations or
# coverage reasons. Alternatives cover environments without the decoder
# (stdlib-only CI: no numpy/Pillow/opencv/librosa/pymupdf).
DAMAGE_REASONS: dict[str, tuple[str, ...]] = {
    "truncated.jpg": ("잘린 파일",),
    "broken-moov.mp4": ("비디오를 열 수 없습니다", "의존성 부재: cv2"),
    "empty.png": ("빈 파일",),
    "empty.wav": ("파일이 비어 있습니다",),
    "text-as.jpg": ("확장자 위장",),
    "zip-as.png": ("확장자 위장",),
    # N1: libsndfile's "Format not recognised" is reported in Korean.
    "png-as.wav": ("오디오 파일을 열 수 없습니다(형식 인식 불가)", "의존성 부재: librosa", "의존성 부재: soundfile"),
    "truncated.png": ("잘린 파일",),
    "huge-dims.png": ("DecompressionBombError", "의존성 부재: numpy"),
    # R4: libsndfile's "Error in WAV[/W64/RF64] file. <detail>" in Korean.
    "garbage.wav": ("WAV 파일 오류(", "의존성 부재: librosa", "의존성 부재: soundfile"),
    "bad.pdf": ("의존성 부재: pymupdf", "문서 텍스트 추출 실패"),
    "bad.docx": ("문서 텍스트 추출 실패",),
    "bomb-declared.zip": (f"압축 예산 초과(선언 크기 {1 << 30}, 한도 {archives.MAX_ARCHIVE_MEMBER_BYTES})",),
    "bomb-deflate.zip": ("압축 예산 초과(선언 크기 67108864", "압축 폭탄 의심"),
    "nested-100.zip": (f"중첩 압축 예산({archives.TOTAL_NESTED_ARCHIVES}개) 소진",),
    "deep-nested.zip": (f"중첩 압축 최대 깊이({archives.MAX_NESTED_DEPTH}) 초과",),
    "dir-symlink.zip": ("심볼릭 링크 멤버",),
    "absolute-path.zip": ("절대 경로 멤버", "경로 이탈 멤버"),
    "corrupt-crc.zip": ("손상된 멤버 데이터",),
    "truncated.zip": ("압축 해제 실패(BadZipFile",),
    "bad.tar.gz": ("압축 해제 실패(ReadError",),
    "fat.zip": ("압축 해제 총량 예산 소진",),
}
# Temp space a scan may use besides archive extraction (extracted document
# text handed to the text models, a few KB here).
NON_ARCHIVE_TEMP_SLACK = 1024 * 1024
POLL_SECONDS = 0.002


class _TempUsagePoller:
    """Polls a temp root in a thread and records peak bytes (D6/QA-IN-5).

    Peak per top-level ``dflens-arc-*`` extraction dir and for the whole
    root; extraction dirs live until the scan ends, so a post-hoc
    measurement would miss transient files (partial corrupt members).
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.peak_total = 0
        self.peak_by_dir: dict[str, int] = {}
        self.polls = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "_TempUsagePoller":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(POLL_SECONDS)

    def _sample(self) -> None:
        total = 0
        try:
            children = list(self.root.iterdir())
        except OSError:
            return
        for child in children:
            size = _tree_bytes(child)
            total += size
            if child.name.startswith("dflens-arc-"):
                self.peak_by_dir[child.name] = max(self.peak_by_dir.get(child.name, 0), size)
        self.peak_total = max(self.peak_total, total)
        self.polls += 1


def _tree_bytes(path: Path) -> int:
    """Bytes of regular files under ``path`` (no symlink follow; races tolerated)."""
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).lstat().st_size
            except OSError:
                continue
    return total


def _disk_usage(root: Path) -> tuple[int, list[Path]]:
    """Bytes of regular files under ``root`` and any symlinks found (lstat, no follow)."""
    total = 0
    links: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                links.append(path)
            elif name in filenames:
                total += path.lstat().st_size
    return total, links


def _comparable(item: ScanItem) -> dict[str, Any]:
    result = item.result
    return {
        "status": item.status,
        "kind": item.kind,
        "verdict": result.verdict_code if result else None,
        "evidence": [(e.title, e.kind, e.direction) for e in result.evidence] if result else None,
        "coverage": [(c.check, c.status, c.reason) for c in result.coverage] if result else None,
    }


class QaIn5DamagedInputsTest(unittest.TestCase):
    """QA-IN-5: 손상 파일 20종(잘린 JPEG, 깨진 mp4 moov, 빈 파일, 확장자 위장, zip 폭탄, 중첩 zip 100개) → 프로세스 생존, 각 파일이 "판단 불가 + 이유" 또는 "미지원". 디스크 사용 상한 초과 없음. 다른 파일 결과에 영향 없음."""

    _tmp: tempfile.TemporaryDirectory[str]
    folder: Path
    clean: Path
    damaged: dict[str, bytes]
    valid: list[str]
    extractions: dict[str, tuple[int, list[Path], archives.ArchiveExtraction]]
    items: list[ScanItem]
    by_path: dict[str, ScanItem]
    summary: Any
    temp_root: Path
    poller: _TempUsagePoller

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.folder = root / "evidence"
        cls.folder.mkdir()
        cls.damaged = damaged_inputs()
        for name, data in cls.damaged.items():
            (cls.folder / name).write_bytes(data)
        cls.valid = write_valid_files(cls.folder)
        cls.clean = root / "clean"
        cls.clean.mkdir()
        write_valid_files(cls.clean)

        cls.extractions = {}
        real_extract = archives.extract_archive

        def measured_extract(path: Any, dest: Any, **kwargs: Any) -> archives.ArchiveExtraction:
            out = real_extract(path, dest, **kwargs)
            usage, links = _disk_usage(Path(dest))
            cls.extractions[Path(path).name] = (usage, links, out)
            return out

        # Every temp file of the scan lands in a dedicated root that a
        # polling thread measures while the scan runs (peak, not post hoc).
        cls.temp_root = root / "scan-temp"
        cls.temp_root.mkdir()
        with mock.patch.object(archives, "TOTAL_EXTRACTION_BYTES", TEST_BUDGET_BYTES), \
                mock.patch("deepfake_lens.core.extract_archive", measured_extract), \
                mock.patch.object(tempfile, "tempdir", str(cls.temp_root)), \
                _TempUsagePoller(cls.temp_root) as poller:
            cls.summary, cls.items = scan_folder(cls.folder, AnalysisOptions(max_files=100))
        cls.poller = poller
        cls.by_path = {item.path: item for item in cls.items}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_twenty_damaged_inputs_present(self) -> None:
        """QA-IN-5: 보조 검사 — 손상 파일 20종 이상이 모두 검사 결과 행으로 나온다."""
        self.assertGreaterEqual(len(self.damaged), 20)
        for name in self.damaged:
            self.assertIn(name, self.by_path, name)

    def test_each_damaged_input_is_undetermined_unsupported_or_failed_with_reason(self) -> None:
        """QA-IN-5: 손상 파일 20종(잘린 JPEG, 깨진 mp4 moov, 빈 파일, 확장자 위장, zip 폭탄, 중첩 zip 100개) → 프로세스 생존, 각 파일이 "판단 불가 + 이유" 또는 "미지원". 디스크 사용 상한 초과 없음. 다른 파일 결과에 영향 없음.

        Every damaged input (and every member pulled out of one) is 판단
        불가 with a reason, 미지원, or 실패 — never a conclusion. Each damaged
        input's reason must be the specific one for its damage
        (DAMAGE_REASONS: 잘린 파일, 빈 파일, 확장자 위장, 압축 예산 초과(선언
        크기 N, 한도 M), …), not just any non-empty string. The disk budget
        (peak, polled while the scan runs) and the "other files unaffected"
        halves (with a manipulation_evidence control) are the sibling
        QA-IN-5 tests in this class.
        """
        self.assertEqual(set(DAMAGE_REASONS), set(self.damaged))
        for item in self.items:
            top = item.path.split("::", 1)[0]
            if top in self.valid:
                continue
            with self.subTest(path=item.path):
                if item.result is None:
                    self.assertIn(item.status, {"failed", "unsupported", "skipped", "unknown"})
                    self.assertTrue(item.error, "a row without a result must carry a reason")
                    reasons = [item.error or ""]
                else:
                    self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED, item.result.verdict)
                    reasons = [item.result.verdict, *item.result.limitations, *(e.reason for e in item.result.coverage if e.reason)]
                    self.assertTrue(any(reasons), "판단 불가 must say why")
                if "::" in item.path:
                    continue  # a member is an ordinary file; its archive row carries the damage reason
                expected = DAMAGE_REASONS[item.path]
                text = "\n".join(reasons)
                self.assertTrue(any(marker in text for marker in expected), f"{item.path}: none of {expected} in {text[:400]}")

    def test_disk_usage_stays_within_budget_and_inside_temp_dir(self) -> None:
        """QA-IN-5: 보조 검사 — extraction never writes past the aggregate budget, no symlink lands on disk, nothing escapes."""
        self.assertTrue(self.extractions)
        for name, (usage, links, _) in self.extractions.items():
            with self.subTest(archive=name):
                self.assertLessEqual(usage, TEST_BUDGET_BYTES)
                self.assertEqual(links, [])
        for candidate in (Path(tempfile.gettempdir()) / ESCAPE_NAME, Path(tempfile.gettempdir()).parent / ESCAPE_NAME, Path("/") / ESCAPE_NAME):
            self.assertFalse(candidate.exists(), candidate)

    def test_peak_temp_usage_polled_during_the_scan_stays_within_budget(self) -> None:
        """QA-IN-5: 보조 검사 — D6: peak disk use, sampled by a polling thread while the
        scan runs — per archive tree within its budget, and the whole temp
        root within (archives x budget) + a small non-archive allowance;
        everything removed afterwards."""
        poller = self.poller
        self.assertGreater(poller.polls, 1, "the poller never sampled the scan")
        archive_count = sum(1 for name in self.damaged if archives.is_archive(name))
        self.assertTrue(poller.peak_by_dir, "no extraction dir was observed")
        self.assertLessEqual(len(poller.peak_by_dir), archive_count)
        for name, peak in poller.peak_by_dir.items():
            with self.subTest(extraction_dir=name):
                self.assertLessEqual(peak, TEST_BUDGET_BYTES)
        self.assertGreater(max(poller.peak_by_dir.values()), 0)
        self.assertLessEqual(poller.peak_total, archive_count * TEST_BUDGET_BYTES + NON_ARCHIVE_TEMP_SLACK)
        self.assertEqual(list(self.temp_root.iterdir()), [], "temp files left behind")

    def test_budget_exhaustion_is_reported(self) -> None:
        """QA-IN-5: 보조 검사 — The fat archive hits the byte budget; the 100-inner-zip archive hits the nested-archive budget."""
        _, _, fat = self.extractions["fat.zip"]
        self.assertTrue(any("총량 예산" in w for w in fat.warnings), fat.warnings)
        _, _, nested = self.extractions["nested-100.zip"]
        self.assertTrue(any("중첩 압축 예산" in w for w in nested.warnings), nested.warnings)
        opened = [m for m in nested.members if m.name.startswith("note-")]
        self.assertLessEqual(len(opened), archives.TOTAL_NESTED_ARCHIVES)
        container = self.by_path["nested-100.zip"]
        assert container.result is not None
        self.assertEqual(container.result.verdict_code, Verdict.UNDETERMINED)

    def test_hostile_members_are_skipped(self) -> None:
        """QA-IN-5: 보조 검사 — 디렉터리 심볼릭 링크·절대 경로·압축 폭탄·CRC 손상 압축은 구성원을 건너뛰고, 깊은 중첩 압축은 최대 깊이 경고를 남긴다."""
        for name in ("dir-symlink.zip", "absolute-path.zip", "bomb-declared.zip", "bomb-deflate.zip", "corrupt-crc.zip"):
            with self.subTest(archive=name):
                _, _, out = self.extractions[name]
                self.assertGreater(out.skipped, 0, out)
        _, _, deep = self.extractions["deep-nested.zip"]
        self.assertTrue(any("최대 깊이" in w for w in deep.warnings), deep.warnings)

    def test_valid_files_unaffected(self) -> None:
        """QA-IN-5: 보조 검사 — the valid files get the same result as in a clean folder."""
        _, clean_items = scan_folder(self.clean, AnalysisOptions())
        clean = {item.path: _comparable(item) for item in clean_items}
        for name in self.valid:
            with self.subTest(path=name):
                item = self.by_path[name]
                self.assertEqual(item.status, "analyzed")
                self.assertIsNotNone(item.result)
                self.assertEqual(_comparable(item), clean[name])
        control = self.by_path[CONCLUSIVE_CONTROL]
        assert control.result is not None
        self.assertEqual(control.result.verdict_code, Verdict.MANIPULATION_EVIDENCE, control.result.verdict)


if __name__ == "__main__":
    unittest.main()
