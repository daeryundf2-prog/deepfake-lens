from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .model_pins import empty_pin_for


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


# Profile-backed candidates: one per models/*-runtime.json, generated from
# the profiles so the registry cannot drift from what actually ships (G9).
# BEGIN GENERATED: profile candidates (scripts/sync_model_docs.py — do not edit by hand)
_PROFILE_CANDIDATES: list[DetectorCandidate] = [
    DetectorCandidate(
        key="aasist-2022",
        name="AASIST (Interspeech 2022) ASVspoof2019-LA anti-spoofing",
        task="binary-audio-detector",
        adapter_target="aasist runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="https://github.com/clovaai/aasist",
        notes=[
            "프로필 models/aasist-runtime.json (runtime aasist, modality audio).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (sha256 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="swin-ai-image-umm-maybe",
        name="Swin-large AI-vs-human image detector (umm-maybe)",
        task="binary-image-detector",
        adapter_target="hf-image-classifier runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="https://huggingface.co/umm-maybe/AI-image-detector",
        notes=[
            "프로필 models/ai-image-swin-runtime.json (runtime hf-image-classifier, modality image).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (revision 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="aide-iclr-2025",
        name="AIDE (ICLR 2025) progan_train",
        task="binary-image-detector",
        adapter_target="aide runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="https://github.com/shilinyan99/AIDE",
        notes=[
            "프로필 models/aide-runtime.json (runtime aide, modality image).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (sha256 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="community-forensics-vit-s384-frames",
        name="CommunityForensics ViT-S/384 per-frame (video-frames runtime)",
        task="video-frame-detector",
        adapter_target="video-frames runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="https://huggingface.co/Red-had1911/deepfake-detector-onnx (generative_detector.onnx — Community Forensics ViT-S/384, arXiv:2411.04125, MIT)",
        notes=[
            "프로필 models/community-forensics-frames-runtime.json (runtime video-frames → onnx, modality video).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (sha256 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="community-forensics-vit-s384",
        name="CommunityForensics ViT-S/384 (OpenSight) general AI-image detector",
        task="binary-image-detector",
        adapter_target="onnx runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="https://huggingface.co/Red-had1911/deepfake-detector-onnx (generative_detector.onnx — direct export of the original timm checkpoint model_v11_ViT_384_base_ckpt.pt; Community Forensics, Park & Owens U-Michigan, arXiv:2411.04125, MIT)",
        notes=[
            "프로필 models/community-forensics-vit-runtime.json (runtime onnx, modality image).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (sha256 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="fakespot-detector-2024",
        name="Fakespot AI text detector (roberta-base)",
        task="binary-text-detector",
        adapter_target="hf-text-classifier runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="https://huggingface.co/fakespot-ai/roberta-base-ai-text-detection-v1",
        notes=[
            "프로필 models/fakespot-detector-runtime.json (runtime hf-text-classifier, modality text).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (revision 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="sbi-effnet-b0-local",
        name="SBI-trained EfficientNet-B0 v2 (local, enriched self-blend: polygon/affine masks + diverse portraits)",
        task="face-manipulation-detector",
        adapter_target="torchvision runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="local training: experiments/train_detector.py --sbi --augment-degradation with enriched blending (polygon hull masks, affine misalignment) on FFHQ parquet + Wikimedia diverse portraits",
        notes=[
            "프로필 models/sbi-effnet-runtime.json (runtime torchvision, modality image).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (sha256 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="sbi-effnet-b0-frames",
        name="SBI EfficientNet-B0 on per-frame face crops (video-frames runtime)",
        task="video-frame-detector",
        adapter_target="video-frames runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="local training: experiments/train_detector.py on self-blend corpus (see experiments/FACESWAP_EVALUATION.md); frame wrapper uses the committed sbi-effnet-runtime.json inner config",
        notes=[
            "프로필 models/sbi-frames-runtime.json (runtime video-frames → torchvision, modality video).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (sha256 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="sd-turbo-effnet-b0-local",
        name="Local EfficientNet-B0 trained on SD-Turbo fakes vs Hemg reals",
        task="binary-image-detector",
        adapter_target="torchscript runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="local training: experiments/train_detector.py --augment-degradation on experiments/gen_sdturbo_corpus.py output (280 SD-Turbo fakes) + Hemg/deepfake-and-real-images real class",
        notes=[
            "프로필 models/sd-turbo-det-runtime.json (runtime torchscript, modality image).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (sha256 비어 있음); measured_on: 없음.",
        ],
    ),
    DetectorCandidate(
        key="wav2vec-xlsr-gustking",
        name="Wav2Vec2-XLSR deepfake audio classifier (Gustking, In-the-Wild)",
        task="binary-audio-detector",
        adapter_target="hf-audio-classifier runtime profile",
        status="unmeasured",
        priority="n/a",
        source_url="https://huggingface.co/Gustking/wav2vec2-large-xlsr-deepfake-audio-classification",
        notes=[
            "프로필 models/wav2vec-deepfake-audio-runtime.json (runtime hf-audio-classifier, modality audio).",
            "supported: false — 0단계: 측정 게이트(클래스당 200건, AUROC 95% CI 하한 0.85) 미충족 — WP-I 측정 전까지 비활성.",
            "pin: 미고정 (revision 비어 있음); measured_on: 없음.",
        ],
    ),
]
# END GENERATED: profile candidates

# Research / reference candidates and rejected detectors (hand-written).
_OTHER_CANDIDATES = [
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
    DetectorCandidate(
        key="univfd-cvpr-2023",
        name="UnivFD UniversalFakeDetect CLIP ViT-L/14 linear probe",
        task="binary-image-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://github.com/YuhengLi99/UniversalFakeDetect",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
    DetectorCandidate(
        key="cnndetection-cvpr-2020",
        name="CNNDetection ResNet-50 blur+jpg",
        task="binary-image-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://github.com/PeterWang512/CNNDetection",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
    DetectorCandidate(
        key="dire-iccv-2023",
        name="DIRE DIffusion Reconstruction Error",
        task="diffusion-image-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://github.com/ZhendongWang6/DIRE",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
    DetectorCandidate(
        key="vit-face-manipulation-dima806",
        name="ViT deepfake-vs-real face classifier (dima806)",
        task="face-manipulation-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://huggingface.co/dima806/deepfake_vs_real_image_detection",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
    DetectorCandidate(
        key="efficientnet-ffpp-2025",
        name="EfficientNet-B0 face-manipulation detector (FaceForensics++ C23)",
        task="face-manipulation-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://huggingface.co/Xicor9/efficientnet-b0-ffpp-c23",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
    DetectorCandidate(
        key="openai-detector-2019",
        name="OpenAI GPT-2 output detector (RoBERTa-base fine-tune)",
        task="binary-text-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://huggingface.co/openai-community/roberta-base-openai-detector",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
    DetectorCandidate(
        key="qwen-ppl-2025",
        name="Qwen2.5-0.5B causal-LM perplexity screen",
        task="zero-shot-text-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://huggingface.co/Qwen/Qwen2.5-0.5B",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
    DetectorCandidate(
        key="binoculars-2024",
        name="Binoculars two-LM perplexity-ratio screen (Qwen2.5 pair)",
        task="zero-shot-text-detector",
        adapter_target="removed runtime profile",
        status="rejected",
        priority="low",
        source_url="https://huggingface.co/Qwen/Qwen2.5-1.5B",
        notes=[
            "Profile removed in phase 0 (WP-C); the measurement behind the removal is recorded in docs/MODEL-REJECTIONS.md.",
        ],
    ),
]

DETECTOR_REGISTRY = [*_PROFILE_CANDIDATES, *_OTHER_CANDIDATES]


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
    profile: dict[str, object] = {
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
            "Run 'deepfake-lens vendor-weights pin <profile>' to record the checkpoint sha256 — an unpinned profile is refused at load time.",
        ],
        "measured_on": None,
    }
    # G9: weights load only against a pin; the placeholder is filled by
    # 'vendor-weights pin'.
    profile["pin"] = empty_pin_for(profile)
    return profile


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
