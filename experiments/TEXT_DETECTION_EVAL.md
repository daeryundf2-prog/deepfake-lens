# Text Detection Capability — Measured Boundaries (2026-09-14)

Question reviewed: *can the text path actually tell AI-written text from
human text?* Answer from a controlled 7-sample probe through both wired
`hf-text-classifier` profiles (`openai-detector-runtime.json`,
`fakespot-detector-runtime.json`) plus the statistical layer:

| Sample | Truth | Fakespot | OpenAI det. | Final |
|---|---|---|---|---|
| Formal English AI paragraph | AI | **100** | 0 | 67 |
| Formal Korean AI paragraph | AI | 3 | 77 | 35 |
| Informal/"humanized" AI paragraph | AI | 2 | 0 | 0 |
| One-sentence AI text | AI | 34 | 42 | 0 |
| Human Korean messages | human | 13 | **98 (FP)** | 24 |
| Human English classic (Austen) | human | 49 | 0 | 0 |
| Human informal English | human | 5 | 0 | 0 |

## What works

- **Formal, paragraph-length English AI text**: Fakespot flags it
  decisively (100). This is the one regime where detection is real.

## What fails — and is now on record

- **Modern LLM text vs the OpenAI detector**: it scored formal modern-LLM
  English **0** (its GPT-2-era training distribution) while flagging human
  Korean text **98** — a false positive on non-English input. Use it only
  as an ensemble member, never alone.
- **Korean text is effectively undetectable** with the current zoo: the AI
  Korean paragraph scored 3 (Fakespot) — both classifiers are
  English-trained.
- **Informal register evades everything**: the casual AI paragraph scored
  0–2 on both models. Style transfer/informalization defeats the current
  signal entirely.
- **Short text is noise**: a one-sentence AI sample landed mid-range on
  both (34/42) — inside the documented unstable band.

## Verdict

