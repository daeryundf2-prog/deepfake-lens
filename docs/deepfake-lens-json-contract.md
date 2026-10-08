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
| `thresholds` | stable | Threshold provenance (`source`, `provisional`, `measured`, …). A loaded profile also reports `in_sample` (cutoffs fitted on the rows they were evaluated on — G28), `note`, and `label` (`"in-sample(참고)"`, `"측정됨"`, `"잠정(미검증)"`). |
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
  signatures (G30). Archive container rows carry the archive file's own
  digest (D9), so a signed report binds the container as well as its members.
- `result`: `null` unless the item was analyzed (archive container rows
  carry a roll-up result with `status: "expanded"`, or `"unknown"` when no
  member could be analyzed). Every member the extractor refused is a
  `skipped` coverage entry with check `archive_member` and reason
  `<member>: <why>` — `경로 이탈 멤버('..' …)`, `절대 경로 멤버(…)`,
  `심볼릭 링크 멤버`, `하드 링크 멤버`, `압축 예산 초과(선언 크기 N, 한도 M) — <cap>`
  (per-member cap, compression-ratio bomb, per-archive or tree byte budget),
  `앞선 멤버에서 해제 예산 소진으로 미해제`, `중첩 압축 최대 깊이(N) 초과로 미해제`,
  `중첩 압축 예산(N개) 소진으로 미해제`, `손상된 멤버 데이터(<Exception>: …)`;
  members of nested archives are named `<inner archive>::<member>`. The same
  lines appear in `limitations` as `구성 파일 거부: <member> — <why>` (at most
  100 per container, then one `외 N개` entry).
- A symlink found in a scanned folder (file or directory) is never followed
  and is listed as its own row: `kind: "unknown"`, `status: "skipped"`,
  `result: null`, `error` starting `심볼릭 링크` (D10); it counts in
  `summary.skipped`.

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
| `layer` | string | `metadata`, `c2pa`, `image_class`, `model`, `face`, `inpaint`, `rppg`, `text`, `document_metadata`, … |
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
| XMP `Iptc4xmpExt:DigitalSourceType` = `trainedAlgorithmicMedia` / `compositeWithTrainedAlgorithmicMedia` / `algorithmicMedia` / `compositeSynthetic` (`XMP 디지털 출처 유형: 생성형 AI`) | deterministic | synthetic | strong |
| A generator name (`evidence_rules.GENERATOR_NAMES`: Flux, Midjourney, DALL-E, DALL·E, Imagen, Stable Diffusion, Firefly, Gemini, Nano Banana, "AI Generated", "Generated by AI") in `xmp:CreatorTool`, `dc:creator` or `photoshop:Credit` (`XMP 생성 도구 필드`) | deterministic | synthetic | strong |
| C2PA manifest valid + trusted signer + `digitalCapture`, data hash match | deterministic | authentic | strong |
| Camera EXIF consistent (`카메라 EXIF 일관`): Make + Model, parseable DateTimeOriginal (≥ 1995, not in the future), Software absent or the camera's own (vendor/model/firmware, never an editor or generator), GPS in range and dated within a day of capture, no generator name in XMP, JPEG quality estimate ≥ 90 (recompression proxy until phase 1) | deterministic | authentic | moderate |
| Camera EXIF present but a condition above fails (`카메라 EXIF 있음(일관성 조건 미충족)`, detail names the failed conditions) | deterministic | neutral | weak |
| C2PA manifest present but untrusted / not validated | deterministic | neutral (synthetic/moderate if it declares AI) | weak |
| Square generator resolution | deterministic | neutral | weak |
| Missing metadata (`메타데이터 부재`) — only when the metadata read completed, no C2PA manifest is present (or its read failed) and no field was found; a truncated/empty/unrecognized file or an EXIF read error makes the `metadata` check `failed` instead | deterministic | neutral | weak |
| Image class from the photo/non-photo gate (`이미지 유형: …`, layer `image_class`) | deterministic | neutral | weak |
| External model output | statistical | synthetic if raw ≥ 50, else neutral | weak (moderate when calibrated) |
| Deep layers (face, inpaint, face-swap seam, rPPG, avatar, lip-sync, face track) — uncalibrated, so `reference_signals` only, title suffixed `(참고, 미보정)`, detail with the raw 0–100 value (D13); never an evidence item | — | — | `reference_signals` only |
| AI identity phrases, template connectors, list structure, style statistics | lexical | synthetic | weak |
| Office document creator/producer naming an AI tool | deterministic | synthetic | moderate |
| Pixel ensemble (plain weighted mean of its experts — no floors, G3), frequency heuristics, audio/video heuristics, fusion score | — | — | `reference_signals` only |

