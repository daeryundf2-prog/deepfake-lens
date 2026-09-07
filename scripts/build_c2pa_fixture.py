#!/usr/bin/env python3
"""Regenerate the C2PA test fixture (fixtures/c2pa-test/signed-c2pa.png).

The original fixture was lost to the blanket `*.png` rule in .gitignore,
and the test-only signing keys were never committed (by design). Instead of
a binary blob that nobody can reproduce, this script rebuilds the whole
chain from scratch on any machine:

  1. fresh ES256 test CA (CN=DeepfakeLensTestCA) + leaf signer cert
     (CN=DeepfakeLensTestSigner), valid 10 years
  2. a 32x32 PNG (pure Python writer, no Pillow)
  3. a C2PA manifest signed with the leaf via the official c2pa-python SDK

The CA/leaf private keys stay in a temp directory and are deleted; only the
CA cert, leaf cert (for inspection), and signed PNG are written. The test
suite expects the signer's SDK validation to be UNTRUSTED (the CA is not in
any real trust store), which is exactly the honest behavior being pinned.

Usage:
    python scripts/build_c2pa_fixture.py [--force]

Requires: c2pa-python (pip install 'deepfake-lens[provenance]') and a local
openssl binary on PATH for certificate generation.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "fixtures" / "c2pa-test"
CA_CN = "DeepfakeLensTestCA"
SIGNER_CN = "DeepfakeLensTestSigner"


def run_openssl(args: list[str], workdir: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["openssl", *args],
        capture_output=True,
        text=True,
        cwd=workdir,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result


def make_png(path: Path, size: int = 32) -> None:
    """Write a small PNG without external dependencies."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    rows = b""
    for y in range(size):
        rows += b"\x00"  # filter: none
        for x in range(size):
            # deterministic soft teal gradient
            rows += bytes((40 + 3 * x, 120 + 2 * y, 140))
    data = header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)


def build_certs(workdir: Path) -> tuple[str, str, str]:
    """Return (ca_cert_pem, leaf_cert_pem, leaf_key_pem).

    C2PA signing requires the leaf cert to carry digitalSignature keyUsage
    and the emailProtection (or documentSigning) EKU, otherwise the SDK
    rejects it as "invalid signing credentials".
    """
    run_openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca-key.pem"], workdir)
    run_openssl(
        ["req", "-new", "-x509", "-key", "ca-key.pem", "-subj", f"/CN={CA_CN}", "-days", "3650", "-out", "ca-cert.pem",
         "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
        workdir,
    )
    run_openssl(["ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "signer-key-sec1.pem"], workdir)
    # The SDK requires a PKCS#8 PEM ("PRIVATE KEY" label), not SEC1 ("EC PRIVATE KEY").
    run_openssl(["pkcs8", "-topk8", "-nocrypt", "-in", "signer-key-sec1.pem", "-out", "signer-key.pem"], workdir)
    run_openssl(["req", "-new", "-key", "signer-key.pem", "-subj", f"/CN={SIGNER_CN}", "-out", "signer.csr"], workdir)
    ext = workdir / "signer.ext"
    ext.write_text("keyUsage=critical,digitalSignature\nextendedKeyUsage=emailProtection\n", encoding="utf-8")
    run_openssl(
        ["x509", "-req", "-in", "signer.csr", "-CA", "ca-cert.pem", "-CAkey", "ca-key.pem",
         "-CAcreateserial", "-days", "3650", "-out", "signer-cert.pem", "-extfile", ext.name],
        workdir,
    )
    return (
        (workdir / "ca-cert.pem").read_text(encoding="utf-8"),
        (workdir / "signer-cert.pem").read_text(encoding="utf-8"),
        (workdir / "signer-key.pem").read_text(encoding="utf-8"),
    )


def sign_png(png_path: Path, out_path: Path, ca_pem: str, leaf_pem: str, key_pem: str) -> None:
    import c2pa  # noqa: PLC0415 - optional provenance extra

    signer_info = c2pa.C2paSignerInfo(
        alg="ES256",
        sign_cert=leaf_pem.encode("utf-8"),
        private_key=key_pem.encode("utf-8"),
        ta_url=None,
    )
    signer = c2pa.Signer.from_info(signer_info)
    manifest = {
        "claim_generator": "deepfake-lens test fixture",
        "title": "Deepfake Lens C2PA test fixture",
        "format": "image/png",
        "assertions": [],
    }
    builder = c2pa.Builder(json.dumps(manifest))
    builder.sign_file(png_path, out_path, signer=signer)


def verify(out_path: Path) -> bool:
    """Read back with the SDK to prove the manifest round-trips."""
    import c2pa  # noqa: PLC0415 - optional provenance extra

    reader = c2pa.Reader(str(out_path))
    try:
        state = str(reader.get_validation_state())
        manifest = reader.get_active_manifest() or {}
        signer = (manifest.get("signature_info") or {}).get("common_name", "")
        print(f"  SDK read-back: state={state} signer={signer}")
        return bool(state) and bool(signer)
    finally:
        try:
            reader.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the C2PA test fixture.")
    parser.add_argument("--force", action="store_true", help="overwrite an existing signed-c2pa.png")
    args = parser.parse_args(argv)

    target = FIXTURE_DIR / "signed-c2pa.png"
    if target.exists() and not args.force:
        print(f"error: {target} already exists; pass --force to regenerate", file=sys.stderr)
        return 2

    if shutil.which("openssl") is None:
        print("error: openssl binary not found on PATH (needed for test certificate generation)", file=sys.stderr)
        return 2
    try:
        import c2pa  # noqa: F401, PLC0415
    except ImportError:
        print("error: c2pa-python not installed; run pip install 'deepfake-lens[provenance]'", file=sys.stderr)
        return 2

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        ca_pem, leaf_pem, key_pem = build_certs(workdir)
        unsigned = workdir / "unsigned.png"
        make_png(unsigned)
        sign_png(unsigned, target, ca_pem, leaf_pem, key_pem)

        (FIXTURE_DIR / "test-ca-cert.pem").write_text(ca_pem, encoding="utf-8")
        (FIXTURE_DIR / "test-signer-cert.pem").write_text(leaf_pem, encoding="utf-8")

    print(f"wrote {target}")
    print(f"wrote {FIXTURE_DIR / 'test-ca-cert.pem'}")
    print(f"wrote {FIXTURE_DIR / 'test-signer-cert.pem'}")
    if not verify(target):
        print("error: SDK could not read back the signed fixture", file=sys.stderr)
        return 1
    print("fixture verified: manifest present and readable (untrusted signer, as expected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
