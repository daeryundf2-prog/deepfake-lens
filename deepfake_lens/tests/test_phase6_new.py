"""Tests for new Phase 6 modules (AI Agent, 3D, Avatar)."""

from __future__ import annotations

import unittest

from deepfake_lens.ai_agent import (
    AgentAnalysis,
    AgentEvidenceSignal,
    analyze_agent_content,
)
from deepfake_lens.threed import (
    ThreeDAnalysis,
    ThreeDEvidenceSignal,
    analyze_3d_content,
)
from deepfake_lens.avatar import (
    AvatarAnalysis,
    AvatarEvidenceSignal,
    analyze_avatar,
)


class AIAgentTest(unittest.TestCase):
    """Test cases for AI agent detection."""

    def test_analyze_empty_returns_low(self) -> None:
        """Empty analysis should return low score."""
        result = analyze_agent_content()
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "low")

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
        """Agent type should be classified correctly."""
        result = analyze_agent_content(text="Created using LangChain framework")
        self.assertIn(result.agent_type, ["openai_agent", "anthropic_agent", "google_agent", "microsoft_agent", "framework_agent", "agentic_workflow", "unknown"])

    def test_agent_analysis_to_json(self) -> None:
        """AgentAnalysis to_json should return a dictionary."""
        result = analyze_agent_content(text="Test text")
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("agent_type", data)


class ThreeDTest(unittest.TestCase):
    """Test cases for 3D content detection."""

    def test_analyze_empty_returns_low(self) -> None:
        """Empty analysis should return low score."""
        result = analyze_3d_content()
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "low")

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
        self.assertEqual(result.band, "low")

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
