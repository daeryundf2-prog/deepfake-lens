from __future__ import annotations

import importlib
import importlib.util
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

# Profile-set marker: a JSON file that lists member profiles/directories so a
# single --model-path can drive several detectors at once.
PROFILE_SET_TYPE = "deepfake-lens-profile-set-v1"
# Scores farther apart than this count as member disagreement.
AGREEMENT_SPREAD = 20
_MAX_PROFILE_DEPTH = 4

# Runtimes are bound to a media modality: image runtimes consume pixels via
# PIL/numpy; audio runtimes consume waveforms. Profiles may declare an
# explicit "modality" field; otherwise it is inferred from the runtime.
IMAGE_RUNTIMES = {"onnx", "torchscript", "aide", "clip-linear", "torchvision"}
AUDIO_RUNTIMES = {"aasist"}
TEXT_RUNTIMES = {"hf-text-classifier", "causal-lm-ppl", "binoculars"}
# "video-frames" samples frames with cv2 and scores each with a nested image
# runtime profile ("inner") — it reuses image checkpoints, so it needs no
# weights of its own.
VIDEO_RUNTIMES = {"video-frames"}
ALL_RUNTIMES = IMAGE_RUNTIMES | AUDIO_RUNTIMES | TEXT_RUNTIMES | VIDEO_RUNTIMES


@dataclass(frozen=True)
class ExternalModelAnalysis:
    available: bool
    score: int
    confidence: str
    model: str
    detail: str
    limitations: list[str] = field(default_factory=list)
    # Per-member results when several profiles ran (model zoo / profile set).
    models: list[dict[str, object]] = field(default_factory=list)


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
    return _aggregate_profile_results(results, hangul_ratio=hangul_ratio)


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
    results = [(source, _analyze_profile_file(media_path, source, depth=depth + 1, modality=modality)) for source in sources]
    return _aggregate_profile_results(results, model_name=model_name, hangul_ratio=hangul_ratio)


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


def _aggregate_profile_results(results: list[tuple[Path, ExternalModelAnalysis]], *, model_name: str | None = None, hangul_ratio: float = 0.0) -> ExternalModelAnalysis:
    """Merge per-profile results into one analysis with an agreement signal."""
    scored = [(source, result) for source, result in results if result.available]
    downweighted: list[str] = []
    weights: list[float] = []
    for source, _result in scored:
        weight = _profile_ensemble_weight(source)
        # Language gate: a member that declares an English-only training
        # list must not dominate a Korean-dominant document — the measured
        # false-positive on human Korean text was 98/100.
        langs = _profile_trained_languages(source)
        if langs and hangul_ratio > 0.3 and "ko" not in langs:
            weight *= 0.25
            downweighted.append(source.stem)
        weights.append(weight)
    total_weight = sum(weights)
    score = int(round(sum(result.score * weight for (_, result), weight in zip(scored, weights)) / total_weight)) if scored else 0
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
            f"English-only members {', '.join(downweighted)} were down-weighted 4x on Korean-dominant text "
            f"(hangul ratio {hangul_ratio:.0%}) — measured false-positive on human Korean was 98/100."
        )
    for _, result in results:
        for item in result.limitations:
            if item not in limitations:
                limitations.append(item)
    limitations.append("Aggregated external scores are a weighted mean of available members — a prioritization signal, not a truth label.")
    if agreement == "low":
        limitations.append(f"Model zoo members disagree (spread {spread} points); weigh metadata/provenance signals before triage.")

    if not scored:
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
    # Hub-resolved runtimes (hf-text-classifier) name a model id, not a local
    # file; video-frames carries no checkpoint of its own (its inner image
    # profile does) — the exists() gate below does not apply to them.
    if runtime not in TEXT_RUNTIMES and runtime not in VIDEO_RUNTIMES and not checkpoint.exists():
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


def _profile_limitations(profile: dict[str, object]) -> list[str]:
    values = profile.get("limitations")
    if not isinstance(values, list):
        return []
    return [str(item) for item in values]


