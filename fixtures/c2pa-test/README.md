# C2PA test fixtures

- `signed-c2pa.png`: a 32x32 PNG carrying a real C2PA (JUMBF) manifest,
  signed with an ES256 test-only certificate chain generated for this
  repository. Used by `deepfake_lens/tests/test_c2pa.py` to exercise the
  official `c2pa-python` SDK validation path.
- `test-ca-cert.pem`: the self-signed root CA that signed the fixture.
- `test-signer-cert.pem`: the leaf signing certificate
  (CN=DeepfakeLensTestSigner; needs digitalSignature keyUsage and
  emailProtection EKU or the SDK rejects it).

The signing keys are test-only, live only in a temp directory during
generation, and are never committed; these credentials prove nothing about
real-world trust.

The SDK validation state is intentionally not "valid" here (the test CA is
not in any real trust store), which the analysis surfaces as
"검증 미완료" rather than success — that is the honest behavior the tests pin.

## Regenerating

The PNG fixture is binary, so it can be rebuilt deterministically on any
machine with openssl and c2pa-python installed:

```sh
python scripts/build_c2pa_fixture.py --force
```
