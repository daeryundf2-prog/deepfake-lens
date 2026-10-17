#!/usr/bin/env python3
"""Rebuild the C2PA 2.x test fixtures (fixtures/c2pa-2.4/) — techniques-2026 item 1.

Everything is made from scratch on any machine with ``openssl`` (3.4+, for
``x509 -not_before/-not_after``) and c2pa-python >= 0.38 (c2pa-rs 0.91,
which writes v2 claims and validates against C2PA 2.4):

1. a fresh ES256 test CA ``DeepfakeLensC2pa24TestCA`` and leaf signers —
   ``DeepfakeLensC2pa24Signer`` (valid 10 years), ``…ExpiredSigner``
   (expires two minutes after the build) — plus a second, never-trusted CA
   ``DeepfakeLensC2pa24OtherCA`` with its own signer;
2. small deterministic PNGs (pure-Python writer);
3. manifests signed with the SDK (thumbnails off, so each file stays a few KB).

The private keys stay in a temporary directory and are deleted; only the
CA certificates and the signed files are written. The tests trust the test
CA by pointing ``DEEPFAKE_LENS_C2PA_TRUST_ANCHORS`` at ``test-ca-cert.pem``;
without it the signer is untrusted (the bundled C2PA trust list does not
contain it). Signed bytes differ on every run (ECDSA nonces, UUIDs), so the
tests check the decoded facts, never the bytes.

Fixtures (see fixtures/c2pa-2.4/README.md):

- ``ai-generated.png`` — c2pa.created with digitalSourceType
  trainedAlgorithmicMedia, c2pa.watermarked.bound, a c2pa.soft-binding
  (alg com.adobe.trustmark.Q), a c2pa.ai-disclosure assertion (2.4);
- ``ai-disclosure-only.png`` — c2pa.created with the C2PA-namespace source type
  ``http://c2pa.org/digitalsourcetype/empty`` and a c2pa.ai-disclosure assertion
  (the AI statement is only in the disclosure);
- ``camera-capture.png`` — c2pa.created digitalCapture, no edits;
- ``screen-capture.png`` — c2pa.created screenCapture;
- ``composite-ai.png`` — c2pa.opened (parent ingredient) + c2pa.edited with
  compositeWithTrainedAlgorithmicMedia;
- ``expired-signer.png`` — trainedAlgorithmicMedia signed by a leaf that
  expires EXPIRING_SIGNER_SECONDS after the build (the SDK will not sign with
  an already expired certificate; no time stamp, so every later read sees
  an expired signer);
- ``other-ca.png`` — trainedAlgorithmicMedia signed under the untrusted CA.

Usage:
    python scripts/build_c2pa24_fixtures.py [--force]
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "c2pa-2.4"
IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"
# A 68-bit TrustMark payload in the "<schema>*<bits>" form of the TrustMark
# C2PA example (adobe/trustmark c2pa/c2pa_watermark_example.py; schema 2 = BCH_4).
SOFT_BINDING_VALUE = "2*" + "01" * 34


def _openssl(args: list[str], workdir: Path) -> None:
    result = subprocess.run(["openssl", *args], capture_output=True, text=True, cwd=workdir, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args[:2])} failed: {result.stderr.strip()}")


def _ca(workdir: Path, name: str, cn: str) -> None:
    _openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{name}-key.pem"], workdir)
    _openssl(["req", "-new", "-x509", "-key", f"{name}-key.pem", "-subj", f"/CN={cn}", "-days", "3650", "-out", f"{name}-cert.pem",
              "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"], workdir)


def _leaf(workdir: Path, ca: str, name: str, cn: str, *, validity: tuple[str, str] | None = None) -> tuple[bytes, bytes]:
    """(chain PEM, PKCS#8 key PEM) of a leaf signed by ``ca`` (C2PA signer profile: digitalSignature + emailProtection)."""
    _openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{name}-sec1.pem"], workdir)
    _openssl(["pkcs8", "-topk8", "-nocrypt", "-in", f"{name}-sec1.pem", "-out", f"{name}-key.pem"], workdir)
    _openssl(["req", "-new", "-key", f"{name}-key.pem", "-subj", f"/CN={cn}", "-out", f"{name}.csr"], workdir)
    (workdir / f"{name}.ext").write_text("keyUsage=critical,digitalSignature\nextendedKeyUsage=emailProtection\n", encoding="utf-8")
    period = ["-not_before", validity[0], "-not_after", validity[1]] if validity else ["-days", "3650"]
    _openssl(["x509", "-req", "-in", f"{name}.csr", "-CA", f"{ca}-cert.pem", "-CAkey", f"{ca}-key.pem", "-CAcreateserial",
              *period, "-out", f"{name}-cert.pem", "-extfile", f"{name}.ext"], workdir)
    chain = (workdir / f"{name}-cert.pem").read_bytes() + (workdir / f"{ca}-cert.pem").read_bytes()
    return chain, (workdir / f"{name}-key.pem").read_bytes()


EXPIRING_SIGNER_SECONDS = 120


def _expiring_validity() -> tuple[str, str]:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    fmt = "%Y%m%d%H%M%SZ"
    return (now - timedelta(days=1)).strftime(fmt), (now + timedelta(seconds=EXPIRING_SIGNER_SECONDS)).strftime(fmt)


def make_png(path: Path, size: int = 48, seed: int = 0) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(
        b"\x00" + b"".join(bytes(((x * 5 + seed * 31) % 256, (y * 4 + seed * 17) % 256, ((x ^ y) * 3 + seed) % 256)) for x in range(size))
        for y in range(size)
    )
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def _manifest(title: str, actions: list[dict[str, object]], extra: list[dict[str, object]] | None = None,
              ingredients: list[dict[str, object]] | None = None) -> dict[str, object]:
    manifest: dict[str, object] = {
        # No "specVersion" in claim_generator_info (the C2PA 2.4 location): c2pa-rs 0.91
        # writes it but then cannot decode its own claim ("claim_cbor: unexpected trailing
        # data") — a manifest written that way is read as a failed c2pa check.
        "claim_generator_info": [{"name": "deepfake-lens c2pa-2.4 test fixture", "version": "1.0"}],
        "title": title,
        "format": "image/png",
        "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": actions}}, *(extra or [])],
    }
    if ingredients:
        manifest["ingredients"] = ingredients
    return manifest


