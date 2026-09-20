# Deepfake Lens screening service

Two localhost servers ship in the package. They are screening tools for a
local analyst workstation — **not** multi-tenant services. Both read local
files on request, so exposure beyond loopback is an explicit, guarded choice.

| Server | Entry point | Stack | Auth |
|---|---|---|---|
| REST API | `deepfake-lens api-serve` (`deepfake_lens.api_server`) | FastAPI + uvicorn (optional deps) | `X-API-Token` header when `--token` is set; loopback Host allowlist otherwise |
| Web GUI | `deepfake-lens web` (`deepfake_lens.webapp`) | stdlib `http.server` | loopback bind + Host-header allowlist; `--allow-lan` to override |

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
  token on a 401 and stores it in `sessionStorage` for the session.
- **Drive-by/CSRF guard on tokenless binds**: when no token is configured,
  every `/api/*` request must carry a non-empty `X-Deepfake-Lens-Client`
  header (the bundled GUI sends `gui`; any non-empty value is accepted).
  Browsers cannot attach custom headers to cross-origin "simple" requests,
  so this forces a preflight the server never answers — unrelated web pages
  cannot trigger scans or write to the feedback log even on loopback.
  `api-serve` applies the same rule to non-GET `/api/*` requests (its GETs
  are side-effect-free). Local tools/curl must send the header explicitly.
- There is no rate limiting, TLS, or per-user isolation — put a reverse proxy
  in front if you need those, and keep `--token` mandatory outside loopback.

## REST API endpoints (`api-serve`, default `127.0.0.1:8765`)

All analyze endpoints take parameters as **query string** values and return a
JSON envelope `{"status": "success", "data": {...}}` or an HTTP error with
`{"detail": "..."}`. `/api/*` routes require auth as above.

| Method | Path | Params | `data` shape |
|---|---|---|---|
| GET | `/` | — | `{"message", "version"}` (no auth) |
| GET | `/api/health` | — | `{"status": "healthy"}` |
| POST | `/api/analyze/image` | `file_path` | `ClassificationResult.to_json()` — `score`/`band`/`signals`/`limitations`/`source_guess`; score is a prioritization signal, not a truth label |
| POST | `/api/analyze/audio` | `file_path` | `AudioAnalysis.to_json()` |
| POST | `/api/analyze/face` | `file_path` | `FaceAnalysis.to_json()` |
| POST | `/api/analyze/text` | `text` | `TextAdvancedAnalysis.to_json()` |
| POST | `/api/analyze/forensic` | `file_path` | `MetadataForensic.to_json()` (C2PA/provenance) |
| POST | `/api/classify` | `file_path` | `Classification.to_json()` — metadata/content classification; reads at most 64 MiB |
| POST | `/api/multimodal` | `image_score`, `text_score`, `audio_score`, `video_score` (ints, optional) | `MultimodalAnalysis.to_json()` — scalar fusion of supplied scores |
| POST | `/api/compare` | `file_path_a` + `file_path_b` | Two-file comparison — same-speaker distance (audio pairs) or same-author stylometry (text/document pairs) |
| POST | `/api/check` | `file_path` **or** `text` | Unified check-all: core scan + every `models/` member that fits the modality + C2PA/forensic + text fingerprint probes in one `{mode, item, advanced?, forensic?}` payload |
| POST | `/api/check/stream` | same as `/api/check` | Server-Sent Events (`text/event-stream`): `job` → `progress` per stage → `result` (same payload as `/api/check`), or `error`/`cancelled` |
| POST | `/api/jobs/{job_id}/cancel` | — | Sets the job's cancellation flag; takes effect at the next stage boundary (`{"status": "success", "cancelled": true}`, 404 for unknown/finished jobs) |
| GET | `/api/jobs/{job_id}` | — | `{"job_id", "done", "cancelled"}` for in-flight stream jobs; 404 once the job is reaped |

`file_path` is a path **on the server's filesystem** — there is no upload
endpoint. Analysis failures surface as HTTP 500 with the exception text.

`/api/check/stream` emits SSE frames `event: <name>\ndata: <json>\n\n`.
Stage events carry `{stage, index, total}` (`core`, `forensic`/`text-advanced`,
`watermark` when requested). Cancellation is cooperative — a running stage
finishes before the job stops, so latency-critical callers should also close
the HTTP connection.

## Web GUI endpoints (`web`, default `127.0.0.1:8765`)

JSON API under `/api/` (GET plus `POST /api/feedback`, `/api/report`,
`/api/analyze-upload`); anything else serves the GUI HTML.

| Path | Params | Response |
|---|---|---|
| `/api/scan` | `folder`, `pixel` (`off`/`fast`/`deep`), `recursive`, `max_files`, `max_file_bytes`, `dedupe`, `heatmaps`, `model_path`, `fusion_profile`, `async` | `scan_to_json` payload (`{"summary", "items"}`) or `{"error": "..."}`; with `async=1` returns `{"job_id", "status": "running"}` |
| `/api/scan-status` | `job` | `{"job_id", "status": "running"\|"done"\|"error"}` plus `result` once finished; jobs live in memory only and expire after 15 min (max 32 concurrent) |
| `/api/analyze-file` | `file` | `{"file", "classification", "forensic", "pixel_analysis"}` or `{"error"}` |
| `/api/heatmap` | `path`, `root` | PNG bytes; 403 unless `path` is a `.png` inside `root`, 404 if missing |
| `/api/stats` | — | `{"status", "version", "modules"}` |
| POST `/api/analyze-upload` | multipart file body (≤ `MAX_UPLOAD_BYTES`) | upload-analysis payload |
| POST `/api/compare` | multipart with two files | Two-file comparison — speaker distance (audio pair) or stylometry (text pair) |
| POST `/api/check` | JSON `{"text": "...", "watermark_secret": "...", "watermark_gamma": 0.25}` **or** one multipart file | Unified check-all: full scan + all `models/` engine members + forensic + text probes → `{mode, item, advanced?, forensic?}` |
| POST `/api/report` | scan JSON body (≤ 64 MiB) | rendered standalone HTML report |
| POST `/api/feedback` | feedback JSON body (≤ 1 MiB) | appends to `~/.deepfake-lens/feedback.jsonl` |

Limits (clamped, not optional): `max_files ≤ 2000`,
`max_file_bytes ≤ 1 GiB`, `heatmaps` only with `--pixel deep`.
The bundled GUI uses `async=1` + `/api/scan-status` polling so a long scan
never holds one request open; the synchronous form still works for tools.

## Honesty contract

- Every score is a **prioritization signal for review order, not a truth
  label**; `limitations` on each result are part of the contract.
- Optional analyzers degrade to `available: false`/errors when their extras
  are missing — a healthy service response, not a crash.
- The service has no persistence, queueing, or auth beyond the token; it is a
  thin documented shell over the same modules the CLI uses.
