from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checkpoint_integrity import load_torch_state
from .model_cache import (  # noqa: F401 — re-exported for existing callers/tests
    _ModelLRU,
    _model_cache_limit,
    _release_cached_model,
    clear_all_model_caches,
)
from .result_types import ExternalModelAnalysis  # noqa: F401 — re-exported
from .model_runtimes import (  # noqa: F401 — dispatch targets + shared caches
    _AASIST_RUNNERS,
    _AIDE_RUNNERS,
    _CLIP_BACKBONES,
    _CLIP_HEADS,
    _HF_AUDIO_MODELS,
    _HF_IMAGE_MODELS,
    _HF_TEXT_MODELS,
    _PPL_MODELS,
    _TORCHVISION_MODELS,
    _checkpoint_hint,
    _checkpoint_path,
    _confidence_for_score,
    _normalize_score,
    _preprocess_image,
    _profile_limitations,
    _run_aasist,
    _run_aide,
    _run_binoculars,
    _run_causal_lm_ppl,
    _run_clip_linear,
    _run_hf_audio_classifier,
    _run_hf_image_classifier,
    _run_hf_text_classifier,
    _run_onnx,
    _run_onnx_audio,
    _run_torchscript,
    _run_torchvision,
    _runtime_install_hint,
    _score_from_outputs,
    _score_from_score_map,
    _score_from_sidecar,
)

# Profile-set marker: a JSON file that lists member profiles/directories so a
# single --model-path can drive several detectors at once.
PROFILE_SET_TYPE = "deepfake-lens-profile-set-v1"
# Scores farther apart than this count as member disagreement.
AGREEMENT_SPREAD = 20
_MAX_PROFILE_DEPTH = 4

# Runtimes are bound to a media modality: image runtimes consume pixels via
# PIL/numpy; audio runtimes consume waveforms. Profiles may declare an
# explicit "modality" field; otherwise it is inferred from the runtime.
IMAGE_RUNTIMES = {"onnx", "torchscript", "aide", "clip-linear", "torchvision", "hf-image-classifier"}
HUB_RUNTIMES = {"hf-text-classifier", "hf-image-classifier", "hf-audio-classifier", "causal-lm-ppl", "binoculars"}
AUDIO_RUNTIMES = {"aasist", "hf-audio-classifier", "onnx-audio"}
TEXT_RUNTIMES = {"hf-text-classifier", "causal-lm-ppl", "binoculars"}
# "video-frames" samples frames with cv2 and scores each with a nested image
# runtime profile ("inner") — it reuses image checkpoints, so it needs no
# weights of its own.
VIDEO_RUNTIMES = {"video-frames"}
ALL_RUNTIMES = IMAGE_RUNTIMES | AUDIO_RUNTIMES | TEXT_RUNTIMES | VIDEO_RUNTIMES


def analyze_external_model(
    path: Path | str,
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
    *,
    modality: str = "image",
) -> ExternalModelAnalysis | None:
    """Score one media file with external model profile(s).

    ``model_path`` may be a single profile/checkpoint file, a directory of
    ``*.json`` profiles, a profile-set JSON (``type: deepfake-lens-profile-set-v1``
    with a ``profiles`` list), or a list of any of those. With more than one
    profile every member runs and the result reports per-model scores plus an
    agreement signal; members that cannot run degrade to ``available=False``
    entries rather than failing the whole analysis.

    ``modality`` (``"image"`` or ``"audio"``) selects which profiles apply:
    a profile matches when it declares a matching ``"modality"`` field, when
    its runtime is bound to that modality, or when it is modality-agnostic
    (score maps, sidecars, placeholders). A directory or list may therefore
    mix image and audio profiles — each file only runs the ones that fit.
    """
    if model_path is None:
        return None

    media_path = Path(path)
    sources = _model_sources(model_path)
    if not sources:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=str(model_path),
            detail=f"no model profiles found under {model_path}",
            limitations=["Point --model-path at a profile JSON, a profile set, or a directory containing *-runtime.json profiles."],
        )
    sources = [source for source in sources if _profile_matches_modality(source, modality)]
    if not sources:
        # Profiles exist but none apply to this file's modality — same as no
        # profile at all, not an error worth a row in the report.
        return None

    results = [(source, _analyze_profile_file(media_path, source, depth=0, modality=modality)) for source in sources]
    if len(results) == 1:
        return results[0][1]
    hangul_ratio = _media_hangul_ratio(media_path) if modality == "text" else 0.0
    jpeg_qf, min_side = _image_quality_context(media_path) if modality == "image" else (None, None)
    return _aggregate_profile_results(results, hangul_ratio=hangul_ratio, jpeg_qf=jpeg_qf, min_side=min_side)


