# Phase-0 reference gate image: Linux + Python 3.13 (HANDOFF §4/§5).
#   /venv     main env (pixel/audio/ml/dev extras, incl. librosa+numba, c2pa)
#   /venv-api API env (fastapi/httpx/uvicorn/pymupdf + numpy/Pillow/opencv/scipy/sklearn/mediapipe; no librosa/c2pa)
# Build:  docker build -t deepfake-lens-gate -f scripts/gate/Dockerfile scripts/gate
#
# R23-10 (round 23): reproducible from this folder. The base image is pinned by digest (the multi-arch index of
# python:3.13.16-slim-trixie — its arm64 layers are the reference image's first four), the Debian packages come from
# the snapshot.debian.org state the reference was built from (the explicit packages also by version), and every pip
# package — dependencies included — is installed at the exact version of the reference image's `pip freeze`
# (requirements-*.lock, --no-deps: nothing is resolved at build time). Before R23-10 nothing was pinned: a rebuild
# took whatever python:3.13-slim and PyPI held that day.
FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ARG DEBIAN_SNAPSHOT=20261011T000000Z
RUN echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99snapshot && \
    printf 'Types: deb\nURIs: http://snapshot.debian.org/archive/debian/%s\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n\nTypes: deb\nURIs: http://snapshot.debian.org/archive/debian-security/%s\nSuites: trixie-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n' \
      "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg=7:7.1.5-0+deb13u1 git=1:2.47.3-0+deb13u1 procps=2:4.0.4-9 \
      util-linux=2.41.5-0+deb13u1 nodejs=20.19.2+dfsg-1+deb13u3 && rm -rf /var/lib/apt/lists/*
COPY requirements-main.lock requirements-api.lock /tmp/locks/
RUN python -m venv /venv && /venv/bin/pip install -q pip==26.2.1 && \
    /venv/bin/pip install -q --no-deps -r /tmp/locks/requirements-main.lock && /venv/bin/pip check
RUN python -m venv /venv-api && /venv-api/bin/pip install -q pip==26.2.1 && \
    /venv-api/bin/pip install -q --no-deps -r /tmp/locks/requirements-api.lock && /venv-api/bin/pip check
WORKDIR /repo
