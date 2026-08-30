# VERIFIED_REGISTRY

Every external link cited by `deepfake_lens/model_registry.py` and `docs/`
must pass `scripts/check_registry_links.py` before being committed, and the
registry is re-checked weekly in CI. This file records the manually verified
entries and the adoption process for new detection techniques.

Last full verification: 2026-08-30 (HTTP status via automated checks; links
marked "content" were additionally inspected for the right project).

## Adopted / candidate techniques (verified)

| Technique | Link | Status | Note |
|---|---|---|---|
| DeepfakeBench | https://github.com/SCLBD/DeepfakeBench | 200, content | unified benchmark; adopt its metric conventions (AUC/EER) |
| NPR | https://github.com/chuangchuangtan/NPR-DeepfakeDetection | 200, content | inspiration for the NPR-consistency feature in `frequency.py` |
| LGrad | https://github.com/chuangchuangtan/LGrad | 200 | candidate direction |
| SBI | https://github.com/mapooon/SelfBlendedImages | 200, content | implemented (simplified) in `experiments/sbi.py` |
| CNNDetection | https://github.com/PeterWang512/CNNDetection | 200 | reference for artifact detectors |
| F3-Net | https://github.com/yyk-wew/F3Net | 200, content | frequency-aware learning; DCT features in `frequency.py` |
| AIDE | https://github.com/shilinyan99/AIDE | 200 | registry candidate (code + checkpoints public) |
| NTIRE 2026 robust detection | https://arxiv.org/abs/2604.11487 | 200, content | robustness target for future evaluation |
| RingID | https://github.com/showlab/RingID | 200 | watermarking; ECCV 2024 (not IEEE S&P) |

## Rejected / removed entries (keep out)

- `researchtrend.ai/papers/2602.07814` — domain no longer resolves
  (removed from the registry and docs in P0).
- MDPI Sensors `1424-8220/26/8/2389` — 403 to automated checks and content
  could not be verified; removed (bot-blocked hosts are treated as reachable
  by the checker, but unverifiable paper links stay out of the registry).
- Any "detection repo" whose owner/name cannot be resolved by the GitHub API
  (fabricated attributions such as `ControlNet/LipForensics` or
  `balazsbear/ComfyUI_PuLID_Flux_Enhanced` seen in third-party surveys).

## Adoption process for a new technique

1. Verify the link manually and via `scripts/check_registry_links.py`.
2. Add an entry here with the verification date and what was checked
   (link only vs. content).
3. Prototype in `experiments/` (training) or as a numpy-only measurement in
   the package (screening), with a test that verifies the measurement on a
   synthetic construction of the artifact.
4. Evaluate on a labeled split with `eval` (AUC/EER, cross-dataset when
   possible). No accuracy claim may be committed without the evaluation
   script and data manifest that produced it.
5. Only then wire the signal into the ensemble with a calibrated weight
   (Phase 1 threshold registry), and record the decision in the PR.
