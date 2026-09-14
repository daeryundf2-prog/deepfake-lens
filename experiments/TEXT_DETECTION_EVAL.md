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
