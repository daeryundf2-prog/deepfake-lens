"""Deprecated alias module for :mod:`deepfake_lens.rule_classifier`.

The classifier was always rule-based (fixed feature thresholds plus
z-score deviations), never a trained ML model, so the module was renamed
to say so. Importing from this module still works but emits a
DeprecationWarning.
"""

from __future__ import annotations

import warnings

from .rule_classifier import (  # noqa: F401
    ClassificationResult,
    RuleClassifier,
    SimpleClassifier,
    load_classifier,
    save_classifier,
    train_rule_classifier,
    train_simple_classifier,
)

warnings.warn(
    "deepfake_lens.ml_classifier는 폐기 예정입니다 — deepfake_lens.rule_classifier에서 import하십시오",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ClassificationResult",
    "RuleClassifier",
    "SimpleClassifier",
    "load_classifier",
    "save_classifier",
    "train_rule_classifier",
    "train_simple_classifier",
]
