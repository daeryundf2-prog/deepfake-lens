# Phase-0 reference gate image: Linux + Python 3.13 (HANDOFF §4/§5).
#   /venv     main env (pixel/audio/ml/dev extras, incl. librosa+numba, c2pa)
#   /venv-api API env (fastapi/httpx/uvicorn/pymupdf + numpy/Pillow/opencv/scipy/sklearn/mediapipe; no librosa/c2pa)
# Build:  docker build -t deepfake-lens-gate -f scripts/gate/Dockerfile scripts/gate
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git procps util-linux nodejs && rm -rf /var/lib/apt/lists/*
RUN python -m venv /venv && /venv/bin/pip install -q --upgrade pip && \
    /venv/bin/pip install -q "opencv-python>=4.8" "numpy>=1.24" "librosa>=0.10" "scipy>=1.10" "scikit-learn>=1.3" \
      "Pillow>=10.0" "c2pa-python>=0.6" "coverage>=7.4" "jsonschema>=4.18" ruff mypy
RUN python -m venv /venv-api && /venv-api/bin/pip install -q fastapi httpx uvicorn pymupdf linkify-it-py markdown-it-py numpy Pillow opencv-python scipy scikit-learn mediapipe
WORKDIR /repo