AI-written text is detectable **only in the narrow regime** of formal,
multi-sentence English — and only via the modern-LLM-trained Fakespot
profile. Korean text, informal text, and short text are not reliably
distinguishable today. The existing honesty notes ("prioritization
signal, not a truth label") are confirmed by measurement; the score must
not be cited as an authenticity verdict, especially for non-English or
short input.

Candidates that would widen the regime (research-only, not wired):
multilingual detectors, perplexity-ratio methods (e.g. Binoculars-style
PPL ratios — needs a reference LM), and watermark checks for providers
that embed them.

## Second probe — Antigravity/Gemini-style output (same day)

Same protocol, samples written in the Antigravity agent's output style
(structured numbered summaries, 습니다체, doc/code blocks):

| Sample | Fakespot | OpenAI det. | Final |
|---|---|---|---|
| English agent change-summary | **93** | 1 | 34 |
| English README-style section | 40 | 0 | 18 |
| Korean agent response (격식체) | 49 | 38 | 10 |
| Korean explanatory answer | 1 | 73 | 0 |

- English Antigravity output is caught by Fakespot (93) — same narrow
  regime as generic formal AI text. The OpenAI detector misses it (1).
- Korean Antigravity output is **not** reliably detectable: scores land
  mid/noise band and invert (Fakespot 1 vs OpenAI 73 on the same file).
- The statistical layer does pick up a stylistic tell — "목록 중심 구성"
  (list-heavy structure) fired on 3/4 samples — but at weight 10 it can
  only nudge the score, never flag it.
- Net: Antigravity-written text is distinguishable only when it is
  formal structured English. Its Korean output is indistinguishable from
  human text to this stack.

## Third probe — causal-LM perplexity runtime + labeled seed corpus

New `causal-lm-ppl` runtime (`models/qwen-ppl-runtime.json`,
Qwen/Qwen2.5-0.5B reference LM, 512-token windows, dual raw/prose views)
evaluated on the labeled seed corpus
(`experiments/text-corpus/manifest.json`, n=22) via
`experiments/eval_text_detect.py`. Fusion across text members is now a
reliability-weighted mean (`ensemble_weight` per profile: qwen-ppl 1.0,
fakespot 0.5, openai-detector 0.25 — weights set from the measurements
above, not claimed accuracy).

### Perplexity alone (direct runtime output)

| Sample | PPL | Score | Reading |
|---|---:|---:|---|
| AI Korean formal | 8.58 | 97 | detected — first working Korean signal |
| AI English formal | 12.96 | 76 | detected |
| Antigravity-style Korean | 19.30 | 56 | ambiguous |
| Human Korean (informal) | 48.22 | 11 | correct |
| Human informal English | 32.03 | 31 | correct |
| Humanized AI English | 107.31 | 0 | missed |
| Austen (classic literary) | 8.97 | 94 | **FP** — polished prose is low-PPL |
| MIT license boilerplate | 2.07 | 100 | **FP** — formulaic text is low-PPL |

### Ensemble on the seed corpus (fused score, threshold 50)

- Overall AUROC 0.325, EER 0.592, recall 0.25, FPR 0.20 — **worse than
  random ranking** on this mixed corpus, driven by two failure poles:
- **Devin/agent technical docs score ~18** (missed): markdown structure,
  identifiers, tables and file paths are high-PPL even after prose
  extraction (devin_doc0: raw ppl=61, prose ppl=131). PPL measures prose
  fluency; technical agent documents are not fluent prose. The markdown
  structure signal (weight 14) and list-structure signals fire but
  cannot lift the fused score past the flag band.
- **Human encyclopedic text scores 24–76** (false positives): Wikipedia
  is exactly the low-PPL formal prose the detector flags. The French
  Revolution article hit 76 — a real FP at the shipped threshold.

### What this probe established

1. Korean AI **prose** is now detectable for the first time (97 vs 11),
   the goal that motivated the runtime.
2. PPL's operating regime is *formal natural prose* — it does not extend
   to technical/agent documents, humanized text, boilerplate, or
   literary prose. Those regimes need structure/provenance signals.
3. The corpus must grow (real Codex/Gemini/Grok/Kimi samples, informal
   human Korean, human technical docs) before any threshold or accuracy
   claim. n=22 is a smoke corpus; AUROC here is directional only.
4. Threshold 50 currently trades recall 0.25 for FPR 0.20 on this mix —
   not deployable as a verdict, consistent with the "prioritization
   signal" contract.

## Fourth probe — binoculars runtime + expanded corpus (n=30)

`binoculars` runtime (`models/binoculars-runtime.json`): performer
Qwen2.5-0.5B log-PPL over observer Qwen2.5-1.5B cross-entropy on the
performer's next-token distribution (Hans et al. 2024 style). First
implementation used argmax picks instead of the distribution
expectation — corrected to true X-PPL cross-entropy H(M1,M2) after the
measured ratios came out inverted (1.5-2.0 instead of ~0.6-1.1).

Raw ratios measured (anchors 0.85/1.05 provisional):

| Sample | ratio | reading |
|---|---:|---|
| AI Korean formal | 0.673 | lowest — detected |
| AI English formal | 0.938 | weak |
| AI devin technical doc | 1.001 | missed |
| Human ko wiki | 0.772 | FP under provisional anchors |
| Human ko talk (informal) | 0.818 | FP |
| Human en wiki | 0.786 | FP |

Measured separation is **weak** — human formal/informal prose clusters
0.77-0.82, overlapping the AI band; `ensemble_weight` is therefore held
at 0.25 and anchors marked uncalibrated. Cost bounded by
`max_windows=4` (raw view only — the X-PPL denominator self-normalizes
markup).

### Ensemble on expanded corpus (n=30: +informal human Korean talk
pages, +informal/short/technical/news AI samples)

- Overall: AUROC 0.416, recall 0.294 @ FPR 0.231 (threshold 50)
- **Korean: AUROC 0.845, recall 0.667 @ FPR 0.286** — informal human
  Korean (talk pages) separates from AI Korean better than encyclopedic
  prose does; the strongest measured split so far
- English: AUROC 0.197 — encyclopedic human English continues to
  out-score AI text (wiki FP 76 vs several AI at 18-42)
- Claude recall 5/11, devin technical docs 0/6 (structural miss —
  confirmed again), MIT boilerplate now scores 24 (correctly low under
  the weighted fusion)

### Standing conclusions

- Text content alone detects **formal prose** AI (incl. Korean now) but
  cannot separate technical/agent documents or encyclopedic human
  prose. Structure/provenance signals remain the load-bearing evidence
  for the user's real corpus.
- Fast-DetectGPT was evaluated and deferred: a second uncalibrated
  zero-shot method adds cost without evidence it fixes the measured
  failures (technical docs, humanized text).
