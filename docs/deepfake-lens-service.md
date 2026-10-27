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
| GET | `/gui` | — | the web GUI shell (`text/html`, the `web` server's GUI; CSP `GUI_CSP`, `X-Content-Type-Options: nosniff`; no auth — the GUI's own `/api/*` calls are authenticated) |
| GET | `/gui.css`, `/gui.js` | — | the GUI's stylesheet and script (`nosniff`) |
| GET | `/gui-shell.css` | — | the app shell stylesheet (`nosniff`) |
| GET | `/gui-settings.css` | — | the settings and confirm dialog stylesheet (`nosniff`) |
| GET | `/gui-onboarding.css` | — | the first-run welcome stylesheet (`nosniff`) |
| GET | `/gui-shell.js` | — | the app shell script (`nosniff`) |
| GET | `/gui-history.js` | — | the sidebar history script (`nosniff`) |
| GET | `/gui-settings.js` | — | the settings script (`nosniff`) |
| GET | `/gui-onboarding.js` | — | the first-run welcome script (`nosniff`) |
| GET | `/api/health` | — | `{"status": "healthy"}` |
| POST | `/api/analyze/image` | `file_path` | **analysis_result** (D2): `kind: "analysis_result"`, `verdict_code` (`manipulation_evidence`/`authenticity_evidence`/`undetermined`), `verdict`, `grade`, `evidence`, `coverage`, `limitations`, `reference_signals`, `sha256` — the scan result from `analysis_api.analyze_path`; no band, no uncalibrated score |
| POST | `/api/analyze/audio` | `file_path` | analysis_result (the audio heuristics are reference signals; the audio profiles run under their pins/gates) |
| POST | `/api/analyze/face` | `file_path` | **layer_diagnostic**: `kind: "layer_diagnostic"`, `measured: false`, `raw_score`, `reference_band` (`reference`/`unavailable`), `reference_note`, the fixed notice and the face layer's raw numbers under `diagnostic` |
| POST | `/api/analyze/text` | `text` (≤256 KB) | analysis_result (text is reference grade → `undetermined`) + `layer_diagnostics.text_statistics` |
| POST | `/api/analyze/forensic` | `file_path` | analysis_result (an A1111 PNG is `manipulation_evidence`, as in `scan`) + `layer_diagnostics.provenance_metadata` (C2PA/provenance scan) |
| POST | `/api/classify` | `file_path` | analysis_result + `tool_candidates` (layer diagnostic of AI-tool marker matches; reads at most 64 MiB; R24-5: a file it cannot read is that layer `failed` — `diagnostic.status` `failed` with the reason — not a 500) |
| POST | `/api/multimodal` | `image_score`, `text_score`, `audio_score`, `video_score` (ints, optional) | layer_diagnostic — the supplied scores are unmeasured; their combination is never a band |
| POST | `/api/compare` | `file_path_a` + `file_path_b` | layer_diagnostic — same-speaker distance (audio pairs) or same-author stylometry (text/document pairs) under `diagnostic`; no same/different band |
| POST | `/api/check` | `file_path` **or** `text` | Unified check-all: core scan + every `models/` member that fits the modality + C2PA/forensic + text fingerprint probes in one `{mode, item, advanced?, forensic?}` payload. An archive `file_path` is expanded exactly as the folder scan expands it: `{mode: "files", summary, items}` with the member rows and the container row (refused members as `archive_member` coverage) |
| POST | `/api/check/stream` | same as `/api/check` | Server-Sent Events (`text/event-stream`): `job` → `progress` per stage → `result` (same payload as `/api/check`), or `error`/`cancelled` |
| POST | `/api/scan/stream` | `directory`, `recursive` (true/false — R24-8: the R23-13 values, else 400 E5), `max_files` (≤5000, default 200) | SSE batch scan of a server-local directory through `analysis_api.scan_folder_run` (the CLI's scan): `job` → `progress` per row (`{stage: "scan", index, total, path, status, verdict_code}`) → `result` (the `/api/scan` payload — `schema_version`, `summary`, `coverage`, `thresholds`, `items` — plus `mode: "scan"`, `directory`, `total`, `capped`, `counts`; `counts` = `manipulation_evidence`/`authenticity_evidence`/`undetermined` from the summary, `other` = skipped + duplicate rows, `failed` = `unsupported_or_failed`), or `error`/`cancelled` (`{job_id, total, done, processed, files_cancelled, partial}` — `total` = rows the scan planned before the first file, `done` = rows reported before the cancel, `processed` = `done` for older clients; N8; R18-3: `files_cancelled` = files never started, `partial` = the `/api/scan` payload of the rows done, its `summary.cancelled` true when files were left out). R26-5 (round 26): the finished scan — `cancelled` (`summary.cancelled`) or `done` — is kept as this server's job record under the stream's `job_id` (in memory, 15 min like `/api/scan?async=1` jobs; at most 32 stream records, oldest dropped first, never counted against the async limit), so `POST /api/report` takes the `partial` (or the `result` rows) with `scan_job` = that `job_id`: a container the cancel left `cancelled` is excluded as cancelled and `files_cancelled` is the server's. Without `scan_job` such a post is 400 (E27) — it was 400 even with the stream's `job_id`, which the report could not find |
| POST | `/api/jobs/{job_id}/cancel` | — | Sets the job's cancellation flag; takes effect at the next stage boundary (`{"status": "success", "cancelled": true}`, 404 for unknown/finished jobs) |
| GET | `/api/jobs/{job_id}` | — | `{"job_id", "done", "cancelled"}` for in-flight stream jobs; 404 once the job is reaped |
| GET | `/api/scan`, `/api/scan-status`, `/api/scan-cancel`, `/api/analyze-file`, `/api/heatmap`, `/api/preview`, `/api/stats`, `/api/reviews`, `/api/review` | as in the web GUI table below | Same payload functions as `web` (identical JSON); `/api/heatmap` is `image/png` (errors `text/plain`), `/api/preview` uses the file's media type; both send `X-Content-Type-Options: nosniff` |
| POST | `/api/analyze-upload`, `/api/report`, `/api/feedback`, `/api/review` | as in the web GUI table below | Same payload functions as `web`; `/api/analyze-upload` is api-serve's upload endpoint (multipart, ≤ `MAX_UPLOAD_BYTES`) |
| GET | `/api/history` | — | Same payload function as `web` (`_history_payload`): `{"items", "retention_days", "store", "note"}`; each item is described under "History item" below |
| POST | `/api/history-delete` | JSON `{"job"}` | Same payload function as `web` (`_history_delete_payload`): `{"status": "success", "job", "deleted"}`; 400 for a missing or invalid `job`, 404 unknown job, 409 while the job is running |
| GET | `/api/job-case` | `job` (query) | Same payload function as `web` (`_job_case_get_payload`): `{"status", "job", "case"}`; 400 / 404 |
| POST | `/api/job-case` | JSON `{"job", "case": {…}}` | Same payload function as `web` (`_job_case_post_payload`): `{"status", "job", "case"}`; 400 for an unknown `case` key or a non-object body or `case`, 404 unknown job |
| GET | `/api/artifacts/{artifact_id}/review` | — | `{"status", "artifact_id", "review"}` — the examiner review stored for that artifact (R22-3: `reviews.json` in the per-user application-data folder — macOS `~/Library/Application Support/deepfake-lens`, Windows `%APPDATA%\deepfake-lens`, else `$XDG_DATA_HOME/deepfake-lens` (absolute) or `~/.local/share/deepfake-lens`; override `DEEPFAKE_LENS_REVIEWS` (absolute); never inside a folder the server examines (E49); records of the old `~/.deepfake-lens/reviews.json` are copied once into the default location — R23-8: never into an override file) |
| PUT | `/api/artifacts/{artifact_id}/review` | JSON object body | saves the review; `{"status", "artifact_id", "review"}` (400 for invalid JSON or a non-object body, E33) |

R10-8: these are all of api-serve's routes — FastAPI's automatic `/docs`,
`/docs/oauth2-redirect`, `/redoc` and `/openapi.json` (an English Swagger/ReDoc
UI outside this table) are disabled and answer 404 `찾을 수 없는 경로입니다`
(`test_servers.DocumentedEndpointsExistTest` checks every route, not only
`/api/*`).

`file_path` is a path **on the server's filesystem**; bytes the client holds
are analyzed through the upload endpoint `POST /api/analyze-upload`
(multipart — R9-8: this paragraph used to say there was none). Upload rows
are marked `"source": "upload"` and are never signed by `/api/report` (P6).
R29 (round 29): every upload entry point — `/api/analyze-upload` on both
servers, the web GUI's `/api/check` file upload — gives, for each file part
(a part with a file name, a 0-byte one included), the rows a folder scan
gives for the same bytes under the client's base name (the last `/`- or
`\`-separated component): an archive (decided on that name) its member rows
and container row, any other file its one row. A name this system cannot
create (too long, NUL, not encodable) is analyzed under `upload<suffix>`
(`upload.zip`, `upload.tar.gz`) with the file-name check `skipped`; an
analysis that raises is one `failed` row. Up to `MAX_UPLOAD_FILES` (20)
*files* are analyzed per request; each further file is a `skipped` row
`파일 수 상한(20개) 초과 — …`. `/api/check` takes exactly one file and
`/api/compare` exactly two (else 400 with the count received).
R30 (round 30): every upload path parses the body with one multipart/form-data
parser (`deepfake_lens/form_data.py`; it was the e-mail parser). A part's bytes
are taken verbatim whatever its `Content-Type` (a browser sends `.eml` as
`message/rfc822` and `.mht` as `multipart/related` — those files vanished or
were analyzed re-serialized, R30-1/R30-2) or `Content-Transfer-Encoding` says,
so a row's `sha256` is that of the bytes sent. A file name is `filename*`
(RFC 5987) when present and well formed, else `filename` read as browsers and
curl write it (the HTML Standard's form-data encoding): a quoted value ends
at the first `"`, a backslash is an ordinary character (it was read as an
escape and removed, R30-3), and only `%22`, `%0D`, `%0A` are decoded (to `"`,
CR, LF — so a name that really contains `%22` reads as `"`); the name's bytes
are UTF-8, else CP949, else kept as surrogate escapes (R12-10).
R31 (round 31): a line may end in CRLF (RFC 2046, what browsers, curl,
requests and undici send) or in a bare LF or CR, as the e-mail parser
accepted — an LF-only body, a mixed one or a preamble ended by LF was refused
as 「boundary와 다릅니다」/「잘렸습니다」/「업로드된 파일이 없습니다」 (R31-1);
the line end before a delimiter is the delimiter's, the bytes of a part are
otherwise verbatim (its own LF bytes included), and a bare LF ends an unquoted
header value. The media type is compared case-insensitively
(`Multipart/Form-Data`, R31-2; `/api/check`'s `application/json` too) and so
is the `boundary` parameter name. An empty `filename*` falls back to
`filename` (R31-3), and a `filename*` charset Python cannot decode with
(e.g. `undefined`, which was a 500) is skipped like an unknown one (R31-4).
A request has at most `MAX_UPLOAD_PARTS` (1000) parts — files, form fields and
nameless parts alike, counted while parsing; one more is a 400
`업로드 본문의 파트(파일·폼 필드)가 상한(1000개)보다 많습니다 — …` before any row
is made (E18, E21, E24; 280,000 tiny parts made 279,620 rows).
A file whose analysis raises is a `failed` row (`<Exception>: …`, R29-2), not
an HTTP error; only a failure outside a file's analysis (the request
handling itself) is a 500 `업로드 분석 중 오류가 발생했습니다` with a `detail`
(E20).

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
`/api/analyze-upload`, `/api/check`, `/api/compare`, `/api/review`); `/` and
`/gui` serve the GUI HTML and `/gui.css`, `/gui.js` its assets (the same GUI
paths as `api-serve`). R11-12: any other path is **404**
`{"error": "찾을 수 없는 경로입니다"}` (it used to serve the GUI HTML — `/docs`,
`/favicon.ico`, anything). R12-11: a trailing slash (`/gui/`, `/gui.js/`,
`/api/scan/`, …) is not a route on either server — both answer 404 (`api-serve`
used to redirect `/gui/` to `/gui`; its `redirect_slashes` is off). R9-8: every path in this table and in the REST
table above is a route of its server, and every route is in its table
(`test_servers.DocumentedEndpointsExistTest`).
The GUI saves its examiner marks through `POST /api/review` (browser
`localStorage` only as an offline fallback) — there is no `/api/review-marks`
(it was documented here but never existed). R18-8 (round 18): its `artifact_id`
is `v2|root:<scan_root>|sha256:<content hash>` (a row without a hash:
`v2|root:<scan_root>|path:<row path>`; uploads: a per-page `upload:…` scope
instead of the root), so a mark belongs to one file content in one scanned
folder — it used to be the bare row path, and a same-named file of another case
showed the earlier case's memo and "검토됨". Older bare-path entries stay in the
stores and are never attached to a row.

