from __future__ import annotations

import json
from pathlib import Path


REQUIRED_RELEASE_FILES = [
    "pyproject.toml",
    "docs/deepfake-lens-cli.md",
    "deepfake_lens/cli.py",
    "deepfake_lens/core.py",
    "deepfake_lens/tests/test_core.py",
    "deepfake_lens/fusion.py",
    "deepfake_lens/perf.py",
    "experiments/README.md",
    "fixtures/deepfake-lens-sample/README.md",
]

RELEASE_CHECK_COMMANDS = [
    "python -m unittest discover deepfake_lens/tests",
    "python -m compileall deepfake_lens",
    "python -m deepfake_lens --help",
    "python -m deepfake_lens models --focus benchmark",
    "python -m py_compile experiments/train_detector.py experiments/export_onnx.py",
    "python -m deepfake_lens fusion fixtures/deepfake-lens-sample --pixel off --out artifacts/fusion-profile.json",
    "python -m deepfake_lens security --out artifacts/security-check.json",
    "python -m deepfake_lens perf fixtures/deepfake-lens-sample --out artifacts/perf.json",
]


def build_release_checklist(root: Path | str) -> dict[str, object]:
    root_path = Path(root)
    files = []
    for relative in REQUIRED_RELEASE_FILES:
        path = root_path / relative
        files.append({"path": relative, "exists": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0})
    pyproject = (root_path / "pyproject.toml").read_text(encoding="utf-8") if (root_path / "pyproject.toml").exists() else ""
    return {
        "version": "deepfake-lens-release-check-v1",
        "files": files,
        "commands": RELEASE_CHECK_COMMANDS,
        "entrypoint_present": 'deepfake-lens = "deepfake_lens.cli:main"' in pyproject,
        "notes": [
            "릴리스 태그를 달기 전에 모든 명령을 실행하십시오.",
            "데이터셋 라이선스, 모델 라이선스, 보정 보고서를 기록하기 전에는 사전학습 가중치를 공개하지 마십시오.",
            "scan/eval/train은 기본적으로 로컬에서만 실행하십시오.",
        ],
    }


def write_release_checklist(root: Path | str, output_path: Path | str) -> dict[str, object]:
    payload = build_release_checklist(root)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
