"""QA-OUT scenarios (phase 0): verdict contract, fail-closed, entry points.

QA-OUT-2 and QA-OUT-3 (WP-B, G1/G12) are the explicit QA forms of the
fail-closed tests in tests/test_fail_closed.py (whose assertion helper they
reuse): an exception injected into model inference is "실패: <예외 유형>",
never "의존성 부재", and the verdict is 판단 불가; a photo where the face
detector finds nothing records "얼굴 검사 미실행: 얼굴 미검출" and never a
"no manipulation" conclusion. The three face conditions are synthetic
scenes run through the real, unmocked face detector — on OpenCV 5 without
CascadeClassifier and MediaPipe that is the weight-free skin/eye heuristic
(face._skin_eye_heuristic_faces, D15) — with synthetic frontal faces as its
positive control.

The remaining scenarios cover the unified entry point (WP-F, G7/G8).

Runs without neural weights: the packaged models dir carries only
``supported: false`` profiles, so every model check is recorded as skipped
and verdicts come from deterministic evidence alone.

QA-OUT-4 scans the same folder through the CLI (``deepfake-lens scan``,
which calls ``analysis_api.scan_folder``), the stdlib web server
(``webapp.build_server`` + a real HTTP GET /api/scan, synchronous and the
``async=1`` job + /api/scan-status poll) and — when fastapi and httpx are
installed — the FastAPI server (/api/scan and the SSE /api/scan/stream),
plus the library call ``analysis_api.scan_folder``, and requires the same
raw scan JSON row for row (round 3): every item field — verdict text,
evidence with detail and layer, coverage reasons, limitations,
source_guess, model/pixel analysis, sha256 — and the whole summary
(analyzed, capped, cached, container_rows, subfolders_skipped, every count)
plus threshold and weight provenance. Only wall-clock timestamps, absolute
paths under the scanned folder and heatmap locations are normalized; the
stream's copied row fields are checked against the row's result and
dropped. R1: the hostile folder (an archive with an A1111 member next to
traversal/absolute/link members, a deflate bomb, a symlinked file) must
come out of every leg — and of /api/check and /api/check/stream for each of
its files — with the same member, container and symlink rows as the CLI.
B1: the single-file commands (``forensic``, ``classify``, ``explain``,
``legal-report``, ``evidence-statement <file>``) and
``analysis_api.analyze_rows``/``analyze_path`` on evil.zip, bomb.zip and
a1111.png report the folder scan's raw rows for the file (the standalone
shape drops only the legacy band keys, D1) and its conclusion; their text
outputs list the member rows exactly as the scan table prints them.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import webapp_api
from deepfake_lens.analysis_api import AnalysisOptions, analyze_path, analyze_rows, scan_folder, scan_payload
from deepfake_lens.cli import main as cli_main
from deepfake_lens.core import analyze_file, build_classification_result
from deepfake_lens.result_text import TEXT_LEGAL_LIMITATION
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Grade,
    RiskBand,
    ScanItem,
    SourceGuess,
    Verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK = REPO_ROOT / "fixtures" / "benchmark"
TEXT_CORPORA = (REPO_ROOT / "experiments" / "text-corpus", REPO_ROOT / "fixtures" / "adversarial-text")
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
HAVE_CV2 = importlib.util.find_spec("cv2") is not None
HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
CLIENT_HEADERS = {"X-Deepfake-Lens-Client": "qa"}


def _png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Minimal 8-bit RGB PNG (stdlib zlib) from packed row-major pixels."""
    rows = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")


