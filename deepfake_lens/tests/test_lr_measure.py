"""OMO-20: narrow-domain measurement and likelihood-ratio calibration."""

from __future__ import annotations

import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.calibration import auroc
from deepfake_lens.corpus_manifest import SCHEMA, manifest_sha256
from deepfake_lens.lr_measure import (
    HD,
    HP,
    LrCalibration,
    MeasureError,
    cllr,
    cluster_bootstrap_auroc_ci,
    load_scores,
    lr_statement,
    measure,
    pav_llrs,
    rank_auroc,
    rates_at,
    threshold_at_fpr,
    verbal,
)
from deepfake_lens.measurement_gate import MEASURED_ON_KEYS
from deepfake_lens.model_adapter import REFERENCE_CONFIDENCE, model_reference_signals
from deepfake_lens.result_types import ExternalModelAnalysis


def _pairs(n: int, pos_mean: float, neg_mean: float, *, seed: int, sd: float = 12.0) -> list[tuple[float, bool]]:
    rng = random.Random(seed)
    clip = lambda v: max(0.0, min(100.0, v))  # noqa: E731
    return [(clip(round(rng.gauss(pos_mean, sd))), True) for _ in range(n)] + [(clip(round(rng.gauss(neg_mean, sd))), False) for _ in range(n)]


def _write_corpus(root: Path, n_per_split: dict[str, int], pos_mean: float, neg_mean: float, *, seed: int = 1) -> tuple[Path, Path]:
    rng = random.Random(seed)
    items, rows = [], []
    for split, n in n_per_split.items():
        for label, mean in (("synthetic", pos_mean), ("real", neg_mean)):
            for k in range(n):
                for variant in ("original", "kakao"):
                    item_id = f"{split}-{label}-{k}-{variant}"
                    sha = f"{abs(hash((split, label, k))) % (16 ** 16):016x}" * 4
                    items.append({
                        "id": item_id, "relpath": f"{label}/gen/{variant}/{split}{k}.jpg", "sha256": sha if variant == "original" else sha[::-1],
                        "modality": "image", "label": label, "generator": "gen" if label == "synthetic" else "camera", "variant": variant,
                        "split": split, "source_note": "", "derived_from": None if variant == "original" else f"{split}-{label}-{k}-original",
                    })
                    rows.append({"id": item_id, "score": max(0.0, min(100.0, round(rng.gauss(mean, 12.0))))})
    manifest = root / "corpus.json"
    manifest.write_text(json.dumps({"schema": SCHEMA, "corpus_id": "omo20-test", "manifest_sha256": manifest_sha256(items), "items": items}), encoding="utf-8")
    scores = root / "scores.jsonl"
    scores.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return manifest, scores


class AurocAndBootstrapTest(unittest.TestCase):
    def test_rank_auroc_equals_the_pairwise_count_with_ties(self) -> None:
        pairs = _pairs(150, 55, 45, seed=3, sd=20)
        value, reference = rank_auroc(pairs), auroc(pairs)
        assert value is not None and reference is not None
        self.assertAlmostEqual(value, reference, places=12)

    def test_bootstrap_is_reproducible_and_clusters_widen_the_interval(self) -> None:
        base = _pairs(120, 60, 40, seed=5)
        clustered = [[pair] * 4 for pair in base]
        independent = [[pair] for pair in base for _ in range(4)]
        first = cluster_bootstrap_auroc_ci(clustered, resamples=400, seed=7)
        self.assertEqual(first, cluster_bootstrap_auroc_ci(clustered, resamples=400, seed=7))
        assert first is not None
        flat = cluster_bootstrap_auroc_ci(independent, resamples=400, seed=7)
        assert flat is not None
        self.assertGreater(first[1] - first[0], 1.5 * (flat[1] - flat[0]))

    def test_threshold_from_val_keeps_the_target_false_positive_rate(self) -> None:
        val = _pairs(400, 65, 35, seed=9)
        cut = threshold_at_fpr(val, 0.05)
        fpr, recall = rates_at(val, cut)
        self.assertLessEqual(fpr, 0.05)
        self.assertGreater(recall, 0.5)


