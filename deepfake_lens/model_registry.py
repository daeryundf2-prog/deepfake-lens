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
            "표시 이름: AASIST 음성 위조 탐지기(ASVspoof2019-LA).",
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
            "표시 이름: Swin-large 생성 이미지 탐지기(umm-maybe).",
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
            "표시 이름: AIDE 생성 이미지 탐지기(ICLR 2025, progan_train).",
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
            "표시 이름: CommunityForensics ViT-S/384 영상 프레임 생성 탐지기.",
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
            "표시 이름: CommunityForensics ViT-S/384 생성 이미지 탐지기.",
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
            "표시 이름: Fakespot AI 텍스트 탐지기(roberta-base).",
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
            "표시 이름: SBI EfficientNet-B0 얼굴 조작 탐지기(로컬 학습 v2).",
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
            "표시 이름: SBI EfficientNet-B0 영상 얼굴 프레임 탐지기.",
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
            "표시 이름: SD-Turbo 생성 이미지 탐지기(로컬 EfficientNet-B0).",
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
            "표시 이름: Wav2Vec2-XLSR 딥페이크 음성 분류기(In-the-Wild).",
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
            "강건성 평가 대상으로 사용: 변환·재압축·리사이즈·블러·크롭 이미지.",
            "챌린지 보고서는 벤치마크·방법 조사이며 재사용 가능한 단일 체크포인트가 아닙니다.",
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
            "진짜 시간축 탐지기(프레임 특징 위의 temporal-transformer) — 임시 프레임 단위 video-frames 경로와 달리 영상의 장기 목표입니다.",
            "연결 전에 체크포인트 공개 여부와 FaceForensics++ 학습 도메인을 확인해야 하며, 동작하는 프로필 없이 지원한다고 표시하지 마십시오.",
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
            "2026-09-19 조사: HF에서 쓸 만한 공개 재연(Face2Face/FOMM 계열)·얼굴 모핑 탐지 체크포인트를 찾지 못했습니다 — 학생 데모와 무관한 저장소뿐이었습니다.",
            "재연 유형의 임시 범위는 SBI로 학습한 크롭 탐지기(검증될 경우)와 시간축 일관성 휴리스틱뿐이며, 검증된 체크포인트가 나오면 다시 검토합니다.",
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
            "프레임 단위 video-frames 경로가 볼 수 없는 입 움직임 의미를 다루며, 채점 전에 얼굴 크롭 전처리가 필요합니다.",
            "지원을 표시하기 전에 공개 가중치, 전처리 파이프라인, 라이선스를 확인하십시오.",
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
            "FaceForensics++와 교차 데이터셋 결과가 좋다고 보고되었습니다. 전용 시간축 런타임을 작성할 때의 후보입니다.",
            "가중치와 라이선스를 확인하기 전에는 지원한다고 표시하지 마십시오.",
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
            "2026-09 선별 후 연결하지 않음: HC3 학습 도메인 밖 입력(GPT-2 출력, 한국어 AI 문체 글)에서 반응하지 않아 앙상블을 희석할 뿐입니다(소규모 측정, 미검증).",
            "experiments/TEXT_DETECTION_EVAL.md(후속 후보 선별)에 기록되어 있습니다.",
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
            "로컬 학습 데이터에 없는 생성기에는 제로샷 방향이 유용합니다.",
            "CLIP 계열 특징 추출이 필요하므로 의존성이 명확해질 때까지 선택 사항으로 둡니다.",
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
            "질감이 많은 영역과 적은 영역의 패치 선택이 로컬 히트맵 검토와 대응됩니다.",
            "데이터셋 평가가 안정된 뒤 출처와 무관한 흔적 탐지에 유용합니다.",
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
            "확산 모델 특화 일반화 후보.",
            "가중치와 라이선스를 확인하기 전에는 지원한다고 표시하지 마십시오.",
            "인용된 MDPI 링크가 자동 검사에서 403을 반환해 확인할 수 없었으며, scripts/check_registry_links.py로 제거했습니다.",
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
            "어떤 사전학습 탐지기에 로컬 어댑터 작업을 먼저 할지 정하는 데 씁니다.",
            "여러 생성기에 대한 제로샷·즉시 사용 동작을 강조합니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "0단계(WP-C)에서 프로필을 삭제했습니다. 삭제 근거가 된 측정은 docs/MODEL-REJECTIONS.md에 기록되어 있습니다.",
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
            "내보낸 체크포인트를 검증한 뒤 input_size, mean/std, input_name, score_index, threshold를 수정하십시오.",
            "이 프로필은 가중치를 포함하지 않으며 Deepfake Lens가 로컬 체크포인트를 가리키게 할 뿐입니다.",
            "'deepfake-lens vendor-weights pin <프로필>'로 체크포인트 sha256을 기록하십시오 — 고정되지 않은 프로필은 로드 시 거부됩니다.",
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
