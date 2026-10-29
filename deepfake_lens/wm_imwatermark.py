"""imwatermark ``dwtDct`` decoder and the SDXL fixed-message check (techniques-2026 item 3).

The Stable Diffusion XL reference pipelines (diffusers
``StableDiffusionXLWatermarker``, Stability AI ``generative-models``) embed a
fixed 48-bit message with the ``invisible-watermark`` library (MIT,
ShieldMnt/invisible-watermark 0.2.0, ``imwatermark/maxDct.py``) — method
``dwtDct``, scales ``[0, 36, 36]``, 4×4 blocks. This module reimplements
only its *decoder* with numpy alone (no OpenCV, no PyWavelets, no torch):

1. OpenCV's ``cvtColor(BGR → YUV)`` of the 8-bit image, cropped to a multiple of 4 — recomputed in numpy with
   OpenCV's 14-bit fixed-point coefficients (:func:`bgr_to_yuv_u8`; equal to ``cv2.cvtColor`` for all 2²⁴ colours);
2. one level of the Haar DWT of the U channel (the only channel with a
   non-zero scale that ``decode`` visits — its loop is ``range(2)``): the
   approximation band ``cA = (a + b + c + d) / 2`` of each 2×2 pixel block;
3. for every 4×4 block of ``cA`` (row-major), the coefficient of largest
   magnitude after the first; its bit is ``(|v| mod 36) > 18``;
4. block *n* votes for message bit ``n mod 48``; a bit is 1 when the mean
   vote × 255 > 127.

Evidence semantics: ONLY an exact 48/48 match with the registered SDXL
message (models/watermark-registry.json) is evidence — "SDXL 공식 파이프라인
워터마크 존재" (deterministic, synthetic, strong, like a generator metadata
field: the message is public and can be imitated). Any other result is no
information — the dwtDct mark does not survive resizing, cropping or
recompression well, so a mismatch never means "no watermark". Both channel
orders are tried (BGR as documented; RGB-as-BGR for pipelines that passed RGB
arrays), each an exact match, so the chance of a random match is
2 × 2⁻⁴⁸ per image under a uniform-bit model; the measured rate on real
photos is in docs/WATERMARK-MEASUREMENTS.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# imwatermark EmbedMaxDct defaults (maxDct.py): scales=[0,36,36], block=4.
DWTDCT_SCALE = 36
DWTDCT_BLOCK = 4
# The decoder's channel: index 1 (U) — the only one with a non-zero scale in range(2).
DWTDCT_CHANNEL = 1
# diffusers: "can't encode images that are smaller than 256" (width); imwatermark: r*c >= 256*256.
MIN_SIDE = 256
# Above this many pixels the check is skipped (memory bound; no generator emits such sizes).
MAX_PIXELS = 80_000_000
ALGORITHM = "imwatermark-dwtDct"
# pywt "haar" decomposition low-pass coefficient (1/sqrt(2) as PyWavelets stores it).
HAAR_LO = 0.7071067811865476

# OpenCV imgproc color_yuv (RGB2YUV, 8-bit): Y = 0.299R + 0.587G + 0.114B, U = 0.492(B - Y) + 128,
# V = 0.877(R - Y) + 128, as integers scaled by 2**14 and rounded half up (CV_DESCALE), then saturated to 0..255.
YUV_SHIFT = 14
YUV_B2Y, YUV_G2Y, YUV_R2Y, YUV_B2U, YUV_R2V = 1868, 9617, 4899, 8061, 14369
# The same transform for float images (OpenCV's float32 path, chroma offset 0.5) and its inverse (YUV2RGB).
YUV_F_B2Y, YUV_F_G2Y, YUV_F_R2Y, YUV_F_B2U, YUV_F_R2V = 0.114, 0.587, 0.299, 0.492, 0.877
YUV_F_U2B, YUV_F_U2G, YUV_F_V2G, YUV_F_V2R = 2.032, -0.395, -0.581, 1.140


@dataclass(frozen=True)
class DwtDctReading:
    bits_bgr: str  # 0/1 string decoded with the documented channel order
    bits_rgb: str  # 0/1 string decoded with R and B swapped
    width: int
    height: int


def bgr_to_yuv_u8(bgr: Any) -> Any:
    """``cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV)`` for a uint8 H×W×3 array, bit for bit, in numpy."""
    import numpy as np

    b, g, r = (bgr[:, :, i].astype(np.int32) for i in range(3))
    half, delta = 1 << (YUV_SHIFT - 1), 128 << YUV_SHIFT
    y = (b * YUV_B2Y + g * YUV_G2Y + r * YUV_R2Y + half) >> YUV_SHIFT
    u = ((b - y) * YUV_B2U + delta + half) >> YUV_SHIFT
    v = ((r - y) * YUV_R2V + delta + half) >> YUV_SHIFT
    return np.clip(np.stack((y, u, v), axis=-1), 0, 255).astype(np.uint8)


def _bgr_to_yuv_f32(bgr: Any) -> Any:
    """``cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV)`` for float32 (equal up to float32 rounding order, ~1 ulp)."""
    import numpy as np

    f = np.float32
    b, g, r = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
    y = b * f(YUV_F_B2Y) + g * f(YUV_F_G2Y) + r * f(YUV_F_R2Y)
    return np.stack((y, (b - y) * f(YUV_F_B2U) + f(0.5), (r - y) * f(YUV_F_R2V) + f(0.5)), axis=-1).astype(np.float32)


def _yuv_to_bgr_f32(yuv: Any) -> Any:
    """``cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)`` for float32 (equal up to float32 rounding order, ~1 ulp)."""
    import numpy as np

    f = np.float32
    y, u, v = yuv[:, :, 0], yuv[:, :, 1] - f(0.5), yuv[:, :, 2] - f(0.5)
    return np.stack((y + u * f(YUV_F_U2B), y + u * f(YUV_F_U2G) + v * f(YUV_F_V2G), y + v * f(YUV_F_V2R)), axis=-1).astype(np.float32)


def _decode_bits(bgr: Any, n_bits: int) -> str:
    import numpy as np

    rows, cols = bgr.shape[0] // 4 * 4, bgr.shape[1] // 4 * 4
    yuv = bgr_to_yuv_u8(bgr[:rows, :cols])
    channel = yuv[:, :, DWTDCT_CHANNEL].astype(np.float64)
    # PyWavelets' haar dwt2 order of operations (rows, then columns, each times 1/sqrt(2)) —
    # bit-identical to pywt.dwt2(..., "haar")[0]; "(a+b+c+d)/2" differs in the last ulp, which
    # flips votes that sit exactly on the 18 = 36/2 boundary (integer pixels make that common).
    lo = channel[0::2, :] * HAAR_LO + channel[1::2, :] * HAAR_LO
    approx = lo[:, 0::2] * HAAR_LO + lo[:, 1::2] * HAAR_LO
    br, bc = approx.shape[0] // DWTDCT_BLOCK, approx.shape[1] // DWTDCT_BLOCK
    blocks = approx[: br * DWTDCT_BLOCK, : bc * DWTDCT_BLOCK].reshape(br, DWTDCT_BLOCK, bc, DWTDCT_BLOCK)
    flat = blocks.transpose(0, 2, 1, 3).reshape(-1, DWTDCT_BLOCK * DWTDCT_BLOCK)
    position = np.argmax(np.abs(flat[:, 1:]), axis=1) + 1
    values = np.abs(flat[np.arange(flat.shape[0]), position])
    votes = (np.mod(values, DWTDCT_SCALE) > 0.5 * DWTDCT_SCALE).astype(np.float64)
    index: Any = np.arange(votes.shape[0]) % n_bits
    sums = np.bincount(index, weights=votes, minlength=n_bits)
    counts = np.bincount(index, minlength=n_bits)
    means = np.divide(sums, counts, out=np.zeros(n_bits), where=counts > 0)
    return "".join("1" if mean * 255 > 127 else "0" for mean in means)


def decode_array(rgb: Any, n_bits: int = 48) -> DwtDctReading:
    """Decode an RGB uint8 array (H×W×3) both ways."""
    bgr = rgb[:, :, ::-1]
    return DwtDctReading(_decode_bits(bgr, n_bits), _decode_bits(rgb, n_bits), int(rgb.shape[1]), int(rgb.shape[0]))


def load_rgb(path: Path) -> Any:
    """The image's pixels as an RGB uint8 array (first frame; no EXIF rotation — the mark is in stored pixels).

    R27-5: when the image metadata layer already decoded this file version (a PNG's ``getexif()`` loads the
    whole image), it handed over the same array (:func:`offer_loaded`); otherwise the file is decoded here.
    """
    from .versioned_cache import MISSING, take

    shared = take(RGB_HANDOFF, path)
    if shared is not MISSING:
        return shared
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


RGB_HANDOFF = "imwatermark.rgb"  # versioned_cache kind (R27-5)


def offer_loaded(path: Path, version: tuple[int, ...] | None, image: Any) -> None:
    """R27-5: hand :func:`load_rgb`'s array to the SDXL check when ``image`` (``Image.open(path)``) is already loaded.

    Only an image whose pixels Pillow has already decoded (``tile`` emptied by ``load()``), with numpy imported
    and at most MAX_PIXELS pixels; the array is
    made by the same calls as :func:`load_rgb` (``load()`` again is a no-op). Any failure offers nothing — the
    SDXL check then decodes the file itself, as before.
    """
    import sys

    if version is None or getattr(image, "tile", None) != [] or sys.modules.get("numpy") is None:
        return
    try:
        width, height = image.size
        if int(width) * int(height) > MAX_PIXELS:
            return
        import numpy as np

        from .versioned_cache import offer

        image.load()
        offer(RGB_HANDOFF, path, version, np.asarray(image.convert("RGB"), dtype=np.uint8))
    except Exception:  # noqa: BLE001 - not offered; the SDXL check decodes the file itself
        return


def encode_array_for_tests(rgb: Any, bits: str) -> Any:
    """The dwtDct *encoder* (maxDct.encode_frame) on an RGB array — test fixtures only.

    Mirrors imwatermark with the Haar DWT written out (``pywt`` haar has
    ``cA = (a+b+c+d)/2`` and the inverse spreads ``cA/2`` back); used to build
    marked images without the library. The analysis never encodes.
    """
    import numpy as np

    marks = [int(b) for b in bits]
    out = rgb[:, :, ::-1].astype(np.float32).copy()  # BGR float, as diffusers passes it
    rows, cols = out.shape[0] // 4 * 4, out.shape[1] // 4 * 4
    yuv = _bgr_to_yuv_f32(out[:rows, :cols]).astype(np.float64)
    ch = yuv[:, :, DWTDCT_CHANNEL]
    a, b, c, d = ch[0::2, 0::2], ch[0::2, 1::2], ch[1::2, 0::2], ch[1::2, 1::2]
    ca, h, v, dd = (a + b + c + d) / 2, (a + b - c - d) / 2, (a - b + c - d) / 2, (a - b - c + d) / 2
    num = 0
    for i in range(ca.shape[0] // 4):
        for j in range(ca.shape[1] // 4):
            block = ca[i * 4:i * 4 + 4, j * 4:j * 4 + 4]
            flat = block.flatten()
            pos = int(np.argmax(np.abs(flat[1:]))) + 1
            val = flat[pos]
            bit = marks[num % len(marks)]
            magnitude = (abs(val) // DWTDCT_SCALE + 0.25 + 0.5 * bit) * DWTDCT_SCALE
            block[pos // 4, pos % 4] = magnitude if val >= 0 else -magnitude
            num += 1
    ch[0::2, 0::2] = (ca + h + v + dd) / 2
    ch[0::2, 1::2] = (ca + h - v - dd) / 2
    ch[1::2, 0::2] = (ca - h + v - dd) / 2
    ch[1::2, 1::2] = (ca - h - v + dd) / 2
    yuv[:, :, DWTDCT_CHANNEL] = ch
    out[:rows, :cols] = _yuv_to_bgr_f32(yuv.astype(np.float32))
    return np.clip(np.rint(out[:, :, ::-1]), 0, 255).astype(np.uint8)


__all__ = ["ALGORITHM", "DwtDctReading", "MAX_PIXELS", "MIN_SIDE", "bgr_to_yuv_u8", "decode_array", "encode_array_for_tests", "load_rgb"]
