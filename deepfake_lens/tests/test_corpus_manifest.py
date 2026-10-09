"""corpus-manifest-v1: round trip, split group invariance, verify (WP-I, G27)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.cli import main as cli_main
from deepfake_lens.corpus_manifest import (
    SCHEMA,
    ManifestError,
    assign_splits,
    build_manifest,
    canonical_items_bytes,
    item_id,
    load_manifest,
    manifest_sha256,
    origin_key,
    parse_ratio,
    run_corpus_cli,
    verify_manifest,
    write_manifest,
)

VARIANTS = ("original", "kakao", "telegram", "jpeg_q50")


def _make_corpus(root: Path, *, originals: int = 12) -> None:
    for label, generator, suffix in (("real", "galaxy-s23", ".jpg"), ("synthetic", "midjourney-v6", ".png")):
        for index in range(originals):
            for variant in VARIANTS:
                path = root / label / generator / variant / f"{label[0]}{index:03d}{suffix}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"{label}-{index}-{variant}".encode())
    (root / "README.md").write_text("ignored", encoding="utf-8")
    (root / "real" / "galaxy-s23" / "notes.xyz").write_text("unknown suffix", encoding="utf-8")


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli_main(argv)
    return code, out.getvalue()


class ManifestBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "corpus"
        _make_corpus(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_infers_labels_and_derivation(self) -> None:
        manifest, skipped = build_manifest(self.root, corpus_id="t-img", label_from_dir=True)
        self.assertEqual(manifest["schema"], SCHEMA)
        self.assertEqual(manifest["corpus_id"], "t-img")
        self.assertEqual(len(manifest["items"]), 2 * 12 * len(VARIANTS))
        self.assertIn("README.md", skipped)
        self.assertIn("real/galaxy-s23/notes.xyz", skipped)
        by_rel = {item["relpath"]: item for item in manifest["items"]}
        kakao = by_rel["real/galaxy-s23/kakao/r000.jpg"]
        self.assertEqual((kakao["label"], kakao["generator"], kakao["variant"], kakao["modality"]), ("real", "galaxy-s23", "kakao", "image"))
        self.assertEqual(kakao["derived_from"], item_id("real/galaxy-s23/original/r000.jpg"))
        self.assertIsNone(by_rel["real/galaxy-s23/original/r000.jpg"]["derived_from"])
        self.assertEqual(manifest["manifest_sha256"], manifest_sha256(manifest["items"]))

    def test_unknown_label_dir_is_an_error(self) -> None:
        (self.root / "maybe" / "x" / "original").mkdir(parents=True)
        (self.root / "maybe" / "x" / "original" / "a.png").write_bytes(b"x")
        with self.assertRaises(ManifestError):
            build_manifest(self.root, label_from_dir=True)

    def test_without_label_from_dir_items_are_unlabeled(self) -> None:
        manifest, _ = build_manifest(self.root)
        self.assertTrue(all(item["label"] is None for item in manifest["items"]))
        problems = verify_manifest(manifest, self.root)
        self.assertTrue(any(problem.startswith("라벨 없음") for problem in problems))

    def test_round_trip_is_byte_stable(self) -> None:
        manifest, _ = build_manifest(self.root, corpus_id="t-img", label_from_dir=True)
        assign_splits(manifest, seed=3)
        path = Path(self._tmp.name) / "m.json"
        write_manifest(path, manifest)
        loaded = load_manifest(path)
        self.assertEqual(loaded["items"], manifest["items"])
        self.assertEqual(loaded["manifest_sha256"], manifest["manifest_sha256"])
        self.assertEqual(canonical_items_bytes(loaded["items"]), canonical_items_bytes(list(reversed(manifest["items"]))))
        rewritten = Path(self._tmp.name) / "m2.json"
        write_manifest(rewritten, loaded)
        self.assertEqual(path.read_bytes(), rewritten.read_bytes())

    def test_manifest_matches_committed_schema(self) -> None:
        """contracts/corpus-manifest-v1.schema.json describes what build writes."""
        from deepfake_lens.tests.test_json_contract import _check_object

        schema_path = Path(__file__).resolve().parents[2] / "contracts" / "corpus-manifest-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        manifest, _ = build_manifest(self.root, label_from_dir=True)
        assign_splits(manifest, seed=2)
        path = Path(self._tmp.name) / "m.json"
        write_manifest(path, manifest, root_hint="corpus")
        payload = json.loads(path.read_text(encoding="utf-8"))
        _check_object(self, payload, schema, schema)
        item_schema = schema["$defs"]["item"]
        for item in payload["items"]:
            self.assertEqual(set(item) - set(item_schema["properties"]), set())
            self.assertRegex(item["sha256"], item_schema["properties"]["sha256"]["pattern"])
        self.assertRegex(payload["manifest_sha256"], schema["properties"]["manifest_sha256"]["pattern"])

    def test_hash_covers_items_not_corpus_id(self) -> None:
        manifest, _ = build_manifest(self.root, corpus_id="a", label_from_dir=True)
        other, _ = build_manifest(self.root, corpus_id="b", label_from_dir=True)
        self.assertEqual(manifest["manifest_sha256"], other["manifest_sha256"])
        other["items"][0]["label"] = "edited"
        self.assertNotEqual(manifest_sha256(manifest["items"]), manifest_sha256(other["items"]))

    def test_load_rejects_bad_files(self) -> None:
        bad = Path(self._tmp.name) / "bad.json"
        for content in ("{nope", json.dumps({"schema": "other", "items": []}), json.dumps({"schema": SCHEMA})):
            bad.write_text(content, encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(bad)
        with self.assertRaises(ManifestError):
            load_manifest(Path(self._tmp.name) / "missing.json")
        with self.assertRaises(ManifestError):
            build_manifest(Path(self._tmp.name) / "missing-dir")


class SplitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "corpus"
        _make_corpus(self.root, originals=30)
        self.manifest, _ = build_manifest(self.root, label_from_dir=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_variants_of_one_original_share_a_split(self) -> None:
        report = assign_splits(self.manifest, seed=11, ratio=parse_ratio("60/20/20"))
        self.assertEqual(report.groups, 60)
        by_origin: dict[str, set[str]] = {}
        refs = {str(item["id"]): item for item in self.manifest["items"]}
        for item in self.manifest["items"]:
            by_origin.setdefault(origin_key(item, refs), set()).add(item["split"])
        self.assertEqual(len(by_origin), 60)
        self.assertTrue(all(len(splits) == 1 for splits in by_origin.values()))
        counts = {split: sum(report.counts[split].values()) for split in ("train", "val", "test")}
        self.assertEqual(sum(counts.values()), len(self.manifest["items"]))
        # 60 groups of 4 at 60/20/20 → 36/12/12 groups per split.
        self.assertEqual(counts, {"train": 144, "val": 48, "test": 48})
        for split in ("train", "val", "test"):
            self.assertEqual(report.counts[split]["real"], report.counts[split]["synthetic"])

    def test_split_is_deterministic_and_seed_dependent(self) -> None:
        first, _ = build_manifest(self.root, label_from_dir=True)
        second, _ = build_manifest(self.root, label_from_dir=True)
        third, _ = build_manifest(self.root, label_from_dir=True)
        assign_splits(first, seed=5)
        assign_splits(second, seed=5)
        assign_splits(third, seed=6)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertNotEqual(first["manifest_sha256"], third["manifest_sha256"])

    def test_group_by_field_and_external_sha(self) -> None:
        assign_splits(self.manifest, seed=1, group_by="generator")
        per_generator: dict[str, set[str]] = {}
        for item in self.manifest["items"]:
            per_generator.setdefault(item["generator"], set()).add(item["split"])
        self.assertTrue(all(len(splits) == 1 for splits in per_generator.values()))
        with self.assertRaises(ManifestError):
            assign_splits(self.manifest, seed=1, group_by="no_such_field")
        external = "b" * 64
        items = [
            {"id": "x1", "relpath": "a", "sha256": "1" * 64, "derived_from": external},
            {"id": "x2", "relpath": "b", "sha256": "2" * 64, "derived_from": external},
        ]
        refs = {item["id"]: item for item in items}
        self.assertEqual(origin_key(items[0], refs), origin_key(items[1], refs))
        with self.assertRaises(ManifestError):
            origin_key({"id": "x3", "relpath": "c", "derived_from": "dangling"}, refs)
        loop = {"x4": {"id": "x4", "derived_from": "x5"}, "x5": {"id": "x5", "derived_from": "x4"}}
        with self.assertRaises(ManifestError):
            origin_key(loop["x4"], loop)

    def test_parse_ratio(self) -> None:
        self.assertEqual(parse_ratio("60/20/20"), (0.6, 0.2, 0.2))
        self.assertEqual(parse_ratio("0.8,0.1,0.1"), (0.8, 0.1, 0.1))
        for bad in ("60/40", "a/b/c", "-1/1/1", "0/0/0"):
            with self.assertRaises(ManifestError):
                parse_ratio(bad)


class VerifyAndCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "corpus"
        _make_corpus(self.root, originals=5)
        self.manifest_path = self.base / "manifest.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cli_build_split_verify_and_tamper(self) -> None:
        code, out = _run(["corpus", "build", str(self.root), "--out", str(self.manifest_path), "--label-from-dir", "--corpus-id", "t"])
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["items"], 2 * 5 * len(VARIANTS))
        stored = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["root_hint"], "corpus")
        code, out = _run(["corpus", "split", "--manifest", str(self.manifest_path), "--seed", "4", "--ratio", "60/20/20"])
        self.assertEqual(code, 0, out)
        self.assertTrue(all(item["split"] in {"train", "val", "test"} for item in load_manifest(self.manifest_path)["items"]))
        code, out = _run(["corpus", "verify", "--manifest", str(self.manifest_path)])
        self.assertEqual(code, 0, out)
        self.assertIn("검증 통과", out)

        victim = self.root / "real" / "galaxy-s23" / "kakao" / "r000.jpg"
        victim.write_bytes(b"tampered")
        code, out = _run(["corpus", "verify", "--manifest", str(self.manifest_path), "--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("해시 불일치", out)
        victim.unlink()
        code, out = _run(["corpus", "verify", "--manifest", str(self.manifest_path)])
        self.assertEqual(code, 1)
        self.assertIn("파일 없음", out)

    def test_verify_detects_edited_items(self) -> None:
        manifest, _ = build_manifest(self.root, label_from_dir=True)
        write_manifest(self.manifest_path, manifest)
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        payload["items"][0]["label"] = "edited"
        payload["items"][1]["split"] = "holdout"
        payload["items"][2]["relpath"] = "../escape.jpg"
        payload["items"].append(dict(payload["items"][3]))
        problems = verify_manifest(payload, self.root)
        joined = "\n".join(problems)
        self.assertIn("manifest_sha256 불일치", joined)
        self.assertIn("허용되지 않는 split", joined)
        self.assertIn("코퍼스 밖", joined)
        self.assertIn("중복 id", joined)
        payload["manifest_sha256"] = "zz"
        self.assertIn("64자리", "\n".join(verify_manifest(payload, self.root)))
        payload["items"][0]["label"] = "fake"
        self.assertIn("허용되지 않는 라벨", "\n".join(verify_manifest(payload, self.root)))

    def test_cli_usage_errors(self) -> None:
        code, out = _run(["corpus"])
        self.assertEqual(code, 2)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, out = _run(["corpus", "verify", "--manifest", str(self.base / "missing.json")])
        self.assertEqual(code, 2)
        # N4: errors go to stderr ("오류: 파일을 찾을 수 없습니다: …"), stdout stays empty
        # (the message used to be printed on stdout).
        self.assertIn("오류: 파일을 찾을 수 없습니다: ", err.getvalue())
        self.assertEqual(out, "")
        import argparse

        self.assertEqual(run_corpus_cli(argparse.Namespace(corpus_command=None)), 2)


if __name__ == "__main__":
    unittest.main()
