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
    clopper_pearson_upper,
    cluster_bootstrap_auroc_ci,
    label_audit,
    load_clusters,
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

    def test_pav_pools_whole_tie_groups(self) -> None:
        pairs = [(0.0, False), (1.0, False), (1.0, True), (1.0, True)]
        llr = pav_llrs(pairs)
        self.assertEqual(llr[0][0], -math.inf)
        self.assertAlmostEqual(llr[1][0], math.log(2.0) - math.log(2 / 2))

    def test_pav_cost_is_never_above_a_monotone_calibration_on_tie_heavy_scores(self) -> None:
        rng = random.Random(11)
        pairs = [(float(min(100, int(rng.expovariate(1 / 6)))), True) for _ in range(400)] + [(float(min(100, int(rng.expovariate(1 / 1.5)))), False) for _ in range(400)]
        rng.shuffle(pairs)
        calibration = LrCalibration.fit(pairs)
        pav = pav_llrs(pairs)
        minimum = cllr([v for v, p in pav if p], [v for v, p in pav if not p])
        actual = cllr([calibration.llr(s) for s, p in pairs if p], [calibration.llr(s) for s, p in pairs if not p])
        self.assertLessEqual(minimum, actual + 1e-12)

    def test_enfsi_verbal_bands_follow_the_lr_direction(self) -> None:
        self.assertEqual(verbal(1.0), "어느 쪽도 지지하지 않음")
        self.assertEqual(verbal(5.0), f"{HP}을 약하게 지지")
        self.assertEqual(verbal(50.0), f"{HP}을 중간 정도로 지지")
        self.assertEqual(verbal(1 / 500), f"{HD}을 중간보다 강하게 지지")
        self.assertEqual(verbal(2e6), f"{HP}을 극히 강하게 지지")
        with self.assertRaises(MeasureError):
            verbal(0.0)

    def test_lr_never_exceeds_the_val_tail_ratio_and_never_falls_with_the_score(self) -> None:
        separated = [(float(s), True) for s in range(80, 100)] + [(float(s), False) for s in range(0, 40)]
        calibration = LrCalibration.fit(separated)
        self.assertAlmostEqual(calibration.lr(90.0), (11 / 21) / (1 / 41))
        self.assertAlmostEqual(calibration.lr(100.0), calibration.lr(90.0))
        self.assertAlmostEqual(calibration.lr(0.0), (1 / 21) / (11 / 41))
        self.assertTrue(calibration.at_bound(90.0))
        values = [calibration.lr(float(s)) for s in range(101)]
        self.assertEqual(values, sorted(values))

    def test_one_real_photo_in_the_top_tail_lowers_the_cap(self) -> None:
        base = [(float(s), True) for s in range(60, 100)] + [(float(s), False) for s in range(0, 60)]
        clean = LrCalibration.fit(base).lr(98.0)
        tainted = LrCalibration.fit([*base, (99.0, False)]).lr(98.0)
        self.assertLess(tainted, clean / 1.5)

    def test_the_table_survives_json(self) -> None:
        calibration = LrCalibration.fit(_pairs(200, 70, 30, seed=2))
        again = LrCalibration.from_json(calibration.to_json())
        for score in (0.0, 37.0, 63.0, 100.0):
            self.assertAlmostEqual(again.llr(score), calibration.llr(score), places=5)


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