def write_photo_like_png(path: Path, *, seed: int, width: int = 192, height: int = 144) -> Path:
    """A deterministic photo-like image: smooth colored blobs + sensor-like noise.

    Many unique colors, moderate noise residual and row correlation well
    inside the natural-image range — not a gradient, flat fill or white
    noise. Requires numpy.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    image = np.zeros((height, width, 3), dtype=np.float64) + rng.uniform(40, 120, size=3)
    for _ in range(6):
        cx, cy = rng.uniform(0, width), rng.uniform(0, height)
        sigma = rng.uniform(width / 10, width / 3)
        amplitude = rng.uniform(-90, 120, size=3)
        blob = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2))
        image += blob[..., None] * amplitude
    image += rng.normal(0, 7.0, size=image.shape)
    pixels = np.clip(image, 0, 255).astype(np.uint8)
    path.write_bytes(_png_bytes(width, height, pixels.tobytes()))
    return path


# QA-OUT-4 (round 3): legs are compared on the raw scan JSON. Only values
# that legitimately differ between two runs are normalized: wall-clock
# timestamps, absolute paths under the scanned folder (a leg may name the
# folder differently) and heatmap file locations.
VOLATILE_KEYS = frozenset({"generated_at", "scanned_at", "timestamp", "elapsed_ms", "duration_ms"})
# The stream copies these result fields to the row top level (pre-R1 row
# shape); they are checked against the row's result, then dropped.
STREAM_ROW_EXTRAS = ("verdict_code", "grade", "probability")


def _normalize(node: Any, prefixes: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in VOLATILE_KEYS:
                continue
            if key == "heatmap_path" and value:
                out[key] = "<HEATMAP>"
                continue
            out[key] = _normalize(value, prefixes)
        return out
    if isinstance(node, list):
        return [_normalize(value, prefixes) for value in node]
    if isinstance(node, str):
        for prefix in prefixes:
            if prefix and node.startswith(prefix):
                return "<FOLDER>" + node[len(prefix):]
    return node


def _raw_item(item: dict[str, Any], prefixes: tuple[str, ...] = ()) -> dict[str, Any]:
    """One scan row as emitted — every field (detail, layer, limitations,
    verdict text, source_guess, coverage reasons, …) — normalized only for
    timestamps, absolute folder paths and heatmap locations."""
    normalized: dict[str, Any] = _normalize(item, prefixes)
    return normalized


def _without_band(row: dict[str, Any]) -> dict[str, Any]:
    """A scan row without the legacy result.band/band_label (B1: the
    standalone analysis_result rows drop them, D1); the dropped band must be
    the one derived from verdict_code."""
    from deepfake_lens.result_types import band_for_verdict

    out = json.loads(json.dumps(row))
    result = out.get("result")
    if isinstance(result, dict):
        band = result.pop("band", None)
        result.pop("band_label", None)
        assert band == band_for_verdict(Verdict(result["verdict_code"])).value, (row.get("path"), band)
    return dict(out)


def _strip_stream_extras(test: unittest.TestCase, payload: dict[str, Any]) -> dict[str, Any]:
    """Check the stream's copied row fields against the row's result, then drop them."""
    rows = []
    for row in payload["items"]:
        row = dict(row)
        result = row.get("result") or {}
        for key in STREAM_ROW_EXTRAS:
            test.assertIn(key, row)
            test.assertEqual(row.pop(key), result.get(key), f"{row.get('path')}: stream {key}")
        rows.append(row)
    return {**payload, "items": rows}


def _norm_payload(payload: dict[str, Any], folder: Path | None = None) -> dict[str, Any]:
    prefixes: tuple[str, ...] = ()
    if folder is not None:
        prefixes = tuple(sorted({str(folder), str(folder.resolve())}, key=len, reverse=True))
    return {
        "items": {item["path"]: _raw_item(item, prefixes) for item in payload["items"]},
        "order": [item["path"] for item in payload["items"]],
        "thresholds": payload["thresholds"],
        "coverage": payload.get("coverage"),
        "schema_version": payload.get("schema_version"),
        # The whole summary — analyzed, capped, cached, container_rows,
        # subfolders_skipped, external_model_active, every count.
        "summary": payload["summary"],
    }


def _core(item: dict[str, Any]) -> dict[str, Any]:
    """Short view of a raw row for the scenario assertions below."""
    result = item.get("result") or {}
    return {
        "status": item.get("status"),
        "verdict_code": result.get("verdict_code"),
        "grade": result.get("grade"),
        "evidence": [(e.get("title"), e.get("kind"), e.get("direction"), e.get("strength")) for e in result.get("evidence", [])],
        "coverage": [(c.get("check"), c.get("status"), c.get("reason")) for c in result.get("coverage", [])],
        "sha256": item.get("sha256"),
        "error": item.get("error"),
    }


def write_hostile_folder(folder: Path) -> Path:
    """R1 fixture: evil.zip (A1111 member + ``../x.png``, ``/abs/y.png`` and a
    symlink member), a deflate bomb (8 MiB of zeros, ratio far above
    MAX_ARCHIVE_RATIO), a symlink to a PNG, and the A1111 PNG itself."""
    import stat
    import zipfile

    a1111 = (BENCHMARK / "a1111-metadata-marker.png").read_bytes()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a1111.png").write_bytes(a1111)
    with zipfile.ZipFile(folder / "evil.zip", "w") as zf:
        zf.writestr("../x.png", a1111)
        zf.writestr("/abs/y.png", a1111)
        link = zipfile.ZipInfo("link.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
        zf.writestr("ok/a1111.png", a1111)
    with zipfile.ZipFile(folder / "bomb.zip", "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("zeros.png", bytes(8 * 1024 * 1024))
    outside = folder.parent / f"{folder.name}-outside.png"
    outside.write_bytes(a1111)
    (folder / "linked.png").symlink_to(outside)
    return folder


def _draw_frontal_face(image: Any, xx: Any, yy: Any, rng: Any, width: int, height: int) -> None:
    """A frontal face: skin oval, two dark eye ellipses, nose, mouth."""
    import numpy as np

    cx, cy = width * rng.uniform(0.35, 0.65), height * rng.uniform(0.45, 0.55)
    fw, fh = width * 0.28, width * 0.28 * 1.6
    skin = np.array([200.0, 155.0, 130.0]) * rng.uniform(0.85, 1.05)
    image[(((xx - cx) / (fw / 2)) ** 2 + ((yy - cy) / (fh / 2)) ** 2) <= 1.0] = skin
    for side in (-1, 1):
        ex, ey = cx + side * fw * 0.2, cy - fh * 0.12
        image[(((xx - ex) / (fw * 0.09)) ** 2 + ((yy - ey) / (fh * 0.045)) ** 2) <= 1.0] = np.array([35.0, 30.0, 30.0])
    image[(((xx - cx) / (fw * 0.06)) ** 2 + ((yy - (cy + fh * 0.05)) / (fh * 0.1)) ** 2) <= 1.0] = skin * 0.85
    image[(((xx - cx) / (fw * 0.18)) ** 2 + ((yy - (cy + fh * 0.27)) / (fh * 0.04)) ** 2) <= 1.0] = np.array([150.0, 60.0, 60.0])


def write_scene_png(path: Path, *, seed: int, condition: str = "no_face", width: int = 224, height: int = 168) -> Path:
    """A deterministic photo-like scene: soft light blobs, hard-edged objects,
    sensor noise. ``condition``: ``no_face`` (scene only), ``profile_face``
    (a side-view head silhouette: skin ellipse, nose bump, hair),
    ``low_light`` (a frontal face in the scene at 20 % exposure with
    ISO-like noise — the spec's "저조도 얼굴") or ``frontal_face`` (a
    frontal face: oval, two eyes, nose, mouth — the detector's positive
    control). Classified ``photo`` by image_class (asserted by the tests).
    Needs numpy.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    image = np.zeros((height, width, 3), dtype=np.float64) + rng.uniform(40, 120, size=3)
    for _ in range(5):
        cx, cy = rng.uniform(0, width), rng.uniform(0, height)
        sigma = rng.uniform(width / 8, width / 3)
        blob = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2))
        image += blob[..., None] * rng.uniform(-80, 100, size=3)
    for _ in range(int(rng.integers(4, 8))):
        x0, y0 = int(rng.uniform(0, width * 0.8)), int(rng.uniform(0, height * 0.8))
        w = min(int(rng.uniform(width * 0.08, width * 0.35)), width - x0)
        h = min(int(rng.uniform(height * 0.08, height * 0.5)), height - y0)
        shade = np.linspace(1.0, rng.uniform(0.6, 1.0), h)[:, None, None]
        image[y0:y0 + h, x0:x0 + w] = rng.uniform(20, 230, size=3) * shade
    if condition == "profile_face":
        cx, cy = width * rng.uniform(0.3, 0.7), height * 0.5
        head = (((xx - cx) / (width * 0.11)) ** 2 + ((yy - cy) / (height * 0.28)) ** 2) <= 1.0
        nose = (((xx - (cx + width * 0.1)) / (width * 0.03)) ** 2 + ((yy - cy) / (height * 0.05)) ** 2) <= 1.0
        image[head | nose] = np.array([200.0, 155.0, 130.0]) + rng.normal(0, 5, 3)
        image[head & (yy < cy - height * 0.1) & (xx < cx + width * 0.02)] = np.array([40.0, 30.0, 25.0])
    if condition in ("low_light", "frontal_face"):
        _draw_frontal_face(image, xx, yy, rng, width, height)
    if condition == "low_light":
        image = image * 0.2 + rng.normal(0, 4.0, size=image.shape)
    else:
        image += rng.normal(0, 5.0, size=image.shape)
    pixels = np.clip(image, 0, 255).astype(np.uint8)
    path.write_bytes(_png_bytes(width, height, pixels.tobytes()))
    return path


