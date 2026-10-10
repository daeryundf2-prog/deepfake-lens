"""The "deliberate skip" exception every check may raise (leaf module, no imports).

R15-1 (round 15): it lives here — not in :mod:`deepfake_lens.checks` — so
that :mod:`deepfake_lens.shutdown` and :mod:`deepfake_lens.native_path`
(which ``checks`` imports through ``error_text``) can raise a subclass of it
("종료 중") without an import cycle. ``checks.CheckSkipped`` is this class.
"""

from __future__ import annotations


class CheckSkipped(Exception):
    """Raised inside a check to record a deliberate, explained skip.

    Used for "not applicable" outcomes that are not errors, e.g. no face
    detected or input outside the measured range.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


__all__ = ["CheckSkipped"]
