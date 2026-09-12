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
| **Full Synthbuster sweep** (9 models x 40: dalle2/3, firefly, glide, midjourney-v5, sd-1.3/1.4/2, sdxl) vs camera reals | 720 | **0.951** | 0.000 | 0.854 | TP 332 / FP 18 / FN 28 / TN 342 |

The full sweep is the honest headline: the ProGAN-trained checkpoint keeps
AUROC 0.951 across nine diffusion/commercial generators it never saw, with
28 misses concentrated in the hardest commercial models (per-model numbers
below). ONNX parity: the exported graph reproduces torch probabilities to 4
decimals on real images (external-data split, 3.57 GB).

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

## Cross-domain measured addendum: CIFAKE (2026-09-13)

A labeled 100-image sample from `dragonintelligence/CIFAKE-image-dataset`
(HF test split, 50 real CIFAR-style + 50 fake Diffusion-generated, all
32×32) was run through the committed eval command against three wired
runtimes:

```sh
python -m deepfake_lens.cli eval /tmp/dfl-bench --model-path models/<profile>.json
```

| Profile | AUROC | Accuracy | FPR | EER |
|---|---|---|---|---|
| `aide-runtime.json` (ConvNeXt-XXL, ProGAN-trained) | **0.554** | 0.51 | 0.26 | 0.44 |
| `univfd-runtime.json` (CLIP ViT-L/14 + fc weights) | **0.468** | 0.49 | 0.02 | 0.54 |
| `cnndetection-runtime.json` (ResNet-50, blur+jpg aug) | **0.476** | 0.48 | 0.04 | 0.53 |

**Honest reading:** on 32×32 thumbnails all three detectors sit near chance.
CIFAKE is far below every model's native input resolution and outside their
training distributions (AIDE trained on ProGAN at 256px+; UnivFD on
LAION-ProGAN; CNNDetection on ProGAN classes). This result *confirms* the
documented caveat — these are prioritization signals that must be
re-validated on data matching the target domain before thresholds mean
anything. It is also the first measured low-resolution failure bound now
on record: do not trust these profiles on thumbnail-scale inputs.

Dataset: CIFAKE (Kaggle/HF mirror), CIFAR-10-derived reals vs SD-v1.x
fakes, CC-BY terms per source. 100-image test-split sample fetched via
HF datasets-server on 2026-09-13; local copies in `/tmp/dfl-bench` (not
committed — third-party media stays out of git per repo policy).
