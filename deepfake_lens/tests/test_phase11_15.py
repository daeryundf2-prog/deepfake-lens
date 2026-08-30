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
        self.assertEqual(explanation.band, "high")

    def test_explain_low_score(self) -> None:
        """Low score should return low band."""
        explanation = explain_classification(20, [])
        self.assertEqual(explanation.band, "low")

    def test_explain_medium_score(self) -> None:
        """Medium score should return medium band."""
        explanation = explain_classification(50, [])
        self.assertEqual(explanation.band, "medium")

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
