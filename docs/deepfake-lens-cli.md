# Deepfake Lens CLI

`deepfake-lens` is the first local scanning engine for the AI-generated material checker.

It is intentionally CLI-first:

- Faster to validate than a browser UI.
- Better for large folders.
- Reusable later from a local web app or localhost API.
- No upload, login, cloud AI API, or new runtime dependency.

## Command Reference

Every subcommand, briefly. Detail for the core workflow lives in the sections below.

**Two output shapes only (phase-0 fix D1).** A conclusion ("is it fake") always comes from
`analysis_api.analyze_path`, the same path as `scan`: three verdicts (`verdict_code`
`manipulation_evidence` / `authenticity_evidence` / `undetermined`), `grade`, `evidence`,
`coverage`. `forensic`, `classify`, `multimodal FILE…`, `explain FILE`, `agent` and
`legal-report` print that **analysis_result** shape. Every per-layer command (`audio`,
`video-analysis`, `text-advanced`, `pixel-analysis`, `inpaint`, `prnu`, `rppg`, `face`,
`avatar`, `3d`, `realtime`, `faceswap-seam`, `compare`, `ml-classify`) prints a **layer
diagnostic** instead: `kind: "layer_diagnostic"`, `measured: false`, `raw_score`, the layer's raw
numbers under `diagnostic`, `reference_band` (`reference` = the layer ran, `unavailable` = it
could not run, with the reason in `reference_note`) and the fixed notice
"이 출력은 측정되지 않은 참고 신호이며 결론이 아닙니다. 결론은 `scan`을 사용하십시오." No standalone
command prints a band (높음/주의/낮음) or a "의심 신호가 강합니다/적습니다" sentence.