The bundled GUI (`gui.html`, `gui.js`, `gui.css`; no network request outside
the server — fonts are local stacks) has four screens on one page:
`#/scan` (case fields without defaults, the folder box prefilled from
`/api/stats` `default_folder`, options, uploads, progress from
`/api/scan-status`, cancel), `#/ledger` (the conclusion headline, the
verdict strip, the file ledger and the evidence panel — skipped checks,
reference signals, previews and technical fields behind disclosures, failed
checks always visible), `#/report` (export checklist — 사건번호 and 사건명 are
required for the evidence statement —, the verdict groups to include, PDF /
HTML / signed JSON through `POST /api/report`, a preview) and `#/tools`
(`/api/check`, `/api/compare`). A 409 (R19-13) from an export lists the changed files with the ledger's and the server's verdict, marks those rows "검사 뒤 바뀜 — 다시 검사" and offers 다시 검사; the preview says the export is refused when files changed.

| Path | Params | Response |
|---|---|---|
| `/` | — | the GUI HTML (CSP header, no inline script/style) |
| `/gui` | — | the GUI HTML (same as `/`) |
| `/gui.css` | — | GUI stylesheet (`text/css`) |
| `/gui.js` | — | GUI script (`text/javascript`) |
| `/gui-shell.css` | — | app shell stylesheet (`text/css`) |
| `/gui-settings.css` | — | settings and confirm dialog stylesheet (`text/css`) |
| `/gui-onboarding.css` | — | first-run welcome stylesheet (`text/css`) |
| `/gui-shell.js` | — | app shell script (`text/javascript`) |
| `/gui-history.js` | — | sidebar history script (`text/javascript`) |
| `/gui-settings.js` | — | settings script (`text/javascript`) |
| `/gui-onboarding.js` | — | first-run welcome script (`text/javascript`) |
| `/api/scan` | `folder` (or `folder_b64` — R13-3: URL-safe base64 of the folder's file-system bytes, for a non-UTF-8 path; takes precedence), `pixel` (`off`/`fast`/`deep`), `recursive`, `max_files`, `max_file_bytes`, `dedupe`, `heatmaps`, `deep_signals`, `no_default_engine`, `model_path` / `fusion_profile` (bare file name in the models dir), `async` | the same `scan_to_json` payload the CLI prints (`{"schema_version", "summary", "coverage", "thresholds", "items"}`) or `{"error": "..."}`; with `async=1` returns `{"job_id", "status": "running"}`. P0R-6: every folder scan is a server-held job — the payload carries `scan_job` (128-bit id; the async job's id; `scan_job_note` when the job could not be persisted), the only thing `POST /api/report` accepts for folder rows. R23-13 (round 23): every true/false parameter (`recursive`, `dedupe`, `heatmaps`, `deep_signals`, `no_default_engine`, `async`, and the same keys in a report's `options`) takes `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off` (any case) or empty; any other value is 400 (E5) — it used to be read as false. **400** `{"error": ...}` for an invalid option (non-integer limit, unknown pixel mode, `model_path`/`fusion_profile` not a file name inside the models dir). **403** `{"error": "허용되지 않은 경로"}` when `folder` is outside the read roots (see below) |
| `/api/scan-status` | `job` | `{"job_id", "status": "running"\|"done"\|"cancelled"\|"error"}` plus `result` once finished (R18-3: `cancelled` when a cancel left files unanalyzed — `result` is the partial scan, `summary.cancelled` true); P0R-6: jobs are bound to the server's token (another token's job is 404), finished jobs are persisted outside the evidence and expire after `DEEPFAKE_LENS_SCAN_JOB_TTL_DAYS` days (default 7; was in memory for 15 min **counted from the job's creation**, so a scan running longer lost its result — review §2.4), a running job is never evicted, at most 32 `async` jobs run at once GUI redesign (U-7): also `done` (rows finished so far), `total` (rows the scan will report; `null` until the folder is enumerated), `current` (the escaped name of the row finished last), `recent` (the last five finished rows: name, status, verdict_code) and `elapsed_seconds` — progress held in memory with a running job, never persisted or signed |
| `/api/scan-cancel` | `job` | Sets the job's cancel flag; the scan stops between items and the job ends `cancelled` with the partial result (R18-3 — R26-5 (round 26): this line said "as `done`"; a cancel after the last file leaves a complete scan, `done`). `{"cancelled": true}` while running, `false` once finished |
| `/api/analyze-file` | `file` (or `file_b64` — R13-3: URL-safe base64 of the file-system bytes, for a non-UTF-8 name; takes precedence; the response carries `file_b64` beside `file`) | analysis_result (D3: the scan result — verdict_code, evidence, coverage; the scan's own photo-gated pixel layer only) + `layer_diagnostics.provenance_metadata` / `.tool_candidates`, or `{"error"}` (+`detail` for unexpected failures). No ungated pixel pre-screen and no band. **403** `{"error": "허용되지 않은 경로"}` when `file` is outside the read roots |
| `/api/heatmap` | `path`, `root` | PNG bytes; 403 unless `path` is a `.png` under a **server-registered** read root (see below), 404 if missing |
| `/api/preview` | `path`, `root` | media bytes with `nosniff`; same registered-root rule, media extensions only, ≤128 MiB |
| `/api/stats` | — | `{"status", "version", "modules", "default_folder", "default_folder_b64", "pdf_available"}` — `default_folder` is the server's `--folder` as an absolute path (`default_folder_b64` its file-system bytes, R12-4; both `null` when the server has none — `api-serve`), which the GUI prefills as the scan folder; `pdf_available` says whether the PDF renderer (pymupdf) is installed (`?format=pdf`/`evidence` answer 501 without it) |
| `/api/reviews` | — | `{"status", "reviews"}` — every stored examiner review (R22-3: `reviews.json` in the per-user application-data folder — macOS `~/Library/Application Support/deepfake-lens`, Windows `%APPDATA%\deepfake-lens`, else `$XDG_DATA_HOME/deepfake-lens` (absolute) or `~/.local/share/deepfake-lens`; override `DEEPFAKE_LENS_REVIEWS` (absolute); never inside a folder the server examines (E49); records of the old `~/.deepfake-lens/reviews.json` are copied once into the default location — R23-8: never into an override file) |
| `/api/review` | `path` or `artifact_id` | `{"status", "artifact_id", "review"}`; 400 without either (E33) |
| POST `/api/review` | JSON object `{"artifact_id" (or "path"), …review fields}` | saves the review; `{"status", "artifact_id", "review"}`; 400 for invalid JSON, a non-object body, a missing or non-string `artifact_id` (E33) |
| POST `/api/analyze-upload` | multipart file body (≤ `MAX_UPLOAD_BYTES`) | upload-analysis payload |
| POST `/api/compare` | multipart with two files | Two-file comparison — speaker distance (audio pair) or stylometry (text pair) |
| POST `/api/check` | JSON `{"text": "...", "watermark_secret": "...", "watermark_gamma": 0.25}` **or** one multipart file | Unified check-all: full scan + all `models/` engine members + forensic + text probes → `{mode, item, advanced?, forensic?}` |
| POST `/api/report` | JSON body (≤ 64 MiB) — P0R-6 (user decision 2026-10-17): **`scan_job`** (the id of a folder scan **this server ran and holds** for this server's token: the `scan_job` of a `GET /api/scan` response, the `job_id` / `result.scan_job` of `/api/scan?async=1`, or the `job_id` of `/api/scan/stream` on api-serve; required unless the report holds upload rows only), optional **`exhibit_paths`** (an ordered list of the job's row `path`s — the evidence statement numbers those rows as exhibits in that order and lists every other row of the job after them as `서증 외(검사 행)`; every format signs it as `exhibit_paths`), optional **`uploads`** (rows of `/api/analyze-upload`, listed unsigned — P6/R22-8 below), `format`, `side` (`plaintiff` only on this branch, else 400) and the case fields `case_no`, `case_name`, `plaintiff`, `defendant`, `court`, `law_firm`, `contact`, `center`, `exhibit_no` (strings). **Any other field is 400** (`보고서 요청에 알 수 없는 필드가 있습니다: … — 보고서는 서버가 보관한 검사 작업(scan_job)으로만 만듭니다 …`) — the request carries no rows, options, summary, thresholds or scan folder. `?format=html` (default) / `pdf` / `evidence` / `json` | rendered standalone HTML report, forensic PDF, evidence statement, or (`json`) the signed report body. **The signed row set is the job's rows** (exactly as the scan produced them, in scan order), the options are the job's (`analysis_options`, captured when the scan ran), the threshold provenance, model coverage and model pins are the job's, and the `기록되지 않은 파일` / cancel accounting is the job's summary (`unrecorded_files.listing.source` `scan_job`, `files_cancelled_source` `server` for a cancelled scan — R18-3/R23-3/R24-1: a cancelled archive container is signed as the job's own `cancelled` row). Change detection (R19-13): every file the scan hashed is re-hashed under the job's scan folder without following links (an archive member through its container's digest; a container the scan could not hash: the member from a fresh extraction) — on any difference **every format is 409** `{"error": "검사 뒤 바뀐 파일이 있습니다 — 다시 검사하십시오(보고서를 만들지 않았습니다)", "changed_rows": [{"path", "reason"}]}` with `reason` `검사 뒤 내용이 바뀌었습니다` / `검사 뒤 파일이 없어졌거나 읽을 수 없습니다` / `검사 뒤 파일이나 그 경로의 폴더가 심볼릭 링크로 바뀌었습니다` / `검사 뒤 압축 파일에서 이 구성 파일이 바뀌었거나 없어졌습니다` — the server's digests, statuses and verdicts are not returned (review §2.4). Rows the scan did not hash (links, special files, unreadable files) are signed as the scan recorded them. Jobs (`scan_jobs.py`): bound to the token the server was started with (another token's job is 404 like an unknown one); persisted when finished to `DEEPFAKE_LENS_SCAN_JOBS` (absolute) or `scan-jobs` in the per-user application-data folder (the reviews' folder, R22-3) — never inside a folder the server examines (then the job is held in memory only and the scan response carries `scan_job_note` `검사 작업 기록을 디스크에 보관하지 않았습니다(…) — 이 서버가 다시 시작되면 이 검사로 보고서를 만들 수 없습니다`); owner-only permissions; with `DEEPFAKE_LENS_REPORT_KEY` each record carries an HMAC and a record that does not verify is refused (409 `검사 작업 기록의 무결성을 확인할 수 없습니다 …`); finished jobs expire after `DEEPFAKE_LENS_SCAN_JOB_TTL_DAYS` days (default 7); a running job is never evicted. Upload rows (`uploads`, P6): an upload's file is a temp file, never a file of the read roots — the row is listed unsigned under `서명 제외(클라이언트 제공 결과)` (`excluded_items`); R22-8: its name is limited to a relative name of at most 255 characters without empty / `.` / `..` components (else 400) and marked `name_source` `server_upload` (this server produced an upload row of that name and sha256) / `client` with the reason `업로드 파일(클라이언트 제공 이름 — …)`. Signed with `DEEPFAKE_LENS_REPORT_KEY` when set, otherwise marked `서명 없음`. A symbolic-link row keeps `null` / `해시 불가(심볼릭 링크 — 링크를 따라가지 않음)` (N2); an archive member row carries the digest the scan recorded (N10). **400** for a malformed request (N11), **403** when the job's scan folder is no longer inside the read roots or an upload row names a `heatmap_path` outside them, **404** for an unknown / expired / another token's job, **409** for a job still running or failed, or a changed file GUI redesign: `format=evidence-preview` — the evidence statement's PDF texts as JSON (`case_lines`, `entries`, `boxes`, `unsigned`, `signoff`, `court`, `scan_root_line`, `analysis_options_line`, plus `unrecorded_listing` and `pdf_glyph_escapes`) built from the same job, the same `exhibit_paths` and the same statement build as `evidence`, with every text as the PDF draws it (R23-4 escapes); unsigned, and without the change detection (the export refuses a changed file) |
| POST `/api/feedback` | feedback JSON body (≤ 1 MiB) | appends to `feedback.jsonl` in the same per-user folder as the reviews (R22-3; override `DEEPFAKE_LENS_FEEDBACK`, absolute; was `~/.deepfake-lens/feedback.jsonl`, copied once into the default location — R23-8: never into an override file, the reviews' rule) — 409 instead of a write inside a folder the server examines (E49); 409 / 507 when the file cannot be written (E50) |
| `/api/history` | — | `{"items", "retention_days", "store", "note"}`: the finished scan jobs of this owner, newest first, each with its `case` (`case_no`, `case_name`); item shape in "History item" below |
| POST `/api/history-delete` | JSON `{"job"}` | `{"status": "success", "job", "deleted"}`: the job's record and its case fields are removed; 400 for a missing or invalid `job`, 404 unknown job, 409 while the job is running |
| `/api/job-case` | `job` | `{"status", "job", "case"}`: the case fields saved with that scan job (empty when none); 400 / 404 |
| POST `/api/job-case` | JSON `{"job", "case": {…}}` | `{"status", "job", "case"}`: saves the case fields of a scan job this owner can see; 400 for an unknown `case` key or a non-object body or `case`, 404 unknown job |

**History item** — each element of `items` is a finished scan job, newest first: `{"job_id", "status", "source", "created", "finished", "scan_root", "folder_name", "files", "summary", "persisted", "case": {"case_no", "case_name"}}`. `case_no` and `case_name` are `""` when the job has no case fields.

**Where the history lives** — the list is the existing scan-job store (`scan-jobs/` in the user-data folder, `DEEPFAKE_LENS_SCAN_JOBS` overrides; records are HMAC-signed with the report key and kept `DEEPFAKE_LENS_SCAN_JOB_TTL_DAYS`, default 7 days). A record whose signature does not verify is left out and counted in `note`. The case fields of each job live in `cases.json` next to `reviews.json` (`DEEPFAKE_LENS_CASES` overrides; file mode 0600; never inside an examined folder, refused with 409 like the other examiner records); `/api/history` drops the entries whose job record is gone, so party names do not outlive their scan. The GUI's own settings (theme, scan defaults, shortcuts, the welcome panel) are kept in the browser's local storage for the page's origin, so a different port or `desktop --browser-tab` starts with the defaults.

Read-root rule (G31) for `/api/scan`, `/api/analyze-file`,
`/api/heatmap`, `/api/preview`, `/api/report` (evidence hashing and
heatmap embedding) and every `api-serve` endpoint that takes a
`file_path`/`directory`: only the operator registers read roots, at server
start — `web --folder <dir>` and each repeatable `--allow-root <dir>`
(`api-serve --allow-root` likewise; max 64, oldest evicted first). No
request can add one; `/api/scan?folder=` no longer registers anything.
With no root registered, the server's own default folder (`--folder`, or
the directory it was started in) is the only root — never the whole disk.
R21-4 (round 21): that directory is checked at start like a named root
(R20-7). R21-10 (user decision 2026-10-12: relocate instead of refuse): when
it holds the tool folders HOME decides (`~/.cache/deepfake-lens` logs,
heatmaps, numba cache) they are moved for that run outside it, with one
notice (R22-2: on Windows to `%ProgramData%\deepfake-lens\<user>` when the
profile is the root — `%LOCALAPPDATA%` and `%TEMP%` are inside it); the server is not started (exit 2) only when no place outside is
left or every temp-folder candidate is inside (`docs/deepfake-lens-cli.md`,
"Logs").
A path outside every root gets **403** `{"error": "허용되지 않은 경로"}`
(heatmap/preview keep their plain-text `forbidden` body) and no file bytes.
When a `root` argument is supplied it must also contain the file, so a
forged `root=C:\` can never widen the read scope.

Limits (clamped, not optional): `max_files ≤ 2000`,
`max_file_bytes ≤ 1 GiB`, `heatmaps` only with `--pixel deep`. A value
below 1 is refused with 400 (Y8), never raised to 1.
The bundled GUI uses `async=1` + `/api/scan-status` polling so a long scan
never holds one request open; the synchronous form still works for tools.
It shows `done`/`total` with the elapsed time (and an estimate once rows
are finishing), polls without a deadline, and keeps the job id for the
browser tab so a reloaded page reconnects to the running job.
The GUI's cancel button calls `/api/scan-cancel`, which sets a flag the
scanner checks between files — the job then ends `cancelled` with the partial
result (`summary.cancelled`, R18-3; R26-5 (round 26) corrected this line, which
said the partial results come back as `done`).

## 데스크톱 모드 (`desktop`)

터미널을 쓰지 않는 사용자를 위한 진입점입니다. `web`과 같은 로컬 서버를 시작하고 브라우저를 엽니다(계획: `docs/gui/plan_desktop_mode.md`).

```
deepfake-lens desktop [--folder P] [--port N] [--no-browser] [--idle-exit-minutes M] [--models-dir D]
```

- `--folder P`: 처음 열 증거 폴더(읽기 허용 폴더로 등록됨, 생략 가능).
- `--port N`: 지정하면 그 포트 하나만 시도합니다. 생략하면 8765부터 21개 포트 중 첫 빈 포트를 씁니다(모두 사용 중이면 `오류: …`와 종료 코드 2). 그 포트에서 이 사용자의 데스크톱 서버가 이미 답하면(`/api/stats`가 `status: "ok"`·`version`·`desktop: true`와 이 사용자의 `instance_user`를 돌려줌 — `web` 서버나 다른 사용자의 인스턴스는 해당하지 않음) 서버를 새로 띄우지 않고 브라우저만 열고 종료 코드 0으로 끝납니다. 이때 브라우저를 열지 못하면 주소를 한 줄 출력하고 종료 코드 3으로 끝납니다(꾸러미의 `Deepfake Lens.cmd`는 3이면 오류 문구 없이 창을 멈춰 주소를 읽을 수 있게 합니다).
- `--no-browser`: 브라우저를 열지 않습니다. 브라우저를 열지 못하면 주소를 한 줄 출력합니다.
- `--idle-exit-minutes M`: 기본 20, 0이면 유휴 종료를 끕니다(음수는 사용법 오류). `--models-dir D`는 `web`과 같습니다.
- 바인드는 항상 `127.0.0.1`이고 `--allow-lan`·`--token`은 없습니다. 서버를 `desktop=True`로 만들 때 이 둘이 함께 오면 `ValueError`로 거부합니다(폴더 선택 창과 종료는 이 PC 앞의 사람에게만 속합니다).
- 시작하면 콘솔에 화면 주소(`/gui`), 종료 방법, 서명 키 줄, 로그 파일 줄, 유휴 종료 줄(`M > 0`일 때)과, x64가 아닌 PC나 OneDrive 폴더 안 실행 같은 `주의:` 줄을 찍습니다.

### `/api/stats`의 `desktop`

`/api/stats`는 `desktop` 항목을 항상 돌려줍니다. 서버가 `desktop=True`로 만들어졌을 때(곧 `desktop` 명령일 때)만 `true`이고, `web`·`api-serve`에서는 `false`입니다. 같은 응답의 추가 항목:

| 항목 | 값 |
| --- | --- |
| `roots` | 등록된 읽기 허용 폴더의 목록(문자열). 등록된 것이 없으면 `[]` |
| `signing` | `{"available": 키 사용 가능 여부, "source": "env"·"file"·"created"·null, "key_id": 키 식별자 또는 null}` — 데스크톱 모드가 아니면 `source`는 환경 변수 키가 있을 때 `"env"`, 없으면 `null` |
| `idle_exit_minutes` | 유휴 종료 분. 데스크톱 모드가 아니면 `null` |
| `instance_user` | 이 프로세스를 실행한 계정 이름의 sha256 앞 12자리(비밀 아님 — 같은 루프백을 쓰는 다른 사용자의 인스턴스를 구별). 데스크톱 모드가 아니면 `null` |

### 데스크톱 전용 경로

네 경로는 서버가 `desktop=True`일 때만 있습니다. 그렇지 않으면 다른 모르는 경로처럼 모두 **404**(`{"error": "찾을 수 없는 경로입니다"}`)입니다. 다른 `/api/*`와 같은 보호가 먼저 적용됩니다. `Host`가 `127.0.0.1`·`localhost`·`::1`이 아니면 **403**(DNS 리바인딩 방어), `X-Deepfake-Lens-Client: gui` 헤더(비어 있지 않은 값)가 없으면 **401**입니다(교차 출처 요청은 이 헤더를 붙일 수 없습니다). 이 보호를 통과한 `/api/*` 요청은 어느 경로든 유휴 시계를 다시 시작합니다.

| 경로 | 메서드 | 응답 |
| --- | --- | --- |
| `/api/roots` | GET | 200 `{"roots": [{"path": 문자열, "path_b64": 바이트 경로의 base64}, …]}` — 등록 순서 |
| `/api/pick-folder` | POST | OS 폴더 선택 창을 열고(Windows PowerShell, macOS `osascript`, Linux `zenity`·`kdialog`) 고른 폴더를 읽기 허용 폴더로 등록합니다. 200 `{"folder": 경로, "folder_b64": base64}`, OneDrive 폴더처럼 주의가 필요하면 선택 항목 `note`(문자열 — 온라인 전용 파일을 내려받으며 검사하거나 건너뛸 수 있다는 안내)가 붙습니다. 취소하면 200 `{"folder": null, "folder_b64": null}`. 창이 이미 열려 있으면 **409** `{"error": "폴더 선택 창이 이미 열려 있습니다 — …"}`. 고른 경로가 폴더가 아니면 400, **너무 넓은 폴더**(홈·드라이브 루트·사용자 데이터 폴더·도구 캐시·임시 폴더·`sys.prefix`를 품는 폴더)면 **400** `{"error": "너무 넓은 폴더입니다(…) — 증거 파일이 든 폴더를 고르십시오"}`. 등록 수 상한(64)을 넘으면 409. **501**: 창을 띄울 수단이 없거나 띄우지 못할 때, 보안 정책이 PowerShell을 제한할 때(`…보안 정책이 PowerShell을 제한합니다…` — 그 밖의 PowerShell 비정상 종료도 501이고 stderr는 로그에만 남음), 10분(`run_child(timeout=600)`) 안에 답이 없을 때(시간 초과 문구) — 501 안내는 폴더를 직접 입력하라고 알립니다 |
| `/api/shutdown` | POST | 비동기 검사가 진행 중이면 **409** `{"error": "검사가 진행 중입니다 — …", "job_id": 작업 id}`. 아니면 200 `{"status": "shutting_down"}`을 보낸 뒤 서버를 끄고 프로세스가 종료 코드 0으로 끝납니다 |
| `/api/heartbeat` | POST | 200 `{"status": "ok"}` — 화면이 열려 있다는 신호(유휴 종료를 미룸) |

`/api/pick-folder`의 등록은 이전 폴더를 밀어내지 않습니다(진행 중 검사의 미리보기가 403이 되지 않게). 이 경로가 시작 뒤 읽기 허용 폴더를 늘리는 유일한 길입니다.

시험용 환경 변수 `DEEPFAKE_LENS_PICK_FOLDER_STUB`(테스트와 GUI 스모크용)는 실제 창 대신 쓰입니다. 폴더 경로면 그 폴더를 고른 것으로, 빈 문자열(`""`)이면 취소로, `"501"`이면 창을 띄울 수 없는 환경(501)으로 취급합니다.

#### Windows 폴더 선택 helper 규칙

PowerShell 스크립트는 표준출력에 결과를 명시합니다. 고르면 `OK:<경로>`, 취소하면 `CANCEL`(둘 다 BOM 없는 UTF-8). 스크립트는 먼저 언어 모드를 검사해 ConstrainedLanguage(AppLocker·WDAC)면 **종료 코드 3**으로 끝나고 서버는 이를 정책 차단 501로 답합니다. 종료 코드 0이 아니거나 `OK:`·`CANCEL`이 아닌 출력은 취소로 보지 않고 501로 처리합니다(예전에는 모든 실패가 「취소」로 보였습니다). 소유 창은 작업 표시줄에 나오지 않을 수 있어 안내는 「Alt+Tab으로 '폴더 찾아보기' 창을 찾으십시오」입니다.

### 포트 독점 바인드

Windows에서는 `SO_REUSEADDR`가 이미 LISTEN 중인 포트의 bind도 허용하므로, 빈 포트 탐침과 서버 소켓 모두 `SO_EXCLUSIVEADDRUSE`를 씁니다(서버의 `allow_reuse_address`는 끕니다). 같은 포트에 두 번째 인스턴스를 띄우면 bind가 거부되고, 그 포트에서 이 도구가 이미 답하면(위 `--port`) 런처는 서버를 새로 띄우지 않고 첫 인스턴스의 화면을 브라우저로 엽니다. 다른 프로그램이 쓰는 포트이거나 bind가 실패(`OSError`)하면 다음 포트로 넘어갑니다. 탐침은 환경의 프록시 설정을 무시합니다(`ProxyHandler({})`).

### 서명 키 파일

`DEEPFAKE_LENS_REPORT_KEY`가 이미 있으면 그것을 씁니다(`source: "env"`). 없으면 사용자 데이터 폴더(Windows `%APPDATA%\deepfake-lens\`)의 `report-key` 파일을 읽습니다(`"file"`). 파일이 없으면 64자리 16진 키를 새로 만들어 쓰고(`"created"`) 이 프로세스의 환경 변수로 올립니다. 만드는 방법: 같은 폴더의 임시 파일(`.report-key.<pid>.<난수>.tmp`, `O_EXCL`, POSIX 권한 0600)에 키를 끝까지 쓴 뒤 `os.link`로 `report-key` 이름에 겁니다. 하드 링크는 이미 있는 이름에 걸리지 않으므로(`FileExistsError`) 동시에 처음 시작한 두 인스턴스 중 하나만 키를 놓고, 진 쪽은 임시 파일을 지우고 놓인 키를 읽습니다 — 반쯤 쓰인 파일을 「손상」으로 읽는 일도, 서로 다른 키로 서명하는 일도 없습니다. 하드 링크를 지원하지 않는 파일 시스템(exFAT 등, `os.link`가 다른 `OSError`)에서만 `report-key`가 아직 없을 때 `os.replace`로 옮겨 놓는데, 이 경우 두 인스턴스가 동시에 처음 시작하면 경합할 수 있습니다(나중에 옮긴 쪽의 키가 파일에 남고, 먼저 옮긴 인스턴스는 그 실행 동안 파일과 다른 자기 키로 서명할 수 있음). 놓인 뒤 Windows에서는 소유자 전용 DACL을 적용합니다. 파일이 읽히지 않거나 비었거나 최소 길이(32바이트)에 못 미치면 `오류: 서명 키 파일이 손상되었습니다: <경로> …`와 종료 코드 2로 끝나며 **파일을 덮어쓰지 않습니다**(이전 감정서는 그 키로만 검증됩니다). 사용자 데이터 폴더를 정할 수 없거나 키 파일을 쓸 수 없으면 서명 없이 시작하고 콘솔에 한 줄을 남깁니다. 이 키로 서명한 감정서는 **이 키 파일을 가진 계정에서만 검증**됩니다(「이 PC에서만」이 아님 — `%APPDATA%`는 Roaming 폴더라 AD 로밍 프로필·폴더 리디렉션이면 다른 PC로 동기화될 수 있습니다. 위치 변경은 BL-72): `deepfake-lens verify-report --key-file <report-key 파일>`.

### 유휴 종료와 종료 방법

`--idle-exit-minutes M`(M > 0)분 동안 API 호출(화면의 heartbeat 포함)이 없고 진행 중인 비동기 검사도 없으면 `M분 동안 사용이 없어 종료합니다.`를 찍고 스스로 꺼집니다. 검사가 진행되는 동안에는 꺼지지 않습니다. 절전 규칙: 유휴 시계의 루프 틱 간격이 60초를 넘게 뛰면(Windows의 `time.monotonic()`은 절전 시간을 포함합니다) 절전 복귀로 보고 유휴 시계를 다시 시작하므로 깨어난 직후 곧바로 꺼지지 않습니다. 화면은 보이는 순간(`visibilitychange`·`focus`·`pageshow`) 즉시 heartbeat를 보냅니다. 그 밖의 종료 방법은 화면의 「프로그램 종료」(`/api/shutdown`, 종료 코드 0), 콘솔 창 닫기, Ctrl-C입니다. Ctrl-C는 서버를 멈춘 뒤 CLI의 중단 경로로 넘어가 `중단됨(사용자 요청)`을 stderr에 찍고 종료 코드 130으로 끝납니다(꾸러미의 `.cmd`에서는 그 뒤 cmd가 「일괄 작업을 끝내시겠습니까 (Y/N)?」를 묻습니다).

## Error status codes — every `/api/*` endpoint (X4)

No `/api/*` endpoint answers an error with 200: every refusal carries an HTTP
error status and a Korean text. The web GUI server (`web`) and api-serve share
the payload functions (`webapp_api`), which return their errors as
`webapp_api.ApiError` (a `{"error": …}` dict with its status); both servers
send that status (api-serve: `api_server._api_json`). Body key: `error` on the
web server and for the shared GUI endpoints on api-serve; api-serve's own
endpoints (`/api/analyze/*`, `/api/classify`, `/api/check`, `/api/compare`,
`/api/jobs/*`, review routes) use FastAPI's `detail`.

| ID | Endpoint (server) | Condition | Status | Korean text (`error` / `detail`) |
|---|---|---|---|---|
| E1 | any `/api/*` (both) | missing `X-Deepfake-Lens-Client` / bad token | 401 | `… 헤더가 없습니다 …` / `인증 실패: …` |
| E2 | any `/api/*` (both) | unknown route | 404 | `찾을 수 없는 경로입니다` |
| E3 | any `/api/*` (web / api) | method not supported / not allowed | 501 / 405 | `지원하지 않는 요청 메서드입니다: …` / `이 경로에서 허용되지 않는 요청 메서드입니다: …` |
| E4 | any `/api/*` (both) | unhandled exception | 500 | `서버 내부 오류가 발생했습니다 — 상세는 서버 로그를 확인하십시오` |
| E5 | GET `/api/scan` (both), POST `/api/scan/stream` `recursive` (api) | invalid option (`pixel`, non-integer limit, `model_path`/`fusion_profile` not a name in the models dir — R23-9: also one the file system cannot look up, e.g. longer than a name may be (was a 500); R23-13: a true/false option — `recursive`, `dedupe`, `heatmaps`, `deep_signals`, `no_default_engine`, `async` — other than `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off` or empty (was read as false); the same values in POST `/api/report` `options` are E27; R24-8 (round 24): and `recursive` of POST `/api/scan/stream` — a pydantic bool there took `t`/`y`/`n`… (200) and answered an unknown value 422) | 400 | e.g. `max_files는 정수여야 합니다` / `recursive은(는) 참·거짓 값이어야 합니다(…): 「maybe」` / `model_path을(를) 모델 디렉터리에서 찾을 수 없습니다(경로가 너무 깁니다…` |
| E6 | GET `/api/scan` (both), POST `/api/scan/stream` (api) | `max_files` or `max_file_bytes` below 1 (Y8 — was clamped to 1) | 400 | `max_files는 1 이상이어야 합니다` / `max_file_bytes는 1 이상이어야 합니다` |
| E7 | GET `/api/scan` (both) | folder missing / a file / unreadable | 400 | `폴더를 찾을 수 없습니다: …` (scan's S4 texts) |
| E8 | GET `/api/scan` (both) | folder (`folder` or `folder_b64`) outside the read roots | 403 | `허용되지 않은 경로` |
| E9 | GET `/api/scan?async=1` (both) | 32 jobs already registered | 400 | `실행 중인 검사 작업이 너무 많습니다 — …` |
| E10 | GET `/api/scan-status`, `/api/scan-cancel` (both) | no `job` | 400 | `job 매개변수가 필요합니다` |
| E11 | GET `/api/scan-status`, `/api/scan-cancel` (both) | unknown or expired job | 404 | `알 수 없거나 만료된 작업입니다` |
| E12 | GET `/api/analyze-file` (both) | no `file` | 400 | `file 매개변수(파일 경로)가 필요합니다` |
| E13 | GET `/api/analyze-file` (both) | file missing | 404 | `파일을 찾을 수 없습니다: …` |
| E14 | GET `/api/analyze-file` (both) | a folder | 400 | `파일이 아니라 폴더입니다: … (폴더는 scan을 사용)` |
| E15 | GET `/api/analyze-file` (both) | outside the read roots (`file` or `file_b64`) | 403 | `허용되지 않은 경로` |
| E16 | GET `/api/analyze-file` (both) | analysis raised | 500 | `파일 분석 중 오류가 발생했습니다` (+ `detail`) |
| E17 | GET `/api/heatmap`, `/api/preview` (both) | outside the roots / not found / not media (P8: preview of a non-media file inside the roots was 403) | 403 / 404 / 400 | plain text `허용되지 않은 경로` / `파일을 찾을 수 없습니다` / `미리보기를 지원하지 않는 형식입니다: …` + `X-Deepfake-Lens-Error` header |
| E18 | POST `/api/analyze-upload` (both) | not multipart, no file part, empty body, body cut off before its closing boundary (P8 — was analyzed as a partial file, 200), no or an empty `boundary=` / a boundary the body never uses (R30-5 — both said the body was cut off), a part whose headers never end, more than `MAX_UPLOAD_PARTS` (1000) parts (round 31) | 400 | `multipart/form-data 업로드가 필요합니다`, `업로드된 파일이 없습니다`, `업로드 본문이 잘렸습니다(닫는 경계 없음) — …`, `Content-Type에 multipart boundary가 없습니다 — …`, `본문의 경계가 Content-Type의 boundary와 다릅니다 — …`, `업로드 본문의 파트 헤더가 끝나지 않습니다(빈 줄 없음) — …`, `업로드 본문의 파트(파일·폼 필드)가 상한(1000개)보다 많습니다 — …` |
| E19 | POST `/api/analyze-upload` (both) | body over `MAX_UPLOAD_BYTES` | 413 | `업로드 크기가 상한(…바이트)을 초과합니다` |
| E20 | POST `/api/analyze-upload` (web) | the request handling raised outside a file's analysis (a file whose analysis raises is a `failed` row of a 200 — R29-2) | 500 | `업로드 분석 중 오류가 발생했습니다` (+ `detail`) |
| E21 | POST `/api/check` (web) | invalid JSON / not an object / text < 8 or > 256 KB / not multipart / no file / more than one file (R29-5 — only the first was analyzed) / cut-off multipart (P8) / no boundary or a boundary the body never uses (R30-5, as E18) / more than 1000 parts (round 31, as E18) | 400 | `JSON 본문을 해석할 수 없습니다`, `요청 본문은 JSON 객체여야 합니다`, `분석할 텍스트가 너무 짧습니다 (8자 이상).`, `빠른 검사는 파일 1개만 받습니다(받은 파일 N개) — …`, … |
| E22 | POST `/api/check` (web) | body over `MAX_UPLOAD_BYTES` | 413 | `요청 본문이 상한(…바이트)을 초과합니다` |
| E23 | POST `/api/check` (api) | neither `file_path` nor `text`; text > 256 KB | 400 | `file_path 또는 text가 필요합니다` / `텍스트가 256KB를 초과합니다` |
| E24 | POST `/api/compare` (web) | not multipart / not exactly 2 files (R29-6: a 0-byte file counts, a third is refused — it was ignored) / mixed pair / cut-off multipart (P8) / no boundary or a boundary the body never uses (R30-5, as E18) / more than 1000 parts (round 31, as E18) | 400 | `비교할 파일 2개가 필요합니다(받은 파일 N개)`, … |
| E25 | POST `/api/compare` (web) | comparison raised | 500 | `비교 분석 중 오류가 발생했습니다` (+ `detail`) |
| E26 | POST `/api/compare` (api) | comparison error / raised | 400 / 500 | the comparison's Korean error |
| E27 | POST `/api/report` (both) | malformed body (not an object, too deep — R11-3), an unknown field (P0R-6: `items`, `options`, `scan_root`, `summary`, … are no longer accepted), `scan_job` missing (without `uploads`) or not a string, `exhibit_paths` not a list of strings / empty / with a repeated path / naming a path that is not a row of the job / without a job, `side` other than `plaintiff`, a case field that is not a string, `uploads` not a list of objects or an upload row without a path or with a name outside the R22-8 form, bad `format` | 400 | see "REST API endpoints" (N11); `JSON 중첩이 너무 깊습니다` |
| E28 | POST `/api/report` (both) | an upload row's `heatmap_path` outside the roots — in `result.pixel_analysis` or at the row's top level (P8) | 403 | `허용되지 않은 경로` |
| E29 | POST `/api/report` (both) | P0R-6: `scan_job` unknown, expired, or a job of another token | 404 | `알 수 없거나 만료된 검사 작업입니다 — 폴더를 다시 검사하십시오` |
| E30 | POST `/api/report` (both) | the job's scan folder is no longer inside the read roots (G31, P1) | 403 | `허용되지 않은 경로` |
| E31 | POST `/api/report` (both) | evidence-statement PDF failed / pymupdf missing for `format=pdf`, `evidence` or `evidence-statement` (checked after the request checks and before the job is read — B8, R22-7) | 500 / 501 | `증거설명서 PDF 생성 실패: …` / `PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다…` |
| E32 | POST `/api/feedback` (both) | invalid JSON, too-deep JSON (R11-3 — was a 500), not an object, unknown `expected_label`, no `path` | 400 | `JSON 본문을 해석할 수 없습니다`, `JSON 중첩이 너무 깊습니다`, `피드백 요청 본문은 JSON 객체여야 합니다`, `expected_label은 인식 가능한 라벨이어야 합니다 …`, `path가 필요합니다` |
| E33 | GET/POST `/api/review` (web), review routes incl. PUT `/api/artifacts/{id}/review` (api) | no `path`/`artifact_id`, invalid or too-deep JSON (R11-3), a JSON body that is not an object (P8 — an array was a 500), a non-string `artifact_id` | 400 | `path 또는 artifact_id가 필요합니다`, `JSON 본문을 해석할 수 없습니다`, `JSON 중첩이 너무 깊습니다`, `검토 요청 본문은 JSON 객체여야 합니다`, `artifact_id 값은 문자열이어야 합니다` |
| E34 | POST `/api/analyze/*`, `/api/classify`, `/api/check` (api) | analysis raised | 500 | the failure reason |
| E35 | `/api/jobs/{id}`, `/api/jobs/{id}/cancel` (api) | unknown or finished job | 404 | `알 수 없거나 이미 끝난 작업입니다` |
| E36 | POST `/api/check/stream`, `/api/scan/stream` (api) | 32 stream jobs running | 429 | `실행 중인 작업이 너무 많습니다 …` |
| E37 | any query parameter (api) | missing / malformed | 422 | `요청 매개변수 오류 — …` |
| E38 | POST `/api/analyze/{image,audio,face,forensic}`, `/api/classify`, `/api/check?file_path=`, `/api/check/stream?file_path=`, `/api/compare` (api) | the named file is missing (P8 — was 200 "success" with a failed row; classify 500; compare a misleading 400) | 404 | `파일을 찾을 수 없습니다: …` |
| E39 | the same api-serve file endpoints (api) | the named path is a folder (P8) | 400 | `파일이 아니라 폴더입니다: …` |
| E40 | the same api-serve file endpoints (api) | outside the read roots | 403 | `허용되지 않은 경로` |
| E41 | POST `/api/multimodal` (api) | a query parameter other than the four scores (e.g. `file_path` — P8: was ignored, 200) | 400 | `알 수 없는 매개변수입니다: … — /api/multimodal은 점수(…)만 받습니다(파일 분석은 /api/analyze/*)` |
| E42 | POST `/api/scan/stream` (api) | `directory` empty, folder missing, a file or unreadable — checked before the stream starts (R9-3: was 200 + an SSE `error` event) | 400 | `directory가 필요합니다` / `폴더를 찾을 수 없습니다: …` / `폴더가 아니라 파일입니다: …` (scan's S4 texts, as E7) |
| E43 | POST `/api/scan/stream` (api) | folder outside the read roots — before the stream starts | 403 | `허용되지 않은 경로` |
| E44 | POST `/api/check/stream` (api) | neither `file_path` nor `text`; text > 256 KB — before the stream starts (R9-3: was 200 + an SSE `error` event) | 400 | `file_path 또는 text가 필요합니다` / `텍스트가 256KB를 초과합니다` |
| E45 | POST `/api/analyze/text` (api) | `text` empty or blank (R9-8: was 200 with the result of an empty text) | 400 | `분석할 텍스트가 비어 있습니다 — text 매개변수에 분석할 글을 넣으십시오` |
| E46 | GET `/api/analyze-file` `file_b64`, `/api/scan` `folder_b64` (also `async=1`), `/api/preview`/`/api/heatmap` `path_b64`/`root_b64` (both) | not URL-safe base64, empty after decoding, or a NUL byte (R12-4, R13-3) | 400 | `경로 인코딩(path_b64·root_b64·file_b64·folder_b64)이 올바르지 않습니다 — 검사 결과의 값을 그대로 보내십시오` (preview/heatmap: plain text) |
| E47 | POST `/api/report` (both) | a file the job hashed changed after the scan (R19-13, P0R-6 — re-hashed on disk; also a file replaced by a link or behind a linked folder, R20-5); the job is still running or failed; the persisted job record does not verify under the report key | 409 | `검사 뒤 바뀐 파일이 있습니다 — 다시 검사하십시오(보고서를 만들지 않았습니다)` + `changed_rows` `[{path, reason}]` / `검사 작업이 아직 끝나지 않았습니다 — …` / `검사 작업이 실패해 보고서를 만들 수 없습니다 — …` / `검사 작업 기록의 무결성을 확인할 수 없습니다 …` |
| E48 | any path value (both): `folder`, `file`, `path`, `root` of GET `/api/scan`, `/api/analyze-file`, `/api/heatmap`, `/api/preview`; an upload row's `path` / `name` / `container` / `member` / `duplicate_of` / `heatmap_path` of POST `/api/report`; api-serve's `file_path`, `file_path_a`/`_b`, `directory` | the value holds a NUL character (R22-6 — was a 500: `ValueError: embedded null byte` from the file system). Other control characters are legal POSIX file-name characters and are accepted; a `*_b64` value whose bytes hold NUL is E46 | 400 | `경로 값에 NUL 문자(\\x00)가 있습니다 — 파일 시스템 경로가 될 수 없습니다: <field>` (report upload rows: `업로드 항목 N번을 해석할 수 없습니다: …`) |
| E49 | POST `/api/feedback`, POST `/api/review` (both), PUT `/api/artifacts/{id}/review` (api) | R22-3: the examiner-record file (`reviews.json` / `feedback.jsonl` in the per-user application-data folder, or `DEEPFAKE_LENS_REVIEWS` / `DEEPFAKE_LENS_FEEDBACK`) would be inside a folder the server examines, or has no place (relative HOME / APPDATA, a relative override) — nothing is written | 409 | `검토·피드백 기록 파일 …이(가) 검사 대상 폴더(…) 안에 있어 저장하지 않았습니다 — 증거 폴더에는 쓰지 않습니다. …` / `검토·피드백 기록을 저장할 위치를 정할 수 없습니다(…)` |
| E50 | POST `/api/feedback`, POST `/api/review` (both), PUT `/api/artifacts/{id}/review` (api) | R23-7 (round 23): the examiner-record file cannot be written (folder not writable, disk or quota full) or the existing `reviews.json` cannot be read (permissions, not a JSON object — a save would overwrite it) — nothing is changed: the review cache is put back, a half-written feedback line is cut off (a review answered 200 `success` with nothing on disk; feedback was a 500). R24-4 (round 24): the file is read again on the next request after a failed read and whenever it changed on disk (mtime, ctime, size, inode), so repairing it ends the refusals on the running server and keeps its entries (it used to take a restart) | 409 (507 when the disk or quota is full) | `검토·피드백 기록 파일 …에 저장하지 못했습니다(<사유>) — 기록은 바뀌지 않았습니다. …` / `기존 검토 기록 파일 …을(를) 읽지 못해(…) 저장하지 않았습니다 — 덮어쓰면 기존 기록이 사라집니다. …` |

The streaming endpoints (`/api/scan/stream`, `/api/check/stream`) validate
every input before the stream starts (E6, E36, E38–E40, E42–E44): an
`event: error` inside a 200 stream is only a failure that happens while the
analysis runs.

Tests: `test_servers.ErrorTableEveryRowTest` reads this table and sends at
least one request per row ID to each server the row names (`web`, `api`,
`both`), checking the status, the Korean text and that no row of the table is
without a test (and no test without a row). `test_servers.ApiErrorStatusTest`
keeps the round-7 spot checks.

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
