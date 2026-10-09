from __future__ import annotations

import importlib
import importlib.util
import json
import logging
from dataclasses import replace
from pathlib import Path

from .native_path import native_safe_path
from .checkpoint_integrity import _expected_sha256, load_torch_state  # noqa: F401 — load_torch_state re-exported
from .model_pins import PIN_FIELD, PinError, verify_pin
from .model_cache import (  # noqa: F401 — re-exported for existing callers/tests
    _ModelLRU,
    _model_cache_limit,
    _release_cached_model,
    clear_all_model_caches,
)
from .result_types import ExternalModelAnalysis, register_model_display_name  # noqa: F401 — ExternalModelAnalysis re-exported
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
from .native_stderr import quiet_native_stderr

logger = logging.getLogger(__name__)

# ExternalModelAnalysis.confidence for a member that *failed* (raised, or
# its integrity check did not match) as opposed to one that was skipped
# (missing optional runtime, missing checkpoint, unsupported profile).
# core.model_coverage maps it to a ``failed`` coverage entry, which makes
# the verdict undetermined (G1, fail-closed).
FAILED_CONFIDENCE = "failed"


# Shown with a pin failure: how to provision a pin (G9).
PIN_HINT = "가중치 고정 필요: 'deepfake-lens vendor-weights pin <프로필>'로 체크포인트 sha256 또는 허브 커밋 revision을 프로필 pin에 기록하세요."


def profile_display_name(profile: dict[str, object], fallback: str) -> str:
    """The profile's Korean ``display_name`` (R4), else ``fallback``."""
    value = profile.get("display_name")
    return str(value).strip() if isinstance(value, str) and value.strip() else fallback


def _failure_detail(context: str, exc: BaseException) -> str:
    """"<ExcType>: <message> (<context>)" — the coverage reason format."""
    from .error_text import failure_reason

    return f"{failure_reason(exc)} ({context})"  # N1: path-scrubbed


# Profile-set marker: a JSON file that lists member profiles/directories so a
# single --model-path can drive several detectors at once.
PROFILE_SET_TYPE = "deepfake-lens-profile-set-v1"
# Scores farther apart than this count as member disagreement.
AGREEMENT_SPREAD = 20
# Display labels of the zoo agreement value in model_analysis.detail (R4).
AGREEMENT_LABELS = {"high": "높음", "low": "낮음", "n/a": "해당 없음"}
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
            detail=f"모델 프로필을 찾을 수 없습니다: {model_path}",
            limitations=["--model-path에는 프로필 JSON, 프로필 세트 또는 *-runtime.json 프로필이 있는 디렉터리를 지정하십시오."],
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
        # Only *-runtime.json files are adapter profiles — thresholds.json,
        # *.model.json score sidecars, and other manifests in a models dir
        # must never be loaded as model configs.
        return sorted(candidate.glob("*-runtime.json"))
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
    except (OSError, ValueError):  # P4: ValueError covers JSONDecodeError and UnicodeDecodeError
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
        resolved = model_file.resolve()
        bare_profile: dict[str, object] = {"runtime": runtime, "checkpoint": str(resolved), "name": model_file.name}
        # A bare checkpoint has no profile to carry a pin; its sha256
        # sidecar (or the DEEPFAKE_LENS_CHECKPOINT_SHA256 env pin) is the
        # pin. Without one the load is refused like any unpinned profile.
        bare_profile[PIN_FIELD] = {"sha256": _bare_checkpoint_sha256(resolved)}
        return _score_from_runtime_profile(bare_profile, media_path, base_dir=model_file.parent)

    try:
        profile = json.loads(model_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # P4: + UnicodeDecodeError
        # A configured profile that cannot be read is an operator error —
        # it fails the check rather than quietly dropping the detector.
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE,
            model=str(model_file),
            detail=_failure_detail("모델 프로필을 읽을 수 없습니다", exc),
        )
    if not isinstance(profile, dict):
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE,
            model=str(model_file),
            detail="ValueError: 모델 프로필이 JSON 객체가 아닙니다.",
        )

    model_name = str(profile.get("name") or profile.get("model") or model_file.name)
    # R4: the Korean display name goes into every examiner-facing string;
    # model_name stays the identifier in ``model``/``profile`` fields.
    display = profile_display_name(profile, model_name)
    register_model_display_name(model_name, display)

    if profile.get("type") == PROFILE_SET_TYPE:
        return replace(
            _analyze_profile_set(media_path, model_file, profile, model_name=model_name, depth=depth, modality=modality),
            display_name=display,
        )

    if profile.get("supported") is False:
        # S7: a gated-off profile never ran — its gate reason (detail →
        # coverage "모델 실행 불가: …") is the whole story. Its download/fetch
        # hints and its run-time caveats describe a model that did not run
        # and are not added to the row's limitations.
        reason = str(profile.get("reason") or "이 프로필은 문서화용 자리표시자이며 실행 가능한 런타임에 연결되어 있지 않습니다.")
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{display}: {reason}",
            limitations=[],
            display_name=display,
        )

    score = _score_from_score_map(profile, media_path)
    if score is None:
        score = _score_from_sidecar(profile, media_path)
    if score is None:
        runtime_result = _score_from_runtime_profile(profile, media_path, base_dir=model_file.parent)
        if runtime_result is not None:
            return replace(runtime_result, display_name=display)
    if score is None:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail="모델 프로필은 읽었지만 이 파일의 점수가 없습니다.",
            limitations=["외부 탐지기 점수는 score_map 항목이나 .model.json 사이드카로 제공하십시오."],
            display_name=display,
        )

    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=f"외부 모델 프로필이 점수 {score}를 제공했습니다.",
        display_name=display,
    )


