"""Adobe TrustMark decoder — variants Q, P and C (techniques-2026 item 4).

TrustMark (adobe/trustmark, MIT — code *and* models, ICCV 2025) hides a
100-bit packet in an image: payload + BCH parity + 4 version bits whose last
two name the schema (0 BCH_SUPER 40 bits, 1 BCH_5 61, 2 BCH_4 68, 3 BCH_3 75).
The decoders are the ONNX exports the TrustMark JavaScript decoder loads
(``https://cai-watermark.adobe.net/watermarking/trustmark-models/decoder_<V>.onnx``),
pinned by sha256 in ``models/assets.json`` and read from the models folder
(``scripts/fetch_watermark_models.py``). Preprocessing follows that decoder
(``js/tm_watermark.js``): RGB in [0, 1], centre square crop when the aspect
ratio exceeds 2 (always for P), bilinear resize to 256 px (Q, C) or 224 px
(P); a bit is 1 when its logit is >= 0. The BCH check is the package's own
(``vendor_trustmark_bch.py``).

Evidence semantics: a packet whose BCH check passes is a **fact** — "a
TrustMark watermark (variant V, schema S) with payload P is embedded". It
is not a statement that the image was generated: TrustMark marks photos
too, and its payload is an identifier, not a generator name. The payload is
linked to a conclusion only when it equals the ``c2pa.soft-binding`` value
(``"<schema>*<bits>"``, alg ``com.adobe.trustmark.<V>``) of a C2PA manifest
whose signature validated — see :func:`trustmark_evidence`. The BCH check
alone accepts a random packet with a non-negligible probability (BCH_3
corrects 3 errors with 21 parity bits); the measured rate on real photos is
docs/WATERMARK-MEASUREMENTS.md and is quoted in every finding.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .check_skipped import CheckSkipped

# Variant -> (asset name in models/assets.json, decoder input size, force centre square).
VARIANTS: dict[str, tuple[str, int, bool]] = {
    "Q": ("trustmark-decoder_Q.onnx", 256, False),
    "P": ("trustmark-decoder_P.onnx", 224, True),
    "C": ("trustmark-decoder_C.onnx", 256, False),
}
# tm_watermark.js: centre-square crop when the aspect ratio exceeds 2.
ASPECT_RATIO_LIMIT = 2.0
PACKET_BITS = 100
VERSION_BITS = 4
# DataLayer.schemaCapacity / raw_payload_split (trustmark/datalayer.py).
SCHEMAS: dict[int, tuple[str, int, int]] = {  # version -> (name, data bits, BCH t)
    0: ("BCH_SUPER", 40, 8),
    1: ("BCH_5", 61, 5),
    2: ("BCH_4", 68, 4),
    3: ("BCH_3", 75, 3),
}
BCH_POLYNOMIAL = 137
# The phase-0 resolution floor of the model layers (128 px); a decoder input is upsampled below it.
MIN_SIDE = 128
SOFT_BINDING_ALG_PREFIX = "com.adobe.trustmark."


@dataclass(frozen=True)
class TrustMarkReading:
    variant: str
    schema: int
    schema_name: str
    payload: str  # 0/1 string, schema capacity bits

    @property
    def soft_binding_value(self) -> str:
        """The value TrustMark's C2PA example writes into c2pa.soft-binding: "<schema>*<payload bits>"."""
        return f"{self.schema}*{self.payload}"


_BCH: dict[int, Any] = {}
_SESSIONS: dict[tuple[str, str], Any] = {}
_LOCK = threading.Lock()


def _bch(t: int) -> Any:
    from .vendor_trustmark_bch import BCH

    with _LOCK:
        if t not in _BCH:
            _BCH[t] = BCH(t, BCH_POLYNOMIAL)
        return _BCH[t]


def decode_packet(bits: str) -> tuple[int, str] | None:
    """``(schema, payload bits)`` when the 100-bit packet passes its BCH check, else None.

    Mirrors trustmark DataLayer._decode_text(MODE="binary"): the schema is the
    last two of the four version bits; data and parity are padded to bytes,
    BCH-corrected, and the payload is the corrected data cut to the schema's
    capacity.
    """
    if len(bits) != PACKET_BITS or set(bits) - {"0", "1"}:
        return None
    version = int(bits[-(VERSION_BITS - 2):], 2)
    schema = SCHEMAS.get(version)
    if schema is None:
        return None
    _, data_bits, t = schema
    data, ecc = bits[:data_bits], bits[data_bits:PACKET_BITS - VERSION_BITS]
    decoder = _bch(t)

    def to_bytes(text: str) -> bytearray:
        text = text + "0" * ((8 - len(text) % 8) % 8)
        return bytearray(int(text[i:i + 8], 2) for i in range(0, len(text), 8))

    data_bytes, ecc_bytes = to_bytes(data), to_bytes(ecc)
    if len(ecc_bytes) != decoder.get_ecc_bytes():
        return None
    with _LOCK:  # the vendored BCH keeps per-instance scratch state
        flips = decoder.decode(data_bytes, ecc_bytes)
    if flips == -1:
        return None
    payload = "".join(format(byte, "08b") for byte in data_bytes)[:data_bits]
    return version, payload