### Coverage entry

| Field | Values |
| --- | --- |
| `check` | `metadata`, `c2pa`, `image_class`, `pixel`, `external_model`, `model:<member>`, `face_manipulation`, `inpaint`, `faceswap_seam`, `rppg`, `avatar`, `lipsync`, `face_track`, `audio_analysis`, `audio_features`, `video_analysis`, `document_text`, `text_lexical`, `archive`, `archive_member` |
| `status` | `ran` \| `skipped` \| `failed` |
| `reason` | Required for `skipped`/`failed`. `metadata` fails with `AnalyzerError: …` when the image's metadata could not be read completely: `JPEG 구조 불완전(SOS 마커 전에 파일 끝) …`, `PNG 구조 불완전(IEND 청크 없음) …`, `빈 파일 …`, `이미지 형식 식별 불가(… 확장자 위장 가능성)`, `EXIF 판독 실패: <Exception>: …`. `c2pa` runs when the SDK read a manifest or reported none (`ManifestNotFound` → status `absent`); any other reader/validation exception (status `unavailable`, CHARTER 4-value rule, D8) fails it with `AnalyzerError: C2PA 판독 실패: <Exception>: …` / `C2PA 검증 실패: …`, and a container the SDK does not handle is skipped `C2PA SDK가 지원하지 않는 형식: …`. Skips: `의존성 부재: <module>`, `얼굴 미검출`, `측정 범위 밖: 해상도 …`, `사진 아님: <kind>` (see below), `비활성화(…)`, `모델 프로필 미지정`, `모델 실행 불가: …`. Failures: `<ExceptionClass>: <message ≤200 chars>`; a model weight refused by the pin policy (G9) fails with `미고정 프로필: …` (no/empty/malformed `pin`) or `무결성 불일치: …` (checkpoint sha256 differs from `pin.sha256`). A language-gated zoo member is `skipped` with `모델 실행 불가: 언어 게이트 제외: …`. |

### Image class — photo/non-photo gate (phase 0, WP-D: G3/G13/G17)

Every image goes through `deepfake_lens/image_class.py:classify_image`
(numpy + Pillow; deterministic rules, constants and their sources at the
top of the module) before any detector. The result is recorded twice:

- **coverage** — check `image_class`: `ran`; `skipped` `의존성 부재: numpy`
  without the imaging extras; `failed` `<Exception>: …` when the file
  cannot be decoded (which, by rule 3, makes the verdict `undetermined`).
- **evidence** — one item, `title` `이미지 유형: <label>`, `kind`
  `deterministic`, `direction` `neutral`, `strength` `weak`, `layer`
  `image_class`; `detail` names the class and the rule values that decided
  it. It never points toward or away from synthesis.

| `kind` | Korean label | Rule (first match wins) |
| --- | --- | --- |
| `too_small` | 저해상도 | long side < 128 px (from the file header when available) |
| `screenshot` | 스크린샷 | dimensions in the module's phone/tablet/monitor resolution table **and** a solid status-bar band at the top **and** ≥ 0.3 % of pixels on exact axis-aligned step-edge runs |
| `document_scan` | 문서 스캔 | > 60 % near-white, ≥ 2 % ink, bimodal luminance, ≥ 5 text lines in the row projection profile |
| `pattern` | 패턴(노이즈·그라데이션·단색) | < 64 unique colours, or 3×3 noise-residual variance outside the photo range, or lag-1 row/column autocorrelation > 0.999 (gradient) / < 0.05 (white noise), or a stationary Gaussian field (blurred noise) |
| `graphic` | 그래픽 | unique-colour ratio < 5 % and > 40 % flat-colour area |
| `photo` | 사진 | none of the above |

