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

### Recommended resolution

Keep `pixel_analyzer.py` as a **fast pre-screen tier** for the surfaces that
already use it (CLI `pixel-analysis`, webapp quick view), but reimplement it
as a thin wrapper: decode once with cv2, then delegate scoring to the
`pixel.py` expert functions instead of maintaining a parallel heuristic set.
Long term, when the AIDE runtime profile (roadmap P1) lands, the pre-screen
tier should prefer the neural adapter and fall back to the expert ensemble —
at which point `pixel_analyzer.py`'s own signal functions can be deleted and
the module shrinks to a result-shape adapter for its two call sites.

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

### Recommended resolution

Keep `enhanced_forensics.py` as the **report-packaging layer** (hashing,
report ID, legal text, checksum) — that part has no duplicate — but make its
evidence collection delegate to `c2pa.py::analyze_metadata_forensic()`
instead of its own byte scans, translating `MetadataForensicAnalysis`
signals/provenance records into `ForensicEvidence` entries. This removes the
conflicting confidence calibration (a raw `c2pa` substring should never
outscore an SDK-verified manifest) and inherits the size cap and SDK path
for free. The `_analyze_metadata`/`_analyze_provenance`/`_analyze_structure`
private functions can then be deleted; tests in `test_phase4_reports.py`
should keep passing since the public `analyze_forensic` contract is
unchanged.

## Standing contract (unchanged by consolidation)

- Scores are prioritization evidence, not truth labels.
- Missing optional dependencies or unreadable inputs return
  `available=False` / "판단 어려움" results — never crash.
- No new mandatory dependencies; c2pa-python stays an optional
  `provenance` extra.
