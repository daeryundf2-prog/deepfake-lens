#!/usr/bin/env python3
"""Build the labeled evaluation corpus used by experiments/eval_all.py.

    python scripts/build_eval_corpus.py --out <corpus_dir> [--parts text,audio] \
        --qwen-revision <40-hex commit> --wikipedia-revision <40-hex commit>

The two hub revisions are required for the text part (G10): the human side
streams wikimedia/wikipedia and the AI side loads
Qwen/Qwen2.5-0.5B-Instruct at exactly those commits, so the corpus can be
rebuilt byte-for-byte later. There is no default revision; look the
current one up with ``huggingface-cli`` / the hub "Files" tab and record it
with the corpus manifest.

Corpus layout (matches eval_all.py):

    <out>/text/real/*.txt    Korean Wikipedia paragraphs (wikimedia/wikipedia)
    <out>/text/fake/*.txt    local Qwen2.5-0.5B-Instruct generations
    <out>/audio/real/*       torchaudio YESNO real speech
    <out>/audio/fake/*       edge-tts neural TTS (ko+en voices)

Face/image corpora are built by their own scripts:
    scripts/fetch_diverse_faces.py   (faces, incl. SBI fake synthesis)
    experiments/build synthetic sets (Synthbuster etc. via fetch_benchmark.py)

Corpus content is NOT committed — only this script is. Regeneration needs
network access for the human/real sides and local CPU inference for the
AI/TTS sides (Qwen ~1 GB, edge-tts network).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

QWEN_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
WIKIPEDIA_DATASET = "wikimedia/wikipedia"
WIKIPEDIA_CONFIG = "20231101.ko"
# A full 40-hex hub commit — branch names ("main") move and are refused.
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _commit(value: str) -> str:
    if not COMMIT_SHA.match(value or ""):
        raise argparse.ArgumentTypeError("40자 16진수 허브 커밋 SHA가 필요합니다(브랜치/태그 불가) — G10")
    return value


def build_text(out: Path, n_human: int, n_ai: int, *, qwen_revision: str, wikipedia_revision: str) -> None:
    real_dir, fake_dir = out / "text" / "real", out / "text" / "fake"
    real_dir.mkdir(parents=True, exist_ok=True)
    fake_dir.mkdir(parents=True, exist_ok=True)

    print("[corpus] text/real: Korean Wikipedia (streaming)...", flush=True)
    from datasets import load_dataset
    ds = load_dataset(WIKIPEDIA_DATASET, WIKIPEDIA_CONFIG, split="train", streaming=True, revision=wikipedia_revision)
    n = 0
    for row in ds:
        for para in row.get("text", "").split("\n"):
            para = para.strip()
            if not (200 <= len(para) <= 1500):
                continue
            hangul = sum(1 for c in para if "가" <= c <= "힣")
            if hangul / max(1, len(para)) < 0.3:
                continue
            (real_dir / f"human_{n:04d}.txt").write_text(para, encoding="utf-8")
            n += 1
            if n >= n_human:
                break
        if n >= n_human:
            break
    print(f"  human: {n}", flush=True)

    print("[corpus] text/fake: local Qwen2.5-0.5B-Instruct...", flush=True)
    import random
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(QWEN_MODEL, revision=qwen_revision)
    model = AutoModelForCausalLM.from_pretrained(QWEN_MODEL, revision=qwen_revision, dtype=torch.float32).eval()
    prompts = [
        "다음 주제에 대해 3문단짜리 설명문을 써줘: {}",
        "{}에 대해 블로그 글처럼 자연스럽게 써줘",
        "뉴스 기사 형식으로 {}에 대한 기사를 작성해줘",
        "{}에 대해 초등학생도 이해할 수 있게 설명해줘",
        "{}에 대한 나의 생각을 일기처럼 써줘",
        "{}에 대한 보고서를 작성해줘",
        "친구에게 {}를 설명하는 메시지를 써줘",
        "{}에 대해 SNS에 올릴 짧은 글을 써줘",
    ]
    topics = [
        "인공지능의 미래", "기후 변화", "한국 경제", "K-팝의 세계화", "우주 탐사",
        "건강한 식습관", "재택근무", "전기차", "인터넷 개인정보", "독서의 중요성",
        "한국 전통 문화", "스마트폰 중독", "지역 축제", "청년 실업", "반려동물",
        "제주도 여행", "한글날의 의미", "미세먼지", "온라인 교육", "고령화 사회",
    ]
    rng = random.Random(0)
    kept = 0
    for i in range(n_ai * 2):  # some samples drop below the length filter
        if kept >= n_ai:
            break
        prompt = rng.choice(prompts).format(rng.choice(topics))
        ids = tok.apply_chat_template(
            [{"role": "user", "content": prompt}],
            add_generation_prompt=True, return_tensors="pt", return_dict=True)
        n_in = ids["input_ids"].shape[1]
        with torch.no_grad():
            gen = model.generate(
                **ids, max_new_tokens=rng.choice([150, 220, 300]),
                do_sample=True, temperature=rng.choice([0.7, 0.9, 1.0]),
                top_p=0.9, pad_token_id=tok.eos_token_id)
        text = tok.decode(gen[0][n_in:], skip_special_tokens=True).strip()
        if len(text) > 150:
            (fake_dir / f"ai_{kept:04d}.txt").write_text(text, encoding="utf-8")
            kept += 1
        if i % 20 == 0:
            print(f"  gen {i}, kept {kept}", flush=True)
    print(f"  ai: {kept}", flush=True)


def build_audio(out: Path) -> None:
    real_dir, fake_dir = out / "audio" / "real", out / "audio" / "fake"
    real_dir.mkdir(parents=True, exist_ok=True)
    fake_dir.mkdir(parents=True, exist_ok=True)

    print("[corpus] audio/real: torchaudio YESNO...", flush=True)
    import torchaudio
    ds = torchaudio.datasets.YESNO(str(out / "_yesno"), download=True)
    import wave
    for i in range(min(12, len(ds))):
        waveform, sr, *_ = ds[i]
        pcm = (waveform[0] * 32767).clamp(-32768, 32767).to("int16")
        with wave.open(str(real_dir / f"yesno_{i:02d}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.numpy().tobytes())
    print("  real: 12 YESNO clips", flush=True)

    print("[corpus] audio/fake: edge-tts voices...", flush=True)
    import asyncio
    import edge_tts
    texts = {
        "ko": "요즘 인공지능 기술이 빠르게 발전하고 있습니다. 특히 음성 합성 기술은 실제 사람의 목소리와 구분하기 어려울 정도로 자연스러워졌습니다.",
        "en": "Artificial intelligence technology is advancing rapidly. Modern speech synthesis has become nearly indistinguishable from a real human voice.",
    }
    voices = ["ko-KR-SunHiNeural", "ko-KR-InJoonNeural", "ko-KR-HyunsuMultilingualNeural",
              "en-US-AriaNeural", "en-US-GuyNeural", "en-GB-SoniaNeural"]

    async def gen() -> None:
        for i, voice in enumerate(voices):
            lang = "ko" if voice.startswith("ko") else "en"
            await edge_tts.Communicate(texts[lang], voice).save(
                str(fake_dir / f"tts_{i:02d}_{voice}.mp3"))
    asyncio.run(gen())
    print(f"  fake: {len(voices)} edge-tts clips", flush=True)


def main(argv: list[str] | None = None) -> int:
    _repo = Path(__file__).resolve().parents[1]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="텍스트·음성 평가 코퍼스(사람/AI)를 허브 revision을 고정해 만듭니다.")
    parser.add_argument("--out", required=True, type=Path, help="코퍼스를 쓸 폴더")
    parser.add_argument("--parts", default="text,audio", help="만들 부분(쉼표로 구분한 `text`·`audio`; 기본: `text,audio`)")
    parser.add_argument("--n-human", type=int, default=400, help="사람이 쓴 표본 수(기본: 400)")
    parser.add_argument("--n-ai", type=int, default=200, help="AI 생성 표본 수(기본: 200)")
    parser.add_argument("--qwen-revision", type=_commit, help=f"{QWEN_MODEL} 허브 커밋(--parts text에 필수)")
    parser.add_argument("--wikipedia-revision", type=_commit, help=f"{WIKIPEDIA_DATASET} 허브 커밋(--parts text에 필수)")
    args = parser.parse_args(argv)
    parts = {p.strip() for p in args.parts.split(",")}

    if "text" in parts:
        missing = [flag for flag, value in (("--qwen-revision", args.qwen_revision), ("--wikipedia-revision", args.wikipedia_revision)) if not value]
        if missing:
            parser.error(f"--parts text에는 {', '.join(missing)}가 필요합니다(허브 revision 고정, G10)")
        build_text(args.out, args.n_human, args.n_ai, qwen_revision=args.qwen_revision, wikipedia_revision=args.wikipedia_revision)
    if "audio" in parts:
        build_audio(args.out)
    print("[corpus] done ->", args.out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