class ReviewFollowUpTest(unittest.TestCase):
    """Per-generator results, person/speaker clusters, audit drops and label-audit bounds."""

    def _two_generators(self, root: Path) -> tuple[Path, dict[str, float]]:
        manifest, scores_path = _write_corpus(root, {"val": 220, "test": 220}, 75, 25)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        scores = load_scores(scores_path)
        for item in data["items"]:
            if item["label"] == "synthetic" and item["split"] == "test" and int(item["id"].split("-")[2]) % 2:
                item["generator"] = "hard"
                scores[item["id"]] = max(0.0, scores[item["id"]] - 40)
        data["manifest_sha256"] = manifest_sha256(data["items"])
        manifest.write_text(json.dumps(data), encoding="utf-8")
        return manifest, scores

    def test_each_generator_is_judged_and_the_weakest_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = self._two_generators(Path(tmp))
            payload = measure(manifest, scores, variants=["kakao"], resamples=200)
            rows = {row["generator"]: row for row in payload["per_generator"]}
            self.assertEqual(set(rows), {"gen", "hard"})
            self.assertEqual(rows["gen"]["n_pos"] + rows["hard"]["n_pos"], payload["measured_on"]["n_pos"])
            self.assertLess(rows["hard"]["auroc"], rows["gen"]["auroc"])
            self.assertFalse(rows["hard"]["passed"])
            self.assertEqual(payload["weakest_generator"]["generator"], "hard")

    def test_person_clusters_widen_the_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores_path = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 60, 40)
            items = json.loads(manifest.read_text(encoding="utf-8"))["items"]
            cluster_file = Path(tmp) / "clusters.jsonl"
            cluster_file.write_text("\n".join(json.dumps({"id": item["id"], "cluster": f"person-{int(item['id'].split('-')[2]) % 8}"}) for item in items), encoding="utf-8")
            scores = load_scores(scores_path)
            base = measure(manifest, scores, variants=["kakao"], resamples=300)
            grouped = measure(manifest, scores, variants=["kakao"], resamples=300, clusters=load_clusters(cluster_file))
            width = lambda m: m["measured_on"]["auroc_ci"][1] - m["measured_on"]["auroc_ci"][0]  # noqa: E731
            self.assertGreater(width(grouped), width(base))
            self.assertEqual(grouped["measured_on"]["auroc"], base["measured_on"]["auroc"])
            self.assertEqual(grouped["measured_on"]["bootstrap"]["origin_groups"], 8)

    def test_a_person_with_real_and_ai_files_is_resampled_as_one_unit(self) -> None:
        rng = random.Random(4)
        people = []
        for _ in range(40):
            offset = rng.gauss(0, 15)
            people.append([(offset + 60 + rng.gauss(0, 5), True), (offset + 40 + rng.gauss(0, 5), False)])
        joint = cluster_bootstrap_auroc_ci(people, resamples=400, seed=1)
        split = cluster_bootstrap_auroc_ci([[pair] for person in people for pair in person], resamples=400, seed=1)
        assert joint is not None and split is not None
        self.assertEqual(joint, cluster_bootstrap_auroc_ci(people, resamples=400, seed=1))
        self.assertNotEqual(joint, split)
        self.assertIsNone(cluster_bootstrap_auroc_ci([[(1.0, True)], [(2.0, True)]], resamples=10))

    def test_a_bad_cluster_row_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clusters.jsonl"
            path.write_text(json.dumps({"id": "a", "cluster": 3}), encoding="utf-8")
            with self.assertRaises(MeasureError):
                load_clusters(path)

    def test_hard_ai_dropped_by_the_audit_is_put_back_and_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 75, 25)
            dropped = {f"dropped-{k}": 5.0 for k in range(80)}
            payload = measure(manifest, load_scores(scores), variants=["kakao"], resamples=300, dropped=dropped)
            block = payload["dropped_by_audit"]
            self.assertEqual(block["n"], 80)
            self.assertAlmostEqual(block["fraction_of_ai"], round(80 / 300, 4))
            self.assertLess(block["auroc_reinserted_as_ai"], block["auroc_excluding_dropped"])
            self.assertFalse(payload["gate"]["passed"])
            self.assertTrue(any("되돌려" in problem for problem in payload["gate"]["problems"]))
            self.assertIsNone(measure(manifest, load_scores(scores), variants=["kakao"], resamples=50)["dropped_by_audit"])

    def test_clopper_pearson_upper_bounds(self) -> None:
        self.assertAlmostEqual(clopper_pearson_upper(0, 200), 1 - 0.05 ** (1 / 200), places=6)
        self.assertAlmostEqual(clopper_pearson_upper(6, 200), 0.0583, places=3)
        self.assertEqual(clopper_pearson_upper(3, 3), 1.0)
        with self.assertRaises(MeasureError):
            clopper_pearson_upper(5, 3)

    def test_a_label_audit_raises_the_required_low_and_caps_the_lr(self) -> None:
        audit = label_audit((6, 200), (0, 200))
        assert audit is not None
        self.assertAlmostEqual(audit["required_auroc_ci_low"], 0.861, places=3)
        self.assertAlmostEqual(audit["lr_cap"], 63, delta=1)
        self.assertIsNone(label_audit(None, None))
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 400, "test": 400}, 90, 10)
            plain = measure(manifest, load_scores(scores), variants=["kakao"], resamples=100)
            capped = measure(manifest, load_scores(scores), variants=["kakao"], resamples=100, audit_pos=(6, 200), audit_neg=(0, 200))
            top = lambda m: max(m["lr_profile"]["calibration"]["log10_lr_table"])  # noqa: E731
            self.assertGreater(top(plain), math.log10(audit["lr_cap"]))
            self.assertAlmostEqual(top(capped), math.log10(audit["lr_cap"]), places=5)
            self.assertEqual(capped["label_audit"]["required_auroc_ci_low"], audit["required_auroc_ci_low"])

    def test_a_label_audit_can_fail_a_domain_that_passes_without_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 62, 38)
            plain = measure(manifest, load_scores(scores), variants=["kakao"], resamples=300)
            low = plain["measured_on"]["auroc_ci"][0]
            self.assertTrue(plain["gate"]["passed"], (low, plain["gate"]["problems"]))
            audited = measure(manifest, load_scores(scores), variants=["kakao"], resamples=300, audit_pos=(40, 200), audit_neg=(0, 200))
            self.assertFalse(audited["gate"]["passed"])
            self.assertTrue(any("라벨 감사" in problem for problem in audited["gate"]["problems"]))


