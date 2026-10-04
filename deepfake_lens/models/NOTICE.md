# Third-party model licenses

Deepfake Lens code is MIT-licensed. Model **weights are never committed** —
each runtime profile in this directory fetches its checkpoint from upstream
and the upstream license applies to the weights, not the MIT license of this
repository. Verify each license before redistributing or commercializing.

| Profile | Upstream | License | Verified |
|---|---|---|---|
| `aide-runtime.json` | https://github.com/shilinyan99/AIDE | MIT | LICENSE file in repo (2026-09) |
| `aasist-runtime.json` | https://github.com/clovaai/aasist | MIT | LICENSE file in repo (2026-09) |
| `cnndetection-runtime.json` | https://github.com/PeterWang512/CNNDetection | Custom/nonstandard (GitHub: NOASSERTION) — read `LICENSE`/`terms` in the repo before redistribution | 2026-09 |
| `univfd-runtime.json` | https://github.com/YuhengLi99/UniversalFakeDetect | **No license file published** — absent a license the default is all-rights-reserved; treat as research-use and contact upstream before redistribution | 2026-09 |
| `dire-runtime.json` | https://github.com/ZhendongWang6/DIRE | **No license file published** — same caveat as UnivFD | 2026-09 |
| `openai-detector-runtime.json` | https://huggingface.co/openai-community/roberta-base-openai-detector | MIT | model card (2026-09) |
| `syncnet_v2.model` + `sfd_face.pth` (not profiles — `lipsync.py` assets) | https://www.robots.ox.ac.uk/~vgg/software/lipsync/ (joonson/syncnet_python) | **Research/non-commercial** (VGG hosting terms) — fetch via `scripts/fetch_syncnet.py`, do not redistribute | 2026-09 |
| `speechbrain/spkrec-ecapa-voxceleb` (used by `audio.py`, not a profile) | https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb | Apache-2.0 | model card (2026-09) |

Each profile's `license` field mirrors this table. The `models` CLI prints
the same field, and every scan result surfaces the profile's `limitations`.