def _bare_checkpoint_sha256(checkpoint: Path) -> str:
    try:
        return _expected_sha256(checkpoint) or ""
    except RuntimeError:
        # A malformed sidecar is no pin at all; the load is refused as
        # unpinned rather than trusted.
        logger.warning("malformed sha256 sidecar for %s", checkpoint)
        return ""


def _analyze_profile_set(media_path: Path, model_file: Path, profile: dict[str, object], *, model_name: str, depth: int, modality: str = "image") -> ExternalModelAnalysis:
    """Run every member listed in a profile-set JSON and aggregate."""
    members = profile.get("profiles")
    if depth >= _MAX_PROFILE_DEPTH or not isinstance(members, list) or not members:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail="프로필 세트에 사용할 수 있는 멤버 프로필이 없습니다(빈 목록이거나 중첩이 너무 깊음).",
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
            detail=f"프로필 세트 {model_file.name}에서 멤버 프로필을 찾지 못했습니다.",
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
    except Exception:
        # Advisory only (ensemble weight gating): log and continue without
        # the quality context rather than failing the model check.
        logger.exception("image quality estimation failed for %s", media_path)
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
    # G33: spread/agreement are computed over the members that actually
    # contributed — a language-gated member's out-of-domain score must not
    # create (or mask) disagreement. A duplicate assignment over `scored`
    # used to overwrite this line.
    scores = [result.score for _, result in kept]
    spread = max(scores) - min(scores) if len(scores) > 1 else 0
    agreement = "n/a" if len(scores) < 2 else ("high" if spread <= AGREEMENT_SPREAD else "low")

    detail = f"모델 프로필 {len(results)}개 중 {len(kept)}개가 점수를 냈습니다"
    if scores:
        detail += f"; 집계 점수 {score}(멤버 가중 평균)"
    if len(scores) > 1:
        detail += f"; 멤버 간 편차 {spread}(일치도: {AGREEMENT_LABELS.get(agreement, agreement)})"

    limitations: list[str] = []
    if downweighted:
        limitations.append(
            f"영어 전용 멤버 {', '.join(downweighted)}는 한국어가 주인 글(한글 비율 {hangul_ratio:.0%})에서 제외했습니다 "
            "— 사람이 쓴 한국어 글에서 오탐이 관찰되었습니다(소규모 측정, 미검증)."
        )
    if degraded_adjusted:
        limitations.append(
            f"JPEG 품질 약 {jpeg_qf:.0f}: 재압축에 약한 멤버 {', '.join(degraded_adjusted)}의 가중치를 낮췄습니다 "
            "(재인코딩 입력에서 점수 붕괴 관찰 — experiments/RECOMPRESSION_EVAL.md, 미검증)."
        )
    if min_side is not None and min_side < 128:
        limitations.append(
            f"입력의 짧은 변이 {min_side}px로 모든 멤버의 기본 해상도보다 작습니다 "
            "— 작은 썸네일에서는 점수가 우연 수준이므로 신뢰할 수 없습니다."
        )
    for _, result in results:
        for item in result.limitations:
            if item not in limitations:
                limitations.append(item)
    if kept:
        # S7: only an aggregate that exists needs the caveat; with no member
        # run the coverage entries ("모델 실행 불가: …") say why.
        limitations.append("외부 모델 집계 점수는 실행된 멤버의 가중 평균인 보정 전 원점수이며 진위 판정이 아닙니다.")
    if agreement == "low":
        limitations.append(f"모델 멤버 간 점수가 엇갈립니다(편차 {spread}점) — 메타데이터·출처 신호를 먼저 검토하십시오.")

    if not kept:
        confidence = "unavailable"
    elif agreement == "low":
        confidence = "low"
    else:
        confidence = _confidence_for_score(score)

    gated = set(downweighted)
    return ExternalModelAnalysis(
        # Only contributing members make the aggregate available: when every
        # scoring member was language-gated there is no usable score.
        available=bool(kept),
        score=score,
        confidence=confidence,
        model=model_name or f"model-zoo ({len(results)} profiles)",
        detail=detail,
        limitations=limitations,
        models=[_member_entry(source, result, gated=source.stem in gated, hangul_ratio=hangul_ratio) for source, result in results],
    )


