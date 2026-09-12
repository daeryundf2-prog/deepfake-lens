from __future__ import annotations

import importlib
import importlib.util
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

# Profile-set marker: a JSON file that lists member profiles/directories so a
# single --model-path can drive several detectors at once.
PROFILE_SET_TYPE = "deepfake-lens-profile-set-v1"
# Scores farther apart than this count as member disagreement.
AGREEMENT_SPREAD = 20
_MAX_PROFILE_DEPTH = 4


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
) -> ExternalModelAnalysis | None:
    """Score one image with external model profile(s).

    ``model_path`` may be a single profile/checkpoint file, a directory of
    ``*.json`` profiles, a profile-set JSON (``type: deepfake-lens-profile-set-v1``
    with a ``profiles`` list), or a list of any of those. With more than one
    profile every member runs and the result reports per-model scores plus an
    agreement signal; members that cannot run degrade to ``available=False``
    entries rather than failing the whole analysis.
    """
    if model_path is None:
        return None

    image_path = Path(path)
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

    results = [(source, _analyze_profile_file(image_path, source, depth=0)) for source in sources]
    if len(results) == 1:
        return results[0][1]
    return _aggregate_profile_results(results)


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


def _analyze_profile_file(image_path: Path, model_file: Path, *, depth: int) -> ExternalModelAnalysis:
    if model_file.suffix.lower() in {".pt", ".pth", ".onnx", ".torchscript"}:
        runtime = "onnx" if model_file.suffix.lower() == ".onnx" else "torchscript"
        return _score_from_runtime_profile({"runtime": runtime, "checkpoint": str(model_file), "name": model_file.name}, image_path, base_dir=model_file.parent)

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
        return _analyze_profile_set(image_path, model_file, profile, model_name=model_name, depth=depth)

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

    score = _score_from_score_map(profile, image_path)
    if score is None:
        score = _score_from_sidecar(profile, image_path)
    if score is None:
        runtime_result = _score_from_runtime_profile(profile, image_path, base_dir=model_file.parent)
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


def _analyze_profile_set(image_path: Path, model_file: Path, profile: dict[str, object], *, model_name: str, depth: int) -> ExternalModelAnalysis:
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
    if not sources:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"profile set {model_file.name} resolved to no member profiles.",
        )
    results = [(source, _analyze_profile_file(image_path, source, depth=depth + 1)) for source in sources]
    return _aggregate_profile_results(results, model_name=model_name)


def _aggregate_profile_results(results: list[tuple[Path, ExternalModelAnalysis]], *, model_name: str | None = None) -> ExternalModelAnalysis:
    """Merge per-profile results into one analysis with an agreement signal."""
    scored = [result for _, result in results if result.available]
    scores = [result.score for result in scored]
    score = int(round(sum(scores) / len(scores))) if scores else 0
    spread = max(scores) - min(scores) if len(scores) > 1 else 0
    agreement = "n/a" if len(scores) < 2 else ("high" if spread <= AGREEMENT_SPREAD else "low")

    detail = f"{len(scored)}/{len(results)} model profiles produced scores"
    if scores:
        detail += f"; aggregate score={score} (mean of members)"
    if len(scores) > 1:
        detail += f"; member spread={spread} (agreement: {agreement})"

    limitations: list[str] = []
    for _, result in results:
        for item in result.limitations:
            if item not in limitations:
                limitations.append(item)
    limitations.append("Aggregated external scores are the mean of available members — a prioritization signal, not a truth label.")
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


def _score_from_runtime_profile(profile: dict[str, object], image_path: Path, *, base_dir: Path) -> ExternalModelAnalysis | None:
    runtime = str(profile.get("runtime") or "").lower()
    if runtime not in {"onnx", "torchscript", "aide", "clip-linear", "torchvision"}:
        return None
    checkpoint = _checkpoint_path(profile, base_dir=base_dir)
    model_name = str(profile.get("name") or profile.get("model") or checkpoint.name)
    profile_limitations = _profile_limitations(profile)
    if not checkpoint.exists():
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
            values = _run_aide(checkpoint, image_path)
        elif runtime == "clip-linear":
            values = _run_clip_linear(checkpoint, image_path, profile)
        else:
            array = _preprocess_image(image_path, profile)
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
    if runtime == "clip-linear":
        return ["Download the detector's linear-head weights and point 'checkpoint' at the .pth file; the CLIP backbone named in 'backbone' is fetched by transformers on first use."]
    if runtime == "torchvision":
        return ["Download the detector's published state-dict checkpoint and point 'checkpoint' at the .pth file."]
    return ["Use an absolute checkpoint path or a path relative to the model profile."]


def _runtime_install_hint(runtime: str) -> str:
    if runtime == "aide":
        return "Install the optional research stack (torch, torchvision, timm, Pillow, numpy) to enable the AIDE engine."
    if runtime == "clip-linear":
        return "Install the optional clip-linear stack (torch, transformers, Pillow) to enable the CLIP linear-probe runtime."
    if runtime == "torchvision":
        return "Install the optional torchvision stack (torch, torchvision, Pillow, numpy) to enable the torchvision runtime."
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
        if isinstance(state, dict) and isinstance(state.get("state_dict"), dict):
            state = state["state_dict"]
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
