from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterable

from .calibration import auroc, binary_metrics
from .core import analyze_file
from .datasets import is_negative_label, is_positive_label
from .fusion import DEFAULT_FUSION_PROFILE, FusionProfile, component_scores, component_scores_from_json
from .result_text import member_row_path, row_identity, unescape_row_path


FEEDBACK_REPORT_VERSION = "feedback-report-v1"

_NO_OBSERVATIONS_NOTE = (
    "검사 결과와 일치하는 감정인 라벨이 없어 가중치 제안을 만들지 않았습니다"
)
_SUGGESTION_NOTE = (
    "suggested_profile은 제안일 뿐 자동으로 적용되지 않습니다. "
    "보고서를 검토한 뒤 사용하려면 --fusion-profile로 프로필 파일을 지정하십시오."
)
_THRESHOLD_NOTE = (
    "suggested_profile은 기준 프로필의 임계값을 바꾸지 않습니다. 임계값은 여기서 고치지 말고 "
    "`calibrate` 또는 `fusion`으로 명시적으로 다시 맞추십시오."
)


@dataclass(frozen=True)
class FeedbackEntry:
    """One examiner verdict: ``{path, expected_label, notes?}``.

    ``embedded_result`` carries a serialized ClassificationResult dict when the
    feedback line itself is an annotated scan row (e.g. an item copied out of
    ``scan --json-out`` with an added ``expected_label``), so no rescan is
    needed for that entry.
    """

    path: str
    expected_label: str
    notes: str = ""
    embedded_result: dict | None = None

    def to_json(self) -> dict[str, object]:
        return {"path": self.path, "expected_label": self.expected_label, "notes": self.notes}


@dataclass(frozen=True)
class FeedbackObservation:
    """An examiner label joined to a concrete scan score/components."""

    path: str
    expected_label: str
    expected_positive: bool
    score: int
    components: dict[str, int]
    signals: tuple[tuple[str, int], ...]


# R9-5 (round 9): a labels file with a UTF-8 BOM, or a cut-off JSONL line,
# was read as 0 labels and the command exited 0. The BOM is stripped; a line
# that is not JSON is an error naming its line number (exit 2 in the CLI).
FEEDBACK_LINE_UNPARSABLE = "피드백 파일 {line}행을 해석할 수 없습니다: {path} — {reason}"
FEEDBACK_NO_LABELS = (
    "피드백 파일에 사용할 수 있는 라벨 행이 없습니다: {path} (행 {rows}개) — 행마다 `path`(또는 `file`)와 "
    "인식 가능한 `expected_label` 값(`ai`, `synthetic`, `real`, `authentic` 등)이 필요합니다"
)


class FeedbackFileError(ValueError):
    """A labels file that cannot be parsed; ``str()`` is the Korean reason (R9-5)."""


def parse_feedback_rows(text: str, path: Path | str) -> list[object]:
    """The rows of a labels file's text: a JSON array/object, else JSON Lines (R9-5).

    A leading UTF-8 BOM is ignored. In JSON Lines every non-blank line must
    parse — :class:`FeedbackFileError` names the first line that does not
    (a cut-off file is never read as fewer labels).
    """
    from .error_text import read_error_ko

    text = text.removeprefix("\ufeff")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("records", "items", "feedback", "entries"):
            if isinstance(payload.get(key), list):
                return list(payload[key])
        # A lone JSON object is a single examiner row.
        return [payload]
    rows: list[object] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise FeedbackFileError(FEEDBACK_LINE_UNPARSABLE.format(line=number, path=path, reason=read_error_ko(exc))) from exc
    return rows


