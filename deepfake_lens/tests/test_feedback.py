"""Tests for the examiner feedback loop (R-8d)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.feedback import (
    FeedbackEntry,
    build_feedback_report,
    load_feedback,
    observations_from_scan_payload,
    observations_live,
    suggest_fusion_weights,
)
from deepfake_lens.fusion import DEFAULT_FUSION_PROFILE


def _item(path: str, score: int, pixel: int, signals: tuple = (), confidence: str = "unknown") -> dict:
    return {
        "path": path,
        "name": Path(path).name,
        "status": "analyzed",
        "result": {
            "score": score,
            "signals": [{"title": title, "detail": "", "weight": weight} for title, weight in signals],
            "pixel_analysis": {"available": True, "score": pixel},
            "model_analysis": {"available": False, "score": 0},
            "source_guess": {"label": "", "confidence": confidence},
        },
    }


def _scan_payload() -> dict:
    items = [_item(f"/data/ai_{i}.png", 80, 80) for i in range(5)]
    items += [_item(f"/data/real_{i}.png", 10, 10) for i in range(5)]
    return {"items": items}


def _entries(correct: bool = True) -> list[FeedbackEntry]:
    entries = [FeedbackEntry(f"/data/ai_{i}.png", "ai") for i in range(5)]
    entries += [FeedbackEntry(f"/data/real_{i}.png", "real") for i in range(5)]
    if not correct:
        # Examiner says a high-scoring item is actually real (scan disagreed).
        entries[0] = FeedbackEntry("/data/ai_0.png", "real", notes="verified camera original")
    return entries


class LoadFeedbackTest(unittest.TestCase):
    def test_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.jsonl"
            path.write_text(
                '{"path": "/a.png", "expected_label": "ai", "notes": "looks synthetic"}\n'
                '{"path": "/b.png", "verdict": "real"}\n'
                '{"path": "/c.png", "expected_label": "unknown"}\n'  # skipped: not pos/neg
                '{"file": "/d.png", "label": "fake"}\n'
                "not json at all\n",
                encoding="utf-8",
            )
            entries = load_feedback(path)
        self.assertEqual([entry.path for entry in entries], ["/a.png", "/b.png", "/d.png"])
        self.assertEqual(entries[0].notes, "looks synthetic")

    def test_json_array_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.json"
            path.write_text(json.dumps([{"path": "/a.png", "expected_label": "ai"}]), encoding="utf-8")
            self.assertEqual(len(load_feedback(path)), 1)
            self.assertEqual(load_feedback(Path(tmp) / "missing.jsonl"), [])


class FeedbackReportTest(unittest.TestCase):
    def test_agreement_metrics_perfect_labels(self) -> None:
        observations, unmatched = observations_from_scan_payload(_scan_payload(), _entries())
        self.assertEqual(unmatched, [])
        self.assertEqual(len(observations), 10)
        report = build_feedback_report(_entries(), observations, unmatched)
        self.assertEqual(report["matched"], 10)
        self.assertEqual(report["agreement"]["accuracy"], 1.0)
        self.assertEqual(report["agreement"]["auroc"], 1.0)

    def test_mislabeled_entry_shows_up_in_agreement(self) -> None:
        entries = _entries(correct=False)
        observations, _ = observations_from_scan_payload(_scan_payload(), entries)
        report = build_feedback_report(entries, observations, [])
        agreement = report["agreement"]
        # The relabeled high-score item counts as a false positive at threshold.
        self.assertEqual(agreement["false_positive"], 1)
        self.assertLess(agreement["accuracy"], 1.0)
        self.assertLess(agreement["auroc"], 1.0)

    def test_suggested_weights_sum_to_one_and_favor_separating_component(self) -> None:
        observations, unmatched = observations_from_scan_payload(_scan_payload(), _entries())
        report = build_feedback_report(_entries(), observations, unmatched)
        suggested = report["suggested_profile"]
        self.assertIsNotNone(suggested)
        weights = suggested["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        # Only the pixel component separates the labels here.
        self.assertGreater(weights["pixel"], 0.9)
        # Threshold is never silently changed: it matches the base profile.
        self.assertEqual(suggested["threshold"], DEFAULT_FUSION_PROFILE.threshold)
        self.assertTrue(any("advisory" in note for note in report["notes"]))

    def test_no_separating_component_keeps_base_weights(self) -> None:
        items = [_item(f"/data/x_{i}.png", 50, 50) for i in range(6)]
        entries = [FeedbackEntry(f"/data/x_{i}.png", "ai" if i % 2 else "real") for i in range(6)]
        observations, _ = observations_from_scan_payload({"items": items}, entries)
        report = build_feedback_report(entries, observations, [])
        weights = report["suggested_profile"]["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertEqual(weights, {key: round(value, 4) for key, value in DEFAULT_FUSION_PROFILE.weights.items()})
        self.assertTrue(any("no component separated" in note for note in report["notes"]))

    def test_unmatched_and_basename_matching(self) -> None:
        entries = [FeedbackEntry("ai_0.png", "ai"), FeedbackEntry("/elsewhere/none.png", "real")]
        observations, unmatched = observations_from_scan_payload(_scan_payload(), entries)
        self.assertEqual(len(observations), 1)  # unique basename matched
        self.assertEqual(unmatched, ["/elsewhere/none.png"])

    def test_empty_feedback_is_noop_with_message(self) -> None:
        report = build_feedback_report([], [], [])
        self.assertEqual(report["matched"], 0)
        self.assertIsNone(report["suggested_profile"])
        self.assertIsNone(report["agreement"])
        self.assertTrue(any("no examiner labels" in note for note in report["notes"]))

    def test_embedded_scan_row_needs_no_scan_payload(self) -> None:
        row = {"path": "/p.png", "expected_label": "ai", "result": _item("/p.png", 88, 70)["result"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            entries = load_feedback(path)
        observations, unmatched = observations_from_scan_payload({"items": []}, entries)
        self.assertEqual(unmatched, [])
        self.assertEqual(observations[0].score, 88)
        self.assertEqual(observations[0].components["pixel"], 70)


class SuggestWeightsTest(unittest.TestCase):
    def test_weights_sum_exactly(self) -> None:
        per_component = {
            "metadata": {"auroc": 0.9},
            "pixel": {"auroc": 0.7},
            "external_model": {"auroc": 0.62},
            "source": {"auroc": 0.5},
        }
        weights, _ = suggest_fusion_weights(per_component, DEFAULT_FUSION_PROFILE.weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=12)
        self.assertEqual(weights["source"], 0.0)
        self.assertGreater(weights["metadata"], weights["pixel"])


class LiveObservationTest(unittest.TestCase):
    def test_live_rescan_scores_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_file = root / "ai_note.txt"
            real_file = root / "real_note.txt"
            ai_file.write_text("As an AI language model, I cannot browse the web.", encoding="utf-8")
            real_file.write_text("plain meeting notes about lunch plans", encoding="utf-8")
            entries = [
                FeedbackEntry(str(ai_file), "ai"),
                FeedbackEntry(str(real_file), "real"),
                FeedbackEntry(str(root / "missing.txt"), "ai"),
            ]
            observations, unmatched = observations_live(entries)
        self.assertEqual(unmatched, [str(root / "missing.txt")])
        self.assertEqual(len(observations), 2)
        report = build_feedback_report(entries, observations, unmatched)
        self.assertIsNotNone(report["suggested_profile"])
        self.assertAlmostEqual(sum(report["suggested_profile"]["weights"].values()), 1.0)


if __name__ == "__main__":
    unittest.main()
