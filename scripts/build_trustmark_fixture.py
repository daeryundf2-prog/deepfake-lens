#!/usr/bin/env python3
"""Rebuild the TrustMark test fixtures (fixtures/trustmark/) — techniques-2026 item 4.

Needs the TrustMark ONNX *encoder* (``encoder_Q.onnx`` from
https://cai-watermark.adobe.net/watermarking/trustmark-models/, MIT — not
pinned, not used by the analysis) given with ``--encoder``, plus
onnxruntime, numpy, opencv, Pillow; and c2pa-python >= 0.38 + openssl for the
signed copies. The analysis-side decoder is wm_trustmark.py.

Embedding follows ``trustmark.TrustMark.encode`` (adobe/trustmark 0.9.2):
the cover (centre-square region when the aspect ratio exceeds 2) is resized
to 256 px, the encoder adds the 100-bit packet, the residual (stego − cover,
per-channel mean removed) is resized back bilinearly and added to the
original pixels (strength 1.0; P ×1.25). The packet is built like
``DataLayer.process_encode`` (payload, BCH parity from the vendored
BCH, 4 version bits).

Writes (deterministic pixels; the C2PA signatures differ per run):

- ``tm-q.png`` — a 256×256 seeded texture with a TrustMark Q, BCH_4 payload
  ``PAYLOAD``;
- ``tm-q-signed.png`` — ``tm-q.png`` with a C2PA manifest signed by the
  c2pa-2.4 test CA: trainedAlgorithmicMedia, ``c2pa.watermarked.bound`` and a
  ``c2pa.soft-binding`` (alg ``com.adobe.trustmark.Q``, value ``"2*<PAYLOAD>"``);
- ``tm-q-signed-other-id.png`` — the same image signed with a soft-binding
  value that differs in one bit;
- ``tm-q-signed-edited.png`` — ``tm-q-signed.png`` whose pixel data was
  re-written after signing (one pixel changed and the PNG re-encoded with
  the manifest bytes kept), so the hard binding no longer matches but the
  watermark and the signed soft-binding value still do.

Usage:
    python scripts/build_trustmark_fixture.py --encoder <dir with encoder_Q.onnx> [--force]
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "trustmark"
PAYLOAD = "0110100101110010011001010110111001100101011101110110000101101001"[:64] + "1010"  # 68 bits (BCH_4 capacity)
ENCODER_SIZE = 256


def encode_packet(payload: str, schema: int = 2) -> list[int]:
    """The 100-bit TrustMark packet of ``payload`` (DataLayer.process_encode, binary mode)."""
    sys.path.insert(0, str(REPO_ROOT))
    from deepfake_lens.vendor_trustmark_bch import BCH
    from deepfake_lens.wm_trustmark import BCH_POLYNOMIAL, SCHEMAS

    _, data_bits, t = SCHEMAS[schema]
    bch = BCH(t, BCH_POLYNOMIAL)
    ecc_bits = bch.get_ecc_bits()
    data = (payload[:data_bits] + "0" * data_bits)[:data_bits]
    padded = data + "0" * ((8 - len(data) % 8) % 8)
    ecc = bch.encode(bytearray(int(padded[i:i + 8], 2) for i in range(0, len(padded), 8)))
    packet_e = "".join(format(byte, "08b") for byte in ecc)[:ecc_bits]
    packet = data + packet_e + format(schema, "04b")
    assert len(packet) == 100, len(packet)
    return [int(bit) for bit in packet]


def embed(encoder, cover, packet: list[int], variant: str = "Q"):
    """``cover`` (PIL RGB) with ``packet`` embedded by the ONNX encoder — a PIL RGB image."""
    import cv2
    import numpy as np
    from PIL import Image

    width, height = cover.size
    ratio = max(width, height) / min(width, height)
    force_square = variant == "P"
    if ratio > 2.0 or force_square:
        side = min(width, height)
        left, top = (width - side) // 2, (height - side) // 2
        box = (left, top, left + side, top + side)
    else:
        box = (0, 0, width, height)
    region = cover.crop(box)
    small = np.asarray(region.resize((ENCODER_SIZE, ENCODER_SIZE), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    chw = small.transpose(2, 0, 1)[None]
    inputs = encoder.get_inputs()
    stego = encoder.run(None, {inputs[0].name: chw, inputs[1].name: np.asarray([packet], dtype=np.float32)})[0]
    residual = np.clip(stego, 0.0, 1.0) - chw
    residual = residual - residual.mean(axis=(2, 3), keepdims=True)
    strength = 1.25 if variant == "P" else 1.0
    full = cv2.resize(residual[0].transpose(1, 2, 0), (box[2] - box[0], box[3] - box[1]), interpolation=cv2.INTER_LINEAR)
    marked = np.clip(full * strength + np.asarray(region, dtype=np.float32) / 255.0, 0.0, 1.0)
    out = np.asarray(cover).copy()
    out[box[1]:box[3], box[0]:box[2]] = np.rint(marked * 255.0).astype(np.uint8)
    return Image.fromarray(out)


def texture(size: int = 256, seed: int = 3):
    import numpy as np
    from PIL import Image

    rng = np.random.RandomState(seed)
    y, x = np.mgrid[0:size, 0:size]
    base = np.stack([
        120 + 70 * np.sin(x / 23.0) * np.cos(y / 31.0),
        110 + 60 * np.cos(x / 17.0 + y / 41.0),
        100 + 50 * np.sin((x - y) / 29.0),
    ], axis=-1) + rng.normal(0, 3, (size, size, 3))
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def _sign(source: Path, out: Path, value: str, work: Path) -> None:
    import c2pa

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from build_c2pa24_fixtures import IPTC, _ca, _leaf

    if not (work / "ca-cert.pem").is_file():
        raise RuntimeError("CA missing")
    chain, key = _leaf(work, "ca", f"tm-{out.stem}", "DeepfakeLensTrustMarkSigner")
    signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg=b"es256", sign_cert=chain, private_key=key, ta_url=None))
    manifest = {
        "claim_generator_info": [{"name": "deepfake-lens trustmark test fixture", "version": "1.0"}],
        "title": out.name, "format": "image/png",
        "assertions": [
            {"label": "c2pa.actions.v2", "data": {"actions": [
                {"action": "c2pa.created", "digitalSourceType": IPTC + "trainedAlgorithmicMedia"},
                {"action": "c2pa.watermarked.bound"},
            ]}},
            {"label": "c2pa.soft-binding", "data": {"alg": "com.adobe.trustmark.Q", "blocks": [{"scope": {}, "value": value}]}},
        ],
    }
    context = c2pa.Context(c2pa.Settings.from_dict({"builder": {"thumbnail": {"enabled": False}}}))
    out.unlink(missing_ok=True)
    c2pa.Builder(json.dumps(manifest), context=context).sign_file(source, out, signer=signer)
    del _ca


def _edit_pixels_keep_manifest(signed: Path, out: Path) -> None:
    """Change one pixel of a signed PNG, keeping every other chunk (the caBX manifest) byte for byte."""
    import struct
    import zlib

    data = signed.read_bytes()
    pos, chunks = 8, []
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunks.append((kind, data[pos + 8:pos + 8 + length]))
        pos += 12 + length
    ihdr = chunks[0][1]
    width, height = struct.unpack(">II", ihdr[:8])
    raw = bytearray(zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT")))
    stride = width * 3 + 1
    # Row 0 is filter byte + RGB; flip the low bit of the first pixel's red (filter-type independent enough for a fixture).
    raw[1] ^= 1
    idat = zlib.compress(bytes(raw), 9)
    out_chunks, wrote = [], False
    for kind, body in chunks:
        if kind == b"IDAT":
            if not wrote:
                out_chunks.append((b"IDAT", idat))
                wrote = True
            continue
        out_chunks.append((kind, body))
    blob = b"\x89PNG\r\n\x1a\n" + b"".join(
        struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF) for kind, body in out_chunks
    )
    out.write_bytes(blob)
    del height, stride


def main(argv: list[str] | None = None) -> int:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="TrustMark 테스트 픽스처(fixtures/trustmark/)를 다시 만듭니다(인코더 ONNX 필요).")
    parser.add_argument("--encoder", type=Path, required=True, help="encoder_Q.onnx가 있는 폴더")
    parser.add_argument("--force", action="store_true", help="기존 픽스처를 덮어씀")
    args = parser.parse_args(argv)
    if (FIXTURE_DIR / "tm-q.png").exists() and not args.force:
        print(f"error: {FIXTURE_DIR} already holds fixtures; pass --force", file=sys.stderr)
        return 2
    import onnxruntime as ort

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from build_c2pa24_fixtures import _ca

    encoder = ort.InferenceSession(str(args.encoder / "encoder_Q.onnx"), providers=["CPUExecutionProvider"])
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    marked = embed(encoder, texture(), encode_packet(PAYLOAD, schema=2), "Q")
    plain = FIXTURE_DIR / "tm-q.png"
    marked.save(plain)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # Re-use the committed c2pa-2.4 test CA? Its key is gone by design — make a fresh CA here and
        # write its certificate next to the fixtures (tests trust it through DEEPFAKE_LENS_C2PA_TRUST_ANCHORS).
        _ca(work, "ca", "DeepfakeLensTrustMarkTestCA")
        (FIXTURE_DIR / "test-ca-cert.pem").write_bytes((work / "ca-cert.pem").read_bytes())
        _sign(plain, FIXTURE_DIR / "tm-q-signed.png", f"2*{PAYLOAD}", work)
        other = PAYLOAD[:-1] + ("0" if PAYLOAD[-1] == "1" else "1")
        _sign(plain, FIXTURE_DIR / "tm-q-signed-other-id.png", f"2*{other}", work)
        _edit_pixels_keep_manifest(FIXTURE_DIR / "tm-q-signed.png", FIXTURE_DIR / "tm-q-signed-edited.png")
    for path in sorted(FIXTURE_DIR.glob("*.png")):
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
