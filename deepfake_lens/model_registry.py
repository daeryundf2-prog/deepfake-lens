from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class DetectorCandidate:
    key: str
    name: str
    task: str
    adapter_target: str
    status: str
    priority: str
    source_url: str
    notes: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


DETECTOR_REGISTRY = [
    DetectorCandidate(
        key="ntire-2026-robust-wild",
        name="NTIRE 2026 Robust AI-Generated Image Detection in the Wild",
        task="benchmark",
        adapter_target="dataset/eval robustness suite",
        status="reference",
        priority="high",
        source_url="https://arxiv.org/abs/2604.11487",
        notes=[
            "Use as the robustness target: transformed, recompressed, resized, blurred, and cropped images.",
            "The challenge report is a benchmark and method survey, not one reusable checkpoint.",
        ],
    ),
    DetectorCandidate(
        key="aide-iclr-2025",
        name="AIDE AI-generated Image DEtector with Hybrid Features",
        task="binary-image-detector",
        adapter_target="torchscript/onnx profile",
        status="candidate",
        priority="high",
        source_url="https://github.com/shilinyan99/AIDE",
        notes=[
            "Good first pretrained integration candidate because code and checkpoints are public.",
            "Hybrid features line up with the existing pixel expert ensemble.",
            "models/aide-frames-runtime.json reuses this checkpoint per frame as an interim frame-level video screen; it is not temporal detection.",
        ],
    ),
    DetectorCandidate(
        key="univfd-cvpr-2023",
        name="UnivFD UniversalFakeDetect CLIP ViT-L/14 linear probe",
        task="binary-image-detector",
        adapter_target="clip-linear runtime profile",
        status="candidate",
        priority="high",
        source_url="https://github.com/YuhengLi99/UniversalFakeDetect",
        notes=[
            "Wired via models/univfd-runtime.json; needs transformers+torch and the released linear-head weights (not committed).",
            "CLIP-feature probes generalize across generators better than classifier retraining, which complements AIDE's DCT view.",
        ],
    ),
    DetectorCandidate(
        key="cnndetection-cvpr-2020",
        name="CNNDetection ResNet-50 blur+jpg",
        task="binary-image-detector",
        adapter_target="torchvision runtime profile",
        status="candidate",
        priority="medium",
        source_url="https://github.com/PeterWang512/CNNDetection",
        notes=[
            "Wired via models/cnndetection-runtime.json; download blur_jpg_prob.pth per the repo README (not committed).",
            "Classic ProGAN-era baseline; known weak transfer to diffusion generators — keep as a low-priority agreement member.",
        ],
    ),
    DetectorCandidate(
        key="swin-ai-image-umm-maybe",
        name="Swin-large AI-vs-human image detector (umm-maybe/AI-image-detector)",
        task="binary-image-detector",
        adapter_target="hf-image-classifier runtime profile",
        status="integrated",
        priority="medium",
        source_url="https://huggingface.co/umm-maybe/AI-image-detector",
        notes=[
            "Wired via models/ai-image-swin-runtime.json (score_label 'artificial'); ~870 MB hub weights download on first use.",
            "Measured locally (2026-09): 1/4 DALL-E samples caught, real Lenna correctly scored human 0.97 — its value is real-image calibration against AIDE's false positives (Lenna: AIDE 85 vs Swin 3), not commercial-generator recall.",
            "Opt-in member: picked up by models/ directory scans (web app, api_server, --model-path models) but kept out of the CLI default list — see experiments/IMAGE_EVALUATION.md.",
        ],
    ),
    DetectorCandidate(
        key="dire-iccv-2023",
        name="DIRE DIffusion Reconstruction Error",
        task="diffusion-image-detector",
        adapter_target="documented placeholder (needs ADM diffusion pipeline)",
        status="research",
        priority="medium",
        source_url="https://github.com/ZhendongWang6/DIRE",
        notes=[
            "Ships as models/dire-runtime.json with supported:false — no drop-in checkpoint exists.",
            "Strong reported diffusion coverage, but per-image inversion is too heavy for the screening path until a runtime is written.",
        ],
    ),
    DetectorCandidate(
        key="vit-face-manipulation-dima806",
        name="ViT deepfake-vs-real face classifier (dima806)",
        task="face-manipulation-detector",
        adapter_target="hf-image-classifier runtime profile",
        status="rejected",
        priority="high",
        source_url="https://huggingface.co/dima806/deepfake_vs_real_image_detection",
        notes=[
            "Measured locally and rejected for manipulation detection: AUROC ~0.51 on 45 face-focused SBI pairs, ~47% FPR@50 driven by aged portraits scoring 99-100 fake.",
            "Profiles kept as supported:false (face-manipulation-vit-*.json); the hf-image-classifier runtime + crop_faces machinery remains for the next candidate.",
            "Its trained task (fully generated faces vs real photos) is a different problem than faceswap — see experiments/FACESWAP_EVALUATION.md.",
        ],
    ),
    DetectorCandidate(
        key="efficientnet-ffpp-2025",
        name="EfficientNet-B0 face-manipulation detector (FaceForensics++ C23)",
        task="face-manipulation-detector",
        adapter_target="torchvision runtime profile",
        status="rejected",
        priority="high",
        source_url="https://huggingface.co/Xicor9/efficientnet-b0-ffpp-c23",
        notes=[
            "Measured locally and rejected: real Lenna face scored 96% fake while an SBI-manipulated copy scored lower (86%) — inverted/unusable signal.",
            "Profiles kept as supported:false placeholders with the measurement recorded; fetch_faceswap.py remains for anyone re-validating with better preprocessing/crops.",
        ],
    ),
    DetectorCandidate(
        key="sbi-effnet-b0-local",
        name="SBI-trained EfficientNet-B0 (local self-blend)",
        task="face-manipulation-detector",
        adapter_target="torchvision runtime profile",
        status="candidate",
        priority="high",
        source_url="local: experiments/train_detector.py --sbi --augment-degradation",
        notes=[
            "Wired via models/sbi-effnet-runtime.json; checkpoint not committed — reproduce with train_detector.py.",
            "Measured: in-domain AUROC ~0.90 incl. jpeg75/resize variants (FPR 0.05 clean); cross-domain portraits AUROC ~0.72, FPR ~0.67 — narrow-domain advisory member.",
            "--augment-degradation was the fix for a measured FPR-1.0 collapse under JPEG recompression; see experiments/FACESWAP_EVALUATION.md.",
        ],
    ),
    DetectorCandidate(
        key="aasist-2022",
        name="AASIST Audio Anti-Spoofing Integrated Spectro-Temporal Graph Attention",
        task="binary-audio-detector",
        adapter_target="aasist runtime profile",
        status="candidate",
        priority="high",
        source_url="https://github.com/clovaai/aasist",
        notes=[
            "Wired via models/aasist-runtime.json; scripts/fetch_aasist.py downloads the in-repo AASIST.pth (~1.3 MB, not committed).",
            "Trained on ASVspoof2019-LA — strong on TTS/VC attacks; verify cross-domain AUROC on local data before trusting thresholds.",
        ],
    ),
    DetectorCandidate(
        key="wav2vec-xlsr-gustking",
        name="Wav2Vec2-XLSR deepfake audio classifier (In-the-Wild)",
        task="binary-audio-detector",
        adapter_target="hf-audio-classifier runtime profile",
        status="integrated",
        priority="high",
        source_url="https://huggingface.co/Gustking/wav2vec2-large-xlsr-deepfake-audio-classification",
        notes=[
            "Wired via models/wav2vec-deepfake-audio-runtime.json on the new hf-audio-classifier runtime (AutoFeatureExtractor + AutoModelForAudioClassification, shared run_aasist waveform decoder).",
            "Measured locally (2026-09): real LibriSpeech 89.7% / 8 kHz YESNO 93.0% real, SAPI TTS 91.7% fake — but a modern neural TTS sample (edge-tts) scored only 17.9% fake, so low scores are not evidence of real audio.",
            "Complements AASIST: AASIST caught the edge-tts sample (72.9% spoof) but false-positives on low-bandwidth real speech (97.5% spoof on 8 kHz YESNO); Gustking is the better-calibrated real-speech member.",
            "Rejected candidate: MelodyMachine/Deepfake-audio-detection-V2 returned inverted scores on local samples (real speech -> fake 100%).",
        ],
    ),
    DetectorCandidate(
        key="ftcn-iccv-2021",
        name="FTCN Fully Temporal Convolution Network",
        task="video-temporal-detector",
        adapter_target="future video runtime profile",
        status="research",
        priority="high",
        source_url="https://github.com/yinglinzheng/FTCN",
        notes=[
            "True temporal detector (temporal-transformer over frame features) — the right long-term target for video, unlike the interim frame-level video-frames bridge.",
            "Checkpoint availability and FaceForensics++ training domain must be verified before wiring; do not claim support without a working profile.",
        ],
    ),
    DetectorCandidate(
        key="reenactment-morph-2026",
        name="Face reenactment / morphing public detectors (HF survey)",
        task="face-reenactment-detector",
        adapter_target="future runtime profile",
        status="research",
        priority="medium",
        source_url="https://huggingface.co/models",
        notes=[
            "Surveyed 2026-09-19: no usable public reenactment (Face2Face/FOMM-style) or face-morphing detector checkpoint found on HF — only student demos and unrelated repos.",
            "The honest interim coverage for reenactment is the SBI-trained crop detector (if it validates) plus the temporal-consistency heuristic; revisit when a vetted checkpoint appears.",
        ],
    ),
    DetectorCandidate(
        key="lipforensics-cvpr-2021",
        name="LipForensics high-level visual forensic irregularities",
        task="video-lip-sync-detector",
        adapter_target="future video runtime profile",
        status="research",
        priority="high",
        source_url="https://github.com/ahaliassos/LipForensics",
        notes=[
            "Targets mouth-motion semantics, which the frame-level video-frames path cannot see; needs face-crop preprocessing before scoring.",
            "Verify released weights, preprocessing pipeline, and license before claiming support.",
        ],
    ),
    DetectorCandidate(
        key="altfreezing-cvpr-2023",
        name="AltFreezing alternating spatial-temporal weights",
        task="video-temporal-detector",
        adapter_target="future video runtime profile",
        status="research",
        priority="medium",
        source_url="https://github.com/ZhendongWang6/AltFreezing",
        notes=[
            "Reported strong FaceForensics++ and cross-dataset results; candidate when a dedicated temporal runtime is written.",
            "Do not claim support until weights and license are verified.",
        ],
    ),
    DetectorCandidate(
        key="openai-detector-2019",
        name="OpenAI GPT-2 output detector (RoBERTa-base fine-tune)",
        task="binary-text-detector",
        adapter_target="hf-text-classifier runtime profile",
        status="candidate",
        priority="medium",
        source_url="https://huggingface.co/openai-community/roberta-base-openai-detector",
        notes=[
            "Wired via models/openai-detector-runtime.json; the ~500 MB checkpoint is fetched from Hugging Face on first use (point 'hub_model' at a local snapshot for offline).",
            "Trained on GPT-2 outputs (2019) — OpenAI's model card warns about modern-LLM and short/non-English text; treat as a legacy baseline, re-validate on target-domain samples.",
        ],
    ),
    DetectorCandidate(
        key="fakespot-detector-2024",
        name="Fakespot AI text detector (RoBERTa-base, modern-LLM training data)",
        task="binary-text-detector",
        adapter_target="hf-text-classifier runtime profile",
        status="candidate",
        priority="medium",
        source_url="https://huggingface.co/fakespot-ai/roberta-base-ai-text-detection-v1",
        notes=[
            "Wired via models/fakespot-detector-runtime.json; fetched from Hugging Face on first use like the OpenAI detector.",
            "Trained on newer LLM outputs than the GPT-2-era OpenAI detector — pair them as a two-member ensemble for an agreement signal.",
            "Still English-centric and weak on short text; re-validate on target-domain samples.",
            "Follow-up screening (2026-09) confirmed it is the strongest public text member measured: scores ~0.98 on OOD GPT-2 output and formal Korean AI-style text.",
        ],
    ),
    DetectorCandidate(
        key="hc3-roberta-2023",
        name="Hello-SimpleAI chatgpt-detector-roberta (HC3 corpus)",
        task="binary-text-detector",
        adapter_target="hf-text-classifier runtime profile",
        status="rejected",
        priority="low",
        source_url="https://huggingface.co/Hello-SimpleAI/chatgpt-detector-roberta",
        notes=[
            "Screened 2026-09 and not wired: perfect on its HC3 training domain but scores ~0.00 on out-of-domain inputs (GPT-2 output, Korean AI-style text) — would only dilute the ensemble.",
            "Recorded in experiments/TEXT_DETECTION_EVAL.md (follow-up candidate screening).",
        ],
    ),
    DetectorCandidate(
        key="qwen-ppl-2025",
        name="Qwen2.5-0.5B causal-LM perplexity screen",
        task="zero-shot-text-detector",
        adapter_target="causal-lm-ppl runtime profile",
        status="candidate",
        priority="high",
        source_url="https://huggingface.co/Qwen/Qwen2.5-0.5B",
        notes=[
            "Wired via models/qwen-ppl-runtime.json; generator-agnostic perplexity screen — LLM output sits at low PPL under a different reference LM, covering generators never enumerated (Codex/Claude/Gemini/Grok/Kimi).",
            "The only wired approach that structurally handles Korean (English-trained classifiers cannot read it); anchors [8,60] are provisional until a labeled corpus calibrates them.",
        ],
    ),
    DetectorCandidate(
        key="binoculars-2024",
        name="Binoculars two-LM perplexity-ratio screen (Qwen2.5 pair)",
        task="zero-shot-text-detector",
        adapter_target="binoculars runtime profile",
        status="candidate",
        priority="medium",
        source_url="https://huggingface.co/Qwen/Qwen2.5-1.5B",
        notes=[
            "Wired via models/binoculars-runtime.json; performer PPL over observer cross-PPL on the performer's own picks — a self-normalizing ratio more robust to domain shift than raw PPL.",
            "Generator-agnostic like causal-lm-ppl but ~2x the cost; ratio anchors [0.85,1.05] are provisional until the labeled corpus grows.",
        ],
    ),
    DetectorCandidate(
        key="clide-wacv-2026",
        name="CLIDE Conditional Likelihood generated Image Detector",
        task="zero-shot-image-detector",
        adapter_target="clip-feature sidecar or python runtime profile",
        status="research",
        priority="medium",
        source_url="https://rbetser.github.io/CLIDE/",
        notes=[
            "Zero-shot direction is useful for generators not represented in local training data.",
            "Requires CLIP-style feature extraction; keep optional until dependencies are explicit.",
        ],
    ),
    DetectorCandidate(
        key="dual-path-2026",
        name="Dual-path AI-generated image detection",
        task="patch-global-detector",
        adapter_target="patch heatmap/localization",
        status="research",
        priority="medium",
        source_url="https://github.com/ljppp117/Dual-Path-AI-Generated-Image-Detection",
        notes=[
            "Patch selection over texture-rich and texture-poor regions maps to local heatmap review.",
            "Useful for source-agnostic artifact detection after dataset evaluation is stable.",
        ],
    ),
    DetectorCandidate(
        key="difc-net-2026",
        name="DIFC-Net Diffusion-Intrinsic Feature Capture",
        task="diffusion-detector",
        adapter_target="future neural checkpoint",
        status="research",
        priority="medium",
        source_url="",
        notes=[
            "Diffusion-specific generalization candidate.",
            "Do not claim support until weights and license are verified.",
            "Cited MDPI link returned 403 to automated checks and could not be verified; removed by scripts/check_registry_links.py.",
        ],
    ),
    DetectorCandidate(
        key="out-of-box-benchmark-2026",
        name="Open-source detector out-of-the-box benchmark",
        task="benchmark",
        adapter_target="model selection rubric",
        status="reference",
        priority="high",
        source_url="https://arxiv.org/abs/2604.11487",
        notes=[
            "Use to decide which pretrained detectors deserve local adapter work first.",
            "Emphasizes zero-shot, out-of-the-box behavior across many generators.",
        ],
    ),
]


