"""Runtime executors for external model profiles.

Each ``_run_*`` function binds one inference backend (AIDE, AASIST, HF
classifiers, CLIP linear probe, causal-LM perplexity, Binoculars,
torchvision, ONNX, TorchScript) and returns raw output values or an
``ExternalModelAnalysis``. Resident model caches live beside their
runtime — ``_ModelLRU`` instances self-register so
``clear_all_model_caches`` reaches them from ``model_cache``.

Extracted from ``model_adapter.py``; the adapter keeps profile routing
and dispatch and re-exports these names for existing callers/tests.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import re
from pathlib import Path

from .checkpoint_integrity import load_torch_state
from .model_cache import _ModelLRU, _model_cache_limit
from .model_pins import UNPINNED_REASON, PinError, is_commit_sha, require_revision
from .result_types import ExternalModelAnalysis


def _pinned_revision(revision: str, model_id: str) -> str:
    """Refuse a hub load without a pinned commit (G10).

    Every ``from_pretrained`` call passes ``revision=`` so the hub serves
    exactly the pinned commit; an empty or non-commit revision would load
    whatever ``main`` points at today, so it is refused here as a second
    line of defence behind the adapter's profile check.
    """
    if not revision or not is_commit_sha(revision):
        raise PinError(f"{UNPINNED_REASON}: {model_id} revision 미지정")
    return revision



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
    if runtime == "onnx-audio":
        return ["Download the ONNX checkpoint named by the profile's source_url into the profile's checkpoint path."]
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
    if runtime == "onnx-audio":
        return "Install onnxruntime and numpy to enable the raw-waveform ONNX audio runtime; PCM .wav files need no other decoder."
    return "Install Pillow plus onnxruntime or torch in the local environment to enable neural inference."


# The AIDE engine keeps its 3.3 GB checkpoint resident between files; keyed by
# resolved checkpoint path so a scan loads weights once instead of per image.
_AIDE_RUNNERS = _ModelLRU(_model_cache_limit())


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
_AASIST_RUNNERS = _ModelLRU(_model_cache_limit())


_AASIST_MODULE: object | None = None


def _aasist_module():
    """Load scripts/run_aasist.py once — its waveform decoder is shared by
    every audio runtime, not just the AASIST checkpoint path."""
    global _AASIST_MODULE
    if _AASIST_MODULE is None:
        repo_root = Path(__file__).resolve().parent.parent
        script = repo_root / "scripts" / "run_aasist.py"
        if not script.is_file():
            raise RuntimeError(f"AASIST runner script is missing: {script}")
        spec = importlib.util.spec_from_file_location("deepfake_lens_aasist_runner", script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load AASIST runner: {script}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AASIST_MODULE = module
    return _AASIST_MODULE


def _aasist_runner(checkpoint: Path) -> tuple[object, object]:
    """Load scripts/run_aasist.py (module, model), cached per checkpoint."""
    importlib.import_module("torch")  # surface ImportError before loading the script
    key = str(checkpoint.resolve())
    cached = _AASIST_RUNNERS.get(key)
    if cached is not None:
        return cached
    module = _aasist_module()
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


# Hugging Face audio classifiers (wav2vec2-family deepfake detectors) are
# multi-hundred-MB downloads — keep them resident between files.
_HF_AUDIO_MODELS = _ModelLRU(_model_cache_limit())


def _hf_audio_model(hub_model: str, revision: str) -> tuple[object, object]:
    revision = _pinned_revision(revision, hub_model)
    key = f"{hub_model}@{revision}"
    cached = _HF_AUDIO_MODELS.get(key)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    extractor = transformers.AutoFeatureExtractor.from_pretrained(hub_model, revision=revision)
    model = transformers.AutoModelForAudioClassification.from_pretrained(hub_model, revision=revision)
    model.eval()
    pair = (extractor, model)
    _HF_AUDIO_MODELS[key] = pair
    return pair


def _run_hf_audio_classifier(media_path: Path, profile: dict[str, object]) -> list[float]:
    """Score one audio file with a Hugging Face audio classifier.

    The profile's ``hub_model`` names the model id (e.g.
    Gustking/wav2vec2-large-xlsr-deepfake-audio-classification); transformers
    fetches it on first use — point it at a local snapshot dir for offline
    runs. Audio is decoded to mono float32 at the extractor's expected
    sampling rate via the shared run_aasist decoder (stdlib wave for PCM
    .wav, soundfile/librosa for everything else).

    Same ``score_label`` contract as hf-image-classifier: when set, the
    matching ``id2label`` entry's softmax probability is returned so label
    order in the checkpoint can never silently flip the score.
    """
    torch = importlib.import_module("torch")
    hub_model = str(profile.get("hub_model") or "")
    if not hub_model:
        raise RuntimeError("hf-audio-classifier profile needs a 'hub_model' field (e.g. Gustking/wav2vec2-large-xlsr-deepfake-audio-classification)")
    extractor, model = _hf_audio_model(hub_model, require_revision(profile))
    sample_rate = int(getattr(extractor, "sampling_rate", 16000) or 16000)
    max_seconds = float(profile.get("max_seconds", 15) or 15)
    waveform = _aasist_module().load_waveform(media_path, sample_rate=sample_rate, max_seconds=max_seconds)
    inputs = extractor(waveform, sampling_rate=sample_rate, return_tensors="pt")
    with torch.no_grad():
        logits = model(**inputs).logits
    values = _flatten_outputs(logits.detach().cpu().numpy())
    score_label = str(profile.get("score_label") or "").lower()
    if score_label:
        id2label = getattr(model.config, "id2label", None) or {}
        target = next((int(idx) for idx, name in id2label.items() if str(name).lower() == score_label), None)
        if target is None or target >= len(values):
            raise RuntimeError(f"score_label '{score_label}' not found in model labels {id2label}")
        shifted = [value - max(values) for value in values]
        exps = [math.exp(max(-80.0, min(80.0, value))) for value in shifted]
        return [exps[target] / max(1e-12, sum(exps))]
    return values


def _run_onnx_audio(checkpoint: Path, audio_path: Path, profile: dict[str, object]) -> list[float]:
    """Score one audio file with a raw-waveform ONNX classifier.

    Decodes mono float32 at the profile's ``sample_rate`` via the shared
    run_aasist decoder (stdlib wave for PCM .wav, soundfile/librosa
    otherwise), caps at ``max_seconds``, optionally applies per-file
    zero-mean/unit-variance normalization (``normalize_audio``), and feeds
    a (1, samples) tensor to the ONNX session.
    """
    ort = importlib.import_module("onnxruntime")
    np = importlib.import_module("numpy")
    sample_rate = int(profile.get("sample_rate", 16000) or 16000)
    max_seconds = float(profile.get("max_seconds", 15) or 15)
    waveform = _aasist_module().load_waveform(audio_path, sample_rate=sample_rate, max_seconds=max_seconds)
    array = np.asarray(waveform, dtype="float32").reshape(1, -1)
    if profile.get("normalize_audio"):
        std = float(array.std())
        array = (array - float(array.mean())) / (std if std > 1e-8 else 1.0)
    session = ort.InferenceSession(str(checkpoint), providers=["CPUExecutionProvider"])
    input_name = str(profile.get("input_name") or session.get_inputs()[0].name)
    outputs = session.run(None, {input_name: array})
    return _flatten_outputs(outputs[0])


# CLIP backbones are multi-hundred-MB downloads; keep them resident between
# files. Linear heads are small but cached too so a scan stays cheap.
_CLIP_BACKBONES = _ModelLRU(_model_cache_limit())
_CLIP_HEADS = _ModelLRU(_model_cache_limit())
_TORCHVISION_MODELS = _ModelLRU(_model_cache_limit())


def _clip_backbone(backbone: str, revision: str) -> tuple[object, object]:
    """Load a Hugging Face CLIPModel + processor, cached per backbone id + revision."""
    revision = _pinned_revision(revision, backbone)
    key = f"{backbone}@{revision}"
    cached = _CLIP_BACKBONES.get(key)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    model = transformers.CLIPModel.from_pretrained(backbone, revision=revision)
    processor = transformers.CLIPProcessor.from_pretrained(backbone, revision=revision)
    model.eval()
    _CLIP_BACKBONES[key] = (model, processor)
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
    state = load_torch_state(checkpoint)
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
    model, processor = _clip_backbone(backbone, require_revision(profile))
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
_HF_TEXT_MODELS = _ModelLRU(_model_cache_limit())
_HF_TEXT_MAX_BYTES = 256 * 1024


def _hf_text_model(hub_model: str, revision: str) -> tuple[object, object]:
    revision = _pinned_revision(revision, hub_model)
    key = f"{hub_model}@{revision}"
    cached = _HF_TEXT_MODELS.get(key)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(hub_model, revision=revision)
    model = transformers.AutoModelForSequenceClassification.from_pretrained(hub_model, revision=revision)
    model.eval()
    pair = (tokenizer, model)
    _HF_TEXT_MODELS[key] = pair
    return pair


def _run_hf_text_classifier(media_path: Path, profile: dict[str, object]) -> list[float]:
    """Score one text file with a Hugging Face sequence classifier.

    The profile's ``hub_model`` names the model id (e.g.
    ``fakespot-ai/roberta-base-ai-text-detection-v1``); transformers fetches
    it on first use. Text is read bounded (256 KiB) and tokenized with
    truncation. Returns raw logits; the profile's score_index/activation
    selects the fake/AI probability.
    """
    torch = importlib.import_module("torch")
    hub_model = str(profile.get("hub_model") or "")
    if not hub_model:
        raise RuntimeError("hf-text-classifier profile needs a 'hub_model' field (e.g. fakespot-ai/roberta-base-ai-text-detection-v1)")
    tokenizer, model = _hf_text_model(hub_model, require_revision(profile))
    raw = media_path.read_bytes()[:_HF_TEXT_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace")
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        logits = model(**inputs).logits
    return _flatten_outputs(logits.detach().cpu().numpy())


_HF_IMAGE_MODELS = _ModelLRU(_model_cache_limit())


def _hf_image_model(hub_model: str, revision: str) -> tuple[object, object]:
    revision = _pinned_revision(revision, hub_model)
    key = f"{hub_model}@{revision}"
    cached = _HF_IMAGE_MODELS.get(key)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    processor = transformers.AutoImageProcessor.from_pretrained(hub_model, revision=revision)
    model = transformers.AutoModelForImageClassification.from_pretrained(hub_model, revision=revision)
    model.eval()
    pair = (processor, model)
    _HF_IMAGE_MODELS[key] = pair
    return pair


def _run_hf_image_classifier(media_path: Path, profile: dict[str, object]) -> list[float]:
    """Score one image with a Hugging Face image classifier.

    The profile's ``hub_model`` names the model id; transformers fetches it
    on first use (set it to a local snapshot directory for offline runs).
    Returns logits in label order by default. When the profile sets
    ``score_label`` (e.g. ``"Fake"``), the matching ``id2label`` entry's
    softmax probability is returned as a single-element output instead —
    pair it with ``score_index: 0`` and ``score_activation: "none"``.
    """
    torch = importlib.import_module("torch")
    image_module = importlib.import_module("PIL.Image")
    hub_model = str(profile.get("hub_model") or "")
    if not hub_model:
        raise RuntimeError("hf-image-classifier profile needs a 'hub_model' field (e.g. umm-maybe/AI-image-detector)")
    processor, model = _hf_image_model(hub_model, require_revision(profile))
    image = image_module.open(media_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        logits = model(**inputs).logits
    values = _flatten_outputs(logits.detach().cpu().numpy())
    score_label = str(profile.get("score_label") or "").lower()
    if score_label:
        id2label = getattr(model.config, "id2label", None) or {}
        target = next((int(idx) for idx, name in id2label.items() if str(name).lower() == score_label), None)
        if target is None or target >= len(values):
            raise RuntimeError(f"score_label '{score_label}' not found in model labels {id2label}")
        shifted = [value - max(values) for value in values]
        exps = [math.exp(max(-80.0, min(80.0, value))) for value in shifted]
        return [exps[target] / max(1e-12, sum(exps))]
    return values


# Causal LMs for the perplexity screen are ~1 GB downloads; keep them
# resident between files like the classifier stack.
_PPL_MODELS = _ModelLRU(_model_cache_limit())
_PPL_MAX_BYTES = 256 * 1024
_PPL_MIN_TOKENS = 16




def _causal_lm_model(hub_model: str, revision: str) -> tuple[object, object]:
    revision = _pinned_revision(revision, hub_model)
    key = f"{hub_model}@{revision}"
    cached = _PPL_MODELS.get(key)
    if cached is not None:
        return cached
    transformers = importlib.import_module("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(hub_model, revision=revision)
    model = transformers.AutoModelForCausalLM.from_pretrained(hub_model, revision=revision)
    model.eval()
    pair = (tokenizer, model)
    _PPL_MODELS[key] = pair
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
    tokenizer, model = _causal_lm_model(hub_model, require_revision(profile))
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
    tokenizer, performer = _causal_lm_model(performer_id, require_revision(profile))
    _, observer = _causal_lm_model(observer_id, require_revision(profile, "observer_revision"))
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
        if hasattr(model, "fc"):
            model.fc = torch.nn.Linear(model.fc.in_features, num_classes)
        elif hasattr(model, "classifier"):
            # EfficientNet-family heads: Sequential(Dropout, …, Linear).
            head = model.classifier
            if isinstance(head, torch.nn.Sequential):
                if not isinstance(head[-1], torch.nn.Linear):
                    raise RuntimeError(f"torchvision arch '{arch}' classifier tail is not Linear")
                head[-1] = torch.nn.Linear(head[-1].in_features, num_classes)
            elif isinstance(head, torch.nn.Linear):
                model.classifier = torch.nn.Linear(head.in_features, num_classes)
            else:
                raise RuntimeError(f"torchvision arch '{arch}' classifier is not Linear/Sequential")
        else:
            raise RuntimeError(f"torchvision arch '{arch}' has no fc/classifier head to rewire for num_classes={num_classes}")
        state = load_torch_state(checkpoint)
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
    normalized = _normalize_score(score) or 0
    bias = profile.get("score_bias")
    if isinstance(bias, (int, float)) and not isinstance(bias, bool):
        normalized = max(0, min(100, normalized - int(round(float(bias)))))
    return normalized


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
