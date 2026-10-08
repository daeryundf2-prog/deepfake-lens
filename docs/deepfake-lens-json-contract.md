# deepfake-lens scan JSON contract

`deepfake-lens scan --format json` (and `--json-out`) emit a single JSON
document assembled by `deepfake_lens.core.scan_to_json`. The payload is
versioned so downstream consumers (for example the RapidForensic
`synthetic-media` artifact provider) can pin to a known shape.

The machine-readable schema is
[`contracts/deepfake-lens-scan-result-v2.schema.json`](../contracts/deepfake-lens-scan-result-v2.schema.json)
(project-owned, pinned in `contracts/PIN.json` under `local`;
`scripts/verify_contracts.py` fails on unpinned edits, and
`deepfake_lens/tests/test_json_contract.py` validates real scan output
against it).

## Version 2 (phase 0) — what changed and why

`schema_version` is **2**. Version 1 reported a hand-weighted 0–100 score
and a four-level band (`high`/`medium`/`low`/`unknown`); keyword hits,
uncalibrated model scores and pixel heuristics all added to that number,
and a crashed check could leave a file looking clean. Version 2 replaces
the conclusion with a rule-based verdict over classified evidence and a
per-file coverage record:

- A result states **one of three verdicts** (`verdict_code`). There is no
  "medium" any more.
- Every piece of evidence is classified as **deterministic** (metadata,
  C2PA, recompression fingerprints), **statistical** (model outputs) or
  **lexical** (keywords, phrase lists, style statistics). Lexical evidence
  can never change the verdict; statistical evidence changes it only when
  it carries a calibration id, the corpus it was measured on and a
  probability at or above that calibration's threshold.
- **Coverage** records every check that ran, was skipped (with the reason)
  or failed (with the exception class). A failed check makes the result
  `undetermined` unless strong deterministic synthetic evidence exists.
- **Text** results are always `grade: "reference"` and their first
  limitation is the fixed sentence
  "텍스트 생성 여부 판별은 2026년 현재 증거능력이 없으며 참고 정보입니다."

## Top-level fields

| Field | Stability | Notes |
| --- | --- | --- |
| `schema_version` | stable | Integer, currently `2`. |
| `summary` | stable | `BatchScanSummary` counts (see below). |
| `coverage` | stable | Weight availability for the run (`weights_available`, `weights_total`, …). Not to be confused with per-item `result.coverage`. |
| `thresholds` | stable | Threshold provenance (`source`, `provisional`, `measured`, …). |
| `items` | stable | Per-file scan items, conclusions first: manipulation evidence, then undetermined, then authenticity evidence, then unanalyzed rows. |

### `summary`

`total`, `analyzed`, `unsupported_or_failed`, `capped`, `cached`,
`duplicates`, `skipped`, `external_model_active`, plus:

| Field | Meaning |
| --- | --- |
| `manipulation_evidence` | Analyzed items whose verdict is `manipulation_evidence`. |
| `authenticity_evidence` | Analyzed items whose verdict is `authenticity_evidence`. |
| `undetermined` | Analyzed items whose verdict is `undetermined`. |
| `checks_failed` | Analyzed items with at least one `failed` coverage entry. |
| `high` / `low` / `unknown` | Legacy names for the three verdict counts above. |
| `medium` | Legacy; always `0` in v2. |

## Item fields (stable)

- `path`, `name`, `kind` (`image`, `audio`, `video`, `text`, `archive`,
  `unsupported`, `duplicate`, `unknown`), `status` (`analyzed`, `failed`,
  `skipped`, `unsupported`, `duplicate`, `expanded`, `unknown`),
  `size_bytes`, `error`, `duplicate_of`.
- `sha256`: SHA-256 (hex) of the file's bytes as analyzed — set by folder
  scans for every readable file (duplicates included), `null` when the file
  was not hashed (oversize skip, unreadable, single-file analysis). It is the
  scan-cache key's content component (G11) and is covered by report
  signatures (G30).