When `kind` is not `photo`, the checks `pixel`, `external_model`,
`face_manipulation`, `inpaint` and `faceswap_seam` are **not run** and are
recorded as `skipped` with reason `사진 아님: <kind>` (for `too_small`:
`측정 범위 밖: 해상도 WxH (최소 변 128px 미만)` — the same reason the
model range gate uses; the 128 px floor is defined once, in
`image_class.MEASURABLE_MIN_SIDE_PX`). Metadata and C2PA still run, so a
deterministic conclusion (e.g. generator metadata) is unaffected. The
verdict sentence reads `이미지(사진 아님 — 생성 탐지 비적용): …` and the
first limitation repeats the class. When the gate itself cannot run
(dependency missing or decode failure) the detectors run as before and
the `image_class` entry says why the gate did not apply.

The thresholds are first-principles values checked against the synthetic
adversarial set (`scripts/make_adversarial_fixtures.py`, QA-ADV-1/2) and a
photo-like positive control; they are not yet measured on a real-photo
corpus (phase 1). A photograph misclassified as non-photo loses its
detector checks — it never gains a conclusion.

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
| `source_guess`, `next_checks`, `model_analysis`, `pixel_analysis`, `av_audio`, `document_metadata`, `source_attribution_label` | Unchanged meaning, except: for text, a `source_guess` drawn from the text's own words (a ChatGPT/Claude/Gemini mention, an AI-identity phrase) is labeled `참고: …` (e.g. `참고: AI 어시스턴트 문체 유사`) with `confidence: "unknown"` — lexical, never a medium/high attribution (D11); only document metadata (creator/producer fields) can raise it. `model_analysis.score` is the uncalibrated raw score. `pixel_analysis` (reference only, D12) carries `raw_score` (uncalibrated 0–100 weighted mean of the pixel experts) and `reference_confidence` (`"참고"` when the ensemble ran, else `"off"`/`"unavailable"`) instead of the former `score`/`confidence`; older records with `score`/`confidence` still load (as `raw_score` / `"참고"`). The scan CSV names these columns `참고_픽셀_원점수` and `참고_픽셀_신뢰도`. In a model zoo, `model_analysis.models[]` lists every member; a member excluded by the language gate has `available: false`, `confidence: "skipped"` and takes no part in the aggregate score or the spread/agreement (G33). |

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

### Signed evidence statement (증거설명서)

`evidence-statement --json-out`/`--format json`, `scan
--evidence-statement-out <file>.json` and the Markdown/PDF renderings
(`--md-out`, `--pdf-out`, `POST /api/report?format=evidence`) are backed by
one signed body: every field of `EvidenceStatement.to_json()` (`case_no`,
`case_name`, `plaintiff`, `defendant`, `court`, `entries[]` with
`purpose_of_proof`/`sha256`/`statutes`, `created_at`, `law_firm`, `contact`,
`center`, `provenance_note`, `reference_note`) plus `"report_type":
"evidence-statement"` and the four signing fields above, all inside the MAC
except `signature`/`signature_key_id`. The Markdown and PDF print the
signature, key id and the signed body's SHA-256 (or the `서명 없음` lines)
so a paper copy can be tied to its signed JSON; verify the JSON with
`signing.verify_report`.

## Measurement records (phase 0, WP-I — G26/G27/G28)

These shapes are not part of the scan payload but are contracts between
the evaluation tooling, the shipped model profiles and CI.

### Corpus manifest (`corpus-manifest-v1`)

Written by `deepfake-lens corpus build`, re-split by `corpus split`,
checked by `corpus verify` (`deepfake_lens/corpus_manifest.py`). Schema:
[`contracts/corpus-manifest-v1.schema.json`](../contracts/corpus-manifest-v1.schema.json).

