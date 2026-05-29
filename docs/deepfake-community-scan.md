# Community Scan: Lightweight AI-Generated Material Analyzer

Date: 2026-05-24

This scan prioritizes community practice over official documentation. Treat these findings as anecdotal product signals, not authoritative guarantees.

## Communities and Surfaces Searched

- Reddit: `r/StableDiffusion`, `r/comfyui`, `r/aiwars`, `r/ArtificialInteligence`, `r/Teachers`, `r/Professors`, `r/ChatGPT`, `r/PromptEngineering`
- GitHub projects/issues: AUTOMATIC1111, ComfyUI, PromptSniffer, metadata extraction tools
- Hugging Face model/tool pages
- Stack Overflow Android SAF / EXIF threads
- Korean communities: DCInside AI creation / singularity / Wrtn AI-related galleries where indexed

## Main Community Pattern

The strongest practical pattern is not "detect AI from pixels only." It is:

1. Scan local folders.
2. Read embedded generation metadata.
3. Parse known formats from common tools.
4. Show prompt/model/seed/workflow when present.
5. Mark missing metadata as "unknown", not "human".

This matches the desired app: a local folder tool that identifies likely AI-generated files and explains why.

## Metadata Findings

### Stable Diffusion / AUTOMATIC1111

Community and project docs repeatedly point to PNG text chunks and image metadata as the best first signal.

Useful signals:
- PNG `parameters` / `tEXt` chunk
- JPEG/WebP EXIF `UserComment`
- fields such as prompt, negative prompt, steps, sampler, CFG scale, seed, model hash, model name

Implementation note:
- Add a small PNG text-chunk reader instead of relying only on EXIF.
- A1111-style metadata should map to `Stable Diffusion / A1111 추정`.

### ComfyUI

Community posts and tools revolve around extracting ComfyUI workflow JSON from images. Users often drag PNGs back into ComfyUI to recover the graph.

Useful signals:
- PNG text chunks named like `workflow` and `prompt`
- JSON containing node graphs, checkpoint loaders, LoRA loaders, samplers, seeds, CFG, dimensions
- WebP or video metadata may also appear in some workflows, but keep v0.1 image/text only

Implementation note:
- If JSON has ComfyUI workflow/prompt structure, classify source as `ComfyUI 추정` with high confidence.
- If custom nodes make parsing hard, still expose raw metadata summary.

### Metadata Loss

Community consensus is strong that metadata is fragile:

- Discord, Twitter/X, Reddit, Facebook, and editing tools often strip or rewrite metadata.
- Photoshop/editor round trips may lose PNG chunks.
- Some users intentionally strip EXIF/XMP/C2PA/prompt metadata before sharing.

Implementation note:
- `메타데이터 없음` must not lower suspicion too much.
- Display: `출처 단서 없음` instead of `AI 아님`.

## Existing Tool Patterns

Tools repeatedly mentioned or discoverable:

- PromptSniffer: extract/remove AI generation metadata; supports ComfyUI, Stable Diffusion, SwarmUI, InvokeAI-style metadata.
- Image MetaHub: local-first folder/library manager for large AI output collections; supports A1111, ComfyUI, InvokeAI, Forge, Fooocus, SwarmUI, Draw Things, Midjourney/Niji, Firefly, DreamStudio, DALL-E when metadata exists.
- AI Meta Viewer: browser extension that scans web/local images for metadata; supports PNG `tEXt/iTXt`, JPEG, WebP, AVIF, and stealth PNG info.
- AI-Metadata-Inspector / SD Image Info / Diffusion Toolkit: local prompt/workflow inspection tools.

Product implication:
- Our differentiator should be Android folder scan + suspicious candidate report, not a huge pro metadata library.
- The app should copy the "local-first, no upload" trust posture.

## Detector Reliability Findings

Community discussion around AI image/text detectors is full of false-positive and false-negative reports.

For images:
- Edited real images, upscaled images, HDR/studio/macro shots, screenshots, and compressed images can be false positives.
- Pixel-only detection is treated as supporting evidence, not proof.

For text:
- Academic/professional style, predictable phrasing, ESL writing, and short passages often trigger false positives.
- Teachers/professors in community threads repeatedly warn not to use detectors as sole evidence.

Implementation note:
- Keep labels as `후보`, `추정`, `주의`, `판단 어려움`.
- Add "suggested next checks" instead of final accusations.

## Android Implementation Signals From Community

Stack Overflow threads reinforce:

- Use Storage Access Framework for user-selected directories.
- `DocumentFile.findFile()` and recursive SAF traversal can be slow.
- `DocumentFile.listFiles()` is simple but can have overhead.
- AndroidX ExifInterface supports `InputStream` from content URIs and is preferable to raw path assumptions.

Implementation note:
- v0.1 should use direct-child scan only.
- Cap scanned files.
- Avoid repeated `findFile()` loops.
- Decode images and read metadata off the main thread.

## Korean Community Signals

Korean AI art communities point users toward:

- Civitai and prompt-sharing sites.
- Prompt/EXIF viewers where metadata remains.
- Stable Diffusion prompt/model/VAE/LoRA workflows.
- Metadata removal/download helper tools.

Implementation note:
- Korean UI should mention `프롬프트`, `모델`, `시드`, `LoRA`, `샘플러`, `CFG` because those terms are familiar in local AI art communities.
- Source labels should include both general tool and detailed generation parameters when present.

## Confidence Rules for This App

High confidence source guess:
- C2PA or verified provenance names the tool.
- Metadata explicitly contains `ComfyUI`, `Automatic1111`, `Stable Diffusion`, `DALL-E`, `Midjourney`, `Firefly`, etc.
- ComfyUI/A1111 structured metadata is present.

Medium confidence source guess:
- Metadata contains generation-like fields but no clear tool name.
- Filename or sidecar text includes tool/model clues.
- Text explicitly names a tool used to create it.

Low confidence source guess:
- Only visual/pixel/statistical signals.
- Only generic AI-like writing style.
- Only square image size or common generator resolution.

Unknown:
- Metadata absent or stripped and no strong content signal.

## Immediate Implementation Adjustments

1. Add metadata parser modules:
   - EXIF/XMP field reader
   - PNG text chunk reader
   - A1111 parameters parser
   - ComfyUI workflow/prompt JSON detector
2. Add `SourceGuess` to result:
   - label
   - confidence
   - reasons
3. Add folder scan:
   - direct children only
   - max file cap
   - candidate-first sorting
4. Update UX:
   - "AI 생성물 후보"
   - "추정 도구"
   - "출처 단서 없음"
   - "추가 확인 필요"
5. Keep detector score secondary to provenance/metadata evidence.

