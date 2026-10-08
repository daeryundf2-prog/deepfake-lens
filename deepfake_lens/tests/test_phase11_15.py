"""Tests for Phase 11-15 modules."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deepfake_lens.evidence import (
    EvidenceChain,
    ForensicReport,
    create_evidence_chain,
    verify_integrity,
    generate_forensic_report,
)
from deepfake_lens.xai import (
    XAIExplanation,
    FeatureImportance,
    explain_classification,
    format_explanation_text,
)
from deepfake_lens.batch import (
    BatchProcessor,
    BatchJob,
    BatchResult,
)


class EvidenceTest(unittest.TestCase):
    """Test cases for evidence module."""

    def test_create_evidence_chain(self) -> None:
        """create_evidence_chain should return an EvidenceChain."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            f.flush()
            chain = create_evidence_chain(f.name, {"score": 50})
            self.assertIsInstance(chain, EvidenceChain)
            self.assertGreater(len(chain.file_hash), 0)

    def test_evidence_chain_to_json(self) -> None:
        """EvidenceChain to_json should return a dictionary."""
        chain = EvidenceChain(
            file_hash="abc123",
            file_path="/test/file.txt",
            file_size=100,
            analysis_timestamp="2026-05-30",
            analyst_id="test",
            tool_version="0.1.0",
            parameters={},
            results={},
            integrity_verified=True,
        )
        data = chain.to_json()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["file_hash"], "abc123")

    def test_generate_forensic_report(self) -> None:
        """generate_forensic_report should return a ForensicReport."""
        chain = EvidenceChain(
            file_hash="abc",
            file_path="/test",
            file_size=100,
            analysis_timestamp="2026-05-30",
            analyst_id="test",
            tool_version="0.1.0",
            parameters={},
            results={},
            integrity_verified=True,
        )
        report = generate_forensic_report([chain], [])
        self.assertIsInstance(report, ForensicReport)
        self.assertEqual(report.total_files, 1)


class XAITest(unittest.TestCase):
    """Test cases for XAI module."""

    def test_explain_classification(self) -> None:
        """explain_classification should return an XAIExplanation."""
        signals = [{"title": "Test", "weight": 20, "detail": "Test detail"}]
        explanation = explain_classification(80, signals)
        self.assertIsInstance(explanation, XAIExplanation)
        self.assertEqual(explanation.overall_score, 80)
        # D1: the 67/35 high/medium/low band was an unmeasured cutoff that
        # read as a conclusion; the explanation is now reference only.
        self.assertEqual(explanation.reference_band, "reference")
        self.assertEqual(explanation.signal_count, 1)
        self.assertIn("80/100", explanation.reference_note)

    def test_explain_low_score(self) -> None:
        """D1: a low score yields no band and no "자연스러운 콘텐츠" conclusion."""
        explanation = explain_classification(20, [])
        self.assertEqual(explanation.reference_band, "reference")
        text = " ".join([explanation.summary, *explanation.decision_path])
        for word in ("자연스러운 콘텐츠", "낮음", "가능성 높음"):
            self.assertNotIn(word, text)

    def test_explain_medium_score(self) -> None:
        """D1: a mid score yields no band and no "추가 확인 권장" tier."""
        explanation = explain_classification(50, [])
        self.assertEqual(explanation.reference_band, "reference")
        self.assertFalse(hasattr(explanation, "band"))
        self.assertNotIn("중간 점수 임계값", " ".join(explanation.decision_path))

    def test_format_explanation_text(self) -> None:
        """format_explanation_text should return a string."""
        explanation = explain_classification(80, [{"title": "Test", "weight": 20, "detail": "Detail"}])
        text = format_explanation_text(explanation)
        self.assertIsInstance(text, str)
        self.assertIn("80", text)

    def test_xai_explanation_to_json(self) -> None:
        """XAIExplanation to_json should return a dictionary."""
        explanation = explain_classification(50, [])
        data = explanation.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("overall_score", data)
        self.assertIn("summary", data)


class BatchTest(unittest.TestCase):
    """Test cases for batch module."""

    def test_batch_processor_creation(self) -> None:
        """BatchProcessor should be created."""
        processor = BatchProcessor(max_workers=2)
        self.assertEqual(processor.max_workers, 2)

    def test_process_batch(self) -> None:
        """process_batch should return a BatchJob."""
        processor = BatchProcessor(max_workers=2)
        
        def dummy_processor(path: Path) -> dict:
            return {"score": 50}
        
        with tempfile.TemporaryDirectory() as tmpdir:
            files = [Path(tmpdir) / f"test{i}.txt" for i in range(3)]
            for f in files:
                f.write_text("test")
            
            job = processor.process_batch(files, dummy_processor)
            self.assertIsInstance(job, BatchJob)
            self.assertEqual(job.total_files, 3)

    def test_batch_job_to_json(self) -> None:
        """BatchJob to_json should return a dictionary."""
        job = BatchJob(
            job_id="test",
            status="completed",
            total_files=10,
            processed_files=8,
            failed_files=2,
            start_time="2026-05-30",
            end_time="2026-05-30",
            results=[],
        )
        data = job.to_json()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["total_files"], 10)


if __name__ == "__main__":
    unittest.main()