class ScoreWithProfileTest(unittest.TestCase):
    def test_items_are_read_from_the_corpus_root_not_the_manifest_folder(self) -> None:
        from unittest import mock

        from deepfake_lens import lr_measure

        with tempfile.TemporaryDirectory() as tmp:
            corpus, elsewhere = Path(tmp) / "corpus", Path(tmp) / "out"
            (corpus / "real/cam/original").mkdir(parents=True)
            elsewhere.mkdir()
            (corpus / "real/cam/original/a.jpg").write_bytes(b"x")
            items = [{"id": "a", "relpath": "real/cam/original/a.jpg"}]
            manifest = elsewhere / "m.json"
            manifest.write_text(json.dumps({"schema": SCHEMA, "root_hint": str(corpus), "items": items}), encoding="utf-8")
            profile = elsewhere / "p-runtime.json"
            profile.write_text("{}", encoding="utf-8")
            seen: list[Path] = []

            def fake(_profile: object, media: Path, *, base_dir: Path) -> ExternalModelAnalysis:
                seen.append(media)
                return ExternalModelAnalysis(available=True, score=42, confidence="low", model="m", detail="")

            with mock.patch("deepfake_lens.model_adapter._score_from_runtime_profile", fake):
                scores = lr_measure.score_items_with_profile(manifest, items, profile)
                self.assertEqual(scores, {"a": 42.0})
                self.assertEqual(seen, [corpus / "real/cam/original/a.jpg"])
                with self.assertRaises(MeasureError):
                    lr_measure.score_items_with_profile(manifest, [{"id": "b", "relpath": "../out/m.json"}], profile)


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

    def test_a_single_profile_analysis_finds_its_lr_profile_through_the_model_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 75, 25)
            lr = measure(manifest, load_scores(scores), variants=["kakao"], resamples=100)["lr_profile"]
            models = Path(tmp) / "models"
            models.mkdir()
            (models / "m-runtime.json").write_text(json.dumps({"modality": "image", "lr_profile": "lr.json"}), encoding="utf-8")
            (models / "lr.json").write_text(json.dumps(lr), encoding="utf-8")
            single = ExternalModelAnalysis(available=False, score=90, confidence=REFERENCE_CONFIDENCE, model="m", detail="")
            (detail,) = [signal.detail for signal in model_reference_signals(single, models)]
            (bare,) = [signal.detail for signal in model_reference_signals(single)]
        self.assertIn("증거 강도(우도비)", detail)
        self.assertNotIn("우도비", bare)

    def _good_lr(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 75, 25)
            return measure(manifest, load_scores(scores), variants=["kakao"], resamples=50)["lr_profile"]

    def test_a_damaged_lr_profile_leaves_the_raw_score_row_alone(self) -> None:
        good = self._good_lr()
        for change in (
            lambda p: p["calibration"].__setitem__("log10_lr_table", [-400.0] * 101),
            lambda p: p["calibration"].__setitem__("log10_lr_table", [float("nan")] * 101),
            lambda p: p.__setitem__("scope", ["kakao"]),
            lambda p: p.__setitem__("calibration", "broken"),
            lambda p: p["calibration"].__setitem__("log10_lr_table", [10 ** 400] * 101),
            lambda p: p["calibration"].__setitem__("a", 10 ** 400),
            lambda p: p["calibration"].__setitem__("log10_lr_table", ["0.5"] * 101),
        ):
            profile = json.loads(json.dumps(good))
            change(profile)
            (detail,) = self._signals(profile)
            self.assertIn("원점수 90/100", detail)
            self.assertNotIn("우도비", detail)

    def test_an_unreadable_lr_file_leaves_the_raw_score_row_alone(self) -> None:
        for raw in (b"\xff\xfe junk", b"[" * 100000, b'{"a": ' + b"9" * 5000 + b"}"):
            with tempfile.TemporaryDirectory() as tmp:
                profile = Path(tmp) / "m-runtime.json"
                profile.write_text(json.dumps({"lr_profile": "lr.json"}), encoding="utf-8")
                (Path(tmp) / "lr.json").write_bytes(raw)
                model = ExternalModelAnalysis(available=False, score=0, confidence="gated", model="zoo", detail="",
                                              models=[{"profile": str(profile), "model": "m", "display_name": "M", "available": False, "score": 90, "confidence": REFERENCE_CONFIDENCE}])
                (detail,) = [signal.detail for signal in model_reference_signals(model)]
            self.assertNotIn("우도비", detail)

    def test_a_scope_list_of_the_wrong_type_is_shown_as_the_whole_corpus(self) -> None:
        profile = json.loads(json.dumps(self._good_lr()))
        profile["scope"]["variants"] = 5
        (detail,) = self._signals(profile)
        self.assertIn("증거 강도(우도비)", detail)
        self.assertIn("전체 변형", detail)

    def test_an_lr_profile_name_outside_the_models_folder_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 220, "test": 220}, 75, 25)
            lr = measure(manifest, load_scores(scores), variants=["kakao"], resamples=50)["lr_profile"]
            models = Path(tmp) / "models"
            models.mkdir()
            (Path(tmp) / "lr.json").write_text(json.dumps(lr), encoding="utf-8")
            (models / "m-runtime.json").write_text(json.dumps({"lr_profile": "../lr.json"}), encoding="utf-8")
            model = ExternalModelAnalysis(available=False, score=0, confidence="gated", model="zoo", detail="",
                                          models=[{"profile": str(models / "m-runtime.json"), "model": "m", "display_name": "M", "available": False, "score": 90, "confidence": REFERENCE_CONFIDENCE}])
            (detail,) = [signal.detail for signal in model_reference_signals(model)]
        self.assertNotIn("우도비", detail)

    def test_an_unvalidated_lr_profile_adds_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest, scores = _write_corpus(Path(tmp), {"val": 60, "test": 60}, 52, 48)
            lr = measure(manifest, load_scores(scores), variants=["kakao"], resamples=50)["lr_profile"]
        (detail,) = self._signals(lr)
        self.assertNotIn("우도비", detail)


if __name__ == "__main__":
    unittest.main()
