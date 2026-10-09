#!/usr/bin/env python3
"""Create the directory skeleton for the five phase-1 evaluation tracks.

Phase 0, WP-I (G27): measurements must come from reproducible corpora laid
out so ``deepfake-lens corpus build <track> --label-from-dir`` can read
label/generator/variant from the path::

    <out>/<track>/<label>/<generator>/<variant>/<file>

This script only creates directories and README placeholders (no media).
Each README lists the classes the track needs and the sample counts the
measurement gate implies (scripts/check_measurement_gate.py): at least 200
items per class in the *test* split, i.e. at least 1,000 independent
originals per class at the default 60/20/20 split — messenger/recompression
variants of one original stay in the same split and do not count as
independent samples.

    python scripts/build_corpus_template.py --out corpora/

Existing READMEs are kept unless --force is given.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Gate constants mirrored from deepfake_lens/measurement_gate.py (spec 게이트 0).
MIN_TEST_PER_CLASS = 200
DEFAULT_TEST_SHARE = 0.2
MIN_ORIGINALS_PER_CLASS = int(MIN_TEST_PER_CLASS / DEFAULT_TEST_SHARE)  # 1,000


@dataclass(frozen=True)
class TrackClass:
    label: str  # corpus-manifest-v1 label: real | synthetic | edited
    description: str
    example_generators: tuple[str, ...]


@dataclass(frozen=True)
class Track:
    track_id: str
    modality: str
    purpose: str
    classes: tuple[TrackClass, ...]
    variants: tuple[str, ...]
    notes: tuple[str, ...] = ()


MESSENGER_VARIANTS = ("original", "kakao", "telegram", "instagram", "jpeg_q50")

TRACKS: tuple[Track, ...] = (
    Track(
        "T-IMG",
        "image",
        "사진의 생성·조작 여부 (G14–G16, G17, QA-ADV-1/2)",
        (
            TrackClass("real", "카메라 원본 사진(기종·촬영 조건 다양화)", ("galaxy-s23", "iphone-15", "dslr")),
            TrackClass("synthetic", "이미지 생성 모델 출력", ("midjourney-v6", "dalle3", "sdxl", "flux")),
            TrackClass("edited", "실제 사진의 부분 조작(페이스스왑, 인페인팅, 합성)", ("inswapper", "sd-inpaint", "photoshop")),
        ),
        MESSENGER_VARIANTS,
        ("사진이 아닌 이미지(스크린샷·그래픽·문서 스캔)는 생성 탐지 비적용이므로 이 트랙의 real/synthetic에 넣지 않는다(WP-D).",),
    ),
    Track(
        "T-VID",
        "video",
        "영상의 생성·조작 여부 (G18–G20)",
        (
            TrackClass("real", "카메라·휴대폰 원본 영상", ("galaxy-s23", "iphone-15", "webcam")),
            TrackClass("synthetic", "영상 생성 모델 출력", ("sora", "veo", "kling")),
            TrackClass("edited", "실제 영상의 얼굴 교체·재연·립싱크 조작", ("faceswap", "reenactment", "wav2lip")),
        ),
        ("original", "kakao", "telegram", "instagram", "h264_crf35"),
    ),
    Track(
        "T-AUD",
        "audio",
        "음성의 합성·편집 여부 (G21)",
        (
            TrackClass("real", "실제 녹음 음성(통화·녹음기·메신저 음성메시지)", ("phone-call", "recorder", "voice-message")),
            TrackClass("synthetic", "TTS·음성 변환 출력", ("elevenlabs", "xtts", "rvc")),
            TrackClass("edited", "실제 녹음의 구간 삭제·삽입·이어붙이기", ("splice", "insert", "delete")),
        ),
        ("original", "kakao", "telegram", "mp3_64k"),
    ),
    Track(
        "T-DOC",
        "document",
        "문서의 작성 이력·변조 여부 (G22, G23)",
        (
            TrackClass("real", "작성 이력이 보존된 원본 문서(PDF·DOCX·HWP)", ("hwp", "docx", "pdf-scan")),
            TrackClass("synthetic", "생성형 도구로 작성된 문서", ("llm-drafted", "template-generated")),
            TrackClass("edited", "작성 후 내용·메타데이터·서명이 변경된 문서", ("incremental-update", "metadata-edit", "signature-break")),
        ),
        ("original", "print_to_pdf", "messenger_resend"),
    ),
    Track(
        "T-TXT",
        "text",
        "텍스트 생성 여부 — 참고 등급 전용 (G24, QA-OUT-6, QA-ADV-3)",
        (
            TrackClass("real", "사람이 쓴 글(AI에 관해 쓴 글, '언어 모델'·'as an AI' 포함 글 포함)", ("human-ko", "human-en")),
            TrackClass("synthetic", "LLM이 생성한 글", ("gpt", "claude", "gemini", "hyperclova")),
        ),
        ("original", "paraphrased", "translated"),
        ("텍스트는 AUROC 하한 대신 FPR 1%에서의 재현율(recall_at_fpr_0_01)을 보고한다. 결론 등급은 항상 '참고'다.",),
    ),
)


def track_readme(track: Track) -> str:
    lines = [
        f"# {track.track_id} — {track.modality}",
        "",
        "> ⚠ 이 디렉터리는 자리표시자다. 파일을 채운 뒤 `deepfake-lens corpus build <이 디렉터리> --out manifest.json --label-from-dir`,",
        "> `deepfake-lens corpus split --manifest manifest.json --seed <N> --ratio 60/20/20`, `deepfake-lens corpus verify --manifest manifest.json` 순서로 매니페스트를 만든다.",
        "",
        f"목적: {track.purpose}",
        "",
        "경로 규칙: `<label>/<generator>/<variant>/<파일>` — label은 real | synthetic | edited.",
        "변형(variant)은 같은 원본 파일과 같은 파일명(확장자 제외)을 쓰고 `original/` 옆에 둔다. 그러면 build가 `derived_from`을 채우고 split이 원본과 변형을 같은 분할에 넣는다.",
        "",
        "## 필요한 클래스와 수량",
        "",
        "| label | 내용 | 생성기/출처 예시 | 최소 독립 원본 수 | test 분할 최소 |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for cls in track.classes:
        lines.append(
            f"| {cls.label} | {cls.description} | {', '.join(cls.example_generators)} | "
            f"{MIN_ORIGINALS_PER_CLASS:,} | {MIN_TEST_PER_CLASS} |"
        )
    lines += [
        "",
        f"수량 근거: 측정 게이트(scripts/check_measurement_gate.py)는 test 분할에서 클래스당 {MIN_TEST_PER_CLASS}개 이상을 요구한다. "
        f"60/20/20 분할이면 독립 원본이 클래스당 {MIN_ORIGINALS_PER_CLASS:,}개 이상 필요하다. 변형 파일은 독립 표본으로 세지 않는다.",
        "",
        f"변형(variant) 목록: {', '.join(f'`{v}`' for v in track.variants)}",
        "",
        "## 출처 기록",
        "",
        "각 생성기 디렉터리에 수집 경위(출처 URL, 라이선스, 수집일, 생성 프롬프트·설정)를 기록하고 `--source-note`로 매니페스트에 남긴다.",
    ]
    for note in track.notes:
        lines += ["", f"주의: {note}"]
    return "\n".join(lines) + "\n"


def root_readme() -> str:
    lines = [
        "# 1단계 평가 코퍼스",
        "",
        "> ⚠ 미검증(n 부족 또는 재현 불가) — 2차 계획 WP-I 참조. 이 디렉터리의 트랙이 채워지고 corpus-manifest-v1 매니페스트로 고정되기 전까지는 어떤 성능 수치도 증거로 사용할 수 없다.",
        "",
        "| 트랙 | 모달리티 | 클래스 |",
        "| --- | --- | --- |",
    ]
    for track in TRACKS:
        lines.append(f"| {track.track_id} | {track.modality} | {', '.join(c.label for c in track.classes)} |")
    lines += [
        "",
        "측정 결과는 프로필의 `measured_on`에 corpus_id, manifest_sha256, manifest_path(매니페스트 파일 경로), split=\"test\", n_pos, n_neg, auroc, auroc_ci, "
        "fpr_at_threshold, recall_at_threshold, measured_at으로 기록한다.",
    ]
    return "\n".join(lines) + "\n"


def build_template(out: Path, *, force: bool = False) -> list[Path]:
    """Create directories and READMEs; return the README paths written."""
    written: list[Path] = []

    def write(path: Path, text: str) -> None:
        if path.exists() and not force:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    out.mkdir(parents=True, exist_ok=True)
    write(out / "README.md", root_readme())
    for track in TRACKS:
        track_dir = out / track.track_id
        for cls in track.classes:
            (track_dir / cls.label).mkdir(parents=True, exist_ok=True)
        write(track_dir / "README.md", track_readme(track))
    return written


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    _repo = Path(__file__).resolve().parents[1]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="1단계 평가 트랙 다섯 개의 코퍼스 폴더 골격을 만듭니다.")
    parser.add_argument("--out", type=Path, required=True, help="트랙 골격을 만들 폴더")
    parser.add_argument("--force", action="store_true", help="기존 README 자리표시 파일을 덮어씀")
    args = parser.parse_args(argv)
    written = build_template(args.out, force=args.force)
    print(f"트랙 {len(TRACKS)}개 골격 생성: {args.out} (README {len(written)}개 기록)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
