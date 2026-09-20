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

## Fifth probe — frontier-LLM fingerprint signals

Four model-agnostic fingerprint families added to the statistical layer
(these target *how* frontier models write, independent of which vendor):

| Signal | Mechanism | Fires on |
|---|---|---|
| `타이포그래픽 구두점` | em-dash/curly quotes/ellipsis density — humans type ASCII, models emit Unicode | agent/prose output ≥6 chars, >0.2% |
| `LLM 과용 어휘` | delve/crucial/realm/tapestry/moreover family + Korean connector set, per-1000-word density | formal model prose |
| `양면 균형 헤징 구조` | both-sides scaffold pairs (한편/반면, on one hand/other hand) | assistant-style answers |
| `문두 접속사 균일성` | >30% of sentences start with a discourse connector | model-scaffolded writing |

Plus `burst_cv` (per-window NLL coefficient of variation — the
GPTZero-style burstiness measure, low for machine text) now reported in
`causal-lm-ppl` details for later calibration.

Measured effect on the corpus (heuristic layer only, no neural members):

- **Overall AUROC 0.769, English AUROC 0.894, FPR 0** — the fingerprint
  signals rank every AI prose/doc sample above every human sample with
  zero false positives; the top-6 scored items are all AI (52/43/35/
  30×3) vs best human 21.
- Devin technical docs moved 18→**30** fused-heuristic (typographic
  punctuation + uniform list structure now fire) — still below the flag
  band, honest improvement not a fix.
- **The fingerprints out-rank the neural ensemble on English** (0.894
  vs 0.197) precisely because they do not false-positive on
  encyclopedic prose — they measure *style fingerprints*, not fluency.
- Remaining gap: short/plain AI samples (news-style, informal, one-line
  Korean) still score 0 — fingerprints need ≥60 words / ≥200 chars of
  scaffolded text to engage.

## Sixth probe — post-gate re-measurement (2026-09-16)

Full-pipeline re-run after the language gate, technical-document gate,
and short-text cap — 30-sample manifest, all wired neural members:

| Metric | Before gates | After gates |
|---|---|---|
| Overall AUROC | 0.416 | 0.299 |
| ko AUROC | 0.845 | 0.333 |
| ko FPR@50 | 0.286 | 0.714 |
| en AUROC | 0.197 | 0.242 |

What the regression actually measures:

- **Short-text cap at 49** initially pushed capped AI samples (49) just
  under the 50 eval threshold → artificial FN cluster → cap moved to 66
  (MEDIUM ceiling; "never HIGH on short text" semantics preserved).
- **English-only member exclusion on Korean** (was ×0.25 downweight):
  correct semantics — a member measured at 98 FP on human Korean is
  noise at any weight — but exposed that the *remaining* ko-capable
  members (Qwen PPL screen, Binoculars) themselves flag polished Korean
  prose. wiki_ko_인공지능 scores 88 purely from the multilingual members.
  This is the A-3 intrinsic-overlap zone; the Qwen anchors [8,60] are
  English-calibrated and the profile itself marks them uncalibrated.
- **The heuristic fingerprint layer still out-ranks the neural ensemble**
  (en 0.894 heuristic-only vs 0.242 full-pipeline). The neural members
  currently subtract information on English, not just Korean.
- devin-doc misses are the *designed* trade-off of the technical-document
  gate: suppressed AI docs rank below human prose rather than
  false-positiving on real documents.

Honest position after measurement: ensemble weighting cannot fix
members that are noise in-domain. Remaining fixes are corpus-based
(P1): re-anchoring qwen-ppl on a labeled ko+en corpus, and a
heuristic-vs-neural weighting pass measured on the manifest.

## Seventh probe — Qwen PPL anchor calibration attempt (2026-09-16)

Raw perplexity measured on all 30 manifest samples (Qwen2.5-0.5B,
dual raw/prose view, best-of):

| Group | PPL range | n |
|---|---|---|
| AI prose (ko+en formal) | 3.0 – 19.3 | 7 |
| AI humanized/informal | 16.7 – 60.8 | 3 |
| AI technical docs (devin) | 18.7 – 84.9 | 6 |
| **Human polished prose (wiki/talk)** | **8.3 – 19.6** | 12 |
| Human boilerplate (license) | 1.5 | 1 |

**No anchor separates this corpus**: human polished prose (8–19) and AI
prose (7–19) occupy the same band; only the extremes (boilerplate 1.5,
tech docs 61–85) are clean. The [8,60] anchors were never calibrated —
with them, every human wiki sample scores 73–98 = systematic FP.

Measured action taken: `qwen-ppl` `ensemble_weight` 1.0 → **0.25**,
matching the binoculars policy for members with no demonstrated
in-domain separation. The raw `ppl=` value stays in `detail` for audit.

What this implies: the perplexity screen is *calibrated-away* — on this
corpus it is near-noise for the contested band. The fingerprint heuristic
layer remains the strongest measured signal (en AUROC 0.894 solo).

## Eighth probe — Binoculars ratio measurement (2026-09-16)