- `result`: `null` unless the item was analyzed (archive container rows
  carry a roll-up result with `status: "expanded"`).

## Result fields — contract v2 (stable)

| Field | Type | Meaning |
| --- | --- | --- |
| `verdict_code` | `manipulation_evidence` \| `authenticity_evidence` \| `undetermined` | The conclusion. |
| `verdict_label` | string | `조작·생성 근거 있음` / `원본성 근거 있음` / `판단 불가`. |
| `grade` | `evidence` \| `reference` | Whether the conclusion may be cited in an expert opinion (`감정 근거로 사용 가능` / `참고`). Text is always `reference`. |
| `grade_label` | string | Korean label of `grade`. |
| `evidence` | list of evidence items | Ordered deterministic → statistical → lexical, strong → weak. |
| `coverage` | list of coverage entries | Every check for this file. |
| `probability` | number \| null | Highest probability among *calibrated* statistical items; `null` otherwise. |
| `probability_ci` | `[lo, hi]` \| null | 95% interval of that probability. |
| `score_is_calibrated` | bool | `true` only when `probability` is set. |
| `reference_signals` | list of `{title, detail, weight}` | Unmeasured heuristics shown for reference only (pixel ensemble, legacy audio/video heuristics, fusion score). `weight` is the heuristic's raw 0–100 output. Never decides. |

### Evidence item

| Field | Values | Notes |
| --- | --- | --- |
| `title`, `detail` | string | Korean. |
| `kind` | `deterministic` \| `statistical` \| `lexical` | |
| `direction` | `synthetic` \| `authentic` \| `neutral` | |
| `strength` | `strong` \| `moderate` \| `weak` | |
| `layer` | string | `metadata`, `c2pa`, `model`, `face`, `inpaint`, `rppg`, `text`, `document_metadata`, … |
| `probability` | number \| null | Set **only** for a calibrated statistical item. |
| `probability_ci` | `[lo, hi]` \| null | |
| `calibration_id` | string \| null | Calibration mapping that produced `probability`. |
| `measured_on` | string \| null | Corpus/split the calibration was measured on. |
| `raw_score` | int \| null | Uncalibrated 0–100 model or deep-layer output. Not a probability; displayed as "원점수(미보정)". |

Phase-0 classification of existing signals:

| Source | kind | direction | strength |
| --- | --- | --- | --- |
| C2PA manifest valid + trusted signer + AI `digitalSourceType` | deterministic | synthetic | strong |
| Generator metadata in a tool-identifying field (A1111 parameters, ComfyUI workflow, PNG generator tag, software/creator field) | deterministic | synthetic | strong |
| Same generator markers found only in header byte strings or free-text fields | deterministic | synthetic | moderate |
| C2PA manifest valid + trusted signer + `digitalCapture`, data hash match | deterministic | authentic | strong |
| C2PA manifest present but untrusted / not validated | deterministic | neutral (synthetic/moderate if it declares AI) | weak |
| Square generator resolution, missing metadata | deterministic | neutral | weak |
| External model output | statistical | synthetic if raw ≥ 50, else neutral | weak (moderate when calibrated) |
| Deep layers (face, inpaint, face-swap seam, rPPG, avatar, lip-sync, face track) | statistical | synthetic | weak, no probability |
| AI identity phrases, template connectors, list structure, style statistics | lexical | synthetic | weak |
| Office document creator/producer naming an AI tool | deterministic | synthetic | moderate |
| Pixel ensemble, frequency heuristics, audio/video heuristics, fusion score | — | — | `reference_signals` only |

### Coverage entry

