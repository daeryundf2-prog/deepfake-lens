# Deepfake Lightweight Tool Research Notes

Date: 2026-05-24

## Build Shape

The first useful version should be a local Android utility:

- Pick a folder through Android Storage Access Framework.
- Scan direct child files only for the first release.
- Analyze supported text and image files.
- Sort AI-generated candidates first.
- Show a compact explanation and possible source/tool guess.

## Android Folder Access

Use Storage Access Framework rather than raw filesystem paths.

Relevant sources:
- Android SAF guide: https://developer.android.com/guide/topics/providers/document-provider
- `ActivityResultContracts.OpenDocumentTree`: https://developer.android.com/reference/androidx/activity/result/contract/ActivityResultContracts.OpenDocumentTree
- `DocumentsContract.buildChildDocumentsUriUsingTree`: https://developer.android.com/reference/android/provider/DocumentsContract

Implementation implication:
- Use `OpenDocumentTree` to get the selected folder URI.
- Use `DocumentsContract.getTreeDocumentId`, `buildChildDocumentsUriUsingTree`, and `ContentResolver.query` for direct-child listing.
- Avoid recursive scanning in v0.1.
- Cap the scan count, for example 100 files.

## Image Decoding and Metadata

Relevant sources:
- Android `ImageDecoder`: https://developer.android.com/reference/android/graphics/ImageDecoder
- AndroidX `ExifInterface`: https://developer.android.com/reference/androidx/exifinterface/media/ExifInterface
- W3C PNG spec: https://www.w3.org/TR/REC-png.pdf
- ComfyUI workflow metadata: https://docs.comfy.org/development/core-concepts/workflow

Implementation implication:
- Decode image content URIs on a worker thread.
- Downsample before pixel analysis.
- Read EXIF/XMP fields such as `Software`, `ImageDescription`, and `UserComment`.
- PNG AI workflows may live in `tEXt`, `zTXt`, or `iTXt` chunks, so a lightweight PNG text-chunk reader may be needed for Stable Diffusion/ComfyUI clues.

## AI Source Guessing

Strong source attribution:
- C2PA/OpenAI Content Credentials saying ChatGPT/API/DALL-E/OpenAI.
- EXIF/XMP/software metadata mentioning Stable Diffusion, Automatic1111, ComfyUI, Midjourney, DALL-E, Firefly, Runway, Leonardo.ai.
- ComfyUI workflow metadata embedded in generated images.

Weak source attribution:
- Text explicitly mentions ChatGPT, Claude, Gemini, etc.
- AI assistant self-reference such as "as an AI language model".

Do not use as exact attribution:
- Square resolution alone.
- Smooth skin or visual weirdness.
- Generic polished writing style.
- Pixel statistics without model-specific training.

## Detection Limits

Relevant sources:
- NIST synthetic content report: https://airc.nist.gov/docs/NIST.AI.100-4.SyntheticContent.ipd.pdf
- NIST GenAI text challenge: https://ai-challenges.nist.gov/text-2026
- OpenAI AI text classifier limitations: https://openai.com/index/new-ai-classifier-for-indicating-ai-written-text/
- GenImage benchmark: https://papers.nips.cc/paper_files/paper/2023/hash/f4d4a021f9051a6c18183b059117e8b5-Abstract-Datasets_and_Benchmarks.html
- DE-FAKE detection and attribution paper: https://arxiv.org/abs/2210.06998
- Synthbuster dataset: https://zenodo.org/records/10066048

Implementation implication:
- The app must say "candidate" and "possible source", not "confirmed fake".
- Short text should become "cannot determine" or low-confidence, not "human".
- Image source attribution should require metadata/provenance or future trained model support.

## Provenance and Watermarks

Relevant sources:
- C2PA specification: https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html
- OpenAI image verification: https://openai.com/research/verify/
- Google DeepMind SynthID: https://deepmind.google/models/synthid/

Implementation implication:
- C2PA metadata is strong when present, but it can be removed.
- SynthID detection is not the same as visual AI guessing; it requires watermark detection support.
- For v0.1, detect obvious metadata/provenance clues first. Full C2PA signature validation can be future work.

## Recommended v0.1 Feature Set

- Folder picker.
- Direct-child scan.
- Supported files: `.jpg`, `.jpeg`, `.png`, `.webp`, `.txt`, `.md`.
- Candidate list sorted by score.
- Candidate detail:
  - suspicion score
  - candidate type
  - possible AI/source tool
  - confidence
  - top 3 reasons
  - suggested next checks
- No upload, no login, no server.

