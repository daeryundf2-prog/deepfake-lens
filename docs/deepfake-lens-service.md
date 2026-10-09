# Deepfake Lens screening service

Two localhost servers ship in the package. They are screening tools for a
local analyst workstation — **not** multi-tenant services. Both read local
files on request, so exposure beyond loopback is an explicit, guarded choice.

| Server | Entry point | Stack | Auth |
|---|---|---|---|
| REST API | `deepfake-lens api-serve` (`deepfake_lens.api_server`) | FastAPI + uvicorn (optional deps) | `X-API-Token` header when `--token` is set; loopback Host allowlist otherwise |
| Web GUI | `deepfake-lens web` (`deepfake_lens.webapp`) | stdlib `http.server` | loopback bind + Host-header allowlist; `--allow-lan` to override |

`api-serve` without `fastapi`/`uvicorn` installed prints a Korean install
hint (`pip install fastapi uvicorn`, or use the stdlib `web` server) to
stderr and **exits with status 2** — no traceback (G29).

## One analysis entry point (G7)

The CLI (`scan`, `batch`, `evidence-statement`), the web GUI (`/api/scan`,
uploads, `/api/check`) and the REST API (`/api/analyze/image`, `/api/check`,
the stream endpoints, `/api/scan`) all analyze through
`deepfake_lens.analysis_api`: an `AnalysisOptions` object
(`from_cli_args` / `from_query`) and the two functions `analyze_path` and
`scan_folder`. `scan_folder(folder, options)` returns `(summary, items)` as
the phase-0 spec states (N15 — it returned a third member, the threshold
profile); a front end that reports threshold provenance calls
`scan_folder_run(folder, options)`, which returns a `ScanRun(summary,
items, thresholds)` with the profile `load_thresholds(options)` resolved
(`scan_file` / `scan_file_run` likewise for one file). Consequences:

- **Same engine set.** The default model set is every `*-runtime.json` in
  the models dir (`--models-dir` / `web --models-dir` /
  `$DEEPFAKE_LENS_MODELS_DIR` / the packaged `models/`); `supported` (the
  measurement gate) and `pin` decide which members actually run, and each
  member's outcome is recorded in coverage as `model:<name>`.
- **Same thresholds.** `analysis_api.load_thresholds` resolves the threshold
  profile once per request — an explicit `--thresholds` file (CLI only),
  else `<models_dir>/thresholds.json`. Every scan-shaped response (scan,
  upload, check) reports the profile it used under `thresholds`
  (`source: "threshold_profile"`, `measured`, `in_sample`, `label`), so a
  GUI scan and a CLI scan of the same folder carry the same verdicts and the
  same threshold provenance (QA-OUT-4).
