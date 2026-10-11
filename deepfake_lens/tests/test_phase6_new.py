"""Tests for new Phase 6 modules (AI Agent, 3D, Avatar)."""

from __future__ import annotations

import unittest

from deepfake_lens.ai_agent import (
    analyze_agent_content,
)
from deepfake_lens.threed import (
    analyze_3d_content,
)
from deepfake_lens.avatar import (
    analyze_avatar,
)


class AIAgentTest(unittest.TestCase):
    """Test cases for AI agent detection."""

    def test_analyze_empty_returns_low(self) -> None:
        """Empty analysis should return low score."""
        result = analyze_agent_content()
        self.assertEqual(result.score, 0)
        self.assertEqual(result.reference_band, "reference")  # D1: layer modules report reference_band/reference_note, never a band

    def test_analyze_text_with_agent_marker(self) -> None:
        """Text with agent marker should be detected."""
        result = analyze_agent_content(text="This was created by an OpenAI agent")
        self.assertGreater(result.score, 0)

    def test_analyze_text_with_workflow_pattern(self) -> None:
        """Text with workflow pattern should be detected."""
        result = analyze_agent_content(text="Step 1: Analyze the data. Step 2: Generate report.")
        self.assertGreater(result.score, 0)

    def test_analyze_metadata_with_agent_marker(self) -> None:
        """Metadata with agent marker should be detected."""
        result = analyze_agent_content(metadata={"agent": "langchain"})
        self.assertGreater(result.score, 0)

    def test_agent_type_classification(self) -> None:
        """Agent type should be classified correctly.

        R18-18 (round 18): this asserted an ``agent_type`` classification — the layer diagnostic now has
        none (raw signals only, like every other layer); the marker is still a raw signal.
        """
        result = analyze_agent_content(text="Created using LangChain framework")
        self.assertFalse(hasattr(result, "agent_type"))
        self.assertIn("langchain 마커 발견", [signal.title for signal in result.signals])

    def test_agent_analysis_to_json(self) -> None:
        """AgentAnalysis to_json should return a dictionary."""
        result = analyze_agent_content(text="Test text")
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        # R18-18 (round 18): asserted "agent_type" in the JSON — the classification (and "confidence") is gone.
        self.assertNotIn("agent_type", data)
        self.assertNotIn("confidence", data)


class AgentDiagnosticIsRawTest(unittest.TestCase):
    """R18-18 (round 18): the `agent` layer diagnostic gave an ordinary text with "operator" and " done"
    ``confidence: "high"``, raw score 90 and ``agent_type: "openai_agent"`` — a grade and a class no other layer
    diagnostic gives ("no band, no verdict"); markers matched inside other words."""

    def test_ordinary_text_gets_no_marker_grade_or_class(self) -> None:
        text = "The crane operator said the abandoned job was done; the doner kebab was finished by noon."
        result = analyze_agent_content(text=text)
        titles = [signal.title for signal in result.signals]
        self.assertNotIn("openai_agent 마커 발견", titles)  # "operator" alone is an ordinary word
        self.assertEqual(titles.count("에이전트 워크플로우 패턴"), 2)  # "done" and "finished" as words, not "abandoned"/"doner"
        self.assertEqual(set(result.to_json()), {"score", "reference_band", "reference_note", "signals", "limitations"})

    def test_markers_match_whole_words_only(self) -> None:
        self.assertEqual(analyze_agent_content(text="the openai operator ran it").signals[0].title, "openai_agent 마커 발견")
        self.assertEqual(analyze_agent_content(text="haystacks of paper").score, 0)
        self.assertEqual(analyze_agent_content(metadata={"note": "toolbox"}).score, 0)
        self.assertGreater(analyze_agent_content(metadata={"tool": "x"}).score, 0)

    def test_the_cli_diagnostic_has_no_confidence(self) -> None:
        import contextlib
        import io
        import json
        import tempfile
        from pathlib import Path

        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "memo.txt")
            path.write_text("The operator said it was done.", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["agent", "--file", str(path), "--format", "json"])
        self.assertEqual(code, 0)
        diagnostic = json.loads(out.getvalue())["layer_diagnostics"]["agent_markers"]["diagnostic"]
        self.assertEqual(set(diagnostic), {"signals", "limitations"})  # no "confidence", no "agent_type"