def _checkpoint_hint(runtime: str) -> list[str]:
    if runtime == "aide":
        return ["Fetch the checkpoint with scripts/fetch_aide.py, or point 'checkpoint' at a local progan_train.pth."]
    if runtime == "aasist":
        return ["Fetch the checkpoint with scripts/fetch_aasist.py (downloads the official AASIST.pth, ~1.3 MB), or point 'checkpoint' at a local AASIST state dict."]
    if runtime == "clip-linear":
        return ["Download the detector's linear-head weights and point 'checkpoint' at the .pth file; the CLIP backbone named in 'backbone' is fetched by transformers on first use."]
    if runtime == "hf-text-classifier":
        return ["The model id in 'hub_model' is fetched by transformers on first use; set it to a local snapshot directory to run fully offline."]
    if runtime == "causal-lm-ppl":
        return ["The reference LM id in 'hub_model' is fetched by transformers on first use (~1 GB); set it to a local snapshot directory to run fully offline."]
    if runtime == "binoculars":
        return ["The performer/observer LM ids in 'hub_model'/'observer_model' are fetched by transformers on first use; set them to local snapshot directories to run fully offline."]
    if runtime == "torchvision":
        return ["Download the detector's published state-dict checkpoint and point 'checkpoint' at the .pth file."]
    return ["Use an absolute checkpoint path or a path relative to the model profile."]


def _runtime_install_hint(runtime: str) -> str:
    if runtime == "aide":
        return "Install the optional research stack (torch, torchvision, timm, Pillow, numpy) to enable the AIDE engine."
    if runtime == "aasist":
        return "Install the optional research stack (torch, numpy) to enable the AASIST engine; PCM .wav files need no other decoder."
    if runtime == "clip-linear":
        return "Install the optional clip-linear stack (torch, transformers, Pillow) to enable the CLIP linear-probe runtime."
    if runtime == "hf-text-classifier":
        return "Install the optional hf-text-classifier stack (torch, transformers) to enable the text-detector runtime."
    if runtime == "causal-lm-ppl":
        return "Install the optional causal-lm-ppl stack (torch, transformers) to enable the perplexity-screen runtime."
    if runtime == "binoculars":
        return "Install the optional binoculars stack (torch, transformers) to enable the two-LM perplexity-ratio runtime."
    if runtime == "torchvision":
        return "Install the optional torchvision stack (torch, torchvision, Pillow, numpy) to enable the torchvision runtime."
    if runtime == "video-frames":
        return "Install opencv plus the stack required by the inner image profile to enable the video-frames runtime."
    return "Install Pillow plus onnxruntime or torch in the local environment to enable neural inference."


# The AIDE engine keeps its 3.3 GB checkpoint resident between files; keyed by
# resolved checkpoint path so a scan loads weights once instead of per image.
_AIDE_RUNNERS: dict[str, tuple[object, object, object]] = {}