def load_feedback(path: Path | str) -> list[FeedbackEntry]:
    """Load examiner labels from JSONL or a JSON array.

    Accepted keys per row: ``path``/``file``, ``expected_label``/``label``/
    ``expected``/``verdict``, optional ``notes``, and optional ``result``
    (an embedded scan result dict). Rows without a usable path or a
    recognized positive/negative label are skipped. A line that is not JSON
    raises :class:`FeedbackFileError` (R9-5).
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # P4: the CLI refuses these first (cli_inputs)
        return []
    rows = parse_feedback_rows(text, path)
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        entry = _entry_from_row(row)
        if entry is not None:
            entries.append(entry)
    return entries


def _entry_from_row(row: dict) -> FeedbackEntry | None:
    path = row.get("path", row.get("file"))
    if not isinstance(path, str) or not path.strip():
        return None
    raw_label = row.get("expected_label", row.get("label", row.get("expected", row.get("verdict", ""))))
    label = str(raw_label).strip().lower()
    if not (is_positive_label(label) or is_negative_label(label)):
        return None
    embedded = row.get("result") if isinstance(row.get("result"), dict) else None
    return FeedbackEntry(path=path, expected_label=label, notes=str(row.get("notes", "") or ""), embedded_result=embedded)


def _real_row_path(item: dict) -> str:
    """The real path a scan row stands for (R10-4).

    A top-level row's ``path`` is escaped when the real name holds "::" or
    "\\:" (R9-1: "tri:::c.png" is the row "tri\\:\\:\\:c.png"); a member row is
    its ``container``/``member`` fields. Labels name real files, so the join
    key is the unescaped path ("<container>::<member>" for a member).
    """
    container, member = row_identity(item)
    real = unescape_row_path(container)
    return real if member is None else member_row_path(real, member)


def _path_suffix_of(entry_path: str, real: str) -> bool:
    """``real`` (a row path relative to the scan root) ends ``entry_path`` at a path boundary."""
    if not real:
        return False
    if entry_path == real:
        return True
    return any(entry_path.endswith(sep + real) for sep in ("/", "\\"))


def _unique_match(
    entry_path: str,
    by_recorded: dict[str, dict],
    by_real: dict[str, list[dict]],
    real_paths: list[tuple[str, dict]],
    by_basename: dict[str, list[dict]],
) -> dict | None:
    """The one scan row a label names (R10-4), or None when none or several do."""
    if entry_path in by_recorded:
        return by_recorded[entry_path]
    for candidates in (
        by_real.get(entry_path),
        [item for real, item in real_paths if _path_suffix_of(entry_path, real)],
        by_basename.get(PurePath(entry_path).name),
    ):
        if candidates:
            return candidates[0] if len(candidates) == 1 else None
    return None


def observations_from_scan_payload(
    scan_payload: dict[str, object], entries: Iterable[FeedbackEntry]
) -> tuple[list[FeedbackObservation], list[str]]:
    """Join feedback entries to items of a prior ``scan --json-out`` payload.

    R10-4 (round 10): the join key is each row's real path — the unescaped
    top-level path, or the ``container``/``member`` fields of a member row —
    never the escaped display path (a label for the real file "tri:::c.png"
    did not match the row "tri\\:\\:\\:c.png"). A label matches, in order: the
    row whose recorded ``path`` equals it; the row whose real path equals it;
    the row whose real path ends it at a path boundary (an absolute label
    path against a row path relative to the scan root); the row with the
    same file name. The first rule with candidates decides, and several
    candidates (an ambiguous label) match nothing.
    Entries that match no analyzed item land in the returned unmatched list.
    """
    items = [item for item in scan_payload.get("items", []) if isinstance(item, dict)] if isinstance(scan_payload.get("items"), list) else []
    real_paths = [(_real_row_path(item), item) for item in items]
    # Recorded row paths are distinct (R9-1); real paths can collide (a real
    # folder "evil.zip::inner" and the member rows of evil.zip) and then
    # match nothing rather than a guess.
    by_recorded = {str(item.get("path", "")): item for item in items}
    by_real: dict[str, list[dict]] = {}
    for real, item in real_paths:
        by_real.setdefault(real, []).append(item)
    by_basename: dict[str, list[dict]] = {}
    for real, item in real_paths:
        by_basename.setdefault(PurePath(real).name, []).append(item)

    observations: list[FeedbackObservation] = []
    unmatched: list[str] = []
    for entry in entries:
        observation = _observation_from_entry(entry)
        if observation is None:
            item = _unique_match(entry.path, by_recorded, by_real, real_paths, by_basename)
            observation = _observation_from_item(entry, item) if item is not None else None
        if observation is None:
            unmatched.append(entry.path)
        else:
            observations.append(observation)
    return observations, unmatched


def observations_live(
    entries: Iterable[FeedbackEntry],
    *,
    pixel_mode: str = "off",
    pixel_max_side: int = 192,
    model_path: Path | None = None,
) -> tuple[list[FeedbackObservation], list[str]]:
    """Score each labeled path directly (used when no prior scan JSON exists).

    Entries carrying an embedded scan ``result`` reuse it instead of a rescan.
    Missing/unanalyzable files land in the returned unmatched list.
    """
    observations: list[FeedbackObservation] = []
    unmatched: list[str] = []
    for entry in entries:
        observation = _observation_from_entry(entry)
        if observation is None:
            try:
                item = analyze_file(
                    Path(entry.path), pixel_mode=pixel_mode, pixel_max_side=pixel_max_side, model_path=model_path
                )
            except OSError:
                item = None
            if item is not None and item.result is not None:
                result = item.result
                observation = FeedbackObservation(
                    path=entry.path,
                    expected_label=entry.expected_label,
                    expected_positive=is_positive_label(entry.expected_label),
                    score=result.score,
                    components=component_scores(result),
                    signals=tuple((signal.title, signal.weight) for signal in result.signals),
                )
        if observation is None:
            unmatched.append(entry.path)
        else:
            observations.append(observation)
    return observations, unmatched


def build_feedback_report(
    entries: list[FeedbackEntry],
    observations: list[FeedbackObservation],
    unmatched: list[str],
    *,
    base_profile: FusionProfile | None = None,
) -> dict[str, object]:
    """Per-signal accuracy report plus an advisory fusion-weight suggestion."""
    base = base_profile or DEFAULT_FUSION_PROFILE
    notes = [_SUGGESTION_NOTE, _THRESHOLD_NOTE]
    payload: dict[str, object] = {
        "version": FEEDBACK_REPORT_VERSION,
        "entries": len(entries),
        "matched": len(observations),
        "unmatched": sorted(unmatched),
        "agreement": None,
        "per_signal": {},
        "per_component": {},
        "suggested_profile": None,
        "notes": notes,
    }
    if not observations:
        notes.insert(0, _NO_OBSERVATIONS_NOTE)
        return payload

    pairs = [(observation.score, observation.expected_positive) for observation in observations]
    agreement = binary_metrics(pairs, base.threshold)
    auc = auroc(pairs)
    if auc is not None:
        agreement["auroc"] = auc
    payload["agreement"] = agreement
    payload["per_signal"] = _per_signal_stats(observations)

    component_keys = list(base.weights)
    per_component: dict[str, object] = {}
    for key in component_keys:
        component_pairs = [
            (int(observation.components.get(key, 0)), observation.expected_positive) for observation in observations
        ]
        component_auc = auroc(component_pairs)
        positive_values = [value for value, positive in component_pairs if positive]
        negative_values = [value for value, positive in component_pairs if not positive]
        per_component[key] = {
            "auroc": component_auc,
            "mean_positive": sum(positive_values) / len(positive_values) if positive_values else None,
            "mean_negative": sum(negative_values) / len(negative_values) if negative_values else None,
            "base_weight": base.weights[key],
        }
    payload["per_component"] = per_component

    suggested_weights, weight_notes = suggest_fusion_weights(per_component, base.weights)
    notes.extend(weight_notes)
    suggested = FusionProfile(
        version="fusion-profile-v1",
        weights=suggested_weights,
        threshold=base.threshold,
        unknown_below=base.unknown_below,
    )
    payload["suggested_profile"] = suggested.to_json()
    return payload


def suggest_fusion_weights(
    per_component: dict[str, object], base_weights: dict[str, float]
) -> tuple[dict[str, float], list[str]]:
    """Weights proportional to each component's observed label separation.

    Contribution is ``max(0, component AUROC - 0.5)``: components that do not
    separate examiner labels get no suggested weight. Suggested weights are
    normalized in integer 1e-4 units so they always sum to 1.0 (within float
    representation). When nothing separates, the base weights are returned
    normalized with an explanatory note rather than a fabricated ranking.
    """
    notes: list[str] = []
    keys = list(base_weights)
    raw: dict[str, float] = {}
    for key in keys:
        stats = per_component.get(key, {})
        auc = stats.get("auroc") if isinstance(stats, dict) else None
        raw[key] = max(0.0, float(auc) - 0.5) if isinstance(auc, (int, float)) else 0.0
    total = sum(raw.values())
    if total <= 0:
        notes.append(
            "감정인 라벨을 분리한 구성 요소가 없습니다(모든 AUROC <= 0.5). "
            "제안 가중치는 기준 프로필을 정규화한 값 그대로입니다"
        )
        base_total = sum(max(0.0, value) for value in base_weights.values()) or 1.0
        suggested = {key: max(0.0, base_weights[key]) / base_total for key in keys}
    else:
        suggested = {key: raw[key] / total for key in keys}
    # Normalize in integer basis points (1e-4 units): deterministic, and the
    # single residual assignment keeps the sum within one basis point of 1.0.
    # Consumers divide by the weight total (fusion.py), so a sub-ulp float
    # remainder carries no meaning — an exact 1.0 is not promised.
    units = {key: int(round(value * 10000)) for key, value in suggested.items()}
    residual = 10000 - sum(units.values())
    if units and residual:
        largest = max(units, key=lambda key: units[key])
        units[largest] += residual
    weights = {key: unit / 10000 for key, unit in units.items()}
    return weights, notes


def _per_signal_stats(observations: list[FeedbackObservation]) -> dict[str, object]:
    """Agreement stats per evidence-signal title across matched items."""
    buckets: dict[str, dict[str, list[int]]] = {}
    for observation in observations:
        for title, weight in observation.signals:
            bucket = buckets.setdefault(title, {"positive": [], "negative": []})
            bucket["positive" if observation.expected_positive else "negative"].append(int(weight))
    stats: dict[str, object] = {}
    for title in sorted(buckets):
        bucket = buckets[title]
        positives, negatives = bucket["positive"], bucket["negative"]
        mean_positive = sum(positives) / len(positives) if positives else 0.0
        mean_negative = sum(negatives) / len(negatives) if negatives else 0.0
        stats[title] = {
            "items": len(positives) + len(negatives),
            "mean_weight_positive": mean_positive,
            "mean_weight_negative": mean_negative,
            "separation": mean_positive - mean_negative,
        }
    return stats


def _observation_from_entry(entry: FeedbackEntry) -> FeedbackObservation | None:
    if entry.embedded_result is None:
        return None
    result = entry.embedded_result
    score = result.get("score", 0)
    if not isinstance(score, (int, float)):
        return None
    signals = tuple(
        (str(signal.get("title", "")), int(signal.get("weight", 0)))
        for signal in result.get("signals", [])
        if isinstance(signal, dict) and isinstance(signal.get("weight"), (int, float))
    )
    return FeedbackObservation(
        path=entry.path,
        expected_label=entry.expected_label,
        expected_positive=is_positive_label(entry.expected_label),
        score=int(score),
        components=component_scores_from_json(result),
        signals=signals,
    )


def _observation_from_item(entry: FeedbackEntry, item: dict | None) -> FeedbackObservation | None:
    result = (item or {}).get("result")
    if not isinstance(result, dict):
        return None
    return _observation_from_entry(
        FeedbackEntry(path=entry.path, expected_label=entry.expected_label, notes=entry.notes, embedded_result=result)
    )