| Field | Meaning |
| --- | --- |
| `schema` | `"corpus-manifest-v1"`. |
| `corpus_id` | Human identifier (default: corpus directory name). Not hashed. |
| `created` | UTC ISO-8601 build time. Not hashed. |
| `items[]` | One per file: `id` (16 hex of SHA-256 over `relpath`), `relpath` (POSIX, relative to the corpus root), `sha256` (file content), `modality` (`image`/`video`/`audio`/`document`/`text`), `label` (`real`/`synthetic`/`edited`; `null` only while unlabeled), `generator`, `variant` (`original`, `kakao`, `telegram`, `instagram`, `jpeg_q50`, …), `split` (`train`/`val`/`test`; `null` before `corpus split`), `source_note`, `derived_from` (item id, relpath or 64-hex sha256 of the original; `null` for originals). |
| `manifest_sha256` | SHA-256 of the canonical items JSON: items sorted by `id`, exactly the item fields above, `sort_keys`, separators `(",", ":")`, UTF-8 without ASCII escaping. Changes when any item or split assignment changes. |
| `root_hint` | Optional; where `verify` looks for the files. Not hashed. |

`corpus build --label-from-dir` reads `<label>/<generator>/<variant>/<file>`
and links a variant to `<label>/<generator>/original/<same stem>.*` through
`derived_from`. `corpus split --group-by origin` (default) keeps every item
that derives from one original — and exact duplicates — in one split.

### Profile `measured_on`

Every `deepfake_lens/models/*-runtime.json` may carry `measured_on`
(object or `null`):

| Key | Meaning |
| --- | --- |
| `corpus_id`, `manifest_sha256` | The corpus manifest measured on (`manifest_sha256` is 64 lowercase hex). |
| `split` | Must be `"test"`. |
| `n_pos`, `n_neg` | Class counts actually scored. |
| `auroc`, `auroc_ci` | AUROC and its 95% stratified bootstrap interval `[lo, hi]` (`evaluation_metrics.bootstrap_ci`, n_boot 2000). |
| `fpr_at_threshold`, `recall_at_threshold` | At the profile's operating threshold. |
| `measured_at` | UTC ISO-8601. |
| `recall_at_fpr_0_01` | Required for text members only. |

`scripts/check_measurement_gate.py` (CI job `measurement-gate`, QA-SYS-9)
fails when a profile with `supported: true` — or no `supported` key — lacks
`measured_on`, or has `split != "test"`, `n_pos < 200`, `n_neg < 200`,
`auroc_ci[0] < 0.85` (not applied to text; text needs `recall_at_fpr_0_01`)
or a malformed `manifest_sha256`.

A profile (or a `video-frames` profile's `inner`) that sets `score_bias`
must also set `score_bias_in_sample` (bool): `true` when the bias was chosen
on the same rows its reported numbers were measured on (G28), with a Korean
`score_bias_note`. `scripts/sync_model_docs.py` shows it in the
`표본 내 보정(G28)` column of `deepfake_lens/models/README.md`. Today
`sbi-effnet` and `sbi-frames` are `true` (score_bias=35 fitted on the
evaluation crops).

### Evaluation outputs

`deepfake-lens eval`/`benchmark`/`calibrate`/`fusion` and
`experiments/eval_*.py` score **raw member outputs** (external model raw
0-100 score, else a statistical evidence item's `raw_score`, else the pixel
heuristic's raw score), never `result.score` — which is 0 for every
uncalibrated v2 result — and say so with `score_basis: "raw, uncalibrated"`.
Every AUROC/recall/FPR is accompanied by `*_ci` (95% bootstrap interval),
`n_pos`, `n_neg` and `ci_method`. Rows with no raw member score are
`predicted: "unscored"` and excluded from metrics (`unscored_count`).

## Versioning rules

- `schema_version` increments on breaking changes (removed or retyped
  stable fields, or a changed meaning of a stable field — v2 changed the
  meaning of `score` and `band`).
- New fields may be added without a version bump; consumers must ignore
  unknown keys.
- Fields not listed above are internal and may change without notice.
