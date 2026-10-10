"""Third-party telemetry is switched off before any dependency is imported (R15-2, round 15).

``import onnxruntime`` (1.2x Linux wheels) starts Microsoft's 1DS
telemetry: it writes ``~/.cache/Microsoft/DeveloperTools/.onnxruntime/
deviceid`` + ``onnxruntime.db`` (``$XDG_CACHE_HOME`` when set) and tries
``mobile.events.data.microsoft.com:443`` — the verifier saw both from
``deepfake-lens doctor`` and ``scan --deep-signals``. For an evidence tool
that is an outbound connection the operator never asked for, and in phase
1 a violation of R-IN-4 (no network on the product path).

:func:`apply` sets every known opt-out variable with
``os.environ.setdefault`` — an operator's explicit value is kept — and is
called first thing in ``deepfake_lens/__init__.py``, so it runs before any
module of the package (and therefore before ``onnxruntime``,
``huggingface_hub``/``transformers``) can be imported; child processes
inherit the environment. Leaf module: no imports from the package.
"""

from __future__ import annotations

import os

# R15-2: name -> value of each opt-out, with its source.
TELEMETRY_OPT_OUT: dict[str, str] = {
    # onnxruntime: read by the C++ telemetry provider at import
    # (onnxruntime/core/platform/telemetry; the string is in
    # capi/onnxruntime_pybind11_state*.so) — no deviceid, no .db, no connect.
    "ORT_DISABLE_TELEMETRY": "1",
    # huggingface_hub (and transformers through it): no usage pings
    # (huggingface_hub/constants.py, HF_HUB_DISABLE_TELEMETRY).
    "HF_HUB_DISABLE_TELEMETRY": "1",
    # The cross-tool convention (consoledonottrack.com), honoured by
    # huggingface_hub >= 0.24 and other libraries.
    "DO_NOT_TRACK": "1",
}


def apply() -> dict[str, str]:
    """Set the opt-outs that are not already set; returns what the environment now holds for them."""
    for name, value in TELEMETRY_OPT_OUT.items():
        os.environ.setdefault(name, value)
    return {name: os.environ[name] for name in TELEMETRY_OPT_OUT}


__all__ = ["TELEMETRY_OPT_OUT", "apply"]