def _model_sources(model_path: Path | str | list[Path | str] | tuple[Path | str, ...]) -> list[Path]:
    """Expand a model-path argument into the profile files it names."""
    if isinstance(model_path, (list, tuple)):
        sources: list[Path] = []
        for entry in model_path:
            sources.extend(_model_sources(entry))
        return sources
    candidate = Path(model_path)
    if candidate.is_dir():
        # *.model.json sidecars are per-file score payloads, not profiles.
        return sorted(path for path in candidate.glob("*.json") if not path.name.endswith(".model.json"))
    return [candidate]


def _profile_modality(source: Path) -> str:
    """Return 'image', 'audio', or 'any' for a profile source.

    Bare checkpoint files are image-bound (they run through the generic
    image runtimes). JSON profiles declare 'modality' explicitly or are
    inferred from their runtime; score maps, sidecars, placeholders, and
    profile sets match any modality — set members are filtered again when
    the set is expanded.
    """
    suffix = source.suffix.lower()
    if suffix in {".pt", ".pth", ".onnx", ".torchscript"}:
        return "image"
    if suffix != ".json":
        return "any"
    try:
        profile = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "any"  # unreadable profiles surface their own error later
    if not isinstance(profile, dict):
        return "any"
    declared = str(profile.get("modality") or "").lower()
    if declared:
        return declared
    if profile.get("type") == PROFILE_SET_TYPE:
        return "any"
    runtime = str(profile.get("runtime") or "").lower()
    if runtime in AUDIO_RUNTIMES:
        return "audio"
    if runtime in IMAGE_RUNTIMES:
        return "image"
    if runtime in TEXT_RUNTIMES:
        return "text"
    if runtime in VIDEO_RUNTIMES:
        return "video"
    return "any"


def _profile_matches_modality(source: Path, modality: str) -> bool:
    profile_modality = _profile_modality(source)
    return profile_modality == "any" or profile_modality == modality


def _analyze_profile_file(media_path: Path, model_file: Path, *, depth: int, modality: str = "image") -> ExternalModelAnalysis:
    if model_file.suffix.lower() in {".pt", ".pth", ".onnx", ".torchscript"}:
        runtime = "onnx" if model_file.suffix.lower() == ".onnx" else "torchscript"
        # Resolve first — _checkpoint_path joins relative paths onto base_dir,
        # so a relative model_file would be doubled into a bogus path.
        return _score_from_runtime_profile({"runtime": runtime, "checkpoint": str(model_file.resolve()), "name": model_file.name}, media_path, base_dir=model_file.parent)

    try:
        profile = json.loads(model_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=str(model_file),
            detail=f"model profile could not be read: {exc}",
        )
    if not isinstance(profile, dict):
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=str(model_file),
            detail="model profile is not a JSON object.",
        )

    model_name = str(profile.get("name") or profile.get("model") or model_file.name)

    if profile.get("type") == PROFILE_SET_TYPE:
        return _analyze_profile_set(media_path, model_file, profile, model_name=model_name, depth=depth, modality=modality)

    if profile.get("supported") is False:
        reason = str(profile.get("reason") or "this profile is a documented placeholder and is not wired to a runnable runtime.")
        fetch_hint = str(profile.get("fetch") or "").strip()
        limitations = _profile_limitations(profile)
        if fetch_hint:
            limitations = [fetch_hint, *limitations]
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{model_name}: {reason}",
            limitations=limitations,
        )

    score = _score_from_score_map(profile, media_path)
    if score is None:
        score = _score_from_sidecar(profile, media_path)
    if score is None:
        runtime_result = _score_from_runtime_profile(profile, media_path, base_dir=model_file.parent)
        if runtime_result is not None:
            return runtime_result
    if score is None:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail="model profile loaded, but no per-file score was available.",
            limitations=["Use score_map entries or a .model.json sidecar for external detector scores."],
        )

    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=f"external model profile supplied score={score}.",
    )