def _aide_runner(checkpoint: Path) -> tuple[object, object, object]:
    """Load scripts/run_aide.py (module, model, DCT preprocessor), cached per checkpoint."""
    importlib.import_module("torch")  # surface ImportError before loading the heavy script
    key = str(checkpoint.resolve())
    cached = _AIDE_RUNNERS.get(key)
    if cached is not None:
        return cached
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "run_aide.py"
    if not script.is_file():
        raise RuntimeError(f"AIDE runner script is missing: {script}")
    spec = importlib.util.spec_from_file_location("deepfake_lens_aide_runner", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load AIDE runner: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runner = (module, module.load_model(checkpoint), module.DctPreprocessor())
    _AIDE_RUNNERS[key] = runner
    return runner


def _run_aide(checkpoint: Path, image_path: Path) -> list[float]:
    """Score one image with the AIDE reimplementation (optional torch stack)."""
    torch = importlib.import_module("torch")
    image_module = importlib.import_module("PIL.Image")
    module, model, dct = _aide_runner(checkpoint)
    batch = module.preprocess(image_module.open(image_path), dct).unsqueeze(0)
    with torch.no_grad():
        logits = model(batch)
    return _flatten_outputs(logits.detach().cpu().numpy())


# The AASIST checkpoint is tiny (~1.3 MB) but still cached per path so a scan
# loads weights once instead of per audio file.
_AASIST_RUNNERS: dict[str, tuple[object, object]] = {}


def _aasist_runner(checkpoint: Path) -> tuple[object, object]:
    """Load scripts/run_aasist.py (module, model), cached per checkpoint."""
    importlib.import_module("torch")  # surface ImportError before loading the script
    key = str(checkpoint.resolve())
    cached = _AASIST_RUNNERS.get(key)
    if cached is not None:
        return cached
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "run_aasist.py"
    if not script.is_file():
        raise RuntimeError(f"AASIST runner script is missing: {script}")
    spec = importlib.util.spec_from_file_location("deepfake_lens_aasist_runner", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load AASIST runner: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runner = (module, module.load_model(checkpoint))
    _AASIST_RUNNERS[key] = runner
    return runner


def _run_aasist(checkpoint: Path, audio_path: Path, profile: dict[str, object]) -> list[float]:
    """Score one audio file with the AASIST reimplementation (optional torch stack).

    Decodes to mono at the profile's ``sample_rate`` (stdlib wave for PCM
    .wav, optional soundfile/librosa otherwise), then scores evenly spaced
    ``window_samples`` windows — each loop-padded/trimmed exactly like
    upstream pad() — and averages logits over up to ``max_seconds`` of audio.
    Returns raw logits [spoof, bonafide]; the profile's score_index selects
    the spoof probability.
    """
    module, model = _aasist_runner(checkpoint)
    sample_rate = int(profile.get("sample_rate", 16000) or 16000)
    nb_samp = int(profile.get("window_samples", 64600) or 64600)
    max_seconds = float(profile.get("max_seconds", 30) or 30)
    logits = module.score_audio_file(model, audio_path, sample_rate=sample_rate, nb_samp=nb_samp, max_seconds=max_seconds)
    return _flatten_outputs(logits.detach().cpu().numpy())


# CLIP backbones are multi-hundred-MB downloads; keep them resident between
# files. Linear heads are small but cached too so a scan stays cheap.
_CLIP_BACKBONES: dict[str, tuple[object, object]] = {}
_CLIP_HEADS: dict[str, tuple[object, object]] = {}
_TORCHVISION_MODELS: dict[str, object] = {}


def _clip_backbone(backbone: str) -> tuple[object, object]:
    """Load a Hugging Face CLIPModel + processor, cached per backbone id/path."""
    cached = _CLIP_BACKBONES.get(backbone)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    model = transformers.CLIPModel.from_pretrained(backbone)
    processor = transformers.CLIPProcessor.from_pretrained(backbone)
    model.eval()
    _CLIP_BACKBONES[backbone] = (model, processor)
    return model, processor


def _load_linear_head(checkpoint: Path):
    """Load a linear-probe head (.pth state dict or raw weight tensor).

    Accepts state dicts whose keys end in ``weight``/``bias`` (e.g. UnivFD's
    ``fc_weights.pth``), a ``{"state_dict": ...}`` wrapper, or a bare 2-D
    weight tensor (bias defaults to 0).
    """
    torch = importlib.import_module("torch")
    key = str(checkpoint.resolve())
    cached = _CLIP_HEADS.get(key)
    if cached is not None:
        return cached
    state = torch.load(str(checkpoint), map_location="cpu")
    if isinstance(state, dict):
        nested = state.get("state_dict") if isinstance(state.get("state_dict"), dict) else state
        weight = next((value for name, value in nested.items() if str(name).lower().endswith("weight") and hasattr(value, "ndim") and value.ndim == 2), None)
        bias = next((value for name, value in nested.items() if str(name).lower().endswith("bias") and hasattr(value, "reshape")), None)
        if weight is None:
            raise RuntimeError(f"linear head checkpoint has no 2-D weight tensor: {checkpoint}")
    else:
        weight, bias = state, None
        if getattr(weight, "ndim", 0) != 2:
            raise RuntimeError(f"linear head checkpoint must be a state dict or a 2-D weight tensor: {checkpoint}")
    weight = weight.float()
    if bias is None:
        bias = torch.zeros(weight.shape[0])
    head = (weight, bias.float().reshape(-1))
    _CLIP_HEADS[key] = head
    return head


def _run_clip_linear(checkpoint: Path, image_path: Path, profile: dict[str, object]) -> list[float]:
    """Score one image with a CLIP backbone + linear probe (UnivFD-style).

    The processor owns resizing/normalization, so the profile's
    input_size/mean/std fields are documentation rather than consumed inputs.
    Features are L2-normalized before the probe, matching UnivFD.
    """
    torch = importlib.import_module("torch")
    image_module = importlib.import_module("PIL.Image")
    backbone = str(profile.get("backbone") or "openai/clip-vit-large-patch14")
    model, processor = _clip_backbone(backbone)
    weight, bias = _load_linear_head(checkpoint)
    image = image_module.open(image_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        features = model.get_image_features(**inputs)
        # transformers >=5 returns BaseModelOutputWithPooling; 4.x returned a
        # bare tensor. Unwrap the pooled feature in either case.
        if hasattr(features, "pooler_output"):
            features = features.pooler_output
        elif isinstance(features, (tuple, list)):
            features = features[0]
        features = features.float()
        features = features / features.norm(dim=-1, keepdim=True).clamp_min(1e-12)
        logits = features @ weight.T + bias
    return _flatten_outputs(logits.detach().cpu().numpy())


# HF text classifiers are multi-hundred-MB downloads; keep them resident
# between files like the CLIP backbones.
_HF_TEXT_MODELS: dict[str, tuple[object, object]] = {}
_HF_TEXT_MAX_BYTES = 256 * 1024


def _hf_text_model(hub_model: str) -> tuple[object, object]:
    cached = _HF_TEXT_MODELS.get(hub_model)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(hub_model)
    model = transformers.AutoModelForSequenceClassification.from_pretrained(hub_model)
    model.eval()
    pair = (tokenizer, model)
    _HF_TEXT_MODELS[hub_model] = pair
    return pair


def _run_hf_text_classifier(media_path: Path, profile: dict[str, object]) -> list[float]:
    """Score one text file with a Hugging Face sequence classifier.

    The profile's ``hub_model`` names the model id (e.g.
    ``openai-community/roberta-base-openai-detector``); transformers fetches
    it on first use. Text is read bounded (256 KiB) and tokenized with
    truncation. Returns raw logits; the profile's score_index/activation
    selects the fake/AI probability.
    """
    torch = importlib.import_module("torch")
    hub_model = str(profile.get("hub_model") or "")
    if not hub_model:
        raise RuntimeError("hf-text-classifier profile needs a 'hub_model' field (e.g. openai-community/roberta-base-openai-detector)")
    tokenizer, model = _hf_text_model(hub_model)
    raw = media_path.read_bytes()[:_HF_TEXT_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace")
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        logits = model(**inputs).logits
    return _flatten_outputs(logits.detach().cpu().numpy())


# Causal LMs for the perplexity screen are ~1 GB downloads; keep them
# resident between files like the classifier stack.
_PPL_MODELS: dict[str, tuple[object, object]] = {}
_PPL_MAX_BYTES = 256 * 1024
_PPL_MIN_TOKENS = 16


def _causal_lm_model(hub_model: str) -> tuple[object, object]:
    cached = _PPL_MODELS.get(hub_model)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(hub_model)
    model = transformers.AutoModelForCausalLM.from_pretrained(hub_model)
    model.eval()
    pair = (tokenizer, model)
    _PPL_MODELS[hub_model] = pair
    return pair


def _extract_prose(text: str) -> str:
    """Strip markup so perplexity measures natural prose, not syntax.

    Markdown structure (headers, tables, code fences, list markers, links)
    is high-perplexity under a causal LM regardless of who wrote it — agent
    output would otherwise score as 'human' purely because of formatting.
    List-item text is kept (markers stripped); tables, code blocks, URLs,
    and pure markup lines are dropped. Falls back to the raw text when
    extraction removes almost everything (plain prose input).
    """
    # Drop fenced code blocks entirely.
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", " ", text)
    # Inline links/images keep their anchor text.
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"<[^>\n]{1,200}>", " ", text)

    kept: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("|"):  # table row
            continue
        if re.fullmatch(r"[-=:_|#*\s`~.]+", line):  # pure markup line
            continue
        # Strip heading/list markers and emphasis, keep the sentence.
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^(?:\d+[.)]|[-*•+])\s+", "", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", line)
        line = line.strip(" -–—|")
        if len(line) >= 12:  # drop label crumbs like 'Summary' or '—'
            kept.append(line)
    prose = " ".join(kept)
    return prose if len(prose) >= 200 else text


def _run_causal_lm_ppl(media_path: Path, profile: dict[str, object], *, model_name: str) -> ExternalModelAnalysis:
    """Perplexity screen: mean token NLL under a reference causal LM.

    Text written by an LLM tends to sit at lower perplexity under a
    *different* reference LM — this is generator-agnostic, so it covers
    generators the profile never saw (Codex/Claude/Gemini/Grok/Kimi all
    compress text toward the same low-PPL regime). It also works on any
    language the reference LM covers, including Korean — unlike the
    English-trained classifier stack.

    Score maps log-PPL onto 0-100 between the profile's ``ppl_low``
    (AI-typical anchor) and ``ppl_high`` (human-typical anchor). The raw
    PPL is reported in ``detail`` so the anchors can be recalibrated on a
    labeled corpus — until then every result carries the uncalibrated
    limitation from the profile.
    """
    torch = importlib.import_module("torch")
    hub_model = str(profile.get("hub_model") or "")
    if not hub_model:
        raise RuntimeError("causal-lm-ppl profile needs a 'hub_model' field (e.g. Qwen/Qwen2.5-0.5B)")
    profile_limitations = _profile_limitations(profile)
    raw = media_path.read_bytes()[:_PPL_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return ExternalModelAnalysis(
            available=False, score=0, confidence="unavailable", model=model_name,
            detail="causal-lm-ppl: file decodes to empty text.",
            limitations=list(profile_limitations),
        )
    tokenizer, model = _causal_lm_model(hub_model)
    window = max(_PPL_MIN_TOKENS, int(profile.get("window_tokens", 512) or 512))
    max_windows = int(profile.get("max_windows", 0) or 0)  # 0 = no cap
    # Score both the raw text and the markup-stripped prose view, keeping
    # the lower perplexity: markdown structure inflates PPL for human and
    # AI alike, so an agent's prose hiding inside a formatted document
    # must not evade purely because of its syntax.
    prose = _extract_prose(text)
    views = [(text, "raw")] + ([(prose, "prose")] if prose != text else [])
    best_ppl: float | None = None
    best_burst_cv: float | None = None
    view_notes: list[str] = []
    for view_text, view_name in views:
        ids = tokenizer(view_text, return_tensors="pt").input_ids[0]
        total_nll = 0.0
        total_tokens = 0
        windows_done = 0
        window_nlls: list[float] = []
        with torch.no_grad():
            for start in range(0, ids.shape[0], window):
                if max_windows and windows_done >= max_windows:
                    break
                chunk = ids[start:start + window]
                if chunk.shape[0] < _PPL_MIN_TOKENS:
                    break
                windows_done += 1
                out = model(input_ids=chunk.unsqueeze(0), labels=chunk.unsqueeze(0))
                n_tokens = int(chunk.shape[0]) - 1
                total_nll += float(out.loss) * n_tokens
                total_tokens += n_tokens
                window_nlls.append(float(out.loss))
        if total_tokens < _PPL_MIN_TOKENS:
            continue
        view_ppl = math.exp(min(20.0, total_nll / total_tokens))
        # Burstiness: coefficient of variation across per-window NLLs.
        # Human writing bursty (high CV); machine text uniform (low CV).
        burst_cv = 0.0
        if len(window_nlls) >= 3:
            mean_w = sum(window_nlls) / len(window_nlls)
            var_w = sum((v - mean_w) ** 2 for v in window_nlls) / len(window_nlls)
            burst_cv = (var_w ** 0.5) / max(1e-9, mean_w)
        view_notes.append(f"{view_name}:ppl={view_ppl:.2f}@{total_tokens}tok burstCV={burst_cv:.2f}")
        if best_ppl is None or view_ppl < best_ppl:
            best_ppl = view_ppl
            mean_nll = total_nll / total_tokens
            total_tokens_used = total_tokens
            best_burst_cv = burst_cv
    if best_ppl is None:
        return ExternalModelAnalysis(
            available=False, score=0, confidence="unavailable", model=model_name,
            detail=f"causal-lm-ppl: fewer than {_PPL_MIN_TOKENS} scored tokens in every view.",
            limitations=list(profile_limitations),
        )
    ppl = best_ppl
    ppl_low = float(profile.get("ppl_low", 8.0) or 8.0)
    ppl_high = float(profile.get("ppl_high", 60.0) or 60.0)
    lo, hi = math.log(ppl_low), math.log(ppl_high)
    score = int(round(max(0.0, min(100.0, 100.0 * (hi - math.log(max(ppl, 1e-9))) / (hi - lo)))))
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=(
            f"causal-lm-ppl: ppl={ppl:.2f} mean_nll={mean_nll:.3f} tokens={total_tokens_used} "
            f"burst_cv={best_burst_cv:.2f} views=[{'; '.join(view_notes)}] window={window} "
            f"ref_lm={hub_model} anchors=[{ppl_low},{ppl_high}] score={score}."
        ),
        limitations=list(profile_limitations),
    )


def _run_binoculars(media_path: Path, profile: dict[str, object], *, model_name: str) -> ExternalModelAnalysis:
    """Binoculars screen (Hans et al. 2024): log-PPL under a performer LM
    divided by the cross-entropy of an observer LM evaluated on the
    performer's own next-token choices.

    s(x) = mean_nll_M1(x) / x_nll(M1->M2). Machine text scores *lower*
    (the observer agrees with the performer's confident picks), human
    text scores higher. This self-normalizing ratio is more robust to
    domain shift than raw PPL and remains generator-agnostic. Both LMs
    must share a tokenizer family (e.g. Qwen2.5-0.5B + Qwen2.5-1.5B);
    the performer's tokenizer drives tokenization.

    Score maps the ratio onto 0-100 between the profile's ``ratio_low``
    (AI-typical) and ``ratio_high`` (human-typical) anchors — provisional
    until calibration on the labeled corpus.
    """
    torch = importlib.import_module("torch")
    performer_id = str(profile.get("hub_model") or "")
    observer_id = str(profile.get("observer_model") or "")
    if not performer_id or not observer_id:
        raise RuntimeError("binoculars profile needs 'hub_model' (performer) and 'observer_model' fields")
    profile_limitations = _profile_limitations(profile)
    raw = media_path.read_bytes()[:_PPL_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return ExternalModelAnalysis(
            available=False, score=0, confidence="unavailable", model=model_name,
            detail="binoculars: file decodes to empty text.",
            limitations=list(profile_limitations),
        )
    tokenizer, performer = _causal_lm_model(performer_id)
    _, observer = _causal_lm_model(observer_id)
    window = max(_PPL_MIN_TOKENS, int(profile.get("window_tokens", 512) or 512))
    max_windows = int(profile.get("max_windows", 0) or 0)  # 0 = no cap
    # Raw view only: the X-PPL denominator already normalizes markup, so
    # the dual-view trick used by causal-lm-ppl buys little here at 2x cost.
    views = [(text, "raw")]
    best_ratio: float | None = None
    best_nll = best_xnll = 0.0
    best_tokens = 0
    view_notes: list[str] = []
    for view_text, view_name in views:
        ids = tokenizer(view_text, return_tensors="pt").input_ids[0]
        sum_nll = sum_xnll = 0.0
        n_positions = 0
        windows_done = 0
        with torch.no_grad():
            for start in range(0, ids.shape[0], window):
                if max_windows and windows_done >= max_windows:
                    break
                chunk = ids[start:start + window]
                if chunk.shape[0] < _PPL_MIN_TOKENS:
                    break
                windows_done += 1
                batch = chunk.unsqueeze(0)
                out1 = performer(input_ids=batch)
                logits1 = out1.logits[0, :-1].float()  # position i predicts token i+1
                out2 = observer(input_ids=batch)
                logp2 = torch.log_softmax(out2.logits[0, :-1].float(), dim=-1)
                # X-PPL: expected observer log-prob over the performer's own
                # next-token distribution — cross-entropy H(M1, M2), not the
                # argmax path. This is the denominator of the paper's ratio.
                probs1 = torch.softmax(logits1, dim=-1)
                x_nll = -(probs1 * logp2).sum(dim=-1)
                # Performer NLL on the actual tokens.
                logp1 = torch.log_softmax(logits1, dim=-1)
                nll = -logp1.gather(-1, chunk[1:].unsqueeze(-1)).squeeze(-1)
                sum_nll += float(nll.sum())
                sum_xnll += float(x_nll.sum())
                n_positions += nll.shape[0]
        if n_positions < _PPL_MIN_TOKENS:
            continue
        mean_nll = sum_nll / n_positions
        mean_xnll = sum_xnll / n_positions
        ratio = mean_nll / max(mean_xnll, 1e-9)
        view_notes.append(f"{view_name}:ratio={ratio:.3f} nll={mean_nll:.3f} xnll={mean_xnll:.3f}@{n_positions}tok")
        if best_ratio is None or ratio < best_ratio:
            best_ratio, best_nll, best_xnll, best_tokens = ratio, mean_nll, mean_xnll, n_positions
    if best_ratio is None:
        return ExternalModelAnalysis(
            available=False, score=0, confidence="unavailable", model=model_name,
            detail=f"binoculars: fewer than {_PPL_MIN_TOKENS} scored tokens in every view.",
            limitations=list(profile_limitations),
        )
    ratio_low = float(profile.get("ratio_low", 0.85) or 0.85)
    ratio_high = float(profile.get("ratio_high", 1.05) or 1.05)
    score = int(round(max(0.0, min(100.0, 100.0 * (ratio_high - best_ratio) / (ratio_high - ratio_low)))))
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=(
            f"binoculars: ratio={best_ratio:.3f} mean_nll={best_nll:.3f} x_nll={best_xnll:.3f} "
            f"tokens={best_tokens} views=[{'; '.join(view_notes)}] window={window} "
            f"performer={performer_id} observer={observer_id} "
            f"anchors=[{ratio_low},{ratio_high}] score={score}."
        ),
        limitations=list(profile_limitations),
    )


def _run_torchvision(checkpoint: Path, array, profile: dict[str, object]) -> list[float]:
    """Score one image with a torchvision arch + published state dict.

    Profile fields: ``arch`` (default ``resnet50``), ``num_classes``
    (default 1 — the classifier head is rebuilt before loading), and
    ``state_dict_prefix`` (e.g. ``model.`` for CNNDetection's wrapped
    checkpoint) stripped from checkpoint keys before ``load_state_dict``.
    """
    torch = importlib.import_module("torch")
    torchvision_models = importlib.import_module("torchvision.models")
    arch = str(profile.get("arch") or "resnet50")
    num_classes = int(profile.get("num_classes", 1) or 1)
    key = f"{arch}:{num_classes}:{checkpoint.resolve()}"
    model = _TORCHVISION_MODELS.get(key)
    if model is None:
        model_fn = getattr(torchvision_models, arch, None)
        if model_fn is None:
            raise RuntimeError(f"torchvision.models has no architecture named '{arch}'")
        model = model_fn(weights=None)
        if not hasattr(model, "fc"):
            raise RuntimeError(f"torchvision arch '{arch}' has no fc head to rewire for num_classes={num_classes}")
        model.fc = torch.nn.Linear(model.fc.in_features, num_classes)
        state = torch.load(str(checkpoint), map_location="cpu")
        if isinstance(state, dict):
            for wrapper in ("state_dict", "model", "net"):
                nested = state.get(wrapper)
                if isinstance(nested, dict) and any(hasattr(v, "ndim") for v in nested.values()):
                    state = nested
                    break
        prefix = str(profile.get("state_dict_prefix") or "")
        if prefix:
            state = {name[len(prefix):] if str(name).startswith(prefix) else name: value for name, value in state.items()}
        model.load_state_dict(state, strict=True)
        model.eval()
        _TORCHVISION_MODELS[key] = model
    with torch.no_grad():
        output = model(torch.from_numpy(array))
    if isinstance(output, (tuple, list)):
        output = output[0]
    return _flatten_outputs(output.detach().cpu().numpy())


def _checkpoint_path(profile: dict[str, object], *, base_dir: Path) -> Path:
    raw = str(profile.get("checkpoint") or profile.get("path") or "")
    path = Path(raw)
    return path if path.is_absolute() else base_dir / path


def _preprocess_image(image_path: Path, profile: dict[str, object]):
    image_module = importlib.import_module("PIL.Image")
    np = importlib.import_module("numpy")
    input_size = int(profile.get("input_size", 224) or 224)
    image = image_module.open(image_path).convert("RGB").resize((input_size, input_size))
    array = np.asarray(image).astype("float32") / 255.0
    mean = np.asarray(profile.get("mean", [0.485, 0.456, 0.406]), dtype="float32").reshape(1, 1, 3)
    std = np.asarray(profile.get("std", [0.229, 0.224, 0.225]), dtype="float32").reshape(1, 1, 3)
    array = (array - mean) / std
    return array.transpose(2, 0, 1)[None, ...]


def _run_onnx(checkpoint: Path, array, profile: dict[str, object]) -> list[float]:
    ort = importlib.import_module("onnxruntime")
    session = ort.InferenceSession(str(checkpoint), providers=["CPUExecutionProvider"])
    input_name = str(profile.get("input_name") or session.get_inputs()[0].name)
    outputs = session.run(None, {input_name: array})
    return _flatten_outputs(outputs[0])


def _run_torchscript(checkpoint: Path, array) -> list[float]:
    torch = importlib.import_module("torch")
    model = torch.jit.load(str(checkpoint), map_location="cpu")
    model.eval()
    with torch.no_grad():
        output = model(torch.from_numpy(array))
    if isinstance(output, (tuple, list)):
        output = output[0]
    return _flatten_outputs(output.detach().cpu().numpy())


def _flatten_outputs(value) -> list[float]:
    try:
        return [float(item) for item in value.reshape(-1).tolist()]
    except AttributeError:
        if isinstance(value, (list, tuple)):
            return [float(item) for item in value]
        return [float(value)]


def _score_from_outputs(values: list[float], profile: dict[str, object]) -> int:
    if not values:
        return 0
    index = int(profile.get("score_index", 1 if len(values) > 1 else 0) or 0)
    index = max(0, min(index, len(values) - 1))
    activation = str(profile.get("score_activation") or ("softmax" if len(values) > 1 else "sigmoid")).lower()
    if activation == "softmax" and len(values) > 1:
        shifted = [value - max(values) for value in values]
        exps = [math.exp(max(-80.0, min(80.0, value))) for value in shifted]
        score = exps[index] / max(1e-12, sum(exps))
    elif activation == "sigmoid":
        score = 1.0 / (1.0 + math.exp(-max(-80.0, min(80.0, values[index]))))
    else:
        score = values[index]
    return _normalize_score(score) or 0


def _score_from_score_map(profile: dict[str, object], image_path: Path) -> int | None:
    score_map = profile.get("score_map")
    if not isinstance(score_map, dict):
        return None
    candidates = [str(image_path), image_path.name]
    try:
        candidates.append(str(image_path.resolve()))
    except OSError:
        pass
    for key in candidates:
        if key in score_map:
            return _normalize_score(score_map[key])
    return None


def _score_from_sidecar(profile: dict[str, object], image_path: Path) -> int | None:
    if profile.get("type") not in {"score-sidecar-v1", "deepfake-lens-portable-threshold-v1"}:
        return None
    sidecars = [
        image_path.with_suffix(image_path.suffix + ".model.json"),
        image_path.with_suffix(".model.json"),
        image_path.parent / (image_path.name + ".model.json"),
    ]
    for sidecar in sidecars:
        if not sidecar.exists():
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("score", "fake_score", "probability", "confidence"):
            if key in payload:
                return _normalize_score(payload[key])
    return None


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

    cv2 = importlib.import_module("cv2")
    inner = profile.get("inner") or profile.get("frame_profile")
    if not isinstance(inner, dict):
        raise RuntimeError("video-frames profile needs an 'inner' image-runtime profile object")
    inner_runtime = str(inner.get("runtime") or "").lower()
    if inner_runtime in VIDEO_RUNTIMES or not inner_runtime:
        raise RuntimeError("video-frames 'inner' profile must name an image runtime (onnx/torchscript/aide/clip-linear/torchvision)")
    frame_target = max(1, int(profile.get("frames", 8) or 8))

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


def _normalize_score(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if 0 <= number <= 1:
        number *= 100
    return max(0, min(100, int(round(number))))


def _confidence_for_score(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"
