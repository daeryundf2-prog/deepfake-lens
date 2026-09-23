"""Face-track temporal consistency measurement for video.

Per-frame image detectors score every frame in isolation; a face swap
that looks clean on any single frame can still betray itself *between*
frames — the swapped identity jitters, the pasted box drifts against
the tracked head motion, and landmark geometry flickers.

This module measures three track-level signals on sampled frames:

- ``embedding_drift``: cosine distance between consecutive face-crop
  feature vectors (EfficientNet-B0 penultimate features via torchvision,
  when available; else a grayscale-histogram proxy). A real tracked face
  drifts smoothly; a per-frame pasted identity produces larger and more
  erratic jumps.
- ``landmark_jitter``: normalized displacement of corresponding measured
  landmarks between consecutive frames, divided by face-box size so the
  metric is resolution-independent. Requires measured landmarks
  (``mediapipe-facelandmarker``); box-ratio estimates are rejected.
- ``box_smoothness``: frame-to-frame change in face-box area and centre
  position, normalized by box size.

Measurement only: the numbers are reported with a weak heuristic
verdict. This is NOT a trained temporal model — thresholds are
calibrated on a small synthetic corpus and recorded in
experiments/FACESWAP_EVALUATION.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
_DEFAULT_FPS = 4.0  # frames sampled per second
_MAX_FRAMES = 96
_MIN_TRACK = 8  # frames with a usable face required for a verdict


@dataclass(frozen=True)
class FaceTrackAnalysis:
    available: bool
    score: int  # 0-100 suspicion of temporal face inconsistency
    verdict: str
    frames_sampled: int
    frames_with_face: int
    embedding_drift_mean: float | None
    embedding_drift_max: float | None
    landmark_jitter_mean: float | None
    box_area_delta_mean: float | None
    embedding_kind: str | None
    limitations: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_face_track(
    path: Path | str, *, fps: float = _DEFAULT_FPS, max_frames: int = _MAX_FRAMES
) -> FaceTrackAnalysis:
    """Measure temporal face-track consistency on a video file."""
    video_path = Path(path)
    limitations = [
        "휴리스틱 시간-일관성 측정이며 학습된 temporal 모델이 아닙니다 — 스크리닝 신호입니다.",
        "작거나 측면 얼굴, 장면 전환, 강한 손떨림은 실사에서도 드리프트를 키웁니다.",
    ]
    try:
        import cv2
        import numpy as np
    except ImportError:
        return _unavailable(limitations, "cv2/numpy가 없어 얼굴 트랙 분석을 건너뜁니다.")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return _unavailable(limitations, "영상을 디코딩할 수 없습니다.")
    try:
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, int(round(src_fps / fps)))
        crops: list = []
        landmarks_seq: list = []
        boxes: list = []
        idx, taken = 0, 0
        while taken < max_frames:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                face = _largest_face(rgb)
                if face is not None:
                    crops.append(_crop_face(rgb, face))
                    landmarks_seq.append(face.get("landmarks"))
                    boxes.append(face["box"])
                else:
                    crops.append(None)
                    landmarks_seq.append(None)
                    boxes.append(None)
                taken += 1
            idx += 1
    finally:
        cap.release()

    usable = [(c, l, b) for c, l, b in zip(crops, landmarks_seq, boxes) if c is not None]
    if len(usable) < _MIN_TRACK:
        return _unavailable(
            limitations,
            f"얼굴이 검출된 프레임이 {len(usable)}개로 부족합니다(최소 {_MIN_TRACK}).",
        )

    embeddings, emb_kind = _embed_sequence([c for c, _, _ in usable], limitations)
    drift = _consecutive_cosine(embeddings) if embeddings is not None else None
    jitter = _landmark_jitter([l for _, l, _ in usable], [b for _, _, b in usable])
    area_delta = _box_smoothness([b for _, _, b in usable])

    score = _score(drift, jitter, area_delta)
    verdict = _verdict(score)
    return FaceTrackAnalysis(
        True,
        score,
        verdict,
        taken,
        len(usable),
        _r(drift["mean"]) if drift else None,
        _r(drift["max"]) if drift else None,
        _r(jitter) if jitter is not None else None,
        _r(area_delta) if area_delta is not None else None,
        emb_kind,
        limitations,
    )


def _largest_face(rgb) -> dict | None:
    """Largest measured-landmark face in one frame, or None."""
    from .face import _detect_faces

    best = None
    for region in _detect_faces(rgb):
        if region.landmarks_source not in ("mediapipe-facelandmarker", "mediapipe-facemesh"):
            continue
        if best is None or region.width * region.height > best["box"][2] * best["box"][3]:
            best = {
                "box": (region.x, region.y, region.width, region.height),
                "landmarks": region.landmarks,
            }
    return best


def _crop_face(rgb, face: dict):
    import cv2

    x, y, w, h = face["box"]
    crop = rgb[y : y + h, x : x + w]
    if crop.size == 0:
        return None
    return cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA)


_EMBEDDER = None
_EMBEDDER_FAILED = False


def _embed_sequence(crops, limitations: list[str]):
    """Per-crop embedding via the local SBI backbone; histogram fallback.

    An untrained network produces near-orthogonal random features
    (cosine distance ~1.0 for every pair), so the backbone must carry
    real weights — the bundled ``sbi-effnet-b0.pth`` was trained on
    face crops and is loaded with its classifier head dropped.
    """
    global _EMBEDDER, _EMBEDDER_FAILED
    try:
        import numpy as np
        import torch
        import torchvision

        if not _EMBEDDER_FAILED and _EMBEDDER is None:
            weights = (
                Path(__file__).resolve().parent.parent / "models" / "sbi-effnet-b0.pth"
            )
            model = torchvision.models.efficientnet_b0(weights=None)
            sd = torch.load(weights, map_location="cpu")
            if isinstance(sd, dict) and "state_dict" in sd:
                sd = sd["state_dict"]
            sd = {k: v for k, v in sd.items() if k.startswith("features.")}
            model.features.load_state_dict(
                {k[len("features.") :]: v for k, v in sd.items()}
            )
            _EMBEDDER = model.features.eval()
        if _EMBEDDER is None:
            raise RuntimeError("embedder unavailable")
        embs = []
        for crop in crops:
            t = torch.from_numpy(crop.transpose(2, 0, 1)).float().unsqueeze(0) / 255.0
            t = torch.nn.functional.interpolate(t, size=(224, 224), mode="bilinear")
            with torch.no_grad():
                f = _EMBEDDER(t).mean(dim=(2, 3)).flatten()
            embs.append(f.numpy())
        return np.stack(embs), "sbi-effnet-b0-features"
    except Exception:
        _EMBEDDER_FAILED = True
        limitations.append(
            "torch/torchvision 또는 SBI 백본 미가용 — 임베딩이 그레이 히스토그램 프록시로 대체됐습니다."
        )
        import numpy as np
        import cv2

        embs = []
        for crop in crops:
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
            embs.append(cv2.calcHist([gray], [0], None, [64], [0, 256]).flatten())
        return np.stack(embs).astype(float), "gray-histogram-proxy"


def _consecutive_cosine(embs) -> dict:
    import numpy as np

    norm = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-8)
    d = 1.0 - (norm[:-1] * norm[1:]).sum(axis=1)
    return {"mean": float(d.mean()), "max": float(d.max()), "std": float(d.std())}


def _landmark_jitter(seq, boxes) -> float | None:
    import numpy as np

    deltas = []
    for prev, cur, box in zip(seq, seq[1:], boxes[1:]):
        if not prev or not cur or len(prev) != len(cur):
            continue
        _, _, w, h = box
        scale = max(1.0, (w * h) ** 0.5)
        p = np.asarray(prev, dtype=float)
        c = np.asarray(cur, dtype=float)
        deltas.append(float(np.linalg.norm(c - p, axis=1).mean() / scale))
    return float(np.mean(deltas)) if deltas else None


def _box_smoothness(boxes) -> float | None:
    deltas = []
    for prev, cur in zip(boxes, boxes[1:]):
        pa = prev[2] * prev[3]
        ca = cur[2] * cur[3]
        if pa <= 0:
            continue
        deltas.append(abs(ca - pa) / pa)
    return float(sum(deltas) / len(deltas)) if deltas else None


def _score(drift, jitter, area_delta) -> int:
    """Weak heuristic: thresholds from the synthetic-corpus calibration."""
    points = 0
    if drift and drift["mean"] > 0.25:
        points += 35
    elif drift and drift["mean"] > 0.12:
        points += 15
    if drift and drift["max"] > 0.5:
        points += 15
    if jitter is not None and jitter > 0.08:
        points += 25
    elif jitter is not None and jitter > 0.04:
        points += 10
    if area_delta is not None and area_delta > 0.15:
        points += 15
    return min(points, 100)


def _verdict(score: int) -> str:
    if score >= 60:
        return "얼굴 트랙 시간-불일치가 큽니다 — 프레임 단위 합성/스왑 후보 (사람 검토 필요)."
    if score >= 30:
        return "얼굴 트랙 드리프트가 경계 영역입니다 — 재촬영/압축과 구분이 필요합니다."
    return "얼굴 트랙이 시간적으로 매끄럽습니다 — 이 신호만으로는 조작을 배제할 수 없습니다."


def _r(v: float) -> float:
    return round(v, 4)


def _unavailable(limitations: list[str], message: str) -> FaceTrackAnalysis:
    return FaceTrackAnalysis(
        False, 0, message, 0, 0, None, None, None, None, None, limitations
    )
