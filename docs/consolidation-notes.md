# Consolidation Notes — duplicate analysis paths

Status date: 2026-09-11 (Stage 0 audit). Two pairs of modules cover the same
ground at different depths. This note records what each path does, where they
overlap, and the recommended resolution. No merging has been done yet; the
recommendations below are for a later stage.

## 1. Pixel analysis: `pixel_analyzer.py` vs `pixel.py`

### `pixel.py` — `analyze_image_pixels()` (canonical ensemble)

- Multi-expert ensemble (`local-multiexpert-pixel-v1`): baseline pixel stats,
  frequency forensics (NPR/DCT via `frequency.py`), difference-in-difference
  reconstruction, SPARK-IL-style prototype retrieval, low-correlation/fractal,
  alpha blending, VRAG-DFD, plus an optional external-model adapter
  (`_ivy_xdetector_adapter`) — fused by a fuzzy decision tree.
- Modes `off` / `fast` / `deep`; `deep` adds a localization expert + heatmap.
- Stdlib-first: decodes PNG rasters itself with a decompression safety cap;
  Pillow is optional for other formats; numpy only enables the frequency
  expert. Returns `available=False` / "판단 어려움" rather than crashing.
- Call site: `core.py` scan pipeline (`analyze_image_pixels` at
  `core.py:307`).

### `pixel_analyzer.py` — `analyze_pixels()` (quick cv2 screen)

- Single-pass cv2/numpy feature extraction (histogram entropy, edge density,
  Laplacian texture variance, FFT magnitude stats, HSV saturation) scored by
  a short weighted-signal list into high/medium/low bands.
- Hard dependency on opencv + numpy; returns `QuickPixelAnalysis`
  (deliberately not `PixelAnalysis` — the two result models are
  incompatible).
- Call sites: `cli.py` `pixel-analysis` command (`cli.py:817`),
  `webapp.py` quick-scan (`webapp.py:187`).

### Overlap

Both measure the same low-level cues — smoothness/entropy, edge statistics,
frequency uniformity, color distribution — but with different decoders,
different thresholds, and different result schemas. `pixel_analyzer.py` is a
strict subset in capability: anything it can flag, the `pixel.py` experts can
express as an expert score with a citation.

### Resolution (executed 2026-09-12)

Kept `pixel_analyzer.py` as the **fast pre-screen tier** and labelled it in
result metadata: `QuickPixelAnalysis.analysis_tier="pre-screen"` vs
`PixelAnalysis.analysis_tier="ensemble"` (round-tripped through the scan
JSON cache). Scoring was deliberately NOT delegated to the `pixel.py`
experts: the ensemble's local heuristics measured AUROC 0.43-0.48 on
ProGAN (below chance, see ROADMAP), so re-emitting its score under a
"quick screen" name would imply more analysis than either tier performs.
The long-term plan below still stands — once the AIDE runtime profile
lands, the pre-screen tier should prefer the neural adapter and fall back
to the expert ensemble, at which point this module's own signal functions
can be deleted and the module shrinks to a result-shape adapter.

## 2. Provenance/forensics: `enhanced_forensics.py` vs `c2pa.py`

### `c2pa.py` — `analyze_metadata_forensic()` (canonical provenance path)

- SDK-first: `validate_c2pa_manifest()` runs the official c2pa-python SDK
  when installed (optional `provenance` extra) and reports real validation
  state, signer, and per-claim codes; byte-marker scanning is only a demoted
  fallback when the SDK is absent, and is skipped entirely when the SDK
  authoritatively finds no manifest.
- Also checks Google tool metadata strings, watermark/tool-identifying
  strings, ExifTool JSON in JPEG COM, PNG text chunks, JPEG APP1/EXIF.
- Enforces `MAX_FORENSIC_FILE_BYTES` (256 MB) before reading the file.
- Call sites: `core.py` scan pipeline, `cli.py:553`, `api_server.py:120`,
  `webapp.py:181`; exported from `__init__.py`.

### `enhanced_forensics.py` — `analyze_forensic()` (legal-report packaging)

- Byte-marker scan: `b"c2pa"`/`b"jumbf"` presence, watermark strings
  (Google/SynthID/Adobe Firefly/Midjourney), JPEG EXIF marker, provenance
  strings (Content Credentials/Adobe/OpenAI/Google), file-size and gzip
  structure checks.
- Wraps findings in `ForensicReport`: SHA-256 file hash, timestamp, report
  ID, legal notes, Korean legal-style text via `generate_legal_text()`, and
  an unkeyed integrity checksum (explicitly documented as *not* a
  signature).
- Call sites: `cli.py` `legal-report` command (`cli.py:861`),
  `test_phase4_reports.py`. **Not** used by the scan pipeline.

### Overlap

`_analyze_metadata()` and `_analyze_provenance()` here re-scan for the same
markers `c2pa.py` already detects — but shallower: substring presence only,
no SDK validation, no file-size cap, no distinction between "manifest
verified" and "the bytes `c2pa` appear somewhere". Its confidence values
(0.9 for a `c2pa` byte match) are stronger than what `c2pa.py` assigns to
the equivalent fallback signal (weight 10, explicitly caveated as string
detection, not verification).

### Resolution (executed 2026-09-12)

Done as recommended: `analyze_forensic` now delegates evidence collection
to `analyze_metadata_forensic()` and translates `ProvenanceRecord`s plus
the two record-less signals (PNG text chunks, JPEG APP1 EXIF) into
`ForensicEvidence` entries. Confidence follows the c2pa.py calibration:
0.9 only for an SDK-`valid` manifest, 0.5 for an SDK-detected but
incompletely validated manifest (e.g. untrusted signer), 0.3 for a bare
byte-marker match (`evidence_type` is now `c2pa_marker`, not
`c2pa_manifest`), 0.4 for tool-metadata strings. `_analyze_metadata` and
`_analyze_provenance` were deleted; `_analyze_structure` was kept (file
size/gzip are not provenance markers) but now reads only magic bytes +
`stat` instead of the whole file. The packaging contract — hash, report
ID, legal text, checksum — is unchanged, and the analysis limitations now
flow into the report's legal notes.

## Standing contract (unchanged by consolidation)

- Scores are prioritization evidence, not truth labels.
- Missing optional dependencies or unreadable inputs return
  `available=False` / "판단 어려움" results — never crash.
- No new mandatory dependencies; c2pa-python stays an optional
  `provenance` extra.