def _analyze_profile_set(media_path: Path, model_file: Path, profile: dict[str, object], *, model_name: str, depth: int, modality: str = "image") -> ExternalModelAnalysis:
    """Run every member listed in a profile-set JSON and aggregate."""
    members = profile.get("profiles")
    if depth >= _MAX_PROFILE_DEPTH or not isinstance(members, list) or not members:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail="profile set has no usable member profiles (empty list or nested too deep).",
        )
    sources: list[Path] = []
    for member in members:
        raw = Path(str(member))
        resolved = raw if raw.is_absolute() else model_file.parent / raw
        sources.extend(_model_sources(resolved))
    sources = [source for source in sources if _profile_matches_modality(source, modality)]
    if not sources:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"profile set {model_file.name} resolved to no member profiles.",
        )
    hangul_ratio = _media_hangul_ratio(media_path) if modality == "text" else 0.0
    jpeg_qf, min_side = _image_quality_context(media_path) if modality == "image" else (None, None)
    results = [(source, _analyze_profile_file(media_path, source, depth=depth + 1, modality=modality)) for source in sources]
    return _aggregate_profile_results(results, model_name=model_name, hangul_ratio=hangul_ratio, jpeg_qf=jpeg_qf, min_side=min_side)


def _profile_ensemble_weight(source: Path) -> float:
    """Reliability weight a member profile declares for ensemble fusion.

    Profiles may set ``ensemble_weight`` (0.05-4.0, default 1.0). Detectors
    with measured narrow coverage or high false-positive rates on the eval
    corpus should carry a lower weight so one noisy member cannot dominate
    or dilute the fused score.
    """
    try:
        profile = json.loads(source.read_text(encoding="utf-8"))
        weight = float(profile.get("ensemble_weight", 1.0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 1.0
    return min(4.0, max(0.05, weight))


def _profile_degraded_weight(source: Path) -> float | None:
    """Weight to use when the input is a low-quality JPEG (``degraded_weight``).

    Members measured fragile under recompression (e.g. AIDE collapsing
    91->6 at q50) declare a lower weight so a re-encoded fake cannot
    rely on the fragile member's destroyed signal — and a re-encoded
    real photo cannot be dragged by its compression-artifact noise.
    """
    try:
        profile = json.loads(source.read_text(encoding="utf-8"))
        weight = profile.get("degraded_weight")
        if weight is None:
            runtime = profile.get("runtime", "")
            if runtime in {"torchvision", "aide"}:
                base = _profile_ensemble_weight(source)
                return min(4.0, max(0.05, base * 0.35))
            return None
        return min(4.0, max(0.05, float(weight)))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _profile_trained_languages(source: Path) -> list[str]:
    """Languages a member was actually trained/validated on.

    Profiles may set ``trained_languages`` (e.g. ``["en"]``). Members
    without the field are treated as language-agnostic — only members
    that declare an explicit list can be down-weighted for out-of-domain
    input.
    """
    try:
        profile = json.loads(source.read_text(encoding="utf-8"))
        langs = profile.get("trained_languages")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []
    return [str(lang).lower() for lang in langs] if isinstance(langs, list) else []


def _image_quality_context(media_path: Path) -> tuple[float | None, int | None]:
    """JPEG quality estimate + smallest side for ensemble weight gating."""
    from .jpegq import estimate_jpeg_quality, image_min_side

    try:
        return estimate_jpeg_quality(media_path), image_min_side(media_path)
    except Exception:  # noqa: BLE001 - quality estimation is advisory
        return None, None


def _media_hangul_ratio(media_path: Path) -> float:
    """Hangul share of letters in a text scan item (0 for non-text files)."""
    try:
        raw = media_path.read_bytes()[:262_144]
    except OSError:
        return 0.0
    text = raw.decode("utf-8", errors="ignore")
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    hangul = sum(1 for ch in letters if "\uac00" <= ch <= "\ud7a3")
    return hangul / len(letters)


def _aggregate_profile_results(results: list[tuple[Path, ExternalModelAnalysis]], *, model_name: str | None = None, hangul_ratio: float = 0.0, jpeg_qf: float | None = None, min_side: int | None = None) -> ExternalModelAnalysis:
    """Merge per-profile results into one analysis with an agreement signal."""
    scored = [(source, result) for source, result in results if result.available]
    downweighted: list[str] = []
    degraded_adjusted: list[str] = []
    weights: list[float] = []
    kept: list[tuple[Path, ExternalModelAnalysis]] = []
    for source, result in scored:
        weight = _profile_ensemble_weight(source)
        # Language gate: a member that declares an English-only training
        # list scores ~98 AI on human Korean (measured) — its output on
        # Korean-dominant text is noise, so it is excluded, not downweighted.
        langs = _profile_trained_languages(source)
        if langs and hangul_ratio > 0.3 and "ko" not in langs:
            downweighted.append(source.stem)
            continue
        if jpeg_qf is not None and jpeg_qf < 75:
            degraded = _profile_degraded_weight(source)
            if degraded is not None and degraded < weight:
                weight = degraded
                degraded_adjusted.append(source.stem)
        kept.append((source, result))
        weights.append(weight)
    total_weight = sum(weights)
    score = int(round(sum(result.score * weight for (_, result), weight in zip(kept, weights)) / total_weight)) if kept else 0
    scores = [result.score for _, result in kept]
    scores = [result.score for _, result in scored]
    spread = max(scores) - min(scores) if len(scores) > 1 else 0
    agreement = "n/a" if len(scores) < 2 else ("high" if spread <= AGREEMENT_SPREAD else "low")

    detail = f"{len(scored)}/{len(results)} model profiles produced scores"
    if scores:
        detail += f"; aggregate score={score} (weighted mean of members)"
    if len(scores) > 1:
        detail += f"; member spread={spread} (agreement: {agreement})"

    limitations: list[str] = []
    if downweighted:
        limitations.append(
            f"English-only members {', '.join(downweighted)} were excluded on Korean-dominant text "
            f"(hangul ratio {hangul_ratio:.0%}) — measured false-positive on human Korean was 98/100."
        )
    if degraded_adjusted:
        limitations.append(
            f"JPEG quality ~{jpeg_qf:.0f}: recompression-fragile members {', '.join(degraded_adjusted)} "
            "down-weighted (measured collapse on re-encoded inputs — see RECOMPRESSION_EVAL.md)."
        )
    if min_side is not None and min_side < 128:
        limitations.append(
            f"Input is {min_side}px on its smallest side — below every member's native resolution; "
            "measured AUROC on 32x32 thumbnails is ~0.5 (chance). Treat scores as unreliable."
        )
    for _, result in results:
        for item in result.limitations:
            if item not in limitations:
                limitations.append(item)
    limitations.append("Aggregated external scores are a weighted mean of available members — a prioritization signal, not a truth label.")
    if agreement == "low":
        limitations.append(f"Model zoo members disagree (spread {spread} points); weigh metadata/provenance signals before triage.")

    if not kept:
        confidence = "unavailable"
    elif agreement == "low":
        confidence = "low"
    else:
        confidence = _confidence_for_score(score)

    return ExternalModelAnalysis(
        available=bool(scored),
        score=score,
        confidence=confidence,
        model=model_name or f"model-zoo ({len(results)} profiles)",
        detail=detail,
        limitations=limitations,
        models=[
            {
                "profile": str(source),
                "model": result.model,
                "available": result.available,
                "score": result.score,
                "confidence": result.confidence,
                "detail": result.detail,
            }
            for source, result in results
        ],
    )


def load_model_threshold(model_path: Path | str | None) -> int | None:
    if model_path is None:
        return None
    model_file = Path(model_path)
    if model_file.suffix.lower() != ".json":
        return None
    try:
        profile = json.loads(model_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = profile.get("threshold")
    if isinstance(value, (int, float)):
        return max(0, min(100, int(round(float(value)))))
    return None


def _score_from_runtime_profile(profile: dict[str, object], media_path: Path, *, base_dir: Path) -> ExternalModelAnalysis | None:
    runtime = str(profile.get("runtime") or "").lower()
    if runtime not in ALL_RUNTIMES:
        return None
    checkpoint = _checkpoint_path(profile, base_dir=base_dir)
    model_name = str(profile.get("name") or profile.get("model") or checkpoint.name)
    profile_limitations = _profile_limitations(profile)
    # Face-conditioned members (faceswap/reenactment detectors trained on
    # face crops) produce meaningless scores on face-free images — skip
    # rather than letting them false-flag landscapes and documents.
    # 'crop_faces' goes further: each detected face region is cropped and
    # scored individually, then aggregated ('crop_aggregate', default max).
    if runtime in IMAGE_RUNTIMES and (profile.get("requires_face") or profile.get("crop_faces")):
        crops = _face_crops(media_path, margin=float(profile.get("crop_margin", 0.25) or 0.25))
        if not crops:
            label = "crop_faces" if profile.get("crop_faces") else "requires_face"
            return ExternalModelAnalysis(
                available=False,
                score=0,
                confidence="skipped",
                model=model_name,
                detail=f"{label}: no face region detected — face-manipulation member not applicable.",
                limitations=profile_limitations,
            )
        if profile.get("crop_faces"):
            return _score_face_crops(crops, profile, base_dir=base_dir, model_name=model_name, profile_limitations=profile_limitations)
    # Hub-resolved runtimes (hf-text-classifier) name a model id, not a local
    # file; video-frames carries no checkpoint of its own (its inner image
    # profile does) — the exists() gate below does not apply to them.
    if runtime not in HUB_RUNTIMES and runtime not in VIDEO_RUNTIMES and not checkpoint.exists():
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{runtime} checkpoint was not found: {checkpoint}",
            limitations=[*_checkpoint_hint(runtime), *profile_limitations],
        )
    try:
        if runtime == "aide":
            values = _run_aide(checkpoint, media_path)
        elif runtime == "clip-linear":
            values = _run_clip_linear(checkpoint, media_path, profile)
        elif runtime == "aasist":
            values = _run_aasist(checkpoint, media_path, profile)
        elif runtime == "hf-text-classifier":
            values = _run_hf_text_classifier(media_path, profile)
        elif runtime == "hf-image-classifier":
            values = _run_hf_image_classifier(media_path, profile)
        elif runtime == "hf-audio-classifier":
            values = _run_hf_audio_classifier(media_path, profile)
        elif runtime == "onnx-audio":
            values = _run_onnx_audio(checkpoint, media_path, profile)
        elif runtime == "causal-lm-ppl":
            return _run_causal_lm_ppl(media_path, profile, model_name=model_name)
        elif runtime == "binoculars":
            return _run_binoculars(media_path, profile, model_name=model_name)
        elif runtime == "video-frames":
            return _run_video_frames(media_path, profile, model_name=model_name, base_dir=base_dir)
        else:
            array = _preprocess_image(media_path, profile)
            if runtime == "onnx":
                values = _run_onnx(checkpoint, array, profile)
            elif runtime == "torchvision":
                values = _run_torchvision(checkpoint, array, profile)
            else:
                values = _run_torchscript(checkpoint, array)
        score = _score_from_outputs(values, profile)
    except ImportError as exc:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{runtime} runtime is optional and not installed: {exc}",
            limitations=[_runtime_install_hint(runtime), *profile_limitations],
        )
    except Exception as exc:  # noqa: BLE001 - model runtimes fail in many library-specific ways.
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{runtime} inference failed: {exc}",
            limitations=["Verify input_size, mean/std, input_name, score_index, and checkpoint compatibility.", *profile_limitations],
        )
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=f"{runtime} runtime supplied score={score}.",
        limitations=list(profile_limitations),
    )


