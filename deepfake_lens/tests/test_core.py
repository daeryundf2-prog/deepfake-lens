from __future__ import annotations

import tempfile
import unittest
import json
import zlib
from pathlib import Path
from urllib.parse import urlencode

from deepfake_lens.benchmark import run_benchmark, write_benchmark, write_benchmark_markdown
from deepfake_lens.collection import build_collection_plan, write_collection_plan
from deepfake_lens.core import RiskBand, SourceConfidence, analyze_file, analyze_image_metadata, analyze_text, scan_directory
from deepfake_lens.datasets import audit_dataset, build_robustness_plan, discover_dataset, plan_dataset_splits, write_audit, write_manifest, write_robustness_plan, write_split_plan
from deepfake_lens.evaluate import calibrate_dataset, evaluate_dataset, evaluate_robustness_dataset, train_portable_baseline
from deepfake_lens.fusion import FusionProfile, apply_fusion_to_items, calibrate_fusion_profile, load_fusion_profile, write_fusion_profile
from deepfake_lens.model_registry import build_runtime_profile, list_detector_candidates, write_detector_registry, write_runtime_profile
from deepfake_lens.perf import run_performance_check, write_performance_check
from deepfake_lens.release import build_release_checklist
from deepfake_lens.reports import write_eval_html_report, write_html_report, write_pdf_report
from deepfake_lens.pixel import analyze_image_pixels
from deepfake_lens.png import read_png_metadata
from deepfake_lens.security import build_security_check, write_security_check
from deepfake_lens.training import build_neural_training_plan, write_neural_training_plan
from deepfake_lens.video import build_video_frame_plan, write_video_frame_plan
from deepfake_lens.webapp import _scan_payload


