# deepfake-lens scan JSON contract

`deepfake-lens scan --format json` (and `--json-out`) emit a single JSON
document assembled by `deepfake_lens.core.scan_to_json`. The payload is
versioned so downstream consumers (for example the RapidForensic
`synthetic-media` artifact provider) can pin to a known shape.

## Top-level fields

| Field | Stability | Notes |
| --- | --- | --- |
| `schema_version` | stable | Integer, currently `1`. Additive-only within a major version; a consumer should treat unknown extra keys as ignorable. |
| `summary` | stable | `BatchScanSummary` counts: `total`, `analyzed`, `high`, `medium`, `unknown`, `low`, `unsupported_or_failed`, `capped`, `cached`, `duplicates`, `skipped`, `external_model_active`. |
| `items` | stable | List of per-file scan items in review-priority order. |

## Item fields (stable)

- `path`, `name`, `kind` (`image`, `audio`, `text`, `unsupported`,
  `duplicate`, `unknown`), `status` (`analyzed`, `failed`, `skipped`,
  `unsupported`, `duplicate`), `size_bytes`, `error`, `duplicate_of`.
- `result`: `null` unless `status == "analyzed"`.

## Result fields (stable)

- `score` (int, 0-100), `band` (`unknown`/`low`/`medium`/`high`),
  `band_label`, `verdict`.
- `signals`: list of `{title, detail, weight}`.
- `limitations`: list of strings describing coverage limits.
- `source_guess`: `{label, confidence, reasons}`.
- `next_checks`: list of suggested follow-up actions.
- `model_analysis`: `null`, or `{available, ...}` describing the external
  model sidecar result. Applies to `image` items (e.g. the AIDE runtime)
  and `audio` items (e.g. the AASIST runtime); profiles only run when their
  declared `modality` matches the file kind.
  `summary.external_model_active` counts items where
  `model_analysis.available` is true.
- `pixel_analysis`: `null`, or the bounded pixel-expert result when
  `--pixel` was used.
- `ai_score`, `source_attribution_label`: fusion/attribution helpers.

## Semantics

`score` is a **prioritization score, not a truth label**. It orders files
for examiner review; it does not assert that a file is or is not
synthetic. `limitations` always carries the reasons a result may be
incomplete, and consumers must surface them alongside the score.

## Versioning rules

- `schema_version` increments only on breaking changes (removed or
  retyped stable fields).
- New fields may be added without a version bump; consumers must ignore
  unknown keys.
- Fields not listed above are internal and may change without notice.