def _has_face(media_path: Path) -> bool:
    """True only when at least one face region is detected in the image.

    Face-conditioned members are meaningless off-face, so an unchecked or
    unreadable image fails closed (skip) — consistent with the project's
    precision-over-recall posture.
    """
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    from .face import _detect_faces, _imread_unicode

    image = _imread_unicode(media_path)
    if image is None:
        return False

    return bool(_detect_faces(image))


def _face_crops(media_path: Path, *, margin: float = 0.25) -> list:
    """Detected face regions cropped from the image (BGR ndarrays).

    Each box is expanded by ``margin`` (25% default — manipulation cues
    live at the blend boundary, so a bare face oval loses context) and
    clipped to the image. Empty when the image is unreadable or no face
    is detected — callers treat that as the requires_face gate.
    """
    try:
        import cv2  # noqa: F401
    except ImportError:
        return []
    from .face import _detect_faces, _imread_unicode

    image = _imread_unicode(media_path)
    if image is None:
        return []

    img_h, img_w = image.shape[:2]
    crops = []
    for region in _detect_faces(image):
        mx = int(region.width * margin)
        my = int(region.height * margin)
        x0 = max(0, region.x - mx)
        y0 = max(0, region.y - my)
        x1 = min(img_w, region.x + region.width + mx)
        y1 = min(img_h, region.y + region.height + my)
        if x1 - x0 >= 16 and y1 - y0 >= 16:
            crops.append(image[y0:y1, x0:x1])
    return crops


