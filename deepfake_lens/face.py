"""Face manipulation detection module.

Detects face swap, face reenactment, and lip sync manipulation
using boundary blending, reflection patterns, and color consistency.

Facial landmark anchors are measured with MediaPipe FaceMesh when the
optional ``face_mediapipe`` extra is installed; otherwise they fall back
to box-ratio estimates that are explicitly labelled via
``FaceRegion.landmarks_source`` and must not feed geometry checks.

Two measurement paths exist, tried in order:

1. **Tasks API** (``mp.tasks`` FaceLandmarker, mediapipe >= 0.10.30 / 1.x
   tasks-only builds): needs the ``face_landmarker.task`` model asset —
   point ``DEEPFAKE_LENS_FACE_LANDMARKER`` at it or drop it at
   ``models/face_landmarker.task``. Yields
   ``landmarks_source="mediapipe-facelandmarker"``.
2. **Legacy FaceMesh** (``mp.solutions.face_mesh``, removed in mediapipe
   0.10.30 / 1.x, so the ``face_mediapipe`` extra pins
   ``mediapipe>=0.10,<0.10.30``). Yields
   ``landmarks_source="mediapipe-facemesh"``. Verified end-to-end on
   mediapipe 0.10.21 (macosx arm64, CPython 3.12): a real face photo
   yields measured anchors that differ from the box constants.

With neither available the labelled box-ratio estimate stays active.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from .layer_diagnostic import REFERENCE_BAND, UNAVAILABLE_BAND, raw_score_note
from .error_text import exception_text, failure_reason
from .vendor_weights import default_models_dir
from .native_path import CascadeLoadError, NativePathError, load_cascade
from .native_stderr import quiet_native_stderr


@dataclass(frozen=True)
class FaceEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class FaceRegion:
    x: int
    y: int
    width: int
    height: int
    landmarks: list[tuple[int, int]]
    confidence: float
    # "mediapipe-facelandmarker"/"mediapipe-facemesh" = measured;
    # "box-ratio-estimate" = derived from the detection box and carries no
    # geometric information.
    landmarks_source: str = "box-ratio-estimate"
    # Which detector produced the box: "haar", "mediapipe-facemesh" or the
    # weight-free HEURISTIC_DETECTOR (D15).
    detector: str = "haar"


@dataclass(frozen=True)
class FaceAnalysis:
    # D1: raw heuristic sum, reference only — no 67/35 band, no verdict.
    # reference_band is REFERENCE_BAND when faces were analyzed, else
    # UNAVAILABLE_BAND with the reason in reference_note.
    score: int
    reference_band: str
    reference_note: str
    signals: list[FaceEvidenceSignal]
    limitations: list[str]
    face_count: int
    manipulation_type: str
    confidence: str
    # R7: why there is (or is not) a manipulation_type — FACE_STATUS_*.
    # Callers branch on this, never on the display value of
    # manipulation_type.
    status: str = "analyzed"

    def to_json(self) -> dict[str, object]:
        return asdict(self)


# R7: FaceAnalysis.status values.
FACE_STATUS_ANALYZED = "analyzed"
FACE_STATUS_NO_FACE = "no_face"
FACE_STATUS_UNSUPPORTED = "unsupported_format"
FACE_STATUS_UNAVAILABLE = "unavailable"
FACE_STATUS_FAILED = "failed"
# R7: display values of manipulation_type/confidence when nothing was
# classified. The diagnostic used to print ``manipulation_type: none`` for
# a photo with no detected face, which reads as "no manipulation"; it now
# says what happened — no face found, or the analysis did not run.
NO_FACE_LABEL = "얼굴 미검출"
NOT_APPLICABLE_LABEL = "해당 없음"
# N7: Korean display label of each manipulation_type identifier (the
# identifier itself stays in the JSON field; text shown to the examiner
# uses the label).
MANIPULATION_TYPE_LABELS = {
    "face_swap": "얼굴 교체(face swap) 추정",
    "reenactment": "표정·동작 재연(reenactment) 추정",
    "face_paste": "얼굴 붙여넣기 추정",
    "unknown": "유형 미상",
}


def manipulation_type_label(manipulation_type: str) -> str:
    """Korean label for a manipulation_type value (labels pass through unchanged)."""
    return MANIPULATION_TYPE_LABELS.get(manipulation_type, manipulation_type)


@quiet_native_stderr  # Y5: OpenCV decoder chatter (fd 2, e.g. grfmt_png) goes to the log, not the console
def _imread_unicode(path: Path | str):
    """cv2.imread that survives non-ASCII paths on Windows.

    cv2.imread silently returns None for paths containing non-ASCII
    characters (Korean/CJK filenames) on Windows — read the bytes and
    decode instead. Returns None on any failure, like cv2.imread.
    """
    import cv2
    import numpy as np

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


@quiet_native_stderr  # Y5: OpenCV decoder chatter (fd 2, e.g. grfmt_png) goes to the log, not the console
def analyze_faces(
    path: Path | str,
) -> FaceAnalysis:
    """Analyze an image for face manipulation signs."""
    image_path = Path(path)
    if not image_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {image_path}")

    extension = image_path.suffix.lower()
    if extension not in FACE_IMAGE_EXTENSIONS:
        # D15: a format the face layer does not read (GIF) is "not
        # applicable" — a skip with a reason, not an analysis failure.
        return _unsupported_format_analysis(extension)

    try:
        importlib.import_module("cv2")  # availability probe (G12: was an unused import)
        importlib.import_module("numpy")  # availability probe (G12: was an unused import)
    except ImportError:
        return _error_analysis("opencv가 설치되어 있지 않습니다. `pip install opencv-python`로 설치하세요.")

    try:
        image = _imread_unicode(image_path)
        if image is None:
            return _error_analysis("이미지를 읽을 수 없습니다.")
    except Exception as exc:
        return _error_analysis(f"이미지 읽기 오류: {exception_text(exc)}")

    try:
        faces = _detect_faces_strict(image)
    except FaceDetectorUnavailable as exc:
        return _unavailable_analysis(f"얼굴 검출기 없음: {exception_text(exc)}")
    except FaceDetectionError as exc:
        return _error_analysis(f"얼굴 검출 오류: {exception_text(exc)}")
    if not faces:
        return FaceAnalysis(
            score=0,
            reference_band=UNAVAILABLE_BAND,
            reference_note="얼굴이 감지되지 않았습니다.",
            signals=[],
            limitations=["얼굴이 감지되지 않아 분석할 수 없습니다."],
            face_count=0,
            manipulation_type=NO_FACE_LABEL,
            confidence=NOT_APPLICABLE_LABEL,
            status=FACE_STATUS_NO_FACE,
        )

    signals: list[FaceEvidenceSignal] = []
    limitations: list[str] = []

    for face in faces:
        # Boundary blending
        boundary_signal = _boundary_blending(face, image)
        if boundary_signal:
            signals.append(boundary_signal)

        # Reflection analysis
        reflection_signal = _reflection_analysis(face, image)
        if reflection_signal:
            signals.append(reflection_signal)

        # Color temperature
        color_signal = _color_temperature(face, image)
        if color_signal:
            signals.append(color_signal)

        # Lighting-direction consistency (face vs scene)
        lighting_signal = _lighting_consistency(face, image)
        if lighting_signal:
            signals.append(lighting_signal)

    # Multi-face consistency
    if len(faces) > 1:
        multi_face_signal = _multi_face_consistency(faces, image)
        if multi_face_signal:
            signals.append(multi_face_signal)

    # Limitations
    if len(faces) == 1:
        limitations.append("단일 얼굴만 감지되어 다중 얼굴 비교가 불가합니다.")
    landmark_sources = {face.landmarks_source for face in faces}
    if landmark_sources <= MEASURED_LANDMARK_SOURCES and landmark_sources:
        limitations.append("랜드마크는 MediaPipe 실측값입니다. 기하/대칭 검증은 아직 구현되지 않았습니다.")
    else:
        limitations.append("랜드마크가 감지 박스 비율 추정값(landmarks_source=box-ratio-estimate)이며 실측이 아닙니다.")
        limitations.append("랜드마크 기하/대칭 검증은 실측 랜드마크가 없어 미평가입니다.")
        limitations.append("눈 위치는 감지 박스에서 추정한 값이므로 반사 패턴 비교는 참고 수준입니다.")
    if any(face.detector == HEURISTIC_DETECTOR for face in faces):
        limitations.append(HEURISTIC_DETECTOR_LIMITATION)
    limitations.append("로컬 휴리스틱 기반 선별 결과이며, 확정적 판별이 아닙니다.")

    score = min(100, sum(signal.weight for signal in signals))

    manipulation_type = _classify_manipulation_type(signals)
    confidence = _calculate_confidence(score, len(faces), len(signals))

    return FaceAnalysis(
        score=score,
        reference_band=REFERENCE_BAND,
        reference_note=raw_score_note("얼굴 조작 휴리스틱", score) + f" (얼굴 {len(faces)}개, 신호 {len(signals)}개)",
        signals=signals,
        limitations=limitations,
        face_count=len(faces),
        manipulation_type=manipulation_type,
        confidence=confidence,
    )


# Still-image formats the face layer decodes (cv2.imdecode); GIF is not one.
FACE_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
UNSUPPORTED_FORMAT = "unsupported_format"
UNSUPPORTED_FORMAT_REASON = "지원하지 않는 이미지 형식"


def _unsupported_format_analysis(extension: str) -> FaceAnalysis:
    message = f"{UNSUPPORTED_FORMAT_REASON}: {extension or '(확장자 없음)'}"
    return FaceAnalysis(
        score=0,
        reference_band=UNAVAILABLE_BAND,
        reference_note=message,
        signals=[],
        limitations=[message],
        face_count=0,
        manipulation_type=NOT_APPLICABLE_LABEL,
        confidence=NOT_APPLICABLE_LABEL,
        status=FACE_STATUS_UNSUPPORTED,
    )


def _unavailable_analysis(message: str) -> FaceAnalysis:
    """No detector could run — distinct from "no face" and from an error."""
    return FaceAnalysis(
        score=0,
        reference_band=UNAVAILABLE_BAND,
        reference_note=message,
        signals=[],
        limitations=[message],
        face_count=0,
        manipulation_type=NOT_APPLICABLE_LABEL,
        confidence=NOT_APPLICABLE_LABEL,
        status=FACE_STATUS_UNAVAILABLE,
    )


def _error_analysis(message: str) -> FaceAnalysis:
    return FaceAnalysis(
        score=0,
        reference_band=UNAVAILABLE_BAND,
        reference_note=message,
        signals=[],
        limitations=[message],
        face_count=0,
        manipulation_type=NOT_APPLICABLE_LABEL,
        confidence=NOT_APPLICABLE_LABEL,
        status=FACE_STATUS_FAILED,
    )


def haar_cascade_candidates() -> list[str]:
    """The Haar frontal-face cascade files to try, in order (existing files only).

    OpenCV 5.x keeps CascadeClassifier (xobjdetect) but no longer ships the
    cascade XML — the bundled copy under models/ keeps air-gapped installs
    detecting faces; ``DEEPFAKE_LENS_HAAR_CASCADE`` names another.
    """
    import cv2

    cv2_data = getattr(cv2, "data", None)
    cv2_cascade_dir = getattr(cv2_data, "haarcascades", "") or ""
    candidates = [
        cv2_cascade_dir + "haarcascade_frontalface_default.xml" if cv2_cascade_dir else "",
        os.environ.get("DEEPFAKE_LENS_HAAR_CASCADE") or "",
        str(Path(__file__).resolve().parent / "models" / "haarcascade_frontalface_default.xml"),
    ]
    return [candidate for candidate in candidates if candidate and Path(candidate).is_file()]


def load_face_cascade() -> Any:
    """R14-5 (round 14): the first Haar cascade that loads, None when there is no cascade file at all.

    Every file goes through :func:`native_path.load_cascade` (never a raw
    ``cv2.CascadeClassifier(path)``: a Korean Windows install path loaded
    nothing and the face checks said "얼굴 미검출"). Files that exist but
    none of which loads raise :class:`CascadeLoadError` — the check that
    needed the detector is ``failed``.
    """
    failures: list[BaseException] = []
    for candidate in haar_cascade_candidates():
        try:
            return load_cascade(candidate)
        except (CascadeLoadError, NativePathError) as exc:
            failures.append(exc)
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise CascadeLoadError("; ".join(str(exc) for exc in failures)) from failures[-1]
    return None


class FaceDetectorUnavailable(RuntimeError):
    """No face detector could run (no Haar cascade XML and no MediaPipe)."""


class FaceDetectionError(RuntimeError):
    """Every available face detector raised; "no face" would be a lie."""


def face_detector_unavailable_reason(*, require_landmarks: bool = False) -> str | None:
    """None when at least one face detector can run, else why not.

    Cheap probe (no inference) so callers can report "의존성 부재" instead
    of "얼굴 미검출" when there is nothing that could have found a face.
    With opencv + numpy the weight-free heuristic (D15) can always run.
    ``require_landmarks`` asks for a detector with measured landmarks
    (MediaPipe) — the face-track layer uses only those.
    """
    try:
        importlib.import_module("cv2")  # availability probe (G12: was an unused import)
        importlib.import_module("numpy")  # availability probe (G12)
    except ImportError:
        return "opencv 없음"
    if require_landmarks:
        try:
            import mediapipe  # noqa: F401
        except ImportError:
            return "실측 랜드마크 검출기 없음: mediapipe"
        return None
    return None


def _detect_faces(image: Any) -> list[FaceRegion]:
    """Lenient detection for helper callers (crops, gates): [] on any problem.

    Analysis entry points that report "얼굴 미검출" must use
    :func:`_detect_faces_strict` instead, so a missing or crashed detector
    is never mistaken for an image without faces (G1/G12).
    """
    try:
        return _detect_faces_strict(image)
    except (FaceDetectorUnavailable, FaceDetectionError):
        return []


def _detect_faces_strict(image: Any) -> list[FaceRegion]:
    """Detect faces: OpenCV Haar, then MediaPipe FaceMesh, then the
    weight-free skin/eye heuristic (D15).

    Haar misses valid frontal faces on generated/atypical imagery (measured
    on local samples); when it returns nothing, a whole-image FaceMesh pass
    recovers detection coverage. Regions recovered by the fallback carry the
    ``mediapipe-facemesh-detection`` landmark source. OpenCV 5 wheels have
    no CascadeClassifier, so on a stock install the heuristic is the
    detector that runs; ``FaceRegion.detector`` names the one that found
    each face.
    """
    try:
        import cv2
        import numpy as np  # noqa: F401
    except ImportError as exc:
        raise FaceDetectorUnavailable(f"opencv 없음: {exc}") from exc

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    regions = []
    detectors_run = 0
    errors: list[BaseException] = []
    if hasattr(cv2, "CascadeClassifier"):
        faces: Any = []
        try:
            # R14-5: loaded through native_safe_path; a cascade file that
            # exists but does not load is an error, never "no face".
            try:
                face_cascade = load_face_cascade()
            except CascadeLoadError as exc:
                errors.append(exc)
                face_cascade = None
            if face_cascade is not None:
                detectors_run += 1
                faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        except cv2.error as exc:
            errors.append(exc)
            faces = []
        for x, y, w, h in faces:
            landmarks, source = _face_landmarks(image, x, y, w, h)
            regions.append(
                FaceRegion(
                    x=x, y=y, width=w, height=h,
                    landmarks=landmarks, confidence=0.9,
                    landmarks_source=source,
                )
            )
    if regions:
        return regions
    try:
        mesh_regions = _mediapipe_detect_faces(image, strict=True)
    except FaceDetectorUnavailable:
        mesh_regions = None
    except FaceDetectionError as exc:
        errors.append(exc)
        mesh_regions = None
    if mesh_regions is not None:
        detectors_run += 1
        if mesh_regions:
            return mesh_regions
    # D15: the weight-free heuristic always runs when Haar/MediaPipe found
    # nothing (or do not exist), so "얼굴 미검출" comes from a real run.
    try:
        heuristic_regions = _skin_eye_heuristic_faces(image)
    except cv2.error as exc:
        errors.append(exc)
    else:
        detectors_run += 1
        if heuristic_regions:
            return heuristic_regions
    if detectors_run == 0:
        if errors:
            raise FaceDetectionError(f"{type(errors[0]).__name__}: {errors[0]}") from errors[0]
        raise FaceDetectorUnavailable("Haar cascade XML과 MediaPipe가 모두 없습니다")
    if errors:
        raise FaceDetectionError(f"{type(errors[0]).__name__}: {errors[0]}") from errors[0]
    return []


def _mediapipe_detect_faces(image: Any, max_faces: int = 3, *, strict: bool = False) -> list[FaceRegion]:
    """Whole-image MediaPipe FaceMesh pass used when Haar finds nothing.

    Each returned mesh's landmark extent becomes the face box (expanded
    ~15%); landmarks are honest box estimates, not measured anchors. Returns
    [] when mediapipe is absent or no mesh is found.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError as exc:
        if strict:
            raise FaceDetectorUnavailable(f"mediapipe 없음: {exc}") from exc
        return []
    if not hasattr(mp, "solutions"):
        if strict:
            raise FaceDetectorUnavailable("mediapipe에 solutions API가 없습니다")
        return []

    try:
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=max_faces,
            min_detection_confidence=0.5,
        )
        try:
            result = face_mesh.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        finally:
            face_mesh.close()
    except Exception as exc:
        if strict:
            raise FaceDetectionError(failure_reason(exc)) from exc
        return []

    if not result.multi_face_landmarks:
        return []

    img_h, img_w = image.shape[:2]
    regions: list[FaceRegion] = []
    for face_landmarks in result.multi_face_landmarks:
        xs = [lm.x for lm in face_landmarks.landmark]
        ys = [lm.y for lm in face_landmarks.landmark]
        x0, x1 = min(xs) * img_w, max(xs) * img_w
        y0, y1 = min(ys) * img_h, max(ys) * img_h
        bw, bh = x1 - x0, y1 - y0
        if bw < 8 or bh < 8:
            continue
        mx, my = bw * 0.15, bh * 0.15
        x = int(max(0, x0 - mx))
        y = int(max(0, y0 - my))
        w = int(min(img_w, x1 + mx) - x)
        h = int(min(img_h, y1 + my) - y)
        if w < 8 or h < 8:
            continue
        regions.append(
            FaceRegion(
                x=x, y=y, width=w, height=h,
                landmarks=_estimate_landmarks(x, y, w, h),
                confidence=0.7,
                landmarks_source="mediapipe-facemesh-detection",
                detector="mediapipe-facemesh",
            )
        )
    return regions


