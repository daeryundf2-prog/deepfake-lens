"""Regression guard: no old-contract band/verdict field or value (D1, verification round 1).

Before phase-0 fix 1 every layer module carried ``band`` / ``band_label`` /
``verdict`` fields filled from unmeasured 67/35 (or 50/20) cutoffs, and the
standalone subcommands printed them as conclusions ("낮음", "의심 신호가
강합니다"). This module pins the end state:

* no dataclass anywhere in ``deepfake_lens.*`` has a field named ``band``,
  ``verdict`` or ``band_label`` — except the read-compat scan records
  ``ClassificationResult`` / ``ScanItem`` / ``BatchScanSummary`` (scan
  contract v2: ``band`` is derived from ``verdict_code`` and ``verdict`` is
  the three-verdict text built by result_text.py);
* every standalone subcommand's JSON, run on ``fixtures/benchmark``, has no
  key named ``band`` with an old band value (low/medium/high) at any depth.

Every CLI command is either exercised here or listed in
``EXCLUDED_COMMANDS`` with the reason, so a new subcommand cannot slip past.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import importlib.util
import inspect
import io
import json
import math
import pkgutil
import tempfile
import unittest
import warnings
import wave
from pathlib import Path
from typing import Any, Iterator

import deepfake_lens
from deepfake_lens.cli import COMMANDS, main

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = REPO_ROOT / "fixtures" / "benchmark"
LEGACY_FIELD_NAMES = frozenset({"band", "verdict", "band_label"})
# Scan contract v2 read-compat records (docs/deepfake-lens-json-contract.md).
READ_COMPAT_CLASSES = frozenset({
    "deepfake_lens.result_types.ClassificationResult",
    "deepfake_lens.result_types.ScanItem",
    "deepfake_lens.result_types.BatchScanSummary",
})
OLD_BAND_VALUES = frozenset({"low", "medium", "high"})
OLD_BAND_LABELS = frozenset({"낮음", "주의", "높음"})
HAVE_CV2 = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None

# Commands with no standalone analysis JSON to check, and why.
EXCLUDED_COMMANDS = {
    "scan": "scan contract v2 itself — ClassificationResult keeps the verdict-derived read-compat band (test_json_contract)",
    "benchmark": "evaluation tooling over the scan path (scan rows, read-compat band)",
    "eval": "evaluation tooling over labeled datasets (metrics + scan rows)",
    "calibrate": "evaluation tooling: fits threshold profiles",
    "fusion": "evaluation tooling: fits fusion profiles",
    "feedback": "evaluation tooling: reviewer feedback statistics",
    "train": "training tooling (portable baseline)",
    "train-neural-plan": "writes a training plan, no analysis",
    "perf": "throughput check over the scan path (BatchScanSummary verdict counts)",
    "doctor": "environment diagnostics, no analysis",
    "verify-report": "signature verification, no analysis",
    "corpus": "corpus manifest tooling, no analysis",
    "collect": "collection plan, no analysis",
    "dataset": "dataset manifest/audit, no analysis",
    "models": "detector candidate registry, no analysis",
    "vendor-weights": "weight fetch/pin tooling, no analysis",
    "video": "frame-extraction plan, no analysis",
    "security": "behavioral security checks, no analysis",
    "release": "release checklist, no analysis",
    "api-serve": "server — endpoints covered by test_standalone_contract / test_servers",
    "web": "server — endpoints covered by test_standalone_contract / test_servers",
}


def _iter_package_modules() -> Iterator[str]:
    for info in pkgutil.walk_packages(deepfake_lens.__path__, "deepfake_lens."):
        if info.name.startswith("deepfake_lens.tests"):
            continue
        yield info.name


def _dataclasses_in(module: Any) -> Iterator[type]:
    stack = [obj for obj in vars(module).values() if inspect.isclass(obj)]
    seen: set[int] = set()
    while stack:
        cls = stack.pop()
        if id(cls) in seen or not cls.__module__.startswith("deepfake_lens") or cls.__module__.startswith("deepfake_lens.tests"):
            continue
        seen.add(id(cls))
        if dataclasses.is_dataclass(cls):
            yield cls
        stack.extend(obj for obj in vars(cls).values() if inspect.isclass(obj))


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}.{key}", str(key), child
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _write_wav(path: Path, *, seconds: float, freq: float, rate: int = 16000) -> None:
    frames = bytearray()
    for index in range(int(seconds * rate)):
        sample = int(8000 * math.sin(2 * math.pi * freq * index / rate))
        frames += sample.to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


class NoLegacyDataclassFieldTest(unittest.TestCase):
    """No dataclass field named band/verdict/band_label outside the scan read-compat records."""

    def test_no_legacy_field_names(self) -> None:
        offenders: list[str] = []
        walked: set[str] = set()
        missing_optional: list[str] = []
        for name in _iter_package_modules():
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)  # ml_classifier shim
                    module = importlib.import_module(name)
            except ModuleNotFoundError as exc:  # optional extra absent in this environment
                missing_optional.append(f"{name}: {exc.name}")
                continue
            for cls in _dataclasses_in(module):
                qualified = f"{cls.__module__}.{cls.__qualname__}"
                walked.add(qualified)
                if qualified in READ_COMPAT_CLASSES:
                    continue
                bad = sorted(field.name for field in dataclasses.fields(cls) if field.name in LEGACY_FIELD_NAMES)
                if bad:
                    offenders.append(f"{qualified}: {', '.join(bad)}")
        self.assertEqual(offenders, [], "old-contract fields — rename to reference_band/reference_note (D1)")
        # The walk is not vacuous: the read-compat records and the renamed
        # layer records were all reached.
        for qualified in (
            *READ_COMPAT_CLASSES,
            "deepfake_lens.face.FaceAnalysis",
            "deepfake_lens.c2pa.MetadataForensicAnalysis",
            "deepfake_lens.lipsync.LipsyncAnalysis",
            "deepfake_lens.face_track.FaceTrackAnalysis",
            "deepfake_lens.xai.XAIExplanation",
            "deepfake_lens.audio.SpeakerComparison",
            "deepfake_lens.text_advanced.StylometryComparison",
            "deepfake_lens.watermark.WatermarkAnalysis",
            "deepfake_lens.evidence_statement.EvidenceStatementEntry",
        ):
            self.assertIn(qualified, walked, f"{qualified} not reached (missing optional modules: {missing_optional})")

    def test_read_compat_records_keep_their_contract_fields(self) -> None:
        from deepfake_lens.result_types import ClassificationResult

        names = {field.name for field in dataclasses.fields(ClassificationResult)}
        self.assertTrue({"band", "band_label", "verdict", "verdict_code"} <= names)

    def test_renamed_records_use_the_layer_diagnostic_convention(self) -> None:
        from deepfake_lens.c2pa import MetadataForensicAnalysis
        from deepfake_lens.face import FaceAnalysis

        for cls in (FaceAnalysis, MetadataForensicAnalysis):
            names = {field.name for field in dataclasses.fields(cls)}
            self.assertTrue({"reference_band", "reference_note"} <= names, cls.__name__)


class NoLegacyBandInStandaloneJsonTest(unittest.TestCase):
    """Every standalone subcommand's JSON on fixtures/benchmark: no ``band`` key with low/medium/high."""

    _tmp: tempfile.TemporaryDirectory[str]
    wav_a: Path
    wav_b: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.wav_a = root / "a.wav"
        cls.wav_b = root / "b.wav"
        _write_wav(cls.wav_a, seconds=1.0, freq=220.0)
        _write_wav(cls.wav_b, seconds=1.5, freq=330.0)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _fixtures(self) -> list[Path]:
        images = sorted(path for path in BENCHMARK.iterdir() if path.suffix.lower() == ".png")
        self.assertGreaterEqual(len(images), 3, "fixtures/benchmark images missing")
        return images

    def _as(self, path: Path, suffix: str) -> str:
        """``path``'s bytes under ``suffix`` (a file of the kind the command accepts)."""
        target = Path(self._tmp.name) / f"{path.stem}{suffix}"
        if not target.exists():
            target.write_bytes(path.read_bytes())
        return str(target)

    def _file_commands(self, path: Path) -> list[list[str]]:
        f = str(path)
        # N4/N7: each layer command now refuses a file outside its formats
        # (exit 2, "지원되지 않는 형식입니다"); it used to run on the benchmark
        # PNG whatever its modality (audio/video-analysis/text-advanced/rppg/
        # agent/compare/watermark on a .png). Those commands get the fixture's
        # bytes under a suffix they accept — still undecodable content, so
        # the JSON they print is the same diagnostic shape this test checks.
        video, text = self._as(path, ".mp4"), self._as(path, ".txt")
        commands = [
            ["audio", self._as(path, ".wav"), "--no-default-engine"],
            ["video-analysis", video],
            ["text-advanced", text],
            ["pixel-analysis", f],
            ["inpaint", f],
            ["prnu", f, "--reference", f, "--reference", f, "--reference", f],
            ["rppg", video],
            ["face", f],
            ["avatar", "--file", f],
            ["faceswap-seam", f, "--format", "json"],
            ["forensic", f],
            ["classify", f],
            ["explain", f, "--format", "json"],
            ["agent", "--file", text],
            ["multimodal", f],
            ["legal-report", f, "--format", "json"],
            ["compare", text, text],
            ["evidence", f],
            ["evidence-statement", f, "--format", "json"],
            ["watermark", text, "--secret", "k", "--format", "json"],
        ]
        if HAVE_CV2:
            commands.append(["ml-classify", f])
        return commands

    def _other_commands(self) -> list[list[str]]:
        readme = str(BENCHMARK / "README.md")
        return [
            ["3d", "--text", "a generated low-poly mesh with procedural texture"],
            ["realtime", "--scores", "10,80,95", "--alert-threshold", "50"],
            ["multimodal", "--image-score", "90", "--text-score", "85"],
            ["explain", "--score", "70", "--format", "json"],
            ["agent", "--text", "As an AI language model, I cannot help."],
            # The renamed comparison/watermark records on inputs they accept.
            ["compare", readme, readme],
            ["compare", str(self.wav_a), str(self.wav_b)],
            ["watermark", readme, "--secret", "k", "--format", "json"],
            ["multimodal", *(str(p) for p in self._fixtures())],
            ["evidence-statement", str(BENCHMARK), "--format", "json"],
            ["batch", str(BENCHMARK)],
        ]

    def _run(self, args: list[str]) -> Any:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(args)
        try:
            return json.loads(out.getvalue())
        except json.JSONDecodeError as exc:
            self.fail(f"{args}: stdout is not JSON ({exc}): {out.getvalue()[:200]!r}")

    def _assert_no_old_band(self, payload: Any, label: str) -> None:
        for path, key, value in _walk(payload):
            if key == "band":
                self.assertNotIn(value, OLD_BAND_VALUES, f"{label}: {path} = {value!r}")
            if key in {"band_label", "reference_band", "verdict"} and isinstance(value, str):
                self.assertNotIn(value, OLD_BAND_VALUES | OLD_BAND_LABELS, f"{label}: {path} = {value!r}")

    def test_every_standalone_command_is_covered_or_excluded(self) -> None:
        exercised = {args[0] for args in self._file_commands(self._fixtures()[0]) + self._other_commands()}
        if not HAVE_CV2:
            exercised.add("ml-classify")  # exercised whenever opencv is installed
        commands = set(COMMANDS) - {"-h", "--help"}
        self.assertEqual(exercised & set(EXCLUDED_COMMANDS), set())
        self.assertEqual(commands - exercised - set(EXCLUDED_COMMANDS), set(), "classify the new subcommand here")
        self.assertEqual((exercised | set(EXCLUDED_COMMANDS)) - commands, set(), "stale command name")

    def test_no_band_key_with_old_value_on_benchmark_fixtures(self) -> None:
        for fixture in self._fixtures():
            for args in self._file_commands(fixture):
                label = f"{' '.join(args[:1])} {fixture.name}"
                with self.subTest(command=label):
                    self._assert_no_old_band(self._run(args), label)
        for args in self._other_commands():
            label = " ".join(args[:2])
            with self.subTest(command=label):
                self._assert_no_old_band(self._run(args), label)

    def test_checker_catches_an_old_band(self) -> None:
        """The walker is not vacuous: a nested old band fails it."""
        with self.assertRaises(AssertionError):
            self._assert_no_old_band({"diagnostic": {"items": [{"band": "medium"}]}}, "planted")


if __name__ == "__main__":
    unittest.main()