def _member_entry(source: Path, result: ExternalModelAnalysis, *, gated: bool, hangul_ratio: float) -> dict[str, object]:
    """Per-member ``models[]`` row; a language-gated member is reported as
    skipped (its score did not enter the aggregate), not as a ran check."""
    if gated:
        return {
            "profile": str(source),
            "model": result.model,
            "display_name": result.label,
            "available": False,
            "score": result.score,
            "confidence": "skipped",
            "detail": f"언어 게이트 제외: 한국어 비중 {hangul_ratio:.0%} — 학습 언어에 한국어 없음",
        }
    return {
        "profile": str(source),
        "model": result.model,
        "display_name": result.label,
        "available": result.available,
        "score": result.score,
        "confidence": result.confidence,
        "detail": result.detail,
    }


def _profile_threshold_entry(profile: dict[str, object]) -> tuple[str, float] | None:
    """``(calibration_id, threshold / 100)`` of one runtime profile, or None (G8).

    The profile's ``threshold`` (0-100, the cut its measurement fixed) is the
    rule-4 threshold for probabilities carrying its ``calibration_id``. A
    profile without both — every phase-0 profile — contributes nothing, so
    rule 4 cannot fire for that calibration id (no 0.5 default).
    """
    calibration_id = profile.get("calibration_id")
    threshold = profile.get("threshold")
    if not isinstance(calibration_id, str) or not calibration_id.strip():
        return None
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= float(threshold) <= 100:
        return None
    return calibration_id.strip(), float(threshold) / 100.0


def profile_probability_thresholds(
    model_path: Path | str | list[Path | str] | tuple[Path | str, ...] | None,
    *,
    _depth: int = 0,
) -> dict[str, float]:
    """``{calibration_id: threshold / 100}`` over every profile ``model_path`` names (G8).

    Profile sets contribute their members. Unreadable files and profiles
    without a calibration id or a numeric 0-100 ``threshold`` are skipped.
    This is what :func:`core.build_classification_result` hands to
    ``decision.decide`` as ``thresholds`` — the decision never invents one.
    """
    if model_path is None or _depth >= _MAX_PROFILE_DEPTH:
        return {}
    out: dict[str, float] = {}
    for source in _model_sources(model_path):
        if source.suffix.lower() != ".json":
            continue
        try:
            profile = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(profile, dict):
            continue
        if profile.get("type") == PROFILE_SET_TYPE:
            members = profile.get("profiles")
            for member in members if isinstance(members, list) else []:
                raw = Path(str(member))
                out.update(profile_probability_thresholds(raw if raw.is_absolute() else source.parent / raw, _depth=_depth + 1))
            continue
        entry = _profile_threshold_entry(profile)
        if entry is not None:
            out[entry[0]] = entry[1]
    return out