def _models_dir() -> Path:
    from .vendor_weights import default_models_dir

    return default_models_dir()


def _session(asset: str, path: Path) -> Any:
    """An onnxruntime session over the *verified* bytes of ``asset`` (pinned sha256), cached per digest."""
    import onnxruntime as ort

    from .model_assets import expected_sha256, verified_bytes

    expected = expected_sha256(asset)
    key = (asset, expected or "")
    with _LOCK:
        cached = _SESSIONS.get(key)
    if cached is not None and cached[1] == _file_state(path):
        return cached[0]
    data = verified_bytes(asset, path, expected=expected)  # AssetPinError (failed) when unpinned or other bytes
    options = ort.SessionOptions()
    # Determinism: one thread, so workers 1 / 4 give the same logits.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(data, sess_options=options, providers=["CPUExecutionProvider"])
    with _LOCK:
        _SESSIONS[key] = (session, _file_state(path))
    return session


def _file_state(path: Path) -> tuple[int, int, int]:
    info = os.stat(path)
    return (info.st_size, info.st_mtime_ns, info.st_ino)


def preprocess(image: Any, size: int, square: bool) -> Any:
    """PIL RGB image -> float32 [1, 3, size, size] in [0, 1] (tm_watermark.js runResizeModelSquare)."""
    import numpy as np
    from PIL import Image

    width, height = image.size
    ratio = width / height
    if ratio >= 1 and (ratio > ASPECT_RATIO_LIMIT or square):
        offset = (width - height) // 2
        image = image.crop((offset, 0, offset + height, height))
    elif ratio < 1 and (ratio < 1 / ASPECT_RATIO_LIMIT or square):
        offset = (height - width) // 2
        image = image.crop((0, offset, width, offset + width))
    resized = image.resize((size, size), Image.Resampling.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    return array.transpose(2, 0, 1)[None]


def available_variants() -> tuple[dict[str, Path], list[str]]:
    """``({variant: decoder path}, [missing asset names])`` in the models folder."""
    folder = _models_dir()
    present: dict[str, Path] = {}
    missing: list[str] = []
    for variant, (asset, _, _) in VARIANTS.items():
        path = folder / asset
        if path.is_file():
            present[variant] = path
        else:
            missing.append(asset)
    return present, missing


def decode_image(path: Path) -> tuple[list[TrustMarkReading], list[str], list[str]]:
    """Run every available decoder: ``(detected readings, variants run, missing decoder assets)``.

    Raises ImportError (skipped "의존성 부재") without onnxruntime / numpy /
    Pillow and CheckSkipped when no decoder file is present or the image is
    below MIN_SIDE.
    """
    from .import_probe import require

    require("numpy", "onnxruntime")  # dependency probes (R27-5: absence remembered)
    from PIL import Image

    present, missing = available_variants()
    if not present:
        raise CheckSkipped(
            "가중치 없음: " + ", ".join(missing) + " — scripts/fetch_watermark_models.py로 내려받아 models 폴더에 두면 실행됩니다"
        )
    with Image.open(path) as opened:
        opened.load()
        image = opened.convert("RGB")
    if min(image.size) < MIN_SIDE:
        raise CheckSkipped(f"측정 범위 밖: 해상도 {image.size[0]}x{image.size[1]}(짧은 변 {MIN_SIDE}px 미만)")
    readings: list[TrustMarkReading] = []
    for variant in sorted(present):
        asset, size, square = VARIANTS[variant]
        session = _session(asset, present[variant])
        logits = session.run(None, {session.get_inputs()[0].name: preprocess(image, size, square)})[0]
        bits = "".join("1" if value >= 0 else "0" for value in logits.reshape(-1)[:PACKET_BITS])
        decoded = decode_packet(bits)
        if decoded is not None:
            readings.append(TrustMarkReading(variant, decoded[0], SCHEMAS[decoded[0]][0], decoded[1]))
    return readings, sorted(present), missing


__all__ = [
    "ASPECT_RATIO_LIMIT",
    "MIN_SIDE",
    "SCHEMAS",
    "SOFT_BINDING_ALG_PREFIX",
    "TrustMarkReading",
    "VARIANTS",
    "available_variants",
    "decode_image",
    "decode_packet",
    "preprocess",
]
