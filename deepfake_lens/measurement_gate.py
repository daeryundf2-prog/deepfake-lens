"""Measurement gate for model runtime profiles (phase 0, WP-I: G26/G28).

A profile may be ``supported`` (take part in scans) only when it carries a
``measured_on`` record from a held-out *test* split of a reproducible corpus
(``corpus-manifest-v1``) with enough samples per class and a high enough
AUROC lower confidence bound. ``scripts/check_measurement_gate.py`` runs
this over ``deepfake_lens/models/*-runtime.json`` in CI (QA-SYS-9).

``measured_on`` shape (all keys required)::

    {"corpus_id": "t-img-2026q4",
     "manifest_sha256": "<64 hex>",
     "manifest_path": "corpora/t-img-2026q4/manifest.json",
     "split": "test",
     "n_pos": 240, "n_neg": 260,
     "auroc": 0.93, "auroc_ci": [0.90, 0.95],
     "fpr_at_threshold": 0.01, "recall_at_threshold": 0.71,
     "measured_at": "2026-11-02T10:00:00Z"}

Text members additionally need ``recall_at_fpr_0_01`` and have no AUROC
floor (text generation detection is reference-grade only — G24); the class
counts still apply. A profile without ``supported`` counts as supported,
matching how experiments/eval_all.py and the adapter treat it.

``manifest_path`` (D16) names the corpus-manifest-v1 file the measurement
was taken on — absolute, or relative to the profile's directory. The gate
opens it: it must exist and load as corpus-manifest-v1, its items must
still hash to its own ``manifest_sha256`` (not edited since), that hash
must equal ``measured_on.manifest_sha256``, its ``corpus_id`` must match,
and its test split must hold at least ``n_pos`` positive (synthetic /
edited) and ``n_neg`` real items. A well-formed but invented hash (all
zeros) therefore fails. ``manifest_sha256`` is the manifest's canonical
items hash (``corpus_manifest.manifest_sha256``), not the bytes of the
JSON file, so a re-serialized manifest still matches.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MEASURED_ON_KEYS = (
    "corpus_id",
    "manifest_sha256",
    "manifest_path",
    "split",
    "n_pos",
    "n_neg",
    "auroc",
    "auroc_ci",
    "fpr_at_threshold",
    "recall_at_threshold",
    "measured_at",
)
# Gate constants from the phase-0 spec (게이트 0): 200 samples per class
# and a 95% CI lower bound of AUROC >= 0.85 on the test split.
MIN_PER_CLASS = 200
MIN_AUROC_CI_LOW = 0.85
REQUIRED_SPLIT = "test"
TEXT_RECALL_KEY = "recall_at_fpr_0_01"
PROFILE_GLOB = "*-runtime.json"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def profile_is_supported(profile: dict[str, Any]) -> bool:
    """Absent ``supported`` means supported (experiments/eval_all.py:65)."""
    return bool(profile.get("supported", True))


def profile_modality(path: Path) -> str:
    from .model_adapter import _profile_modality

    return _profile_modality(path)


def check_profile(path: Path) -> list[str]:
    """Korean reasons this profile fails the gate; empty when it passes."""
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"프로필을 읽을 수 없습니다({type(exc).__name__}) — 지원 여부를 판단할 수 없으므로 실패로 처리합니다."]
    if not isinstance(profile, dict):
        return ["프로필이 JSON 객체가 아닙니다."]
    if not profile_is_supported(profile):
        return []
    measured = profile.get("measured_on")
    if not isinstance(measured, dict):
        return [
            "supported가 true(또는 생략)인데 measured_on 측정 기록이 없습니다 — "
            "재현 가능한 코퍼스의 test 분할에서 측정하기 전에는 supported: false여야 합니다."
        ]
    problems: list[str] = []
    missing = [key for key in MEASURED_ON_KEYS if key not in measured]
    if missing:
        problems.append(f"measured_on에 필수 키가 없습니다: {', '.join(missing)}")
    if measured.get("split") != REQUIRED_SPLIT:
        problems.append(f"measured_on.split이 {REQUIRED_SPLIT!r}가 아닙니다({measured.get('split')!r}) — 학습·검증 분할 측정치는 증거가 아닙니다.")
    for key in ("n_pos", "n_neg"):
        value = measured.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"measured_on.{key}가 정수가 아닙니다({value!r}).")
        elif value < MIN_PER_CLASS:
            problems.append(f"measured_on.{key}={value} < {MIN_PER_CLASS} — 클래스당 {MIN_PER_CLASS}개 미만은 측정으로 인정하지 않습니다.")
    manifest_sha = measured.get("manifest_sha256")
    if not (isinstance(manifest_sha, str) and _HEX64.match(manifest_sha)):
        problems.append("measured_on.manifest_sha256이 64자리 소문자 16진수가 아닙니다 — 코퍼스 매니페스트(corpus-manifest-v1)를 특정할 수 없습니다.")
    else:
        problems.extend(_manifest_problems(path, measured))
    if profile_modality(path) == "text":
        if TEXT_RECALL_KEY not in measured:
            problems.append(f"텍스트 프로필은 measured_on.{TEXT_RECALL_KEY}(FPR 1%에서의 재현율)가 필요합니다.")
    else:
        ci = measured.get("auroc_ci")
        if not (isinstance(ci, (list, tuple)) and len(ci) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in ci)):
            problems.append(f"measured_on.auroc_ci가 [하한, 상한] 숫자 쌍이 아닙니다({ci!r}).")
        elif float(ci[0]) < MIN_AUROC_CI_LOW:
            problems.append(f"AUROC 95% 신뢰구간 하한 {float(ci[0]):.3f} < {MIN_AUROC_CI_LOW} — 측정 게이트 미달입니다.")
    return problems


POSITIVE_LABELS = frozenset({"synthetic", "edited"})
NEGATIVE_LABELS = frozenset({"real"})


def _manifest_problems(profile_path: Path, measured: dict[str, Any]) -> list[str]:
    """The corpus manifest behind ``measured_on`` must exist and match it (D16)."""
    from .corpus_manifest import ManifestError, load_manifest, manifest_sha256

    raw = measured.get("manifest_path")
    if not isinstance(raw, str) or not raw.strip():
        return ["measured_on.manifest_path가 없습니다 — 측정에 쓴 코퍼스 매니페스트 파일을 지정해야 해시를 확인할 수 있습니다."]
    manifest_path = Path(raw)
    if not manifest_path.is_absolute():
        manifest_path = profile_path.parent / manifest_path
    if not manifest_path.is_file():
        return [f"measured_on.manifest_path 파일이 없습니다: {raw} — 존재하지 않는 코퍼스에 대한 측정 기록은 인정하지 않습니다."]
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        return [f"measured_on.manifest_path를 corpus-manifest-v1로 읽을 수 없습니다: {exc}"]
    items = [item for item in manifest["items"] if isinstance(item, dict)]
    actual = manifest_sha256(items)
    problems: list[str] = []
    if manifest.get("manifest_sha256") != actual:
        problems.append(f"매니페스트 파일 {raw}의 manifest_sha256이 항목 목록과 맞지 않습니다 — 작성 이후 편집된 매니페스트입니다.")
    if measured.get("manifest_sha256") != actual:
        problems.append(f"measured_on.manifest_sha256이 {raw}의 해시({actual[:12]}…)와 다릅니다 — 측정 기록이 그 코퍼스를 가리키지 않습니다.")
    if manifest.get("corpus_id") != measured.get("corpus_id"):
        problems.append(f"measured_on.corpus_id({measured.get('corpus_id')!r})가 매니페스트의 corpus_id({manifest.get('corpus_id')!r})와 다릅니다.")
    test_items = [item for item in items if item.get("split") == REQUIRED_SPLIT]
    available = {
        "n_pos": sum(1 for item in test_items if item.get("label") in POSITIVE_LABELS),
        "n_neg": sum(1 for item in test_items if item.get("label") in NEGATIVE_LABELS),
    }
    for key, count in available.items():
        value = measured.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > count:
            problems.append(f"measured_on.{key}={value}이(가) 매니페스트 test 분할의 해당 항목 수 {count}보다 큽니다.")
    return problems


def check_models_dir(models_dir: Path | str) -> dict[str, list[str]]:
    """``{profile file name: problems}`` for every runtime profile in the dir."""
    root = Path(models_dir)
    return {path.name: check_profile(path) for path in sorted(root.glob(PROFILE_GLOB))}


def gate_report_lines(results: dict[str, list[str]]) -> tuple[list[str], bool]:
    """Korean report lines and overall pass flag."""
    failing = {name: problems for name, problems in results.items() if problems}
    lines: list[str] = []
    for name, problems in failing.items():
        lines.append(f"[실패] {name}")
        lines.extend(f"    - {problem}" for problem in problems)
    passed = not failing
    summary = (
        f"측정 게이트 통과: 프로필 {len(results)}개 검사, 실패 0개."
        if passed
        else f"측정 게이트 실패: 프로필 {len(results)}개 중 {len(failing)}개가 측정 기준(test 분할, 클래스당 {MIN_PER_CLASS}개, AUROC CI 하한 {MIN_AUROC_CI_LOW}) 미충족."
    )
    lines.append(summary)
    return lines, passed
