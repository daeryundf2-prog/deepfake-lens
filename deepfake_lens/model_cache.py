"""Bounded LRU caches for resident model objects.

Heavyweight runners (AIDE ~3.3 GB, HF classifiers, PPL models) stay
resident across files; unbounded residency OOMs on multi-modality scans.
Each ``_ModelLRU`` instance self-registers so ``clear_all_model_caches``
can flush every cache without knowing where they were declared.
Extracted from ``model_adapter.py``; names are re-exported there.
"""

from __future__ import annotations

import importlib
import os
import threading
from collections import OrderedDict


_MODEL_CACHES: list["_ModelLRU"] = []


def _model_cache_limit() -> int:
    """Per-cache residency cap for loaded model objects."""
    try:
        return max(1, int(os.environ.get("DEEPFAKE_LENS_MODEL_CACHE_MAX", "4")))
    except ValueError:
        return 4


def _release_cached_model(entry: object) -> None:
    """Drop an evicted model entry and return accelerator memory.

    Cache entries hold torch modules/transformers pipelines with
    hundreds of MB–GB of weights; after eviction, GC + empty_cache
    hands CUDA/MPS reservations back so long scans do not OOM.
    """
    try:
        import gc

        del entry
        gc.collect()
        torch = importlib.import_module("torch")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is not None and mps.is_available() and hasattr(torch, "mps"):
            torch.mps.empty_cache()
    except Exception:
        pass


class _ModelLRU(OrderedDict):
    """Bounded LRU dict for resident model objects.

    Evicting least-recently-used entries keeps a bounded residency while
    still avoiding per-file reloads. Instances register themselves in
    ``_MODEL_CACHES`` so ``clear_all_model_caches`` reaches every cache.

    Thread-safe (G34): scans run file workers in a thread pool and the web
    servers analyze on request threads, so get/set/clear take a per-cache
    re-entrant lock (``move_to_end`` during a concurrent eviction would
    otherwise raise KeyError or corrupt the order). Evicted models are
    released outside the lock — ``gc.collect`` can be slow.
    """

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self._lock = threading.RLock()
        _MODEL_CACHES.append(self)

    def __getitem__(self, key):
        with self._lock:
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def get(self, key, default=None):
        with self._lock:
            try:
                return self[key]
            except KeyError:
                return default

    def __setitem__(self, key, value):
        evicted: list[object] = []
        with self._lock:
            if key in self:
                super().__delitem__(key)
            super().__setitem__(key, value)
            while len(self) > self.limit:
                _, old = self.popitem(last=False)
                evicted.append(old)
        for entry in evicted:
            _release_cached_model(entry)

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)

    def clear(self) -> None:
        with self._lock:
            super().clear()


def clear_all_model_caches() -> None:
    """Flush all resident model weights and release GPU/MPS memory back to OS."""
    for cache in list(_MODEL_CACHES):
        cache.clear()
    _release_cached_model(None)
