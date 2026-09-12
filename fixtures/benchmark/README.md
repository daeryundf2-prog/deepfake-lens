# Benchmark fixtures (synthetic, license-clean)

Small programmatically generated PNGs used by
`deepfake_lens/tests/test_benchmark_e2e.py` as an end-to-end
smoke/regression harness for `scan_directory`. Every byte is produced by
`scripts/make_benchmark_fixtures.py` — nothing is downloaded or derived
from third-party media, so there are no licensing concerns.

| File | Content | Expected pipeline behavior |
| ---- | ------- | -------------------------- |
| `real-like-texture.png` | 96x96 value-noise + grain texture, no metadata | low score, no source guess |
| `ai-like-gradient.png` | 512x512 smooth gradient, no metadata | low score; triggers only the square-resolution signal |
| `a1111-metadata-marker.png` | 64x64 gradient + tEXt `parameters` chunk with an AUTOMATIC1111-style string | HIGH band via the A1111 metadata heuristic |

This is **not** a calibrated accuracy benchmark — the images are
synthetic stand-ins that pin pipeline behavior, not detection quality.
For measured detection numbers see `experiments/AIDE_EVALUATION.md`.

## Regenerating / verifying

```sh
python scripts/make_benchmark_fixtures.py          # rewrite fixtures/
python scripts/make_benchmark_fixtures.py --check  # byte-exact verification
```

Generation is seeded, so committed bytes must reproduce identically.
The E2E test enforces this by regenerating into a temp dir and comparing.

## Score report

`test_benchmark_e2e.py` writes a per-file score report (smoke output, not
an accuracy metric). Set `DEEPFAKE_LENS_BENCH_REPORT=/path/report.json`
to choose the destination; otherwise it goes to a temp directory.