class ThreeDTest(unittest.TestCase):
    """Test cases for 3D content detection."""

    def test_analyze_empty_returns_low(self) -> None:
        """Empty analysis should return low score."""
        result = analyze_3d_content()
        self.assertEqual(result.score, 0)
        self.assertEqual(result.reference_band, "reference")  # D1: layer modules report reference_band/reference_note, never a band

    def test_analyze_text_with_nerf_marker(self) -> None:
        """Text with NeRF marker should be detected."""
        result = analyze_3d_content(text="This is a Neural Radiance Field rendering")
        self.assertGreater(result.score, 0)

    def test_analyze_text_with_gaussian_marker(self) -> None:
        """Text with Gaussian Splatting marker should be detected."""
        result = analyze_3d_content(text="Generated using Gaussian Splatting technique")
        self.assertGreater(result.score, 0)

    def test_analyze_file_extension(self) -> None:
        """GLB file extension should be detected."""
        result = analyze_3d_content(file_path="model.glb")
        self.assertGreater(result.score, 0)

    def test_content_type_classification(self) -> None:
        """Content type should be classified correctly."""
        result = analyze_3d_content(text="This is a NeRF model")
        self.assertIn(result.content_type, ["nerf", "gaussian_splatting", "mesh_generation", "point_cloud", "unknown"])

    def test_threed_analysis_to_json(self) -> None:
        """ThreeDAnalysis to_json should return a dictionary."""
        result = analyze_3d_content(text="Test text")
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("content_type", data)


class AvatarTest(unittest.TestCase):
    """Test cases for avatar detection."""

    def test_analyze_empty_returns_low(self) -> None:
        """Empty analysis should return low score."""
        result = analyze_avatar()
        self.assertEqual(result.score, 0)
        self.assertEqual(result.reference_band, "reference")  # D1: layer modules report reference_band/reference_note, never a band

    def test_analyze_metadata_with_avatar_marker(self) -> None:
        """Metadata with avatar marker should be detected."""
        result = analyze_avatar(metadata={"tool": "heygen"})
        self.assertGreater(result.score, 0)

    def test_analyze_metadata_with_synthesia(self) -> None:
        """Metadata with Synthesia marker should be detected."""
        result = analyze_avatar(metadata={"source": "synthesia"})
        self.assertGreater(result.score, 0)

    def test_analyze_file_video_extension(self) -> None:
        """Video file extension should be detected."""
        result = analyze_avatar(file_path="avatar.mp4")
        self.assertGreater(result.score, 0)

    def test_path_object_is_accepted(self) -> None:
        """Regression: core passes a Path; ``.lower()`` on it crashed the
        avatar check (AttributeError) for every video file."""
        from pathlib import Path

        result = analyze_avatar(file_path=Path("/evidence/clip.MP4"))
        self.assertEqual(result.score, analyze_avatar(file_path="/evidence/clip.MP4").score)
        self.assertGreater(result.score, 0)
        self.assertEqual(analyze_avatar(file_path=Path("/evidence/photo.png")).score, 0)

    def test_core_avatar_check_runs_and_format_alone_is_not_evidence(self) -> None:
        """Regression (core._deep_video_layers): the avatar check now runs
        ("ran", not "failed"), and the container format alone adds no
        evidence row."""
        import tempfile
        from pathlib import Path

        from deepfake_lens.core import _deep_video_layers
        from deepfake_lens.result_types import CoverageStatus

        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.mp4"
            clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            layers = _deep_video_layers(clip)
        [entry] = [entry for entry in layers.coverage if entry.check == "avatar"]
        self.assertEqual(entry.status, CoverageStatus.RAN, entry.reason)
        self.assertFalse([e for e in layers.evidence if e.layer == "avatar"])

    def test_avatar_type_classification(self) -> None:
        """Avatar type should be classified correctly."""
        result = analyze_avatar(metadata={"tool": "heygen"})
        self.assertIn(result.avatar_type, ["heygen", "synthesia", "d_id", "generic_avatar", "unknown"])

    def test_avatar_analysis_to_json(self) -> None:
        """AvatarAnalysis to_json should return a dictionary."""
        result = analyze_avatar()
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("avatar_type", data)


if __name__ == "__main__":
    unittest.main()
