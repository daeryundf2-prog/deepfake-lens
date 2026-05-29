# Deepfake Lens MVP

`deepfakeclassifier` is a standalone Android module for checking AI-generated text, suspicious image signals, and supported files inside a user-selected folder.

## Current Approach

- Text analysis runs fully on-device with explainable heuristics:
  - AI self-disclosure phrases
  - template-like conclusion/summary phrasing
  - list-heavy answer structure
  - unusually uniform sentence lengths
  - repeated phrase shingles
  - missing personal anchors in long text
- Image analysis runs fully on-device with metadata and pixel statistics:
  - known generator names in EXIF metadata
  - PNG `tEXt`, `iTXt`, and `zTXt` metadata used by Stable Diffusion/A1111 and ComfyUI workflows
  - square generator-style resolutions
  - unusually smooth or high-frequency texture patterns
  - uniform saturation and repeated regional patterns
- Folder analysis uses Android Storage Access Framework:
  - direct child files only
  - max 100 files per scan
  - `.jpg`, `.jpeg`, `.png`, `.webp`, `.txt`, and `.md`
  - candidate-first sorting
  - unsupported and failed files are kept visible but de-emphasized
- Source guessing stays metadata-first:
  - high confidence for explicit tool metadata or known workflow fields
  - medium confidence for generation-like metadata fields
  - unknown when clues are missing or stripped

## Product Boundary

The app reports a review score and possible source clues, not a final truth claim. Real deepfake detection needs trained image/video models, provenance checks, source comparison, and calibration data.

## Recommended Next Steps

1. Add a small evaluation dataset with known human, AI-generated, camera, and generated-image examples.
2. Add C2PA/content credential parsing when available.
3. Replace or complement image heuristics with a TensorFlow Lite classifier.
4. Add calibrated thresholds and confusion-matrix reporting before any high-stakes use.