# ---------------------------------------------------------------------------
# Dependency-free fallback detector (D15). OpenCV 5.0 wheels no longer ship
# cv2.CascadeClassifier and MediaPipe is optional, so without this the face
# check could only ever say "의존성 부재". cv2.FaceDetectorYN (YuNet) exists in
# OpenCV 5 but needs an ONNX download that would have to be sha256-pinned like
# any weight; phase 0 uses this weight-free heuristic instead.
#
# Method (eyes first, so a face touching a skin-coloured background or neck
# is still found):
# 1. skin pixels by the YCrCb box of Chai & Ngan (1999, "Face segmentation
#    using skin-color map in videophone applications", IEEE TCSVT 9(4)),
#    after a bounded gain on dark frames;
# 2. eye candidates = dark non-skin holes enclosed by skin (not connected to
#    the frame border);
# 3. a pair of candidates at eye geometry (level, similar size) defines a
#    face box from the inter-eye distance d (width 2.5 d, height 3.5 d, eyes
#    1.3 d below the top — typical adult proportions, interpupillary
#    distance ~ 0.4 of face width);
# 4. the box is accepted when the ellipse inscribed in it is mostly skin.
#
# Limits (unmeasured; phase 1 replaces it with a pinned detector): finds
# frontal, upright faces with both eyes visible as dark regions and skin in
# the Chai-Ngan chroma box; misses profiles, closed/occluded eyes, faces with
# sunglasses, skin outside the box, faces with eyes under ~3 px apart at the
# working scale; can fire on skin-coloured objects with two dark spots.
# Landmarks are box-ratio estimates (not measured geometry).
# ---------------------------------------------------------------------------
HEURISTIC_DETECTOR = "skin-eye-heuristic"
HEURISTIC_WORK_MAX_SIDE = 320
SKIN_CR_RANGE = (133, 173)
SKIN_CB_RANGE = (77, 127)
# Skin needs some light: below this luma the chroma is mostly noise.
SKIN_MIN_LUMA = 40
# Low-light normalisation: when the 95th-percentile luma is below
# LOW_LIGHT_P95 the frame is gained towards LOW_LIGHT_TARGET (at most
# LOW_LIGHT_MAX_GAIN x) before the skin test.
LOW_LIGHT_P95 = 120
LOW_LIGHT_TARGET = 200
LOW_LIGHT_MAX_GAIN = 5.0
LOW_LIGHT_BLUR_KERNEL = (5, 5)
# Eye candidates: pixel area at the working scale, darkness vs face skin.
EYE_MIN_AREA_PX = 4
EYE_MAX_AREA_FRACTION = 0.01
EYE_DARK_RATIO = 0.6
EYE_MAX_CANDIDATES = 60
# Pair geometry: inter-eye distance (px at the working scale), vertical
# offset relative to it, area ratio.
EYE_MIN_DISTANCE_PX = 8
EYE_MAX_TILT = 0.25
EYE_MAX_AREA_RATIO = 3.0
# Face box from the inter-eye distance d.
FACE_WIDTH_PER_EYE_DISTANCE = 2.5
FACE_HEIGHT_PER_EYE_DISTANCE = 3.5
FACE_TOP_ABOVE_EYES = 1.3
# Share of the inscribed ellipse that must be skin (eye/mouth holes and
# brows excluded by the margin), and share of it that must lie in frame.
FACE_MIN_SKIN_FRACTION = 0.6
FACE_MIN_INSIDE_FRACTION = 0.8
# Eye blobs larger than this share of the face box are not eyes.
EYE_MAX_BOX_FRACTION = 0.06
HEURISTIC_CONFIDENCE = 0.5
HEURISTIC_DETECTOR_LIMITATION = (
    "얼굴 검출: 피부색·눈 영역 휴리스틱(가중치 없음, 미측정) — 정면이고 두 눈이 보이는 얼굴만 찾으며, "
    "측면·가려진 얼굴은 놓치고 피부색 물체를 얼굴로 잡을 수 있습니다."
)


