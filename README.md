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
./gradlew :deepfakeclassifier:testDebugUnitTest
```

## Scope

This repository is independent from YH Mobile Trace. It does not contain mobile forensic acquisition code, evidence-chain schemas, or Android/iOS device collection routes.