Raw performer/observer PPL ratio on all 30 samples (Qwen2.5 pair):

| Group | Ratio range | Resulting score |
|---|---|---|
| Human polished (wiki/talk, 12/12) | 0.706 – 0.843 | **100 — every one FP** |
| AI ko (claude family) | 0.673 – 0.912 | 69 – 100 |
| AI en / devin docs | 0.938 – 1.086 | 0 – 56 |
| Human boilerplate | 1.380 | 0 |

The ratio tracks **register polish, not AI origin** — polished human
prose sits *below* the AI formal band (humans write surprisingly
model-like when encyclopedic). On this corpus the member is worse than
noise: it guarantees an FP on every clean human document.

Measured action: `ensemble_weight` 0.25 → **0.05** (effectively disabled
until re-anchored on a real corpus; kept non-zero so the profile still
reports a detail for audit).

State after all probes: the fingerprint/statistical layer is the only
measured-discriminative text path (en AUROC 0.894 solo). All three
neural members are now weight-limited by measurement rather than
assumption — the honest current capability for text is heuristic-first
with provenance signals (metadata, watermark-with-key) on top.

## Final measurement — after member calibration (2026-09-16)

With en-only members excluded on ko, and both PPL members marked
English-anchored + weight-limited:

| Metric | Value |
|---|---|
| Overall AUROC | **0.586** (best so far) |
| ko FPR@50 | **0.0** (was 0.71 before calibration) |
| ko recall | 0.17 — conservative miss-over-accuse posture |
| en FPR@50 | 0.167 |
| Wikipedia FP@50 | 0.11 (was 0.44) |

Endpoint interpretation: the pipeline now *misses* hard cases rather
than accusing humans — the correct failure direction for a screening
tool. ko detection is heuristic-layer only until a Korean-anchored
member exists; en detection leans on fingerprint signals. Both are
documented as prioritization signals, and every change in this session
was driven by a measured corpus value, not intuition.

## Follow-up candidate screening (2026-09-18)

Candidate: `Hello-SimpleAI/chatgpt-detector-roberta` (45.8k HF downloads,
RoBERTa fine-tuned on the HC3 human-vs-ChatGPT corpus) — screened before
wiring against the incumbent fakespot member.

| Input | fakespot P(AI) | HC3-roberta P(ChatGPT) |
|---|---|---|
| HC3 human answers (8) | 0.000 (sampled) | 0.000–0.002 |
| HC3 ChatGPT answers (8) | 1.000 (sampled) | 0.998–1.000 |
| Local GPT-2 output (OOD) | **0.977** | 0.002 — miss |
| Korean formal AI-style (OOD) | **0.985** | 0.008 — miss |

Verdict: **rejected, not wired.** The HC3 model is perfect on its own
training domain but blind outside it — the incumbent fakespot member is
already the stronger public model and actually generalizes further
(GPT-2 and even formal Korean AI-style text score ~0.98). Adding a
weaker second classifier would only dilute the ensemble.

New measured note: fakespot scoring 0.985 on formal Korean AI-style
prose is better than this document's earlier Korean recall estimate
suggested — but one formal sample is not a recall number; informal,
short, and humanized Korean remains heuristic-only.

## Korean corpus evaluation (2026-09-20)

Corpus built locally: 398 human samples (Korean Wikipedia) + 175 AI
samples (Qwen2.5-0.5B-Instruct, 8 prompt styles × 25 topics) + 39
cross-generator samples (Qwen2.5-1.5B base, prompt-completion).
40-sample subsets per side, score≥50 = flagged:

| Member | AI-ko recall | human-ko FPR | Note |
|---|---|---|---|
| fakespot roberta | 0.90 | 0.30 | gated off Korean anyway (`trained_languages: en`) |
| openai detector | 0.97 | 0.57 | gated off Korean |
| qwen-ppl | 1.00 | **0.97** | flags nearly all Korean as AI — gated |
| binoculars | 1.00 | **1.00** | flags all Korean as AI — gated |
| heuristic layer | 0.00 | 0.00 | blind on Korean |

Every English-trained member false-positives badly on Korean human text
(FPR 0.30–1.00) — the `trained_languages` gate is load-bearing and all
four are correctly excluded on hangul-dominant input. But that means
Korean AI text today gets **zero neural coverage** — heuristics score ~0.

## Rejected candidate: locally fine-tuned KoELECTRA-small

Trained `monologg/koelectra-small-v3-discriminator` on the balanced
corpus (88 human / 88 Qwen-0.5B AI, 3 epochs):

- Same-distribution holdout: AUROC 1.00, FPR 0.00, recall 1.00
- **Cross-generator (Qwen2.5-1.5B): recall 3/29 = 0.10**

The perfect holdout was single-generator overfitting — the classifier
learned Qwen-0.5B-instruct style, not "AI text". Consistent with every
supervised detector measured here. **Not wired.** A usable Korean member
needs a multi-generator Korean AI corpus (HyperCLOVA/GPT/Claude outputs),
which requires API access we don't have locally.