def _skin_eye_heuristic_faces(image: Any) -> list[FaceRegion]:
    """Weight-free face detector (D15); BGR uint8 image -> face regions."""
    import cv2
    import numpy as np

    if image is None or getattr(image, "ndim", 0) != 3 or image.shape[2] < 3:
        return []
    height0, width0 = image.shape[:2]
    scale = min(1.0, HEURISTIC_WORK_MAX_SIDE / max(height0, width0))
    work = np.ascontiguousarray(image[..., :3])
    if scale < 1.0:
        work = cv2.resize(work, (max(1, round(width0 * scale)), max(1, round(height0 * scale))), interpolation=cv2.INTER_AREA)
    ycrcb = cv2.cvtColor(work, cv2.COLOR_BGR2YCrCb)
    p95 = float(np.percentile(ycrcb[..., 0], 95))
    if 0 < p95 < LOW_LIGHT_P95:
        gain = min(LOW_LIGHT_MAX_GAIN, LOW_LIGHT_TARGET / p95)
        work = np.clip(work.astype(np.float64) * gain, 0, 255).astype(np.uint8)
        # The gain amplifies sensor noise too; smooth it before the
        # per-pixel chroma test.
        work = cv2.GaussianBlur(work, LOW_LIGHT_BLUR_KERNEL, 0)
        ycrcb = cv2.cvtColor(work, cv2.COLOR_BGR2YCrCb)
    luma = ycrcb[..., 0].astype(np.float64)
    cr, cb = ycrcb[..., 1], ycrcb[..., 2]
    skin = (
        (cr >= SKIN_CR_RANGE[0]) & (cr <= SKIN_CR_RANGE[1])
        & (cb >= SKIN_CB_RANGE[0]) & (cb <= SKIN_CB_RANGE[1])
        & (luma >= SKIN_MIN_LUMA)
    )
    skin = cv2.morphologyEx(skin.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
    if not skin.any():
        return []
    work_h, work_w = skin.shape

    # Eye candidates: non-skin components enclosed by skin.
    count, labels, stats, centroids = cv2.connectedComponentsWithStats((~skin).astype(np.uint8), connectivity=8)
    max_area = EYE_MAX_AREA_FRACTION * work_h * work_w
    candidates: list[tuple[float, float, float, int]] = []
    for label in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[label])
        if x == 0 or y == 0 or x + w >= work_w or y + h >= work_h:
            continue  # touches the frame: background, not a hole
        if not EYE_MIN_AREA_PX <= area <= max_area:
            continue
        candidates.append((float(centroids[label][0]), float(centroids[label][1]), float(area), label))
    candidates = sorted(candidates, key=lambda c: -c[2])[:EYE_MAX_CANDIDATES]

    yy, xx = np.mgrid[0:work_h, 0:work_w]
    found: list[tuple[float, tuple[int, int, int, int]]] = []
    for i, first in enumerate(candidates):
        for second in candidates[i + 1:]:
            left, right = sorted((first, second))
            distance = right[0] - left[0]
            if distance < EYE_MIN_DISTANCE_PX or abs(right[1] - left[1]) > EYE_MAX_TILT * distance:
                continue
            if max(left[2], right[2]) > EYE_MAX_AREA_RATIO * min(left[2], right[2]):
                continue
            box = _face_box_from_eyes(left, right, distance)
            score = _face_box_score(box, (left, right), skin, luma, labels, xx, yy)
            if score is not None:
                found.append((score, box))
    regions: list[FaceRegion] = []
    for score, box in sorted(found, key=lambda item: -item[0]):
        if any(_overlap(box, (r.x * scale, r.y * scale, r.width * scale, r.height * scale)) > 0.3 for r in regions):
            continue
        bx, by, bw, bh = box
        fx, fy = max(0, int(round(bx / scale))), max(0, int(round(by / scale)))
        fw, fh = int(round(bw / scale)), int(round(bh / scale))
        regions.append(FaceRegion(
            x=fx, y=fy, width=fw, height=fh,
            landmarks=_estimate_landmarks(fx, fy, fw, fh),
            confidence=HEURISTIC_CONFIDENCE,
            landmarks_source="box-ratio-estimate",
            detector=HEURISTIC_DETECTOR,
        ))
    return regions