def list_detector_candidates(*, focus: str | None = None) -> dict[str, object]:
    candidates = DETECTOR_REGISTRY
    if focus:
        needle = focus.lower()
        candidates = [
            candidate
            for candidate in DETECTOR_REGISTRY
            if needle in candidate.task.lower()
            or needle in candidate.adapter_target.lower()
            or needle in candidate.name.lower()
            or needle in candidate.key.lower()
        ]
    return {
        "version": "detector-registry-v1",
        "count": len(candidates),
        "candidates": [candidate.to_json() for candidate in candidates],
    }


def write_detector_registry(path: Path | str, *, focus: str | None = None) -> dict[str, object]:
    payload = list_detector_candidates(focus=focus)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def build_runtime_profile(
    candidate_key: str,
    checkpoint: Path | str,
    *,
    runtime: str | None = None,
    input_size: int = 224,
    score_index: int = 1,
) -> dict[str, object]:
    candidate = _candidate(candidate_key)
    checkpoint_path = Path(checkpoint)
    inferred_runtime = runtime or ("onnx" if checkpoint_path.suffix.lower() == ".onnx" else "torchscript")
    return {
        "type": "deepfake-lens-runtime-profile-v1",
        "name": candidate.name,
        "candidate_key": candidate.key,
        "runtime": inferred_runtime,
        "checkpoint": str(checkpoint_path),
        "input_size": input_size,
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
        "score_index": score_index,
        "score_activation": "softmax",
        "threshold": 67,
        "source_url": candidate.source_url,
        "notes": [
            "Edit input_size, mean/std, input_name, score_index, and threshold after validating the exported checkpoint.",
            "This profile does not bundle weights; it points Deepfake Lens at a local checkpoint.",
        ],
    }


def write_runtime_profile(path: Path | str, candidate_key: str, checkpoint: Path | str, *, runtime: str | None = None, input_size: int = 224, score_index: int = 1) -> dict[str, object]:
    payload = build_runtime_profile(candidate_key, checkpoint, runtime=runtime, input_size=input_size, score_index=score_index)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _candidate(candidate_key: str) -> DetectorCandidate:
    for candidate in DETECTOR_REGISTRY:
        if candidate.key == candidate_key:
            return candidate
    raise ValueError(f"unknown detector candidate: {candidate_key}")