class LikelihoodRatioTest(unittest.TestCase):
    def test_cllr_reference_values(self) -> None:
        self.assertAlmostEqual(cllr([0.0, 0.0], [0.0]), 1.0)
        self.assertEqual(cllr([math.inf], [-math.inf]), 0.0)
        self.assertAlmostEqual(cllr([math.log(9)], [-math.log(9)]), math.log2(10 / 9))
        self.assertEqual(cllr([-math.inf], [0.0]), math.inf)

    def test_pav_gives_zero_for_separated_and_one_for_uninformative_scores(self) -> None:
        separated = [(float(s), True) for s in range(60, 80)] + [(float(s), False) for s in range(10, 30)]
        llr = pav_llrs(separated)
        self.assertEqual(cllr([v for v, p in llr if p], [v for v, p in llr if not p]), 0.0)
        flat = [(50.0, True)] * 30 + [(50.0, False)] * 30
        llr = pav_llrs(flat)
        self.assertAlmostEqual(cllr([v for v, p in llr if p], [v for v, p in llr if not p]), 1.0)

    def test_enfsi_verbal_bands_follow_the_lr_direction(self) -> None:
        self.assertEqual(verbal(1.0), "어느 쪽도 지지하지 않음")
        self.assertEqual(verbal(5.0), f"{HP}을(를) 약하게 지지")
        self.assertEqual(verbal(50.0), f"{HP}을(를) 중간 정도로 지지")
        self.assertEqual(verbal(1 / 500), f"{HD}을(를) 중간보다 강하게 지지")
        self.assertEqual(verbal(2e6), f"{HP}을(를) 극히 강하게 지지")
        with self.assertRaises(MeasureError):
            verbal(0.0)

    def test_lr_is_bounded_by_the_calibration_sample_sizes(self) -> None:
        separated = [(float(s), True) for s in range(80, 100)] + [(float(s), False) for s in range(0, 40)]
        calibration = LrCalibration.fit(separated)
        self.assertAlmostEqual(calibration.lr(100.0), 41.0)
        self.assertAlmostEqual(calibration.lr(0.0), 1 / 21)


class MeasureTest(unittest.TestCase):
    def test_a_separable_domain_passes_the_unchanged_gate_and_validates_its_lr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 75, 25)
            payload = measure(manifest, load_scores(scores), variants=["kakao"], resamples=300)
            measured = payload["measured_on"]
            self.assertTrue(set(MEASURED_ON_KEYS) <= set(measured))
            self.assertEqual((measured["n_pos"], measured["n_neg"], measured["split"]), (220, 220, "test"))
            self.assertEqual(payload["gate"]["problems"], [])
            self.assertTrue(payload["gate"]["passed"])
            lr = payload["lr_profile"]
            self.assertTrue(lr["validated"], lr["reasons"])
            self.assertLess(lr["validation"]["cllr"], 1.0)
            self.assertLessEqual(lr["validation"]["cllr_min"], lr["validation"]["cllr"])

    def test_a_weak_or_small_domain_fails_the_gate_and_keeps_its_lr_unvalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 60, "test": 60}, 52, 48)
            payload = measure(manifest, load_scores(scores), variants=["kakao"], resamples=200)
            self.assertFalse(payload["gate"]["passed"])
            self.assertTrue(any("200" in problem for problem in payload["gate"]["problems"]))
            self.assertFalse(payload["lr_profile"]["validated"])
            self.assertIsNone(lr_statement(payload["lr_profile"], 90.0))

    def test_a_split_without_both_classes_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 20, "test": 20}, 70, 30)
            with self.assertRaises(MeasureError):
                measure(manifest, load_scores(scores), variants=["telegram"], resamples=50)


class ReferenceSignalLrTest(unittest.TestCase):
    def _signals(self, lr_profile: dict[str, object]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "m-runtime.json"
            profile.write_text(json.dumps({"lr_profile": "lr.json"}), encoding="utf-8")
            (Path(tmp) / "lr.json").write_text(json.dumps(lr_profile), encoding="utf-8")
            model = ExternalModelAnalysis(
                available=False, score=0, confidence="gated", model="zoo", detail="",
                models=[{"profile": str(profile), "model": "m", "display_name": "M", "available": False, "score": 90, "confidence": REFERENCE_CONFIDENCE}],
            )
            return [signal.detail for signal in model_reference_signals(model)]

    def test_a_validated_lr_profile_adds_the_evidence_strength_to_the_reference_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 75, 25)
            lr = measure(manifest, load_scores(scores), variants=["kakao"], resamples=100)["lr_profile"]
        (detail,) = self._signals(lr)
        self.assertIn("증거 강도(우도비)", detail)
        self.assertIn(HP, detail)
        self.assertIn("결론이 아닌 증거 강도", detail)

    def test_an_unvalidated_lr_profile_adds_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 60, "test": 60}, 52, 48)
            lr = measure(manifest, load_scores(scores), variants=["kakao"], resamples=50)["lr_profile"]
        (detail,) = self._signals(lr)
        self.assertNotIn("우도비", detail)


if __name__ == "__main__":
    unittest.main()