AI_DISCLOSURE = {"label": "c2pa.ai-disclosure", "data": {
    "modelType": "c2pa.types.model.diffusion", "modelName": "DeepfakeLensTestGenerator",
    "modelIdentifier": "pkg:generic/deepfake-lens-test-generator@1.0", "contentProfile": {"humanOversightLevel": "fully_autonomous"},
}}
SOFT_BINDING = {"label": "c2pa.soft-binding", "data": {"alg": "com.adobe.trustmark.Q", "blocks": [{"scope": {}, "value": SOFT_BINDING_VALUE}]}}


def fixtures() -> dict[str, tuple[str, dict[str, object]]]:
    """``{file name: (signer, manifest)}``."""
    created_ai = {"action": "c2pa.created", "digitalSourceType": IPTC + "trainedAlgorithmicMedia"}
    return {
        "ai-generated.png": ("signer", _manifest("AI generated", [created_ai, {"action": "c2pa.watermarked.bound"}], [SOFT_BINDING, AI_DISCLOSURE])),
        "ai-disclosure-only.png": ("signer", _manifest("AI disclosure only", [{"action": "c2pa.created", "digitalSourceType": "http://c2pa.org/digitalsourcetype/empty"}], [AI_DISCLOSURE])),
        "camera-capture.png": ("signer", _manifest("Camera capture", [{"action": "c2pa.created", "digitalSourceType": IPTC + "digitalCapture"}])),
        "screen-capture.png": ("signer", _manifest("Screen capture", [{"action": "c2pa.created", "digitalSourceType": IPTC + "screenCapture"}])),
        "composite-ai.png": ("signer", _manifest("Composite", [
            {"action": "c2pa.created", "digitalSourceType": IPTC + "digitalCapture"},
            {"action": "c2pa.edited", "digitalSourceType": IPTC + "compositeWithTrainedAlgorithmicMedia"},
        ])),
        "expired-signer.png": ("expired", _manifest("Expired signer", [created_ai])),
        "other-ca.png": ("other", _manifest("Untrusted CA", [created_ai])),
    }


def build(target: Path) -> None:
    import c2pa  # noqa: PLC0415 - optional provenance extra

    no_thumbnails = c2pa.Context(c2pa.Settings.from_dict({"builder": {"thumbnail": {"enabled": False}}}))
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _ca(work, "ca", "DeepfakeLensC2pa24TestCA")
        _ca(work, "other", "DeepfakeLensC2pa24OtherCA")
        signers = {
            "signer": _leaf(work, "ca", "signer", "DeepfakeLensC2pa24Signer"),
            # The SDK refuses to sign with a certificate that is not valid now, so the
            # "expired" leaf is valid for EXPIRING_SIGNER_SECONDS from now: it signs
            # while valid and is expired for every later read (no time stamp is added).
            "expired": _leaf(work, "ca", "expired", "DeepfakeLensC2pa24ExpiredSigner", validity=_expiring_validity()),
            "other": _leaf(work, "other", "othersigner", "DeepfakeLensC2pa24OtherSigner"),
        }
        target.mkdir(parents=True, exist_ok=True)
        for index, (name, (who, manifest)) in enumerate(sorted(fixtures().items())):
            chain, key = signers[who]
            signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg=b"es256", sign_cert=chain, private_key=key, ta_url=None))
            source = work / f"src-{index}.png"
            make_png(source, seed=index)
            out = target / name
            out.unlink(missing_ok=True)
            c2pa.Builder(json.dumps(manifest), context=no_thumbnails).sign_file(source, out, signer=signer)
            print(f"wrote {out} ({out.stat().st_size} bytes)")
        shutil.copyfile(work / "ca-cert.pem", target / "test-ca-cert.pem")
        shutil.copyfile(work / "other-cert.pem", target / "other-ca-cert.pem")


def main(argv: list[str] | None = None) -> int:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help

    parser = KoreanArgumentParser(description="C2PA 2.x 테스트 픽스처(fixtures/c2pa-2.4/)를 다시 만듭니다.")
    parser.add_argument("--force", action="store_true", help="기존 픽스처를 덮어씀")
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR, help="출력 폴더(기본 fixtures/c2pa-2.4)")
    args = parser.parse_args(argv)
    if (args.out / "ai-generated.png").exists() and not args.force:
        print(f"error: {args.out} already holds fixtures; pass --force to regenerate", file=sys.stderr)
        return 2
    if shutil.which("openssl") is None:
        print("error: openssl binary not found on PATH", file=sys.stderr)
        return 2
    try:
        import c2pa  # noqa: F401, PLC0415
    except ImportError:
        print("error: c2pa-python not installed; run pip install 'deepfake-lens[provenance]'", file=sys.stderr)
        return 2
    build(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
