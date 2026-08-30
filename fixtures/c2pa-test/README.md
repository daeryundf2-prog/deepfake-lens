# C2PA test fixtures

- `signed-c2pa.png`: a 32x32 PNG carrying a real C2PA (JUMBF) manifest,
  signed with an ES256 test-only certificate chain generated for this
  repository. Used by `deepfake_lens/tests/test_c2pa.py` to exercise the
  official `c2pa-python` SDK validation path.
- `test-ca-cert.pem`: the self-signed root CA that signed the fixture.
  The test registers it as a trust anchor so the SDK validation state is
  deterministic ("valid"). Without it the SDK reports an untrusted signer,
  which the analysis surfaces as "검증 미완료" rather than success.

The signing key is test-only and never leaves this directory's context;
these credentials prove nothing about real-world trust.