- `scan <folder>`: folder screening with metadata + optional pixel ensemble and reports. The table lists every row except `원본성 근거 있음` rows, which `--include-low` (alias `--include-authentic`; the old name is kept for compatibility and no longer means "low risk") adds; the number of omitted rows is printed so the table reconciles with the header counts (R5/R16); `--sign` adds an HMAC-SHA256 `signature`/`signature_key_id` to `--json-out` (key via `--key-file` or `DEEPFAKE_LENS_REPORT_KEY`; proves integrity to the key holder, not legal non-repudiation — anyone holding the key can sign). The MAC covers every field except `signature` and `signature_key_id` — including `signature_note`, `tool_version`, `model_pins` (profile name + pin, `null` when unpinned) and every item's `sha256` (G30). `eval` and `benchmark` accept the same flags; `verify-report <report.json>` (below) and `deepfake_lens.signing.verify_report(path, key)` verify and report `검증됨` / `변조됨` / `키 ID 불일치` / `서명 없음` / `검증 키 없음`, and a missing key produces an unsigned report whose note says `서명 없음` rather than a failure — but an explicit `--key-file` that is empty or unreadable stops any command before it starts (exit 2, `verify-report` exit 4) with `오류: 서명 키가 비어 있습니다: <file>` / `오류: 서명 키 파일을 읽을 수 없습니다: <file> (…)` (N8). `--html-out`/`--pdf-out`/`--forensic-pdf-out` are signed the same way when `DEEPFAKE_LENS_REPORT_KEY` is set (HTML embeds the signed JSON body in `<script id="deepfake-lens-signed-report">`; PDFs print the signature and the signed body's SHA-256) and say `서명 없음` otherwise.
- Non-recursive scans (N8): `scan` without `--recursive` analyzes the folder's direct files only and says how many subfolders it did not enter — `summary.subfolders_skipped` in the JSON and one table line `참고: 하위 폴더 N개는 검사하지 않았습니다(바로 아래 파일만 검사) — 포함하려면 --recursive 를 추가하십시오.` (the GUI banner says the same).
- Logs (N8): every command logs per-file failures (unreadable/corrupt evidence — truncated JPEG, garbage WAV, broken DOCX…) with their tracebacks to `$DEEPFAKE_LENS_LOG_DIR/deepfake-lens.log` (default `~/.cache/deepfake-lens/logs/`, never the evidence folder); stderr gets one Korean line `참고: 판독 불가·손상 파일 등 처리 오류 N건 — … 로그 파일 <path>에 기록했습니다(--verbose 로 화면에도 표시).` The failure itself is always in the row's coverage. `--verbose` (any command) also prints the log records and tracebacks on stderr.
- Scan order and cache (G11/G32): the whole walk is collected and sorted once by the full relative path string in POSIX form (`B.txt` < `a-dir/x.txt` < `a.txt` < `sub/c.txt` < `z.txt` — plain string order, not "files first, then subfolders"), so the same folder yields the same file order on every OS and a `--max-files` cap keeps the same files. `--cache` entries are keyed by the file's SHA-256 + its extension (`ext:.png`; N6 — the extension selects the analysis and the C2PA reader, so same bytes under another extension are analyzed on their own) + a hash of the analysis options + tool version + the sorted model-profile pin list (`unpinned:<name>` for profiles without a pin) — never by path, size or mtime (key layout `content-v4`). R14-3: the key also carries the output-format generation (`fmt:<n>`, `scan_cache.OUTPUT_FORMAT_GENERATION`), bumped in every commit that changes what a row says for the same input (reasons, limitations, verdict wording, path scrubbing), so a row written by an older commit of the same tool version is never replayed; and a cached row whose text names a native decoder's staging folder or staged temp name (`deepfake-lens-native-…`, `000001-<12 hex>.<ext>`, written before R13-1) is dropped when the cache is loaded. A row served for a same-bytes file under another name has every path-dependent text rewritten to the current file (`<root>/<path>` and the file name), so a cached scan prints exactly what an uncached scan prints (N6). An empty file's `c2pa` check is `failed` with `빈 파일 — C2PA 매니페스트를 읽을 수 없습니다` whatever its extension (the SDK answered `.png` and `.jpg` differently). Each scanned item records its content `sha256`; with `--dedupe` the same digest is reused rather than hashing twice.
- `collect <folder> --out`: write a dataset collection plan.
- `dataset <folder> --manifest-out`: labeled-dataset manifest, audit, split and robustness plans.
- `corpus build <dir> --out manifest.json [--label-from-dir]` / `corpus split --manifest m.json --seed N --ratio 60/20/20 [--group-by <field>]` / `corpus verify --manifest m.json`: reproducible `corpus-manifest-v1` manifests (content hashes, label/generator/variant from `<label>/<generator>/<variant>/file`, train/val/test splits that keep every variant of one original together, re-hash check). See "Reproducible corpora and the measurement gate" below.
- `eval <folder>`: labeled-dataset metrics (accuracy/precision/recall/FPR, AUROC, EER, per-split), each AUROC/recall/FPR with a 95% bootstrap CI and n_pos/n_neg, on raw uncalibrated member scores (`score_basis: "raw, uncalibrated"`).
- `benchmark <folder>`: pixel-mode/model matrix benchmark.
- `fusion <folder> --out` / `calibrate <folder> --out` / `train <folder> --out`: fusion profile, threshold calibration, portable baseline.
- `feedback <labels.jsonl>`: join examiner verdicts (`{path, expected_label, notes?}`) to a prior `--scan-json` payload or a live rescan; emits a per-signal accuracy report and an advisory `--profile-out` fusion-weight suggestion (never applied automatically; thresholds are left unchanged). A label is joined to a `--scan-json` row (R10-4) by, in order: the row's recorded `path`; its real path (the unescaped path of a real file — `tri:::c.png` for the row `tri\:\:\:c.png` — or `<container>::<member>` from a member row's fields); the row whose real path ends the label path (an absolute label against a row relative to the scan root); the file name. The first rule with candidates decides; a label that several rows fit is listed in `unmatched`.
- `models [--focus]`: detector registry and runtime profile scaffolding.
- `train-neural-plan <folder> --out`: neural training/ONNX handoff plan.
- `video <folder> --out --frame-root`: video frame extraction plan (ffmpeg optional).
- `video-analysis <file>`: temporal consistency heuristics (stability signals skip static footage). **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `audio <file>`: AI-generation/voice-cloning heuristics (jitter/shimmer regularity included). **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `face <file>`: face manipulation heuristics (boundary blending, reflection, color temperature). Landmark anchors are measured via MediaPipe FaceMesh when the `face_mediapipe` extra is installed; otherwise they are labelled `landmarks_source=box-ratio-estimate`. **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `inpaint <file>`: inpainting/partial manipulation heuristics. **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `text-advanced <file>`: advanced text stylometry analysis (no `ai_probability`: score/100 is not a probability). **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `forensic <file>`: the file's three-verdict result (same as `scan`; an A1111 PNG is `manipulation_evidence`) plus the C2PA/provenance metadata scan as `layer_diagnostics.provenance_metadata` (SDK validation when `provenance` extra installed).
- `classify <file>`: the file's three-verdict result plus `tool_candidates` — a layer diagnostic of AI-tool marker matches (word-boundary matching; a candidate name, never a verdict).
- `multimodal [FILE…]`: with files, each one is analyzed through `analysis_api` and the combined `verdict_code` follows the decision-rule order (any manipulation evidence → `manipulation_evidence`; all authenticity → `authenticity_evidence`; else `undetermined`). The legacy `--image-score/--text-score/…` inputs and `--av-sync <video>` (audio-envelope vs motion-envelope cross-correlation, requires opencv+librosa) are a layer diagnostic only.
- `realtime [--scores …] [--alert-threshold N]`: moving average of uncalibrated frame scores; crossings of an operator-chosen `--alert-threshold` are recorded (no default — the old 67/35 cutoffs were never measured; `--warning-threshold` is ignored). **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `rppg <video>`: CHROM cardiac-pulse screening from face video. **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `prnu <target> --reference ...`: sensor-fingerprint provenance correlation. **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `evidence <file>`: forensic evidence chain with measured integrity verification.
- Exit codes (S4, N4/N7): every command exits 2 on a usage error (argparse prints `usage: …`), on a bad **input path** (below), on an explicit `--key-file` that is empty or unreadable (N8; `verify-report`: 4) and when an optional dependency a requested output needs is missing (R6/R8: PDF renderer, fastapi/uvicorn). An unexpected internal error exits 1 with `오류: 처리 중 예기치 않은 오류가 발생했습니다(<예외 클래스>) — 상세는 로그 파일을 확인하십시오` on stderr and the traceback in the log file — never an English traceback on the console, never an empty report with exit 0.

  **Input paths (N4/N7).** Every subcommand declares its input paths in `deepfake_lens/cli_inputs.py` (`INPUT_SPECS`: argument, file/folder, supported formats) and every one is checked by `require_input_path` before the command does any work. A bad path prints one line on stderr and nothing on stdout, exit 2 (`verify-report` 4, because its 2 means "키 ID 불일치"):

  | 입력 문제 | stderr | 종료 코드 |
  | --- | --- | --- |
  | 없는 경로 | `오류: 파일을 찾을 수 없습니다: <경로>` / `오류: 폴더를 찾을 수 없습니다: <경로>` / (`evidence-statement`, `--model-path`) `오류: 파일이나 폴더를 찾을 수 없습니다: <경로>` | 2 |
  | 파일 자리에 폴더 | `오류: 파일이 아니라 폴더입니다: <경로>` (판정 명령은 ` (폴더는 scan을 사용)` 추가) | 2 |
  | 폴더 자리에 파일 | `오류: 폴더가 아니라 파일입니다: <경로>` (`scan`은 ` (단일 파일은 forensic/classify를 사용)` 추가) | 2 |
  | 파일 경로 끝에 구분자 (`photo.png/` — Z3; 모든 명령의 입력 경로, 분석하지 않음. 폴더 끝의 `/`는 그대로 허용) | `오류: 폴더가 아니라 파일입니다: <입력한 경로> — 경로 끝의 구분자('/')는 폴더를 뜻합니다. 파일이면 구분자 없이 지정하십시오` | 2 |
  | 지원되지 않는 형식 | `오류: 지원되지 않는 형식입니다: <경로> (지원 형식: …)` | 2 |
  | 계층 명령에 심볼릭 링크 | `오류: 심볼릭 링크는 따라가지 않습니다: <경로> — 링크 대상 파일을 직접 지정하십시오` (판정 명령은 G6대로 건너뜀 행) | 2 |
  | 일반 파일이 아님 / 확인 불가 | `오류: 일반 파일이 아닙니다: <경로>` / `오류: 경로를 확인할 수 없습니다: <경로>` | 2 |
  | 빈 경로 `""` (Y3 — 현재 폴더로 바뀌지 않음; 모든 경로 인수) | `오류: 인수 <이름>: 경로가 비어 있습니다 — 빈 문자열("")은 경로로 쓸 수 없습니다(현재 폴더는 . 으로 지정)` | 2 |
  | `--thresholds`가 임계값 프로필이 아님(깨진 JSON, 다른 형식, 버전 불일치 — Y2; 기본값으로 계속하지 않음) | `오류: 임계값 프로필을 읽을 수 없거나 버전이 맞지 않습니다: <경로> (layer-thresholds-v1 JSON이어야 합니다)` (깨진 JSON이면 ` — JSON 형식 오류: <사유>(<행>행 <열>열)` 추가) | 2 |
  | 읽어 들이는 파일이 UTF-8이 아님·읽기 실패·JSON 형식 오류·JSON 객체가 아님 (P4/P9 — `--thresholds`, `--fusion-profile`, `--calibration`, `--model-path <프로필.json>`, `--hash-db`, `feedback` 라벨·`--scan-json`, `corpus --manifest`, `evidence-statement <scan.json>`, `verify-report`(종료 4), `--law-firm`이 있는 명령의 설정 파일 `~/.deepfake-lens/config.json`) | `오류: <무엇>을 읽을 수 없습니다(인코딩): <경로> (UTF-8 텍스트가 아닙니다(바이트 위치 N))` / `오류: <무엇>을 읽을 수 없습니다: <경로> (<사유>(오류 번호 N))` / `오류: <무엇>을 해석할 수 없습니다: <경로> (JSON 형식 오류: <사유>(<행>행 <열>열))` / `… (JSON 객체가 아니라 배열입니다)` — 사유는 한국어(영어 예외 문구·파이썬 형식 이름 없음) | 2 |
  | 출력 파일 인수(`--json-out`, `--html-out`, `--csv-out`, `--pdf-out`, `--md-out`, `--out`, `--output`, `--cache`, …)가 기존 폴더 (Y7 — 검사 전에 확인) | `오류: 출력 경로가 폴더입니다: <경로> — 저장할 파일 이름을 지정하십시오` | 2 |
  | `--*-out` 출력 파일(`--json-out`, `--csv-out`, `--html-out`, `--pdf-out`, `--md-out`, `--forensic-pdf-out`, `--evidence-statement-out`, `--manifest-out`, …)의 상위 폴더가 없음 (Z5 — 검사 전에 확인, 폴더를 만들지 않음; `--out`, `--output`, `--cache`, `--hash-db`는 종전대로 상위 폴더를 만듦) | `오류: 출력 폴더가 없습니다: <상위 폴더> — 출력 폴더는 자동으로 만들지 않습니다. 폴더를 먼저 만들거나 기존 폴더를 지정하십시오` | 2 |
  | 출력 파일 인수가 검사 대상 폴더 안(`--json-out`, `--cache`, `--heatmap-dir` 등 모든 출력 — P11; R9-4: 출력 폴더 `video --frame-root`, `train-neural-plan --output-dir`, `vendor-weights --to`/`--bundle-to`/`--models-dir`(설치 묶음 안)도 — 증거 폴더에는 아무것도 쓰지 않음) | `오류: 출력 경로가 검사 대상 폴더 안에 있습니다: <경로> — 검사 대상 폴더(<폴더>) 밖에 저장하십시오(증거 폴더에는 아무것도 쓰지 않습니다)` | 2 |
  | 출력 파일 인수가 입력 파일과 같음(입력 검사 JSON, 증거 파일, 프로필 — P11; 덮어쓰지 않음) | `오류: 출력 경로가 입력 파일과 같습니다: <경로> — 입력 파일은 덮어쓰지 않습니다. 다른 파일 이름을 지정하십시오` | 2 |
  | 출력 폴더(또는 만들어질 위치의 상위 폴더)에 쓸 수 없음 — 읽기 전용 폴더·마운트 (P10; 검사 전에 os.access와 임시 파일 생성·삭제로 확인 — 종전에는 검사 뒤 '처리 오류 N건') | `오류: 출력 폴더에 쓸 수 없습니다: <폴더> (<사유>) — 검사 전에 확인했습니다. 쓰기 가능한 폴더를 지정하십시오` / `오류: 출력 파일에 쓸 수 없습니다: <경로> (쓰기 권한이 없습니다) — 검사 전에 확인했습니다` | 2 |
  | `--cache`가 검사 캐시가 아닌 기존 파일(보고서 JSON 등 — Y4; 덮어쓰지 않음). 캐시 파일은 `{"format": "deepfake-lens-cache-v1", "version": 1, "items": {…}}`; 없는 파일·빈 파일·머리글 이전의 `{"version", "items"}` 캐시는 그대로 사용 | `오류: 캐시 파일이 아닙니다: <경로> — --cache 파일 형식(deepfake-lens-cache-v1)이 아닌 기존 파일은 덮어쓰지 않습니다. 새 캐시 파일 경로를 지정하십시오` | 2 |
  | `evidence-statement <scan.json>`에 검사 결과 행이 없음 (Y1) | `오류: 검사 JSON에 items가 없습니다(검사 결과 행 0건): <경로>` | 2 |
  | `feedback` 라벨 파일(JSONL)의 한 행이 JSON이 아님 — 잘린 줄 포함 (R9-5; 앞의 UTF-8 BOM은 무시하고 읽음 — 종전에는 BOM·잘린 줄을 0건으로 읽고 종료 코드 0) | `오류: 피드백 파일 <n>행을 해석할 수 없습니다: <경로> — <이유>` (R10-8: 이유의 위치도 파일의 행 번호 — `… (<n>행 <m>열)`, 종전에는 그 행 안의 `(1행 <m>열)`) | 2 |
  | `feedback` 라벨 파일에 쓸 수 있는 라벨 행이 없음(path·인식 가능한 expected_label 없음 — R9-5) | `오류: 피드백 파일에 사용할 수 있는 라벨 행이 없습니다: <경로> (행 <n>개) — …` | 2 |

  Inputs and their kinds (supported formats = the formats the command reads):

  | 명령 | 입력 | 종류 · 형식 |
  | --- | --- | --- |
  | `scan`, `collect`, `dataset`, `eval`, `benchmark`, `fusion`, `calibrate`, `train`, `train-neural-plan`, `video`, `batch`, `perf`, `corpus build` | `folder` | 폴더 |
  | `forensic`, `classify`, `explain`, `legal-report`, `multimodal FILE…` | 파일 | 파일 · scan 지원 형식(이미지·오디오·영상·텍스트·문서·압축); 심볼릭 링크는 건너뜀 행(G6) |
  | `agent --file` | 파일 | 파일 · 텍스트/문서 |
  | `audio` | 파일 | 파일 · 오디오(.wav .mp3 .flac .ogg .m4a .aac .wma .opus) |
  | `face`, `inpaint`, `pixel-analysis`, `ml-classify`, `faceswap-seam`, `prnu` (+ `--reference`) | 파일 | 파일 · 이미지(.jpg .jpeg .png .webp .bmp .gif .tif .tiff) |
  | `video-analysis`, `rppg`, `multimodal --av-sync` | 파일 | 파일 · 영상(.mp4 .mov .m4v .avi .mkv .webm .flv) |
  | `avatar --file` | 파일 | 파일 · 영상·이미지 |
  | `text-advanced` | 파일 | 파일 · .txt .md |
  | `3d --file` | 파일 | 파일 · 3D(.glb .gltf .obj .fbx .ply .pcd .npy .npz)·.txt .md |
  | `compare` | `file_a`, `file_b` | 파일 · 오디오 쌍 또는 텍스트·문서 쌍(.txt .md .rst .log + 문서); 종류가 섞이면 `오류: 두 파일의 종류가 같아야 합니다…` 2 |
  | `watermark` | 파일 | 파일 · 텍스트·문서; `--secret`/`--synthid-keys` 둘 다 없으면 2 |
  | `evidence` | 파일 | 파일 · 형식 제한 없음(모든 파일의 연속성 기록) |
  | `evidence-statement` | `target` | 폴더, 검사 결과 .json 또는 scan 지원 형식의 파일 |
  | `feedback` | `labels`, `--scan-json` | 파일 · .json/.jsonl, .json |
  | `verify-report` | `report` | 파일 · .json (종료 코드 4) |
  | `corpus split`, `corpus verify` | `--manifest` (+ `--root` 폴더) | 파일 · .json |
  | `models` | `--checkpoint` | 파일 |
  | `web` (`--folder`, `--allow-root`), `api-serve` (`--allow-root`), `vendor-weights --install` | 폴더 | 폴더 |
  | 공통 설정 입력: `--thresholds`, `--fusion-profile`, `--calibration` | | 파일 · .json (없는 파일을 경고 후 내장 기본값으로 진행하던 동작 폐지) |
  | 공통 설정 입력: `--model-path` | | 프로필·체크포인트 파일 또는 프로필 폴더 |
  | 공통 설정 입력: `--models-dir` | | 폴더 (`vendor-weights`는 설치 대상일 수 있어 제외) |

  `deepfake_lens/tests/test_cli_inputs.py` runs every declared input of every subcommand in a subprocess with a nonexistent path, a folder for a file, a file for a folder and an unsupported extension, and checks that no `Path`-typed input of `cli_parser` is undeclared. R10-9: every `Path`-typed argument of every subcommand is either a registered write target (`OUTPUT_FILE_ATTRS`, `OUTPUT_FOLDER_ATTRS`, `COMMAND_OUTPUT_FOLDER_ATTRS` — checked against the examined folder before any work) or declared read-only (`INPUT_SPECS`, `COMMON_INPUT_SPECS`, `PATH_OPTIONS_NOT_OUTPUT`, `PATH_OPTIONS_READ_ONLY`); `cli_inputs.unclassified_path_arguments` lists any other, whatever its name (a new `--frames` or `--dest`), and the test requires none. R11-11: a `str`-typed argument counts as a path too when its option strings, dest, help or metavar name a path (경로/폴더/파일/디렉터리/dir/path/file/folder) or its default is a `Path`; such arguments are classified as above, edited in place (`PATH_OPTIONS_EDITED_IN_PLACE` — `vendor-weights pin <프로필>`), or declared not a path (`STR_OPTIONS_NOT_PATHS` — `corpus build --corpus-id`); `watermark --tokenizer` is read-only. Per command:

  | 명령 | 0 | 1 | 2 | 3 | 4 |
  | --- | --- | --- | --- | --- | --- |
  | `scan` | 검사 완료 — 결론(조작·생성 근거 있음 포함)과 무관 | 예기치 않은 내부 오류 | 입력 경로 오류(위 표); `오류: 폴더를 읽을 수 없습니다: <경로> (<사유>)`; 잘못된 옵션; 빈·읽을 수 없는 `--key-file`; PDF 출력에 필요한 렌더러 없음 | — | — |
  | `verify-report` | `검증됨` | `변조됨` | `키 ID 불일치` (argparse 사용 오류도 2) | `서명 없음` | 입력 경로 오류(위 표), 보고서 JSON 해석 불가(깨진 JSON·JSON 객체가 아님·R10-5: 파이썬 재귀 한도를 넘는 중첩 `(JSON 중첩이 너무 깊습니다(파이썬 재귀 한도 초과))` — Z4: stderr `오류: 보고서 JSON을 해석할 수 없습니다: <경로> (<사유>)`, stdout 없음), 검증 중 RecursionError/ValueError(R10-5: `오류: 입력을 처리할 수 없습니다(<사유>) — 상세는 로그 파일 <경로>을(를) 확인하십시오`), 검증 키 없음, 빈·읽을 수 없는 `--key-file` |
  | `evidence-statement` | 작성 완료 | 예기치 않은 내부 오류 | 입력 경로 오류(`오류: 파일이나 폴더를 찾을 수 없습니다: <경로>` 등), 검사 JSON 해석 불가, 폴더를 읽을 수 없음(`scan`과 같은 문구), `--pdf-out`에 필요한 렌더러 없음, 빈·읽을 수 없는 `--key-file`, 잘못된 옵션 | — | — |
  | `forensic` / `classify` / `explain FILE` / `legal-report` / `agent --file` / `multimodal FILE…` | 분석 완료 — 결론과 무관(심볼릭 링크는 건너뜀 행) | 예기치 않은 내부 오류 | 입력 경로 오류(G5/N4, 분석·보고서 ID 발급 전에 중단); 잘못된 옵션 | — | — |
  | 계층 진단 명령(`audio`, `face`, `video-analysis`, `inpaint`, `text-advanced`, `pixel-analysis`, `ml-classify`, `rppg`, `prnu`, `faceswap-seam`, `3d`, `avatar`, `compare`, `watermark`, `realtime`) | 계층 진단 출력 | 예기치 않은 내부 오류, 파일 내용을 읽지 못함(`compare` 처리 실패, `watermark` 텍스트 추출 실패 — stderr `오류: …`) | 입력 경로 오류(위 표), 잘못된 옵션; `realtime --scores`에 정수가 아닌 값(Z1: `오류: --scores에는 쉼표로 구분한 정수만 쓸 수 있습니다: '<값>' (예: --scores 10,20,30)`); `ml-classify` 이미지 디코드 실패(Y6: stderr `오류: 이미지를 읽을 수 없습니다(손상되었거나 디코드할 수 없는 이미지): <경로>`, stdout 없음) 또는 opencv/numpy 없음 | — | — |
  | 폴더 명령(`collect`, `dataset`, `eval`, `benchmark`, `fusion`, `calibrate`, `train`, `train-neural-plan`, `video`, `batch`, `perf`, `corpus …`) | 완료 | 예기치 않은 내부 오류(`corpus verify`: 검증 실패) | 입력 경로 오류(위 표), 잘못된 옵션, 매니페스트 해석 불가(`corpus`) | — | — |
  | `evidence`, `models`, `feedback`, `web`, `vendor-weights` | 완료 | 예기치 않은 내부 오류(`vendor-weights`: 고정 실패 — 체크포인트 없음, 허브 조회 실패 등) | 입력 경로 오류(위 표), 잘못된 옵션(R10-5: `web --port`가 1–65535 밖이거나 정수가 아님 — `오류: 인수 --port: 포트는 1–65535 범위의 정수여야 합니다: <값>`); `vendor-weights pin`의 프로필이 파일도 모델 디렉터리의 프로필 이름도 아님(Z2: `오류: 파일을 찾을 수 없습니다: <프로필> — 프로필 파일 경로도, 모델 디렉터리 <경로>의 프로필 이름도 아닙니다`) | — | — |
  | `api-serve` | 서버 정상 종료 | 예기치 않은 내부 오류 | 입력 경로 오류(`--allow-root`), fastapi/uvicorn 없음(한국어 설치 안내), localhost가 아닌 주소에 `--token` 없이 바인드, 잘못된 옵션(`--port` 1–65535 밖 — R10-5, `web`과 같은 문구) | — | — |

  R10-5 (round 10): a `RecursionError` or `ValueError` that escapes any command (an input it cannot use — e.g. a JSON nested past Python's recursion limit) exits with the input-error code — `verify-report` 4, every other command 2 — and prints `오류: 입력을 처리할 수 없습니다(<사유>) — 상세는 로그 파일 <경로>을(를) 확인하십시오`; JSON inputs read before the command runs (`evidence-statement <scan.json>`, `feedback`, `--thresholds`, `corpus --manifest` …) say `JSON 중첩이 너무 깊습니다(파이썬 재귀 한도 초과)` in their usual `해석할 수 없습니다` line. Any other unexpected error exits 1 with `오류: 처리 중 예기치 않은 오류가 발생했습니다(<예외>) — 상세는 로그 파일 <경로>을(를) 확인하십시오`. The `참고: 판독 불가·손상 파일 등 처리 오류 N건 — 각 행의 검사 범위(coverage)에 사유가 기록…` line counts per-row errors only and is omitted when the command produced no row.

  The web/API servers answer the same folder errors with the same Korean text in `error` (`/api/scan`).
- Single-file commands and archives (B1): `forensic`, `classify`, `explain FILE`, `legal-report`, `evidence-statement <file>`, `multimodal FILE…` and `agent --file` analyze the file through the folder scanner's own body (`analysis_api.analyze_rows` → `core.scan_paths` with the file's folder as root). A named symbolic link follows the scan's rule (G6): it is the scan's skipped row (`심볼릭 링크 — 링크를 따라가지 않으므로 분석하지 않았습니다…`, no `sha256`), its target is never opened — the provenance/tool-marker side layers report `심볼릭 링크 — … 실행하지 않았습니다`; `/api/analyze-file` does the same (the link is confined by the folder it lives in) and answers a missing file or a folder with the same Korean text in `error`. An archive is therefore expanded exactly as `scan` expands it — the same member rows (`archive.zip::inner/path`), the same container row (verdict roll-up, rejected members, limitations) and the same SHA-256 values; the printed conclusion is the container row's and the member rows are listed under it in the scan table's wording (`legal-report` text: `=== 압축 구성 파일 ===`). The JSON carries the raw rows as `rows[]` (contract: `docs/deepfake-lens-json-contract.md`).
- `api-serve [--token] [--allow-root DIR]`: REST API server (token mandatory off-localhost; contract: `docs/deepfake-lens-service.md`). Read roots come only from `--allow-root` (there is no `--folder` here); without a token every `/api/*` route except `/api/health` requires the `X-Deepfake-Lens-Client` header, like the built-in web server. Without `fastapi`/`uvicorn` it prints a Korean install hint and exits 2 (no traceback).
- `batch <folder>`: parallel per-file analysis (same engines/thresholds as `scan`, via `analysis_api.analyze_path`).
- `explain <file> [--format text|json]`: which decision rule (1-6 of `decision.decide`) produced the file's verdict, with its evidence and coverage. `--score` alone is a layer diagnostic stating that a raw score cannot be explained.
- `agent --text|--file`: the text's three-verdict result (text is reference grade, so always `undetermined`) plus the AI-agent marker heuristics as a layer diagnostic. `3d --text|--file` / `avatar --file`: 3D-asset and avatar marker heuristics. **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `pixel-analysis <file>`: cv2-based quick pixel screen (QuickPixelAnalysis, `analysis_tier="pre-screen"` — the scan pipeline's `--pixel` ensemble is a separate tier), behind the same photo/non-photo gate as `scan` (a non-photo is `reference_band: unavailable`, "사진 아님: …"). **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `ml-classify <file>`: feature-threshold rules (requires opencv/numpy; a corrupt image is `오류: …` on stderr, exit 2 — Y6); reports `rule_weight_sum` and `rules_matched`, never an ai/natural label or probability. **계층 진단(참고 신호 · 미측정)** — `kind: "layer_diagnostic"`, `measured: false`, raw numbers, `reference_band` (`reference`/`unavailable`), no band, no verdict.
- `legal-report <file> [--output F] [--json-out F] [--key-file F] [--analyst-id ID] [--format text|json]`: legal-style report built from the scan result (`analysis_api.analyze_path`): conclusion (`verdict_code`, grade), every evidence item (kind/direction/strength/layer), every coverage entry, limitations, threshold provenance, file SHA-256 and the package `tool_version`. `--json-out` writes the body signed with HMAC-SHA256 when a key is set (`--key-file` or `DEEPFAKE_LENS_REPORT_KEY`), otherwise with the 서명 없음 note; check it with `verify-report`.
- `verify-report <report.json> [--key-file F] [--format text|json]`: verify a signed report (scan `--json-out --sign`, `legal-report --json-out`, `evidence-statement --json-out`, …). The key comes from `--key-file` or `DEEPFAKE_LENS_REPORT_KEY`. Prints one of `검증됨` (exit 0), `변조됨` (exit 1 — any byte of the signed body changed), `키 ID 불일치` (exit 2 — signed under a different key), `서명 없음` (exit 3 — the report carries no signature); a missing key, or a report that is not a JSON object (corrupt JSON, a list, … — Z4: `오류: 보고서 JSON을 해석할 수 없습니다: <경로> (<사유>)` on stderr, nothing on stdout) exits 4.
- `perf <folder> --out`: throughput/cache/duplicate-rate report.
- `security --out` / `release --out`: guardrail and release-readiness reports.
- `web`: local web GUI (localhost; Host-header guarded; contract: `docs/deepfake-lens-service.md`).
- `vendor-weights [--verify|--fetch|--bundle-to|--install|--manifest-out]`: air-gap weight inventory, verification and bundling. `--fetch` downloads `https://` URLs only, caps each download, and records a checkpoint without a declared `pin.sha256` as `unverified` (exit 1), never `fetched`.
- `vendor-weights pin <profile> [--revision <commit>]`: write the profile's `pin` — the sha256 of its local checkpoint, or the current Hugging Face commit of its hub model (via `huggingface_hub`, online, only on this explicit command; `--revision` sets it offline). See "Weight pins" below.
- `compare <file_a> <file_b> [--format json|table] [--ecapa-revision <commit>]`: two-file comparison — same-speaker distance for an audio pair, same-author stylometry for a text/document pair (the same function as the web/API `/api/compare`). The SpeechBrain ECAPA-TDNN speaker model loads only at a pinned hub commit (`--ecapa-revision` or `$DEEPFAKE_LENS_ECAPA_REVISION`, 40 hex; G10); without one it is not downloaded and the MFCC fallback runs with that reason in `limitations`.
- `doctor [--models-dir DIR] [--format table|json] [--json-out]`: environment and model-integrity diagnostics. Every runtime profile gets three columns — **pin** (the `pin` object carries every key its runtime needs), **런타임 의존성** (the runtime's modules actually import: torch/transformers for hub classifiers, onnxruntime for `onnx`, …) and **체크포인트** (the local file exists and its sha256 matches the pin; hub runtimes are pinned by revision instead). The **실행 가능** summary counts only `supported:true` profiles whose three columns are all OK — the same set a scan of that models dir reports as `ran` in coverage (`model:<name>`, QA-SYS-3); a hub model is never OK without torch/transformers (G29). The thresholds row shows the profile's state label (`측정됨` / `잠정(미검증)` / `in-sample(참고)`). Exit code is always 0 — doctor reports, it does not gate.
- `evidence-statement <folder|scan.json|file> [--case-no … --pdf-out … --md-out … --format table|json|markdown]`: ECFS evidence explanation statement (증거설명서) from a prior scan JSON, a folder (scanned through the same `analysis_api` path and the same `AnalysisOptions` as `scan` — X1: `--max-files` with scan's default 1000, `--recursive`, `--allow-symlinks`; it used to stop silently at 100 files, flat) or one file. Every statement (Markdown, PDF, JSON, table) has a `기록되지 않은 파일` section with the count and reasons of the folder's files without an analysis result (파일 상한, 하위 폴더 미포함, 심볼릭 링크, 중복, 미지원, 기타 건너뜀); a JSON input takes the cap/subfolder counts from its `summary`. Office identity (N17): no law firm, address or phone number is built in — the report headers, the 소송대리인 line and the forensic PDF signoff print `--law-firm` / `--contact` (on `scan` and `evidence-statement`), else the `law_firm` / `contact` keys of `~/.deepfake-lens/config.json` (`$DEEPFAKE_LENS_CONFIG` names another file), else nothing (the header shows the generic `디지털포렌식 감정센터`); the web `/api/report` takes `law_firm` / `contact` from the request body with the same fallback.
- `faceswap-seam <image> [--thresholds FILE] [--format table|json] [--json-out]`: face-swap boundary-seam / Poisson-feathering / sensor-noise mismatch heuristics on detected faces; thresholds default to `<models_dir>/thresholds.json` like `scan`.
- `watermark <file> --secret|--synthid-keys --tokenizer-revision <commit>`: KGW / SynthID-Text watermark verification under a known key; the hub tokenizer must be pinned to a 40-hex commit or the check reports unavailable (`미고정 프로필`).

## Usage

```sh
python -m deepfake_lens scan /path/to/folder
python -m deepfake_lens scan /path/to/folder --include-low   # = --include-authentic: also list 원본성 근거 있음 rows
python -m deepfake_lens scan /path/to/folder --recursive --max-files 5000
python -m deepfake_lens scan /path/to/folder --json-out report.json --csv-out report.csv
python -m deepfake_lens scan /path/to/folder --pixel fast
python -m deepfake_lens scan /path/to/folder --pixel deep --heatmaps --heatmap-dir heatmaps
python -m deepfake_lens scan /path/to/folder --pixel deep --cache .cache/deepfake-lens.json --workers 4 --html-out report.html --pdf-out report.pdf
python -m deepfake_lens scan /path/to/folder --recursive --dedupe --hash-db .cache/hashes.json --max-file-bytes 25000000
python -m deepfake_lens collect /path/to/dataset-root --out collection-plan.json
python -m deepfake_lens dataset /path/to/dataset --manifest-out dataset-manifest.json --fingerprints --audit-out audit.json --split-out split.json --robustness-out robustness.json
python -m deepfake_lens eval /path/to/dataset --pixel deep --json-out eval.json --html-out benchmark.html --false-positive-out fp.jsonl --false-negative-out fn.jsonl
python -m deepfake_lens eval /path/to/dataset --pixel deep --robustness --json-out robustness-eval.json
python -m deepfake_lens benchmark /path/to/dataset --pixel-modes off,deep --model-path aide-profile.json --json-out matrix.json --md-out matrix.md
python -m deepfake_lens fusion /path/to/dataset --pixel deep --model-path aide-profile.json --target-fpr 0.05 --out fusion-profile.json
python -m deepfake_lens feedback examiner-labels.jsonl --scan-json report.json --json-out feedback-report.json --profile-out suggested-fusion-profile.json
python -m deepfake_lens scan /path/to/folder --fusion-profile fusion-profile.json
python -m deepfake_lens benchmark /path/to/dataset --pixel-modes off,fast,deep --fusion-profile fusion-profile.json --json-out matrix.json
python -m deepfake_lens calibrate /path/to/dataset --pixel deep --out calibration.json
python -m deepfake_lens calibrate /path/to/dataset --pixel deep --out calibration.json --mapping-out calibration-profile.json
python -m deepfake_lens train /path/to/dataset --pixel deep --out portable-model.json
python -m deepfake_lens train-neural-plan /path/to/dataset --out neural-plan.json --output-dir experiments/run-001
python experiments/train_detector.py --manifest dataset-manifest.json --arch convnext_tiny --epochs 10 --out experiments/run-001
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript --out experiments/run-001/convnext_tiny.onnx
python -m deepfake_lens models --json-out detector-registry.json
python -m deepfake_lens models --candidate aide-iclr-2025 --checkpoint models/aide.onnx --profile-out aide-profile.json
python -m deepfake_lens video /path/to/videos --out video-plan.json --frame-root extracted-frames
python -m deepfake_lens perf /path/to/folder --pixel deep --workers 4 --cache .cache/scan.json --hash-db .cache/hashes.json --out perf.json
python -m deepfake_lens security --out security-check.json
python -m deepfake_lens release --out release-check.json
python -m deepfake_lens web --folder /path/to/folder --allow-root /path/to/other-case
```

`security` runs behavioral checks (localhost bind, LAN token, client
header, symlink opt-in, oversize skip, path redaction) plus the QA-SYS-6
(signature covers the whole report) and QA-SYS-7 (read-root confinement)
test suites, and fails when the QA tests are not available (they ship only
in the source tree). `web --allow-root <dir>` (repeatable) adds read roots
next to `--folder`; the GUI can only scan or read files under them.

If installed from the package, the entry point is:

```sh
deepfake-lens scan /path/to/folder
```

## Supported Files

- `.png`
- `.jpg`
- `.jpeg`
- `.webp`
- `.txt`
- `.md`

## Detection Strategy

For large-folder speed, the CLI is metadata-first:

- Reads only a bounded prefix of image files by default.
- Extracts PNG `tEXt`, `iTXt`, and `zTXt` chunks.
- Searches image headers for explicit generation metadata.
- Reads only a bounded prefix of text files.
- Sorts high/medium/unknown candidates before low-signal and unsupported files.

Pixel-level analysis is opt-in:

- `--pixel off` keeps the default metadata-first scanner.
- `--pixel fast` adds local pixel, spectral/statistical, reconstruction, retrieval, compositing, fuzzy-fusion, and external-baseline adapter experts.
- `--pixel deep` also adds SAFE-style local manipulation/localization.
- `--heatmaps` with `--pixel deep` writes small PNG heatmaps for localization review. Without `--heatmap-dir` they go to `$DEEPFAKE_LENS_HEATMAP_DIR` or `~/.cache/deepfake-lens/heatmaps/<folder key>/` — never into the scanned evidence folder (R-IN-1, QA-IN-1: a scan leaves every evidence file's bytes, mtime and the folder listing unchanged).
- PNG pixels are decoded with the built-in reader. JPEG/WebP pixel analysis works when Pillow is available, without making Pillow a required dependency.
- Ivy-xDetector can be used as an external baseline by placing a sidecar next to the image, for example `image.png.ivy.json` or `image.ivy.json`, with `score`, `fake_score`, `probability`, or `label`.
- `--model-path` can point to a JSON score profile, a sidecar profile, or an optional ONNX/TorchScript runtime profile. ONNX Runtime, PyTorch, Pillow, and NumPy remain optional local dependencies rather than mandatory install requirements.
- Without `--model-path` the engine set is every `*-runtime.json` in the models dir (`--models-dir`, `$DEEPFAKE_LENS_MODELS_DIR`, or the packaged `models/`) — the same set the web GUI and the REST API use (G7, `deepfake_lens.analysis_api`). Profiles that are `supported:false` or unpinned are recorded in coverage as skipped/failed, never silently dropped. `--no-default-engine` runs no models.
- Thresholds: `--thresholds FILE`, else `<models_dir>/thresholds.json` when present (stderr warns when it is provisional or `in-sample(참고)`). The JSON `thresholds` block records which profile produced the scan; GUI and API scans report the same block for the same folder.

The recent-research layer is represented in the JSON report as named experts:

1. `difference_in_difference_reconstruction`
2. `spark_il_spectral_retrieval`
3. `low_correlation_fractal_signal`
4. `alpha_blending_compositing`
5. `safe_pixel_localization`
6. `vrag_dfd_local_retrieval`
7. `reveal_evidence_chain`
8. `agentfox_explainable_summary`
9. `fuzzy_decision_tree_fusion`
10. `ivy_xdetector_adapter`

## Source Guessing

High-confidence source guesses require explicit metadata or known workflow fields:

- Stable Diffusion / A1111
- ComfyUI
- Midjourney / Niji
- DALL-E / OpenAI
- Adobe Firefly
- Runway
- Leonardo.ai
- NovelAI

Text source guesses are only made when the text directly names a tool or contains assistant self-reference.

## Implementation Map

The next-stage plan is implemented as local-first commands and adapters:

1. Dataset preparation: `collect` writes a real/AI source collection plan; `dataset` discovers `ai`, `edited`, and `real` folder labels, writes a manifest, can add SHA-256 fingerprints, writes an audit, plans deterministic splits, and emits a robustness transform plan.
2. Evaluation runner: `eval` reports threshold, accuracy, precision, recall, false-positive rate, AUROC, confusion counts, false-positive/false-negative case files, benchmark HTML, source-attribution coverage, and per-source metrics. `benchmark` compares multiple pixel modes and model profiles in one matrix.
3. Calibration and fusion: `calibrate` writes a versioned threshold profile targeting a requested false-positive rate; `--mapping-out` additionally writes an isotonic (pool-adjacent-violators, dependency-free) score-calibration profile with a mapping table, method, and dataset fingerprint. Calibrated values are dataset-dependent screening confidences, not truth probabilities, and an undersized dataset produces an honest `insufficient-data` profile rather than a fabricated mapping. `fusion` calibrates a local metadata/pixel/external-model/source score profile, and `scan`, `eval`, and `benchmark` accept `--fusion-profile`.
4. Pretrained detector adapter: `--model-path` accepts JSON score profiles, sidecar profiles, direct `.onnx`/`.pt`/`.pth`/`.torchscript` paths, and JSON runtime profiles for optional ONNX/TorchScript inference. `models --profile-out` scaffolds candidate profiles such as AIDE.
5. Training baseline: `train` creates a portable threshold model from local scores until verified neural checkpoints are available. `train-neural-plan` writes the ConvNeXt/ONNX training handoff plan, and optional scripts in `experiments/` run a real PyTorch image experiment when `torch`, `torchvision`, and `Pillow` are installed.
6. Patch/localization: `--pixel deep --heatmaps` writes SAFE-style PNG localization heatmaps; HTML reports embed small heatmap previews.
7. Source attribution: JSON separates `ai_score`, `source_guess`, and `source_attribution_label`. Metadata rules now include Flux, Ideogram, Imagen/Gemini, Recraft, Canva AI, and Grok/xAI in addition to earlier sources.
8. Large scans and performance: `--cache`, `--workers`, `--dedupe`, `--hash-db`, `--max-file-bytes`, and `--progress` support resumable parallel scans with duplicate and oversize handling. `perf` writes a throughput/cache/duplicate-rate report for local tuning.
9. Local web app: `web` starts a localhost-only UI/API with escaped table rendering, optional recursive/dedupe scans, model/fusion profile fields, and heatmap preview serving constrained to the read roots plus the tool-owned heatmap output root (`*.heatmap.png` only).
10. Reports: `--html-out` and `--pdf-out` write review artifacts with optional `--redact-paths`; HTML reports embed heatmaps when available. Y10: the HTML table has a `SHA-256` column — every row's full digest as the scan recorded it, or the PDFs' `해시 불가(…)` reason (symbolic link, unreadable file) — so the HTML, the forensic PDF and the signed body name the same hash per row. `--pdf-out` renders the same Korean PDF as `--forensic-pdf-out` (pymupdf required; G2: every string is measured with the embedded CJK font and wrapped to its column — full paths, full 64-hex SHA-256 values, the complete summary/notice/signature lines; a row taller than the page continues on the next page; no text leaves the page margins `pdf_layout.PAGE_MARGINS`, the evidence-statement PDF likewise); without pymupdf both flags stop before the scan with a Korean message and exit 2 (B8) — the old Latin-1 English PDF no longer exists (the web/API `/api/report?format=pdf` answers HTTP 501 with the Korean JSON error ``PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다(설치: `pip install pymupdf`).`` instead). `--help` texts, usage lines and argument errors are Korean. `--redact-paths` (HTML, PDF, forensic PDF and the signed body the HTML embeds) shows each row's file name only and also hides the tool's install path (S3): `model_analysis.models[].profile` becomes the bare profile file name (`aide-runtime.json`) and any other value under the package folder, the models dir or `site-packages` keeps only its file name (`serialization.redact_install_paths`); `--json-out`, `--csv-out` and the table keep full paths.
11. Evaluation output includes AUROC and EER (threshold-swept equal error rate) alongside confusion counts, and `eval` reports per-split metrics when the dataset declares splits.
12. Security/privacy: `security` writes a local-only guardrail report. Network calls are not used by scan/eval/train, symlink following is opt-in, oversize files can be skipped, report paths can be redacted, and the web server binds to localhost unless `--allow-lan` is passed.
13. Release prep: `release` writes a readiness checklist and `.github/workflows/deepfake-lens.yml` runs compile, unit tests, registry smoke, and CLI help checks.

## Dataset And Benchmark Workflow

Folder labels are inferred from path segments:

- Positive: `ai`, `fake`, `synthetic`, `generated`, `edited`, `deepfake` — benchmark-style class-prefixed folders (`1_fake`, `2_synthetic`) are matched by dropping the numeric prefix
- Negative: `real`, `human`, `camera`, `authentic`, `original` (`0_real` likewise)
- Splits: `train`, `val`, `valid`, `validation`, `test`

Recommended sequence:

```sh
python -m deepfake_lens collect data/raw --out artifacts/collection-plan.json
python -m deepfake_lens dataset data/raw --manifest-out artifacts/manifest.json --fingerprints --audit-out artifacts/audit.json --split-out artifacts/split.json --robustness-out artifacts/robustness-plan.json
python -m deepfake_lens eval data/raw --pixel deep --json-out artifacts/eval.json --html-out artifacts/eval.html --false-positive-out artifacts/fp.jsonl --false-negative-out artifacts/fn.jsonl
python -m deepfake_lens calibrate data/raw --pixel deep --target-fpr 0.05 --out artifacts/calibration.json
python -m deepfake_lens train data/raw --pixel deep --target-fpr 0.05 --out artifacts/portable-threshold.model.json
python -m deepfake_lens benchmark data/raw --pixel-modes off,fast,deep --json-out artifacts/benchmark.json --md-out artifacts/benchmark.md
python -m deepfake_lens fusion data/raw --pixel deep --model-path artifacts/aide-profile.json --target-fpr 0.05 --out artifacts/fusion-profile.json
python -m deepfake_lens benchmark data/raw --pixel-modes off,fast,deep --fusion-profile artifacts/fusion-profile.json --json-out artifacts/benchmark-fused.json --md-out artifacts/benchmark-fused.md
python -m deepfake_lens perf data/raw --pixel deep --workers 4 --cache artifacts/scan-cache.json --hash-db artifacts/hash-db.json --out artifacts/perf.json
```

The robustness plan is a manifest of variants to generate. `scripts/build_robustness_variants.py`
generates them locally (requires numpy + Pillow; reuses the pure-numpy bilinear
resize from `experiments/sbi.py`): JPEG quality changes, resizing, crops,
light blur, screenshots, and social-media recompression. Variants land under
folders named by transform and `eval --robustness` reports per-transform
metrics against the same threshold:

```sh
python -m deepfake_lens dataset data/raw --robustness-out artifacts/robustness-plan.json
python scripts/build_robustness_variants.py --root data/raw --out data/robust
python -m deepfake_lens eval data/robust --pixel deep --robustness --json-out artifacts/robustness-eval.json
```

To pull a small real benchmark set, use `scripts/fetch_benchmark.py` with a
checksummed manifest (per-file URL + sha256 + `dest` under a label folder;
see the script docstring for the format). No dataset URL is baked in — the
Synthbuster Zenodo record cited in `docs/deepfake-lightweight-tool-research.md`
is one candidate source; check its terms before use. CI never fetches
external data — it runs the committed synthetic `fixtures/benchmark/` set via
`deepfake_lens/tests/test_benchmark_e2e.py`:

```sh
python scripts/fetch_benchmark.py --manifest bench.json --dest public_datasets/bench
python scripts/fetch_benchmark.py --manifest bench.json --verify-only   # re-check sha256 later
python -m deepfake_lens benchmark public_datasets/bench --pixel-modes off --json-out artifacts/bench.json
```

For command smoke checks without a real benchmark, use the tiny layout fixture:

```sh
python -m deepfake_lens dataset fixtures/deepfake-lens-sample --manifest-out /tmp/dfl-manifest.json --audit-out /tmp/dfl-audit.json --split-out /tmp/dfl-split.json
python -m deepfake_lens eval fixtures/deepfake-lens-sample --pixel off --json-out /tmp/dfl-eval.json
python -m deepfake_lens perf fixtures/deepfake-lens-sample --out /tmp/dfl-perf.json
```

For an end-to-end smoke of the robustness loop, use the synthetic dataset
builder (requires numpy + Pillow):

```sh
python scripts/build_synthetic_dataset.py --out /tmp/dfl-smoke-dataset --per-split 6
python scripts/build_robustness_variants.py --root /tmp/dfl-smoke-dataset --out /tmp/dfl-smoke-variants
python -m deepfake_lens eval /tmp/dfl-smoke-variants --pixel deep --robustness --json-out /tmp/dfl-smoke-robustness.json
```

## Reproducible corpora and the measurement gate

Phase 0 (WP-I): a performance number is evidence only when it names the
exact files it was measured on. Lay a corpus out as
`<label>/<generator>/<variant>/<file>` (label `real`/`synthetic`/`edited`;
`python scripts/build_corpus_template.py --out corpora/` creates the five
phase-1 track skeletons T-IMG, T-VID, T-AUD, T-DOC, T-TXT), then:

```sh
python -m deepfake_lens corpus build corpora/T-IMG --out artifacts/t-img.json --label-from-dir --corpus-id t-img-2026q4
python -m deepfake_lens corpus split --manifest artifacts/t-img.json --seed 20261009 --ratio 60/20/20
python -m deepfake_lens corpus verify --manifest artifacts/t-img.json
```

`manifest_sha256` covers every item (hash, label, split …); a profile that
is `supported` must record it in `measured_on` together with the manifest
file's `manifest_path` (the gate opens it and re-checks the hash, corpus id
and test-split counts), test-split n_pos/n_neg (>= 200 each) and an AUROC
95% CI lower bound >= 0.85 —
`python scripts/check_measurement_gate.py` enforces this in CI. Schema and
field list: `docs/deepfake-lens-json-contract.md`.

## Model Registry

`models` prints detector candidates and integration notes. It can also write a local runtime profile:

```sh
python -m deepfake_lens models --candidate aide-iclr-2025 --checkpoint models/aide.onnx --profile-out artifacts/aide-profile.json
python -m deepfake_lens scan samples --model-path artifacts/aide-profile.json
```

### Weight pins (phase 0, G9/G10)

No weight loads without a pin. Every runtime profile carries a `pin` object —
`{"sha256": "<64 hex>"}` for a local checkpoint (re-hashed on every load) or
`{"revision": "<40 hex commit>"}` for a hub model (passed as `revision=` to
every `from_pretrained`). An empty or malformed pin is recorded as a failed
check `미고정 프로필`, a mismatch as `무결성 불일치: …`; either makes the verdict
`판단 불가`. Fill a pin after provisioning the weights:

```sh
python -m deepfake_lens vendor-weights pin aasist                 # sha256 of models/aasist.pth
python -m deepfake_lens vendor-weights pin wav2vec-deepfake-audio  # current hub commit (needs huggingface_hub, online)
python -m deepfake_lens vendor-weights pin fakespot-detector --revision <40-hex commit>
```

In phase 0 every committed profile is also `supported: false` (measurement
gate not met), so the default engines below are recorded as skipped until
WP-I measures them. There is no default text engine. Removed profiles are
listed in `docs/MODEL-REJECTIONS.md`; `models/README.md`, `models/NOTICE.md`
and the profile-backed registry entries are generated by
`scripts/sync_model_docs.py` (CI runs it with `--check`).

### Default engine: AIDE (ICLR 2025)

`scan`, `eval`, and `fusion` auto-discover the committed runtime profile
`models/aide-runtime.json` when `--model-path` is not given (opt out with
`--no-default-engine`). The checkpoint itself is not committed — it is ~3.3 GB
under the AIDE research license. Fetch it once with checksum verification:

```sh
# The Model Zoo link is a Google Drive folder; copy the file ID of
# progan_train.pth and pass its direct link.
python scripts/fetch_aide.py --url "https://drive.google.com/uc?id=<FILE_ID>" --sha256 <hex>
```

Without the checkpoint the engine reports `model_analysis.available=false`
with the reason in `limitations` and scans continue heuristic-only — the JSON
keeps the external signal distinct (`model_analysis` per item,
`summary.external_model_active` for the batch). Scores remain prioritization
signals, not truth labels; measured AUROC lives in
`experiments/AIDE_EVALUATION.md`.

### Default audio engine: AASIST (Interspeech 2022)

`scan` and the `audio` command also auto-discover
`models/aasist-runtime.json` for audio files. AASIST is the standard audio
deepfake/anti-spoofing baseline (RawNet2-style sinc frontend +
spectro-temporal graph attention), reimplemented self-contained in
`scripts/run_aasist.py`. The checkpoint is the official in-repo
`AASIST.pth` (~1.3 MB, MIT-licensed, trained on ASVspoof2019-LA) and is not
committed:

```sh
# Direct GitHub raw link — no Google Drive step needed.
python scripts/fetch_aasist.py --sha256 <hex>
```

The runtime requires optional torch; without torch or the checkpoint audio
scans degrade to `model_analysis.available=false` and stay heuristic-only.
Higher `model_analysis.score` means more spoof suspicion — still a
prioritization signal, not a truth label.

The current registry is research-backed and intentionally separates benchmarks from reusable checkpoints:

- [NTIRE 2026 Robust AI-Generated Image Detection in the Wild](https://arxiv.org/abs/2604.11487): robustness benchmark and challenge report for transformed real-world images.
- [AIDE ICLR 2025](https://github.com/shilinyan99/AIDE): public code/checkpoints candidate for first pretrained image detector integration.
- [CLIDE WACV 2026](https://rbetser.github.io/CLIDE/): zero-shot CLIP-likelihood direction for unseen generators.
- [Dual-Path AI-Generated Image Detection](https://github.com/ljppp117/Dual-Path-AI-Generated-Image-Detection): patch/global detector candidate for local artifact heatmaps.
- DIFC-Net 2026: diffusion-intrinsic feature research candidate (previously cited MDPI link could not be verified by automated checks and was removed).
- [Out-of-box benchmark 2026](https://arxiv.org/abs/2604.11487): model selection reference covering many open-source detector variants.

## Video Workflow

Video support is intentionally frame-first:

```sh
python -m deepfake_lens video cases/videos --out artifacts/video-plan.json --frame-root artifacts/frames
python -m deepfake_lens video cases/videos --out artifacts/video-plan.json --frame-root artifacts/frames --extract
python -m deepfake_lens scan artifacts/frames --recursive --pixel deep --heatmaps
```

`--extract` requires local `ffmpeg`; planning does not.

## Neural Training Handoff

`train-neural-plan` writes a concrete training/export checklist for an external PyTorch experiment:

```sh
python -m deepfake_lens train-neural-plan data/raw --out artifacts/neural-plan.json --output-dir experiments/run-001 --architecture convnext_tiny --epochs 10
```

The plan records dataset counts, expected artifacts, ONNX export location, benchmark command, and guardrails. The optional experiment scripts provide the runnable local handoff:

```sh
python experiments/train_detector.py --manifest artifacts/manifest.json --arch convnext_tiny --epochs 10 --out experiments/run-001
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript --out experiments/run-001/convnext_tiny.onnx
python -m deepfake_lens scan samples --model-path experiments/run-001/runtime-profile.json --fusion-profile artifacts/fusion-profile.json
```

`train_detector.py` writes a TorchScript checkpoint, state dict, runtime profile, and training metadata when optional PyTorch dependencies are present. It intentionally stays outside the package dependency set.

## Frequency, Biometric And Provenance Screening (Phase 2)

- `pixel` ensemble `frequency_forensics` expert (requires numpy): real FFT/DCT measurements replacing the former shift-difference pseudo-spectral expert — radial power-spectrum slope (natural images decay ~1/f^2), robust spectral-spike detection above the radial average (upsampling/checkerboard artifacts), NPR-inspired neighboring-pixel interpolation consistency, and per-block DCT high-frequency energy share. Feature computation lives in `deepfake_lens/frequency.py`.
- `rppg <video>` (requires opencv; numpy for the pulse math): CHROM remote photoplethysmography — face-ROI RGB means are projected to chrominance signals and band-passed to 0.7-4 Hz, now over a 3x3 sub-ROI grid with a FakeCatcher-style phase-coherence field (`phase_coherence`, `roi_count`). A stable cardiac peak (SNR >= 8, 45-200 bpm) is evidence of a camera-captured live face; its absence raises a weak 25-weight suspicion signal only, and decorrelated per-ROI phases under a present global pulse raise a weak 20-weight signal. Compression, poor lighting, and motion can erase the pulse, so neither signal is a verdict on its own.
- `prnu <target> --reference a.png --reference b.png --reference c.png` (requires numpy): sensor-fingerprint (PRNU) provenance screening — residuals of 3+ same-device reference images are averaged into a fingerprint and the target's residual is correlated against it (zero-mean NCC after border-cropped Gaussian denoising). NCC >= 0.10 reads as same-device origin; a mismatch raises a weak suspicion signal. Re-compression, resizing, and rendering degrade the fingerprint, so a mismatch is a lead, not a verdict.
- C2PA manifest validation (`forensic` command; optional `provenance` extra = c2pa-python): when the official SDK is installed, `forensic` validates real C2PA manifests — validation state, signer identity, and per-claim success/failure codes — instead of guessing from byte strings. A state other than `valid` usually means the signer is not in the trust store (reported as "검증 미완료"), not proof of tampering — except a hash mismatch (`*.mismatch` codes), which means the content changed after signing and is reported as "C2PA 무결성 불일치: 매니페스트 해시 불일치 — 서명 이후 내용이 변경됨" (N6). Without the SDK, marker strings are reported as reference-level hints only. Byte scanning can never detect SynthID (a pixel-domain watermark); Google tool strings in metadata are attribution hints, and the analysis says so.

## SBI Training (experiments)

`python experiments/train_detector.py --manifest artifacts/manifest.json --sbi ...` trains with Self-Blended Images (Shiohara & Yamasaki, CVPR 2022): fakes are synthesized from the real-labeled images only, by blending two differently distorted copies (bilinear resampling, Gaussian blur, DCT-quantization JPEG simulation, color jitter) under a random soft mask. A detector trained this way learns blending/resampling artifacts rather than one generator's signature and needs no fake data. `experiments/sbi.py` is pure numpy; training itself still requires torch/torchvision.

## Limits

This is a screening tool, not a truth engine.

- Missing metadata means `출처 단서 없음`, not human-made.
- Pixel-level scores are local heuristic ensemble scores, not calibrated probabilities from a trained foundation model.
- Research-named experts are local implementations or adapters inspired by those approaches. They are not claimed to reproduce original paper weights or benchmark scores.
- The `train` command currently produces a portable threshold model, not a deep neural checkpoint. Neural checkpoint training is available only through optional `experiments/` scripts.
- Exact model/checkpoint attribution is only possible when metadata contains those details.