- **No request-chosen files.** `model_path` and `fusion_profile` query
  parameters accept only a bare file name that exists inside the server's
  models dir; a path (`/`, `\`, `..`, drive or absolute) or a missing name
  is **400** `{"error": "..."}` before any file is read.

## Bind and auth rules (enforced)

- Both servers default to `127.0.0.1` (`api-serve --host`, `web --host`).
- `api-serve --host <non-localhost>` **without `--token` is refused** (exit 2):
  the API reads arbitrary local paths on request, so a token is mandatory off
  loopback. With `--token`, every `/api/*` request needs the
  `X-API-Token: <token>` header (401 otherwise).
- Without a token, `/api/*` requests whose `Host` header is not
  `127.0.0.1`/`localhost`/`::1` get 403 — a DNS-rebinding guard. CORS is
  restricted to `http(s)://localhost[:port]` origins, `GET`/`POST` only.
- `web` refuses non-loopback binds unless `--allow-lan` is passed, and applies
  the same Host-header guard while bound to loopback. `--allow-lan` requires
  `--token`; every `/api/*` request then needs `X-Deepfake-Lens-Token: <token>`
  (401 otherwise). The GUI shell stays unauthenticated but prompts for the
  token on a 401 (async modal) and stores it in `sessionStorage` for the
  session.
- Both servers accept either token header — `X-Deepfake-Lens-Token` or the
  `X-API-Token` alias — so a single credential works across `web` and
  `api-serve` while the two surfaces converge.
- **Drive-by/CSRF guard on tokenless binds**: when no token is configured,
  every `/api/*` request must carry a non-empty `X-Deepfake-Lens-Client`
  header (the bundled GUI sends `gui`; any non-empty value is accepted).
  Browsers cannot attach custom headers to cross-origin "simple" requests,
  so this forces a preflight the server never answers — unrelated web pages
  cannot trigger scans or write to the feedback log even on loopback.
  `api-serve` applies the same rule to every `/api/*` request, GETs
  included (`/api/stats`, `/api/reviews`, …; G8, D16) — only the liveness
  probe `/api/health` is exempt. Local tools/curl must send the header
  explicitly.
- Streaming jobs (`/api/check/stream`, `/api/scan/stream`) are capped at 32
  in flight; a 33rd gets **429** `{"detail": "실행 중인 작업이 너무 많습니다 — …"}`.
  A client that disconnects mid-stream cancels its job at the next stage
  boundary. The web server's async scan jobs share the same cap of 32.
- Synchronous analysis never blocks the API's event loop: analysis handlers
  are plain `def` (FastAPI's thread pool) and upload/report/feedback bodies
  are processed with `run_in_threadpool` (G34).
- There is no rate limiting, TLS, or per-user isolation — put a reverse proxy
  in front if you need those, and keep `--token` mandatory outside loopback.

## REST API endpoints (`api-serve`, default `127.0.0.1:8765`)

All analyze endpoints take parameters as **query string** values and return a
JSON envelope `{"status": "success", "data": {...}}` or an HTTP error with
`{"detail": "..."}`. `/api/*` routes require auth as above. Every error text is
Korean (G7/G9, round 5): a missing or malformed query parameter is **422**
`{"detail": "요청 매개변수 오류 — <이름>(쿼리): 값이 필요합니다", "errors": [{"loc", "type"}]}`
(FastAPI's English `msg` is not returned); `/api/report` request errors are
**400** (N11; both servers — was 200) with
`{"error": "보고서에 넣을 검사 결과 항목(items 배열)이 필요합니다"}`,
`"<field> 값은 JSON 객체여야 합니다"`, `"검사 결과 항목 N번을 해석할 수 없습니다: …"`
(every posted row is checked against the scan-result item contract —
`contracts/deepfake-lens-scan-result-v2.schema.json` `$defs.item`, via
`report_items.check_report_item`: required fields, types, enums, the sha256
pattern; `{"items": [{"path": 3}]}` is refused, never rendered),
`"지원되지 않는 보고서 형식입니다: 「…」 (…)"`; a server-side
evidence-statement PDF failure is 500, a missing pymupdf 501;
a refused `/api/heatmap` / `/api/preview` path answers 403 with the body
`허용되지 않은 경로` (header `X-Deepfake-Lens-Error: forbidden`).
N5: statuses the frameworks answer on their own are Korean JSON too, on both
servers (`webapp_api.http_error_text`): an unknown route is 404
`찾을 수 없는 경로입니다`, a wrong method on api-serve 405
`이 경로에서 허용되지 않는 요청 메서드입니다: PUT`, an unsupported method on the
stdlib server 501 `지원하지 않는 요청 메서드입니다: PUT`, and an unhandled
exception 500 `서버 내부 오류가 발생했습니다 — 상세는 서버 로그를 확인하십시오`
(traceback in the server log only). api-serve puts the text in `detail`, the
stdlib server in `error`.

Every parameter that names a server-side path — `file_path`
(`/api/analyze/image|audio|face|forensic`, `/api/classify`, `/api/check`,
`/api/check/stream`), `directory` (`/api/scan/stream`) and
`file_path_a`/`file_path_b` (`/api/compare`) — must resolve inside a read
root (same rule as `/api/scan`, see "Read-root rule" below); otherwise the
response is **403** `{"error": "허용되지 않은 경로"}` with no file content
and, for the streaming endpoints, no job is started (G31).

| Method | Path | Params | `data` shape |
|---|---|---|---|
| GET | `/` | — | `{"message", "version"}` (no auth) |
| GET | `/api/health` | — | `{"status": "healthy"}` |
| POST | `/api/analyze/image` | `file_path` | **analysis_result** (D2): `kind: "analysis_result"`, `verdict_code` (`manipulation_evidence`/`authenticity_evidence`/`undetermined`), `verdict`, `grade`, `evidence`, `coverage`, `limitations`, `reference_signals`, `sha256` — the scan result from `analysis_api.analyze_path`; no band, no uncalibrated score |
| POST | `/api/analyze/audio` | `file_path` | analysis_result (the audio heuristics are reference signals; the audio profiles run under their pins/gates) |
| POST | `/api/analyze/face` | `file_path` | **layer_diagnostic**: `kind: "layer_diagnostic"`, `measured: false`, `raw_score`, `reference_band` (`reference`/`unavailable`), `reference_note`, the fixed notice and the face layer's raw numbers under `diagnostic` |
| POST | `/api/analyze/text` | `text` (≤256 KB) | analysis_result (text is reference grade → `undetermined`) + `layer_diagnostics.text_statistics` |
| POST | `/api/analyze/forensic` | `file_path` | analysis_result (an A1111 PNG is `manipulation_evidence`, as in `scan`) + `layer_diagnostics.provenance_metadata` (C2PA/provenance scan) |
| POST | `/api/classify` | `file_path` | analysis_result + `tool_candidates` (layer diagnostic of AI-tool marker matches; reads at most 64 MiB) |
| POST | `/api/multimodal` | `image_score`, `text_score`, `audio_score`, `video_score` (ints, optional) | layer_diagnostic — the supplied scores are unmeasured; their combination is never a band |
| POST | `/api/compare` | `file_path_a` + `file_path_b` | layer_diagnostic — same-speaker distance (audio pairs) or same-author stylometry (text/document pairs) under `diagnostic`; no same/different band |
| POST | `/api/check` | `file_path` **or** `text` | Unified check-all: core scan + every `models/` member that fits the modality + C2PA/forensic + text fingerprint probes in one `{mode, item, advanced?, forensic?}` payload. An archive `file_path` is expanded exactly as the folder scan expands it: `{mode: "files", summary, items}` with the member rows and the container row (refused members as `archive_member` coverage) |
| POST | `/api/check/stream` | same as `/api/check` | Server-Sent Events (`text/event-stream`): `job` → `progress` per stage → `result` (same payload as `/api/check`), or `error`/`cancelled` |
| POST | `/api/scan/stream` | `directory`, `recursive` (bool), `max_files` (≤5000, default 200) | SSE batch scan of a server-local directory through `analysis_api.scan_folder_run` (the CLI's scan): `job` → `progress` per row (`{stage: "scan", index, total, path, status, verdict_code}`) → `result` (the `/api/scan` payload — `schema_version`, `summary`, `coverage`, `thresholds`, `items` — plus `mode: "scan"`, `directory`, `total`, `capped`, `counts`; `counts` = `manipulation_evidence`/`authenticity_evidence`/`undetermined` from the summary, `other` = skipped + duplicate rows, `failed` = `unsupported_or_failed`), or `error`/`cancelled` (`{job_id, total, done, processed}` — `total` = rows the scan planned before the first file, `done` = rows reported before the cancel, `processed` = `done` for older clients; N8) |
| POST | `/api/jobs/{job_id}/cancel` | — | Sets the job's cancellation flag; takes effect at the next stage boundary (`{"status": "success", "cancelled": true}`, 404 for unknown/finished jobs) |
| GET | `/api/jobs/{job_id}` | — | `{"job_id", "done", "cancelled"}` for in-flight stream jobs; 404 once the job is reaped |
| GET | `/api/scan`, `/api/scan-status`, `/api/scan-cancel`, `/api/analyze-file`, `/api/heatmap`, `/api/preview` | as in the web GUI table below | Same payload functions as `web` (identical JSON); `/api/heatmap` is `image/png` (errors `text/plain`), `/api/preview` uses the file's media type; both send `X-Content-Type-Options: nosniff` |

`file_path` is a path **on the server's filesystem** — there is no upload
endpoint. Analysis failures surface as HTTP 500 with the exception text.

`/api/check/stream` emits SSE frames `event: <name>\ndata: <json>\n\n`.
Stage events carry `{stage, index, total}` (`core`, `forensic`/`text-advanced`,
`watermark` when requested). Cancellation is cooperative — a running stage
finishes before the job stops, so latency-critical callers should also close
the HTTP connection.

`/api/scan/stream` reuses the same job/cancel machinery for directories.
It runs `analysis_api.scan_folder_run` — never its own folder walk — so the
rows are the CLI's rows (R1): archives are expanded into member rows plus a
container row with every refused member (traversal, absolute path, link,
bomb/budget) recorded as `archive_member` coverage, symlinked files appear
as `skipped` rows, and every row carries its `sha256`. An `enumerate`
progress event comes first, then one `scan` progress event per finished row
(`index` = rows done, `total` = rows known so far). Per-file failures are
rows, never an aborted scan. `items` are the full `/api/scan` items; each
also repeats `verdict_code`, `grade` and `probability` from its `result` at
the top level for clients of the earlier compact rows. `/api/check` returns
`forensic` and `advanced` as layer diagnostics (raw numbers + notice, no band).

## Web GUI endpoints (`web`, default `127.0.0.1:8765`)

JSON API under `/api/` (GET plus `POST /api/feedback`, `/api/report`,
`/api/analyze-upload`); anything else serves the GUI HTML.

| Path | Params | Response |
|---|---|---|
| `/api/scan` | `folder`, `pixel` (`off`/`fast`/`deep`), `recursive`, `max_files`, `max_file_bytes`, `dedupe`, `heatmaps`, `deep_signals`, `no_default_engine`, `model_path` / `fusion_profile` (bare file name in the models dir), `async` | the same `scan_to_json` payload the CLI prints (`{"schema_version", "summary", "coverage", "thresholds", "items"}`) or `{"error": "..."}`; with `async=1` returns `{"job_id", "status": "running"}`. **400** `{"error": ...}` for an invalid option (non-integer limit, unknown pixel mode, `model_path`/`fusion_profile` not a file name inside the models dir). **403** `{"error": "허용되지 않은 경로"}` when `folder` is outside the read roots (see below) |
| `/api/scan-status` | `job` | `{"job_id", "status": "running"\|"done"\|"error"}` plus `result` once finished; jobs live in memory only and expire after 15 min (max 32 concurrent) |
| `/api/scan-cancel` | `job` | Sets the job's cancel flag; the scan stops between items and returns partial results as `done`. `{"cancelled": true}` while running, `false` once finished |
| `/api/analyze-file` | `file` | analysis_result (D3: the scan result — verdict_code, evidence, coverage; the scan's own photo-gated pixel layer only) + `layer_diagnostics.provenance_metadata` / `.tool_candidates`, or `{"error"}` (+`detail` for unexpected failures). No ungated pixel pre-screen and no band. **403** `{"error": "허용되지 않은 경로"}` when `file` is outside the read roots |
| `/api/heatmap` | `path`, `root` | PNG bytes; 403 unless `path` is a `.png` under a **server-registered** read root (see below), 404 if missing |
| `/api/preview` | `path`, `root` | media bytes with `nosniff`; same registered-root rule, media extensions only, ≤128 MiB |
| `/api/stats` | — | `{"status", "version", "modules"}` |
| `/api/review-marks` | — | `{"status", "marks": {path: {star, note, ts}}}` — durable examiner marks store (default `~/.deepfake_lens/review-marks.json`, override `DEEPFAKE_LENS_REVIEW_STORE`) |
| POST `/api/review-marks` | JSON `{"marks": {path: {star, note, ts}}}` (≤ 8 MiB) | merges per-key marks; an entry with neither `star` nor `note` deletes the key |
| POST `/api/analyze-upload` | multipart file body (≤ `MAX_UPLOAD_BYTES`) | upload-analysis payload |
| POST `/api/compare` | multipart with two files | Two-file comparison — speaker distance (audio pair) or stylometry (text pair) |
| POST `/api/check` | JSON `{"text": "...", "watermark_secret": "...", "watermark_gamma": 0.25}` **or** one multipart file | Unified check-all: full scan + all `models/` engine members + forensic + text probes → `{mode, item, advanced?, forensic?}` |
| POST `/api/report` | scan JSON body (≤ 64 MiB) — `items`, optional `options` (the `/api/scan` analysis keys); `?format=html` (default) / `pdf` / `evidence` / `json` | rendered standalone HTML report, forensic PDF, evidence statement, or (`json`) the signed report body. X2: only server-derived results are signed — every posted row's file is re-analyzed on the server inside the read roots (`analysis_api.scan_file_run`, request `options`, server models dir and thresholds) and the row is replaced by the server's (verdict, evidence, coverage, `sha256`, provenance); a row that cannot be re-analyzed is left out of the signed body (`excluded_items`) and rendered under `서명 제외(클라이언트 제공 결과)` (JSON contract, "Signed reports"). The format is `?format=`, else the JSON body's `"format"`, else `html` (N9: both servers set `Content-Type` from that one value — `application/pdf` for `pdf`/`evidence`). Signed with `DEEPFAKE_LENS_REPORT_KEY` when set, otherwise marked `서명 없음`. Every item `sha256` is recomputed server-side from files under the read roots (posted hashes are ignored; unreadable → `null`): a symbolic-link row, or a path through a linked folder, stays `null` / `해시 불가(심볼릭 링크 — 링크를 따라가지 않음)` (N2 — the link is never followed, as in the CLI); an archive member row `a.zip::x` is hashed from the container re-extracted under the roots with the scanner's own extractor, so it carries the same digest as the scan row (N10). **400** for a malformed request (N11), **403** when any item's `heatmap_path` is outside the read roots |
| POST `/api/feedback` | feedback JSON body (≤ 1 MiB) | appends to `~/.deepfake-lens/feedback.jsonl` |

Read-root rule (G31) for `/api/scan`, `/api/analyze-file`,
`/api/heatmap`, `/api/preview`, `/api/report` (evidence hashing and
heatmap embedding) and every `api-serve` endpoint that takes a
`file_path`/`directory`: only the operator registers read roots, at server
start — `web --folder <dir>` and each repeatable `--allow-root <dir>`
(`api-serve --allow-root` likewise; max 64, oldest evicted first). No
request can add one; `/api/scan?folder=` no longer registers anything.
With no root registered, the server's own default folder (`--folder`, or
the directory it was started in) is the only root — never the whole disk.
A path outside every root gets **403** `{"error": "허용되지 않은 경로"}`
(heatmap/preview keep their plain-text `forbidden` body) and no file bytes.
When a `root` argument is supplied it must also contain the file, so a
forged `root=C:\` can never widen the read scope.

Limits (clamped, not optional): `max_files ≤ 2000`,
`max_file_bytes ≤ 1 GiB`, `heatmaps` only with `--pixel deep`. A value
below 1 is refused with 400 (Y8), never raised to 1.
The bundled GUI uses `async=1` + `/api/scan-status` polling so a long scan
never holds one request open; the synchronous form still works for tools.
The GUI's cancel button calls `/api/scan-cancel`, which sets a flag the
scanner checks between files — partial results still come back as `done`.

## Error status codes — every `/api/*` endpoint (X4)

No `/api/*` endpoint answers an error with 200: every refusal carries an HTTP
error status and a Korean text. The web GUI server (`web`) and api-serve share
the payload functions (`webapp_api`), which return their errors as
`webapp_api.ApiError` (a `{"error": …}` dict with its status); both servers
send that status (api-serve: `api_server._api_json`). Body key: `error` on the
web server and for the shared GUI endpoints on api-serve; api-serve's own
endpoints (`/api/analyze/*`, `/api/classify`, `/api/check`, `/api/compare`,
`/api/jobs/*`, review routes) use FastAPI's `detail`.

| Endpoint (server) | Condition | Status | Korean text (`error` / `detail`) |
|---|---|---|---|
| any `/api/*` (both) | missing `X-Deepfake-Lens-Client` / bad token | 401 | `… 헤더가 없습니다 …` / `인증 실패: …` |
| any `/api/*` (both) | unknown route | 404 | `찾을 수 없는 경로입니다` |
| any `/api/*` (web / api) | method not supported / not allowed | 501 / 405 | `지원하지 않는 요청 메서드입니다: …` / `이 경로에서 허용되지 않는 요청 메서드입니다: …` |
| any `/api/*` (both) | unhandled exception | 500 | `서버 내부 오류가 발생했습니다 — 상세는 서버 로그를 확인하십시오` |
| GET `/api/scan` (both) | invalid option (`pixel`, non-integer limit, `model_path`/`fusion_profile` not a name in the models dir) | 400 | e.g. `max_files는 정수여야 합니다` |
| GET `/api/scan` (both), POST `/api/scan/stream` (api) | `max_files` or `max_file_bytes` below 1 (Y8 — was clamped to 1) | 400 | `max_files는 1 이상이어야 합니다` / `max_file_bytes는 1 이상이어야 합니다` |
| GET `/api/scan` (both) | folder missing / a file / unreadable | 400 | `폴더를 찾을 수 없습니다: …` (scan's S4 texts) |
| GET `/api/scan` (both) | folder outside the read roots | 403 | `허용되지 않은 경로` |
| GET `/api/scan?async=1` (both) | 32 jobs already registered | 400 | `실행 중인 검사 작업이 너무 많습니다 — …` |
| GET `/api/scan-status`, `/api/scan-cancel` (both) | no `job` | 400 | `job 매개변수가 필요합니다` |
| GET `/api/scan-status`, `/api/scan-cancel` (both) | unknown or expired job | 404 | `알 수 없거나 만료된 작업입니다` |
| GET `/api/analyze-file` (both) | no `file` | 400 | `file 매개변수(파일 경로)가 필요합니다` |
| GET `/api/analyze-file` (both) | file missing | 404 | `파일을 찾을 수 없습니다: …` |
| GET `/api/analyze-file` (both) | a folder | 400 | `파일이 아니라 폴더입니다: … (폴더는 scan을 사용)` |
| GET `/api/analyze-file` (both) | outside the read roots | 403 | `허용되지 않은 경로` |
| GET `/api/analyze-file` (both) | analysis raised | 500 | `파일 분석 중 오류가 발생했습니다` (+ `detail`) |
| GET `/api/heatmap`, `/api/preview` (both) | outside the roots / not found / not media | 403 / 404 / 400 | plain text + `X-Deepfake-Lens-Error` header |
| POST `/api/analyze-upload` (both) | not multipart, no file part, empty or incomplete body | 400 | `multipart/form-data 업로드가 필요합니다`, `업로드된 파일이 없습니다`, … |
| POST `/api/analyze-upload` (both) | body over `MAX_UPLOAD_BYTES` | 413 | `업로드 크기가 상한(…바이트)을 초과합니다` |
| POST `/api/analyze-upload` (web) | analysis raised | 500 | `업로드 분석 중 오류가 발생했습니다` (+ `detail`) |
| POST `/api/check` (web) | invalid JSON / not an object / text < 8 or > 256 KB / not multipart / no file | 400 | `JSON 본문을 해석할 수 없습니다`, `분석할 텍스트가 너무 짧습니다 (8자 이상).`, … |
| POST `/api/check` (web) | body over `MAX_UPLOAD_BYTES` | 413 | `요청 본문이 상한(…바이트)을 초과합니다` |
| POST `/api/check` (api) | neither `file_path` nor `text`; text > 256 KB | 400 | `file_path 또는 text가 필요합니다` |
| POST `/api/compare` (web) | not multipart / fewer than 2 files / mixed pair | 400 | `비교할 파일 2개가 필요합니다`, … |
| POST `/api/compare` (web) | comparison raised | 500 | `비교 분석 중 오류가 발생했습니다` (+ `detail`) |
| POST `/api/compare` (api) | comparison error / raised | 400 / 500 | the comparison's Korean error |
| POST `/api/report` (both) | malformed body or row, bad `format`/`options` | 400 | see "REST API endpoints" (N11, X2) |
| POST `/api/report` (both) | a `heatmap_path` outside the roots | 403 | `허용되지 않은 경로` |
| POST `/api/report` (both) | evidence-statement PDF failed / pymupdf missing | 500 / 501 | `증거설명서 PDF 생성 실패: …` / `PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다…` |
| POST `/api/feedback` (both) | invalid JSON, not an object, unknown `expected_label`, no `path` | 400 | `JSON 본문을 해석할 수 없습니다`, `expected_label은 인식 가능한 라벨이어야 합니다 …`, `path가 필요합니다` |
| GET/POST `/api/review` (web), review routes (api) | no `path`/`artifact_id`, invalid JSON | 400 | `path 또는 artifact_id가 필요합니다`, `JSON을 해석할 수 없습니다` |
| POST `/api/analyze/*`, `/api/classify`, `/api/check` (api) | analysis raised | 500 | the failure reason |
| `/api/jobs/{id}`, `/api/jobs/{id}/cancel` (api) | unknown or finished job | 404 | `알 수 없거나 이미 끝난 작업입니다` |
| POST `/api/check/stream`, `/api/scan/stream` (api) | 32 stream jobs running | 429 | `실행 중인 작업이 너무 많습니다 …` |
| any query parameter (api) | missing / malformed | 422 | `요청 매개변수 오류 — …` |

Tests: `test_servers.ApiErrorStatusTest` sends every web-server row above (and
the shared-endpoint rows to api-serve) and checks the status and the Korean
text.

## Honesty contract

- Every score is a **prioritization signal for review order, not a truth
  label**; `limitations` on each result are part of the contract.
- Optional analyzers degrade to `available: false`/errors when their extras
  are missing — a healthy service response, not a crash.
- Archives (scan and upload) are expanded under one aggregate budget per
  top-level archive — 2 GiB written, 5000 members, 50 nested archives,
  nesting depth 2 — and symlink/hardlink/device members (zip, tar, 7z, rar)
  are never materialized (G34). A container that hits a budget is
  `판단 불가` with the reason in `limitations`.
- The service has no persistence, queueing, or auth beyond the token; it is a
  thin documented shell over the same modules the CLI uses.