def _face_box_from_eyes(left: tuple[float, ...], right: tuple[float, ...], distance: float) -> tuple[int, int, int, int]:
    mid_x, mid_y = (left[0] + right[0]) / 2, (left[1] + right[1]) / 2
    width = FACE_WIDTH_PER_EYE_DISTANCE * distance
    height = FACE_HEIGHT_PER_EYE_DISTANCE * distance
    return int(round(mid_x - width / 2)), int(round(mid_y - FACE_TOP_ABOVE_EYES * distance)), int(round(width)), int(round(height))


def _face_box_score(
    box: tuple[int, int, int, int],
    eyes: tuple[tuple[float, ...], tuple[float, ...]],
    skin: Any,
    luma: Any,
    labels: Any,
    xx: Any,
    yy: Any,
) -> float | None:
    """Skin share of the box's inscribed ellipse, or None when not a face."""
    import numpy as np

    x, y, w, h = box
    if w <= 0 or h <= 0:
        return None
    ellipse = (((xx - (x + w / 2)) / (w / 2)) ** 2 + ((yy - (y + h / 2)) / (h / 2)) ** 2) <= 1.0
    full_area = np.pi * (w / 2) * (h / 2)
    inside = int(ellipse.sum())
    if inside < FACE_MIN_INSIDE_FRACTION * full_area:
        return None
    eye_pixels = (labels == eyes[0][3]) | (labels == eyes[1][3])
    if eye_pixels.sum() > EYE_MAX_BOX_FRACTION * w * h * 2:
        return None
    # The eyes must be dark against the face's own skin.
    face_skin = ellipse & skin
    if not face_skin.any():
        return None
    skin_luma = float(np.median(luma[face_skin]))
    if float(luma[eye_pixels].mean()) >= EYE_DARK_RATIO * skin_luma:
        return None
    skin_share = float(face_skin.sum()) / inside
    return skin_share if skin_share >= FACE_MIN_SKIN_FRACTION else None


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Intersection over the smaller box."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    smaller = min(aw * ah, bw * bh)
    return (ix * iy) / smaller if smaller > 0 else 0.0


