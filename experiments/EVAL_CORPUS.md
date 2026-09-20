# Labeled evaluation corpus — layout, provenance, regeneration

The corpus lives at `eval_corpus/` (gitignored — content is derived data,
only the build scripts are committed). It backs `experiments/eval_all.py`
and `experiments/suggest_weights.py`.

## Layout and provenance

| Path | n | Source | License/status |
|---|---|---|---|
| `text/real/ko_wiki_*.txt` | 398 | Korean Wikipedia paragraphs (wikimedia/wikipedia 20231101.ko, streamed) | CC BY-SA; derived excerpts, not redistributed |
| `text/fake/ko_qwen05_*.txt` | ~88 | Local Qwen2.5-0.5B-Instruct, 8 prompt styles × 25 topics | locally generated |
| `text/fake/ko_qwen15_*.txt` | ~29 | Local Qwen2.5-1.5B base, prompt-completion | locally generated; cross-generator holdout |
| `audio/real/*.wav/flac` | ~7 | torchaudio YESNO (8 kHz telephone speech) + LibriSpeech sample | public domain / CC BY 4.0 |
| `audio/fake/*.mp3/wav` | ~9 | edge-tts (ko/en voices) + Windows SAPI | locally generated |
| `image/real/` | 26 | lenna + 25 held-out samples from Hemg/deepfake-and-real-images (HF, real class) | dataset license per HF page |
| `image/fake/` | 29 | 4 DALL-E samples from Wikimedia Commons + 25 held-out Hemg fakes | AI-generated; Hemg provenance per HF page |
| `face/real/` | 54 | Wikimedia vintage/diverse portraits + xdomain crops (via `scripts/fetch_diverse_faces.py`) | public domain portraits |

Face positives are **synthesized at eval time** — `eval_all.py` runs SBI
self-blends on `face/real/` so the fake class never goes stale relative to
the training recipe.

## Regenerate

```bash
python scripts/build_eval_corpus.py --out eval_corpus --parts text,audio
python scripts/fetch_diverse_faces.py --out eval_corpus/face_src  # then copy crops into face/real
```

Human/real sides need network; AI/TTS sides need local CPU inference
(Qwen ~1 GB download once) plus network for edge-tts.

## Run the evaluation

```bash
# everything
python experiments/eval_all.py --corpus eval_corpus --report report.json --md report.md

# one modality
python experiments/eval_all.py --corpus eval_corpus --modality audio

# measurement-derived weight suggestions (review before writing)
python experiments/suggest_weights.py report.json
python experiments/suggest_weights.py report.json --write   # updates profiles
```

## Caveats baked into interpretation

- `text/fake` is dominated by **one generator family (Qwen)** — a detector
  that aces this corpus may still miss other generators. The KoELECTRA
  experiment measured exactly this failure (holdout AUROC 1.0, cross-gen
  recall 0.10). Treat high corpus scores as necessary-not-sufficient.
- `image/fake` now mixes DALL-E (4) and Hemg parquet fakes (25) — the Hemg
  generator mix is undocumented on the HF page, so treat image metrics as
  cross-generator-agnostic rather than per-generator.
- `face/` positives are SBI self-blends — coverage of Deepfakes/
  FaceShifter-class pipelines is inferred, not measured.
- Small n means wide confidence intervals; AUROC differences <0.1 on
  n<100 are noise-level.
