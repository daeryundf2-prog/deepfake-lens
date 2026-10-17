# Deepfake Lens

[![CI](https://github.com/daeryundf2-prog/deepfake-lens/actions/workflows/deepfake-lens.yml/badge.svg)](https://github.com/daeryundf2-prog/deepfake-lens/actions/workflows/deepfake-lens.yml)

Local AI-generated image/text screening tools split from the mobile-forensics repository.

## Python CLI

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
deepfake-lens --help
```

## Verification

```bash
python -m compileall deepfake_lens
python -m unittest discover deepfake_lens/tests
python -m deepfake_lens --help
python -m deepfake_lens models --focus benchmark
python -m deepfake_lens fusion fixtures/deepfake-lens-sample --pixel off --out /tmp/deepfake-lens-fusion.json
python -m deepfake_lens perf fixtures/deepfake-lens-sample --out /tmp/deepfake-lens-perf.json
python scripts/build_c2pa_fixture.py --force   # needs c2pa-python + openssl
python scripts/build_synthetic_dataset.py --out /tmp/dfl-smoke-dataset --per-split 6
python scripts/build_robustness_variants.py --root /tmp/dfl-smoke-dataset --out /tmp/dfl-smoke-variants
python -m deepfake_lens eval /tmp/dfl-smoke-variants --pixel deep --robustness --json-out /tmp/dfl-smoke-robustness.json
(cd extras/android && ./gradlew :deepfakeclassifier:testDebugUnitTest)   # P0R-5: Android module, outside the phase-0 gate
```

### Coverage

Coverage is gated at `fail_under = 65` (branch coverage) — set just below the
measured floor of ~67% with the dev extra installed, so the gate pins the
floor without blocking legitimate refactor noise. Install the dev extra and
run the suite under coverage:

```bash
python -m pip install -e ".[dev]"
coverage run -m unittest discover deepfake_lens/tests
coverage report          # terminal table with missing lines; exits nonzero below 65%
coverage html            # optional HTML report in htmlcov/ (gitignored)
```

Source/omit rules and the `fail_under` floor live in `[tool.coverage.*]` in
`pyproject.toml`. Optional-dependency paths (torch/transformers/onnxruntime,
c2pa, mediapipe) are exercised only when those extras are installed, so the
floor is intentionally a floor — coverage with all extras is higher.

## Machine-readable marks (techniques-2026)

C2PA 2.2–2.4 manifests are verified offline against the bundled C2PA trust list
(`DEEPFAKE_LENS_C2PA_TRUST_ANCHORS` adds a deployment's own anchors), and images,
audio and video are checked for invisible watermarks — SDXL's fixed imwatermark
message (numpy + opencv), Adobe TrustMark Q/P/C (`pip install -e ".[watermark]"`),
Meta VideoSeal / AudioSeal (`pip install -e ".[watermark_meta]"`, torch). The
decoder weights are pinned in `deepfake_lens/models/assets.json` and fetched once
with `python scripts/fetch_watermark_models.py`; without a runtime or weights the
checks are recorded as skipped. A watermark is a fact, never by itself a verdict,
and the absence of a mark never means "original" — see
[docs/MACHINE-READABLE-MARKS.md](docs/MACHINE-READABLE-MARKS.md) and the measured
false-positive rates in [docs/WATERMARK-MEASUREMENTS.md](docs/WATERMARK-MEASUREMENTS.md).

```bash
python scripts/build_c2pa24_fixtures.py --force          # C2PA 2.x fixtures (openssl 3.4+, c2pa-python>=0.38)
python scripts/fetch_watermark_models.py                  # decoder weights, sha256-checked
python scripts/fetch_real_photos.py --out /tmp/real240    # measurement photo set (network, preparation only)
python scripts/measure_watermark_fpr.py --photos /tmp/real240 --out /tmp/fpr.json
```

## Scope

This repository is independent from YH Mobile Trace. It does not contain mobile forensic acquisition code, evidence-chain schemas, or Android/iOS device collection routes.

## Roadmap

Completed steps, pending work, and the blocked-on-token-scope CI patch are tracked in [docs/ROADMAP.md](docs/ROADMAP.md).
