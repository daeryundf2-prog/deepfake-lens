"""KGW-style statistical watermark detection for LLM text.

Implements the detector side of the Kirchenbauer et al. (2023) scheme: for
each token, the previous ``context_width`` tokens plus a secret key seed a
hash that partitions the vocabulary into a green list (fraction ``gamma``)
and a red list. Watermarked generators bias toward green tokens, so the
observed green fraction is tested against the binomial null with a z-score.

Two honest boundaries:

- Detection requires knowing the watermarking secret — this is a
  *verification* tool for when the key is known/suspected, not a universal
  scanner. Different secret, gamma, or tokenizer than the generator's means
  the null distribution applies and the test reports "no signal".
- Public frontier models (Codex, Claude, Gemini, Grok, Kimi) do not publish
  KGW keys, so this layer mainly covers self-hosted watermarked generation
  and research corpora. SynthID-style schemes are closed and out of scope.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass

# z > 4 ≈ p < 3e-5 — the standard KGW detection threshold.
_Z_THRESHOLD = 4.0
_MIN_TOKENS = 32


@dataclass(frozen=True)
class WatermarkAnalysis:
    """KGW green-list detection result."""

    available: bool
    score: int  # 0-100 watermark likelihood
    verdict: str
    z_score: float | None
    green_fraction: float | None
    token_count: int
    limitations: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def detect_kgw_watermark(
    text: str,
    *,
    secret: str,
    tokenizer_model: str = "Qwen/Qwen2.5-0.5B",
    gamma: float = 0.25,
    context_width: int = 1,
) -> WatermarkAnalysis:
    """Test text for a KGW watermark under a known secret key.

    ``secret`` is the green-list seed the generator used; without the true
    key the result is a calibrated "no signal", not evidence of absence.
    """
    limitations = [
        "생성 시 사용된 비밀키/γ/토크나이저와 일치해야만 검출됩니다 — 키가 다르면 '신호 없음'이 출력됩니다.",
        "공개 프론티어 모델은 워터마크 키를 공개하지 않아 이 검사는 자체 생성/연구 코퍼스용입니다.",
        "편집/요약된 텍스트는 그린 비율이 희석됩니다.",
    ]
    try:
        from transformers import AutoTokenizer
    except ImportError:
        return _unavailable(limitations, "transformers가 설치되지 않았습니다.")
    if len(text.strip()) < 200:
        return _unavailable(limitations, "텍스트가 너무 짧습니다 — 최소 200자 이상이 필요합니다.")
    try:
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_model)
    except Exception as exc:
        return _unavailable(limitations, f"토크나이저를 불러올 수 없습니다: {exc}")

    token_ids = tokenizer.encode(text, add_special_tokens=False)
    n = len(token_ids)
    if n < _MIN_TOKENS:
        return _unavailable(limitations, f"토큰 {n}개 — {_MIN_TOKENS}개 미만이라 통계 검정이 불가합니다.")

    vocab_size = len(tokenizer)
    green_count = 0
    trials = 0
    for i in range(context_width, n):
        context = token_ids[i - context_width : i]
        if token_ids[i] in _green_list(context, secret, vocab_size, gamma):
            green_count += 1
        trials += 1
    if trials == 0:
        return _unavailable(limitations, "검정 가능한 토큰 위치가 없습니다.")

    expected = gamma * trials
    variance = trials * gamma * (1.0 - gamma)
    z = (green_count - expected) / math.sqrt(variance)
    green_fraction = green_count / trials
    score = min(100, max(0, int((z / 8.0) * 100)))  # z=8 saturates at 100
    if z >= _Z_THRESHOLD:
        verdict = f"그린리스트 비율 {green_fraction:.1%}(기대 {gamma:.0%}), z={z:.1f} — 워터마크 신호가 강합니다."
    elif z >= 2.0:
        verdict = f"그린리스트 비율 {green_fraction:.1%}, z={z:.1f} — 약한 신호, 키/γ 재확인이 필요합니다."
    else:
        verdict = f"그린리스트 비율 {green_fraction:.1%}(기대 {gamma:.0%}) — 이 키 기준 워터마크 신호 없음."
    return WatermarkAnalysis(True, score, verdict, round(z, 2), round(green_fraction, 4), n, limitations)


def _green_list(context: list[int], secret: str, vocab_size: int, gamma: float) -> frozenset[int]:
    """Deterministic green list for a token context + secret seed."""
    digest = hashlib.sha256(
        secret.encode("utf-8") + b":" + b",".join(str(t).encode() for t in context)
    ).digest()
    seed = int.from_bytes(digest[:8], "big")
    rng = random.Random(seed)
    return frozenset(rng.sample(range(vocab_size), int(vocab_size * gamma)))


def _unavailable(limitations: list[str], reason: str) -> WatermarkAnalysis:
    return WatermarkAnalysis(False, 0, f"워터마크 검사 불가 — {reason}", None, None, 0, limitations + [reason])