FACE_CONDITIONS = ("no_face", "profile_face", "low_light")
FACE_IMAGES_PER_CONDITION = 20
# Exception types injected into model inference (QA-OUT-2): runtime, input,
# lookup, resource and I/O failures — none of them a missing dependency.
INJECTED_EXCEPTIONS: tuple[type[BaseException], ...] = (RuntimeError, ValueError, KeyError, MemoryError, OSError)
# Phrases that would state a face-manipulation absence (QA-OUT-3).
NO_MANIPULATION_PHRASES = ("얼굴 조작 없음", "조작 없음", "조작 흔적 없음", "조작되지 않")


def _pinned_fake_aide(root: Path) -> Path:
    """A pinned ``aide`` image profile over a fake checkpoint (as in
    test_fail_closed): pin verification passes, so inference is reached."""
    checkpoint = root / "fake-aide.pth"
    checkpoint.write_bytes(b"not a real checkpoint")
    profile = root / "fake-aide-runtime.json"
    pin = {"sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    profile.write_text(json.dumps({"name": "fake-aide", "runtime": "aide", "checkpoint": str(checkpoint), "modality": "image", "pin": pin}), encoding="utf-8")
    return profile


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (scene generator)")
class QaOut2InferenceExceptionTest(unittest.TestCase):
    """QA-OUT-2: an exception inside model inference is a failed check, never a missing dependency."""

    def test_injected_inference_exceptions_are_failed_and_undetermined(self) -> None:
        """QA-OUT-2: 모델 추론 함수에 예외를 강제 주입(monkeypatch)하고 검사 → 커버리지에 "실패: <예외 유형>" 기록, 결론 "판단 불가". "의존성 부재"로 표기되지 않음.

        Five exception types x five photo scenes, injected into
        model_adapter._run_aide behind a correctly pinned fake profile.
        """
        from deepfake_lens.image_class import PHOTO, classify_image
        from deepfake_lens.tests.test_fail_closed import FailClosedAssertions

        assertions = FailClosedAssertions()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = _pinned_fake_aide(root)
            images = [write_scene_png(root / f"scene-{index}.png", seed=500 + index) for index in range(5)]
            for image in images:
                self.assertEqual(classify_image(image).kind, PHOTO, image.name)
            for exc_type in INJECTED_EXCEPTIONS:
                for image in images:
                    with self.subTest(exception=exc_type.__name__, image=image.name), \
                            mock.patch("deepfake_lens.model_adapter._run_aide", side_effect=exc_type("inference crashed")), \
                            self.assertLogs("deepfake_lens.model_adapter", level="ERROR"):
                        item = analyze_file(image, model_path=profile)
                        assertions.assertFailedUndetermined(item, "external_model", exc_type.__name__)
                        result = item.result
                        assert result is not None
                        [entry] = [e for e in result.coverage if e.check == "external_model"]
                        self.assertIn(f"실패: {exc_type.__name__}", entry.describe())
                        self.assertNotIn("의존성 부재", entry.describe())
                        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                        self.assertIsNone(result.probability)
                        self.assertFalse(result.score_is_calibrated)

    def test_failure_survives_the_unified_entry_point(self) -> None:
        """QA-OUT-2 (scan JSON): the failed entry reaches scan_folder's payload unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = _pinned_fake_aide(root)
            media = root / "media"
            media.mkdir()
            write_scene_png(media / "scene.png", seed=77)
            with mock.patch("deepfake_lens.model_adapter._run_aide", side_effect=RuntimeError("boom")), \
                    self.assertLogs("deepfake_lens.model_adapter", level="ERROR"):
                summary, items, _ = scan_folder(media, AnalysisOptions(model_path=profile))
        payload = items[0].to_json()
        coverage = {entry["check"]: entry for entry in payload["result"]["coverage"]}
        self.assertEqual(coverage["external_model"]["status"], "failed")
        self.assertTrue(coverage["external_model"]["reason"].startswith("RuntimeError"))
        self.assertEqual(payload["result"]["verdict_code"], "undetermined")
        self.assertEqual(summary.authenticity_evidence, 0)


@unittest.skipUnless(HAVE_NUMPY and HAVE_CV2, "numpy + opencv required (scene generator, face layer)")
class QaOut3NoFaceTest(unittest.TestCase):
    """QA-OUT-3: an undetected face is recorded as a skipped check, never as "no manipulation"."""

    def _assert_no_face_claim(self, result: Any) -> None:
        self.assertNotEqual(result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)
        self.assertNotEqual(result.band, RiskBand.LOW)
        self.assertFalse([e for e in result.evidence if e.layer == "face" and e.direction == EvidenceDirection.AUTHENTIC])
        texts = [result.verdict, *result.limitations, *(e.title for e in result.evidence), *(e.detail for e in result.evidence)]
        for phrase in NO_MANIPULATION_PHRASES:
            self.assertFalse([text for text in texts if phrase in text], phrase)

    def _face_entry(self, result: Any) -> CoverageEntry:
        [entry] = [e for e in result.coverage if e.check == "face_manipulation"]
        return entry

    def test_no_face_profile_and_low_light_record_face_check_not_run(self) -> None:
        """QA-OUT-3: 얼굴 없는 사진, 측면 얼굴, 저조도 얼굴 각 20장 → 얼굴 미검출 시 커버리지에 "얼굴 검사 미실행: 얼굴 미검출" 기록. 얼굴 조작 결론이 "없음"으로 나오지 않음.

        60 synthetic photo scenes (20 per condition) through the real face
        detector — nothing is mocked. On OpenCV 5 without CascadeClassifier
        and without MediaPipe that is the weight-free skin/eye heuristic
        (face._skin_eye_heuristic_faces, D15), wrapped with a spy only to
        prove it ran. Scenes without a face and profile heads must come
        back "얼굴 미검출" from that run; a low-light frontal face is either
        found (the check ran) or recorded "얼굴 미검출"; no result ever
        claims "no manipulation". The detector's positive control is
        test_real_detector_finds_frontal_faces.
        """
        from deepfake_lens import face as face_module
        from deepfake_lens.image_class import PHOTO, classify_image

        spy = mock.patch.object(face_module, "_skin_eye_heuristic_faces", wraps=face_module._skin_eye_heuristic_faces)
        not_detected = 0
        with tempfile.TemporaryDirectory() as tmp, spy as heuristic:
            root = Path(tmp)
            for condition in FACE_CONDITIONS:
                for index in range(FACE_IMAGES_PER_CONDITION):
                    image = write_scene_png(root / f"{condition}-{index:02d}.png", seed=3000 + index, condition=condition)
                    with self.subTest(condition=condition, image=image.name):
                        self.assertEqual(classify_image(image).kind, PHOTO, "the face layer runs on photos only")
                        item = analyze_file(image, deep_signals=True)
                        result = item.result
                        assert result is not None
                        entry = self._face_entry(result)
                        if condition in ("no_face", "profile_face"):
                            self.assertEqual(entry.status, CoverageStatus.SKIPPED, entry)
                        if entry.status == CoverageStatus.SKIPPED:
                            self.assertEqual(entry.describe(), "얼굴 검사 미실행: 얼굴 미검출")
                            not_detected += 1
                        else:
                            self.assertEqual(entry.status, CoverageStatus.RAN, entry)
                        self.assertFalse([e for e in result.evidence if e.layer == "face"])
                        self._assert_no_face_claim(result)
            # "얼굴 미검출" came from detector runs, not from a stub.
            self.assertGreaterEqual(heuristic.call_count, not_detected)
        self.assertGreaterEqual(not_detected, 2 * FACE_IMAGES_PER_CONDITION)

    def test_real_detector_finds_frontal_faces(self) -> None:
        """QA-OUT-3 (positive control): the same unmocked detector finds 20
        synthetic frontal faces, so its "얼굴 미검출" above is a real negative;
        a found face yields only reference signals, never a conclusion."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(FACE_IMAGES_PER_CONDITION):
                image = write_scene_png(root / f"frontal-{index:02d}.png", seed=3000 + index, condition="frontal_face")
                with self.subTest(image=image.name):
                    item = analyze_file(image, deep_signals=True)
                    result = item.result
                    assert result is not None
                    self.assertEqual(self._face_entry(result).status, CoverageStatus.RAN)
                    self.assertFalse([e for e in result.evidence if e.layer == "face"])
                    self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                    self._assert_no_face_claim(result)

    def test_gif_face_check_is_skipped_not_failed(self) -> None:
        """D15: a GIF is outside the face layer's formats — skipped with the reason."""
        if importlib.util.find_spec("PIL") is None:
            self.skipTest("Pillow writes the GIF")
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            png = write_scene_png(Path(tmp) / "scene.png", seed=5, condition="frontal_face")
            gif = Path(tmp) / "scene.gif"
            with Image.open(png) as source:
                source.save(gif)
            item = analyze_file(gif, deep_signals=True)
        assert item.result is not None
        entry = self._face_entry(item.result)
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)
        self.assertEqual(entry.reason, "지원하지 않는 이미지 형식: .gif")