| Field | Values |
| --- | --- |
| `check` | `metadata`, `c2pa`, `pixel`, `external_model`, `model:<member>`, `face_manipulation`, `inpaint`, `faceswap_seam`, `rppg`, `avatar`, `lipsync`, `face_track`, `audio_analysis`, `audio_features`, `video_analysis`, `document_text`, `text_lexical`, `archive` |
| `status` | `ran` \| `skipped` \| `failed` |
| `reason` | Required for `skipped`/`failed`. Skips: `의존성 부재: <module>`, `얼굴 미검출`, `측정 범위 밖: 해상도 …`, `비활성화(…)`, `모델 프로필 미지정`, `모델 실행 불가: …`. Failures: `<ExceptionClass>: <message ≤200 chars>`. |

### Decision rule (`deepfake_lens/decision.py`)

Evaluated in order; the first that applies wins.

1. `grade == reference` → `undetermined`.
2. Any deterministic + synthetic + strong evidence → `manipulation_evidence`
   (kept even if a check failed; the failure stays in `coverage`).
3. Any `failed` coverage entry → `undetermined`.
4. Any calibrated statistical synthetic item with `probability ≥` its
   threshold → `manipulation_evidence`. (No profile is calibrated in phase 0.)
5. Deterministic + authentic + strong evidence and no non-lexical
   synthetic-direction evidence → `authenticity_evidence`.
6. Otherwise → `undetermined`.

## Legacy result fields (kept for read-compat, derived)

| Field | Derivation in v2 |
| --- | --- |
| `band` | `manipulation_evidence` → `high`, `authenticity_evidence` → `low`, `undetermined` → `unknown`. `medium` is never produced; the enum value is kept only so v1 files load. |
| `band_label` | Same as `verdict_label`. |
| `score`, `ai_score` | `round(probability × 100)` when calibrated, otherwise `0`. |
| `verdict` | Korean sentence naming the verdict and its basis (text: starts with `참고:`). |
| `signals` | Derived from `evidence`: `{title, detail, weight}` with `weight` 60/25/5 for strong/moderate/weak **synthetic** items and 0 for authentic/neutral ones, sorted by weight. |
| `limitations` | Known limits; for text the legal sentence is first. A failed check is also listed here as `검사 실패 — …`. |
| `source_guess`, `next_checks`, `model_analysis`, `pixel_analysis`, `av_audio`, `document_metadata`, `source_attribution_label` | Unchanged meaning. `model_analysis.score` is the uncalibrated raw score. |

Reading a v1 record: missing v2 fields load as `verdict_code:
"undetermined"`, `grade: "evidence"`, empty `evidence`/`coverage`; the
stored v1 `band` is kept verbatim but is not a verdict.

## Signed reports (G30)

`scan --sign`, signed HTML/PDF renderings and `POST /api/report` add these
top-level fields. The HMAC-SHA256 covers the canonical JSON
(`sort_keys`, compact separators, UTF-8) of **every field except
`signature` and `signature_key_id`**.

| Field | Meaning |
| --- | --- |
| `tool_version` | Deepfake Lens version that produced the report. |
| `model_pins` | `[{"profile": "<profile file stem>", "pin": {"sha256"\|"revision": …} \| null}]` for every runtime profile in the effective models dir (plus explicit `--model-path` profiles), sorted by name. `null` = unpinned. |
| `signature_note` | Korean note: what the HMAC proves, or `서명 없음 (unsigned): …` when no key was configured. Inside the MAC. |
| `signature_key_id` | `hmac-sha256-v1:<first 12 hex of SHA-256(key)>`. Outside the MAC; a mismatch with the verifying key is reported as `키 ID 불일치`. |
| `signature` | Hex HMAC, or `null` when unsigned. |

Rendered reports (`POST /api/report`, `--html-out`) additionally carry
`report_format` and the posted `thresholds`/`coverage` in the signed body.

## Versioning rules

- `schema_version` increments on breaking changes (removed or retyped
  stable fields, or a changed meaning of a stable field — v2 changed the
  meaning of `score` and `band`).
- New fields may be added without a version bump; consumers must ignore
  unknown keys.
- Fields not listed above are internal and may change without notice.