def _score_face_crops(crops: list, profile: dict[str, object], *, base_dir: Path, model_name: str, profile_limitations: list[str]) -> ExternalModelAnalysis:
    """Score each face crop through the profile's runtime and aggregate.

    ``crop_aggregate`` picks the rule: ``max`` (default — one manipulated
    face flags the image) or ``mean``. Per-crop results are reported under
    ``models[]`` so the viewer can show which face drove the score.
    """
    import tempfile

    import cv2

    inner = {key: value for key, value in profile.items() if key not in {"crop_faces", "requires_face", "crop_aggregate", "crop_margin"}}
    aggregate = str(profile.get("crop_aggregate") or "max").lower()
    results: list[ExternalModelAnalysis] = []
    with tempfile.TemporaryDirectory(prefix="dfl-faces-") as tmp_dir:
        for index, crop in enumerate(crops):
            crop_path = Path(tmp_dir) / f"face_{index}.png"
            cv2.imwrite(str(crop_path), crop)
            result = _score_from_runtime_profile(inner, crop_path, base_dir=base_dir)
            if result is not None:
                results.append(result)
    scored = [result.score for result in results if result.available]
    if not scored:
        cause = results[0].detail if results else "no crop produced a result"
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"crop_faces: {len(crops)} face crop(s) detected but inference failed ({cause}).",
            limitations=profile_limitations,
        )
    if aggregate == "mean":
        score = int(round(sum(scored) / len(scored)))
    else:
        score = max(scored)
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=f"crop_faces scored {len(scored)}/{len(crops)} face crop(s); aggregate={aggregate} -> {score}.",
        limitations=list(profile_limitations),
        models=[
            {"crop": index, "available": result.available, "score": result.score, "detail": result.detail}
            for index, result in enumerate(results)
        ],
    )


