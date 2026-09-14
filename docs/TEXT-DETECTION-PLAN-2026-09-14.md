# AI Text Detection — Upgrade Plan (2026-09-14)

Basis: controlled probes in `experiments/TEXT_DETECTION_EVAL.md` plus a
507-document sweep of this machine's recent files (user-confirmed ground
truth: most were AI-generated).

## Measured failures to fix

| # | Failure | Evidence |
|---|---|---|
| F1 | Korean AI text undetectable | AI Korean → Fakespot 3 / heuristic signals never fire (TTR 0.98 sits permanently outside the 0.4–0.6 band) |
| F2 | Korean human text false-positives | human Korean → OpenAI detector 98 |
| F3 | Modern LLM English text missed | formal AI English → OpenAI 0 (GPT-2-era training) |
| F4 | Informal/short text evades all | casual AI → 0–2 on both models |
| F5 | Boilerplate false positives | MIT LICENSE files → Fakespot 95+ |
| F6 | Real-world recall ~4% | 8/196 flags on a mostly-AI corpus |

## Phase A — real perplexity engine (the missing primitive)

Today the module explicitly measures *bigram entropy, not LM perplexity* —
the single most informative signal is absent. Add a `causal-lm-ppl`
runtime:

- Load a small multilingual causal LM via transformers, compute mean
  token log-probability over the document. Text written by an LLM sits at
  measurably lower perplexity under a *different* reference LM.
- **Language-agnostic** — perplexity is a token-probability statistic, so
  Korean works without any Korean-specific training data. This is the
  only approach on this list that structurally fixes F1.
- Candidate models (~0.5–1.2 B params, CPU-feasible at ~1–3 s/doc):
  - `Qwen/Qwen2.5-0.5B` (Apache-2.0, strong multilingual incl. Korean)
  - `EleutherAI/polyglot-ko-1.2b` (dedicated Korean LM; pair with the
    Qwen model for cross-model comparison)
- New optional extra `text_lm = ["torch", "transformers"]`; profile
  degrades to `available: false` like every other runtime.
- Output contract: report `ppl`, `mean_logprob`, and a calibrated score —
  raw value in `detail` so calibration is auditable.

## Phase B — stronger zero-shot methods (English accuracy)

- `fast-detectgpt` runtime — conditional-probability curvature, one LM,
  no sampling. Better than raw PPL on English, still single-model cost.
- `binoculars` runtime — performer/observer PPL ratio (two LMs).
  Strongest published zero-shot method; 2× model memory. Optional only.

## Phase C — Korean-aware statistical layer

- Tokenization: whitespace "words" are wrong for agglutinative Korean —
  add 어절-level splitting (조사/어미 strip via regex fallback; optional
  `kiwipiepy` extra for real morphemes).
- Per-language threshold tables for TTR/hapax/MTLD/bigram-entropy,
  calibrated on a labeled Korean corpus (Phase D) — not guessed.
- **Markdown/structure fingerprint family**: numbered-list density,
  bullet density, header/bold/code-fence density, emoji-in-headers.
  Measured tell of agent output — "목록 중심 구성" fired on 3/4
  Antigravity samples but at weight 10 could only nudge. Promote to a
  proper signal group.

## Phase D — labeled benchmark & calibration (gates all claims)

- Fixture: user-confirmed AI corpus (dev-project agent docs) + human
  samples (news/books/user-authored text); Korean + English, formal +
  informal, long + short, plus a boilerplate negative set (licenses —
  fixes F5 measurement).
- `evaluate.py` already reports `per_source`; add `per_language` split.
- Record AUROC/EER per method honestly; calibrate score→band mapping on
  held-out data. **No accuracy claim without this.**

## Phase E — provenance-first fusion

- Text scores stay low-weight in `fusion` profiles; provenance signals
  (C2PA, metadata, agent-tool markers) dominate when present — the
  507-doc sweep showed file context outperforms content analysis.
- Keep the honesty contract: every new score carries limitations; Korean
  scores carry the calibration caveat until Phase D lands.

## Priority & effort

| Phase | Fixes | Effort | Value |
|---|---|---|---|
| A (PPL runtime) | F1, F2, F4 | medium — new runtime + LM download | **highest** — only structural fix for Korean |
| C (Korean heuristics) | F1 partial | small — tokenization + thresholds | high for Korean UX |
| D (benchmark) | gates all claims | medium — corpus assembly | required before trusting anything |
| B (DetectGPT/Binoculars) | F3, F4 | medium-large | English-only marginal gain |
| E (fusion) | precision | small | reduces false positives |

Recommended order: A → C → D → E → B. Phase A+C directly serve the
user's actual corpus (mostly Korean, agent-generated docs).

## Status (2026-09-14, post-implementation)

- **Phase A — done.** `causal-lm-ppl` runtime wired
  (`models/qwen-ppl-runtime.json`, Qwen2.5-0.5B, dual raw/prose views,
  hub- or local-dir loading, graceful degradation). Korean AI prose now
  detected (ppl 8.58 → score 97 vs human Korean 11).
- **Phase C — done.** 어절-aware tokenization (조사/어미 strip) for
  Hangul-heavy text, provisional Korean diversity band, and a proper
  markdown-structure fingerprint signal (`구조화 마크업 밀도`, fires on
  agent-style docs).
- **Phase D — seed corpus landed.** `experiments/text-corpus/` (n=22:
  devin docs, claude samples, Wikipedia human ko/en, MIT boilerplate) +
  `eval_text_detect.py` (AUROC/EER/confusion, per-language and
  per-generator splits). **Not yet a benchmark** — needs real
  Codex/Gemini/Grok/Kimi samples and informal human Korean.
- **Phase E — done (partial).** Text members now fuse by
  reliability-weighted mean via per-profile `ensemble_weight`
  (qwen-ppl 1.0 / fakespot 0.5 / openai-detector 0.25, binoculars 0.25,
  weights from measured behavior, not claimed accuracy). Provenance
  stays dominant.
- **Phase B — done (measured, calibrated low).** `binoculars` runtime
  wired (Qwen2.5-0.5B performer / 1.5B observer, true X-PPL
  cross-entropy after fixing an argmax shortcut, `max_windows` cost
  bound). Measured separation on the seed corpus is weak (AI
  0.67-0.94 vs human 0.77-0.82) — held at weight 0.25. Fast-DetectGPT
  evaluated and deferred: adds cost without evidence it fixes the
  measured failures.

## Measured outcome — honest read

Ensemble on the expanded seed corpus (n=30): AUROC 0.416 overall,
**Korean AUROC 0.845** (informal human Korean separates well),
English AUROC 0.197 (encyclopedic FP). PPL works on formal prose
incl. Korean but not on technical/agent documents, and human
encyclopedic prose false-positives at 51–76. See
`experiments/TEXT_DETECTION_EVAL.md` for the full tables. Remaining
structural gap: technical-doc discrimination needs
structure/provenance signals, not likelihood scores — and the corpus
still lacks real Codex/Gemini/Grok/Kimi outputs.