def load_model_threshold(model_path: Path | str | None) -> int | None:
    if model_path is None:
        return None
    model_file = Path(model_path)
    if model_file.suffix.lower() != ".json":
        return None
    try:
        profile = json.loads(model_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # P4: ValueError covers JSONDecodeError and UnicodeDecodeError
        return None
    value = profile.get("threshold")
    if isinstance(value, (int, float)):
        return max(0, min(100, int(round(float(value)))))
    return None


def _pin_failure(profile: dict[str, object], checkpoint: Path | None, *, model_name: str, profile_limitations: list[str]) -> ExternalModelAnalysis | None:
    """Verify the profile's weight pin (G9); a refusal is a *failed* check.

    Runs on every load — there is no "verified once" cache, so a checkpoint
    swapped between two scans is caught on the second one (QA-SYS-2).
    """
    try:
        verify_pin(profile, checkpoint)
    except PinError as exc:
        logger.warning("model load refused (%s): %s", model_name, exc.reason)
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE,
            model=model_name,
            detail=exc.reason,
            limitations=[PIN_HINT, *profile_limitations],
        )
    except OSError as exc:
        logger.exception("checkpoint could not be hashed: %s", checkpoint)
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE,
            model=model_name,
            detail=_failure_detail("체크포인트 무결성 검사 실패", exc),
            limitations=list(profile_limitations),
        )
    return None