# Landmark sources that carry measured geometry (vs the box-ratio estimate).
MEASURED_LANDMARK_SOURCES = {"mediapipe-facelandmarker", "mediapipe-facemesh"}

# FaceLandmarker (Tasks API) model asset. Bundled under models/ or pointed at
# with DEEPFAKE_LENS_FACE_LANDMARKER; absent → the legacy FaceMesh path.
_FACE_LANDMARKER_ENV = "DEEPFAKE_LENS_FACE_LANDMARKER"
_FACE_LANDMARKER_ASSET = default_models_dir() / "face_landmarker.task"


def _facelandmarker_model_path() -> Path | None:
    """Resolve the FaceLandmarker .task asset, if one is provisioned."""
    override = os.environ.get(_FACE_LANDMARKER_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    return _FACE_LANDMARKER_ASSET if _FACE_LANDMARKER_ASSET.is_file() else None


def _face_landmarks(image, x: int, y: int, w: int, h: int) -> tuple[list[tuple[int, int]], str]:
    """Return (anchor points, source label) for one detected face box.

    Prefers measured MediaPipe anchors (Tasks-API FaceLandmarker first, then
    legacy FaceMesh); falls back to box-ratio estimates so every FaceRegion
    is honest about where its landmarks came from.
    """
    measured = _facelandmarker_landmarks(image, x, y, w, h)
    if measured is not None:
        return measured, "mediapipe-facelandmarker"
    measured = _mediapipe_landmarks(image, x, y, w, h)
    if measured is not None:
        return measured, "mediapipe-facemesh"
    return _estimate_landmarks(x, y, w, h), "box-ratio-estimate"


# MediaPipe FaceMesh canonical indices for the four anchors, averaged where
# a small neighbourhood is more stable than a single point.
_MEDIAPIPE_ANCHOR_INDICES = (
    (33, 133),    # left eye: inner+outer canthus midpoint
    (263, 362),   # right eye: inner+outer canthus midpoint
    (1,),         # nose tip
    (13, 14),     # mouth center: upper/lower inner lip midpoint
)


def _mediapipe_landmarks(image, x: int, y: int, w: int, h: int) -> list[tuple[int, int]] | None:
    """Measure eye/nose/mouth anchors with MediaPipe FaceMesh.

    Returns None when the optional ``face_mediapipe`` extra is not
    installed, when FaceMesh finds no face in the crop, or when the
    measurement fails — callers then fall back to the labelled box-ratio
    estimate.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        return None

    crop = image[max(0, y) : y + h, max(0, x) : x + w]
    if crop.size == 0:
        return None

    try:
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            min_detection_confidence=0.5,
        )
        try:
            result = face_mesh.process(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        finally:
            face_mesh.close()
    except Exception:
        return None

    if not result.multi_face_landmarks:
        return None

    mesh = result.multi_face_landmarks[0].landmark
    crop_h, crop_w = crop.shape[:2]

    def anchor(indices: tuple[int, ...]) -> tuple[int, int]:
        px = sum(mesh[i].x for i in indices) / len(indices) * crop_w
        py = sum(mesh[i].y for i in indices) / len(indices) * crop_h
        return (x + int(px), y + int(py))

    return [anchor(indices) for indices in _MEDIAPIPE_ANCHOR_INDICES]


def _facelandmarker_landmarks(image, x: int, y: int, w: int, h: int) -> list[tuple[int, int]] | None:
    """Measure anchors with the Tasks-API FaceLandmarker.

    This is the supported path on mediapipe >= 0.10.30 / 1.x, where the
    legacy ``mp.solutions`` API no longer exists. It needs the
    ``face_landmarker.task`` model asset (see ``_facelandmarker_model_path``);
    returns None when the asset or the Tasks API is absent, or when no face
    is found in the crop.
    """
    model_path = _facelandmarker_model_path()
    if model_path is None:
        return None
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
    except (ImportError, AttributeError):
        return None

    try:
        crop = image[max(0, y) : y + h, max(0, x) : x + w]
        if crop.size == 0:
            return None
        options = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
        )
        with mp_vision.FaceLandmarker.create_from_options(options) as landmarker:
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    except Exception:
        return None

    if not result.face_landmarks:
        return None

    # Same canonical 478-point topology as FaceMesh — the shared anchor
    # indices apply unchanged.
    mesh = result.face_landmarks[0]
    crop_h, crop_w = crop.shape[:2]

    def anchor(indices: tuple[int, ...]) -> tuple[int, int]:
        px = sum(mesh[i].x for i in indices) / len(indices) * crop_w
        py = sum(mesh[i].y for i in indices) / len(indices) * crop_h
        return (x + int(px), y + int(py))

    return [anchor(indices) for indices in _MEDIAPIPE_ANCHOR_INDICES]


def _estimate_landmarks(x: int, y: int, w: int, h: int) -> list[tuple[int, int]]:
    """Assume eye/nose/mouth positions from the detection box proportions.

    These are NOT measured landmarks; they only anchor the eye-region
    sampling used by reflection analysis. Geometry checks must not be built
    on them because every derived relation is a constant of the box shape.
    Callers must label them ``landmarks_source="box-ratio-estimate"``.
    """
    # Left eye, right eye, nose tip, mouth center
    left_eye = (x + int(w * 0.35), y + int(h * 0.35))
    right_eye = (x + int(w * 0.65), y + int(h * 0.35))
    nose_tip = (x + int(w * 0.5), y + int(h * 0.55))
    mouth_center = (x + int(w * 0.5), y + int(h * 0.75))
    return [left_eye, right_eye, nose_tip, mouth_center]


def _boundary_blending(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Analyze face boundary blending artifacts."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    # Extract face region
    x, y, w, h = face.x, face.y, face.width, face.height
    face_region = image[y:y+h, x:x+w]
    if face_region.size == 0:
        return None

    # Convert to grayscale
    gray = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY)

    # Edge detection at boundary
    edges = cv2.Canny(gray, 50, 150)

    # Check boundary edge density
    boundary_width = max(1, int(min(w, h) * 0.1))
    boundary_mask = np.zeros_like(edges)
    boundary_mask[:boundary_width, :] = 1
    boundary_mask[-boundary_width:, :] = 1
    boundary_mask[:, :boundary_width] = 1
    boundary_mask[:, -boundary_width:] = 1

    boundary_edges = np.sum(edges * boundary_mask)
    total_edges = np.sum(edges)

    if total_edges > 0:
        boundary_ratio = boundary_edges / total_edges
        if boundary_ratio > 0.7:
            return FaceEvidenceSignal(
                "경계 블렌딩 의심",
                f"얼굴 경계에서 에지 비율({boundary_ratio:.2f})이 높아 합성 경계일 수 있습니다.",
                18,
            )

    return None


def _reflection_analysis(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Analyze eye reflection patterns."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    if len(face.landmarks) < 2:
        return None

    left_eye, right_eye = face.landmarks[0], face.landmarks[1]

    # Extract eye regions
    eye_size = max(5, int(face.width * 0.08))

    def get_eye_region(center):
        cx, cy = center
        x1 = max(0, cx - eye_size)
        y1 = max(0, cy - eye_size)
        x2 = min(image.shape[1], cx + eye_size)
        y2 = min(image.shape[0], cy + eye_size)
        return image[y1:y2, x1:x2]

    left_region = get_eye_region(left_eye)
    right_region = get_eye_region(right_eye)

    if left_region.size == 0 or right_region.size == 0:
        return None

    # Compare brightness patterns
    left_brightness = np.mean(cv2.cvtColor(left_region, cv2.COLOR_BGR2GRAY))
    right_brightness = np.mean(cv2.cvtColor(right_region, cv2.COLOR_BGR2GRAY))

    brightness_diff = abs(left_brightness - right_brightness)
    if brightness_diff > 40:
        return FaceEvidenceSignal(
            "반사 패턴 불일치",
            f"양쪽 눈 밝기 차이({brightness_diff:.1f})가 커서 인위적 합성일 수 있습니다.",
            15,
        )

    return None


def _color_temperature(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Analyze color temperature consistency between face and surrounding."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    x, y, w, h = face.x, face.y, face.width, face.height

    # Face region
    face_region = image[y:y+h, x:x+w]
    if face_region.size == 0:
        return None

    # Surrounding region (neck/hair area)
    surround_y = min(image.shape[0], y + h)
    surround_h = min(int(h * 0.3), image.shape[0] - surround_y)
    if surround_h <= 0:
        return None

    surround_region = image[surround_y:surround_y+surround_h, x:x+w]
    if surround_region.size == 0:
        return None

    # Compare color histograms
    face_hsv = cv2.cvtColor(face_region, cv2.COLOR_BGR2HSV)
    surround_hsv = cv2.cvtColor(surround_region, cv2.COLOR_BGR2HSV)

    face_hue_mean = np.mean(face_hsv[:, :, 0])
    surround_hue_mean = np.mean(surround_hsv[:, :, 0])

    # OpenCV hue is circular in [0, 179]; compare on the circle so hue 5 vs
    # hue 175 (the same red family) does not read as a 170-unit mismatch.
    hue_diff = abs(float(face_hue_mean) - float(surround_hue_mean))
    circular_hue_diff = min(hue_diff, 180.0 - hue_diff)
    if circular_hue_diff > 15:
        return FaceEvidenceSignal(
            "색온도 불일치",
            f"얼굴과 주변 영역 색상 차이({circular_hue_diff:.1f})가 커서 합성일 수 있습니다.",
            12,
        )

    return None


def _multi_face_consistency(faces: list[FaceRegion], image) -> FaceEvidenceSignal | None:
    """Check consistency across multiple faces."""
    if len(faces) < 2:
        return None

    # Check if faces have similar sizes (could indicate pasted faces)
    sizes = [f.width * f.height for f in faces]
    if len(sizes) > 1:
        size_cv = (max(sizes) - min(sizes)) / max(1, max(sizes))
        if size_cv < 0.1 and len(faces) > 2:
            return FaceEvidenceSignal(
                "다중 얼굴 크기 균일",
                f"여러 얼굴의 크기가 비정상적으로 균일합니다 ({len(faces)}개 얼굴).",
                8,
            )

    return None


def _classify_manipulation_type(signals: list[FaceEvidenceSignal]) -> str:
    """Classify the type of manipulation based on signals."""
    signal_titles = {s.title for s in signals}

    if "경계 블렌딩 의심" in signal_titles:
        return "face_swap"
    if "반사 패턴 불일치" in signal_titles:
        return "reenactment"
    if "색온도 불일치" in signal_titles:
        return "face_swap"
    if "다중 얼굴 크기 균일" in signal_titles:
        return "face_paste"
    return "unknown"


def _calculate_confidence(score: int, face_count: int, signal_count: int) -> str:
    """Calculate confidence level."""
    if score >= 67 and face_count >= 1 and signal_count >= 2:
        return "high"
    if score >= 35 and signal_count >= 1:
        return "medium"
    return "low"


def _lighting_consistency(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Compare the face's shading direction against the scene's light slope.

    A pasted/AI-composited face often carries illumination from a different
    photo. The face is approximately convex, so its left/right and up/down
    luminance asymmetry approximates the light azimuth; the surrounding
    ring's least-squares luminance ramp estimates the scene's overall
    light direction. A large angular mismatch is a screening signal, not
    proof — mixed lighting and flat studio light both suppress it.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    x, y, w, h = face.x, face.y, face.width, face.height
    if w < 40 or h < 40:
        return None
    face_region = image[y : y + h, x : x + w]
    if face_region.size == 0:
        return None
    gray_face = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY).astype(float)

    # Face shading asymmetry — left vs right and top vs bottom halves.
    mid_x, mid_y = w // 2, h // 2
    fx = float(gray_face[:, :mid_x].mean() - gray_face[:, mid_x:].mean())
    fy = float(gray_face[:mid_y, :].mean() - gray_face[mid_y:, :].mean())
    face_strength = float(np.hypot(fx, fy))
    if face_strength < 4.0:
        return None  # evenly lit face — no direction to compare

    # Scene light slope — fit I = a*x + b*y + c over the ring around the
    # face (1.8x box, face excluded).
    img_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(float)
    x0, y0 = max(0, int(x - 0.4 * w)), max(0, int(y - 0.4 * h))
    x1, y1 = min(image.shape[1], int(x + 1.4 * w)), min(image.shape[0], int(y + 1.4 * h))
    if (x1 - x0) < w or (y1 - y0) < h:
        return None
    coords, values = [], []
    ys, xs = np.mgrid[y0:y1, x0:x1]
    inside = (xs >= x) & (xs < x + w) & (ys >= y) & (ys < y + h)
    ring_x, ring_y = xs[~inside], ys[~inside]
    ring_v = img_gray[y0:y1, x0:x1][~inside]
    if len(ring_v) < 200 or float(ring_v.std()) < 3.0:
        return None  # featureless background — no scene direction
    a_mat = np.stack([ring_x, ring_y, np.ones_like(ring_x)], axis=1)
    coef, *_ = np.linalg.lstsq(a_mat, ring_v, rcond=None)
    gx, gy = float(coef[0]), float(coef[1])
    scene_strength = float(np.hypot(gx, gy))
    if scene_strength < 0.02:
        return None  # uniform background — no direction to compare

    # Sign convention: face asymmetry fx>0 means LEFT darker → light from
    # right, i.e. direction +x. The scene ramp gradient points toward
    # increasing brightness = light direction.
    face_dir = np.arctan2(-fy, -fx)  # darker side → opposite of light
    scene_dir = np.arctan2(gy, gx)
    diff = abs(face_dir - scene_dir)
    diff = min(diff, 2 * np.pi - diff)
    degrees = float(np.degrees(diff))
    if degrees >= 60:
        return FaceEvidenceSignal(
            "조명 방향 불일치",
            f"얼굴 음영 방향과 장면 조명 방향이 약 {degrees:.0f}도 어긋납니다 — 다른 조명 환경의 합성 얼굴 후보.",
            14,
        )
    return None