class DeepfakeLensCoreTest(unittest.TestCase):
    def test_text_ai_disclosure_is_high_and_assistant_source(self) -> None:
        result = analyze_text(
            "As an AI language model, I can provide a balanced approach.\n"
            "1. It is important to note the context.\n"
            "2. Overall, this can help users.\n"
            "3. In conclusion, it depends on multiple perspectives."
        )

        self.assertEqual(result.band, RiskBand.HIGH)
        self.assertEqual(result.source_guess.label, "AI 어시스턴트 문체 추정")

    def test_generic_ai_like_text_does_not_invent_vendor(self) -> None:
        result = analyze_text("결론적으로 이 문제는 다양한 관점에서 접근해야 합니다. 균형 잡힌 이해가 도움이 됩니다.")

        self.assertEqual(result.source_guess.confidence, SourceConfidence.UNKNOWN)
        self.assertEqual(result.source_guess.label, "출처 단서 없음")

    def test_stable_diffusion_a1111_metadata_is_high(self) -> None:
        result = analyze_image_metadata(
            {
                "png.parameters": (
                    "portrait\nNegative prompt: blurry\n"
                    "Steps: 30, Sampler: DPM++ 2M, CFG scale: 7, Seed: 1234, Model hash: abc123"
                )
            },
            dimensions=(1024, 1024),
        )

        self.assertEqual(result.band, RiskBand.HIGH)
        self.assertEqual(result.source_guess.label, "Stable Diffusion / A1111 추정")

    def test_comfyui_metadata_is_high(self) -> None:
        result = analyze_image_metadata({"png.workflow": '{"1":{"class_type":"KSampler","inputs":{"seed":1}}}'})

        self.assertEqual(result.band, RiskBand.HIGH)
        self.assertEqual(result.source_guess.label, "ComfyUI 추정")

    def test_modern_generator_metadata_source_rules(self) -> None:
        flux = analyze_image_metadata({"header.text": "Generated with FLUX.1 by Black Forest Labs"})
        ideogram = analyze_image_metadata({"header.text": "prompt exported from Ideogram"})
        imagen = analyze_image_metadata({"header.text": "Gemini Imagen generation"})

        self.assertEqual(flux.source_guess.label, "Flux / Black Forest Labs 추정")
        self.assertEqual(ideogram.source_guess.label, "Ideogram 추정")
        self.assertEqual(imagen.source_guess.label, "Google Imagen/Gemini 추정")

    def test_png_text_and_compressed_chunks_extract(self) -> None:
        compressed = zlib.compress(b"Steps: 20, Sampler: Euler, CFG scale: 7, Seed: 42")
        data = _png(
            _chunk(b"tEXt", b"workflow\x00{\"class_type\":\"KSampler\"}"),
            _chunk(b"zTXt", b"parameters\x00\x00" + compressed),
        )

        metadata = read_png_metadata(data)

        self.assertIn("KSampler", metadata["png.workflow"])
        self.assertIn("Sampler", metadata["png.parameters"])

    def test_png_compressed_chunks_are_bounded(self) -> None:
        compressed = zlib.compress(b"x" * (2 * 1024 * 1024 + 1))
        data = _png(_chunk(b"zTXt", b"parameters\x00\x00" + compressed))

        metadata = read_png_metadata(data)

        self.assertNotIn("png.parameters", metadata)

    def test_scan_directory_sorts_candidates_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_png_with_text(
                root / "ai.png",
                "parameters",
                "prompt\nNegative prompt: blur\nSteps: 10, Sampler: Euler, CFG scale: 5, Seed: 9",
            )
            (root / "note.txt").write_text("오늘은 친구와 점심을 먹었다.", encoding="utf-8")
            (root / "movie.mp4").write_bytes(b"not supported")

            summary, items = scan_directory(root)

            self.assertEqual(summary.high, 1)
            self.assertEqual(items[0].name, "ai.png")
            self.assertEqual(items[-1].status, "unsupported")

    def test_png_pixel_experts_flag_repeating_texture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checker.png"
            _write_rgb_png(path, 64, 64, lambda x, y: (255, 255, 255) if (x // 4 + y // 4) % 2 == 0 else (0, 0, 0))

            result = analyze_image_pixels(path, mode="deep")

            self.assertTrue(result.available)
            self.assertGreaterEqual(result.score, 45)
            self.assertTrue(any(expert.family == "spectral" for expert in result.experts))

    def test_analyze_file_merges_pixel_signal_and_heatmap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "tile.png"
            heatmap_dir = root / "heatmaps"
            _write_rgb_png(path, 64, 64, lambda x, y: (220, 220, 220) if (x // 8 + y // 8) % 2 == 0 else (30, 30, 30))

            item = analyze_file(path, root=root, pixel_mode="deep", heatmaps=True, heatmap_dir=heatmap_dir)

            self.assertIsNotNone(item.result)
            self.assertIsNotNone(item.result.pixel_analysis)
            self.assertTrue(item.result.pixel_analysis.available)
            self.assertTrue(item.result.pixel_analysis.heatmap_path)
            self.assertTrue(Path(item.result.pixel_analysis.heatmap_path).exists())
            self.assertEqual(item.result.band, RiskBand.MEDIUM)
            self.assertTrue(any(signal.title.startswith("픽셀") for signal in item.result.signals))

    def test_deep_pixel_analysis_exposes_all_recent_research_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tile.png"
            _write_rgb_png(path, 64, 64, lambda x, y: (245, 245, 245) if (x // 8 + y // 8) % 2 == 0 else (15, 15, 15))
            path.with_suffix(path.suffix + ".ivy.json").write_text(json.dumps({"score": 0.81, "explanation": "synthetic texture"}), encoding="utf-8")

            result = analyze_image_pixels(path, mode="deep")

            self.assertTrue(result.available)
            expert_names = {expert.name for expert in result.experts}
            self.assertIn("difference_in_difference_reconstruction", expert_names)
            self.assertIn("spark_il_spectral_retrieval", expert_names)
            self.assertIn("low_correlation_fractal_signal", expert_names)
            self.assertIn("alpha_blending_compositing", expert_names)
            self.assertIn("safe_pixel_localization", expert_names)
            self.assertIn("vrag_dfd_local_retrieval", expert_names)
            self.assertIn("fuzzy_decision_tree_fusion", expert_names)
            self.assertIn("ivy_xdetector_adapter", expert_names)
            self.assertEqual(result.fusion, "fuzzy_decision_tree_v0")
            self.assertTrue(any("reveal_evidence_chain" in item for item in result.implemented_references))
            self.assertTrue(any("agentfox_explainable_summary" in item for item in result.implemented_references))
            self.assertTrue(any("Ivy-xDetector" in expert.detail for expert in result.experts if expert.available))
            self.assertTrue(result.evidence_chain)

            summary, _ = scan_directory(Path(tmp), pixel_mode="deep")
            self.assertEqual(summary.total, 1)

    def test_dataset_eval_calibrate_train_cache_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_dir = root / "train" / "ai" / "stable-diffusion"
            real_dir = root / "train" / "real" / "camera"
            ai_dir.mkdir(parents=True)
            real_dir.mkdir(parents=True)
            _write_rgb_png(ai_dir / "ai.png", 64, 64, lambda x, y: (245, 245, 245) if (x // 8 + y // 8) % 2 == 0 else (15, 15, 15))
            _write_rgb_png(real_dir / "real.png", 64, 64, lambda x, y: (120 + (x % 5), 118 + (y % 7), 122))
            (ai_dir / "ai.png.model.json").write_text(json.dumps({"score": 0.9}), encoding="utf-8")

            summary, records = discover_dataset(root)
            self.assertEqual(summary.total, 2)
            self.assertEqual(summary.positive, 1)
            self.assertEqual(summary.negative, 1)

            manifest = root / "manifest.json"
            write_manifest(root, manifest)
            self.assertTrue(manifest.exists())

            evaluation = evaluate_dataset(root, pixel_mode="deep")
            self.assertEqual(evaluation["metrics"]["samples"], 2)
            self.assertIn("accuracy", evaluation["metrics"])
            self.assertIn("false_positives", evaluation["case_summary"])
            eval_html = root / "eval.html"
            write_eval_html_report(eval_html, evaluation, redact_paths=True)
            self.assertIn("Deepfake Lens Benchmark", eval_html.read_text(encoding="utf-8"))

            calibration = calibrate_dataset(root, pixel_mode="deep")
            self.assertIn("threshold", calibration)

            model = train_portable_baseline(root, pixel_mode="deep")
            self.assertEqual(model["type"], "deepfake-lens-portable-threshold-v1")

            cache = root / "scan-cache.json"
            scan_root = root / "train"
            first_summary, items = scan_directory(scan_root, recursive=True, pixel_mode="deep", cache_path=cache, workers=2)
            second_summary, cached_items = scan_directory(scan_root, recursive=True, pixel_mode="deep", cache_path=cache, workers=2)
            self.assertEqual(first_summary.total, 2)
            self.assertEqual(second_summary.cached, 2)
            self.assertEqual(len(cached_items), 2)

            html = root / "report.html"
            pdf = root / "report.pdf"
            heatmap_items = scan_directory(scan_root, recursive=True, pixel_mode="deep", heatmaps=True, heatmap_dir=root / "heatmaps")[1]
            write_html_report(html, second_summary, heatmap_items, redact_paths=True)
            write_pdf_report(pdf, second_summary, items, redact_paths=True)
            self.assertIn("Deepfake Lens Report", html.read_text(encoding="utf-8"))
            self.assertIn("data:image/png;base64", html.read_text(encoding="utf-8"))
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))

            model_profile = root / "external-model.json"
            model_profile.write_text(json.dumps({"type": "score-sidecar-v1", "name": "external fixture"}), encoding="utf-8")
            item = analyze_file(ai_dir / "ai.png", root=root, pixel_mode="off", model_path=model_profile)
            self.assertIsNotNone(item.result.model_analysis)
            self.assertTrue(item.result.model_analysis.available)

            fusion_payload = calibrate_fusion_profile(root, pixel_mode="deep", model_path=model_profile)
            profile_payload = fusion_payload["profile"]
            fusion_profile = FusionProfile(
                version=str(profile_payload["version"]),
                weights={str(key): float(value) for key, value in dict(profile_payload["weights"]).items()},
                threshold=int(profile_payload["threshold"]),
                unknown_below=int(profile_payload["unknown_below"]),
            )
            fusion_path = root / "fusion-profile.json"
            write_fusion_profile(fusion_path, fusion_profile)
            loaded_fusion = load_fusion_profile(fusion_path)
            self.assertIsNotNone(loaded_fusion)
            fused_eval = evaluate_dataset(root, pixel_mode="deep", model_path=model_profile, fusion_profile=loaded_fusion)
            self.assertEqual(fused_eval["threshold"], loaded_fusion.threshold)
            fused_items = apply_fusion_to_items(items, loaded_fusion)
            self.assertTrue(any(item.result and item.result.signals[0].title == "융합 점수" for item in fused_items))

            web_payload = _scan_payload(
                urlencode({"folder": str(scan_root), "recursive": "true", "pixel": "deep", "fusion_profile": str(fusion_path)}),
                default_folder=None,
            )
            self.assertIn("summary", web_payload)
            self.assertEqual(web_payload["summary"]["total"], 2)

    def test_dataset_audit_split_robustness_registry_video_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_dir = root / "ai" / "flux"
            real_dir = root / "real" / "camera"
            ai_dir.mkdir(parents=True)
            real_dir.mkdir(parents=True)
            _write_rgb_png(ai_dir / "ai.png", 8, 8, lambda x, y: (255, 255, 255))
            _write_rgb_png(ai_dir / "ai_copy.png", 8, 8, lambda x, y: (255, 255, 255))
            _write_rgb_png(real_dir / "real.png", 8, 8, lambda x, y: (10, 20, 30))

            manifest = root / "manifest.json"
            write_manifest(root, manifest, include_fingerprints=True)
            self.assertIn("fingerprint", manifest.read_text(encoding="utf-8"))

            audit = audit_dataset(root)
            self.assertTrue(audit.duplicate_groups)
            audit_path = root / "audit.json"
            write_audit(root, audit_path)
            self.assertTrue(audit_path.exists())

            split = plan_dataset_splits(root, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2, seed="fixture")
            self.assertEqual(split["summary"]["total"], 3)
            split_path = root / "split.json"
            write_split_plan(root, split_path, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2, seed="fixture")
            self.assertTrue(split_path.exists())

            robust = build_robustness_plan(root)
            self.assertTrue(robust["transforms"])
            robust_path = root / "robust.json"
            write_robustness_plan(root, robust_path)
            self.assertTrue(robust_path.exists())
            robustness_eval = evaluate_robustness_dataset(root, pixel_mode="off")
            self.assertIn("robustness_transforms", robustness_eval)

            registry = list_detector_candidates(focus="benchmark")
            self.assertGreaterEqual(registry["count"], 1)
            registry_path = root / "models.json"
            write_detector_registry(registry_path, focus="benchmark")
            self.assertIn("detector-registry-v1", registry_path.read_text(encoding="utf-8"))

            video = root / "clip.mp4"
            video.write_bytes(b"not a real video")
            plan = build_video_frame_plan(root, output_root=root / "frames")
            self.assertEqual(plan["count"], 1)
            video_plan = root / "video-plan.json"
            write_video_frame_plan(root, video_plan, frame_root=root / "frames")
            self.assertTrue(video_plan.exists())

            release = build_release_checklist(Path.cwd())
            self.assertTrue(release["entrypoint_present"])

    def test_scan_dedupe_skips_large_files_and_optional_runtime_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("As an AI language model, I can help.", encoding="utf-8")
            (root / "b.txt").write_text("As an AI language model, I can help.", encoding="utf-8")
            (root / "big.txt").write_text("x" * 2048, encoding="utf-8")

            summary, items = scan_directory(root, dedupe=True, max_file_bytes=1024)

            self.assertEqual(summary.total, 3)
            self.assertEqual(summary.duplicates, 1)
            self.assertEqual(summary.skipped, 1)
            self.assertTrue(any(item.duplicate_of in {"a.txt", "b.txt"} for item in items))

            hash_db = root / "hash-db.json"
            first_summary, _ = scan_directory(root, dedupe=True, hash_db_path=hash_db, max_file_bytes=1024)
            second_summary, _ = scan_directory(root, dedupe=True, hash_db_path=hash_db, max_file_bytes=1024)
            self.assertGreaterEqual(first_summary.duplicates, 1)
            self.assertGreaterEqual(second_summary.duplicates, 2)
            self.assertTrue(hash_db.exists())

            image = root / "image.png"
            _write_rgb_png(image, 8, 8, lambda x, y: (255, 255, 255))
            model = root / "detector.onnx"
            model.write_bytes(b"not an onnx model")
            item = analyze_file(image, root=root, model_path=model)
            self.assertIsNotNone(item.result)
            self.assertIsNotNone(item.result.model_analysis)
            self.assertFalse(item.result.model_analysis.available)

    def test_collection_benchmark_runtime_profile_training_and_security(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_dir = root / "train" / "ai" / "flux"
            real_dir = root / "train" / "real" / "camera"
            ai_dir.mkdir(parents=True)
            real_dir.mkdir(parents=True)
            _write_rgb_png(ai_dir / "ai.png", 8, 8, lambda x, y: (255, 255, 255))
            _write_rgb_png(real_dir / "real.png", 8, 8, lambda x, y: (10, 20, 30))

            collection = build_collection_plan(root)
            self.assertGreaterEqual(len(collection["targets"]), 2)
            collection_path = root / "collection.json"
            write_collection_plan(root, collection_path)
            self.assertTrue(collection_path.exists())

            profile = build_runtime_profile("aide-iclr-2025", root / "aide.onnx")
            self.assertEqual(profile["runtime"], "onnx")
            profile_path = root / "runtime-profile.json"
            write_runtime_profile(profile_path, "aide-iclr-2025", root / "aide.onnx")
            self.assertIn("runtime-profile-v1", profile_path.read_text(encoding="utf-8"))

            benchmark = run_benchmark(root, pixel_modes=["off"], model_paths=[None], max_files=2)
            self.assertEqual(len(benchmark["rows"]), 1)
            benchmark_path = root / "benchmark.json"
            benchmark_md = root / "benchmark.md"
            write_benchmark(benchmark_path, benchmark)
            write_benchmark_markdown(benchmark_md, benchmark)
            self.assertIn("Deepfake Lens Benchmark", benchmark_md.read_text(encoding="utf-8"))

            perf = run_performance_check(root, pixel_mode="off", max_files=10)
            self.assertGreaterEqual(perf["summary"]["total"], 2)
            self.assertGreater(perf["files_per_second"], 0)
            perf_path = root / "perf.json"
            write_performance_check(perf_path, perf)
            self.assertIn("performance-check-v1", perf_path.read_text(encoding="utf-8"))

            plan = build_neural_training_plan(root, output_dir=root / "training")
            self.assertIn("runtime_profile", plan["artifacts"])
            training_path = root / "training-plan.json"
            write_neural_training_plan(root, training_path, output_dir=root / "training")
            self.assertTrue(training_path.exists())

            security = build_security_check(Path.cwd())
            self.assertTrue(security["checks"])
            self.assertTrue(security["passed"])
            security_path = root / "security.json"
            write_security_check(Path.cwd(), security_path)
            self.assertTrue(security_path.exists())


def _write_png_with_text(path: Path, key: str, value: str) -> None:
    path.write_bytes(_png(_chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value.encode("utf-8"))))


def _write_rgb_png(path: Path, width: int, height: int, pixel_at) -> None:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(pixel_at(x, y))
        rows.append(bytes(row))
    compressed = zlib.compress(b"".join(rows))
    ihdr = _chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ihdr + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b""))


def _png(*chunks: bytes) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
    return signature + ihdr + b"".join(chunks) + _chunk(b"IEND", b"")


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + kind + payload + b"\x00\x00\x00\x00"


if __name__ == "__main__":
    unittest.main()
