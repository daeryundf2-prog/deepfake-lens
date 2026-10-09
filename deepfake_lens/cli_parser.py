"""Argument-parser construction for the CLI.

Extracted from cli.main() — ``main`` owns dispatch; this module owns the
argparse shape so each stays readable as commands are added.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .core import DEFAULT_MAX_FILES
from .corpus_manifest import add_corpus_parser
from .pixel import DEFAULT_PIXEL_MAX_SIDE, SUPPORTED_PIXEL_MODES


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(prog="deepfake-lens", description="로컬 AI 생성·조작 미디어/문서 폴더 검사기 — 결론은 조작·생성 근거 있음 / 원본성 근거 있음 / 판단 불가 세 가지뿐입니다.")
    subparsers = parser.add_subparsers(dest="command")
    scan_parser = subparsers.add_parser("scan", help="폴더 검사(세 가지 결론·근거·검사 범위 기록)")
    scan_parser.add_argument("folder", type=Path, help="검사할 폴더")
    scan_parser.add_argument("--recursive", action="store_true", help="하위 폴더까지 검사(기본은 바로 아래 파일만 — 건너뛴 하위 폴더 수는 결과에 표시됨)")
    scan_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"검사할 최대 파일 수(기본: {DEFAULT_MAX_FILES})")
    scan_parser.add_argument(
        "--include-low",
        "--include-authentic",
        dest="include_low",
        action="store_true",
        help="표에 '원본성 근거 있음' 행도 포함합니다(기본은 생략하고 생략 건수만 표시). "
        "--include-low는 호환을 위해 남긴 이름이며 '낮은 위험' 행이라는 뜻이 아닙니다.",
    )
    scan_parser.add_argument("--format", choices=["table", "json"], default="table", help="표준 출력 형식(table 또는 json)")
    scan_parser.add_argument("--json-out", type=Path, help="전체 JSON 보고서를 이 파일에 저장")
    scan_parser.add_argument("--csv-out", type=Path, help="요약 CSV 보고서를 이 파일에 저장")
    scan_parser.add_argument("--text-bytes", type=int, default=64 * 1024, help="텍스트 파일마다 읽을 최대 바이트 수")
    scan_parser.add_argument("--metadata-bytes", type=int, default=4 * 1024 * 1024, help="이미지 파일마다 메타데이터를 찾으려고 읽을 앞부분 최대 바이트 수")
    scan_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off", help="이미지 픽셀 휴리스틱 실행(참고 신호 전용 — 결론에 참여하지 않음)")
    scan_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE, help=f"픽셀 분석 표본의 최대 변 길이(기본: {DEFAULT_PIXEL_MAX_SIDE})")
    scan_parser.add_argument("--heatmaps", action="store_true", help="--pixel deep 위치 추정 히트맵 PNG 저장")
    scan_parser.add_argument("--heatmap-dir", type=Path, help="히트맵 저장 폴더(기본: $DEEPFAKE_LENS_HEATMAP_DIR 또는 ~/.cache/deepfake-lens/heatmaps/<폴더 키> — 검사 대상 증거 폴더 안에는 쓰지 않음)")
    scan_parser.add_argument("--model-path", type=Path, help="외부 모델 프로필·체크포인트·프로필 폴더(기본: 모델 폴더의 프로필 자동 탐색)")
    scan_parser.add_argument("--no-default-engine", action="store_true", help="기본 엔진 프로필(models/*-runtime.json)을 쓰지 않음")
    scan_parser.add_argument("--fusion-profile", type=Path, help="점수 융합 프로필(참고 점수 전용)")
    scan_parser.add_argument("--cache", type=Path, help="대용량 폴더 재개용 JSON 캐시(내용 해시 기반)")
    scan_parser.add_argument("--workers", type=int, default=1, help="병렬로 분석할 파일 작업자 수")
    scan_parser.add_argument("--dedupe", action="store_true", help="파일 해시로 내용이 같은 파일을 중복으로 표시")
    scan_parser.add_argument("--deep-signals", action="store_true", help="선택형 심층 검사 실행: 이미지 얼굴 조작·인페인팅, 영상 rPPG·아바타·입모양 동기")
    scan_parser.add_argument("--thresholds", type=Path, help="계층 임계값 프로필 JSON(layer-thresholds-v1) — 휴리스틱 기준값을 대체")
    scan_parser.add_argument("--models-dir", type=Path, help="모델 폴더(프로필+가중치). 기본: 패키지 models/ 또는 $DEEPFAKE_LENS_MODELS_DIR")
    scan_parser.add_argument("--law-firm", type=str, default="법무법인(유한) 대륜", help="법원 제출 문서에 적을 법무법인 이름")
    scan_parser.add_argument("--contact", type=str, default="02-780-1128", help="법원 제출 문서에 적을 대표전화")
    scan_parser.add_argument("--center", type=str, default="디지털포렌식 감정센터", help="법원 제출 문서에 적을 감정센터 이름")
    scan_parser.add_argument("--hash-db", type=Path, help="반복 검사 사이에 중복 판정용 해시를 보관할 파일")
    scan_parser.add_argument("--max-file-bytes", type=int, help="이 크기(바이트)보다 큰 파일은 건너뜀(건너뛴 행으로 기록)")
    scan_parser.add_argument("--allow-symlinks", action="store_true", help="심볼릭 링크 파일을 따라가 분석(기본은 따라가지 않고 건너뜀 행으로 기록)")
    scan_parser.add_argument("--progress", action="store_true", help="진행 상황을 표준 오류로 간단히 출력")
    scan_parser.add_argument("--html-out", type=Path, help="HTML 보고서 저장")
    scan_parser.add_argument("--pdf-out", type=Path, help="간이 PDF 보고서 저장")
    scan_parser.add_argument("--forensic-pdf-out", type=Path, help="ECFS 호증 표지와 SHA-256 해시가 들어간 감정 PDF 보고서 저장")
    scan_parser.add_argument("--evidence-statement-out", type=Path, help="ECFS 증거설명서 저장(.pdf → PDF, .json → 서명된 JSON, 그 외 → Markdown; --key-file 또는 DEEPFAKE_LENS_REPORT_KEY로 서명)")
    scan_parser.add_argument("--evidence-statement-pdf-out", type=Path, help="ECFS 증거설명서를 PDF로 저장")
    scan_parser.add_argument("--case-no", type=str, default="(사건번호 입력)", help="증거설명서의 사건번호")
    scan_parser.add_argument("--case-name", type=str, default="성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반", help="증거설명서의 사건명")
    scan_parser.add_argument("--plaintiff", type=str, default="(의뢰사 상호명 입력) 귀하", help="증거설명서의 원고(고소인)")
    scan_parser.add_argument("--defendant", type=str, default="(피고/피의자 성명 입력)", help="증거설명서의 피고(피의자)")
    scan_parser.add_argument("--court", type=str, default="○○지방법원 귀중", help="증거설명서의 제출처(법원/수사기관)")
    scan_parser.add_argument("--exhibit-no", type=str, default="갑 제        호증", help="감정 PDF 보고서의 호증 번호(기본: '갑 제        호증')")
    scan_parser.add_argument("--redact-paths", action="store_true", help="HTML/PDF 보고서의 경로를 파일 이름만 남기고 가림")
    scan_parser.add_argument("--sign", action="store_true", help="--json-out 보고서에 HMAC-SHA256 서명(키 보유자 대상 무결성 확인이며 법적 부인방지는 아님; 키는 --key-file 또는 DEEPFAKE_LENS_REPORT_KEY)")
    scan_parser.add_argument("--key-file", type=Path, help="보고서 서명 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수; 빈 파일이면 오류)")

    collect_parser = subparsers.add_parser("collect", help="write a dataset collection plan")
    collect_parser.add_argument("folder", type=Path)
    collect_parser.add_argument("--out", type=Path, required=True)
    collect_parser.add_argument("--minimum-per-source", type=int)

    dataset_parser = subparsers.add_parser("dataset", help="discover a labeled dataset and write a manifest")
    dataset_parser.add_argument("folder", type=Path)
    dataset_parser.add_argument("--manifest-out", type=Path, required=True)
    dataset_parser.add_argument("--fingerprints", action="store_true", help="include SHA-256 fingerprints in the manifest")
    dataset_parser.add_argument("--audit-out", type=Path, help="write dataset audit JSON")
    dataset_parser.add_argument("--split-out", type=Path, help="write deterministic train/val/test split plan")
    dataset_parser.add_argument("--split-ratios", default="0.8,0.1,0.1", help="train,val,test split ratios")
    dataset_parser.add_argument("--split-seed", default="deepfake-lens-v1")
    dataset_parser.add_argument("--robustness-out", type=Path, help="write robustness transform plan")
    dataset_parser.add_argument("--no-recursive", action="store_true")

    eval_parser = subparsers.add_parser("eval", help="evaluate a labeled dataset")
    eval_parser.add_argument("folder", type=Path)
    eval_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    eval_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE)
    eval_parser.add_argument("--calibration", type=Path)
    eval_parser.add_argument("--model-path", type=Path, help="external model profile (default: auto-discover models/aide-runtime.json)")
    eval_parser.add_argument("--no-default-engine", action="store_true", help="ignore the bundled models/aide-runtime.json default-engine profile")
    eval_parser.add_argument("--fusion-profile", type=Path)
    eval_parser.add_argument("--thresholds", type=Path, help="layer-threshold profile JSON overriding heuristic cutoffs")
    eval_parser.add_argument("--max-files", type=int)
    eval_parser.add_argument("--json-out", type=Path)
    eval_parser.add_argument("--html-out", type=Path)
    eval_parser.add_argument("--false-positive-out", type=Path)
    eval_parser.add_argument("--false-negative-out", type=Path)
    eval_parser.add_argument("--robustness", action="store_true", help="also summarize transform-named robustness folders")
    eval_parser.add_argument("--redact-paths", action="store_true")
    eval_parser.add_argument("--sign", action="store_true", help="HMAC-SHA256 sign the --json-out report (integrity-to-key-holder, not legal non-repudiation; key from --key-file or DEEPFAKE_LENS_REPORT_KEY)")
    eval_parser.add_argument("--key-file", type=Path, help="report signing key file (default: DEEPFAKE_LENS_REPORT_KEY env var)")

    benchmark_parser = subparsers.add_parser("benchmark", help="run a matrix benchmark across pixel modes and model profiles")
    benchmark_parser.add_argument("folder", type=Path)
    benchmark_parser.add_argument("--pixel-modes", default="off,deep", help="comma-separated pixel modes")
    benchmark_parser.add_argument("--model-path", type=Path, action="append", default=[])
    benchmark_parser.add_argument("--fusion-profile", type=Path)
    benchmark_parser.add_argument("--robustness", action="store_true")
    benchmark_parser.add_argument("--max-files", type=int)
    benchmark_parser.add_argument("--json-out", type=Path, required=True)
    benchmark_parser.add_argument("--md-out", type=Path)
    benchmark_parser.add_argument("--sign", action="store_true", help="HMAC-SHA256 sign the --json-out report (integrity-to-key-holder, not legal non-repudiation; key from --key-file or DEEPFAKE_LENS_REPORT_KEY)")
    benchmark_parser.add_argument("--key-file", type=Path, help="report signing key file (default: DEEPFAKE_LENS_REPORT_KEY env var)")

    fusion_parser = subparsers.add_parser("fusion", help="calibrate a metadata/pixel/model/source fusion profile")
    fusion_parser.add_argument("folder", type=Path)
    fusion_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    fusion_parser.add_argument("--model-path", type=Path, help="external model profile (default: auto-discover models/aide-runtime.json)")
    fusion_parser.add_argument("--no-default-engine", action="store_true", help="ignore the bundled models/aide-runtime.json default-engine profile")
    fusion_parser.add_argument("--target-fpr", type=float, default=0.05)
    fusion_parser.add_argument("--max-files", type=int)
    fusion_parser.add_argument("--out", type=Path, required=True)

    calibrate_parser = subparsers.add_parser("calibrate", help="fit a score threshold from a labeled dataset")
    calibrate_parser.add_argument("folder", type=Path)
    calibrate_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    calibrate_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE)
    calibrate_parser.add_argument("--target-fpr", type=float, default=0.05)
    calibrate_parser.add_argument("--max-files", type=int)
    calibrate_parser.add_argument("--out", type=Path, required=True)
    calibrate_parser.add_argument("--mapping-out", type=Path, help="also write an isotonic score-calibration profile (mapping table + method + dataset fingerprint); values are dataset-dependent confidences, not truth probabilities")
    calibrate_parser.add_argument("--thresholds", type=Path, help="layer-threshold profile JSON overriding heuristic cutoffs")

    feedback_parser = subparsers.add_parser("feedback", help="compare examiner labels against scan scores and suggest fusion weights")
    feedback_parser.add_argument("labels", type=Path, help="JSONL/JSON examiner verdicts: {path, expected_label, notes?} per row")
    feedback_parser.add_argument("--scan-json", type=Path, help="prior scan --json-out payload to score against (default: re-analyze each labeled path)")
    feedback_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off")
    feedback_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE)
    feedback_parser.add_argument("--model-path", type=Path, help="external model profile for live rescan (default: auto-discover models/aide-runtime.json)")
    feedback_parser.add_argument("--no-default-engine", action="store_true", help="ignore the bundled models/aide-runtime.json default-engine profile")
    feedback_parser.add_argument("--fusion-profile", type=Path, help="base fusion profile to adjust (default: built-in)")
    feedback_parser.add_argument("--json-out", type=Path, help="write the feedback report JSON")
    feedback_parser.add_argument("--profile-out", type=Path, help="write the suggested fusion profile; apply explicitly via --fusion-profile")

    train_parser = subparsers.add_parser("train", help="train a portable threshold baseline from a labeled dataset")
    train_parser.add_argument("folder", type=Path)
    train_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep")
    train_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE)
    train_parser.add_argument("--target-fpr", type=float, default=0.05)
    train_parser.add_argument("--max-files", type=int)
    train_parser.add_argument("--out", type=Path, required=True)

    models_parser = subparsers.add_parser("models", help="list researched detector integration candidates")
    models_parser.add_argument("--focus", help="filter by task, key, name, or adapter target")
    models_parser.add_argument("--json-out", type=Path)
    models_parser.add_argument("--profile-out", type=Path, help="write a runtime profile for a candidate checkpoint")
    models_parser.add_argument("--candidate", default="aide-iclr-2025")
    models_parser.add_argument("--checkpoint", type=Path)
    models_parser.add_argument("--runtime", choices=["onnx", "torchscript"])
    models_parser.add_argument("--input-size", type=int, default=224)
    models_parser.add_argument("--score-index", type=int, default=1)

    neural_parser = subparsers.add_parser("train-neural-plan", help="write a neural training and ONNX export plan")
    neural_parser.add_argument("folder", type=Path)
    neural_parser.add_argument("--out", type=Path, required=True)
    neural_parser.add_argument("--output-dir", type=Path, required=True)
    neural_parser.add_argument("--architecture", default="convnext_tiny")
    neural_parser.add_argument("--image-size", type=int, default=224)
    neural_parser.add_argument("--epochs", type=int, default=10)

    video_parser = subparsers.add_parser("video", help="plan or run video frame extraction for image scanning")
    video_parser.add_argument("folder", type=Path)
    video_parser.add_argument("--out", type=Path, required=True)
    video_parser.add_argument("--frame-root", type=Path, required=True)
    video_parser.add_argument("--sample-every", type=float, default=2.0)
    video_parser.add_argument("--no-recursive", action="store_true")
    video_parser.add_argument("--extract", action="store_true", help="run ffmpeg commands after writing the plan")
    video_parser.add_argument("--extract-limit", type=int)

    audio_parser = subparsers.add_parser("audio", help="audio acoustic layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    audio_parser.add_argument("file", type=Path, help="audio file to analyze")
    audio_parser.add_argument("--segment-seconds", type=int, default=30, help="maximum seconds to analyze (default: 30)")
    audio_parser.add_argument("--model-path", type=Path, help="external audio model profile (default: auto-discover models/aasist-runtime.json)")
    audio_parser.add_argument("--no-default-engine", action="store_true", help="ignore the bundled models/aasist-runtime.json default-engine profile")
    audio_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    audio_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    face_parser = subparsers.add_parser("face", help="face manipulation layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    face_parser.add_argument("file", type=Path, help="image file to analyze")
    face_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    face_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    video_analysis_parser = subparsers.add_parser("video-analysis", help="video temporal-consistency layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    video_analysis_parser.add_argument("file", type=Path, help="video file to analyze")
    video_analysis_parser.add_argument("--frame-rate", type=float, default=1.0, help="frame sample rate (default: 1.0 fps)")
    video_analysis_parser.add_argument("--max-frames", type=int, default=100, help="maximum frames to analyze (default: 100)")
    video_analysis_parser.add_argument("--model-path", type=Path, nargs="*", help="video-modality model profile(s) — e.g. models/community-forensics-frames-runtime.json scores sampled frames with an image detector")
    video_analysis_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    video_analysis_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    inpaint_parser = subparsers.add_parser("inpaint", help="inpainting / partial-edit layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    inpaint_parser.add_argument("file", type=Path, help="image file to analyze")
    inpaint_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    inpaint_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    text_advanced_parser = subparsers.add_parser("text-advanced", help="text statistics layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    text_advanced_parser.add_argument("file", type=Path, help="text file to analyze")
    text_advanced_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    text_advanced_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    compare_parser = subparsers.add_parser("compare", help="same-speaker / same-author similarity — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    compare_parser.add_argument("file_a", type=Path, help="first file (audio pair or text/document pair)")
    compare_parser.add_argument("file_b", type=Path, help="second file")
    compare_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    compare_parser.add_argument("--ecapa-revision", help="pinned hub commit SHA (40 hex) for the SpeechBrain ECAPA speaker model; default $DEEPFAKE_LENS_ECAPA_REVISION, else ECAPA is not loaded (MFCC fallback)")

    watermark_parser = subparsers.add_parser("watermark", help="test text for a KGW or SynthID watermark under a known secret")
    watermark_parser.add_argument("file", type=Path, help="text/document file to test")
    watermark_parser.add_argument("--secret", help="KGW green-list secret used at generation time")
    watermark_parser.add_argument("--synthid-keys", help="comma-separated SynthID-Text integer keys used at generation (enables SynthID mean-g detection instead of KGW)")
    watermark_parser.add_argument("--tokenizer", default="Qwen/Qwen2.5-0.5B", help="HF tokenizer model or local path")
    watermark_parser.add_argument("--tokenizer-revision", default="", help="pinned 40-hex hub commit for --tokenizer (required; unpinned tokenizers are refused)")
    watermark_parser.add_argument("--gamma", type=float, default=0.25, help="green-list fraction used at generation")
    watermark_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")

    forensic_parser = subparsers.add_parser("forensic", help="three-verdict result (same path as scan) plus the provenance-metadata layer diagnostic")
    forensic_parser.add_argument("file", type=Path, help="file to analyze")
    forensic_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    forensic_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    classify_parser = subparsers.add_parser("classify", help="three-verdict result (same path as scan) plus reference AI-tool marker candidates")
    classify_parser.add_argument("file", type=Path, help="file to analyze")
    classify_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    classify_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    multimodal_parser = subparsers.add_parser("multimodal", help="combined three-verdict result for several files (score inputs are a reference-only layer diagnostic)")
    multimodal_parser.add_argument("files", type=Path, nargs="*", help="files to analyze through the scan path; the combined verdict follows the decision-rule order")
    multimodal_parser.add_argument("--image-score", type=int, help="image raw score (reference only, never a conclusion)")
    multimodal_parser.add_argument("--text-score", type=int, help="text analysis score")
    multimodal_parser.add_argument("--audio-score", type=int, help="audio analysis score")
    multimodal_parser.add_argument("--video-score", type=int, help="video analysis score")
    multimodal_parser.add_argument("--image-source", type=str, help="image source guess")
    multimodal_parser.add_argument("--text-source", type=str, help="text source guess")
    multimodal_parser.add_argument("--audio-source", type=str, help="audio source guess")
    multimodal_parser.add_argument("--video-source", type=str, help="video source guess")
    multimodal_parser.add_argument("--av-sync", type=Path, help="video file for audio/visual sync check (requires opencv+librosa)")
    multimodal_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    multimodal_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    realtime_parser = subparsers.add_parser("realtime", help="moving average of uncalibrated frame scores (layer diagnostic, no verdict)")
    realtime_parser.add_argument("--window-size", type=int, default=30, help="moving average window size (default: 30)")
    realtime_parser.add_argument("--alert-threshold", type=int, default=None, help="optional operator threshold whose crossings are recorded (no default: the former 67 cutoff was never measured)")
    realtime_parser.add_argument("--warning-threshold", type=int, default=None, help="deprecated and ignored (the former 'medium' band no longer exists)")
    realtime_parser.add_argument("--scores", type=str, help="comma-separated scores to process (for testing)")
    realtime_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    realtime_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    rppg_parser = subparsers.add_parser("rppg", help="rPPG pulse layer (CHROM) — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    rppg_parser.add_argument("file", type=Path, help="video file to analyze")
    rppg_parser.add_argument("--max-frames", type=int, default=600, help="maximum face samples to collect (default: 600)")
    rppg_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    rppg_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    prnu_parser = subparsers.add_parser("prnu", help="PRNU sensor-fingerprint correlation layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    prnu_parser.add_argument("file", type=Path, help="target image to check")
    prnu_parser.add_argument("--reference", type=Path, action="append", required=True, help="reference image from the same device (repeat 3+ times)")
    prnu_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    prnu_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    evidence_parser = subparsers.add_parser("evidence", help="create forensic evidence chain")
    evidence_parser.add_argument("file", type=Path, help="file to create evidence for")
    evidence_parser.add_argument("--analyst-id", type=str, default="system", help="analyst identifier")
    evidence_parser.add_argument("--output", type=Path, help="output evidence file")

    api_parser = subparsers.add_parser("api-serve", help="REST API 서버 시작(fastapi/uvicorn 필요)")
    api_parser.add_argument("--host", type=str, default="127.0.0.1", help="바인딩할 호스트")
    api_parser.add_argument("--port", type=int, default=8765, help="수신 포트")
    api_parser.add_argument("--token", type=str, help="/api 요청에 X-API-Token 헤더를 요구(localhost 외 호스트에서는 필수)")
    api_parser.add_argument("--allow-root", type=Path, action="append", default=[], help="API가 읽을 수 있는 폴더(반복 지정 가능); 모든 --allow-root 밖의 경로 요청은 403")

    batch_parser = subparsers.add_parser("batch", help="process files in batch")
    batch_parser.add_argument("folder", type=Path, help="folder to process")
    batch_parser.add_argument("--workers", type=int, default=4, help="number of workers")
    batch_parser.add_argument("--output", type=Path, help="output results file")

    explain_parser = subparsers.add_parser("explain", help="explain which decision rule produced a file's verdict")
    explain_parser.add_argument("file", type=Path, nargs="?", help="file to analyze through the scan path and explain")
    explain_parser.add_argument("--score", type=int, help="deprecated: a raw score alone cannot be explained (layer diagnostic)")
    explain_parser.add_argument("--signals", type=str, help="deprecated: JSON signals array echoed into the --score diagnostic")
    explain_parser.add_argument("--format", choices=["text", "json"], default="text", help="output format")

    agent_parser = subparsers.add_parser("agent", help="three-verdict text result (reference grade) plus the AI-agent marker layer diagnostic")
    agent_parser.add_argument("--text", type=str, help="text to analyze")
    agent_parser.add_argument("--file", type=Path, help="file to analyze")
    agent_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    agent_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    threed_parser = subparsers.add_parser("3d", help="3D-generation marker layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    threed_parser.add_argument("--file", type=Path, help="file to analyze")
    threed_parser.add_argument("--text", type=str, help="text to analyze")
    threed_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    threed_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    avatar_parser = subparsers.add_parser("avatar", help="AI-avatar marker layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    avatar_parser.add_argument("--file", type=Path, help="file to analyze")
    avatar_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    avatar_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    pixel_parser = subparsers.add_parser("pixel-analysis", help="pixel pre-screen layer behind the photo gate — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    pixel_parser.add_argument("file", type=Path, help="image file to analyze")
    pixel_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    pixel_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    ml_parser = subparsers.add_parser("ml-classify", help="feature-threshold rule layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    ml_parser.add_argument("file", type=Path, help="image file to analyze")
    ml_parser.add_argument("--format", choices=["table", "json"], default="json", help="output format")
    ml_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    legal_parser = subparsers.add_parser("legal-report", help="legal forensic report built from the scan result (verdict, evidence, coverage)")
    legal_parser.add_argument("file", type=Path, help="file to analyze")
    legal_parser.add_argument("--output", type=Path, help="write the Korean report text to this file")
    legal_parser.add_argument("--json-out", type=Path, help="write the signed report JSON (verify with verify-report)")
    legal_parser.add_argument("--key-file", type=Path, help="report signing key file (default: DEEPFAKE_LENS_REPORT_KEY env var); without a key the report says 서명 없음")
    legal_parser.add_argument("--analyst-id", type=str, default="system", help="analyst identifier recorded in the report")
    legal_parser.add_argument("--format", choices=["text", "json"], default="text", help="stdout format")

    verify_parser = subparsers.add_parser("verify-report", help="서명된 보고서 JSON 검증(종료 코드 0 검증됨, 1 변조됨, 2 키 ID 불일치, 3 서명 없음, 4 사용 오류)")
    verify_parser.add_argument("report", type=Path, help="서명된 보고서 JSON(scan --json-out --sign, legal-report --json-out, evidence-statement --json-out 등)")
    verify_parser.add_argument("--key-file", type=Path, help="검증 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수; 빈 파일이면 오류)")
    verify_parser.add_argument("--format", choices=["text", "json"], default="text", help="표준 출력 형식")

    perf_parser = subparsers.add_parser("perf", help="measure scan throughput and cache/hash behavior")
    perf_parser.add_argument("folder", type=Path)
    perf_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off")
    perf_parser.add_argument("--workers", type=int, default=1)
    perf_parser.add_argument("--cache", type=Path)
    perf_parser.add_argument("--hash-db", type=Path)
    perf_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    perf_parser.add_argument("--no-recursive", action="store_true")
    perf_parser.add_argument("--out", type=Path, required=True)

    release_parser = subparsers.add_parser("release", help="write a release readiness checklist")
    release_parser.add_argument("--out", type=Path, required=True)

    security_parser = subparsers.add_parser("security", help="write a local-only security guardrail report")
    security_parser.add_argument("--out", type=Path, required=True)

    web_parser = subparsers.add_parser("web", help="로컬 웹 앱(GUI) 시작")
    web_parser.add_argument("--folder", type=Path, help="GUI가 처음 열 증거 폴더(읽기 허용 폴더로 등록됨)")
    web_parser.add_argument("--host", default="127.0.0.1", help="바인딩할 호스트")
    web_parser.add_argument("--port", type=int, default=8765, help="수신 포트")
    web_parser.add_argument("--allow-lan", action="store_true", help="같은 네트워크의 다른 기기 접속 허용(--token 필수)")
    web_parser.add_argument("--token", default=None, help="--allow-lan 때 요구할 API 토큰")
    web_parser.add_argument("--models-dir", type=Path, help="vendor-weights --install로 준비한 모델 폴더")
    web_parser.add_argument("--allow-root", type=Path, action="append", default=[], help="추가로 읽기를 허용할 폴더(반복 지정 가능); --folder/--allow-root 밖의 경로 요청은 403")

    doctor_parser = subparsers.add_parser("doctor", help="모델 가중치·가속기·의존성 진단")
    doctor_parser.add_argument("--format", choices=["table", "json"], default="table", help="표준 출력 형식")
    doctor_parser.add_argument("--json-out", type=Path, help="진단 결과를 JSON으로 저장")
    doctor_parser.add_argument("--models-dir", type=Path, help="진단할 모델 폴더(기본: 패키지 models/ 또는 $DEEPFAKE_LENS_MODELS_DIR)")

    faceswap_parser = subparsers.add_parser("faceswap-seam", help="face-swap boundary seam layer — layer diagnostic: unmeasured reference numbers, no verdict (use scan)")
    faceswap_parser.add_argument("--thresholds", type=Path, help="layer-threshold profile JSON overriding heuristic cutoffs")
    faceswap_parser.add_argument("file", type=Path, help="image file to analyze")
    faceswap_parser.add_argument("--format", choices=["table", "json"], default="table", help="output format")
    faceswap_parser.add_argument("--json-out", type=Path, help="write JSON report to file")

    evidence_stmt_parser = subparsers.add_parser("evidence-statement", help="ECFS 전자소송 증거설명서 작성")
    evidence_stmt_parser.add_argument("target", type=Path, help="검사할 폴더, 검사 결과 JSON 또는 파일 하나")
    evidence_stmt_parser.add_argument("--case-no", type=str, default="(사건번호 입력)", help="사건번호")
    evidence_stmt_parser.add_argument("--case-name", type=str, default="성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반", help="사건명")
    evidence_stmt_parser.add_argument("--plaintiff", type=str, default="(의뢰사 상호명 입력) 귀하", help="원고(고소인)")
    evidence_stmt_parser.add_argument("--defendant", type=str, default="(피고/피의자 성명 입력)", help="피고(피고소인)")
    evidence_stmt_parser.add_argument("--court", type=str, default="○○지방법원 귀중", help="제출처(관할법원/수사관서)")
    evidence_stmt_parser.add_argument("--law-firm", type=str, default="법무법인(유한) 대륜", help="소송대리인 상호(머리글)")
    evidence_stmt_parser.add_argument("--contact", type=str, default="02-780-1128", help="머리글의 대표전화")
    evidence_stmt_parser.add_argument("--center", type=str, default="디지털포렌식 감정센터", help="머리글의 감정센터 이름")
    evidence_stmt_parser.add_argument("--pdf-out", type=Path, help="증거설명서 PDF 저장")
    evidence_stmt_parser.add_argument("--md-out", type=Path, help="증거설명서 Markdown 저장")
    evidence_stmt_parser.add_argument("--json-out", type=Path, help="서명된 증거설명서 JSON 저장(verify-report로 검증)")
    evidence_stmt_parser.add_argument("--key-file", type=Path, help="서명 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수; 빈 파일이면 오류); 키가 없으면 모든 출력에 서명 없음 표시")
    evidence_stmt_parser.add_argument("--format", choices=["table", "json", "markdown"], default="table", help="표준 출력 형식")

    vendor_parser = subparsers.add_parser("vendor-weights", help="망분리 감정실용 모델 가중치 검증·고정·오프라인 묶음")
    vendor_parser.add_argument("action", nargs="?", choices=["pin"], help="'pin <프로필>': 체크포인트 sha256 또는 허브 커밋 revision을 프로필 pin에 기록")
    vendor_parser.add_argument("profile", nargs="?", help="'pin' 대상 프로필 이름(예: aasist) 또는 경로")
    vendor_parser.add_argument("--revision", help="허브 모델 'pin'에서 허브 조회 대신 쓸 40자리 16진 커밋")
    vendor_parser.add_argument("--models-dir", type=Path, help="모델 폴더 경로(기본: 패키지 models/)")
    vendor_parser.add_argument("--verify", action="store_true", help="오프라인 가중치의 SHA-256 무결성 검증")
    vendor_parser.add_argument("--fetch", action="store_true", help="선언된 checkpoint_url 가중치를 내려받아 SHA-256 검증 후 저장")
    vendor_parser.add_argument("--offline", action="store_true", help="모든 네트워크 접근 거부(망분리 모드)")
    vendor_parser.add_argument("--manifest-out", type=Path, help="오프라인 모델 목록 JSON 저장")
    vendor_parser.add_argument("--bundle-to", type=Path, help="오프라인 가중치 묶음을 이 폴더로 내보내기")
    vendor_parser.add_argument("--copy-weights", action="store_true", help="큰 가중치 파일도 묶음 폴더에 복사")
    vendor_parser.add_argument("--force", action="store_true", help="비어 있지 않은 대상 폴더에도 묶음 생성 허용")
    vendor_parser.add_argument("--install", type=Path, metavar="BUNDLE_DIR", help="내려받은 묶음을 --models-dir(또는 --to 대상)에 설치")
    vendor_parser.add_argument("--to", type=Path, help="--install 대상 모델 폴더(기본: --models-dir / DEEPFAKE_LENS_MODELS_DIR)")
    vendor_parser.add_argument("--format", choices=["table", "json", "markdown"], default="table", help="표준 출력 형식")

    # corpus build|split|verify — reproducible corpus manifests (WP-I, G27).
    corpus_parser = add_corpus_parser(subparsers)

    # N8: every command logs per-file failures (with tracebacks) to the log
    # file only; --verbose also prints them on stderr.
    for sub in subparsers.choices.values():
        sub.add_argument("--verbose", action="store_true", help="처리 오류의 상세 로그(트레이스백)를 표준 오류에도 출력(기본: 로그 파일에만 기록)")

    return parser, {
        "corpus": corpus_parser,
        "scan": scan_parser, "collect": collect_parser, "dataset": dataset_parser,
        "eval": eval_parser, "benchmark": benchmark_parser, "fusion": fusion_parser,
        "calibrate": calibrate_parser, "feedback": feedback_parser, "train": train_parser,
        "models": models_parser, "train-neural-plan": neural_parser, "video": video_parser,
        "audio": audio_parser, "face": face_parser, "video-analysis": video_analysis_parser,
        "inpaint": inpaint_parser, "text-advanced": text_advanced_parser, "compare": compare_parser,
        "watermark": watermark_parser, "forensic": forensic_parser, "classify": classify_parser,
        "multimodal": multimodal_parser, "realtime": realtime_parser, "rppg": rppg_parser,
        "prnu": prnu_parser, "evidence": evidence_parser, "api-serve": api_parser,
        "batch": batch_parser, "explain": explain_parser, "agent": agent_parser,
        "3d": threed_parser, "avatar": avatar_parser, "pixel-analysis": pixel_parser,
        "ml-classify": ml_parser, "legal-report": legal_parser, "verify-report": verify_parser, "perf": perf_parser,
        "release": release_parser, "security": security_parser, "web": web_parser,
        "doctor": doctor_parser, "faceswap-seam": faceswap_parser,
        "evidence-statement": evidence_stmt_parser, "vendor-weights": vendor_parser,
    }
