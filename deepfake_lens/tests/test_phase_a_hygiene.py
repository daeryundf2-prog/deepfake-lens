"""Regression tests for Phase A hygiene fixes: word-boundary tool matching,
forensic loader issue reporting, and --json-out parent creation."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.classifier import classify_text_content
from deepfake_lens.evidence import (
    EvidenceChain,
    create_evidence_chain,
    load_evidence_chains,
    save_evidence_chains,
)


class ClassifierBoundaryTest(unittest.TestCase):
    """Bare markers must not fire inside unrelated words.

    Known limitation (documented, not testable away by boundaries): a
    standalone word collision such as Apache's "Spark" vs iFlytek Spark
    still matches — distinguishing those needs multi-marker confidence,
    not string matching.
    """

    def _has_match(self, text: str) -> bool:
        return classify_text_content(text).primary_match is not None

    def test_sparked_does_not_match_spark(self) -> None:
        self.assertFalse(self._has_match("the change sparked a debate about provenance"))

    def test_invoked_verb_is_not_invokeai(self) -> None:
        self.assertFalse(self._has_match("please ensure the hook invoked a fallback"))

    def test_barking_is_not_suno_bark(self) -> None:
        self.assertFalse(self._has_match("a barking dog echoed across the yard"))

    def test_metadata_word_is_not_meta_imagine(self) -> None:
        self.assertFalse(self._has_match("we collected metadata about metadata standards"))

    def test_real_tool_still_detected(self) -> None:
        self.assertTrue(self._has_match("generated with midjourney v6 on discord"))
        self.assertTrue(
            self._has_match("generated in the stable diffusion webui with dpm++ 2m karras sampler")
        )

    def test_phrase_marker_respects_boundaries(self) -> None:
        # 'midjourney' inside 'midjourneyish' must not match; the standalone
        # word must.
        self.assertFalse(self._has_match("that style is very midjourneyish"))
        self.assertTrue(self._has_match("that style is very midjourney"))


class EvidenceLoaderTest(unittest.TestCase):
    """A forensic loader must report skipped entries instead of returning []."""

    def _chain(self, path: Path) -> EvidenceChain:
        return create_evidence_chain(path, results={"score": 10})

    def test_roundtrip_reports_no_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.txt"
            sample.write_text("evidence")
            chains_file = Path(tmp) / "chains.json"
            save_evidence_chains([self._chain(sample)], chains_file)
            chains, issues = load_evidence_chains(chains_file)
            self.assertEqual(len(chains), 1)
            self.assertEqual(issues, [])

    def test_malformed_entry_is_skipped_with_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chains_file = Path(tmp) / "chains.json"
            chains_file.write_text(
                json.dumps(
                    [
                        {"file_hash": "abc", "file_path": "/x", "file_size": 1,
                         "analysis_timestamp": "t", "analyst_id": "a", "tool_version": "v",
                         "parameters": {}, "results": {}, "integrity_verified": True},
                        {"totally": "wrong shape"},
                    ]
                ),
                encoding="utf-8",
            )
            chains, issues = load_evidence_chains(chains_file)
            self.assertEqual(len(chains), 1)
            self.assertEqual(len(issues), 1)
            self.assertIn("항목 1", issues[0])  # R4

    def test_invalid_json_reports_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chains_file = Path(tmp) / "chains.json"
            chains_file.write_text("{not json", encoding="utf-8")
            chains, issues = load_evidence_chains(chains_file)
            self.assertEqual(chains, [])
            self.assertTrue(any("JSON 형식 오류" in issue for issue in issues))  # R4

    def test_missing_file_reports_issue(self) -> None:
        chains, issues = load_evidence_chains("/nonexistent/chains.json")
        self.assertEqual(chains, [])
        self.assertTrue(any("찾을 수 없습니다" in issue for issue in issues))  # R4


class JsonOutParentCreationTest(unittest.TestCase):
    # Z5 (Gaps: 신규): this test pinned the old behaviour — --json-out created
    # missing parent folders (a typo scattered reports into new folders). A
    # missing parent is now a usage error before the scan (exit 2, nothing
    # created); an existing parent still gets the report.
    def test_scan_json_out_refuses_missing_parents(self) -> None:
        from deepfake_lens import cli

        repo_root = Path(__file__).resolve().parent.parent.parent
        sample = str(repo_root / "fixtures" / "deepfake-lens-sample")
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "deep" / "nested" / "report.json"
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                exit_code = cli.main(["scan", sample, "--recursive", "--no-default-engine", "--json-out", str(out_path)])
            self.assertEqual(exit_code, 2)
            self.assertIn(f"오류: 출력 폴더가 없습니다: {out_path.parent}", err.getvalue())
            self.assertFalse((Path(tmp) / "deep").exists())

            out_path.parent.mkdir(parents=True)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = cli.main(["scan", sample, "--recursive", "--no-default-engine", "--json-out", str(out_path)])
            self.assertEqual(exit_code, 0)
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn("items", payload)


if __name__ == "__main__":
    unittest.main()
