from __future__ import annotations

import importlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ExternalModelAnalysis:
    available: bool
    score: int
    confidence: str
    model: str
    detail: str
    limitations: list[str] = field(default_factory=list)


def analyze_external_model(path: Path | str, model_path: Path | str | None) -> ExternalModelAnalysis | None:
    if model_path is None:
        return None

    image_path = Path(path)
    model_file = Path(model_path)
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

    model_name = str(profile.get("name") or profile.get("model") or model_file.name)
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
    if runtime not in {"onnx", "torchscript"}:
        return None
    checkpoint = _checkpoint_path(profile, base_dir=base_dir)
    model_name = str(profile.get("name") or profile.get("model") or checkpoint.name)
    if not checkpoint.exists():
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{runtime} checkpoint was not found: {checkpoint}",
            limitations=["Use an absolute checkpoint path or a path relative to the model profile."],
        )
    try:
        array = _preprocess_image(image_path, profile)
        values = _run_onnx(checkpoint, array, profile) if runtime == "onnx" else _run_torchscript(checkpoint, array)
        score = _score_from_outputs(values, profile)
    except ImportError as exc:
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{runtime} runtime is optional and not installed: {exc}",
            limitations=["Install Pillow plus onnxruntime or torch in the local environment to enable neural inference."],
        )
    except Exception as exc:  # noqa: BLE001 - model runtimes fail in many library-specific ways.
        return ExternalModelAnalysis(
            available=False,
            score=0,
            confidence="unavailable",
            model=model_name,
            detail=f"{runtime} inference failed: {exc}",
            limitations=["Verify input_size, mean/std, input_name, score_index, and checkpoint compatibility."],
        )
    return ExternalModelAnalysis(
        available=True,
        score=score,
        confidence=_confidence_for_score(score),
        model=model_name,
        detail=f"{runtime} runtime supplied score={score}.",
    )


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
