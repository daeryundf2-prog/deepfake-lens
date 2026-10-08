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
`scan_folder`. Consequences:

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
  `api-serve` applies the same rule to every non-GET `/api/*` request and to
  the GETs that start, poll or cancel work or read caller-named files:
  `/api/scan`, `/api/scan-status`, `/api/scan-cancel`, `/api/analyze-file`,
  `/api/heatmap`, `/api/preview` (G8). Local tools/curl must send the header
  explicitly.
- Streaming jobs (`/api/check/stream`, `/api/scan/stream`) are capped at 32
  in flight; a 33rd gets **429** `{"detail": "too many jobs in flight; …"}`.
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
`{"detail": "..."}`. `/api/*` routes require auth as above.

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
| POST | `/api/check` | `file_path` **or** `text` | Unified check-all: core scan + every `models/` member that fits the modality + C2PA/forensic + text fingerprint probes in one `{mode, item, advanced?, forensic?}` payload |
| POST | `/api/check/stream` | same as `/api/check` | Server-Sent Events (`text/event-stream`): `job` → `progress` per stage → `result` (same payload as `/api/check`), or `error`/`cancelled` |
| POST | `/api/scan/stream` | `directory`, `recursive` (bool), `max_files` (≤5000, default 200) | SSE batch scan of a server-local directory: `job` → `progress` per file (`{stage, index, total, path, verdict_code}`) → `result` (`{mode: "scan", total, counts, items}`; `counts` = `manipulation_evidence`/`authenticity_evidence`/`undetermined`/`other`/`failed`), or `error`/`cancelled` |
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

`/api/scan/stream` reuses the same job/cancel machinery for directories:
an `enumerate` progress event reports the file count first, then one
`scan` progress event per file. Per-file failures are recorded in
`items` (and counted under `counts.failed`) rather than aborting the
scan. `items` entries are compact `{path, kind, status, band, score}`
summaries — use `/api/check` per file for full detail.

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
| POST `/api/report` | scan JSON body (≤ 64 MiB); `?format=html` (default) / `pdf` / `evidence` / `json` | rendered standalone HTML report, forensic PDF, evidence statement, or (`json`) the signed report body. Signed with `DEEPFAKE_LENS_REPORT_KEY` when set, otherwise marked `서명 없음`. Every item `sha256` is recomputed server-side from files under the read roots (posted hashes are ignored; unreadable → `null`). **403** when any item's `heatmap_path` is outside the read roots |
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
`max_file_bytes ≤ 1 GiB`, `heatmaps` only with `--pixel deep`.
The bundled GUI uses `async=1` + `/api/scan-status` polling so a long scan
never holds one request open; the synchronous form still works for tools.
The GUI's cancel button calls `/api/scan-cancel`, which sets a flag the
scanner checks between files — partial results still come back as `done`.

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