def _score_from_runtime_profile(profile: dict[str, object], media_path: Path, *, base_dir: Path, pin_verified: bool = False) -> ExternalModelAnalysis | None:
    """Score one file through a runtime profile.

    ``pin_verified`` is set only by this module's own fan-out (per face
    crop, per video frame) after the caller verified the pin once for the
    whole file; every top-level load verifies it.
    """
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
                detail=f"{label}: 얼굴 영역이 검출되지 않아 얼굴 조작 멤버를 적용하지 않았습니다.",
                limitations=profile_limitations,
            )
    # Hub-resolved runtimes (hf-text-classifier) name a model id, not a local
    # file; video-frames carries no checkpoint of its own (its inner image
    # profile does) — the exists() gate below does not apply to them.
    if runtime not in HUB_RUNTIMES and runtime not in VIDEO_RUNTIMES and not checkpoint.exists():
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{runtime} 체크포인트를 찾을 수 없습니다: {checkpoint.name}",
            limitations=[*_checkpoint_hint(runtime), *profile_limitations],
        )
    # G9: no weight loads without a matching pin (sha256 for a local
    # checkpoint, commit revision for a hub model). video-frames verifies
    # its inner profile's pin once in _run_video_frames.
    if not pin_verified and runtime not in VIDEO_RUNTIMES:
        refused = _pin_failure(profile, checkpoint if runtime not in HUB_RUNTIMES else None, model_name=model_name, profile_limitations=profile_limitations)
        if refused is not None:
            return refused
    if runtime in IMAGE_RUNTIMES and profile.get("crop_faces"):
        return _score_face_crops(crops, profile, base_dir=base_dir, model_name=model_name, profile_limitations=profile_limitations)
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
    except PinError as exc:
        # Second line of defence inside a runtime (e.g. an empty hub revision).
        logger.warning("model load refused (%s): %s", model_name, exc.reason)
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE,
            model=model_name,
            detail=exc.reason,
            limitations=[PIN_HINT, *profile_limitations],
        )
    except ImportError as exc:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"의존성 부재: {exc.name or exc} ({runtime} 런타임은 선택 설치 항목이며 설치되어 있지 않습니다)",
            limitations=[_runtime_install_hint(runtime), *profile_limitations],
        )
    except Exception as exc:
        # G1: an inference crash is a *failed* check (verdict undetermined),
        # never "score 0 / unavailable" that reads like a clean result.
        logger.exception("%s inference failed for %s", runtime, media_path)
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE,
            model=model_name,
            detail=_failure_detail(f"{runtime} 추론 실패", exc),
            limitations=["input_size, mean/std, input_name, score_index와 체크포인트 호환성을 확인하십시오.", *profile_limitations],
        )
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=f"{runtime} 런타임이 점수 {score}를 제공했습니다.",
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
            result = _score_from_runtime_profile(inner, crop_path, base_dir=base_dir, pin_verified=True)
            if result is not None:
                results.append(result)
    scored = [result.score for result in results if result.available]
    if not scored:
        cause = results[0].detail if results else "결과를 낸 얼굴 크롭이 없습니다"
        crashed = any(result.confidence == FAILED_CONFIDENCE for result in results)
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE if crashed else "unavailable",
            model=model_name,
            detail=f"crop_faces: 얼굴 크롭 {len(crops)}개를 검출했지만 추론에 실패했습니다({cause}).",
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
        detail=f"crop_faces: 얼굴 크롭 {len(crops)}개 중 {len(scored)}개 채점, 집계({aggregate}) {score}.",
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
        raise RuntimeError("video-frames 프로필에는 'inner' 이미지 런타임 프로필 객체가 필요합니다")
    inner_runtime = str(inner.get("runtime") or "").lower()
    if inner_runtime in VIDEO_RUNTIMES or not inner_runtime:
        raise RuntimeError("video-frames의 'inner' 프로필은 이미지 런타임(onnx/torchscript/aide/clip-linear/torchvision)을 지정해야 합니다")
    frame_target = max(1, int(profile.get("frames", 8) or 8))
    cv2 = importlib.import_module("cv2")
    # The outer profile's pin describes the inner weights (G9); verify it
    # once here instead of re-hashing the checkpoint for every frame.
    if PIN_FIELD not in inner and PIN_FIELD in profile:
        inner = {**inner, PIN_FIELD: profile[PIN_FIELD]}
    inner_checkpoint = _checkpoint_path(inner, base_dir=base_dir)
    inner_has_weights = inner_runtime in HUB_RUNTIMES or inner_checkpoint.exists()
    if inner_has_weights:
        refused = _pin_failure(
            inner,
            inner_checkpoint if inner_runtime not in HUB_RUNTIMES else None,
            model_name=model_name,
            profile_limitations=_profile_limitations(profile),
        )
        if refused is not None:
            return refused

    scored_frames: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="dfl-frames-") as tmp_dir:
        frame_paths = _extract_sampled_frames(cv2, media_path, Path(tmp_dir), frame_target)
        if not frame_paths:
            raise RuntimeError(f"영상에서 프레임을 디코딩하지 못했습니다: {media_path}")
        for index, frame_path in enumerate(frame_paths):
            result = _score_from_runtime_profile(inner, frame_path, base_dir=base_dir, pin_verified=inner_has_weights)
            scored_frames.append(
                {
                    "frame": index,
                    "path": str(frame_path),
                    "failed": bool(result and result.confidence == FAILED_CONFIDENCE),
                    "available": bool(result and result.available),
                    "score": result.score if result else 0,
                    "detail": result.detail if result else "결과 없음",
                }
            )

    available = [entry["score"] for entry in scored_frames if entry["available"]]
    if not available:
        crashed = any(entry["failed"] for entry in scored_frames)
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence=FAILED_CONFIDENCE if crashed else "unavailable",
            model=model_name,
            detail=f"video-frames: 프레임 {len(frame_paths)}개를 디코딩했지만 내부 런타임이 점수를 내지 못했습니다.",
            limitations=_profile_limitations(profile),
            models=scored_frames,
        )
    score = int(round(sum(available) / len(available)))
    spread = max(available) - min(available) if len(available) > 1 else 0
    detail = f"video-frames 런타임: 프레임 {len(frame_paths)}개 중 {len(available)}개 채점, 평균 {score}"
    if len(available) > 1:
        detail += f"; 프레임 간 편차 {spread}"
    limitations = list(_profile_limitations(profile))
    limitations.append(
        "프레임 단위 이미지 탐지이며 시간축/립싱크 모델이 아닙니다 — 프레임 점수를 평균하며 프레임 간 일관성은 모델링하지 않습니다."
    )
    if spread > AGREEMENT_SPREAD:
        limitations.append(f"프레임 점수가 {spread}점 엇갈립니다 — 시간축 불일치이거나 경계선상의 프레임일 수 있습니다.")
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=detail + ".",
        limitations=limitations,
        models=scored_frames,
    )


@quiet_native_stderr  # G14: decoder chatter (fd 2) goes to the log, not the console
def _extract_sampled_frames(cv2, media_path: Path, out_dir: Path, count: int) -> list[Path]:
    """Decode ``count`` evenly spaced frames to PNG files in ``out_dir``."""
    with native_safe_path(media_path) as native_video:  # R12-1: never a non-ASCII name to cv2
        capture = cv2.VideoCapture(native_video)
        if not capture.isOpened():
            raise RuntimeError(f"영상을 열 수 없습니다: {media_path}")
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
