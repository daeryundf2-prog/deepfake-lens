# AIDE Detector Evaluation Report

Date: 2026-09-09. All numbers below were produced by
`scripts/eval_aide.py` (committed) with the official AIDE
`progan_train.pth` checkpoint (3.3 GB, Google Drive link from the AIDE
README). The reimplementation in `scripts/run_aide.py` was verified
bit-exact against the official AIDE code first: identical logits on a
fixed random input, trunk maxdiff 0.0 (see "Reimplementation parity"
below).

## Measured results

| Test set | Samples | AUROC | EER | Accuracy @0.5 | At target FPR 5% |
|---|---|---|---|---|---|
| ProGAN cat (test split) | 400 | **1.000** | 0.000 | 1.000 | TP 200 / FP 10 / FN 0 / TN 190 |
| ProGAN airplane (unseen category) | 120 | **1.000** | 0.000 | 1.000 | TP 60 / FP 3 / FN 0 / TN 57 |
| Cross-domain: Synthbuster dalle2+glide vs camera reals | 90 | **1.000** | 0.000 | 0.933 | TP 60 / FP 2 / FN 0 / TN 28 |

Context: the heuristic pixel ensemble measured AUROC **0.43-0.48** on the
same ProGAN data (below chance) — the pretrained AIDE detector closes the
measured pixel gap completely on GAN data and generalizes to diffusion
images (DALL-E 2, glide) out of the box.

## Reproduction

```sh
# checkpoint: progan_train.pth from the AIDE README's Google Drive folder
python scripts/eval_aide.py --checkpoint aide-progan.pth \
    --root <labeled 0_real/1_fake folder> --report aide-eval.json
```

Per-image inference (single file):

```sh
python scripts/run_aide.py --checkpoint aide-progan.pth --image suspect.png
```

## Reimplementation parity (why the numbers are trustworthy)

1. `DctPreprocessor` output matches `AIDE.data.dct.DCT_base_Rec_Module`
   exactly (allclose on all 4 views, 1e-4 tolerance).
2. The ConvNeXt trunk is timm's `convnext_xxlarge` — the same module open_clip
   wraps for AIDE. Hand-rolled substitutes diverge badly (measured trunk
   maxdiff 14.1 before the switch). Head surgery (global_pool/flatten ->
   Identity) reproduces AIDE's [b, 3072, 8, 8] feature map; state-dict keys
   match the checkpoint 378/378.
3. Full-model logits on a fixed input are identical to the official code
   (maxdiff 0.0); on real images the outputs match the official pipeline
   (real ~0.05 fake-prob, fake ~0.95).
4. The SRM HPF kernels are vendored verbatim from AIDE
   (`experiments/aide_srm_kernels.py`) and match the checkpoint's stored
   `hpf.hpf.weight` exactly.

## Caveats (per VERIFIED_REGISTRY rules)

- The `progan_train.pth` checkpoint was trained on ProGAN; the airplane
  category and the Synthbuster diffusion models are outside its training
  mix, but full cross-generator claims need the full Synthbuster set plus
  RAISE-1k reals (manual license request) — the 90-image cross test here is
  a promising smoke, not a benchmark claim.
- Real comparators for Synthbuster used ProGAN's 0_real (RAISE-style camera
  images); a proper Synthbuster eval pairs it with RAISE-1k itself.
- The checkpoint license is AIDE's research code license; redistribution of
  the checkpoint is not covered here — users download it themselves.