class QaOut4SameResultEverywhereTest(unittest.TestCase):
    """QA-OUT-4: 같은 폴더를 CLI, GUI(/api/scan), API 서버로 각각 검사 → 세 결과의 결론·근거·확률·임계값 출처가 동일."""

    def setUp(self) -> None:
        # Read roots and the server models dir are process globals; isolate.
        patches: list[Any] = [
            mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()),
            mock.patch.object(webapp_api, "_MODELS_DIR", None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _cli_payload(self, folder: Path) -> dict[str, Any]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["scan", str(folder), "--format", "json"]), 0)
        return json.loads(out.getvalue())

    def _cli_subprocess_payload(self, folder: Path) -> dict[str, Any]:
        """The CLI as a user runs it: ``python -m deepfake_lens scan <folder> --format json`` in a child process."""
        import os
        import subprocess
        import sys

        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (str(REPO_ROOT), os.environ.get("PYTHONPATH"))))}
        completed = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "scan", str(folder), "--format", "json"],
            capture_output=True, env=env, timeout=600, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace")[-2000:])
        payload: dict[str, Any] = json.loads(completed.stdout.decode("utf-8"))
        return payload

    def _web_check_upload(self, folder: Path, names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        """The stdlib server's POST /api/check (multipart upload) for each file: {name: response data}."""
        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=folder)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        out: dict[str, dict[str, Any]] = {}
        try:
            port = server.server_address[1]
            boundary = "----qaout4check"
            for name in names:
                body = (
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode() + (folder / name).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/check", data=body, method="POST",
                    headers={**CLIENT_HEADERS, "Content-Type": f"multipart/form-data; boundary={boundary}"},
                )
                with urllib.request.urlopen(request, timeout=120) as response:
                    self.assertEqual(response.status, 200)
                    out[name] = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
        return out

    def _web_payload(self, folder: Path) -> dict[str, Any]:
        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=folder)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            query = urllib.parse.urlencode({"folder": str(folder)})
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/scan?{query}", headers=CLIENT_HEADERS)
            with urllib.request.urlopen(request, timeout=60) as response:
                self.assertEqual(response.status, 200)
                return json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()

    def _web_async_payload(self, folder: Path) -> dict[str, Any]:
        """The GUI's background scan: GET /api/scan?async=1, poll /api/scan-status."""
        import time

        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=folder)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def get(path: str, params: dict[str, str]) -> dict[str, Any]:
            url = f"http://127.0.0.1:{server.server_address[1]}{path}?{urllib.parse.urlencode(params)}"
            with urllib.request.urlopen(urllib.request.Request(url, headers=CLIENT_HEADERS), timeout=60) as response:
                self.assertEqual(response.status, 200)
                return json.loads(response.read().decode("utf-8"))

        try:
            job = get("/api/scan", {"folder": str(folder), "async": "1"})
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status = get("/api/scan-status", {"job": str(job["job_id"])})
                if status["status"] != "running":
                    self.assertEqual(status["status"], "done", status)
                    return status["result"]
                time.sleep(0.05)
            self.fail("async web scan did not finish")
        finally:
            server.shutdown()
            server.server_close()

    def _api_client(self, folder: Path) -> Any:
        from fastapi.testclient import TestClient

        from deepfake_lens.api_server import create_app

        webapp_api.configure_read_roots(folder)
        return TestClient(create_app(default_folder=folder))

    def _api_payload(self, folder: Path) -> dict[str, Any]:
        client = self._api_client(folder)
        response = client.get(
            "/api/scan", params={"folder": str(folder)}, headers={"host": "localhost", **CLIENT_HEADERS},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _api_stream_payload(self, folder: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """POST /api/scan/stream: (result event, progress events)."""
        client = self._api_client(folder)
        with client.stream(
            "POST", "/api/scan/stream", params={"directory": str(folder)}, headers={"host": "localhost", **CLIENT_HEADERS},
        ) as response:
            self.assertEqual(response.status_code, 200)
            body = "".join(response.iter_text())
        events: dict[str, list[dict[str, Any]]] = {}
        for block in body.split("\n\n"):
            lines = block.strip().splitlines()
            if not lines or not lines[0].startswith("event: "):
                continue
            data = "".join(line[len("data: "):] for line in lines[1:] if line.startswith("data: "))
            events.setdefault(lines[0][len("event: "):], []).append(json.loads(data))
        self.assertNotIn("error", events, events.get("error"))
        self.assertEqual(len(events.get("result", [])), 1, list(events))
        return events["result"][0], events.get("progress", [])

    def _direct_payload(self, folder: Path) -> dict[str, Any]:
        """The library leg: analysis_api.scan_folder with the CLI's default options."""
        options = AnalysisOptions.from_cli_args(type("Args", (), {})())
        summary, items, thresholds = scan_folder(folder, options)
        payload: dict[str, Any] = json.loads(json.dumps(scan_payload(summary, items, thresholds, options), ensure_ascii=False))
        return payload

    def _assert_same(self, reference: dict[str, Any], other: dict[str, Any], leg: str) -> None:
        """Raw item-for-item equality, with a readable first difference."""
        self.assertEqual(reference["order"], other["order"], f"{leg}: row order")
        for path, row in reference["items"].items():
            self.assertEqual(row, other["items"].get(path), f"{leg}: row {path}")
        self.assertEqual(reference, other, leg)

    def test_cli_gui_api_identical_on_benchmark_fixtures(self) -> None:
        """QA-OUT-4: 같은 폴더를 CLI, GUI(/api/scan), API 서버로 각각 검사 → 세 결과의 결론·근거·확률·임계값 출처가 동일.

        Raw rows (verdict text, evidence with detail/layer, coverage reasons,
        limitations, source_guess, model/pixel analysis, sha256) and the
        whole summary are identical across the CLI, the library call and
        the stdlib web server here; the FastAPI leg is test_api_server_leg
        (skipped without fastapi/httpx).
        """
        folder = BENCHMARK.resolve()
        cli = _norm_payload(self._cli_payload(folder), folder)
        direct = _norm_payload(self._direct_payload(folder), folder)
        web = _norm_payload(self._web_payload(folder), folder)

        self.assertEqual(len(cli["items"]), 4)  # 3 PNG fixtures + README.md
        self._assert_same(cli, direct, "scan_folder")
        self._assert_same(cli, _norm_payload(self._cli_subprocess_payload(folder), folder), "CLI subprocess")
        self._assert_same(cli, web, "/api/scan (stdlib)")
        # G7: the GUI used to report builtin_defaults while the CLI loaded the
        # measured profile; both now report the packaged profile.
        self.assertEqual(web["thresholds"]["source"], "threshold_profile")
        self.assertTrue(web["thresholds"]["measured"])
        if HAVE_FASTAPI:
            self._assert_same(cli, _norm_payload(self._api_payload(folder), folder), "/api/scan (FastAPI)")

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed — API-server leg of QA-OUT-4")
    def test_api_server_leg(self) -> None:
        """QA-OUT-4 (API leg): FastAPI /api/scan and /api/scan/stream match the CLI, raw row for row."""
        folder = BENCHMARK.resolve()
        cli = _norm_payload(self._cli_payload(folder), folder)
        self._assert_same(cli, _norm_payload(self._api_payload(folder), folder), "/api/scan")
        stream, _ = self._api_stream_payload(folder)
        self._assert_same(cli, _norm_payload(_strip_stream_extras(self, stream), folder), "/api/scan/stream")

    def _assert_hostile_rows(self, cli: dict[str, Any]) -> None:
        """What the CLI itself must report for the R1 hostile folder."""
        items = {path: _core(row) for path, row in cli["items"].items()}
        member = items["evil.zip::ok/a1111.png"]
        self.assertEqual(member["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value)
        self.assertEqual(member["grade"], Grade.EVIDENCE.value)
        self.assertTrue(member["evidence"])
        self.assertEqual(items["evil.zip"]["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value)
        evil_reasons = [reason for check, status, reason in items["evil.zip"]["coverage"] if check == "archive_member"]
        self.assertEqual(len(evil_reasons), 3, evil_reasons)  # ../x.png, /abs/y.png, link.png
        bomb = items["bomb.zip"]
        self.assertEqual(bomb["verdict_code"], Verdict.UNDETERMINED.value)
        bomb_reasons = [reason for check, status, reason in bomb["coverage"] if check == "archive_member" and status == "skipped"]
        self.assertTrue(any("압축 예산 초과" in str(reason) for reason in bomb_reasons), bomb_reasons)
        self.assertEqual(items["linked.png"]["status"], "skipped")
        self.assertIn("심볼릭 링크", str(items["linked.png"]["error"]))
        for path in ("a1111.png", "evil.zip", "bomb.zip", "evil.zip::ok/a1111.png"):
            self.assertRegex(str(items[path]["sha256"]), r"^[0-9a-f]{64}$", path)
        # R5: container rows are counted by their verdict, never as
        # unsupported/failed — header == table.
        summary = cli["summary"]
        self.assertEqual(summary["unsupported_or_failed"], 0)
        self.assertEqual(summary["manipulation_evidence"], 3)  # a1111.png, the member, evil.zip
        self.assertEqual(summary["undetermined"], 1)  # bomb.zip
        self.assertEqual(summary["skipped"], 1)  # linked.png
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["analyzed"], 4)
        self.assertEqual(summary["container_rows"], 2)

    def test_hostile_folder_cli_and_web_legs_identical(self) -> None:
        """QA-OUT-4 (R1): archive with an A1111 member, zip bomb and file symlink — CLI (in-process and subprocess)
        == scan_folder == /api/scan == async web scan, raw row for row; the stdlib /api/check upload of each
        file reports the scan's rows for it."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_hostile_folder(Path(tmp).resolve() / "case")
            cli = _norm_payload(self._cli_payload(folder), folder)
            self._assert_hostile_rows(cli)
            self._assert_same(cli, _norm_payload(self._cli_subprocess_payload(folder), folder), "CLI subprocess")
            self._assert_same(cli, _norm_payload(self._direct_payload(folder), folder), "scan_folder")
            self._assert_same(cli, _norm_payload(self._web_payload(folder), folder), "/api/scan (stdlib)")
            self._assert_same(cli, _norm_payload(self._web_async_payload(folder), folder), "/api/scan?async=1")
            # The symlink is not uploadable as a link (its bytes would be the
            # target's); its scan row is the skipped one asserted above.
            names = ("evil.zip", "bomb.zip", "a1111.png")
            for name, data in self._web_check_upload(folder, names).items():
                with self.subTest(leg="/api/check (stdlib upload)", file=name):
                    rows = data["items"] if data.get("mode") == "files" else [data["item"]]
                    expected = {path: row for path, row in cli["items"].items() if path == name or path.startswith(name + "::")}
                    self.assertEqual({row["path"]: _raw_item(row) for row in rows}, expected)
                    self.assertEqual(data["thresholds"], cli["thresholds"])

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed — API-server leg of QA-OUT-4")
    def test_hostile_folder_api_and_stream_legs_identical(self) -> None:
        """QA-OUT-4 (R1): the same hostile folder through FastAPI /api/scan and /api/scan/stream matches the CLI raw row for row."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_hostile_folder(Path(tmp).resolve() / "case")
            cli = _norm_payload(self._cli_payload(folder), folder)
            self._assert_hostile_rows(cli)
            self._assert_same(cli, _norm_payload(self._api_payload(folder), folder), "/api/scan (FastAPI)")
            stream, progress = self._api_stream_payload(folder)
            self._assert_same(cli, _norm_payload(_strip_stream_extras(self, stream), folder), "/api/scan/stream")
            # Stream extras: counts mirror the summary; one progress event
            # per row (member, container, symlink rows included).
            self.assertEqual(stream["counts"], {
                "failed": 0, "other": 1, "manipulation_evidence": 3, "authenticity_evidence": 0, "undetermined": 1,
            })
            scanned = [event for event in progress if event.get("stage") == "scan"]
            self.assertEqual(sorted(event["path"] for event in scanned), sorted(cli["items"]))
            self.assertEqual(scanned[-1]["index"], len(cli["items"]))

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed — API-server leg of QA-OUT-4")
    def test_api_check_on_hostile_folder_matches_scan(self) -> None:
        """QA-OUT-4 (R1): /api/check and /api/check/stream on every hostile-folder file report the scan's raw rows
        (archives: member + container rows; the symlink is refused like the scan's skipped row)."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_hostile_folder(Path(tmp).resolve() / "case")
            cli = _norm_payload(self._cli_payload(folder), folder)
            client = self._api_client(folder)
            headers = {"host": "localhost", **CLIENT_HEADERS}
            for name in ("evil.zip", "bomb.zip", "a1111.png"):
                with self.subTest(file=name):
                    response = client.post("/api/check", params={"file_path": str(folder / name)}, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    data = response.json()["data"]
                    expected = {path: row for path, row in cli["items"].items() if path == name or path.startswith(name + "::")}
                    if data.get("mode") == "files":
                        checked = {item["path"]: _raw_item(item, (str(folder),)) for item in data["items"]}
                    else:
                        # A single file is reported under its absolute path
                        # (normalized to the scan's relative path here).
                        item = _raw_item(data["item"], (str(folder) + "/",))
                        item["path"] = item["path"].removeprefix("<FOLDER>")
                        checked = {item["path"]: item}
                    self.assertEqual(checked, expected)
                    with client.stream("POST", "/api/check/stream", params={"file_path": str(folder / name)}, headers=headers) as streamed:
                        body = "".join(streamed.iter_text())
                    result_block = next(block for block in body.split("\n\n") if block.startswith("event: result"))
                    result = json.loads("".join(line[len("data: "):] for line in result_block.splitlines() if line.startswith("data: ")))
                    self.assertEqual(result.get("mode"), data.get("mode"))
                    if result.get("mode") == "files":
                        streamed_rows = {item["path"]: _raw_item(item, (str(folder),)) for item in result["items"]}
                    else:
                        streamed = _raw_item(result["item"], (str(folder) + "/",))
                        streamed["path"] = streamed["path"].removeprefix("<FOLDER>")
                        streamed_rows = {streamed["path"]: streamed}
                    self.assertEqual(streamed_rows, expected)
            # The symlink: the scan reports a skipped row (asserted above);
            # /api/check and its stream refuse the path outright.
            self.assertEqual(cli["items"]["linked.png"]["status"], "skipped")
            response = client.post("/api/check", params={"file_path": str(folder / "linked.png")}, headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
            with client.stream("POST", "/api/check/stream", params={"file_path": str(folder / "linked.png")}, headers=headers) as streamed:
                self.assertEqual(streamed.status_code, 403)

    def _cli_run(self, args: list[str]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(args), 0, args)
        return out.getvalue()

    def test_single_file_commands_report_the_folder_scan_rows(self) -> None:
        """QA-OUT-4 (B1): forensic, classify, explain --json, legal-report --json/text and
        evidence-statement <file> --json-out on evil.zip, bomb.zip and a1111.png report the folder
        scan's raw rows for the file (archives: member rows + container row) and its conclusion;
        the text outputs list the member rows exactly as the scan table prints them."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            folder = write_hostile_folder(base / "case")
            raw_scan = self._cli_payload(folder)
            cli = _norm_payload(raw_scan, folder)
            table = self._cli_run(["scan", str(folder), "--include-low"]).splitlines()
            prefixes = (str(folder),)
            for name in ("evil.zip", "bomb.zip", "a1111.png"):
                target = folder / name
                expected = {path: row for path, row in cli["items"].items() if path == name or path.startswith(name + "::")}
                own = expected[name]["result"]
                with self.subTest(file=name):
                    if name.endswith(".zip"):
                        self.assertGreater(len(expected), 1 if name == "evil.zip" else 0)
                        self.assertEqual(expected[name]["kind"], "archive")
                    for command in ("forensic", "classify", "explain", "legal-report"):
                        payload = json.loads(self._cli_run([command, str(target), "--format", "json"]))
                        rows = {row["path"]: _raw_item(row, prefixes) for row in payload["rows"]}
                        # Raw scan rows; a standalone shape never carries the
                        # legacy band keys (D1), which derive from verdict_code.
                        self.assertEqual(rows, {path: _without_band(row) for path, row in expected.items()}, command)
                        conclusion = payload["conclusion"] if command == "legal-report" else payload
                        self.assertEqual(conclusion["verdict_code"], own["verdict_code"], command)
                        self.assertEqual(conclusion["verdict"], own["verdict"], command)
                        for key in ("evidence", "coverage", "limitations", "reference_signals"):
                            self.assertEqual(payload[key], own[key], f"{command}: {key}")
                        digest = payload["file"]["sha256"] if command == "legal-report" else payload["sha256"]
                        self.assertEqual(digest, expected[name]["sha256"], command)
                        if command == "explain":
                            # The container verdict is the roll-up; each member has its own rule.
                            members = sorted(path for path in expected if "::" in path)
                            self.assertEqual(sorted(entry["path"] for entry in payload.get("member_rules", [])), members)
                            self.assertNotIn("rule_note", payload)
                            self.assertTrue(all("rule_note" not in entry for entry in payload.get("member_rules", [])))
                    if name == "evil.zip":
                        self.assertEqual(own["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value)
                    # The library entry points behind those commands.
                    options = AnalysisOptions.from_cli_args(type("Args", (), {})())
                    self.assertEqual(
                        {row.path: _raw_item(row.to_json(), prefixes) for row in analyze_rows(target, options)}, expected,
                    )
                    if name.endswith(".zip"):
                        self.assertEqual(_raw_item(analyze_path(target, options).to_json(), prefixes), expected[name])
                    # Text: every member row reads exactly as in the scan table.
                    member_lines = [line for line in table if f" {name}::" in line]
                    self.assertEqual(len(member_lines), len(expected) - 1)
                    for args in (
                        ["forensic", str(target), "--format", "table"],
                        ["classify", str(target), "--format", "table"],
                        ["explain", str(target)],
                        ["legal-report", str(target)],
                    ):
                        text = self._cli_run(args).splitlines()
                        for line in member_lines:
                            self.assertIn(line, text, args[0])
                    # evidence-statement <file> == evidence-statement on the
                    # folder scan's rows for that file.
                    rows_json = base / f"{name}.rows.json"
                    rows_json.write_text(json.dumps({
                        **raw_scan,
                        "items": [row for row in raw_scan["items"] if row["path"] == name or row["path"].startswith(name + "::")],
                    }, ensure_ascii=False), encoding="utf-8")
                    single, from_rows = base / f"{name}.single.json", base / f"{name}.from-rows.json"
                    self._cli_run(["evidence-statement", str(target), "--json-out", str(single)])
                    self._cli_run(["evidence-statement", str(rows_json), "--json-out", str(from_rows)])
                    statement = _normalize(json.loads(single.read_text(encoding="utf-8")), prefixes)
                    self.assertEqual(statement, _normalize(json.loads(from_rows.read_text(encoding="utf-8")), prefixes))
                    self.assertEqual(sorted(entry["file_path"] for entry in statement["entries"]), sorted(expected))

    def test_upload_reports_same_threshold_provenance(self) -> None:
        """QA-OUT-4 (GUI upload): /api/analyze-upload reports the thresholds it used and the scan's raw row."""
        boundary = "----qaout4"
        data = (BENCHMARK / "ai-like-gradient.png").read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"ai-like-gradient.png\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        payload: dict[str, Any] = webapp_api._analyze_upload_payload(f"multipart/form-data; boundary={boundary}", body)
        cli = self._cli_payload(BENCHMARK.resolve())
        self.assertEqual(payload["thresholds"], cli["thresholds"])
        uploaded = _raw_item(payload["items"][0])
        scanned = _raw_item(next(item for item in cli["items"] if item["path"] == "ai-like-gradient.png"))
        self.assertEqual(uploaded, scanned)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed (photo-like fixture generator)")
class QaOut1NoWeightsTest(unittest.TestCase):
    """QA-OUT-1: 신경망 가중치를 제거한 상태에서 사진 100장 검사 → 결론이 "판단 불가" 또는 결정적 근거에 의한 결론뿐. "낮음/깨끗함"이 통계적 근거 없이 나오는 건 0개."""

    def test_hundred_photos_without_weights(self) -> None:
        """QA-OUT-1: 신경망 가중치를 제거한 상태에서 사진 100장 검사 → 결론이 "판단 불가" 또는 결정적 근거에 의한 결론뿐. "낮음/깨끗함"이 통계적 근거 없이 나오는 건 0개."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index in range(100):
                write_photo_like_png(folder / f"photo-{index:03d}.png", seed=index, width=160 + (index % 5) * 16, height=128 + (index % 3) * 16)
            summary, items, _ = scan_folder(folder, AnalysisOptions(max_files=200))
        self.assertEqual(summary.analyzed, 100)
        for item in items:
            with self.subTest(path=item.path):
                result = item.result
                assert result is not None
                deciding = [
                    e for e in result.evidence
                    if e.kind == EvidenceKind.DETERMINISTIC and e.strength == EvidenceStrength.STRONG
                ]
                if result.verdict_code == Verdict.MANIPULATION_EVIDENCE:
                    self.assertTrue(any(e.direction == EvidenceDirection.SYNTHETIC for e in deciding), result.evidence)
                elif result.verdict_code == Verdict.AUTHENTICITY_EVIDENCE:
                    self.assertTrue(any(e.direction == EvidenceDirection.AUTHENTIC for e in deciding), result.evidence)
                else:
                    self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                if result.band == RiskBand.LOW:
                    self.assertTrue(any(e.direction == EvidenceDirection.AUTHENTIC for e in deciding), result.evidence)
                self.assertNotEqual(result.band, RiskBand.MEDIUM)
                # No weights: no statistical evidence may carry a probability.
                self.assertFalse(any(e.kind == EvidenceKind.STATISTICAL and e.probability is not None for e in result.evidence))
                self.assertFalse(result.score_is_calibrated)


def _probability_provenance_gaps(result: Any) -> list[str]:
    """Statistical evidence items whose probability lacks provenance (QA-OUT-5)."""
    gaps: list[str] = []
    for item in result.evidence:
        if item.kind != EvidenceKind.STATISTICAL or item.probability is None:
            continue
        missing = [
            name for name, value in (
                ("calibration_id", item.calibration_id),
                ("measured_on", item.measured_on),
                ("probability_ci", item.probability_ci),
            ) if not value
        ]
        if missing:
            gaps.append(f"{item.title}: {', '.join(missing)}")
    return gaps


class QaOut5ProbabilityProvenanceTest(unittest.TestCase):
    """QA-OUT-5: 모델 확률이 표시된 모든 결과 → 각 확률에 보정 코퍼스 ID, 측정 조건, 95% CI가 붙어 있음. 측정 범위 밖 입력(64 px 이하)은 "범위 밖"으로 표시되고 확률 없음."""

    def _result(self, evidence: list[EvidenceItem]) -> Any:
        return build_classification_result(
            subject="이미지",
            evidence=evidence,
            coverage=[CoverageEntry("model:calibrated-fake", CoverageStatus.RAN)],
            source_guess=SourceGuess.unknown("테스트"),
            limitations=[],
            next_checks=[],
        )

    def test_synthetic_calibrated_probability_carries_provenance(self) -> None:
        """QA-OUT-5: 모델 확률이 표시된 모든 결과 → 각 확률에 보정 코퍼스 ID, 측정 조건, 95% CI가 붙어 있음. 측정 범위 밖 입력(64 px 이하)은 "범위 밖"으로 표시되고 확률 없음.

        구조 검사(0단계에 보정 모델 없음): no calibrated model exists in
        phase 0, so no real result carries a probability yet. This test only
        checks the structure — a synthetically built calibrated statistical
        item carries calibration id, measurement conditions and CI through
        the result contract; the real-scan (no probability anywhere) and
        64 px halves are the sibling QA-OUT-5 tests. The conformance table
        labels this QA ID the same way (traceability.json "label").
        """
        calibrated = EvidenceItem(
            title="보정된 모델 확률",
            detail="합성 입력",
            kind=EvidenceKind.STATISTICAL,
            direction=EvidenceDirection.SYNTHETIC,
            strength=EvidenceStrength.MODERATE,
            layer="model",
            probability=0.91,
            probability_ci=(0.86, 0.95),
            calibration_id="calib-qa-out-5",
            measured_on="corpus-qa@test n=400/400",
        )
        result = self._result([calibrated])
        self.assertEqual(_probability_provenance_gaps(result), [])
        self.assertEqual(result.probability, 0.91)
        self.assertEqual(result.probability_ci, (0.86, 0.95))
        self.assertTrue(result.score_is_calibrated)
        payload = result.to_json()
        [row] = [e for e in payload["evidence"] if e["kind"] == "statistical"]
        self.assertEqual(row["calibration_id"], "calib-qa-out-5")
        self.assertEqual(row["measured_on"], "corpus-qa@test n=400/400")
        self.assertEqual(list(row["probability_ci"]), [0.86, 0.95])

    def test_assertion_catches_probability_without_provenance(self) -> None:
        """The QA-OUT-5 check itself fails on a probability with no calibration record."""
        bare = EvidenceItem(
            title="보정 없는 확률",
            detail="합성 입력",
            kind=EvidenceKind.STATISTICAL,
            direction=EvidenceDirection.SYNTHETIC,
            strength=EvidenceStrength.WEAK,
            layer="model",
            probability=0.7,
        )
        result = self._result([bare])
        self.assertEqual(len(_probability_provenance_gaps(result)), 1)
        # Uncalibrated: never a probability-driven verdict or score.
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertFalse(result.score_is_calibrated)

    def test_scanned_results_have_no_unprovenanced_probability(self) -> None:
        """QA-OUT-5 on real scans: every displayed probability has provenance (none exist without weights)."""
        summary, items, _ = scan_folder(BENCHMARK, AnalysisOptions())
        self.assertGreater(summary.analyzed, 0)
        for item in items:
            if item.result is not None:
                self.assertEqual(_probability_provenance_gaps(item.result), [], item.path)

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed (photo-like fixture generator)")
    def test_64px_image_is_out_of_range_without_probability(self) -> None:
        """QA-OUT-5: a 64 px image gets external_model skipped "측정 범위 밖" and no probability."""
        with tempfile.TemporaryDirectory() as tmp:
            image = write_photo_like_png(Path(tmp) / "thumb.png", seed=64, width=64, height=64)
            item = analyze_file(image, model_path=REPO_ROOT / "deepfake_lens" / "models")
        result = item.result
        assert result is not None
        entries = [entry for entry in result.coverage if entry.check == "external_model"]
        self.assertEqual(len(entries), 1, result.coverage)
        self.assertEqual(entries[0].status, CoverageStatus.SKIPPED)
        self.assertIn("측정 범위 밖", entries[0].reason)
        self.assertIsNone(result.probability)
        self.assertIsNone(result.model_analysis)
        self.assertFalse(any(e.probability is not None for e in result.evidence))


class QaOut6TextIsReferenceTest(unittest.TestCase):
    """QA-OUT-6: 텍스트 파일 50개 검사 → 모든 결론 등급이 "참고", 보고서에 법적 한계 문구 존재."""

    def test_text_corpora_are_reference_grade(self) -> None:
        """QA-OUT-6: 텍스트 파일 50개 검사 → 모든 결론 등급이 "참고", 보고서에 법적 한계 문구 존재."""
        texts: list[ScanItem] = []
        for corpus in TEXT_CORPORA:
            _, items, _ = scan_folder(corpus, AnalysisOptions(recursive=True))
            texts.extend(item for item in items if item.kind == "text" and item.status == "analyzed")
        self.assertGreaterEqual(len(texts), 50)
        for item in texts:
            with self.subTest(path=item.path):
                result = item.result
                assert result is not None
                self.assertEqual(result.grade, Grade.REFERENCE)
                self.assertEqual(result.limitations[0], TEXT_LEGAL_LIMITATION)
                self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                self.assertTrue(result.verdict.startswith("참고"), result.verdict)

        from deepfake_lens.core import summarize
        from deepfake_lens.reports import write_html_report

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.html"
            write_html_report(report, summarize(texts, capped=False), texts)
            self.assertIn(TEXT_LEGAL_LIMITATION, report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
