"""Import MediaPipe without the side effects of modules it imports but this package never uses (R15-8, R16-4).

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
user of ``sounddevice`` in the process is unaffected.

R16-4 (round 16): ``mediapipe.tasks.python.vision.drawing_utils`` does
``import matplotlib.pyplot as plt`` at module level (only its
``plot_landmarks`` uses it). The first import of ``matplotlib.pyplot`` builds
the font cache: two untracked ``fc-list`` children and
``~/.cache/matplotlib/fontlist-*.json`` — or, with an unwritable home/XDG
cache, a ``$TMPDIR/matplotlib-*`` folder outside the session folder that a
SIGTERM left behind. (The R15-8 commit message said "no child process"; it
covered the linker children only.) ``matplotlib`` cannot simply be blocked —
the import of ``drawing_utils`` would fail and MediaPipe with it — so while
MediaPipe is imported every ``matplotlib`` / ``matplotlib.*`` module that is
not already loaded is served as an inert stub (:class:`_InertModule`: any
attribute use raises ``AttributeError``); the stubs leave ``sys.modules``
afterwards, so a later ``import matplotlib`` elsewhere gets the real package.
No font cache, no ``MPLCONFIGDIR``, no child process.

Every MediaPipe import of the package goes through :func:`import_mediapipe`
(the AST meta-test in ``tests/test_mediapipe_import.py`` keeps it that way).
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import threading
from types import ModuleType
from typing import Sequence

# R15-8: modules MediaPipe imports that this package never uses and whose
# import spawns processes / writes temp files (sounddevice → find_library).
# An import of them raises ImportError while MediaPipe is imported.
BLOCKED_DURING_IMPORT = ("sounddevice",)
# R16-4: packages MediaPipe imports at module level for helpers this package
# never calls, and whose import spawns processes / writes cache files
# (matplotlib.pyplot → font cache, fc-list). Served as inert stubs while
# MediaPipe is imported (a failed import would fail MediaPipe's import).
STUBBED_DURING_IMPORT = ("matplotlib",)
_LOCK = threading.Lock()


class _InertModule(ModuleType):
    """R16-4: a stand-in package for a module MediaPipe imports but this package never uses."""

    def __getattr__(self, name: str) -> object:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise AttributeError(f"{self.__name__}.{name}: MediaPipe 가져오기 동안 대체된 모듈입니다(R16-4 — 이 패키지는 사용하지 않음)")


class _InertLoader(importlib.abc.Loader):
    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        module = _InertModule(spec.name)
        module.__path__ = []  # a package: its submodules resolve through the finder too
        return module

    def exec_module(self, module: ModuleType) -> None:
        return None


class _InertFinder(importlib.abc.MetaPathFinder):
    """Serves :data:`STUBBED_DURING_IMPORT` (and their submodules) as :class:`_InertModule`."""

    def __init__(self, roots: Sequence[str]) -> None:
        self.roots = tuple(roots)
        self.loader = _InertLoader()

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> importlib.machinery.ModuleSpec | None:
        if any(fullname == root or fullname.startswith(root + ".") for root in self.roots):
            return importlib.machinery.ModuleSpec(fullname, self.loader, is_package=True)
        return None


def import_mediapipe(name: str = "mediapipe") -> ModuleType:
    """``importlib.import_module(name)`` for ``mediapipe`` or one of its submodules, without side-effect imports.

    ``sounddevice`` is blocked and ``matplotlib`` stubbed while it runs (see the
    module docstring). Raises ``ImportError`` like a plain import when
    MediaPipe is absent.
    """
    if name != "mediapipe" and not name.startswith("mediapipe."):
        raise ValueError(f"not a mediapipe module: {name}")
    with _LOCK:
        placed = [blocked for blocked in BLOCKED_DURING_IMPORT if blocked not in sys.modules]
        for blocked in placed:
            sys.modules[blocked] = None  # type: ignore[assignment]  # an import of it raises ImportError
        finder = _InertFinder([root for root in STUBBED_DURING_IMPORT if root not in sys.modules])
        if finder.roots:
            sys.meta_path.insert(0, finder)
        try:
            return importlib.import_module(name)
        finally:
            if finder in sys.meta_path:
                sys.meta_path.remove(finder)
            for module_name, module in list(sys.modules.items()):
                if isinstance(module, _InertModule) and getattr(module.__spec__, "loader", None) is finder.loader:
                    del sys.modules[module_name]
            for blocked in placed:
                if blocked in sys.modules and sys.modules[blocked] is None:
                    del sys.modules[blocked]


__all__ = ["BLOCKED_DURING_IMPORT", "STUBBED_DURING_IMPORT", "import_mediapipe"]
