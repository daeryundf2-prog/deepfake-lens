"""Machine learning classifier for AI-generated image detection.

Uses extracted features to classify images as AI-generated or natural.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClassificationResult:
    prediction: str
    confidence: float
    probability_ai: float
    probability_natural: float
    features_used: list[str]
    model_version: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class SimpleClassifier:
    """Simple rule-based classifier using extracted features."""
    
    def __init__(self):
        self.model_version = "1.0"
        self.threshold = 0.5
        self.feature_means: dict[str, float] = {}
        self.feature_stds: dict[str, float] = {}
    
    def predict(self, features: dict[str, float]) -> ClassificationResult:
        """Predict if image is AI-generated based on features."""
        ai_score = 0.0
        features_used = []
        
        # Use statistical comparison if we have training data
        if self.feature_means and self.feature_stds:
            for key in ["hist_entropy", "texture_variance", "std", "edge_density"]:
                if key in features and key in self.feature_means:
                    mean = self.feature_means[key]
                    std = self.feature_stds[key]
                    if std > 0:
                        z_score = abs(features[key] - mean) / std
                        ai_score += min(0.25, z_score * 0.1)
                        features_used.append(f"{key}_zscore")
        else:
            # Rule-based approach
            if "hist_entropy" in features and features["hist_entropy"] < 6.0:
                ai_score += 0.2
                features_used.append("low_entropy")
            
            if "texture_variance" in features and features["texture_variance"] < 500:
                ai_score += 0.15
                features_used.append("low_texture")
            
            if "std" in features and features["std"] < 30:
                ai_score += 0.15
                features_used.append("smooth_image")
            
            if "edge_density" in features and features["edge_density"] > 0.15:
                ai_score += 0.1
                features_used.append("edge_pattern")
        
        ai_score = min(1.0, ai_score)
        prediction = "ai" if ai_score >= self.threshold else "natural"
        confidence = abs(ai_score - 0.5) * 2
        
        return ClassificationResult(
            prediction=prediction,
            confidence=confidence,
            probability_ai=ai_score,
            probability_natural=1.0 - ai_score,
            features_used=features_used,
            model_version=self.model_version,
        )


def train_simple_classifier(training_data: list[dict[str, Any]]) -> SimpleClassifier:
    """Train a simple classifier from labeled data."""
    classifier = SimpleClassifier()
    
    if not training_data:
        return classifier
    
    # Calculate feature statistics from training data
    feature_keys = ["hist_entropy", "texture_variance", "std", "edge_density", 
                    "color_std_r", "color_std_g", "color_std_b"]
    
    for key in feature_keys:
        values = [d.get(key, 0) for d in training_data if key in d]
        if values:
            classifier.feature_means[key] = sum(values) / len(values)
            variance = sum((v - classifier.feature_means[key]) ** 2 for v in values) / len(values)
            classifier.feature_stds[key] = variance ** 0.5
    
    return classifier


def save_classifier(classifier: SimpleClassifier, path: Path) -> None:
    """Save classifier to file."""
    data = {
        "model_version": classifier.model_version,
        "threshold": classifier.threshold,
        "feature_means": classifier.feature_means,
        "feature_stds": classifier.feature_stds,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_classifier(path: Path) -> SimpleClassifier:
    """Load classifier from file."""
    if not path.exists():
        return SimpleClassifier()
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        classifier = SimpleClassifier()
        classifier.model_version = data.get("model_version", "1.0")
        classifier.threshold = data.get("threshold", 0.5)
        classifier.feature_means = data.get("feature_means", {})
        classifier.feature_stds = data.get("feature_stds", {})
        return classifier
    except (json.JSONDecodeError, KeyError):
        return SimpleClassifier()
