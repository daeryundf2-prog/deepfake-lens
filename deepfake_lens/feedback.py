from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .calibration import auroc, binary_metrics
from .core import analyze_file
from .datasets import is_negative_label, is_positive_label
from .fusion import DEFAULT_FUSION_PROFILE, FusionProfile, component_scores, component_scores_from_json


FEEDBACK_REPORT_VERSION = "feedback-report-v1"

_NO_OBSERVATIONS_NOTE = (
    "no examiner labels matched usable scan results; no weight suggestion emitted"
)
_SUGGESTION_NOTE = (
    "suggested_profile is advisory only: nothing is applied automatically. "
    "Review the report, then pass the profile file via --fusion-profile to use it."
)
_THRESHOLD_NOTE = (
    "suggested_profile keeps the base profile threshold unchanged; refit "
    "thresholds explicitly with `calibrate` or `fusion` instead of editing here."
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


def load_feedback(path: Path | str) -> list[FeedbackEntry]:
    """Load examiner labels from JSONL or a JSON array.

    Accepted keys per row: ``path``/``file``, ``expected_label``/``label``/
    ``expected``/``verdict``, optional ``notes``, and optional ``result``
    (an embedded scan result dict). Rows without a usable path or a
    recognized positive/negative label are skipped.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[object] = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("records", "items", "feedback", "entries"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        else:
            # A lone JSON object is a single examiner row.
            rows = [payload]
    else:
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
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


def observations_from_scan_payload(
    scan_payload: dict[str, object], entries: Iterable[FeedbackEntry]
) -> tuple[list[FeedbackObservation], list[str]]:
    """Join feedback entries to items of a prior ``scan --json-out`` payload.

    Paths match exactly first, then by unique basename. Entries that match no
    analyzed item land in the returned unmatched list.
    """
    items = [item for item in scan_payload.get("items", []) if isinstance(item, dict)] if isinstance(scan_payload.get("items"), list) else []
    by_path = {str(item.get("path", "")): item for item in items}
    by_basename: dict[str, list[dict]] = {}
    for item in items:
        by_basename.setdefault(Path(str(item.get("path", ""))).name, []).append(item)

    observations: list[FeedbackObservation] = []
    unmatched: list[str] = []
    for entry in entries:
        observation = _observation_from_entry(entry)
        if observation is None:
            item = by_path.get(entry.path)
            if item is None:
                candidates = by_basename.get(Path(entry.path).name, [])
                item = candidates[0] if len(candidates) == 1 else None
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
            "no component separated examiner labels (all AUROC <= 0.5); "
            "suggested weights keep the base profile normalized"
        )
        base_total = sum(max(0.0, value) for value in base_weights.values()) or 1.0
        suggested = {key: max(0.0, base_weights[key]) / base_total for key in keys}
    else:
        suggested = {key: raw[key] / total for key in keys}
    # Normalize in integer basis points (1e-4 units) so the residual distribution
    # is deterministic and unaffected by float addition order.
    units = {key: int(round(value * 10000)) for key, value in suggested.items()}
    residual = 10000 - sum(units.values())
    if units and residual:
        largest = max(units, key=lambda key: units[key])
        units[largest] += residual
    weights = {key: unit / 10000 for key, unit in units.items()}
    if weights:
        # Emit the largest weight last and pin it to the float complement of
        # the rest: sum() only gained compensated (Neumaier) summation for
        # floats in Python 3.12, and under the older left-to-right fold only
        # the final addend can pin the total to exactly 1.0.
        largest = max(keys, key=lambda key: weights[key])
        weights = {key: weights[key] for key in keys if key != largest}
        weights[largest] = 1.0 - sum(weights.values())
        for _ in range(8):  # absorb any residual double-rounding
            residual = 1.0 - sum(weights.values())
            if residual == 0.0:
                break
            weights[largest] = math.nextafter(
                weights[largest], math.inf if residual > 0 else -math.inf
            )
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
