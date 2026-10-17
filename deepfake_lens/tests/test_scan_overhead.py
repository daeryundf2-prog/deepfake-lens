"""R27-5 (round 27): per-file overhead of an image scan stays flat, and the per-process caches stay verified.

Image-only scans became ~2x slower per file after the techniques-2026 merge: every file re-read and re-hashed
both C2PA trust bundles three times, rebuilt the SDK Context (parsing ~100 KB of PEM) and searched ``sys.path``
twice for absent watermark dependencies. These tests pin the fix without wall-clock timing:

- the bundles / operator anchors are read once per file version — and a changed file still fails closed;
- the Context is built once per thread and settings content;
- a scan of N files reads the bundles, builds Contexts and searches for absent modules a constant number of
  times, not N times.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import c2pa as c2pa_module
from deepfake_lens import import_probe
from deepfake_lens.tests.png_fixture import write_png


def _bundle_copy(folder: Path) -> Path:
    """A copy of the official bundle (manifest + PEM) in ``folder``; returns the manifest path."""
    meta = json.loads(c2pa_module.TRUST_BUNDLE_MANIFEST.read_text(encoding="utf-8"))
    shutil.copyfile(c2pa_module.TRUST_BUNDLE_MANIFEST, folder / "c2pa-trust.json")
    shutil.copyfile(c2pa_module.TRUST_BUNDLE_MANIFEST.parent / meta["file"], folder / meta["file"])
    return folder / "c2pa-trust.json"


class TrustBundleCacheTest(unittest.TestCase):
    def test_the_bundle_is_read_once_per_file_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _bundle_copy(Path(tmp))
            with mock.patch.object(c2pa_module, "TRUST_BUNDLE_MANIFEST", manifest):
                before = c2pa_module.TRUST_READS["bundle"]
                first = c2pa_module.reader_settings()
                for _ in range(5):
                    self.assertEqual(c2pa_module.reader_settings(), first)
                    c2pa_module.trust_basis()
                # one read of the patched bundle (+ at most one of the ITL if no earlier test read it)
                self.assertLessEqual(c2pa_module.TRUST_READS["bundle"] - before, 2)

    def test_a_cached_bundle_whose_pem_changes_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _bundle_copy(Path(tmp))
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            with mock.patch.object(c2pa_module, "TRUST_BUNDLE_MANIFEST", manifest):
                c2pa_module.reader_settings()  # cached and verified
                pem = Path(tmp) / meta["file"]
                pem.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", encoding="ascii")
                with self.assertRaisesRegex(c2pa_module.C2paTrustError, "무결성 불일치"):
                    c2pa_module.reader_settings()
                with self.assertRaisesRegex(c2pa_module.C2paTrustError, "무결성 불일치"):
                    c2pa_module.trust_basis()
                self.assertEqual(c2pa_module.trust_cache_tokens()[0][:17], "c2pa-trust:error:")

    def test_a_cached_bundle_whose_pin_changes_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _bundle_copy(Path(tmp))
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            with mock.patch.object(c2pa_module, "TRUST_BUNDLE_MANIFEST", manifest):
                c2pa_module.reader_settings()
                manifest.write_text(json.dumps(dict(meta, sha256="0" * 64)), encoding="utf-8")
                with self.assertRaisesRegex(c2pa_module.C2paTrustError, "무결성 불일치"):
                    c2pa_module.reader_settings()
                # restored: verified again and usable
                manifest.write_text(json.dumps(meta), encoding="utf-8")
                self.assertIn("-----BEGIN CERTIFICATE-----", c2pa_module.reader_settings()["trust"]["trust_anchors"])

    def test_a_removed_bundle_is_not_served_from_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _bundle_copy(Path(tmp))
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            with mock.patch.object(c2pa_module, "TRUST_BUNDLE_MANIFEST", manifest):
                c2pa_module.reader_settings()
                (Path(tmp) / meta["file"]).unlink()
                with self.assertRaisesRegex(c2pa_module.C2paTrustError, "읽을 수 없습니다"):
                    c2pa_module.reader_settings()

    def test_a_changed_itl_after_caching_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            meta = json.loads(c2pa_module.ITL_BUNDLE_MANIFEST.read_text(encoding="utf-8"))
            shutil.copyfile(c2pa_module.ITL_BUNDLE_MANIFEST, Path(tmp) / "c2pa-itl.json")
            shutil.copyfile(c2pa_module.ITL_BUNDLE_MANIFEST.parent / meta["file"], Path(tmp) / meta["file"])
            with mock.patch.object(c2pa_module, "ITL_BUNDLE_MANIFEST", Path(tmp) / "c2pa-itl.json"):
                c2pa_module.trust_basis()
                (Path(tmp) / meta["file"]).write_bytes(b"-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----\n")
                with self.assertRaises(c2pa_module.C2paTrustError):
                    c2pa_module.trust_basis()

    def test_operator_anchors_are_cached_and_a_change_is_seen(self) -> None:
        pem = (c2pa_module.TRUST_BUNDLE_MANIFEST.parent / json.loads(
            c2pa_module.TRUST_BUNDLE_MANIFEST.read_text(encoding="utf-8"))["file"]).read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            anchors = Path(tmp) / "anchors.pem"
            anchors.write_bytes(pem)
            with mock.patch.dict(os.environ, {c2pa_module.TRUST_ANCHORS_ENV: str(anchors)}):
                before = c2pa_module.TRUST_READS["operator"]
                for _ in range(4):
                    self.assertEqual(c2pa_module.trust_basis()["operator_anchors_sha256"], hashlib.sha256(pem).hexdigest())
                self.assertEqual(c2pa_module.TRUST_READS["operator"] - before, 1)
                anchors.write_text("not a certificate", encoding="ascii")
                with self.assertRaisesRegex(c2pa_module.C2paTrustError, "PEM 인증서가 없습니다"):
                    c2pa_module.reader_settings()
                anchors.unlink()
                with self.assertRaisesRegex(c2pa_module.C2paTrustError, "읽을 수 없습니다"):
                    c2pa_module.reader_settings()


class _FakeSdk:
    """Counts Context builds; ``valid`` controls the built Context's ``is_valid``."""

    def __init__(self) -> None:
        self.built: list[dict[str, object]] = []
        self.valid = True
        sdk = self

        class Settings:
            @staticmethod
            def from_dict(settings: dict[str, object]) -> dict[str, object]:
                return settings

        class Context:
            def __init__(self, settings: dict[str, object]) -> None:
                sdk.built.append(settings)

            @property
            def is_valid(self) -> bool:
                return sdk.valid

        self.Settings = Settings
        self.Context = Context


