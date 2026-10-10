"""Import MediaPipe without its microphone module's library search (R15-8, round 15).

``import mediapipe`` imports ``mediapipe.tasks.python.audio.core.audio_record``
(microphone capture — never used here), which imports ``sounddevice``. On
Linux ``sounddevice``'s import runs ``ctypes.util.find_library`` for
PortAudio, which spawns ``ldconfig -p`` and then ``gcc``/``ld`` (three times
per name tried): child processes the shutdown cleanup does not track,
writing ``cc*``/``tmp*`` temp files in TMPDIR outside the session folder —
the verifier saw them from ``scan --deep-signals`` in the API venv.
``audio_record`` already copes with a failed import (``sd = None``), so
while MediaPipe is imported ``sys.modules["sounddevice"]`` is ``None`` (the
import raises ``ImportError`` at once) and is put back afterwards: another
user of ``sounddevice`` in the process is unaffected. Every MediaPipe import
of the package goes through :func:`import_mediapipe` (the AST meta-test in
``tests/test_mediapipe_import.py`` keeps it that way).
"""

from __future__ import annotations

import importlib
import sys
import threading
from types import ModuleType

# R15-8: modules MediaPipe imports that this package never uses and whose
# import spawns processes / writes temp files (sounddevice → find_library).
BLOCKED_DURING_IMPORT = ("sounddevice",)
_LOCK = threading.Lock()


def import_mediapipe(name: str = "mediapipe") -> ModuleType:
    """``importlib.import_module(name)`` for ``mediapipe`` or one of its submodules, with ``sounddevice`` blocked.

    Raises ``ImportError`` like a plain import when MediaPipe is absent.
    """
    if name != "mediapipe" and not name.startswith("mediapipe."):
        raise ValueError(f"not a mediapipe module: {name}")
    with _LOCK:
        placed = [blocked for blocked in BLOCKED_DURING_IMPORT if blocked not in sys.modules]
        for blocked in placed:
            sys.modules[blocked] = None  # type: ignore[assignment]  # an import of it raises ImportError
        try:
            return importlib.import_module(name)
        finally:
            for blocked in placed:
                if blocked in sys.modules and sys.modules[blocked] is None:
                    del sys.modules[blocked]


__all__ = ["BLOCKED_DURING_IMPORT", "import_mediapipe"]
