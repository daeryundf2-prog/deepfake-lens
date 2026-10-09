#!/usr/bin/env python3
"""Generate the model-zoo tables from the runtime profiles (G9/G33).

The profiles in ``deepfake_lens/models/*-runtime.json`` are the single
source of truth. This script rewrites the generated blocks in:

- ``deepfake_lens/models/README.md``  — profile / runtime / weights / pin / status table
- ``deepfake_lens/models/NOTICE.md``  — profile / upstream / license table
- ``deepfake_lens/model_registry.py`` — the profile-backed ``DetectorCandidate`` entries

Each block sits between ``BEGIN GENERATED`` / ``END GENERATED`` markers;
everything outside the markers is hand-written and left untouched.

Usage:
    python scripts/sync_model_docs.py           # rewrite the generated blocks
    python scripts/sync_model_docs.py --check   # exit 1 if any block is stale (CI)
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "deepfake_lens" / "models"
README = MODELS_DIR / "README.md"
NOTICE = MODELS_DIR / "NOTICE.md"
REGISTRY = REPO_ROOT / "deepfake_lens" / "model_registry.py"

MD_BEGIN = "<!-- BEGIN GENERATED: profiles (scripts/sync_model_docs.py — do not edit by hand) -->"
MD_END = "<!-- END GENERATED: profiles -->"
PY_BEGIN = "# BEGIN GENERATED: profile candidates (scripts/sync_model_docs.py — do not edit by hand)"
PY_END = "# END GENERATED: profile candidates"

# Runtimes whose weights come from the hub (revision pin) — mirrors
# deepfake_lens.model_pins without importing the package, so the script
# runs before an editable install.
HUB_RUNTIMES = {"hf-text-classifier", "hf-image-classifier", "hf-audio-classifier", "causal-lm-ppl", "binoculars"}
UNKNOWN_LICENSE = "미기재 — 재배포 전 업스트림 확인"


def load_profiles(models_dir: Path = MODELS_DIR) -> list[tuple[str, dict]]:
    profiles = []
    for path in sorted(models_dir.glob("*-runtime.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit(f"profile is not a JSON object: {path}")
        # R4: every profile carries the Korean name shown to the examiner;
        # the raw ``name`` is an identifier only.
        if not str(data.get("display_name") or "").strip():
            raise SystemExit(f"profile has no Korean display_name: {path}")
        profiles.append((path.name, data))
    return profiles


def _target(profile: dict) -> dict:
    inner = profile.get("inner")
    if profile.get("runtime") == "video-frames" and isinstance(inner, dict):
        return inner
    return profile


def _runtime(profile: dict) -> str:
    runtime = str(profile.get("runtime") or "")
    if runtime == "video-frames":
        return f"video-frames → {_target(profile).get('runtime', '?')}"
    return runtime


def _modality(profile: dict) -> str:
    return str(profile.get("modality") or "image")


def _weights(profile: dict) -> str:
    target = _target(profile)
    if target.get("runtime") in HUB_RUNTIMES:
        return f"허브 `{target.get('hub_model', '?')}`"
    if target.get("checkpoint"):
        return f"로컬 `{target['checkpoint']}`"
    return "—"


def _pin_status(profile: dict) -> str:
    pin = profile.get("pin")
    if not isinstance(pin, dict) or not pin:
        return "pin 없음"
    filled = {key: str(value) for key, value in pin.items() if value}
    if len(filled) == len(pin):
        return "고정 (" + ", ".join(f"{key} {value[:12]}…" for key, value in sorted(filled.items())) + ")"
    return "미고정 (" + ", ".join(sorted(pin)) + " 비어 있음)"


def _supported(profile: dict) -> str:
    return "false" if profile.get("supported") is False else "true"


def _measured(profile: dict) -> str:
    measured = profile.get("measured_on")
    if isinstance(measured, dict):
        return str(measured.get("corpus_id") or "기록 있음")
    return "없음"


def _in_sample(profile: dict) -> str:
    """G28: a calibration value (score_bias) fitted on the evaluation data."""
    target = _target(profile)
    for candidate in (profile, target):
        if candidate.get("score_bias_in_sample") is True:
            return f"예 (score_bias={candidate.get('score_bias', '?')})"
    return "—"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def readme_block(profiles: list[tuple[str, dict]]) -> str:
    lines = [
        "| 프로필 | 표시 이름 | 검출기(식별자 name) | 모달리티 | 런타임 | 가중치 | pin | supported | measured_on | 표본 내 보정(G28) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, profile in profiles:
        lines.append(
            f"| `{name}` | {_cell(str(profile.get('display_name') or ''))} | {_cell(str(profile.get('name') or name))} | {_modality(profile)} | `{_runtime(profile)}` "
            f"| {_weights(profile)} | {_pin_status(profile)} | {_supported(profile)} | {_measured(profile)} | {_in_sample(profile)} |"
        )
    return "\n".join(lines)


def notice_block(profiles: list[tuple[str, dict]]) -> str:
    lines = [
        "| 프로필 | 업스트림 | 라이선스 |",
        "|---|---|---|",
    ]
    for name, profile in profiles:
        source = _cell(str(profile.get("source_url") or "—"))
        license_text = _cell(str(profile.get("license") or UNKNOWN_LICENSE))
        lines.append(f"| `{name}` | {source} | {license_text} |")
    return "\n".join(lines)


def _task(profile: dict) -> str:
    modality = _modality(profile)
    if modality == "image" and (profile.get("crop_faces") or profile.get("requires_face")):
        return "face-manipulation-detector"
    return {
        "image": "binary-image-detector",
        "audio": "binary-audio-detector",
        "text": "binary-text-detector",
        "video": "video-frame-detector",
    }.get(modality, "detector")


def _py(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def registry_block(profiles: list[tuple[str, dict]]) -> str:
    lines = ["_PROFILE_CANDIDATES: list[DetectorCandidate] = ["]
    for name, profile in profiles:
        supported = profile.get("supported") is not False
        notes = [
            f"표시 이름: {profile.get('display_name')}.",
            f"프로필 models/{name} (runtime {_runtime(profile)}, modality {_modality(profile)}).",
            "supported: true" if supported else f"supported: false — {profile.get('reason') or '비활성'}",
            f"pin: {_pin_status(profile)}; measured_on: {_measured(profile)}.",
        ]
        lines.extend([
            "    DetectorCandidate(",
            f"        key={_py(str(profile.get('candidate_key') or name.removesuffix('-runtime.json')))},",
            f"        name={_py(str(profile.get('name') or name))},",
            f"        task={_py(_task(profile))},",
            f"        adapter_target={_py(str(profile.get('runtime') or '') + ' runtime profile')},",
            f"        status={_py('integrated' if supported else 'unmeasured')},",
            '        priority="n/a",',
            f"        source_url={_py(str(profile.get('source_url') or ''))},",
            "        notes=[",
            *[f"            {_py(note)}," for note in notes],
            "        ],",
            "    ),",
        ])
    lines.append("]")
    return "\n".join(lines)


def _replace_block(text: str, begin: str, end: str, body: str, path: Path) -> str:
    start = text.find(begin)
    stop = text.find(end)
    if start < 0 or stop < 0 or stop < start:
        raise SystemExit(f"generated-block markers missing in {path}")
    return text[: start + len(begin)] + "\n" + body + "\n" + text[stop:]


def render(models_dir: Path = MODELS_DIR) -> dict[Path, str]:
    """Expected contents of every generated file."""
    profiles = load_profiles(models_dir)
    return {
        README: _replace_block(README.read_text(encoding="utf-8"), MD_BEGIN, MD_END, readme_block(profiles), README),
        NOTICE: _replace_block(NOTICE.read_text(encoding="utf-8"), MD_BEGIN, MD_END, notice_block(profiles), NOTICE),
        REGISTRY: _replace_block(REGISTRY.read_text(encoding="utf-8"), PY_BEGIN, PY_END, registry_block(profiles), REGISTRY),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 when a generated block differs from the profiles")
    args = parser.parse_args(argv)
    stale = 0
    for path, expected in render().items():
        current = path.read_text(encoding="utf-8")
        if current == expected:
            continue
        if args.check:
            stale += 1
            rel = path.relative_to(REPO_ROOT)
            sys.stdout.writelines(difflib.unified_diff(current.splitlines(True), expected.splitlines(True), f"{rel} (committed)", f"{rel} (generated)"))
        else:
            path.write_text(expected, encoding="utf-8")
            print(f"updated {path.relative_to(REPO_ROOT)}")
    if stale:
        print(f"\n{stale} generated file(s) are stale — run: python scripts/sync_model_docs.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