class ReaderContextCacheTest(unittest.TestCase):
    def test_one_context_per_settings_content_and_rebuilt_when_invalid(self) -> None:
        sdk = _FakeSdk()
        settings, key = c2pa_module._reader_settings_keyed()
        first = c2pa_module._reader_context(sdk, settings, key)
        self.assertIs(c2pa_module._reader_context(sdk, settings, key), first)
        self.assertEqual(len(sdk.built), 1)
        itl = c2pa_module._reader_context(sdk, *c2pa_module._reader_settings_keyed(itl=True))
        self.assertIsNot(itl, first)
        self.assertEqual(len(sdk.built), 2)
        sdk.valid = False
        c2pa_module._reader_context(sdk, settings, key)
        self.assertEqual(len(sdk.built), 3)

    def test_the_key_follows_the_checked_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _bundle_copy(Path(tmp))
            meta = json.loads(manifest.read_text(encoding="utf-8"))
            _, key = c2pa_module._reader_settings_keyed()
            pem = (Path(tmp) / meta["file"]).read_bytes() + b"\n"
            (Path(tmp) / meta["file"]).write_bytes(pem)
            manifest.write_text(json.dumps(dict(meta, sha256=hashlib.sha256(pem).hexdigest())), encoding="utf-8")
            with mock.patch.object(c2pa_module, "TRUST_BUNDLE_MANIFEST", manifest):
                _, other = c2pa_module._reader_settings_keyed()
            self.assertNotEqual(key, other)
            with mock.patch.dict(os.environ, {c2pa_module.TRUST_ANCHORS_ENV: str(Path(tmp) / meta["file"])}):
                _, with_operator = c2pa_module._reader_settings_keyed()
            self.assertNotEqual(key, with_operator)


class ImportProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        # a module name no other test has probed (absence is remembered per process)
        self.NAME = f"deepfake_lens_r27_5_no_such_module_{self._testMethodName}"

    def test_absence_is_remembered_with_the_same_error(self) -> None:
        before = import_probe.PROBE_STATS["attempts"]
        errors = []
        for _ in range(3):
            with self.assertRaises(ModuleNotFoundError) as caught:
                import_probe.require("json", self.NAME)
            errors.append((str(caught.exception), caught.exception.name))
        self.assertEqual(import_probe.PROBE_STATS["attempts"] - before, 1)
        self.assertEqual(errors, [(f"No module named '{self.NAME}'", self.NAME)] * 3)

    def test_a_module_in_sys_modules_wins_and_none_is_honoured(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            import_probe.require(self.NAME)
        with mock.patch.dict(sys.modules, {self.NAME: mock.MagicMock()}):
            import_probe.require(self.NAME)  # a test double / a module imported meanwhile
        with mock.patch.dict(sys.modules, {"json": None}):
            with self.assertRaises(ModuleNotFoundError) as caught:
                import_probe.require("json")
            self.assertEqual(caught.exception.name, "json")
        import_probe.require("json")  # the None was not remembered

    def test_a_changed_sys_path_searches_again(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            import_probe.require(self.NAME)
        before = import_probe.PROBE_STATS["attempts"]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / f"{self.NAME}.py").write_text("VALUE = 1\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [tmp, *sys.path]):
                try:
                    import_probe.require(self.NAME)
                finally:
                    sys.modules.pop(self.NAME, None)
        self.assertEqual(import_probe.PROBE_STATS["attempts"] - before, 1)

    def test_require_available_raises_what_import_raises_without_importing(self) -> None:
        with self.assertRaises(ModuleNotFoundError) as imported:
            __import__(self.NAME)
        for _ in range(2):
            with self.assertRaises(ModuleNotFoundError) as probed:
                import_probe.require_available(self.NAME)
            self.assertEqual((str(probed.exception), probed.exception.name), (str(imported.exception), imported.exception.name))
        with mock.patch.dict(sys.modules, {"json": None}):
            with self.assertRaises(ModuleNotFoundError) as imported:
                __import__("json")
            with self.assertRaises(ModuleNotFoundError) as probed:
                import_probe.require_available("json")
            self.assertEqual((str(probed.exception), probed.exception.name), (str(imported.exception), imported.exception.name))
        with tempfile.TemporaryDirectory() as tmp:
            name = self.NAME + "_present"
            (Path(tmp) / f"{name}.py").write_text("raise ImportError('broken on import')\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [tmp, *sys.path]):
                import_probe.require_available(name)  # found: not imported here (a broken module fails where it is used)
                self.assertNotIn(name, sys.modules)
                before = import_probe.PROBE_STATS["attempts"]
                import_probe.require_available(name)
                self.assertEqual(import_probe.PROBE_STATS["attempts"], before)  # found once, not searched again


class ScanOverheadTest(unittest.TestCase):
    """A scan's fixed work does not grow with the number of files (the R27-5 regression, deterministically)."""

    def _scan(self, folder: Path, workers: int) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run

        run = scan_folder_run(folder, AnalysisOptions(pixel_mode="off", no_default_engine=True, workers=workers))
        self.assertEqual(len(run.items), 12)

    def test_trust_reads_context_builds_and_import_searches_are_per_scan_not_per_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index in range(12):
                write_png(folder / f"i{index:02d}.png", {"parameters": "a cat"} if index % 3 == 0 else None, size=(64, 64), seed=index)
            for workers in (1, 4):
                with self.subTest(workers=workers):
                    reads = dict(c2pa_module.TRUST_READS)
                    attempts = import_probe.PROBE_STATS["attempts"]
                    self._scan(folder, workers)
                    # at most: official + ITL bundle read, one Context per worker thread (none without the SDK)
                    self.assertLessEqual(c2pa_module.TRUST_READS["bundle"] - reads["bundle"], 2)
                    self.assertLessEqual(c2pa_module.TRUST_READS["context"] - reads["context"], workers)
                    # absent watermark dependencies (torch, onnxruntime, audioseal) are searched for once per scan
                    self.assertLessEqual(import_probe.PROBE_STATS["attempts"] - attempts, 4)

    def test_a_scan_of_small_images_does_not_import_opencv(self) -> None:
        """R27-5: importing cv2 starts OpenCV's BLAS threads; the SDXL check only locates it below its size floor."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            for index in range(3):
                write_png(Path(tmp) / f"i{index}.png", size=(64, 64), seed=index)
            code = (
                "import sys\n"
                "from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run\n"
                f"run = scan_folder_run({tmp!r}, AnalysisOptions(pixel_mode='off', no_default_engine=True))\n"
                "entries = [e for i in run.items for e in i.result.coverage if e.check == 'watermark_sdxl']\n"
                "print(len(run.items), 'cv2' in sys.modules, sorted({e.status.value for e in entries}))\n"
            )
            env = dict(os.environ, HOME=tmp, PYTHONPATH=os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH")])))
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=300, check=True).stdout.split()
        self.assertEqual(out[:2], ["3", "False"], out)


class DisplayNameFastPathTest(unittest.TestCase):
    """R27-5: display_name returns a text of unchanged-alone characters as it is — exactly what the walk gives."""

    POOL = (
        [chr(c) for c in range(0x20, 0x7F)] + ["한", "하", "국", "어", "e", "·", "—", "「", "」", "…", "→", "×", "é", "Å"]
        # characters the walk changes, alone or in a run: controls, invisible, lookalike spaces, marks, Hangul
        # medial/final jamo, NFC-changed singletons (ANGSTROM SIGN, a CJK compatibility ideograph), surrogates
        + ["\n", "\r", "\t", "\x1b", "\x7f", "\u200b", "\u202e", "\u00a0", "\u3000", "\u3164", "\u0301", "\u1161",
           "\u11a8", "\u212b", "\uf900", "\ufe0f", "\u2800", "\ue000", "\u0378", "\udcff", "\\", "|", "\U0001f600"]
    )

    def test_fast_path_equals_the_walk(self) -> None:
        import random

        from deepfake_lens.result_text import _display_name_walk, display_name

        rnd = random.Random(27_5)
        for _ in range(20000):
            text = "".join(rnd.choice(self.POOL) for _ in range(rnd.randint(0, 12)))
            for escape_pipe in (True, False):
                self.assertEqual(display_name(text, escape_pipe=escape_pipe), _display_name_walk(text, escape_pipe), repr(text))


class VersionedCacheTest(unittest.TestCase):
    """R27-5 patterns on the techniques-2026b layers: tables once per file version, hand-offs only for the same file."""

    def test_a_table_is_loaded_once_per_version_and_a_change_is_seen(self) -> None:
        from deepfake_lens import device_labels, screen_capture, versioned_cache

        for module, attr, loader in ((device_labels, "TABLE_PATH", device_labels.load_table), (screen_capture, "TRAITS_PATH", screen_capture.load_traits)):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
                table = Path(tmp) / "table.json"
                shutil.copyfile(getattr(module, attr), table)
                with mock.patch.object(module, attr, table):
                    before = versioned_cache.LOADS["tables"]
                    first = loader()
                    for _ in range(3):
                        self.assertIs(loader(), first)
                    self.assertEqual(versioned_cache.LOADS["tables"] - before, 1)
                    data = json.loads(table.read_text(encoding="utf-8"))
                    table.write_text(json.dumps(dict(data, version="changed")), encoding="utf-8")
                    self.assertEqual(loader().version, "changed")
                    table.write_text(json.dumps(dict(data, schema="other")), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        loader()
                    with self.assertRaises(ValueError):  # a failure is not cached either
                        loader()

    def test_a_hand_off_is_taken_once_and_only_for_the_same_unchanged_file(self) -> None:
        from deepfake_lens import versioned_cache as vc

        with tempfile.TemporaryDirectory() as tmp:
            path = write_png(Path(tmp) / "a.png")
            other = write_png(Path(tmp) / "b.png", seed=1)
            vc.offer("k", path, vc.file_version(path), "value")
            self.assertIs(vc.take("k", other), vc.MISSING)  # another file: the slot is dropped
            self.assertIs(vc.take("k", path), vc.MISSING)
            vc.offer("k", path, vc.file_version(path), "value")
            self.assertEqual(vc.take("k", path), "value")
            self.assertIs(vc.take("k", path), vc.MISSING)  # taken once
            vc.offer("k", path, vc.file_version(path), "value")
            write_png(path, seed=2)  # changed after the offer
            self.assertIs(vc.take("k", path), vc.MISSING)

    def test_a_shared_prefix_reads_like_the_file(self) -> None:
        from deepfake_lens import versioned_cache as vc
        from deepfake_lens.scan_cache import _read_prefix

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.bin"
            path.write_bytes(bytes(range(256)) * 40)
            vc.offer_prefix(path, vc.file_version(path), 4096, _read_prefix(path, 4096))
            for limit in (0, 10, 4096, 5000, 20000):
                self.assertEqual(vc.read_prefix(path, limit), _read_prefix(path, limit), limit)
            small = Path(tmp) / "s.bin"
            small.write_bytes(b"abc")
            vc.offer_prefix(small, vc.file_version(small), 4096, _read_prefix(small, 4096))
            self.assertEqual(vc.read_prefix(small, 1 << 20), b"abc")  # the offered read reached the end
            small.write_bytes(b"abcdef")  # changed: read again
            self.assertEqual(vc.read_prefix(small, 4096), b"abcdef")

    def test_shared_pixels_and_user_comment_equal_a_fresh_read(self) -> None:
        try:
            import numpy  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow + numpy needed for the fixture")
        from deepfake_lens import screen_capture, versioned_cache, wm_imwatermark
        from deepfake_lens.image_metadata import read_image_metadata_full

        with tempfile.TemporaryDirectory() as tmp:
            path = write_png(Path(tmp) / "a.png", {"Comment": "x"}, size=(300, 260), seed=3)
            read_image_metadata_full(path)
            shared_rgb = wm_imwatermark.load_rgb(path)
            shared_comment = screen_capture.exif_user_comment(path)
            fresh_rgb = wm_imwatermark.load_rgb(path)  # nothing offered any more: decoded here
            self.assertIs(versioned_cache.take(screen_capture.USER_COMMENT_HANDOFF, path), versioned_cache.MISSING)
            self.assertEqual(screen_capture.exif_user_comment(path), shared_comment)
            self.assertEqual((shared_rgb.shape, shared_rgb.dtype, shared_rgb.tobytes()), (fresh_rgb.shape, fresh_rgb.dtype, fresh_rgb.tobytes()))


class PlainValueTest(unittest.TestCase):
    """R27-5: result_types.plain_value is dataclasses.asdict's conversion, value for value and type for type."""

    def _same(self, a: object, b: object) -> None:
        self.assertIs(type(a), type(b))
        if isinstance(a, dict):
            self.assertEqual(list(a), list(b))  # key order too
            for key in a:
                self._same(a[key], b[key])  # type: ignore[index]
        elif isinstance(a, (list, tuple)):
            self.assertEqual(len(a), len(b))  # type: ignore[arg-type]
            for x, y in zip(a, b):  # type: ignore[call-overload]
                self._same(x, y)
        else:
            self.assertEqual(a, b)

    def test_rows_convert_exactly_like_asdict(self) -> None:
        from collections import OrderedDict, defaultdict, namedtuple
        from dataclasses import asdict, dataclass, field

        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run
        from deepfake_lens.result_types import _fields_except

        pair = namedtuple("pair", "a b")

        @dataclass
        class Odd:
            t: tuple = (1, [2, {"x": (3,)}])
            n: object = pair(1, [2])
            d: dict = field(default_factory=lambda: defaultdict(list, {"k": [1]}))
            o: dict = field(default_factory=lambda: OrderedDict(z=1, a=2))
            s: set = field(default_factory=lambda: {1, 2})

        self._same(_fields_except(Odd(), frozenset()), asdict(Odd()))
        with tempfile.TemporaryDirectory() as tmp:
            write_png(Path(tmp) / "a.png", {"parameters": "a cat, Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1"})
            write_png(Path(tmp) / "b.png", seed=4)
            (Path(tmp) / "c.txt").write_text("사람이 쓴 글입니다. " * 20, encoding="utf-8")
            items = scan_folder_run(tmp, AnalysisOptions(pixel_mode="off", no_default_engine=True)).items
        self.assertEqual(len(items), 3)
        for item in items:
            self._same(_fields_except(item, frozenset()), asdict(item))
            assert item.result is not None
            self._same(_fields_except(item.result, frozenset()), asdict(item.result))


if __name__ == "__main__":
    unittest.main()