def _run_video_frames(
    media_path: Path,
    profile: dict[str, object],
    *,
    model_name: str,
    base_dir: Path,
) -> ExternalModelAnalysis:
    """Score a video by running a nested image-runtime profile per frame.

    The profile carries an ``inner`` field: a complete image-runtime profile
    (runtime + checkpoint + preprocessing fields). ``frames`` (default 8)
    evenly spaced frames are decoded with cv2, written to temporary PNGs, and
    each is scored through the normal profile path — so AIDE, UnivFD,
    CNNDetection, or any ONNX/TorchScript image detector works on video
    without a video-specific checkpoint.

    The aggregate score is the mean of per-frame scores; ``models[]`` lists
    each frame's result, and a high spread across frames is reported as a
    limitation (temporal inconsistency is itself a manipulation cue). This
    is frame-level screening — it is NOT a lip-sync or temporal-model
    detector (those need dedicated video checkpoints; see the registry).
    """
    import tempfile

    inner = profile.get("inner") or profile.get("frame_profile")
    if not isinstance(inner, dict):
        raise RuntimeError("video-frames profile needs an 'inner' image-runtime profile object")
    inner_runtime = str(inner.get("runtime") or "").lower()
    if inner_runtime in VIDEO_RUNTIMES or not inner_runtime:
        raise RuntimeError("video-frames 'inner' profile must name an image runtime (onnx/torchscript/aide/clip-linear/torchvision)")
    frame_target = max(1, int(profile.get("frames", 8) or 8))
    cv2 = importlib.import_module("cv2")

    scored_frames: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="dfl-frames-") as tmp_dir:
        frame_paths = _extract_sampled_frames(cv2, media_path, Path(tmp_dir), frame_target)
        if not frame_paths:
            raise RuntimeError(f"no frames could be decoded from {media_path}")
        for index, frame_path in enumerate(frame_paths):
            result = _score_from_runtime_profile(inner, frame_path, base_dir=base_dir)
            scored_frames.append(
                {
                    "frame": index,
                    "path": str(frame_path),
                    "available": bool(result and result.available),
                    "score": result.score if result else 0,
                    "detail": result.detail if result else "no result",
                }
            )

    available = [entry["score"] for entry in scored_frames if entry["available"]]
    if not available:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"video-frames decoded {len(frame_paths)} frames but the inner runtime produced no scores.",
            limitations=_profile_limitations(profile),
            models=scored_frames,
        )
    score = int(round(sum(available) / len(available)))
    spread = max(available) - min(available) if len(available) > 1 else 0
    detail = f"video-frames runtime scored {len(available)}/{len(frame_paths)} frames; mean={score}"
    if len(available) > 1:
        detail += f"; frame spread={spread}"
    limitations = list(_profile_limitations(profile))
    limitations.append(
        "Frame-level image detection, not a temporal/lip-sync model — frame scores are averaged and inter-frame consistency is not modeled."
    )
    if spread > AGREEMENT_SPREAD:
        limitations.append(f"Frame scores disagree by {spread} points — possible temporal inconsistency or borderline frames.")
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=detail + ".",
        limitations=limitations,
        models=scored_frames,
    )


def _extract_sampled_frames(cv2, media_path: Path, out_dir: Path, count: int) -> list[Path]:
    """Decode ``count`` evenly spaced frames to PNG files in ``out_dir``."""
    capture = cv2.VideoCapture(str(media_path))
    if not capture.isOpened():
        raise RuntimeError(f"video could not be opened: {media_path}")
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            # Streaming/unknown-length sources: take the first frames.
            indices = list(range(count))
        else:
            indices = sorted({min(total - 1, int(i * total / count)) for i in range(count)})
        frames: list[Path] = []
        wanted = iter(indices)
        target = next(wanted, None)
        current = 0
        while target is not None:
            ok, frame = capture.read()
            if not ok:
                break
            if current == target:
                out_path = out_dir / f"frame-{current:05d}.png"
                cv2.imwrite(str(out_path), frame)
                if out_path.is_file():
                    frames.append(out_path)
                target = next(wanted, None)
            current += 1
        return frames
    finally:
        capture.release()
