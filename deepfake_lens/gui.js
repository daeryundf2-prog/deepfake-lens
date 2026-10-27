        /* Deepfake Lens GUI — three screens on one page (#/scan, #/ledger,
           #/report) plus the single-file tools (#/tools). The ledger leads
           with the conclusion; detail (coverage reasons, reference signals,
           technical fields) stays reachable behind disclosures. */
        let results = [];
        let lastProvenance = { coverage: null, thresholds: null, summary: null };
        // X2: the options of the last folder scan, kept with its rows (shown state only — since P0R-6 a
        // report takes the options, like the rows, from the server-held scan job; nothing is re-analyzed).
        let lastScanOptions = {};
        // R19-3: the server job whose result is on screen (its cancel count is verified by the server).
        let lastScanJob = '';
        let lastScanJobNote = '';
        let selectedFiles = [];
        let lastScanRoot = '';
        // R12-4: the scan folder as URL-safe base64 file-system bytes (scan_root_b64);
        // previews and heatmaps are requested by bytes — a lone surrogate (a
        // non-UTF-8 file name) cannot go into a URL.
        let lastScanRootB64 = '';
        let lastScanAt = null;
        const RENDER_WINDOW = 200;
        let renderedRows = 0;
        let scanBusy = false;
        let currentJobId = null;
        let bandFilter = null;
        let textFilter = '';
        // D16: default 결론순 (now evidence first: manipulation → authenticity → undetermined → other).
        let sortMode = 'verdict';
        let revFilter = false;
        let selectedEntry = null;
        // U-8: the server's --folder (from /api/stats) and whether PDFs can be made.
        let defaultFolder = '';
        let defaultFolderB64 = '';
        let pdfAvailable = null;
        // DESK-2: the desktop mode (`deepfake-lens desktop`, /api/stats desktop) and whether the page has been shut down.
        let desktopMode = false;
        let appShutDown = false;
        // DESK-3: the registered read roots as /api/stats and /api/roots list them (display text).
        let readRoots = [];
        // DESK-6: the report signing key as /api/stats describes it ({available, source, key_id}), null when not said.
        let signingInfo = null;

        /* Review marks stored locally and synchronized with backend API */
        const REVIEW_KEY = 'dflens-review-v1';
        let reviewStore = {};
        try { reviewStore = JSON.parse(localStorage.getItem(REVIEW_KEY) || '{}'); } catch (e) { reviewStore = {}; }
        // R18-8 (round 18): a review mark (★, memo, examiner verdict) belongs to one
        // file content in one scanned folder. The key was item.path || item.name
        // in one global store, so another case's IMG_0001.jpg showed this case's
        // memo and "검토됨" — also in the JSON/CSV exports. Keys are now
        // "v2|<scope>|sha256:<content hash>" (G11: content, not path), scope = the
        // scanned folder ("root:<scan_root>") or this page's uploads; a row without
        // a hash falls back to its path inside that scope. Pre-v2 keys (bare paths)
        // stay in the stores untouched and are never attached to a row.
        const REVIEW_KEY_VERSION = 'v2';
        const uploadReviewScope = 'upload:' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
        function reviewScope() { return lastScanRoot ? 'root:' + lastScanRoot : uploadReviewScope; }
        function itemKey(item) {
            const sha = typeof item.sha256 === 'string' && /^[0-9a-f]{64}$/.test(item.sha256) ? item.sha256 : '';
            const what = sha ? 'sha256:' + sha : 'path:' + (item.path || item.name || '');
            return `${REVIEW_KEY_VERSION}|${reviewScope()}|${what}`;
        }
        let reviewPushTimer = null;
        function saveReviewStore(changedKey, onSynced) {
            try { localStorage.setItem(REVIEW_KEY, JSON.stringify(reviewStore)); } catch (e) { /* quota — non-fatal */ }
            // Server-side store keeps examiner marks across browsers/devices
            // (chain of custody); localStorage stays as offline fallback.
            // Debounced so per-keystroke note edits send one update.
            if (!changedKey) return;
            clearTimeout(reviewPushTimer);
            reviewPushTimer = setTimeout(async () => {
                const refused = await pushReviewToServer(changedKey, reviewStore[changedKey] || {});
                if (onSynced) onSynced(refused);
            }, 400);
        }
        // Merge of phase0 R22-3: the server refuses to save a review it would write inside an
        // examined folder (409, E49, with a Korean reason). The answer is returned — '' when
        // saved, else the server's message — and said next to the review controls; it was
        // ignored and "서버에 저장됨" was shown whatever the server answered.
        async function pushReviewToServer(key, entry) {
            if (!key) return '';
            try {
                // POST /api/review exists on both the builtin web server and the
                // FastAPI api_server; explicit blank fields let the store prune
                // the entry when the examiner clears every mark.
                const res = await apiFetch('/api/review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Object.assign({artifact_id: key, star: false, note: '', verdict: 'unreviewed'}, entry)),
                });
                return res.ok ? '' : await apiError(res);
            } catch (e) {
                return '서버에 연결하지 못했습니다(' + String(e.message || e) + ')';
            }
        }
        function reviewSyncText(refused) {
            return refused ? '서버에 저장하지 못했습니다: ' + refused + ' — 이 검토 기록은 이 브라우저에만 남아 있습니다.' : '서버에 저장됨';
        }

        async function fetchReviewsFromServer() {
            try {
                const res = await apiFetch('/api/reviews');
                if (res.ok) {
                    const data = await res.json();
                    if (data && data.reviews && typeof data.reviews === 'object') {
                        for (const [k, v] of Object.entries(data.reviews)) {
                            if (!reviewStore[k] || (v.updated_at && (!reviewStore[k].updated_at || v.updated_at > reviewStore[k].updated_at))) {
                                reviewStore[k] = v;
                            }
                        }
                        saveReviewStore();
                    }
                }
            } catch (e) { /* ignore */ }
        }

        function toggleReview(item, onSynced) {
            const key = itemKey(item);
            const entry = reviewStore[key] || {};
            entry.star = !entry.star;
            entry.ts = Date.now();
            if (!entry.star && !entry.note && !entry.verdict) delete reviewStore[key];
            else reviewStore[key] = entry;
            saveReviewStore(key, onSynced);
            return Boolean(entry.star);
        }

        /* ── infra ─────────────────────────────────── */
        const $ = id => document.getElementById(id);
        const sleep = ms => new Promise(res => setTimeout(res, ms));

        // A11Y-6: an error stays on the page (the error region under the top
        // bar) until the examiner closes it — never only a toast that fades.
        function showError(msg, label, fromScan) {
            const region = $('error-region');
            const box = document.createElement('div');
            box.className = 'err-item';
            const text = document.createElement('p');
            text.className = 'err-text';
            const head = document.createElement('b');
            head.textContent = (label || '오류') + ' ';
            const body = document.createElement('span');
            body.textContent = displayName(msg);
            text.append(head, body);
            const close = document.createElement('button');
            close.type = 'button';
            close.className = 'btn btn-sm';
            close.textContent = '닫기';
            close.addEventListener('click', () => {
                const next = box.nextElementSibling || box.previousElementSibling;
                box.remove();
                (next ? next.querySelector('button') : $('main')).focus();
            });
            // GUI-REV5 L-2: export failures and warnings are said next to the export buttons
            // (#export-alert), not here.
            box.append(text, close);
            // GUI-REV2 H-10: only a scan's own errors carry its generation; a quick
            // check or export error is never later marked "이전 검사".
            if (fromScan) box.dataset.scan = String(scanGeneration);
            region.appendChild(box);
            // DESK-5: the lost-connection box stays while the server is gone — only the other boxes are trimmed.
            const trimmable = () => region.querySelectorAll('.err-item:not(#conn-lost)');
            while (trimmable().length > 4) trimmable()[0].remove();
            return box;
        }

        // GUI-REV G-4: an error belongs to the scan it was raised in. When a new
        // scan starts, the earlier ones stay readable but are marked "이전 검사"
        // (they no longer describe the results on screen).
        // GUI-REV7 F-4: the mark is a new aria-hidden node, not a rewrite of the box's title — #error-region is
        // aria-live="assertive" and every title rewrite read the old error out again when a scan started.
        let scanGeneration = 0;
        function retireErrors() {
            scanGeneration += 1;
            document.querySelectorAll('#error-region .err-item[data-scan]').forEach(box => {
                if (box.classList.contains('err-old')) return;
                box.classList.add('err-old');
                const tag = document.createElement('span');
                tag.className = 'err-old-tag';
                tag.setAttribute('aria-hidden', 'true');
                tag.textContent = '이전 검사 · ';
                const text = box.querySelector('.err-text');
                text.insertBefore(tag, text.firstChild);
            });
        }
        // GUI-REV7 F-4: once a new scan's results are on screen (done or cancelled), the earlier scans'
        // errors are removed — they stood above every screen after a successful scan. Errors of this scan
        // and errors that are not a scan's (quick check, export) stay.
        function dropRetiredErrors() {
            const old = Array.from(document.querySelectorAll('#error-region .err-item.err-old'));
            const hadFocus = old.some(box => box.contains(document.activeElement));
            old.forEach(box => box.remove());
            if (hadFocus) $('main').focus();
        }

        function toast(msg, isErr, label, scope) {
            if (isErr) { showError(msg, label, scope === 'scan'); return; }
            const el = document.createElement('div');
            el.className = 'toast';
            el.textContent = displayName(msg);  // R11-10: server messages may echo a name
            $('toasts').appendChild(el);
            setTimeout(() => el.remove(), 4000);
        }

        // A button that is busy keeps its focus: aria-disabled, not disabled (A11Y-4).
        function setBusyButton(el, busy) {
            if (busy) el.setAttribute('aria-disabled', 'true');
            else el.removeAttribute('aria-disabled');
        }
        function isBusyButton(el) { return el.getAttribute('aria-disabled') === 'true'; }

        /* ── token dialog (A11Y-4: modal <dialog> — focus kept inside, returned on close) ── */
        let tokenPromptPending = null;
        function requestToken() {
            if (tokenPromptPending) return tokenPromptPending;
            tokenPromptPending = new Promise(resolve => {
                const modal = $('token-modal'), input = $('token-modal-input'), form = $('token-form');
                const back = document.activeElement;
                let value = null;
                const onSubmit = e => { e.preventDefault(); value = input.value.trim() || null; modal.close(); };
                const onCancel = () => { value = null; modal.close(); };
                const onClose = () => {
                    form.removeEventListener('submit', onSubmit);
                    $('token-modal-cancel').removeEventListener('click', onCancel);
                    modal.removeEventListener('close', onClose);
                    input.value = '';
                    tokenPromptPending = null;
                    if (back && typeof back.focus === 'function') back.focus();
                    resolve(value);
                };
                form.addEventListener('submit', onSubmit);
                $('token-modal-cancel').addEventListener('click', onCancel);
                modal.addEventListener('close', onClose);
                modal.showModal();
                input.focus();
            });
            return tokenPromptPending;
        }

        async function apiFetch(url, options = {}) {
            const token = sessionStorage.getItem('dfl.token') || '';
            const headers = Object.assign(
                { 'X-Deepfake-Lens-Client': 'gui' },
                options.headers || {},
                token ? { 'X-Deepfake-Lens-Token': token } : {}
            );
            let response;
            try {
                response = await fetch(url, Object.assign({}, options, { headers }));
            } catch (e) {
                // TypeError: Failed to fetch → 서버 다운/네트워크 단절을
                // 사용자가 이해할 수 있는 한국어로 바꾼다.
                // REV2-D2: in the desktop mode the server is this PC's program — a request that cannot reach it puts
                // the lost-connection box up at once (not after two missed heartbeats) and says what to do in the
                // same words. An abort (the heartbeat's own timeout) is not a lost connection.
                if (desktopMode && !(e && e.name === 'AbortError')) {
                    markConnectionLost();
                    const lost = new Error(CONNECTION_LOST);
                    lost.connectionLost = true;
                    throw lost;
                }
                throw new Error('서버에 연결할 수 없습니다 — deepfake-lens web 프로세스가 실행 중인지 확인하세요');
            }
            // REV2-D3: any answer while the box is up means the server is back — the heartbeat says so and refreshes.
            if (desktopMode && url !== '/api/heartbeat' && connectionLost(heartbeatFailures)) sendHeartbeat();
            if (response.status === 401) {
                const entered = await requestToken();
                if (entered) {
                    sessionStorage.setItem('dfl.token', entered);
                    return apiFetch(url, options);
                }
            }
            return response;
        }

        // Read an error payload safely — HTML error pages and aborts must
        // never surface as "Unexpected token" / raw English to the examiner.
        async function apiError(res) {
            let detail = '';
            try {
                const ct = res.headers.get('Content-Type') || '';
                if (ct.includes('application/json')) {
                    const j = await res.json();
                    detail = j.error || j.detail || '';
                    // R19-13: a report refused because files changed after the scan (409) names them.
                    if (Array.isArray(j.changed_rows) && j.changed_rows.length) {
                        const paths = j.changed_rows.map(r => String(r.path));
                        detail += ` — 바뀐 파일: ${paths.slice(0, 5).join(', ')}${paths.length > 5 ? ` 외 ${paths.length - 5}개` : ''}`;
                    }
                } else {
                    detail = `서버 오류 (HTTP ${res.status})`;
                }
            } catch (e) {
                detail = `서버 오류 (HTTP ${res.status})`;
            }
            return detail || `요청 실패 (HTTP ${res.status})`;
        }

        async function apiJson(url, options = {}) {
            const res = await apiFetch(url, options);
            if (!res.ok) throw new Error(await apiError(res));
            try {
                return await res.json();
            } catch (e) {
                throw new Error('서버 응답을 해석할 수 없습니다 (JSON이 아님)');
            }
        }

        // R18-3 (round 18): result_text.cancelled_note — the scan was cancelled before every file was analyzed.
        function partialResultNote(summary) {
            if (!summary || summary.cancelled !== true) return '';
            const n = Number.isInteger(summary.files_cancelled) ? summary.files_cancelled : 0;
            return `검사 취소 — 폴더 전체가 아닌 부분 결과입니다: 분석하지 않은 파일 ${n}개(행·해시 없음). 폴더 전체의 결과로 쓰지 마십시오.`;
        }

        // R18-17 (round 18): a model that did not run (available === false) has no score —
        // its 0 was shown as "외부 모델 원점수(미보정, 결론 불참여) — 0", like a measurement.
        // R19-10 (round 19): a layer diagnostic without a score says "없음" — "원점수 0" read as a measured 0.
        function rawScoreText(value) { return value != null ? String(value) : '없음'; }
        function modelScoreText(entry) {
            if (!entry || entry.available === false) {
                const why = entry && (entry.detail || entry.reason || entry.error);
                return `없음 — 미실행(${shown(why || '사유 기록 없음')})`;
            }
            return entry.score != null ? escapeHtml(String(entry.score)) : '없음';
        }

        // GUI-REV7 F-7: the server scans at most analysis_api.WEB_MAX_SCAN_FILES files (a larger 최대 파일 수 is
        // lowered to it without a word); test_gui_rev7 keeps this copy and gui.html's max equal to it.
        const SERVER_MAX_SCAN_FILES = 2000;
        // GUI-REV7 F-7: "'최대 파일 수'를 늘려 다시 검사하십시오" could not be followed at the server's ceiling.
        function overCapAdvice(maxFiles) {
            const asked = Number.parseInt(String(maxFiles == null ? '' : maxFiles), 10);
            if (Number.isInteger(asked) && asked < SERVER_MAX_SCAN_FILES) {
                return `'최대 파일 수'(이번 검사 ${asked}개)를 늘리거나 폴더를 나누어 다시 검사하십시오(서버 상한 ${SERVER_MAX_SCAN_FILES}개).`;
            }
            return `서버 상한 ${SERVER_MAX_SCAN_FILES}개 — 폴더를 나누어 검사하십시오.`;
        }
        // GUI-REV7 F-7: the form is novalidate — a 최대 파일 수 the server would refuse (0, empty: HTTP 400 shown
        // only at the top) or lower (above the ceiling) is said next to the box before the scan is sent.
        function maxFilesProblem(raw) {
            const text = String(raw == null ? '' : raw).trim();
            if (!text) return `최대 파일 수를 입력하십시오(1~${SERVER_MAX_SCAN_FILES}).`;
            if (!/^-?\d+$/.test(text)) return `최대 파일 수는 1~${SERVER_MAX_SCAN_FILES} 사이의 정수여야 합니다.`;
            const n = Number.parseInt(text, 10);
            if (n < 1) return `최대 파일 수는 1 이상이어야 합니다(서버 상한 ${SERVER_MAX_SCAN_FILES}개).`;
            if (n > SERVER_MAX_SCAN_FILES) return `최대 파일 수는 서버 상한 ${SERVER_MAX_SCAN_FILES}개를 넘을 수 없습니다 — 폴더를 나누어 검사하십시오.`;
            return '';
        }
        function showMaxFilesProblem(message) {
            const field = $('max-files'), note = $('max-files-err');
            note.textContent = message;
            note.hidden = !message;
            if (message) {
                field.setAttribute('aria-invalid', 'true');
                field.setAttribute('aria-describedby', 'max-files-err');
            } else {
                field.removeAttribute('aria-invalid');
                field.removeAttribute('aria-describedby');
            }
        }

        // OMO-11: a long caveat keeps its headline in view; the explanation after it (same text) folds behind 자세한 설명.
        function caveatMoreHtml(restHtml) {
            return `<details class="caveat-more"><summary>자세한 설명</summary>${restHtml}</details>`;
        }
        // Headline = up to the first </b>, else the first ' — ', else the first sentence ('. '); nothing to fold when none.
        function foldWarningHtml(html) {
            const bold = html.indexOf('</b>');
            let cut = bold >= 0 ? bold + 4 : html.indexOf(' — ');
            if (cut < 0) { const dot = html.indexOf('. '); cut = dot >= 0 ? dot + 1 : -1; }
            if (cut < 0 || !html.slice(cut).trim()) return `<p>${html}</p>`;
            return `<div class="caveat-item"><p>${html.slice(0, cut)}</p>${caveatMoreHtml(`<p>${html.slice(cut)}</p>`)}</div>`;
        }
        // The verdict advice / one-liner: the first sentence (or clause before ' — ') stays, the rest folds.
        function foldTextHtml(text, escape) {
            const dash = text.indexOf(' — ');
            const dot = text.indexOf('. ');
            const cut = dash >= 0 && (dot < 0 || dash < dot) ? dash : (dot >= 0 ? dot + 1 : -1);
            if (cut < 0) return { head: escape(text), more: '' };
            return { head: escape(text.slice(0, cut)), more: caveatMoreHtml(escape(text.slice(cut))) };
        }
        function adviceHtml(text) {
            const f = foldTextHtml(text, escapeHtml);
            return `<div class="band-advice">${f.head}${f.more}</div>`;
        }
        function provenanceBannerHtml() {
            const cov = lastProvenance.coverage || {};
            const thr = lastProvenance.thresholds || {};
            const parts = [];
            // R18-3: a cancelled scan is a partial result — said first, never shown as a complete folder.
            const cancelledNote = partialResultNote(lastProvenance.summary);
            if (cancelledNote) parts.push(`<b>${shown(cancelledNote)}</b>`);
            const wa = cov.weights_available, wt = cov.weights_total;
            if (wt !== undefined) {
                if ((wa || 0) === 0) parts.push('<b>신경망 미탑재(측정 게이트 미충족) — 재현 가능한 기록 근거만 반영</b> — 탑재된 신경망 가중치가 없어 통계적 근거는 결론에 참여하지 않습니다.');
                else if (wa < wt) parts.push(`신경망 가중치 일부 탑재 (${wa}/${wt}) — 미탑재 엔진의 판단이 빠져 있습니다.`);
            }
            const env = envFacts();
            if (env.gated.length) parts.push(`측정 게이트 때문에 꺼진 신경망 탐지기 ${env.gated.length}종: ${env.gated.map(shown).join(', ')}`);
            env.missing.forEach(([check, n]) => parts.push(`<b>결정 검사 미설치</b> — ${escapeHtml(checkLabel(check))}: 의존성이 없어 ${n}개 파일에서 실행하지 못했습니다. 이 검사가 내는 결론(원본성 근거 등)은 나올 수 없습니다.`));
            if (thr.provisional || thr.source === 'builtin_defaults') {
                parts.push('판정 임계값: <b>미측정 잠정값</b> — 라벨 코퍼스로 보정되기 전에는 임계값을 근거로 쓰지 마십시오.');
            }
            if (thr.in_sample) parts.push('판정 임계값: <b>in-sample(참고)</b> — 적합에 쓴 같은 표본에서 평가된 값이라 감정 근거가 아닙니다.');
            // N8: a non-recursive scan never omits subfolders silently.
            const skippedDirs = (lastProvenance.summary || {}).subfolders_skipped || 0;
            // P5: and the files inside them (recursive count, symlinks excluded).
            const skippedDirFiles = (lastProvenance.summary || {}).subfolder_files_skipped || 0;
            if (skippedDirs) parts.push(`하위 폴더 <b>${skippedDirs}개(안의 파일 ${skippedDirFiles}개)는 검사하지 않았습니다</b> — 포함하려면 '하위 폴더 포함' 옵션을 켜고 다시 검사하십시오.`);
            // X1: files beyond the file-count cap are never silently omitted.
            const overCap = (lastProvenance.summary || {}).files_over_cap || 0;
            if (overCap) parts.push(`파일 수 상한에 도달해 <b>${overCap}개 파일은 검사·기록하지 않았습니다</b> — ${escapeHtml(overCapAdvice(lastScanOptions.max_files))}`);
            if (!parts.length) return '';
            // The closing one-line caveat stays in view: folding it cost a row of its own and hid the verdict limit.
            return `${parts.map(foldWarningHtml).join('')}<p class="note">이 결과는 결론과 근거로 읽으십시오; 점수는 보정된 경우에만 표시됩니다. 유죄·불법성의 확정 판정이 아닙니다.</p>`;
        }

        // P7: inverse of result_text.escape_row_path ("\\:" -> ":", "\\\\" -> "\\").
        function unescapeRowPath(path) {
            if (!path.includes('\\:')) return path;
            let out = '';
            for (let i = 0; i < path.length; i++) {
                if (path[i] === '\\' && i + 1 < path.length && (path[i + 1] === '\\' || path[i + 1] === ':')) {
                    out += path[i + 1];
                    i++;
                } else {
                    out += path[i];
                }
            }
            return out;
        }

        // R10-1/R11-4: a file name as every report shows it (result_text.display_name):
        // controls and invisible characters (CR, LF, TAB, ESC, zero-width,
        // bidi overrides) as escapes, "|" as "\|" and every literal "\" as
        // "\\" — injective, so two different names never look the same.
        function displayName(value) {
            const chars = Array.from(String(value));
            const nonNfc = new Array(chars.length).fill(false);
            let start = 0;
            for (let i = 1; i <= chars.length; i++) {
                if (i === chars.length || !composesWithPrevious(chars[i])) {
                    const run = chars.slice(start, i).join('');
                    // R18-11: result_text._lookalike_positions — a run NFC would change is escaped
                    // whole; otherwise the marks after an escape (or a leading mark) are escaped.
                    if (run.normalize('NFC') !== run) {
                        for (let j = start; j < i; j++) nonNfc[j] = true;
                    } else {
                        let escaped = composesWithPrevious(chars[start]);
                        for (let j = start; j < i; j++) {
                            escaped = escaped || escapedAlone(chars[j]);
                            if (escaped) nonNfc[j] = true;
                        }
                    }
                    start = i;
                }
            }
            let out = '';
            chars.forEach((ch, i) => {
                const code = ch.codePointAt(0);
                const hex = () => code < 0x100 ? '\\x' + code.toString(16).padStart(2, '0')
                    : code < 0x10000 ? '\\u' + code.toString(16).padStart(4, '0')
                    : '\\U' + code.toString(16).padStart(8, '0');
                if (ch === '\\') out += '\\\\';
                else if (ch === '|') out += '\\|';
                else if (ch === '\n') out += '\\n';
                else if (ch === '\r') out += '\\r';
                else if (ch === '\t') out += '\\t';
                else if (nonNfc[i] || invisibleOrSpace(ch) || controlLike(ch)) out += hex();
                else out += ch;
            });
            return out;
        }
        // R18-11 (round 18): result_text.DEFAULT_IGNORABLE_RANGES — escaped like
        // a space other than U+0020 and a run that is not in NFC, so names that
        // render alike ("한" NFC/NFD, NBSP, U+3164) are shown differently.
        const DEFAULT_IGNORABLE_RANGES = [
            [0x00AD, 0x00AD], [0x034F, 0x034F], [0x061C, 0x061C], [0x115F, 0x1160], [0x17B4, 0x17B5],
            [0x180B, 0x180F], [0x200B, 0x200F], [0x202A, 0x202E], [0x2060, 0x206F], [0x3164, 0x3164],
            [0xFE00, 0xFE0F], [0xFEFF, 0xFEFF], [0xFFA0, 0xFFA0], [0xFFF0, 0xFFF8], [0x1BCA0, 0x1BCA3],
            [0x1D173, 0x1D17A], [0xE0000, 0xE0FFF],
        ];
        function composesWithPrevious(ch) {
            const code = ch.codePointAt(0);
            return /\p{M}/u.test(ch) || (code >= 0x1160 && code <= 0x11FF) || (code >= 0xD7B0 && code <= 0xD7FF);
        }
        function controlLike(ch) { return /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\p{Cs}]/u.test(ch); }
        function escapedAlone(ch) { return ch === '\\' || ch === '|' || controlLike(ch) || invisibleOrSpace(ch); }
        function invisibleOrSpace(ch) {
            const code = ch.codePointAt(0);
            // R19-8: U+2800 (renders blank) and private-use characters (\p{Co}) too — as result_text.
            // R20-8: and unassigned code points / noncharacters (\p{Cn}).
            return (/\p{Zs}/u.test(ch) && ch !== ' ') || code === 0x2800 || /\p{Co}|\p{Cn}/u.test(ch)
                || DEFAULT_IGNORABLE_RANGES.some(([low, high]) => code >= low && code <= high);
        }

        // R10-2: a CSV cell that starts with =, +, -, @, TAB or CR is read
        // as a formula by spreadsheets (OWASP CSV injection) — prefixed with "'".
        // R11-13: so is one that starts with a fullwidth/compatibility form of
        // them ("＝", "＋", "－", "＠", "﹦" …: NFKC of the first character).
        function csvCell(value) {
            const raw = String(value);
            let text = displayName(raw);
            const formula = s => /^[=+\-@\t\r]/.test(s) || /^[=+\-@\t\r]/.test(([...s][0] || '').normalize('NFKC'));
            if (formula(raw) || formula(text)) text = "'" + text;
            const quoted = /[",\r\n]/.test(text);
            return quoted ? '"' + text.replace(/"/g, '""') + '"' : text;
        }

        function escapeHtml(value) {
            return String(value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
        }
        // R11-10: every string a result carries (evidence titles and details,
        // coverage reasons, verdict text, limitations, signals, model and
        // source notes) is shown through displayName — controls, bidi and
        // zero-width characters escaped — as the HTML report shows them.
        function shown(value) {
            return escapeHtml(displayName(value));
        }

        /* ── verdict vocabulary ──────────────────── */
        /* Result contract v2: three verdicts, evidence by kind, coverage.
           Statistical (model) and lexical (keyword) evidence never decide;
           a failed check leaves the file undetermined. */
        const VERDICT_LABELS = {
            manipulation_evidence: '조작·생성 근거 있음',
            authenticity_evidence: '원본성 근거 있음',
            undetermined: '판단 불가',
        };
        const KIND_LABELS = { deterministic: '재현 가능한 기록 근거', statistical: '통계적 근거', lexical: '어휘적 근거' };
        const KIND_NOTES = {
            deterministic: '메타데이터·C2PA 등 파일이 스스로 기록한 사실 — 결론을 낼 수 있습니다.',
            statistical: '모델 출력 — 보정(측정 코퍼스·신뢰구간)이 없으면 결론에 참여하지 않습니다.',
            lexical: '키워드·문체 통계 — 결론을 바꾸지 못하는 참고 정보입니다.',
        };
        const DIRECTION_LABELS = { synthetic: '조작·생성 방향', authentic: '원본성 방향', neutral: '중립' };
        const STRENGTH_LABELS = { strong: '강', moderate: '중', weak: '약' };
        const CHECK_LABELS = {
            metadata: '메타데이터', c2pa: 'C2PA 출처 검증', image_class: '이미지 유형 판별(사진/비사진)',
            pixel: '픽셀 휴리스틱(참고)', external_model: '외부 모델',
            face_manipulation: '얼굴 검사', inpaint: '인페인팅 검사', faceswap_seam: '페이스스왑 경계면 검사',
            rppg: 'rPPG 맥박 검사', avatar: '아바타 검사', lipsync: '립싱크 검사', face_track: '얼굴 트랙 검사',
            audio_analysis: '오디오 분석', audio_features: '오디오 특징 추출', video_analysis: '영상 분석', av_audio: '영상 음성 트랙 분석',
            document_text: '문서 텍스트 추출', text_lexical: '어휘·문체 신호', archive: '압축 해제',
            archive_member: '압축 구성 파일', archive_member_name: '압축 구성 파일 이름 구분', file_integrity: '분석 전후 파일 동일성',
            // Merges of phase0 (techniques-2026 P0R-22, techniques-2026b TB): watermark, online-only, container and TB fact checks.
            watermark_sdxl: 'imwatermark 고정 메시지 워터마크 검사(SDXL·FLUX.1·SD 1/2)', watermark_trustmark: 'TrustMark 워터마크 검사',
            watermark_videoseal: 'VideoSeal 워터마크 검사', watermark_audioseal: 'AudioSeal 워터마크 검사', online_detection: '온라인 전용 검출(미실행)', watermark_perth: 'PerTh 워터마크 검사',
            text_invisible_marks: '비가시 유니코드 표식 검사', document_structure: '문서 구조 사실(증분 저장·편집 메타데이터)',
            platform_recompression: '플랫폼 재압축 양자화표 대조', file_name_pattern: '파일 이름 형식(메신저·스크린샷·녹음)', audio_channel_trace: '통화·메신저 코덱·대역폭 흔적', device_ai_label: '기기 AI 편집 라벨(삼성·Google)', screen_capture: '스크린샷 사실(캡처 주석·화면 해상도)',
            container_metadata: '컨테이너·비트스트림 메타데이터 판독',
        };
        const COVERAGE_STATUS_LABELS = { ran: '실행', skipped: '미실행', failed: '실패' };
        // B5: same table as result_types.SOURCE_CONFIDENCE_LABELS; a text or
        // reference-only guess (label "참고: …") is shown as "참고".
        const SOURCE_CONFIDENCE_LABELS = { unknown: '알 수 없음', low: '낮음', medium: '중간', high: '높음' };
        const REFERENCE_SOURCE_PREFIX = '참고: ';
        function sourceConfidenceLabel(r, sg) {
            if (String(sg.label || '').startsWith(REFERENCE_SOURCE_PREFIX) || (r && r.grade === 'reference')) return '참고';
            return SOURCE_CONFIDENCE_LABELS[sg.confidence] || SOURCE_CONFIDENCE_LABELS.unknown;
        }
        const TEXT_LEGAL_LIMITATION = '텍스트 생성 여부 판별은 2026년 현재 증거능력이 없으며 참고 정보입니다.';

        function verdictOf(item) {
            const r = (item && item.result) || null;
            // R5: same rule as result_types.is_verdict_row — container rows count by verdict.
            if (!r || ['failed', 'unsupported', 'duplicate', 'skipped'].includes(item.status)) return 'other';
            return r.verdict_code || 'undetermined';
        }
        // R4: profile name -> Korean display_name, learned from model_analysis.models.
        const MODEL_DISPLAY_NAMES = {};
        function learnModelNames(r) {
            const members = ((r || {}).model_analysis || {}).models || [];
            members.forEach(m => { if (m && m.model && m.display_name) MODEL_DISPLAY_NAMES[m.model] = m.display_name; });
        }
        function checkLabel(check) {
            if (String(check).startsWith('model:')) {
                const name = String(check).slice(6);
                return `외부 모델(${MODEL_DISPLAY_NAMES[name] || name})`;
            }
            return CHECK_LABELS[check] || check;
        }
        function coverageText(entry) {
            const head = `${checkLabel(entry.check)} ${COVERAGE_STATUS_LABELS[entry.status] || entry.status}`;
            return entry.reason ? `${head}: ${entry.reason}` : head;
        }
        function verdictAdvice(verdict, grade) {
            if (grade === 'reference') return '텍스트 결과는 참고 등급입니다 — 어휘·문체 신호는 사람이 쓴 글에도 나타나므로 결론으로 쓰지 마세요.';
            if (verdict === 'manipulation_evidence') return '재현 가능한 기록 근거(메타데이터·C2PA 등)가 조작·생성을 가리킵니다 — 근거 항목과 원본 파일을 대조해 감정서에 인용하세요.';
            if (verdict === 'authenticity_evidence') return '재현 가능한 기록 근거가 원본성을 가리킵니다 — 서명자·해시 검증 내역을 감정서에 함께 기재하세요.';
            return '결론을 낼 재현 가능한 기록 근거가 없거나 검사가 실패했습니다 — "원본"이라는 뜻이 아닙니다. 아래 검사 범위의 미실행·실패 사유를 확인하세요.';
        }
        function evidenceHtml(r) {
            const ev = r.evidence || [];
            if (!ev.length) return '<div class="dgroup ev-group"><div class="dt">근거</div><div class="note">근거 항목 없음</div></div>';
            return ['deterministic', 'statistical', 'lexical'].map(kind => {
                const rows = ev.filter(e => e.kind === kind);
                if (!rows.length) return '';
                const lis = rows.map(e => {
                    const prob = (e.probability != null && e.calibration_id)
                        ? ` · p=${Number(e.probability).toFixed(2)}${e.probability_ci ? ` (95% CI ${Number(e.probability_ci[0]).toFixed(2)}–${Number(e.probability_ci[1]).toFixed(2)})` : ''} · 보정 ${shown(e.calibration_id)}`
                        : '';
                    return `<li class="ev-item ev-${escapeHtml(e.direction)}"><b>${shown(e.title)}</b> <span class="ev-badge">${escapeHtml(DIRECTION_LABELS[e.direction] || e.direction)}·${escapeHtml(STRENGTH_LABELS[e.strength] || e.strength)}${prob}</span><div class="note">${shown(e.detail || '')}</div></li>`;
                }).join('');
                return `<div class="dgroup ev-group ev-kind-${kind}" data-kind="${kind}"><div class="dt">${KIND_LABELS[kind]} (${rows.length})</div><div class="note">${KIND_NOTES[kind]}</div><ul>${lis}</ul></div>`;
            }).join('');
        }
        function coverageHtml(r) {
            learnModelNames(r);
            const cov = r.coverage || [];
            if (!cov.length) return '';
            const failed = cov.filter(c => c.status === 'failed');
            const skipped = cov.filter(c => c.status === 'skipped');
            const ran = cov.filter(c => c.status === 'ran');
            const li = (c, cls) => `<li class="${cls}">${shown(coverageText(c))}</li>`;
            return `<div class="dgroup cov-group"><div class="dt">검사 범위 — 실행 ${ran.length} · 미실행 ${skipped.length} · 실패 ${failed.length}</div><ul>` +
                failed.map(c => li(c, 'cov-failed')).join('') +
                skipped.map(c => li(c, 'cov-skipped')).join('') +
                (ran.length ? `<li class="cov-ran">실행: ${ran.map(c => shown(checkLabel(c.check))).join(', ')}</li>` : '') +
                '</ul></div>';
        }
        function verdictHeadHtml(r) {
            const v = r.verdict_code || 'undetermined';
            // U-2: the grade belongs to the modality — "참고" for text; "감정 근거로
            // 사용 가능" only beside a conclusion, never beside 판단 불가.
            const grade = r.grade === 'reference' ? '참고' : (v === 'undetermined' ? '' : '감정 근거로 사용 가능');
            const parts = [`<div class="verdict-head v-${escapeHtml(v)}" data-verdict="${escapeHtml(v)}"><span class="band-pill band-${bandCls(v)}">${escapeHtml(VERDICT_LABELS[v] || v)}</span>${grade ? ` <span class="grade-pill">${escapeHtml(grade)}</span>` : ''}</div>`];
            if (r.grade === 'reference') parts.push(`<div class="legal-note">${escapeHtml(TEXT_LEGAL_LIMITATION)}</div>`);
            if (r.verdict) parts.push(`<div class="verdict">${shown(r.verdict)}</div>`);
            return parts.join('');
        }

        function riskLabel(verdict) {
            return VERDICT_LABELS[verdict] || '미지원·실패';
        }
        // Verdict -> colour class: red = manipulation evidence, blue =
        // authenticity, neutral grey = undetermined (A11Y-1: never orange).
        const VERDICT_CLS = { manipulation_evidence: 'high', undetermined: 'medium', authenticity_evidence: 'low' };
        function bandCls(verdictOrBand) {
            if (VERDICT_CLS[verdictOrBand]) return VERDICT_CLS[verdictOrBand];
            return verdictOrBand === 'high' || verdictOrBand === 'medium' || verdictOrBand === 'low' ? verdictOrBand : 'other';
        }

        function listItems(title, values, cls) {
            if (!values || !values.length) return '';
            const rows = values.map(v => {
                const text = typeof v === 'string' ? v : (v.title ? `${v.title}: ${v.detail || ''}` : JSON.stringify(v));
                return `<li>${shown(text)}</li>`;
            }).join('');
            return `<div class="dgroup ${cls || ''}"><div class="dt">${title}</div><ul>${rows}</ul></div>`;
        }

        // U-10: the measurement-gate reason was repeated once per model; members
        // that share one score text are listed once with their names.
        function modelMembersHtml(ma) {
            const members = ma.models || [];
            const texts = members.map(m => modelScoreText(m));
            // A member's reason repeats its own name ("<이름>: 0단계: 측정 게이트 …") — compared without it.
            const keys = members.map((m, i) => texts[i].split(shown(m.display_name || m.model || '') + ': ').join(''));
            if (members.length > 1 && keys.every(k => k === keys[0])) {
                return `<div class="note">${members.length}종 모두 원점수 ${keys[0]}</div><div class="names">${members.map(m => shown(m.display_name || m.model || '모델')).join(', ')}</div>`;
            }
            return `<ul>${members.map(m => `<li>${shown(m.display_name || m.model || '모델')} — 원점수 ${modelScoreText(m)}</li>`).join('')}</ul>`;
        }

        /* ── ledger vocabulary ───────────────────── */
        const VERDICT_MARK = { manipulation_evidence: 'm', authenticity_evidence: 'a', undetermined: 'u', other: 'x' };
        const VERDICT_SHORT = { manipulation_evidence: '조작·생성 근거 있음', authenticity_evidence: '원본성 근거 있음', undetermined: '판단 불가', other: '분석 못 함' };
        // U-9: a row that was not analyzed says why — 미지원 is not 분석 실패.
        // Merge of phase0 R24-1: "cancelled" — an archive a cancel left (partly) unanalyzed; still a verdict row
        // (the roll-up of the members that were analyzed), labelled as result_types.STATUS_LABELS does.
        const STATUS_LABELS = { unsupported: '미지원 형식', failed: '처리 실패', duplicate: '중복', skipped: '건너뜀', cancelled: '검사 취소' };
        // R24-1: the container's own words for the cancel ("검사 취소 — 구성 파일 N개 미분석(부분 결과).").
        function cancelledContainerText(item) {
            if (!item || item.status !== 'cancelled') return '';
            const said = String(((item.result || {}).verdict) || '').match(/검사 취소 — 구성 파일 \d+개 미분석\(부분 결과\)\./);
            return said ? said[0] : '검사 취소 — 구성 파일 일부 미분석(부분 결과).';
        }
        const MEDIA_KIND_LABELS = { image: '이미지', video: '영상', audio: '음성', text: '텍스트', document: '문서', archive: '압축 파일', unsupported: '알 수 없는 형식' };
        const SCOPE_REASON = /^(측정 범위 밖|사진 아님)/;

        function itemBand(entry) {
            return verdictOf(entry.item);
        }
        function failedChecks(item) {
            return (((item || {}).result || {}).coverage || []).filter(c => c.status === 'failed');
        }
        function coverageCounts(r) {
            const cov = (r && r.coverage) || [];
            return { ran: cov.filter(c => c.status === 'ran').length, failed: cov.filter(c => c.status === 'failed').length, total: cov.length };
        }
        function scopeReason(r) {
            const hit = ((r && r.coverage) || []).find(c => c.status === 'skipped' && SCOPE_REASON.test(String(c.reason || '')));
            return hit ? String(hit.reason) : '';
        }
        // GUI-REV G-2/G-3: why a 판단 불가 row is undetermined — one kind per row:
        // reference (text, 참고 전용), failed (a check failed), cue (it carries
        // record evidence that does not reach a conclusion — never "근거 없음"),
        // none (no record evidence).
        // GUI-REV2 H-6: one primary kind per row for counting and ordering —
        // cue first (a cue is never hidden behind a failure or the reference
        // grade), then an unverified record (a C2PA manifest whose signature was
        // not confirmed), failed, reference, none. The row label shows every
        // aspect that applies ("단서 1건 · 검사 실패").
        const UNDET_KINDS = { cue: '단서 있음', c2pa: '확인 안 된 기록', none: '근거 없음', failed: '검사 실패', reference: '참고 전용' };
        // GUI-REV3 J-9: one order everywhere — headline, strip and ledger sort.
        const UNDET_SPLIT = ['cue', 'c2pa', 'failed', 'none', 'reference'];
        function cueEvidence(r) {
            // GUI-REV4 K-6: a cue is record evidence that did NOT reach a conclusion — on a row
            // with a conclusion the same evidence is decisive, never a "단서".
            if (!r || (r.verdict_code || 'undetermined') !== 'undetermined') return [];
            return (r.evidence || []).filter(e => e.kind === 'deterministic' && e.direction !== 'neutral');
        }
        function unverifiedRecords(r) {
            return ((r && r.evidence) || []).filter(e => e.kind === 'deterministic' && e.direction === 'neutral' && e.layer === 'c2pa');
        }
        function undeterminedKind(item) {
            const r = item.result || {};
            if (cueEvidence(r).length) return 'cue';
            if (unverifiedRecords(r).length) return 'c2pa';
            if (failedChecks(item).length) return 'failed';
            if (r.grade === 'reference') return 'reference';
            return 'none';
        }
        function undeterminedAspects(item) {
            const r = item.result || {};
            const parts = [];
            const cues = cueEvidence(r).length;
            if (cues) parts.push(`단서 ${cues}건`);
            if (!cues && unverifiedRecords(r).length) parts.push('확인 안 된 기록');
            if (failedChecks(item).length) parts.push('검사 실패');
            if (r.grade === 'reference') parts.push('참고 전용');
            return parts.length ? parts : ['근거 없음'];
        }
        // A decisive check (metadata, C2PA, image type) that did not run for a
        // reason other than the file's scope — e.g. a missing dependency.
        // GUI-REV7 F-14 (display only): a check's reason can say one thing three times — "C2PA SDK가 지원하지 않는
        // 형식: C2PA SDK 오류(NotSupported): 지원하지 않는 형식" (core's text, the SDK error class, the SDK's message). On
        // the ledger row a ": " part already contained in an earlier part is left out, and so is an error-class
        // part ("… 오류(Kind)") whose message was left out; the full reason stays in the detail panel.
        function foldReason(text) {
            const parts = String(text).split(': ');
            if (parts.length < 2) return String(text);
            const drop = parts.map(() => false);
            parts.forEach((part, i) => {
                const said = part.trim();
                drop[i] = Boolean(said) && parts.slice(0, i).some((earlier, j) => !drop[j] && earlier.includes(said));
            });
            // an error class whose message (every part after it) was left out says nothing of its own
            parts.forEach((part, i) => {
                if (/오류\([A-Za-z_]+\)$/.test(part.trim()) && i + 1 < parts.length && drop.slice(i + 1).every(Boolean)) drop[i] = true;
            });
            return parts.filter((part, i) => !drop[i]).join(': ');
        }
        function decisiveNotRun(r) {
            return ((r && r.coverage) || []).filter(c => c.status === 'skipped' && DECISIVE_CHECKS.includes(c.check) && !SCOPE_REASON.test(String(c.reason || '')));
        }
        // GUI-REV3 J-2: a duplicate row (dedupe) is not analyzed itself — it shows the
        // conclusion of the row it duplicates, so a copy of a manipulated file is never
        // just "중복". (Its strip group stays 분석 못 함, as the scan summary counts it.)
        let originIndex = { list: null, map: new Map() };
        function originOf(item) {
            if (!item || !item.duplicate_of) return null;
            if (originIndex.list !== results) {
                originIndex = { list: results, map: new Map(results.map(e => [e.item.path, e.item])) };
            }
            return originIndex.map.get(item.duplicate_of) || null;
        }
        function shortVerdict(item) {
            const v = verdictOf(item);
            const origin = item.status === 'duplicate' ? originOf(item) : null;
            if (origin) return '분석 못 함 — 중복 · 원본: ' + VERDICT_SHORT[verdictOf(origin)];
            if (v === 'other') return '분석 못 함 — ' + (STATUS_LABELS[item.status] || '기타');
            const cancelled = item.status === 'cancelled' ? ' · 검사 취소(부분 결과)' : '';
            if (v === 'undetermined') return '판단 불가 — ' + undeterminedAspects(item).join(' · ') + cancelled;
            return VERDICT_SHORT[v] + (restsOnSelfDeclared(item) ? ' · 자기 선언 기록' : '') + cancelled;
        }
        function decisiveEvidence(r, v) {
            // The direction that carries this verdict, by its label (조작·생성 방향 / 원본성 방향).
            const dir = v === 'manipulation_evidence' ? '조작·생성 방향' : v === 'authenticity_evidence' ? '원본성 방향' : '';
            const order = { strong: 0, moderate: 1, weak: 2 };
            return ((r && r.evidence) || [])
                .filter(e => e.kind === 'deterministic' && (dir ? DIRECTION_LABELS[e.direction] === dir : e.direction !== 'neutral'))
                .sort((a, b) => (order[a.strength] ?? 3) - (order[b.strength] ?? 3));
        }
        // GUI-REV6 M-1: the evidence that carries a conclusion, by the server's own rule
        // (evidence_rules.conclusion_caveats): a strong record, or a calibrated model for manipulation.
        function decidingEvidence(r, v) {
            const ev = (r && r.evidence) || [];
            if (v === 'manipulation_evidence') {
                return ev.filter(e => DIRECTION_LABELS[e.direction] === '조작·생성 방향' && ((e.kind === 'deterministic' && e.strength === 'strong') || (e.kind === 'statistical' && e.calibration_id)));
            }
            if (v === 'authenticity_evidence') return ev.filter(e => DIRECTION_LABELS[e.direction] === '원본성 방향' && e.kind === 'deterministic' && e.strength === 'strong');
            return [];
        }
        // A conclusion that rests only on records the file wrote about itself (P0R-12 self_declared) — anyone can
        // plant or strip them; said wherever the conclusion is said, not only next to the evidence item.
        function restsOnSelfDeclared(item) {
            const v = verdictOf(item);
            const deciding = decidingEvidence(item && item.result, v);
            return deciding.length > 0 && deciding.every(e => e.self_declared);
        }
        const SELF_DECLARED_MARK = '자기 선언 — 심거나 지울 수 있음';
        // One line per row: why this row has its conclusion.
        function rowReason(item) {
            // R19-13: a row the server found changed since the scan says so first.
            if (changedPaths.has(String(item.path))) return '검사 뒤 바뀜 — 다시 검사 · ' + rowReasonOf(item);
            // R24-1 merge: a cancelled archive says so first — its conclusion covers only the members analyzed.
            const cancelled = cancelledContainerText(item);
            return cancelled ? cancelled + ' · ' + rowReasonOf(item) : rowReasonOf(item);
        }
        function rowReasonOf(item) {
            const r = item.result;
            const v = verdictOf(item);
            if (v === 'other' || !r) {
                if (item.status === 'duplicate' && item.duplicate_of) {
                    const origin = originOf(item);
                    return '같은 내용의 파일: ' + item.duplicate_of + (origin ? ' — 원본 결론: ' + shortVerdict(origin) : '');
                }
                return item.error || STATUS_LABELS[item.status] || '분석하지 않음';
            }
            if (v === 'manipulation_evidence' || v === 'authenticity_evidence') {
                const top = decisiveEvidence(r, v)[0];
                // M-1: the self-declared mark follows the title — the detail after it is cut short in the row.
                const title = top && top.self_declared ? top.title + ' (' + SELF_DECLARED_MARK + ')' : (top && top.title);
                if (top) return top.detail ? title + ' — ' + top.detail : title;
                return VERDICT_SHORT[v];
            }
            // H-6: every aspect that applies, failure first, then the cue, then the rest.
            const parts = [];
            const failed = failedChecks(item);
            if (failed.length) parts.push('검사 실패 — ' + failed.map(c => checkLabel(c.check)).join(', '));
            // G-3: a decisive check that did not run leads, ahead of the file's scope.
            const notRun = decisiveNotRun(r);
            // GUI-REV7 F-14: the row folds a reason's repeated phrases (foldReason); the detail panel keeps it whole.
            if (notRun.length) parts.push('결정 검사 미실행 — ' + notRun.map(c => checkLabel(c.check) + ': ' + (c.reason ? foldReason(c.reason) : '사유 기록 없음')).join('; '));
            const cues = cueEvidence(r);
            if (cues.length) parts.push(`결론에 못 미친 근거 ${cues.length}건 — ` + cues[0].title);
            const unverified = unverifiedRecords(r);
            if (!cues.length && unverified.length) parts.push('확인 안 된 기록 — ' + unverified[0].title);
            if (r.grade === 'reference' && !parts.length) return '참고 전용 — 텍스트 생성 판별은 결론을 내지 않음';
            if (parts.length) return parts.join(' · ');
            const scope = scopeReason(r);
            if (scope) return '근거 없음 · ' + scope;
            return '근거 없음 — 원본이라는 뜻 아님';
        }
        function verdictLine(item) {
            // R24-1 merge: the partial roll-up of a cancelled archive is said before its conclusion.
            const cancelled = cancelledContainerText(item);
            return cancelled ? cancelled + ' ' + verdictLineOf(item) : verdictLineOf(item);
        }
        // The heading holds the first sentence of the verdict line; a second sentence folds right under it.
        function verdictLineHeadHtml(item, tag) {
            const f = foldTextHtml(verdictLine(item), shown);
            const open = tag === 'h2' ? 'h2 id="detail-h" tabindex="-1"' : tag;
            return `<${open}>${f.head}</${tag.split(' ')[0]}>${f.more}`;
        }
        function verdictLineOf(item) {
            const v = verdictOf(item);
            const r = item.result || {};
            // M-1: a conclusion resting on self-declared metadata says so in its own sentence (as the server's verdict does).
            const declared = restsOnSelfDeclared(item) ? ' 다만 그 기록은 파일이 스스로 적은 메타데이터라 누구나 심거나 지울 수 있습니다.' : '';
            if (v === 'manipulation_evidence') return '이 파일의 기록에 조작·생성을 가리키는 근거가 있습니다.' + declared;
            if (v === 'authenticity_evidence') return '이 파일의 기록에 원본성을 가리키는 근거가 있습니다.' + declared;
            if (v === 'other') {
                return { unsupported: '지원하지 않는 형식이라 분석하지 않았습니다.', failed: '파일을 처리하지 못했습니다.',
                         duplicate: '같은 내용의 파일을 이미 검사했습니다.', skipped: '검사를 건너뛰었습니다.' }[item.status] || '분석하지 못했습니다.';
            }
            const kind = undeterminedKind(item);
            const failed = failedChecks(item).length > 0;
            if (kind === 'cue') return failed ? '단서가 있지만 검사가 실패해 결론을 내리지 않았습니다.' : '기록에 단서가 있지만 결론을 낼 만큼은 아닙니다.';
            if (kind === 'c2pa') return '서명을 확인하지 못한 출처 기록이 있습니다 — 결론을 내리지 않았습니다.';
            if (kind === 'failed') return '검사가 실패해 결론을 내리지 않았습니다.';
            if (kind === 'reference') return '텍스트는 참고 정보만 제공합니다 — 결론을 내지 않습니다.';
            if (decisiveNotRun(r).length) return '결정 검사를 실행하지 못해 결론을 내리지 않았습니다.';
            return '판단할 근거를 찾지 못했습니다.';
        }
        function noEvidenceText(item) {
            const v = verdictOf(item);
            const r = item.result || {};
            if (v === 'other') return item.error || STATUS_LABELS[item.status] || '분석하지 않았습니다.';
            if (failedChecks(item).length) return '검사가 실패해 근거를 확인하지 못했습니다. 아래 실패한 검사를 보십시오.';
            if (r.grade === 'reference') return TEXT_LEGAL_LIMITATION;
            const scope = scopeReason(r);
            if (scope) return scope + ' — 생성 탐지를 적용하지 않았고, 결론을 낼 기록도 없었습니다.';
            return '결론을 낼 기록(메타데이터·C2PA 서명 등)을 찾지 못했습니다.';
        }
        function limitAdvice(item) {
            const v = verdictOf(item);
            if (v === 'manipulation_evidence') return '기록이 있다는 사실을 보여 줍니다. 누가 언제 만들었는지는 이 기록만으로 알 수 없습니다.';
            if (v === 'authenticity_evidence') return '기록된 서명 이후 내용이 바뀌지 않았다는 뜻입니다. 촬영 장면 자체가 연출되었는지는 알 수 없습니다.';
            if (v === 'other') return '이 파일의 내용에 대해서는 아무것도 말하지 않습니다.';
            // H-6: the wording follows the kind — a cue row is not "근거가 없다".
            const kind = undeterminedKind(item);
            if (kind === 'cue') return '단서는 결론에 못 미치는 기록입니다 — 조작·생성되었다는 뜻도, 원본이라는 뜻도 아닙니다.';
            if (kind === 'c2pa') return '서명을 확인하지 못한 기록은 원본성의 근거가 아닙니다 — 원본이라는 뜻도, 조작이라는 뜻도 아닙니다.';
            if (kind === 'failed') return '검사가 실패해 확인하지 못한 것이 있습니다 — 원본이라는 뜻이 아닙니다.';
            return '근거가 없다는 것은 원본이라는 뜻이 아닙니다.';
        }
        function fmtSize(bytes) {
            const n = Number(bytes);
            if (!Number.isFinite(n) || n < 0) return '';
            if (n < 1024) return n + ' B';
            if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
            if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
            return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
        }
        function metaLine(item) {
            const sha = typeof item.sha256 === 'string' && /^[0-9a-f]{64}$/.test(item.sha256) ? item.sha256 : '';
            const parts = [sha ? 'sha256 ' + sha.slice(0, 4) + '…' + sha.slice(-4) : '해시 없음'];
            const size = fmtSize(item.size_bytes);
            if (size) parts.push(size);
            parts.push(MEDIA_KIND_LABELS[item.kind] || String(item.kind || ''));
            if (item.member) parts.push('압축 구성 파일');
            if (item.source === 'upload') parts.push('올린 파일');
            return parts.filter(Boolean).join(' · ');
        }

        /* ── environment notice (derived from the scan itself) ── */
        const DECISIVE_CHECKS = ['metadata', 'c2pa', 'image_class'];
        function envFacts() {
            const gated = new Set();
            const missing = {};
            results.forEach(entry => {
                const r = entry.item.result;
                if (!r) return;
                (r.coverage || []).forEach(c => {
                    const reason = String(c.reason || '');
                    const isModel = String(c.check).startsWith('model:') || c.check === 'external_model';
                    if (isModel && c.status === 'skipped' && reason.includes('측정 게이트')) {
                        const m = /^모델 실행 불가: (.+?): /.exec(reason);
                        gated.add(m ? m[1] : checkLabel(c.check));
                    }
                    // U-6: a decisive check that could not run for a missing dependency.
                    if (c.status !== 'ran' && DECISIVE_CHECKS.includes(c.check) && /의존성|설치/.test(reason)) {
                        missing[c.check] = (missing[c.check] || 0) + 1;
                    }
                });
            });
            const cov = lastProvenance.coverage || {};
            const thr = lastProvenance.thresholds || {};
            const summary = lastProvenance.summary || {};
            const short = [];
            const neuralOff = gated.size > 0 || (cov.weights_total !== undefined && !(cov.weights_available > 0));
            if (gated.size) short.push(`신경망 탐지기 ${gated.size}종은 측정 기준에 못 미쳐 꺼져 있습니다`);
            else if (neuralOff) short.push('신경망 탐지기는 꺼져 있습니다(측정 게이트 미충족)');
            Object.keys(missing).forEach(check => short.push(`${checkLabel(check)} 미설치 — ${missing[check]}개 파일에서 실행 못 함`));
            const notScanned = (summary.subfolder_files_skipped || 0) + (summary.files_over_cap || 0);
            if (notScanned || summary.subfolders_skipped) short.push(`검사하지 않은 파일 ${notScanned}개(하위 폴더·파일 상한)`);
            if (!short.length && (thr.in_sample || thr.provisional)) short.push('판정 임계값은 참고용입니다');
            if (neuralOff) short.push('결론은 파일에 남은 기록만으로 냈습니다');
            return { short, gated: [...gated], missing: Object.entries(missing) };
        }

        // GUI-REV G-9(c): the preview's "검사의 한계" from the scan's own limits —
        // never "특이 사항 없음" while thresholds are in-sample or checks failed.
        function reportLimitLines() {
            // The specific threshold lines below replace the notice's short one.
            const lines = envFacts().short.filter(line => line !== '판정 임계값은 참고용입니다');
            const thr = lastProvenance.thresholds || {};
            if (thr.in_sample) lines.push('판정 임계값은 적합에 쓴 같은 표본에서 평가된 값(in-sample)이라 감정 근거가 아님');
            if (thr.provisional || thr.source === 'builtin_defaults') lines.push('판정 임계값은 미측정 잠정값');
            // K-6: only 판단 불가 rows are "판단 불가"; a row with a conclusion and a failed check
            // is said separately.
            const undetermined = results.filter(e => itemBand(e) === 'undetermined');
            const failed = undetermined.filter(e => failedChecks(e.item).length).length;
            const failedCue = undetermined.filter(e => failedChecks(e.item).length && cueEvidence(e.item.result).length).length;
            if (failed) lines.push(`검사가 실패한 파일 ${failed}개` + (failedCue ? `(그중 단서가 있는 ${failedCue}개)` : '') + ' — 해당 파일은 판단 불가');
            const concludedFailed = results.filter(e => (itemBand(e) === 'manipulation_evidence' || itemBand(e) === 'authenticity_evidence') && failedChecks(e.item).length).length;
            if (concludedFailed) lines.push(`결론은 났지만 일부 검사 실패 ${concludedFailed}개 — 실패한 검사는 근거에 쓰이지 않음`);
            if (results.some(e => (e.item.result || {}).grade === 'reference')) lines.push(TEXT_LEGAL_LIMITATION);
            const partial = partialResultNote(lastProvenance.summary);
            if (partial) lines.push(partial);
            if (!results.length) return ['검사 결과 없음'];
            return lines.length ? lines : ['기록된 검사 한계 없음 — 결론은 파일에 남은 기록에만 근거함'];
        }

        /* ── views ───────────────────────────────── */
        const VIEWS = ['scan', 'ledger', 'report', 'tools'];
        const VIEW_TITLES = { scan: '검사', ledger: '사건 대장', report: '감정서 만들기', tools: '빠른 검사·비교' };
        const VIEW_HEADINGS = { scan: 'scan-h', report: 'rep-h', tools: 'tools-h' };
        let currentView = null;
        let pendingFocus = null;
        function viewFromHash() {
            const m = /^#\/(\w+)/.exec(location.hash);
            return m && VIEWS.includes(m[1]) ? m[1] : null;
        }
        function viewHeading(view) {
            if (view === 'ledger') return results.length ? $('sum-h') : $('ledger-empty-h');
            return $(VIEW_HEADINGS[view]);
        }
        function navigate(view, focusTarget) {
            if (viewFromHash() !== view) {
                pendingFocus = focusTarget || null;
                location.hash = '#/' + view;  // hashchange shows the view
                return;
            }
            showView(view, focusTarget);
        }
        function showView(view, focusTarget, initial) {
            currentView = view;
            VIEWS.forEach(v => { $('view-' + v).hidden = v !== view; });
            document.querySelectorAll('[data-view-link]').forEach(a => {
                if (a.dataset.viewLink === view) a.setAttribute('aria-current', 'page');
                else a.removeAttribute('aria-current');
            });
            // The one set of case fields lives in the screen that shows it.
            const slot = view === 'report' ? $('case-slot-report') : $('case-slot-scan');
            if ($('case-fields').parentElement !== slot) slot.appendChild($('case-fields'));
            if (view === 'report') renderReport();
            updateBusyBand();
            document.title = VIEW_TITLES[view] + ' — ' + document.querySelector('.brand-name').textContent;
            if (!initial) {
                const target = focusTarget || viewHeading(view);
                if (target) target.focus();
            }
        }
        // GUI-REV7 F-1: a hash that names no screen (the skip link's #main, an empty hash after
        // 뒤로) leaves the screen as it is — it showed the ledger (or 검사) and hid the screen the
        // examiner was on, e.g. 감정서 만들기.
        window.addEventListener('hashchange', () => {
            const view = viewFromHash();
            const focusTarget = pendingFocus;
            pendingFocus = null;
            if (!view) return;
            showView(view, focusTarget);
        });
        // GUI-REV7 F-1: "본문으로 건너뛰기" moves the focus to <main> without changing the hash
        // (href="#main" stays for a page without scripts).
        document.querySelector('.skip-link').addEventListener('click', event => {
            event.preventDefault();
            $('main').focus();
        });

        /* ── case information (one set of fields; kept for this tab) ── */
        const CASE_FIELDS = { 'case-no': 'case_no', 'case-name': 'case_name',
                              'case-plaintiff': 'plaintiff', 'case-defendant': 'defendant',
                              'case-court': 'court', 'case-firm': 'law_firm',
                              'case-center': 'center', 'case-contact': 'contact' };
        const CASE_STORE = 'dfl.case';
        function caseValue(id) { return $(id).value.trim(); }
        // GUI-REV G-9: the submitting side has no default — the examiner picks it.
        const SIDES = { plaintiff: ['원고·고소인 측', '갑'], defendant: ['피고·피의자 측', '을'] };
        function submitterSide() {
            const on = document.querySelector('input[name="submitter-side"]:checked');
            return on ? on.value : '';
        }
        // GUI-REV2 H-8: the case fields are kept for the tab, so they are tied to the
        // folder they were entered (or confirmed) for; another folder asks again.
        let caseFolder = '';
        // J-4: each upload (or archive quick check) is its own scope, not one shared 'upload'.
        let lastUploadScope = '';
        function caseScope() { return lastScanRoot ? 'root:' + lastScanRoot : (results.length ? lastUploadScope || 'upload' : ''); }
        function caseHasValues() { return Object.keys(CASE_FIELDS).some(id => caseValue(id)) || Boolean(submitterSide()); }
        function caseNeedsConfirm(folder, scope, hasValues) {
            return Boolean(hasValues && folder && scope && folder !== scope);
        }
        function caseUnconfirmed() { return caseNeedsConfirm(caseFolder, caseScope(), caseHasValues()); }
        function confirmCaseFolder() {
            caseFolder = caseScope();
            saveCase();
        }
        // GUI-REV3 J-4: saving never rebinds the case to the current folder — only the
        // "이 폴더의 사건이 맞음" button or a new 사건번호/사건명 does (editing 법무법인 or
        // the side silently cleared the H-8 confirmation).
        function saveCase() {
            const out = {};
            Object.keys(CASE_FIELDS).forEach(k => { out[k] = $(k).value; });
            out.side = submitterSide();
            out.folder = caseFolder;
            try { sessionStorage.setItem(CASE_STORE, JSON.stringify(out)); } catch (e) { /* storage full — non-fatal */ }
        }
        function updateCaseLine() {
            const no = caseValue('case-no'), name = caseValue('case-name');
            $('case-line-title').textContent = no || name ? [no, name].filter(Boolean).join(' · ') : '사건 정보 없음';
            const meta = [];
            if (results.length) {
                // The folder's last name only; the full path is in the report preview and exports.
                if (lastScanRoot) meta.push(displayName(lastScanRoot.split(/[\\/]/).filter(Boolean).pop() || lastScanRoot));
                else meta.push('올린 파일');
                meta.push(results.length + '개 파일');
                if (lastScanAt) meta.push(fmtStamp(lastScanAt) + ' 검사');
            }
            $('case-line-meta').textContent = meta.join(' · ');
        }
        function fmtStamp(d) {
            const p = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
        }
        try {
            const saved = JSON.parse(sessionStorage.getItem(CASE_STORE) || '{}');
            Object.keys(CASE_FIELDS).forEach(id => { if (typeof saved[id] === 'string') $(id).value = saved[id]; });
            const side = document.querySelector(`input[name="submitter-side"][value="${saved.side === 'defendant' ? 'defendant' : saved.side === 'plaintiff' ? 'plaintiff' : 'none'}"]`);
            if (side) side.checked = true;
            if (typeof saved.folder === 'string') caseFolder = saved.folder;
        } catch (e) { /* no saved case */ }
        const onCaseChange = event => {
            const identity = event && event.target && (event.target.id === 'case-no' || event.target.id === 'case-name');
            if (identity && caseScope()) caseFolder = caseScope();
            saveCase();
            updateCaseLine();
            // GUI-REV7 F-9: a keystroke re-renders without the live export note and with the longer preview delay;
            // the side choice renders everything. Leaving a text field (change) writes only the note: a full render
            // rebuilds the checklist, and the blur that fires change on a click of a checklist button would replace
            // that button under the pointer (gate.sh gui, "이 폴더의 사건이 맞음").
            if (currentView !== 'report') return;
            if (event && event.type === 'change' && event.target && event.target.type !== 'radio') { setExportNote(exportNoteText()); return; }
            if (currentView === 'report') renderReport(Boolean(event && event.type === 'input'));
        };
        Object.keys(CASE_FIELDS).forEach(id => {
            $(id).addEventListener('input', onCaseChange);
            $(id).addEventListener('change', onCaseChange);
        });
        document.querySelectorAll('input[name="submitter-side"]').forEach(el => el.addEventListener('change', onCaseChange));

        /* ── scan: progress panel ────────────────── */
        const prog = { serverElapsed: 0, at: 0, done: 0, total: null, timer: null, quarter: 0, running: false };
        function fmtClock(sec) {
            const s = Math.max(0, Math.floor(sec));
            const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
            const p = n => String(n).padStart(2, '0');
            return h ? `${h}:${p(m)}:${p(r)}` : `${p(m)}:${p(r)}`;
        }
        function fmtRemaining(sec) {
            if (sec < 60) return '1분 미만';
            if (sec < 3600) return Math.round(sec / 60) + '분';
            return Math.floor(sec / 3600) + '시간 ' + Math.round((sec % 3600) / 60) + '분';
        }
        function elapsedNow() { return prog.serverElapsed + (prog.at && prog.running ? (Date.now() - prog.at) / 1000 : 0); }
        function paintTime() {
            const el = elapsedNow();
            let text = '경과 ' + fmtClock(el);
            if (prog.running && prog.total && prog.done >= 2 && prog.done < prog.total) {
                text += ' · 남은 시간 약 ' + fmtRemaining(el / prog.done * (prog.total - prog.done));
            }
            $('prog-time').textContent = text;
        }
        function setStatusText(text) { $('progress-text').textContent = text; }
        function announce(text) { $('scan-live').textContent = text; }
        function paintCounts() {
            const bar = $('progress');
            $('prog-done').textContent = String(prog.done);
            $('prog-total').textContent = prog.total == null ? '–' : String(prog.total);
            if (prog.total) {
                bar.classList.remove('indeterminate');
                bar.ariaValueMax = String(prog.total);
                bar.ariaValueNow = String(Math.min(prog.done, prog.total));
                bar.ariaValueText = null;
                $('progress-fill').style.width = Math.min(100, prog.done / prog.total * 100) + '%';
            } else {
                bar.ariaValueMax = '0';
                bar.ariaValueNow = '0';
                bar.ariaValueText = '파일 목록을 만드는 중';
            }
        }
        function progressStart(title, canCancel) {
            prog.serverElapsed = 0; prog.at = Date.now(); prog.done = 0; prog.total = null; prog.quarter = 0; prog.running = true;
            $('prog-h').textContent = title;
            $('progress').classList.remove('cancelled');
            $('progress').classList.add('indeterminate');
            $('progress-fill').style.width = '';
            $('cancel-banner').hidden = true;
            $('view-results-btn').hidden = true;
            // GUI-REV7 F-5: a cancelled scan renames it "부분 결과 보기" (progressFinish); every new scan names it back.
            $('view-results-btn').textContent = '결과 보기';
            $('scan-cancel').hidden = !canCancel;
            $('cancel-note').hidden = !canCancel;
            setBusyButton($('scan-cancel'), false);
            $('recent-list').innerHTML = '';
            setStatusText('파일 목록을 만드는 중입니다.');
            paintCounts();
            paintTime();
            clearInterval(prog.timer);
            prog.timer = setInterval(paintTime, 1000);
            announce(title + ' 시작');
        }
        function progressUpdate(state) {
            if (typeof state.elapsed_seconds === 'number') { prog.serverElapsed = state.elapsed_seconds; prog.at = Date.now(); }
            if (Number.isInteger(state.done)) prog.done = state.done;
            if (Number.isInteger(state.total)) prog.total = state.total;
            paintCounts();
            paintTime();
            // The row name comes from the server already escaped by display_name (R11-14).
            const lastName = typeof state.current === 'string' ? state.current : '';
            if (lastName) setStatusText('마지막으로 끝난 파일: ' + lastName);
            if (Array.isArray(state.recent)) renderRecent(state.recent);
            if (prog.total) {
                const quarter = Math.floor(prog.done / prog.total * 4);
                if (quarter > prog.quarter && quarter < 4) { prog.quarter = quarter; announce(`${prog.done} / ${prog.total} 파일 검사함`); }
            }
        }
        function renderRecent(rows) {
            const list = $('recent-list');
            list.innerHTML = '';
            rows.forEach(row => {
                const v = row.verdict_code && row.status === 'analyzed' ? row.verdict_code : 'other';
                const li = document.createElement('li');
                li.innerHTML = `<span class="mk mk-${VERDICT_MARK[v] || 'x'}" aria-hidden="true"></span><span class="nm"></span><span class="v"></span>`;
                // Already display_name-escaped by the server (status payload).
                const escapedName = String(row.name || '');
                li.querySelector('.nm').textContent = escapedName;
                li.querySelector('.v').textContent = VERDICT_SHORT[v] || '';
                list.appendChild(li);
            });
        }
        function progressFinish(kind, data) {
            clearInterval(prog.timer);
            prog.timer = null;
            if (prog.at) prog.serverElapsed = elapsedNow();
            prog.running = false;
            paintTime();
            if (document.activeElement === $('scan-cancel')) focusSafely($('prog-h'));
            $('scan-cancel').hidden = true;
            $('cancel-note').hidden = true;
            $('progress').classList.remove('indeterminate');
            const rows = data && Array.isArray(data.items) ? data.items.length : 0;
            if (kind === 'done' || kind === 'cancelled') dropRetiredErrors();
            if (kind === 'done') {
                $('prog-h').textContent = '검사 완료';
                if (prog.total == null) { prog.total = rows; prog.done = rows; paintCounts(); }
                setStatusText(`${rows}개 행을 사건 대장에 실었습니다.`);
                $('view-results-btn').hidden = false;
                announce(`검사 완료 — ${rows}개 파일`);
            } else if (kind === 'cancelled') {
                const summary = (data && data.summary) || {};
                $('prog-h').textContent = '검사 중단됨';
                $('progress').classList.add('cancelled');
                $('cancel-banner').hidden = false;
                $('cancel-banner-text').textContent = partialResultNote(summary);
                setStatusText(`${rows}개 행은 결과가 있고, ${summary.files_cancelled || 0}개 파일은 검사하지 않았습니다.`);
                $('view-results-btn').hidden = false;
                $('view-results-btn').textContent = '부분 결과 보기';
                announce('검사를 중단했습니다 — 부분 결과');
            } else {
                $('prog-h').textContent = '검사 실패';
                setStatusText('검사를 마치지 못했습니다. 위의 오류를 확인하십시오.');
                announce('검사 실패');
            }
        }
        $('view-results-btn').addEventListener('click', () => navigate('ledger'));

        /* ── scan: controls ──────────────────────── */
        // R19-3: the export buttons too — a report made while a scan runs would
        // mix the rows on screen with the options of the scan in progress.
        const SCAN_INPUTS = ['folder-path', 'analyze-btn', 'pick-folder-btn', 'max-files',
            'opt-recursive', 'opt-dedupe', 'opt-heatmaps', 'opt-model',
            'opt-deep', 'file-picker-btn', 'dir-picker-btn', 'upload-btn', 'upload-clear',
            'exp-json', 'exp-csv', 'exp-report', 'exp-pdf', 'exp-evidence', 'exp-signed', 'exp-clear'];

        // R19-3: every export handler refuses while a scan runs (a click that beat the disabled state).
        function exportBlocked() {
            if (!scanBusy) return false;
            toast('검사 중에는 내보낼 수 없습니다 — 검사가 끝난 뒤 다시 시도하십시오', true);
            return true;
        }

        function setBusy(busy) {
            scanBusy = busy;
            $('progress').setAttribute('aria-busy', busy ? 'true' : 'false');
            // A11Y-4: the control that has focus is about to be disabled —
            // focus moves to the next action (cancel) or the progress heading,
            // never to <body>.
            const active = document.activeElement;
            if (busy && active && (SCAN_INPUTS.includes(active.id) || active.name === 'pixel-mode' || active.closest('.file-chips'))) {
                ($('scan-cancel').hidden ? $('prog-h') : $('scan-cancel')).focus();
            }
            SCAN_INPUTS.forEach(id => { const el = $(id); if (el) el.disabled = busy; });
            document.querySelectorAll('input[name="pixel-mode"]').forEach(el => { el.disabled = busy; });
            updateBusyBand();
            if (!busy) dropShutdownRefusal();  // REV2-D7
            if (currentView === 'report') renderReport();
        }
        // GUI-REV G-5/G-6: while a scan runs, every other screen says so — the
        // ledger and report still hold the previous results (or none yet).
        // GUI-REV2 H-5: a finished scan opens the ledger only from the 검사 screen.
        // Elsewhere (the examiner may be typing) the band says it finished and
        // links to the results — no screen change, no focus move (WCAG 3.2.2).
        let scanDoneNotice = '';
        function showResultsWhenDone(kind) {
            if (currentView === 'scan' && kind !== 'cancelled') {
                navigate('ledger', $('sum-h'));
                return;
            }
            scanDoneNotice = kind === 'cancelled' ? 'cancelled' : 'done';
            updateBusyBand();
            // GUI-REV3 J-5: focus stays where the examiner put it; only if the results
            // replaced the focused element (e.g. the empty ledger's heading) does it
            // move to this screen's heading — never to <body>.
            const active = document.activeElement;
            if (!active || active === document.body || !active.getClientRects().length) focusSafely(viewHeading(currentView));
        }
        function updateBusyBand() {
            const band = $('busy-band');
            const link = $('busy-band-link');
            if (scanBusy) scanDoneNotice = false;
            if (currentView === 'ledger') scanDoneNotice = false;
            if (scanBusy && currentView !== 'scan') {
                band.hidden = false;
                $('busy-band-text').textContent = results.length
                    ? '새 검사 진행 중 — 아래는 이전 결과입니다. 끝나기 전에는 내보낼 수 없습니다.'
                    : '새 검사 진행 중입니다.';
                link.href = '#/scan';
                link.textContent = '진행 상황 보기';
            } else if (scanDoneNotice) {
                band.hidden = false;
                $('busy-band-text').textContent = scanDoneNotice === 'cancelled' ? '검사가 취소되었습니다.' : '검사가 끝났습니다.';
                link.href = '#/ledger';
                link.textContent = scanDoneNotice === 'cancelled' ? '부분 결과 보기' : '결과 보기';
            } else {
                band.hidden = true;
            }
        }
        function pixelMode() {
            const on = document.querySelector('input[name="pixel-mode"]:checked');
            return on ? on.value : 'off';
        }

        async function traverseEntry(entry, out) {
            if (entry.isFile) {
                await new Promise(res => entry.file(f => { out.push(f); res(); }, res));
            } else if (entry.isDirectory) {
                const reader = entry.createReader();
                let batch;
                do {
                    batch = await new Promise(res => reader.readEntries(res, () => res([])));
                    for (const child of batch) await traverseEntry(child, out);
                } while (batch.length);
            }
        }

        const dropZone = $('drop-zone');
        dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('over'); });
        dropZone.addEventListener('dragleave', () => dropZone.classList.remove('over'));
        dropZone.addEventListener('drop', async e => {
            e.preventDefault();
            dropZone.classList.remove('over');
            if (scanBusy) return;
            const items = e.dataTransfer.items;
            if (items && items.length && items[0].webkitGetAsEntry) {
                const files = [];
                for (const item of items) {
                    const entry = item.webkitGetAsEntry();
                    if (entry) await traverseEntry(entry, files);
                }
                addFiles(files);
            } else {
                addFiles(Array.from(e.dataTransfer.files));
            }
        });
        // GUI-REV7 F-10: a file dropped outside the drop zone was opened by the browser (the page left for
        // file:///…, and the results on screen were gone). File drags are refused everywhere but the drop zone;
        // text dragged into a text box is not a file drag and is left to the browser.
        function isFileDrag(e) {
            const types = e && e.dataTransfer ? Array.from(e.dataTransfer.types || []) : [];
            return types.includes('Files');
        }
        document.addEventListener('dragover', e => {
            if (!isFileDrag(e) || dropZone.contains(e.target)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'none';
        });
        document.addEventListener('drop', e => {
            if (!isFileDrag(e) || dropZone.contains(e.target)) return;
            e.preventDefault();
            // DESK-4: the block is folded in the desktop mode — opened, so the zone the toast names is there.
            $('upload-block').open = true;
            toast('파일을 열지 않았습니다 — 검사 화면의 "파일 올려서 검사" 칸에 놓으십시오.');
        });
        function openFilePicker() {
            if (scanBusy) return;
            const input = document.createElement('input');
            input.type = 'file';
            input.multiple = true;
            input.accept = '.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif,.txt,.md,.pdf,.docx,.xlsx,.pptx,.hwp,.wav,.mp3,.flac,.ogg,.m4a,.aac,.opus,.mp4,.mov,.m4v,.mkv,.webm,.avi,.zip,.7z,.rar,.tar,.tgz,.gz,.bz2,.xz';
            input.onchange = e => addFiles(Array.from(e.target.files));
            input.click();
        }
        $('file-picker-btn').addEventListener('click', openFilePicker);
        $('dir-picker-btn').addEventListener('click', () => { if (!scanBusy) $('dir-picker').click(); });
        $('dir-picker').addEventListener('change', e => { addFiles(Array.from(e.target.files)); e.target.value = ''; });
        $('upload-clear').addEventListener('click', () => { selectedFiles = []; renderFileChips(); $('file-picker-btn').focus(); });

        function renderFileChips() {
            const list = $('file-list');
            list.innerHTML = '';
            selectedFiles.forEach((file, i) => {
                const relName = file.webkitRelativePath || file.name;
                const li = document.createElement('li');
                li.innerHTML = `<span class="nm">${shown(relName)}</span><span class="mono small">${(file.size / 1024).toFixed(0)}K</span>`;
                const x = document.createElement('button');
                x.type = 'button';
                x.className = 'btn btn-sm';
                x.textContent = '빼기';
                x.setAttribute('aria-label', displayName(relName) + ' 빼기');
                x.addEventListener('click', () => {
                    selectedFiles.splice(i, 1);
                    renderFileChips();
                    const rest = $('file-list').querySelectorAll('button');
                    (rest[Math.min(i, rest.length - 1)] || $('file-picker-btn')).focus();
                });
                li.appendChild(x);
                list.appendChild(li);
            });
            $('upload-row').hidden = selectedFiles.length === 0;
            $('upload-n').textContent = selectedFiles.length;
        }

        function addFiles(files) {
            files.forEach(file => {
                const relName = file.webkitRelativePath || file.name;
                if (!selectedFiles.find(f => (f.webkitRelativePath || f.name) === relName && f.size === file.size)) {
                    selectedFiles.push(file);
                }
            });
            renderFileChips();
        }

        /* ── folder scan (async job + poll; U-7: no deadline, reconnects) ── */
        const JOB_STORE = 'dfl.scan-job';
        function rememberJob(job) { try { sessionStorage.setItem(JOB_STORE, JSON.stringify(job)); } catch (e) { /* non-fatal */ } }
        function forgetJob() { try { sessionStorage.removeItem(JOB_STORE); } catch (e) { /* non-fatal */ } }

        // OMO-5: quiet (DFL.loadJob) loads a finished job into the ledger without the progress panel, the success and
        // partial-result toasts, the move to the results and the dfl:scan-* events; the error toasts still show.
        async function runScanJob(jobId, folderPath, options, quiet) {
            currentJobId = jobId;
            if (!quiet) dflEmit('dfl:scan-started', { jobId, folder: folderPath || '' });
            retireErrors();
            if (!quiet)
            progressStart('검사 중', true);
            setBusy(true);
            let state = null;
            try {
                while (true) {
                    let res;
                    try {
                        res = await apiFetch('/api/scan-status?job=' + encodeURIComponent(jobId));
                    } catch (e) {
                        // The server may be restarting or the network briefly gone:
                        // the job keeps running there, so keep asking.
                        setStatusText('서버에 연결할 수 없습니다 — 잠시 뒤 다시 확인합니다.');
                        await sleep(3000);
                        continue;
                    }
                    if (!res.ok) throw new Error(await apiError(res));
                    state = await res.json();
                    if (!quiet)
                    progressUpdate(state);
                    if (!quiet) dflEmit('dfl:scan-progress', { jobId, done: prog.done, total: prog.total });
                    if (state.status !== 'running') break;
                    await sleep(1200);
                }
                const data = state.result;
                if (state.status === 'error' || !data || data.error) {
                    toast('검사 실패: ' + ((data && (data.detail || data.error)) || '검사 결과 없음'), true, '', 'scan');
                    if (!quiet)
                    progressFinish('error');
                    if (!quiet) dflEmit('dfl:scan-finished', { jobId, status: 'error' });
                    return;
                }
                // P1: the server's resolved scan folder — /api/report resolves
                // every row against it (not against the read root).
                lastScanRoot = data.scan_root || folderPath;
                lastScanRootB64 = data.scan_root_b64 || '';
                lastScanOptions = options || {};  // R19-3: with the rows they produced
                lastScanJob = data.scan_job || jobId;  // P0R-6: the server-held job every report is built from
                // P0R-6: a job the server could not persist (its store would be inside the evidence) is held in
                // memory only — said, since a server restart then loses it (the report needs a new scan).
                lastScanJobNote = data.scan_job_note ? String(data.scan_job_note) : '';
                if (lastScanJobNote) toast(lastScanJobNote, true, '주의', 'scan');
                processResults(data);
                // R18-3: "분석 완료" was shown for a cancelled scan too.
                if (!quiet)
                if (partialResultNote(data.summary)) toast(`검사 취소 — 부분 결과 ${(data.items || []).length}개 행, 분석하지 않은 파일 ${data.summary.files_cancelled || 0}개`, true, '주의', 'scan');
                else toast(`분석 완료 — ${(data.items || []).length}개 파일`);
                if (state.status === 'cancelled' || partialResultNote(data.summary)) {
                    if (!quiet)
                    progressFinish('cancelled', data);
                    setBusy(false);
                    // G-6/J-5: on the 검사 screen focus goes to "부분 결과 보기"; elsewhere the
                    // examiner keeps the focus and the band says the scan was cancelled.
                    if (!quiet)
                    if (currentView === 'scan') focusSafely($('view-results-btn'));
                    else showResultsWhenDone('cancelled');
                } else {
                    if (!quiet)
                    progressFinish('done', data);
                    setBusy(false);
                    if (!quiet)
                    showResultsWhenDone();
                }
                if (!quiet) dflEmit('dfl:scan-finished', { jobId, status: state.status === 'cancelled' || partialResultNote(data.summary) ? 'cancelled' : 'done' });
            } catch (error) {
                toast('검사 중 오류: ' + error.message + deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode, true), true, '', 'scan');
                if (!quiet)
                progressFinish('error');
                if (!quiet) dflEmit('dfl:scan-finished', { jobId, status: 'error' });
            } finally {
                currentJobId = null;
                forgetJob();
                if (scanBusy) setBusy(false);
            }
        }

        // U-8: an out-of-root folder says which folders can be scanned. GUI-REV7 F-3: and names the server's
        // own folder (--folder). DESK-3: /api/stats now lists every registered folder (roots) — when that list is
        // known the refusal names those folders instead (the --allow-root ones and the ones chosen in the dialog too).
        // RISK B4: a refusal for a scan already made (its job, previews, report — `earlier`) means its folder is no
        // longer registered: a folder chosen in the dialog is registered only until the program stops.
        function deniedFolderHint(message, folder, roots, desktop, earlier) {
            if (!/허용되지 않은 경로/.test(message)) return '';
            if (earlier && desktop) return " — 프로그램을 다시 시작하면 고른 폴더의 등록이 사라집니다. 같은 폴더를 '검사할 폴더 고르기'로 다시 고르십시오.";
            if (earlier) return ' — 이 검사의 폴더가 지금 서버에 등록되어 있지 않습니다(서버를 다시 시작한 경우). 그 폴더를 --folder 또는 --allow-root로 등록해 서버를 다시 시작하십시오.';
            const pick = desktop ? ' 다른 폴더는 위의 "검사할 폴더 고르기"로 고르십시오.' : '';
            if (Array.isArray(roots) && roots.length) return ` — 지금 검사할 수 있는 폴더(그 안의 폴더 포함): ${rootsSummary(roots)}.` + pick;
            if (desktop) return ' — 아직 검사할 수 있는 폴더가 없습니다.' + pick;
            const own = folder ? ` 이 서버의 기본 폴더: ${folder}` : '';
            return ' — 서버를 시작할 때 --folder 또는 --allow-root로 등록한 폴더 안만 검사할 수 있습니다.' + own;
        }
        // RISK B4: the hint for a 403 answer about a scan already made (the body is read from a copy).
        async function deniedResponseHint(res) {
            if (res.status !== 403) return '';
            let text = '';
            try { text = await res.clone().text(); } catch (e) { text = ''; }
            return deniedFolderHint(text, defaultFolder, readRoots, desktopMode, true);
        }
        // DESK-3: at most five folders by name, the rest counted.
        function rootsSummary(roots) {
            const shown = roots.slice(0, ROOTS_SHOWN);
            return shown.join(', ') + (roots.length > shown.length ? ` 외 ${roots.length - shown.length}개` : '');
        }

        // RISK B25: the Enter that ends a Korean IME composition (isComposing; keyCode 229 where the browser reports the
        // key as processed by the IME, e.g. Safari's keydown after compositionend) also submitted the form — the last
        // syllable was lost or the scan started twice. Such an Enter is ignored by every form; the plain Enter that
        // follows submits. The other text boxes (case fields, quick-check text, review memo, filter) do nothing on Enter.
        function isImeEnter(e) {
            return (e.key === 'Enter' || e.code === 'Enter' || e.code === 'NumpadEnter') && (e.isComposing === true || e.keyCode === 229);
        }
        $('scan-form').addEventListener('keydown', e => { if (isImeEnter(e)) e.preventDefault(); });
        $('token-form').addEventListener('keydown', e => { if (isImeEnter(e)) e.preventDefault(); });

        $('scan-form').addEventListener('submit', async e => {
            e.preventDefault();
            // GUI-REV7 F-6: scanBusy is set only when /api/scan has answered; the button is marked busy
            // (synchronously, below) from the first submit — an Enter or a double click in between started
            // a second job whose poll overwrote the progress panel.
            if (scanBusy || isBusyButton($('analyze-btn'))) return;
            const folderPath = $('folder-path').value.trim();
            if (!folderPath) {
                toast('폴더 경로를 입력하세요', true, '', 'scan');
                setFolderNote('폴더 경로를 입력하세요', true, '');  // REV2-D4: said next to the box too
                $('folder-path').focus();
                return;
            }
            const maxFilesNote = maxFilesProblem($('max-files').value);
            showMaxFilesProblem(maxFilesNote);
            if (maxFilesNote) {
                $('scan-options').open = true;
                $('max-files').focus();
                return;
            }
            const params = new URLSearchParams({
                folder: folderPath,
                pixel: pixelMode(),
                max_files: $('max-files').value,
                recursive: $('opt-recursive').checked,
                dedupe: $('opt-dedupe').checked,
                heatmaps: $('opt-heatmaps').checked,
                deep_signals: $('opt-deep').checked,
                async: '1',
            });
            // R12-4/R13-3: the server's own folder is named by its bytes while
            // the box still holds it (a non-UTF-8 path survives).
            if (defaultFolderB64 && folderPath === defaultFolder) {
                params.delete('folder');
                params.set('folder_b64', defaultFolderB64);
            } else if (pickedFolder && pickedFolder.b64 && folderPath === pickedFolder.folder) {
                // DESK-3: a folder chosen in the OS dialog is named by its bytes too (folder_b64 of /api/pick-folder).
                params.delete('folder');
                params.set('folder_b64', pickedFolder.b64);
            }
            if (!$('opt-model').checked) params.set('no_default_engine', 'true');
            // GUI-REV G-5: the options become the report's options only when this
            // scan's results arrive — until then the ledger still shows the
            // previous scan, exported with the previous scan's options.
            const scanOptions = {
                pixel: pixelMode(),
                heatmaps: String($('opt-heatmaps').checked),
                deep_signals: String($('opt-deep').checked),
                no_default_engine: String(!$('opt-model').checked),
                // R22-1: the walk options too — without the job (expired) the server lists the folder with them
                recursive: String($('opt-recursive').checked),
                max_files: $('max-files').value,
            };
            setBusyButton($('analyze-btn'), true);
            try {
                const job = await apiJson('/api/scan?' + params.toString());
                if (job.error) { scanStartFailed('오류: ' + job.error, '검사를 시작하지 못했습니다: ' + job.error, folderPath); return; }
                // REV2-D4: a refusal said for this folder before no longer holds; a OneDrive caution stays.
                if (folderNote.isError) setFolderNote('', false, '');
                rememberJob({ job: job.job_id, folder: folderPath, options: scanOptions });
                await runScanJob(job.job_id, folderPath, scanOptions);
            } catch (error) {
                // U-8: an out-of-root folder says which folders can be scanned.
                // REV2-D3: in the desktop mode the list is read again first (the server may have been started again),
                // and a folder this page saw registered before is "pick it again" (RISK B4's text), not "pick another".
                let earlier = false;
                if (desktopMode && /허용되지 않은 경로/.test(error.message)) {
                    await refreshRoots();
                    earlier = folderUnderRoots(folderPath, Array.from(seenRoots)) && !folderUnderRoots(folderPath, readRoots);
                }
                const hint = deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode);
                const said = earlier ? deniedFolderHint(error.message, defaultFolder, readRoots, desktopMode, true) : hint;
                scanStartFailed('검사를 시작하지 못했습니다: ' + error.message + said, '검사를 시작하지 못했습니다: ' + error.message, folderPath);
            } finally {
                setBusyButton($('analyze-btn'), false);
            }
        });

        // REV2-D4: a scan that did not start is said in the error region (the box scrolled into view when it is
        // off-screen — the region is at the top of the page) and in one line under the path box.
        function scanStartFailed(text, summary, folder) {
            const box = showError(text, '', true);
            setFolderNote(summary, true, folder);
            if (!boxInView(box.getBoundingClientRect(), window.innerHeight)) box.scrollIntoView({ block: 'nearest' });
        }
        function boxInView(rect, height) {
            return rect.top >= 0 && rect.bottom <= height;
        }
        // REV2-D3: the folder is one of the folders, or inside one (a Windows path compared without case).
        function folderUnderRoots(folder, roots) {
            const norm = path => {
                let text = String(path).replace(/\\/g, '/');
                while (text.length > 1 && text.endsWith('/') && !/^[A-Za-z]:\/$/.test(text)) text = text.slice(0, -1);
                return /^[A-Za-z]:\//.test(text) || text.startsWith('//') ? text.toLowerCase() : text;
            };
            const own = norm(folder);
            return roots.some(root => {
                const base = norm(root);
                return own === base || own.startsWith(base.endsWith('/') ? base : base + '/');
            });
        }

        $('max-files').addEventListener('input', () => { if (!$('max-files-err').hidden) showMaxFilesProblem(maxFilesProblem($('max-files').value)); });

        $('scan-cancel').addEventListener('click', async () => {
            if (!currentJobId || isBusyButton($('scan-cancel'))) return;
            setBusyButton($('scan-cancel'), true);
            try {
                const data = await apiJson('/api/scan-cancel?job=' + encodeURIComponent(currentJobId));
                if (data.cancelled) setStatusText('중단 요청을 보냈습니다 — 지금 검사 중인 파일까지 끝내고 멈춥니다.');
            } catch (e) {
                toast('중단 요청 실패: ' + e.message, true, '', 'scan');
                setBusyButton($('scan-cancel'), false);
            }
        });

        function focusSafely(el) {
            const visible = el && el.getClientRects().length > 0;
            const target = visible ? el : viewHeading(currentView);
            if (target) target.focus();
        }

        function resumeJob() {
            let saved = null;
            try { saved = JSON.parse(sessionStorage.getItem(JOB_STORE) || 'null'); } catch (e) { saved = null; }
            if (!saved || typeof saved.job !== 'string') return false;
            const options = saved.options && typeof saved.options === 'object' ? saved.options : {};
            if (typeof saved.folder === 'string') $('folder-path').value = saved.folder;
            toast('진행 중이던 검사에 다시 연결합니다');
            // GUI-REV G-6: a reconnected scan is shown, not left behind an empty ledger.
            if (currentView !== 'scan') navigate('scan');
            runScanJob(saved.job, typeof saved.folder === 'string' ? saved.folder : '', options);
            return true;
        }

        /* ── upload scan (batched) ─────────────────── */
        $('upload-btn').addEventListener('click', async () => {
            if (!selectedFiles.length || scanBusy) return;
            const BATCH = 20;
            retireErrors();
            progressStart('올린 파일 검사 중', false);
            setBusy(true);
            try {
                const allItems = [];
                let stagedCoverage = null, stagedThresholds = null;
                const total = selectedFiles.length;
                progressUpdate({ done: 0, total });
                for (let i = 0; i < total; i += BATCH) {
                    const batch = selectedFiles.slice(i, i + BATCH);
                    const form = new FormData();
                    batch.forEach(f => form.append('files', f, f.webkitRelativePath || f.name));
                    const data = await apiJson('/api/analyze-upload', { method: 'POST', body: form });
                    if (data.error) { toast('오류: ' + data.error, true, '', 'scan'); progressFinish('error'); return; }
                    allItems.push(...(data.items || []));
                    // GUI-REV2 H-9: staged — the provenance on screen is the shown results'
                    // until these results arrive (processResults commits it).
                    if (data.coverage) stagedCoverage = data.coverage;
                    if (data.thresholds) stagedThresholds = data.thresholds;
                    progressUpdate({ done: Math.min(i + BATCH, total), total });
                }
                lastScanRoot = '';  // uploads are temp files; heatmaps unavailable
                lastScanRootB64 = '';
                lastScanOptions = {};  // R19-3: upload rows are never re-analyzed (P6)
                lastScanJob = '';
                lastScanJobNote = '';
                lastUploadScope = 'upload:' + Date.now().toString(36) + ':' + (selectedFiles[0] ? (selectedFiles[0].webkitRelativePath || selectedFiles[0].name) : '');
                processResults({ items: allItems, coverage: stagedCoverage, thresholds: stagedThresholds });
                toast(`분석 완료 — ${allItems.length}개 파일`);
                selectedFiles = [];
                renderFileChips();
                progressFinish('done', { items: allItems });
                setBusy(false);
                showResultsWhenDone();
            } catch (error) {
                toast('분석 중 오류: ' + error.message, true, '', 'scan');
                progressFinish('error');
            } finally {
                if (scanBusy) setBusy(false);
            }
        });

        /* ── results ─────────────────────────────── */
        function processResults(data) {
            results = (data.items || []).map(item => ({ item }));
            if (data.coverage) lastProvenance.coverage = data.coverage;
            if (data.thresholds) lastProvenance.thresholds = data.thresholds;
            lastProvenance.summary = data.summary || null;
            lastScanAt = new Date();
            results.forEach(entry => learnModelNames(entry.item.result));
            bandFilter = null;
            textFilter = '';
            revFilter = false;
            $('res-filter').value = '';
            $('rev-filter').setAttribute('aria-pressed', 'false');
            selectedEntry = null;
            groupsTouched = false;
            resetExportState();
            // H-8: case fields typed before the first scan belong to it.
            if (!caseFolder && caseHasValues()) confirmCaseFolder();
            renderResults();
            updateCaseLine();
        }

        // Merge of phase0 P0R-6: a report is built from the server's own scan job — its rows, options and
        // accounting; the GUI sends the job id, the ordered exhibit choice, the side and the case fields, never
        // rows. Results without a job (uploads, P6) go as `uploads` and are listed unsigned.
        function reportPayload(extra) {
            const out = {};
            if (lastScanJob) out.scan_job = lastScanJob;
            else out.uploads = results.map(r => r.item);
            if (submitterSide()) out.side = submitterSide();  // plaintiff only until BL-15
            for (const [id, key] of Object.entries(CASE_FIELDS)) {
                const el = $(id);
                if (el && el.value.trim()) out[key] = el.value.trim();
            }
            return Object.assign(out, extra || {});
        }

        function bandCounts() {
            const bands = { manipulation_evidence: 0, undetermined: 0, authenticity_evidence: 0, other: 0 };
            results.forEach(entry => { bands[itemBand(entry)]++; });
            return bands;
        }

        function undeterminedCounts() {
            const kinds = { cue: 0, c2pa: 0, none: 0, failed: 0, reference: 0 };
            results.forEach(e => { if (itemBand(e) === 'undetermined') kinds[undeterminedKind(e.item)]++; });
            return kinds;
        }
        // J-9: a cue row whose check also failed is counted once (as 단서 있음) and the
        // overlap is said, so the split adds up to the "검사가 실패한 파일 n개" limit line.
        function undeterminedSplitText() {
            const kinds = undeterminedCounts();
            const overlap = results.filter(e => itemBand(e) === 'undetermined' && undeterminedKind(e.item) === 'cue' && failedChecks(e.item).length).length;
            return UNDET_SPLIT.filter(k => kinds[k]).map(k => `${UNDET_KINDS[k]} ${kinds[k]}` +
                (k === 'cue' && overlap ? `(검사 실패가 겹친 ${overlap}개 포함)` : '')).join(' · ');
        }

        function renderSummary() {
            const b = bandCounts();
            const n = results.length;
            const found = b.manipulation_evidence + b.authenticity_evidence;
            $('sum-h').textContent = found
                ? `${n}개 중 ${found}개에서 결론 근거를 찾았습니다.`
                : `${n}개 중 결론 근거를 찾은 파일은 없습니다.`;
            const sub = [];
            // G-3: 판단 불가 is split by why — never all called "근거가 없어".
            const split = undeterminedSplitText();
            // M-1: how many conclusions rest only on records the file wrote about itself.
            const declared = results.filter(e => restsOnSelfDeclared(e.item)).length;
            if (declared) sub.push(`그중 ${declared}개는 파일이 스스로 적은 메타데이터(자기 선언 — 심거나 지울 수 있음)에만 근거합니다.`);
            if (b.undetermined) sub.push(`${found ? '나머지 ' : ''}${b.undetermined}개는 판단 불가(${split})입니다 — <strong>원본이라는 뜻이 아닙니다.</strong>`);
            if (b.other) sub.push(`${b.other}개는 분석하지 못했습니다.`);
            $('sum-sub').innerHTML = sub.join(' ');
            $('sum-sub').hidden = !sub.length;
            $('stat-manip').textContent = b.manipulation_evidence;
            $('stat-auth').textContent = b.authenticity_evidence;
            $('stat-undet').textContent = b.undetermined;
            $('stat-other').textContent = b.other;
            $('sub-undet').textContent = (split || '원본이라는 뜻 아님') + (b.undetermined ? ' — 원본이라는 뜻 아님' : '');
            const statuses = {};
            results.filter(e => itemBand(e) === 'other').forEach(e => {
                const label = STATUS_LABELS[e.item.status] || '기타';
                statuses[label] = (statuses[label] || 0) + 1;
            });
            const breakdown = Object.entries(statuses).map(([k, v]) => `${k} ${v}`).join(' · ');
            $('sub-other').textContent = breakdown || '미지원·실패·중복·건너뜀';
        }

        function renderEnv() {
            const env = envFacts();
            const details = provenanceBannerHtml();
            $('env-text').textContent = env.short.join(' · ');
            $('env-note').hidden = !env.short.length && !details;
            $('env-more').hidden = !details;
            $('prov-banner').innerHTML = details;
            if (!details) { $('prov-banner').hidden = true; $('env-more').setAttribute('aria-expanded', 'false'); }
            if (!env.short.length && details) $('env-text').textContent = '검사 조건에 관한 알림이 있습니다';
        }
        $('env-more').addEventListener('click', () => {
            const open = $('env-more').getAttribute('aria-expanded') !== 'true';
            $('env-more').setAttribute('aria-expanded', open ? 'true' : 'false');
            $('env-more').textContent = open ? '접기' : '자세히';
            $('prov-banner').hidden = !open;
        });

        function renderPartial() {
            const note = partialResultNote(lastProvenance.summary);
            const box = $('partial-banner');
            box.hidden = !note;
            box.innerHTML = note ? `<span class="t">부분 결과 — 검사를 중단했습니다</span>${shown(note)}` : '';
        }

        // Evidence-bearing rows first: manipulation → authenticity → undetermined → other.
        const BAND_ORDER = { manipulation_evidence: 0, authenticity_evidence: 1, undetermined: 2, other: 3 };
        // Rows are told apart by their path in the scanned folder (two subfolders may hold 00.png).
        const byName = (a, b) => String(a.item.path || a.item.name || '').localeCompare(String(b.item.path || b.item.name || ''), 'ko');
        // G-2: inside 판단 불가, rows with cues come before rows without evidence.
        const UNDET_ORDER = Object.fromEntries(UNDET_SPLIT.map((kind, index) => [kind, index]));
        const rank = e => BAND_ORDER[itemBand(e)] * 10 + (itemBand(e) === 'undetermined' ? UNDET_ORDER[undeterminedKind(e.item)] : 0);
        const byVerdict = (a, b) => (rank(a) - rank(b)) || byName(a, b);
        function filteredResults() {
            const needle = textFilter.trim().toLowerCase();
            const filtered = results.filter(entry => {
                if (bandFilter && itemBand(entry) !== bandFilter) return false;
                if (revFilter && !(reviewStore[itemKey(entry.item)] || {}).star) return false;
                if (!needle) return true;
                const item = entry.item;
                return ((item.name || '') + ' ' + (item.path || '')).toLowerCase().includes(needle);
            });
            // 결론순 (D16): verdict order, then name. The uncalibrated score is
            // always 0 in phase 0 and never orders results.
            filtered.sort(sortMode === 'name' ? byName : byVerdict);
            return filtered;
        }

        function renderResults() {
            const has = results.length > 0;
            $('scan-guide').hidden = has;  // DESK-7: the first-use steps stand only while there are no results
            $('ledger-empty').hidden = has;
            $('results-section').hidden = !has;
            if (!has) { $('detail').innerHTML = ''; return; }
            renderPartial();
            renderSummary();
            renderEnv();
            renderList();
            if (currentView === 'report') renderReport();
        }

        function renderRow(list, entry) {
            const item = entry.item;
            const v = verdictOf(item);
            const r = item.result;
            const cc = coverageCounts(r);
            const rev = reviewStore[itemKey(item)] || {};
            const rowPath = item.path || item.name || '파일';
            const li = document.createElement('li');
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = changedPaths.has(String(item.path)) ? 'row row-changed' : 'row';
            btn.dataset.verdict = v;
            btn.tabIndex = -1;
            const cov = cc.total
                ? `<span class="sr-only">검사 범위 </span><span class="${cc.failed ? 'fail' : ''}">${cc.ran}/${cc.total}</span>${cc.failed ? `<span class="sr-only">, 실패 ${cc.failed}</span>` : ''}`
                : '<span class="sr-only">검사 범위 </span>–';
            btn.innerHTML =
                `<span class="mk mk-${VERDICT_MARK[v]}" aria-hidden="true"></span>` +
                `<span class="row-name"><span class="nm">${pathHtml(rowPath)}</span><span class="vs">${shown(shortVerdict(item))}</span></span>` +
                `<span class="row-reason">${shown(rowReason(item))}</span>` +
                `<span class="row-cov">${cov}</span>` +
                `<span class="row-rev">${rev.star ? '검토함' : ''}</span>`;
            btn.addEventListener('click', () => selectEntry(entry, window.innerWidth <= 1180));
            entry.row = btn;
            li.appendChild(btn);
            list.appendChild(li);
        }

        // A long path keeps its file name whole: the folder part is what is cut short.
        // The path is escaped whole (displayName), then split for display.
        function pathHtml(path) {
            const text = displayName(path);
            const cut = Math.max(text.lastIndexOf('/'), text.lastIndexOf('::') >= 0 ? text.lastIndexOf('::') + 1 : -1);
            if (cut <= 0) return `<span class="base">${escapeHtml(text)}</span>`;
            return `<span class="dir">${escapeHtml(text.slice(0, cut + 1))}</span><span class="base">${escapeHtml(text.slice(cut + 1))}</span>`;
        }

        function markRows() {
            const rows = [...$('res-list').querySelectorAll('.row')];
            const current = selectedEntry && selectedEntry.row && rows.includes(selectedEntry.row) ? selectedEntry.row : rows[0];
            rows.forEach(b => {
                b.setAttribute('aria-current', selectedEntry && b === selectedEntry.row ? 'true' : 'false');
                b.tabIndex = b === current ? 0 : -1;
            });
        }

        function renderList() {
            const list = $('res-list');
            list.innerHTML = '';
            results.forEach(entry => { entry.row = null; });
            document.querySelectorAll('#stat-pills .seg').forEach(p => {
                p.setAttribute('aria-pressed', p.dataset.band === bandFilter ? 'true' : 'false');
            });
            const filtered = filteredResults();
            if (!filtered.length) {
                list.innerHTML = `<li class="empty-row"><p class="note">${results.length ? '거르기 조건과 맞는 파일이 없습니다.' : '분석 결과가 없습니다.'}</p></li>`;
            }
            renderedRows = Math.min(RENDER_WINDOW, filtered.length);
            filtered.slice(0, renderedRows).forEach(e => renderRow(list, e));
            updateMore(filtered.length);
            const containers = (lastProvenance.summary || {}).container_rows != null
                ? lastProvenance.summary.container_rows
                : results.filter(entry => entry.item.kind === 'archive' && entry.item.result).length;
            // N5: archive container rows are counted by verdict; say how many.
            const scope = bandFilter
                ? `${VERDICT_SHORT[bandFilter]} ${filtered.length}개 · 같은 칸을 다시 누르면 전체`
                : (filtered.length === results.length ? `전체 ${results.length}개` : `${filtered.length} / ${results.length}개`);
            $('ledger-count').textContent = scope + (containers ? ` · 압축 파일 ${containers}건 포함` : '');
            if (!selectedEntry || !filtered.includes(selectedEntry)) selectedEntry = filtered[0] || null;
            renderDetail(selectedEntry);
            markRows();
        }

        function updateMore(filteredLen) {
            const row = $('more-row');
            if (renderedRows >= filteredLen) { row.hidden = true; return; }
            row.hidden = false;
            $('more-btn').textContent = `나머지 ${filteredLen - renderedRows}개 더 보기`;
        }
        $('more-btn').addEventListener('click', () => {
            const list = $('res-list');
            const filtered = filteredResults();
            const prev = renderedRows;
            renderedRows = Math.min(renderedRows + RENDER_WINDOW, filtered.length);
            filtered.slice(prev, renderedRows).forEach(e => renderRow(list, e));
            updateMore(filtered.length);
            markRows();
            const first = filtered[prev];
            if (first && first.row) focusRow(first.row);
        });

        function selectEntry(entry, focusPanel) {
            selectedEntry = entry;
            renderDetail(entry);
            markRows();
            if (focusPanel && $('detail-h')) $('detail-h').focus();
        }
        function focusRow(btn) {
            $('res-list').querySelectorAll('.row').forEach(b => { b.tabIndex = b === btn ? 0 : -1; });
            btn.focus();
        }

        /* A11Y-5: list keys only inside the ledger (roving tabindex) — no
           page-wide single-key shortcut. ↑↓ Home End move; J/K too unless
           switched off in the 내보내기 menu. Enter/Space open (native button). */
        const KB_STORE = 'dfl.kb';
        try { $('kb-toggle').checked = localStorage.getItem(KB_STORE) !== 'off'; } catch (e) { /* default on */ }
        $('kb-toggle').addEventListener('change', () => {
            try { localStorage.setItem(KB_STORE, $('kb-toggle').checked ? 'on' : 'off'); } catch (e) { /* non-fatal */ }
        });
        $('res-list').addEventListener('keydown', e => {
            const rows = [...$('res-list').querySelectorAll('.row')];
            const at = rows.indexOf(document.activeElement);
            if (at < 0 || e.altKey || e.ctrlKey || e.metaKey) return;
            let step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
            if (step == null && $('kb-toggle').checked) step = { j: 1, J: 1, k: -1, K: -1 }[e.key];
            let to = null;
            if (step != null) to = Math.max(0, Math.min(rows.length - 1, at + step));
            else if (e.key === 'Home') to = 0;
            else if (e.key === 'End') to = rows.length - 1;
            if (to == null) return;
            e.preventDefault();
            focusRow(rows[to]);
        });

        /* Verdict strip and the text box are the filter surface — pressing
           the active segment again clears the filter. */
        document.querySelectorAll('#stat-pills .seg').forEach(p => {
            p.addEventListener('click', () => {
                bandFilter = p.dataset.band === bandFilter ? null : p.dataset.band;
                renderList();
            });
        });
        $('res-filter').addEventListener('input', e => { textFilter = e.target.value; renderList(); });
        $('res-sort').addEventListener('change', e => { sortMode = e.target.value; renderList(); });
        $('rev-filter').addEventListener('click', () => {
            revFilter = !revFilter;
            $('rev-filter').setAttribute('aria-pressed', revFilter ? 'true' : 'false');
            renderList();
        });
        // The 내보내기 menu closes on Escape and on a click outside it.
        $('export-menu').addEventListener('keydown', e => {
            if (e.key === 'Escape' && $('export-menu').open) {
                $('export-menu').open = false;
                $('export-menu').querySelector('summary').focus();
            }
        });
        document.addEventListener('click', e => {
            if ($('export-menu').open && !$('export-menu').contains(e.target)) $('export-menu').open = false;
        });

        /* ── evidence panel ──────────────────────── */
        function evItemHtml(e) {
            const neutral = e.direction === 'neutral';
            const tag = neutral ? '중립' : (STRENGTH_LABELS[e.strength] || e.strength);
            const prob = (e.probability != null && e.calibration_id)
                ? ` · p=${Number(e.probability).toFixed(2)}${e.probability_ci ? ` (95% CI ${Number(e.probability_ci[0]).toFixed(2)}–${Number(e.probability_ci[1]).toFixed(2)})` : ''} · 보정 ${shown(e.calibration_id)}`
                : '';
            // Merge of phase0 P0R-12: a record the file declares about itself (metadata a tool wrote) is marked —
            // anyone can plant or strip it.
            const declared = e.self_declared ? `<span class="self-declared">${SELF_DECLARED_MARK}</span>` : '';
            return `<li><span class="str${neutral ? ' str-neutral' : ''}">${escapeHtml(tag)}</span><span>${shown(e.title)}${declared}` +
                `${e.detail ? `<span class="ev-src">${shown(e.detail || '')}</span>` : ''}` +
                `<span class="ev-src">${escapeHtml(KIND_LABELS[e.kind] || e.kind)} · ${escapeHtml(DIRECTION_LABELS[e.direction] || e.direction)}${prob}</span></span></li>`;
        }

        function skippedHtml(r) {
            const cov = (r && r.coverage) || [];
            const groups = [];
            const byKey = new Map();
            cov.filter(c => c.status === 'skipped').forEach(c => {
                const reason = String(c.reason || '사유 기록 없음');
                const m = /^모델 실행 불가: (.+?): (.+)$/.exec(reason);
                const key = (m ? 'm|' + m[2] : 'c|' + reason);
                let g = byKey.get(key);
                if (!g) {
                    g = { why: m ? m[2] : reason, isModel: Boolean(m), names: [] };
                    byKey.set(key, g);
                    groups.push(g);
                }
                g.names.push(m ? m[1] : checkLabel(c.check));
            });
            const lis = groups.map(g => {
                const head = g.isModel ? `신경망 탐지기 ${g.names.length}종` : `검사 ${g.names.length}개`;
                return `<li><b>${escapeHtml(head)}</b> — ${shown(g.why)}<div class="names">${g.names.map(shown).join(', ')}</div></li>`;
            }).join('');
            const ran = cov.filter(c => c.status === 'ran');
            return (lis ? `<ul>${lis}</ul>` : '<p>실행하지 않은 검사가 없습니다.</p>') +
                (ran.length ? `<p class="names">실행한 검사: ${ran.map(c => shown(checkLabel(c.check))).join(', ')}</p>` : '');
        }

        function referenceHtml(r) {
            const parts = [];
            const soft = ((r && r.evidence) || []).filter(e => e.kind !== 'deterministic');
            if (soft.length) parts.push(`<div class="dgroup"><div class="dt">통계적·어휘적 근거 (보정 전 — 결론 불참여)</div><ol class="ev-list">${soft.map(evItemHtml).join('')}</ol></div>`);
            const ma = r && r.model_analysis;
            if (ma) {
                parts.push(`<div class="dgroup"><div class="dt">외부 모델 원점수(미보정, 결론 불참여) — ${modelScoreText(ma)}</div>${(ma.models || []).length ? modelMembersHtml(ma) : `<div class="note">${shown(ma.detail || '')}</div>`}</div>`);
            }
            // Unmeasured heuristics (pixel ensemble, fusion, legacy audio/
            // video heuristics) — displayed for reference, never decide.
            parts.push(listItems('참고 신호(미측정 휴리스틱 — 결론 불참여)', ((r && r.reference_signals) || []).map(s => ({ title: `${s.title} (${s.weight})`, detail: s.detail }))));
            const sg = r && r.source_guess;
            if (sg && sg.label) {
                const reasons = (sg.reasons || []).map(shown).join(' ');
                parts.push(`<div class="dgroup"><div class="dt">출처 추정 (${escapeHtml(sourceConfidenceLabel(r, sg))})</div><div class="note">${shown(sg.label)} — ${reasons}</div></div>`);
            }
            parts.push(listItems('다음 확인', r && r.next_checks));
            const html = parts.filter(Boolean).join('');
            const count = soft.length + (ma ? 1 : 0) + (((r && r.reference_signals) || []).length) + (sg && sg.label ? 1 : 0);
            return { html, count };
        }

        function techText(item) {
            const r = item.result || {};
            const lines = [
                '파일: ' + displayName(item.path || item.name || ''),
                'sha256: ' + (item.sha256 || '없음'),
                '크기: ' + (item.size_bytes != null ? item.size_bytes + ' B' : '없음'),
                '종류: ' + String(item.kind || '') + ' · 상태: ' + String(item.status || ''),
                'verdict_code: ' + String(r.verdict_code || '없음'),
                'grade: ' + String(r.grade || '없음'),
                'score_is_calibrated: ' + String(Boolean(r.score_is_calibrated)),
                'probability: ' + (r.probability == null ? 'null' : String(r.probability)),
            ];
            if (item.member) lines.push('압축 구성 파일: ' + displayName(item.member));
            return lines.join('\n');
        }

        // GUI-REV7 F-15: an archive that was not opened (its archive check did not run, status not 'expanded') has
        // no member rows — the server's "컨테이너 행은 구성 파일 결과의 요약입니다 …" is left out of its limitations.
        const CONTAINER_SUMMARY_NOTE = '컨테이너 행은 구성 파일 결과의 요약입니다.';
        function detailLimitations(item) {
            const r = (item && item.result) || {};
            const lims = [...new Set(r.limitations || [])];
            const archiveCheck = (r.coverage || []).find(c => c && c.check === 'archive');
            const unopened = item && item.kind === 'archive' && item.status !== 'expanded' && (!archiveCheck || archiveCheck.status !== 'ran');
            return unopened ? lims.filter(l => !String(l).startsWith(CONTAINER_SUMMARY_NOTE)) : lims;
        }
        function renderDetail(entry) {
            const box = $('detail');
            if (!entry) { box.innerHTML = ''; return; }
            const item = entry.item;
            const r = item.result || null;
            const v = verdictOf(item);
            const mk = VERDICT_MARK[v];
            const rev = reviewStore[itemKey(item)] || {};
            const parts = [];
            parts.push(`<div class="d-head"><div class="d-verdict vt-${mk}"><span class="mk mk-lg mk-${mk}" aria-hidden="true"></span><span>${shown(shortVerdict(item))}</span>${r && r.grade === 'reference' ? '<span class="grade-pill">참고 등급</span>' : ''}</div>` +
                verdictLineHeadHtml(item, 'h2') +
                `<div class="d-name">${shown(item.path || item.name || '파일')}</div>` +
                `<div class="d-meta">${shown(metaLine(item))}</div>` +
                `<button type="button" class="linkbtn d-back">목록으로 돌아가기</button></div>`);
            const body = [];
            // R19-13: the server found this file changed since the scan.
            if (changedPaths.has(String(item.path))) body.push(`<section class="d-sec d-fail"><h3>검사 뒤 바뀜</h3><p>내보낼 때 서버가 파일의 SHA-256을 다시 계산해 보니 검사 때와 다릅니다: ${shown(changedInfo.get(String(item.path)) || '바뀜')}. 이 결과로는 내보낼 수 없습니다 — 폴더를 다시 검사하십시오.</p></section>`);
            // 근거: the records that carry the conclusion, then neutral records.
            const decisive = decisiveEvidence(r, v);
            const neutral = ((r && r.evidence) || []).filter(e => e.kind === 'deterministic' && e.direction === 'neutral');
            let ev = decisive.length ? `<ol class="ev-list">${decisive.map(evItemHtml).join('')}</ol>` : `<p>${shown(noEvidenceText(item))}</p>`;
            if (neutral.length) ev += `<p class="hint">확인한 기록 (결론과 무관한 중립 기록)</p><ol class="ev-list">${neutral.map(evItemHtml).join('')}</ol>`;
            if (item.error && v !== 'other') ev += `<p class="c-red">분석 실패: ${shown(guiText(item.error))}</p>`;
            body.push(`<section class="d-sec"><h3>근거</h3>${ev}</section>`);
            // Merge of phase0 P0R-12: why this conclusion could be wrong — never behind a disclosure.
            const caveats = (r && r.conclusion_caveats) || [];
            if (caveats.length) body.push(`<section class="d-sec d-caveat"><h3>이 결론이 틀릴 수 있는 이유</h3><ul>${caveats.map(c => `<li>${shown(c)}</li>`).join('')}</ul></section>`);
            // A failed check is never behind a disclosure.
            const failed = failedChecks(item);
            if (failed.length) body.push(`<section class="d-sec d-fail"><h3>실패한 검사 ${failed.length}</h3><ul>${failed.map(c => `<li>${shown(guiText(coverageText(c)))}</li>`).join('')}</ul></section>`);
            // Limitations that repeat a failed check word for word are shown once, in 실패한 검사.
            const failedTexts = new Set(failed.map(c => '검사 실패 — ' + coverageText(c)));
            const lims = detailLimitations(item).filter(l => !failedTexts.has(l));
            body.push(`<section class="d-sec d-limit"><h3>이 결론의 한계</h3><p>${escapeHtml(limitAdvice(item))}</p>${lims.length ? `<ul>${lims.map(l => `<li>${shown(guiText(l))}</li>`).join('')}</ul>` : ''}</section>`);
            const discs = [];
            if (r) {
                const skipped = (r.coverage || []).filter(c => c.status === 'skipped').length;
                const cc = coverageCounts(r);
                discs.push(`<details class="disc"><summary>실행하지 않은 검사 <span class="cnt">${skipped}</span><span class="sub">실행 ${cc.ran} / 해당 ${cc.total}</span></summary><div class="disc-body">${skippedHtml(r)}</div></details>`);
                const ref = referenceHtml(r);
                discs.push(`<details class="disc"><summary>참고 신호 <span class="cnt">${ref.count}</span><span class="sub">결론에 쓰지 않음</span></summary><div class="disc-body">${ref.html || '<p>없음</p>'}</div></details>`);
            }
            // P7/R9-1: a member row has item.member (its "::" is display only);
            // any other row is a real file whose escaped path is unescaped for
            // the preview (never read as a member by a "::" in it).
            // R12-4: the file is named to the server by item.path_b64 (its real
            // relative path as file-system bytes) and the scan folder by
            // lastScanRootB64 — never by text, which a non-UTF-8 name breaks.
            const hasPreview = Boolean(lastScanRoot && lastScanRootB64 && item.path_b64 && !item.member &&
                (item.kind === 'image' || item.kind === 'video' || item.kind === 'audio'));
            const pvB64 = hasPreview ? item.path_b64 : '';
            const hmB64 = item.heatmap_path_b64 || (((r || {}).pixel_analysis || {}).heatmap_path_b64) || '';
            const hasHeatmap = Boolean(hmB64 && lastScanRoot && lastScanRootB64);
            if (hasPreview || hasHeatmap) {
                let media = '';
                if (item.kind === 'image' && hasPreview && hasHeatmap) {
                    media = `<div class="forensic-studio-slot" data-pv="${escapeHtml(pvB64)}" data-hm="${escapeHtml(hmB64)}"><span class="note">포렌식 비교 스튜디오 준비 중…</span></div>`;
                } else {
                    if (hasPreview) media += `<div class="dgroup"><div class="dt">미리보기</div><div class="pv-slot" data-pv="${escapeHtml(pvB64)}" data-kind="${escapeHtml(item.kind)}"><span class="note">로딩 중…</span></div></div>`;
                    if (hasHeatmap) media += `<div class="dgroup"><div class="dt">픽셀 히트맵</div><div class="hm-slot" data-hm="${escapeHtml(hmB64)}"><span class="note">히트맵 로딩 중…</span></div></div>`;
                }
                discs.push(`<details class="disc disc-media"><summary>${hasHeatmap ? '미리보기·히트맵' : '미리보기'}</summary><div class="disc-body">${media}</div></details>`);
            }
            if (r) {
                discs.push(`<details class="disc"><summary>기술 세부</summary><div class="disc-body">${verdictHeadHtml(r)}${evidenceHtml(r)}${coverageHtml(r)}` +
                    adviceHtml(verdictAdvice(r.verdict_code || 'undetermined', r.grade)) +
                    `<div class="caveat">결론은 재현 가능한 기록 근거로만 내립니다. 통계적·어휘적 근거와 검사 실패 내역은 근거·검사 범위 목록에 전부 남습니다.</div>` +
                    `<pre class="tech">${escapeHtml(techText(item))}</pre></div></details>`);
            } else {
                discs.push(`<details class="disc"><summary>기술 세부</summary><div class="disc-body"><pre class="tech">${escapeHtml(techText(item))}</pre></div></details>`);
            }
            body.push(`<div class="discs">${discs.join('')}</div>`);
            body.push(`<section class="d-review" aria-labelledby="rev-h"><h3 id="rev-h">검토</h3>
                <div class="rev-actions">
                    <button type="button" class="btn rev-star" aria-pressed="${rev.star ? 'true' : 'false'}">${rev.star ? '검토함' : '검토 완료로 표시'}</button>
                    <label class="lbl">검토 판정
                        <select class="field rev-verdict-sel">
                            <option value="unreviewed">미검토</option>
                            <option value="synthetic">인공합성 의심</option>
                            <option value="authentic">원본 정상</option>
                            <option value="inconclusive">판단 보류</option>
                        </select>
                    </label>
                </div>
                <label class="lbl" for="rev-note">검토 메모</label>
                <textarea class="field rev-note" id="rev-note"></textarea>
                <p class="rev-sync-status" role="status" aria-live="polite"></p>
                <details class="disc"><summary>학습용 라벨 보내기</summary><div class="disc-body"><div class="fb-row"><span class="note">검토자 라벨:</span>
                    <button type="button" class="btn btn-sm" data-label="synthetic">실제 합성/AI</button>
                    <button type="button" class="btn btn-sm" data-label="real">실제 실물</button>
                    <span class="fb-status"></span></div></div></details>
                <p class="hint">검토 기록은 이 사건(검사한 폴더)과 파일 내용(sha256)에만 붙습니다.</p>
            </section>`);
            box.innerHTML = parts.join('') + `<div class="d-body">${body.join('')}</div>`;
            bindDetail(box, entry);
        }

        // GUI-REV G-8: a mark belongs to a content (sha256) in a folder — every
        // row with that key shows it, not only the row it was set on.
        function refreshReviewMarks(key) {
            const on = Boolean((reviewStore[key] || {}).star);
            results.forEach(e => {
                if (e.row && itemKey(e.item) === key) e.row.querySelector('.row-rev').textContent = on ? '검토함' : '';
            });
        }

        function bindDetail(box, entry) {
            const item = entry.item;
            const rev = reviewStore[itemKey(item)] || {};
            box.querySelector('.d-back').addEventListener('click', () => {
                if (entry.row) focusRow(entry.row);
            });
            box.querySelectorAll('.disc-media').forEach(d => d.addEventListener('toggle', () => {
                if (!d.open || d.dataset.loaded) return;
                d.dataset.loaded = '1';
                d.querySelectorAll('.forensic-studio-slot').forEach(loadForensicStudio);
                d.querySelectorAll('.hm-slot').forEach(loadHeatmap);
                d.querySelectorAll('.pv-slot').forEach(loadPreview);
            }));
            const star = box.querySelector('.rev-star');
            const syncStatus = box.querySelector('.rev-sync-status');
            const showSync = refused => {
                syncStatus.textContent = displayName(reviewSyncText(refused));
                syncStatus.classList.toggle('sync-err', Boolean(refused));
            };
            star.addEventListener('click', () => {
                const on = toggleReview(item, showSync);
                star.setAttribute('aria-pressed', on ? 'true' : 'false');
                star.textContent = on ? '검토함' : '검토 완료로 표시';
                refreshReviewMarks(itemKey(item));
            });
            box.querySelectorAll('button[data-label]').forEach(b => b.addEventListener('click', () => {
                submitFeedback(item, b.dataset.label, box.querySelector('.fb-status'));
            }));
            const note = box.querySelector('.rev-note');
            note.value = rev.note || '';
            const verdictSel = box.querySelector('.rev-verdict-sel');
            verdictSel.value = rev.verdict || 'unreviewed';
            function saveAndSync() {
                const k = itemKey(item);
                const cur = reviewStore[k] || {};
                cur.note = note.value;
                cur.verdict = verdictSel.value;
                cur.ts = Date.now();
                reviewStore[k] = cur;
                saveReviewStore(k, showSync);  // one debounced save; its answer is shown
            }
            note.addEventListener('input', saveAndSync);
            verdictSel.addEventListener('change', saveAndSync);
        }

        async function submitFeedback(item, label, statusEl) {
            statusEl.textContent = '기록 중…';
            try {
                const data = await apiJson('/api/feedback', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        path: item.path || item.name,
                        name: item.name,
                        expected_label: label,
                        result: item.result,
                    }),
                });
                statusEl.textContent = data.ok ? '기록됨 ✓' : ('실패: ' + displayName(data.error || ''));  // R12-6
                statusEl.classList.toggle('sync-err', !data.ok);
            } catch (e) {
                // Merge of R22-3: a 409 (E49 — the feedback file would be inside an examined folder)
                // arrives here with the server's Korean reason, next to the label buttons.
                statusEl.textContent = '기록하지 못했습니다: ' + displayName(e.message);  // R12-6: a server message may echo a name
                statusEl.classList.add('sync-err');
            }
        }

        // R12-4: /api/preview and /api/heatmap by URL-safe base64 bytes only.
        function previewUrl(pathB64) {
            return '/api/preview?path_b64=' + encodeURIComponent(pathB64) + '&root_b64=' + encodeURIComponent(lastScanRootB64);
        }

        function heatmapUrl(pathB64) {
            return '/api/heatmap?path_b64=' + encodeURIComponent(pathB64) + '&root_b64=' + encodeURIComponent(lastScanRootB64);
        }

        // R12-4: a media-loading failure in Korean (a browser's own message,
        // e.g. "URI malformed", is never shown).
        function mediaErrorText(e) {
            const message = e && typeof e.message === 'string' ? e.message : '';
            return /[가-힣]/.test(message) ? message : '파일을 불러오는 중 오류가 발생했습니다';
        }

        async function loadPreview(slot) {
            const pathB64 = slot.dataset.pv, kind = slot.dataset.kind;
            try {
                const res = await apiFetch(previewUrl(pathB64));
                if (!res.ok) { slot.innerHTML = `<span class="note">미리보기 불가 (${res.status})${shown(await deniedResponseHint(res))}</span>`; return; }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const tag = kind === 'image' ? 'img' : kind === 'video' ? 'video' : 'audio';
                const el = document.createElement(tag);
                el.className = 'preview-media';
                if (tag !== 'img') el.controls = true;
                if (tag === 'img') el.alt = '분석 대상 미리보기';
                el.src = url;
                slot.innerHTML = '';
                slot.appendChild(el);
            } catch (e) {
                slot.innerHTML = `<span class="note">미리보기 실패: ${shown(mediaErrorText(e))}</span>`;
            }
        }

        async function loadHeatmap(slot) {
            const hmPath = slot.dataset.hm;
            try {
                const res = await apiFetch(heatmapUrl(hmPath));
                if (!res.ok) { slot.innerHTML = `<span class="note">히트맵을 불러올 수 없습니다 (${res.status})${shown(await deniedResponseHint(res))}</span>`; return; }
                const blob = await res.blob();
                const img = document.createElement('img');
                img.className = 'heatmap-img';
                img.alt = '픽셀 분석 히트맵';
                img.src = URL.createObjectURL(blob);
                slot.innerHTML = '';
                slot.appendChild(img);
            } catch (e) {
                slot.innerHTML = `<span class="note">히트맵 로딩 실패: ${shown(mediaErrorText(e))}</span>`;
            }
        }

        async function loadForensicStudio(slot) {
            const pvPath = slot.dataset.pv;
            const hmPath = slot.dataset.hm;
            slot.innerHTML = `<span class="note">포렌식 레이어 로딩 중 (원본 & 히트맵)…</span>`;

            try {
                const [resPv, resHm] = await Promise.all([
                    apiFetch(previewUrl(pvPath)),
                    apiFetch(heatmapUrl(hmPath)),
                ]);

                if (!resPv.ok || !resHm.ok) {
                    const denied = (await deniedResponseHint(resPv)) || (await deniedResponseHint(resHm));
                    slot.innerHTML = `<span class="note">포렌식 비교 이미지 로드 실패 (원본: ${resPv.status}, 히트맵: ${resHm.status})${shown(denied)}</span>`;
                    return;
                }

                const blobPv = await resPv.blob();
                const blobHm = await resHm.blob();
                const urlPv = URL.createObjectURL(blobPv);
                const urlHm = URL.createObjectURL(blobHm);

                const studio = document.createElement('div');
                studio.className = 'forensic-studio';

                let mode = 'split';
                let splitPos = 50;
                let blendOpacity = 60;

                studio.innerHTML = `
                    <div class="studio-header">
                        <div class="studio-title">
                            <span>포렌식 비교 분석 스튜디오</span>
                        </div>
                        <div class="studio-modes" role="group" aria-label="비교 모드">
                            <button type="button" class="studio-btn active" data-mode="split">좌우 분할</button>
                            <button type="button" class="studio-btn" data-mode="blend">알파 중첩</button>
                            <button type="button" class="studio-btn" data-mode="orig">원본만</button>
                            <button type="button" class="studio-btn" data-mode="heat">히트맵만</button>
                        </div>
                    </div>
                    <div class="split-slider-viewport">
                        <img class="split-base-img" src="${urlPv}" alt="원본 이미지">
                        <div class="split-overlay-wrap">
                            <img class="split-overlay-img" src="${urlHm}" alt="픽셀 히트맵">
                        </div>
                        <div class="split-divider-line">
                            <div class="split-handle" title="드래그하여 분할 조정">⟷</div>
                        </div>
                    </div>
                    <div class="split-controls">
                        <span class="split-mode-label">분할 위치:</span>
                        <input type="range" class="split-range" min="0" max="100" value="50" aria-label="비교 슬라이더 위치">
                        <span class="split-pct-label">50%</span>
                        <button type="button" class="btn btn-sm" data-quick="0">0%</button>
                        <button type="button" class="btn btn-sm" data-quick="50">50%</button>
                        <button type="button" class="btn btn-sm" data-quick="100">100%</button>
                    </div>
                    <div class="split-labels">
                        <span class="split-label-orig">◀ 원본 (Original)</span>
                        <span class="split-label-heat">히트맵 (Grad-CAM/ELA) ▶</span>
                    </div>
                `;

                const viewport = studio.querySelector('.split-slider-viewport');
                const overlayWrap = studio.querySelector('.split-overlay-wrap');
                const overlayImg = studio.querySelector('.split-overlay-img');
                const baseImg = studio.querySelector('.split-base-img');
                const divider = studio.querySelector('.split-divider-line');
                const range = studio.querySelector('.split-range');
                const pctLabel = studio.querySelector('.split-pct-label');
                const modeLabel = studio.querySelector('.split-mode-label');
                const modeBtns = studio.querySelectorAll('.studio-btn');

                function updateView() {
                    if (mode === 'split') {
                        overlayWrap.style.clipPath = `polygon(${splitPos}% 0, 100% 0, 100% 100%, ${splitPos}% 100%)`;
                        overlayImg.style.opacity = '1';
                        baseImg.style.display = 'block';
                        divider.style.display = 'block';
                        divider.style.left = splitPos + '%';
                        range.value = splitPos;
                        pctLabel.textContent = splitPos + '%';
                        modeLabel.textContent = '분할 위치:';
                    } else if (mode === 'blend') {
                        overlayWrap.style.clipPath = 'none';
                        overlayImg.style.opacity = String(blendOpacity / 100);
                        baseImg.style.display = 'block';
                        divider.style.display = 'none';
                        range.value = blendOpacity;
                        pctLabel.textContent = blendOpacity + '%';
                        modeLabel.textContent = '중첩 투명도:';
                    } else if (mode === 'orig') {
                        overlayWrap.style.clipPath = 'polygon(100% 0, 100% 0, 100% 100%, 100% 100%)';
                        overlayImg.style.opacity = '0';
                        baseImg.style.display = 'block';
                        divider.style.display = 'none';
                        range.value = 0;
                        pctLabel.textContent = '0%';
                        modeLabel.textContent = '원본 100%';
                    } else if (mode === 'heat') {
                        overlayWrap.style.clipPath = 'none';
                        overlayImg.style.opacity = '1';
                        baseImg.style.display = 'none';
                        divider.style.display = 'none';
                        range.value = 100;
                        pctLabel.textContent = '100%';
                        modeLabel.textContent = '히트맵 100%';
                    }
                }

                modeBtns.forEach(b => b.addEventListener('click', (e) => {
                    e.stopPropagation();
                    modeBtns.forEach(btn => btn.classList.remove('active'));
                    b.classList.add('active');
                    mode = b.dataset.mode;
                    updateView();
                }));

                range.addEventListener('input', (e) => {
                    e.stopPropagation();
                    const val = parseInt(range.value, 10);
                    if (mode === 'blend') blendOpacity = val;
                    else {
                        splitPos = val;
                        if (mode !== 'split') {
                            mode = 'split';
                            modeBtns.forEach(btn => btn.classList.toggle('active', btn.dataset.mode === 'split'));
                        }
                    }
                    updateView();
                });

                studio.querySelectorAll('button[data-quick]').forEach(b => {
                    b.addEventListener('click', (e) => {
                        e.stopPropagation();
                        const val = parseInt(b.dataset.quick, 10);
                        if (mode === 'blend') blendOpacity = val;
                        else {
                            splitPos = val;
                            if (mode !== 'split') {
                                mode = 'split';
                                modeBtns.forEach(btn => btn.classList.toggle('active', btn.dataset.mode === 'split'));
                            }
                        }
                        updateView();
                    });
                });

                let isDragging = false;
                function handlePointer(clientX) {
                    const rect = viewport.getBoundingClientRect();
                    if (!rect.width) return;
                    let pct = Math.round(((clientX - rect.left) / rect.width) * 100);
                    pct = Math.max(0, Math.min(100, pct));
                    if (mode === 'blend') blendOpacity = pct;
                    else {
                        splitPos = pct;
                        if (mode !== 'split') {
                            mode = 'split';
                            modeBtns.forEach(btn => btn.classList.toggle('active', btn.dataset.mode === 'split'));
                        }
                    }
                    updateView();
                }

                viewport.addEventListener('mousedown', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    isDragging = true;
                    handlePointer(e.clientX);
                });
                window.addEventListener('mousemove', (e) => {
                    if (!isDragging) return;
                    e.preventDefault();
                    handlePointer(e.clientX);
                });
                window.addEventListener('mouseup', () => { isDragging = false; });

                viewport.addEventListener('touchstart', (e) => {
                    if (e.touches.length > 0) {
                        isDragging = true;
                        handlePointer(e.touches[0].clientX);
                    }
                }, { passive: true });
                viewport.addEventListener('touchmove', (e) => {
                    if (isDragging && e.touches.length > 0) {
                        handlePointer(e.touches[0].clientX);
                    }
                }, { passive: true });
                viewport.addEventListener('touchend', () => { isDragging = false; });

                updateView();
                slot.innerHTML = '';
                slot.appendChild(studio);
            } catch (e) {
                slot.innerHTML = `<span class="note">스튜디오 로딩 실패: ${shown(mediaErrorText(e))}</span>`;
            }
        }

        /* ── exports ─────────────────────────────── */
        function download(blob, name) {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = name; a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        }
        function closeMenu() { $('export-menu').open = false; }
        $('exp-json').addEventListener('click', () => {
            if (exportBlocked()) return;
            closeMenu();
            if (!results.length) { toast('분석 결과가 없습니다', true); return; }
            const enriched = results.map(e => {
                const rev = reviewStore[itemKey(e.item)];
                return rev ? { ...e.item, review: rev } : e.item;
            });
            // R18-3: a cancelled scan's export says it is partial (an object with the note and the summary).
            const partial = partialResultNote(lastProvenance.summary);
            const exported = partial ? { partial_result: partial, summary: lastProvenance.summary, items: enriched } : enriched;
            download(new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' }), 'deepfake-lens-results.json');
        });
        $('exp-csv').addEventListener('click', () => {
            if (exportBlocked()) return;
            closeMenu();
            if (!results.length) { toast('분석 결과가 없습니다', true); return; }
            const f = csvCell;
            // BOM prefix keeps Korean intact when the CSV is opened in Excel.
            const partial = partialResultNote(lastProvenance.summary);  // R18-3: a cancelled scan's CSV starts with the note
            let csv = '﻿' + (partial ? `# ${partial}\n` : '') + '결론,등급,재현 가능한 기록 근거,통계적 근거,어휘적 근거,검사 실패,출처 추정,모델,파일명,검토,메모,판정 문장\n';
            results.forEach(e => {
                const item = e.item, r = item.result || {};
                const rev = reviewStore[itemKey(item)] || {};
                const ev = r.evidence || [];
                const titles = kind => ev.filter(x => x.kind === kind).map(x => x.title).join(' / ');
                const failedCov = (r.coverage || []).filter(c => c.status === 'failed').map(coverageText).join(' / ');
                csv += [f(riskLabel(verdictOf(item))), f(r.grade === 'reference' ? '참고' : (r.grade && verdictOf(item) !== 'undetermined' ? '근거' : '')), f(titles('deterministic')), f(titles('statistical')), f(titles('lexical')), f(failedCov), f((r.source_guess && r.source_guess.label) || ''), f((r.model_analysis && (r.model_analysis.display_name || r.model_analysis.model)) || ''), f(item.name || item.path), f(rev.star ? '검토됨' : ''), f(rev.note || ''), f(r.verdict || '')].join(',') + '\n';
            });
            download(new Blob([csv], { type: 'text/csv;charset=utf-8' }), 'deepfake-lens-results.csv');
        });
        $('exp-clear').addEventListener('click', () => {
            if (exportBlocked()) return;
            closeMenu();
            // G-9(d): clearing is not undoable — ask first.
            if (!results.length || !window.confirm(`화면의 검사 결과 ${results.length}개를 지웁니다. 검토 기록은 남습니다. 지울까요?`)) return;
            results = [];
            resetExportState();  // L-1: the changed-file state goes with the results
            lastProvenance = { coverage: null, thresholds: null, summary: null };
            selectedEntry = null;
            renderResults();
            updateCaseLine();
            $('ledger-empty-h').focus();
        });

        /* ── report screen ───────────────────────── */
        const GROUPS = [['manipulation_evidence', 'inc-manip'], ['authenticity_evidence', 'inc-auth'], ['undetermined', 'inc-undet'], ['other', 'inc-other']];
        let groupsTouched = false;
        GROUPS.forEach(([, id]) => $(id).addEventListener('change', () => { groupsTouched = true; renderReport(); }));
        function selectedEntries() {
            if (!groupsTouched) {
                // Default exhibits: the evidence-bearing groups only.
                GROUPS.forEach(([band, id]) => { $(id).checked = band === 'manipulation_evidence' || band === 'authenticity_evidence'; });
            }
            const on = new Set(GROUPS.filter(([, id]) => $(id).checked).map(([band]) => band));
            // GUI-REV2 H-1: uploaded files are never exhibits (the server lists them as
            // 서명 제외); the order here is the order the statement numbers them in.
            // GUI-REV7 F-13: the exhibits are numbered in this order (the ledger's 결론순), not in the order the
            // examiner ticked the groups — the hint and the checklist say so.
            return results.filter(e => on.has(itemBand(e)) && e.item.source !== 'upload').sort(byVerdict);
        }
        const TAGS = { req: ['필수', 'tag-req'], ok: ['확인됨', 'tag-ok'], info: ['알림', 'tag-info'], warn: ['주의', 'tag-warn'] };
        function reportChecks(sel) {
            const out = [];
            if (!results.length) out.push({ tag: 'req', text: '검사 결과가 없습니다. 먼저 폴더를 검사하십시오.', act: ['검사 화면으로', () => navigate('scan')] });
            // K-2: files changed since the scan block the signed formats until a rescan.
            if (changedPaths.size) out.push({ tag: 'req', text: `검사 뒤 바뀐 파일 ${changedPaths.size}개(다시 검사 필요) — 서버가 내보내기를 거부했습니다.`, act: ['다시 검사', rescanFolder] });
            if (results.length && caseUnconfirmed()) {
                const earlier = caseFolder.startsWith('root:') ? displayName(caseFolder.slice(5).split(/[\\/]/).filter(Boolean).pop() || caseFolder.slice(5)) : '올린 파일';
                out.push({ tag: 'req', text: `사건 정보는 다른 검사(${earlier})에서 입력되었습니다 — 이 폴더의 사건이 맞는지 확인하십시오.`, act: ['이 폴더의 사건이 맞음', () => { confirmCaseFolder(); renderReport(); }] });
            }
            const no = caseValue('case-no'), nm = caseValue('case-name');
            out.push(no && nm
                ? { tag: 'ok', text: '사건: ' + no + ' · ' + nm }
                : { tag: 'req', text: '사건번호와 사건명이 비어 있습니다 — 증거설명서에 필요합니다.', act: ['입력', () => $(no ? 'case-name' : 'case-no').focus()] });
            const pl = caseValue('case-plaintiff'), df = caseValue('case-defendant');
            if (!pl || !df) {
                // H-3: the empty box is named as the form names it; the client is on the chosen side.
                // J-10: "원고·고소인 칸과 피고·피의자 칸" — the old "·" join ran the box names together.
                const empty = [!pl ? '원고·고소인 칸' : '', !df ? '피고·피의자 칸' : ''].filter(Boolean).join('과 ');
                const client = submitterSide() === 'defendant' ? '피고·피의자(의뢰인)' : submitterSide() === 'plaintiff' ? '원고·고소인(의뢰인)' : '';
                out.push({ tag: 'info', text: `${empty}이 비어 있습니다 — 증거설명서에는 "(… 입력)" 자리표시로 남습니다.` + (client ? ` 의뢰인 자리: ${client}.` : ''),
                    act: ['입력', () => $(pl ? 'case-defendant' : 'case-plaintiff').focus()] });
            }
            const side = submitterSide();
            out.push(side
                ? { tag: 'ok', text: `의뢰 측: ${SIDES[side][0]} — 서증은 ${SIDES[side][1]}호증으로 번호를 붙입니다.` }
                : { tag: 'req', text: '의뢰 측을 고르지 않았습니다 — 기본값은 없습니다(증거설명서에 필요).', act: ['고르기', () => document.querySelector('input[name="submitter-side"]').focus()] });
            if (results.length) {
                const partial = partialResultNote(lastProvenance.summary);
                out.push(partial ? { tag: 'warn', text: partial } : { tag: 'ok', text: '전체 검사 결과입니다(중단 없음).' });
                // The scan's own limits (G-9c); the statement prints them in its 무결성/해석 고지.
                out.push({ tag: 'info', text: '검사의 한계: ' + reportLimitLines().join(' · ') });
                if (lastScanJobNote) out.push({ tag: 'warn', text: '검사 작업 기록: ' + lastScanJobNote });
                const listed = listingCheck(a4Listing);
                if (listed) out.push(listed);
                // GUI-REV3 J-8: uploaded files are never exhibits — said plainly, and left
                // out of the appendix count (they go to the 서명 제외 box).
                const uploads = results.filter(e => e.item.source === 'upload').length;
                const scanned = results.length - uploads;
                if (uploads) out.push({ tag: 'info', text: `올린 파일 ${uploads}개는 증거설명서의 서증이 될 수 없습니다 — 서버가 보관한 검사 작업의 행이 아니어서 서명 제외(클라이언트 제공 결과)로 실립니다.` });
                if (!scanned) out.push({ tag: 'req', text: '올린 파일만 있어 증거설명서를 만들 수 없습니다 — 폴더를 검사하십시오. 감정 보고서·HTML·서명된 JSON은 만들 수 있습니다.', act: ['검사 화면으로', () => navigate('scan')] });
                else if (!sel.length) out.push({ tag: 'req', text: '증거설명서의 서증으로 넣을 파일을 고르십시오. 근거 있는 파일이 없으면 판단 불가 칸을 고를 수 있습니다.', act: ['고르기', () => $('inc-manip').focus()] });
                else if (sel.length < scanned) out.push({ tag: 'info', text: `서증 ${sel.length}개(사건 대장의 결론순으로 번호) · 나머지 ${scanned - sel.length}개는 증거설명서 표에 번호 없이 "서증 외(검사 행)"로 남습니다. 다른 형식에는 ${results.length}개가 모두 실립니다.` });
                // GUI-REV2 H-4: say which appendix rows carry more than a verdict.
                const appendix = results.filter(e => !sel.includes(e) && e.item.source !== 'upload' && itemBand(e) === 'undetermined');
                const cueRows = appendix.filter(e => cueEvidence(e.item.result).length).length;
                // J-2: duplicates of a manipulated file that go to the appendix.
                const dupManip = results.filter(e => !sel.includes(e) && e.item.source !== 'upload' && e.item.status === 'duplicate' &&
                    originOf(e.item) && verdictOf(originOf(e.item)) === 'manipulation_evidence').length;
                if (dupManip) out.push({ tag: 'warn', text: `번호 없이 남는 중복 행 중 조작·생성 근거 있음 ${dupManip}개 — 원본과 내용이 같은 사본입니다. 서증으로 넣으려면 분석 못 함 칸을 고르십시오.` });
                const failedRows = appendix.filter(e => failedChecks(e.item).length).length;
                if (cueRows || failedRows) out.push({ tag: 'warn', text: `번호 없이 남는 판단 불가 행 중 단서 ${cueRows}개 · 검사 실패 ${failedRows}개 — 서증 번호를 붙이려면 판단 불가 칸을 고르십시오.`, act: ['판단 불가 넣기', () => { $('inc-undet').checked = true; groupsTouched = true; renderReport(); $('inc-undet').focus(); }] });
            }
            if (pdfAvailable === false) out.push({ tag: 'warn', text: 'PDF 엔진(pymupdf)이 설치되어 있지 않아 PDF는 만들 수 없습니다 — HTML 보고서와 서명된 JSON은 만들 수 있습니다.' });
            // DESK-6: where the signing key comes from (and so where a report can be verified).
            const signed = signingCheck(signingInfo);
            if (signed) out.push(signed);
            return out;
        }
        // Merge of phase0 R22-1: the 기록되지 않은 파일 counts are the server's own listing of the
        // folder (its scan job, else its own walk), or "확인 불가" — said in the checklist as the
        // preview (the same server build) shows it. null until a preview has been drawn.
        function listingCheck(listing) {
            if (!listing || typeof listing !== 'object') return null;
            if (listing.source === 'unknown') {
                return { tag: 'warn', text: '기록되지 않은 파일: 확인 불가 — ' + String(listing.reason || '사유 기록 없음') +
                    '. 서버가 이 폴더의 파일 목록을 다시 확인하지 못해 문서에 "확인 불가"로 실립니다(없음이 아닙니다).' };
            }
            const missing = Number.isInteger(listing.not_in_report) ? listing.not_in_report : 0;
            if (missing > 0) {
                return { tag: 'warn', text: `폴더에 있으나 이 보고서에 행이 없는 파일 ${missing}개 — 문서의 기록되지 않은 파일에 "보고서에 행 없음"으로 실립니다.`, act: ['다시 검사', rescanFolder] };
            }
            return { tag: 'ok', text: '기록되지 않은 파일: 서버가 ' + (listing.source === 'scan_job' ? '이 검사 작업으로' : '폴더를 다시 읽어') + ' 확인했습니다.' };
        }
        function exportBlockReason(kind, sel) {
            if (scanBusy) return '새 검사가 진행 중입니다 — 끝난 뒤 내보내십시오. 지금 화면의 결과는 이전 검사입니다.';
            if (!results.length) return '검사 결과가 없습니다.';
            if (changedPaths.size) return `검사 뒤 바뀐 파일 ${changedPaths.size}개 — 다시 검사하기 전에는 서명 형식을 내보낼 수 없습니다.`;
            if ((kind === 'evidence' || kind === 'pdf') && caseUnconfirmed()) return '사건 정보가 이 폴더의 것인지 먼저 확인하십시오(확인 목록).';
            // GUI-REV6 F-2: with upload rows only there is nothing to choose — the reason was "고르십시오".
            if (kind === 'evidence' && !results.some(e => e.item.source !== 'upload')) return '올린 파일만 있어 증거설명서를 만들 수 없습니다 — 폴더를 검사하십시오.';
            if (kind === 'evidence' && !sel.length) return '증거설명서의 서증으로 넣을 파일을 고르십시오.';
            if ((kind === 'evidence' || kind === 'pdf') && pdfAvailable === false) return 'PDF(증거설명서·감정 보고서)는 PDF 엔진(pymupdf)이 설치되어 있지 않아 만들 수 없습니다.';
            if (kind === 'evidence' && !(caseValue('case-no') && caseValue('case-name'))) return '사건번호와 사건명을 채우면 증거설명서를 내보낼 수 있습니다.';
            if (kind === 'pdf' && !submitterSide()) return '의뢰 측(원고·고소인 측 / 피고·피의자 측)을 고르면 감정 보고서 PDF의 호증 칸이 정해집니다.';
            if (kind === 'evidence' && !submitterSide()) return '의뢰 측(원고·고소인 측 / 피고·피의자 측)을 고르면 증거설명서를 내보낼 수 있습니다.';
            return '';
        }
        // G-9(b): the note says which formats can be made now and why the others cannot.
        function exportNote(sel) {
            const ready = Object.keys(EXPORT_BUTTONS).filter(kind => !exportBlockReason(kind, sel)).map(kind => EXPORT_LABELS[kind]);
            const blocked = Object.keys(EXPORT_BUTTONS).filter(kind => exportBlockReason(kind, sel));
            const reasons = [...new Set(blocked.map(kind => exportBlockReason(kind, sel)))];
            const head = ready.length ? `지금 내보낼 수 있는 형식: ${ready.join(', ')}.` : '지금은 내보낼 수 있는 형식이 없습니다.';
            return [head, ...reasons].join(' ');
        }
        const EXPORT_LABELS = { evidence: '증거설명서 PDF', pdf: '감정 보고서 PDF', html: 'HTML 보고서', json: '서명된 JSON' };
        const EXPORT_BUTTONS = { evidence: 'exp-evidence', pdf: 'exp-pdf', html: 'exp-report', json: 'exp-signed' };
        // GUI-REV7 F-9: #export-note is aria-live — it is written only when its text changes (a rewrite with the
        // same text was read out again), and not while the examiner types a case field (renderReport(true) from
        // its input event): the note follows when the field is left (change) or on the next full render.
        function exportNoteText() {
            return [exportNote(selectedEntries()), exportStickyNote].filter(Boolean).join(' ');
        }
        function setExportNote(text) {
            const note = $('export-note');
            if (note.textContent !== text) note.textContent = text;
        }
        function renderReport(typing) {
            const b = bandCounts();
            const sel = selectedEntries();
            // J-8: the group counts are the rows that can become exhibits (no uploads).
            const exhibitable = results.filter(e => e.item.source !== 'upload');
            GROUPS.forEach(([band, id]) => { $(id + '-n').textContent = exhibitable.filter(e => itemBand(e) === band).length + '개'; });
            $('inc-label').textContent = `${sel.length}개 선택`;
            renderChecks(sel);
            Object.entries(EXPORT_BUTTONS).forEach(([kind, id]) => setBusyButton($(id), Boolean(exportBlockReason(kind, sel))));
            if (!typing) setExportNote([exportNote(sel), exportStickyNote].filter(Boolean).join(' '));
            renderA4(sel, typing ? 500 : 250);
        }
        function renderChecks(sel) {
            const list = $('report-checks');
            list.innerHTML = '';
            reportChecks(sel).forEach(c => {
                const li = document.createElement('li');
                const tag = document.createElement('span');
                tag.className = 'tag ' + TAGS[c.tag][1];
                tag.textContent = TAGS[c.tag][0];
                const text = document.createElement('span');
                text.textContent = c.text;
                li.append(tag, text);
                if (c.act) {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'btn btn-sm';
                    btn.textContent = c.act[0];
                    btn.addEventListener('click', c.act[1]);
                    li.appendChild(btn);
                }
                list.appendChild(li);
            });
        }
        // GUI-REV2 H-2: the preview is drawn from the server's own statement template
        // (format=evidence-preview: the same build and the same case box, boxes and
        // signoff helpers the PDF renderer uses) — it cannot show a section the PDF
        // lacks or miss one it has. Built from the same server-held scan job as the export, unsigned and
        // without the change check; the export re-hashes the job's files (409 on a change) and signs.
        let a4Timer = null;
        let a4Seq = 0;
        let a4Listing = null;  // R22-1 merge: {source, not_in_report, reason} of the last preview
        // GUI-REV7 F-9: while a case field is typed in, the preview POST waits 500 ms after the last key (250 ms otherwise).
        function renderA4(sel, delay) {
            clearTimeout(a4Timer);
            a4Seq += 1;
            const box = $('a4');
            box.dataset.ready = '';
            // GUI-GATE (Linux reference run): a preview with nothing to draw is settled at once —
            // it said so but stayed "not ready" for good (e.g. a folder whose rows are all
            // 판단 불가 with that group unticked: 0 exhibits).
            if (!results.length) { a4Listing = null; box.innerHTML = '<p class="miss">검사 결과가 없습니다.</p>'; box.dataset.ready = String(a4Seq); return; }
            if (!sel.length) {
                box.innerHTML = '<p class="miss">고른 서증이 없습니다 — 서증으로 넣을 파일을 고르면 증거설명서 서식 그대로 미리 봅니다.</p>';
                box.dataset.ready = String(a4Seq);
                return;
            }
            const seq = a4Seq;
            a4Timer = setTimeout(() => loadA4(seq), delay || 250);
        }
        async function loadA4(seq) {
            const box = $('a4');
            const payload = exportPayload('evidence');
            payload.format = 'evidence-preview';
            try {
                const res = await apiFetch('/api/report?format=evidence-preview', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
                });
                const body = await res.json();
                if (seq !== a4Seq) return;
                const denied = res.status === 403 ? deniedFolderHint(String(body.error || ''), defaultFolder, readRoots, desktopMode, true) : '';
                box.innerHTML = res.ok ? a4Html(body) : `<p class="miss">미리보기를 만들지 못했습니다: ${shown(body.error || res.status)}${shown(denied)}</p>`;
                // Merge of R22-1: the server's listing of the folder (the 기록되지 않은 파일 box) feeds the checklist.
                a4Listing = res.ok && body.unrecorded_listing && typeof body.unrecorded_listing === 'object' ? body.unrecorded_listing : null;
                if (currentView === 'report') renderChecks(selectedEntries());
            } catch (e) {
                if (seq !== a4Seq) return;
                box.innerHTML = `<p class="miss">미리보기를 만들지 못했습니다: ${shown(e.message)}</p>`;
            }
            box.dataset.ready = String(seq);
        }
        // GUI-REV3 J-3: the preview's texts are the server template's own output — the
        // text the PDF prints. File names in it are already escaped by the server
        // (display_name/row_label), so applying displayName() again doubled every
        // backslash and turned the template's line breaks into the two characters
        // "\n". Each line is HTML-escaped as it is and the lines are joined with <br>.
        function docHtml(text) {
            return String(text == null ? '' : text).split('\n').map(line => escapeHtml(line)).join('<br>');
        }
        // GUI-REV3 J-6: before a side is chosen the server draws its default (원고·갑)
        // form; the preview marks every side-dependent text "(의뢰 측 미선택)" instead.
        const NO_SIDE = '(의뢰 측 미선택)';
        function unsidedText(text) {
            return String(text).replace(/[갑을] 제(\d+)호증/g, NO_SIDE + ' 제$1호증')
                .replace(/(원고\(고소인\)|피고\(피의자\))(의 )?(소송대리인|대리인|변호인)/g, NO_SIDE + '$2제출인');
        }
        function a4Html(body) {
            if (!submitterSide()) {
                body = Object.assign({}, body, {
                    entries: (body.entries || []).map(e => Object.assign({}, e, { exhibit_no: unsidedText(e.exhibit_no), purpose_of_proof: unsidedText(e.purpose_of_proof) })),
                    case_lines: (body.case_lines || []).map(unsidedText),
                    signoff: (body.signoff || []).map(unsidedText),
                    boxes: (body.boxes || []).map(([heading, boxLines]) => [heading, boxLines.map(unsidedText)]),
                });
            }
            const lines = list => list.map(line => `<p>${docHtml(line)}</p>`).join('');
            const head = (body.table_headers || []).map(h => `<th scope="col">${docHtml(h)}</th>`).join('');
            const flag = e => changedPaths.has(String(e.file_path)) ? '<br><span class="a4-flag">(검사 뒤 바뀜 — 내보낼 수 없음)</span>' : '';
            const rows = (body.entries || []).map(e => `<tr data-exhibit="${docHtml(e.exhibit_no)}" data-path="${shown(e.file_path)}"${changedPaths.has(String(e.file_path)) ? ' class="a4-changed"' : ''}>` +
                `<td>${docHtml(e.exhibit_no)}${flag(e)}</td><td>${docHtml(e.document_name)}</td><td>${docHtml(e.author_date)}</td><td class="a4-purpose">${docHtml(e.purpose_of_proof)}</td></tr>`).join('');
            const boxes = (body.boxes || []).map(([heading, boxLines]) => `<section class="a4-box"><div class="a4-h">${docHtml(heading)}</div>${lines(boxLines)}</section>`).join('');
            return `<div class="a4-top"><span>미리보기 — 서명 전</span><span>${submitterSide() ? '서버의 증거설명서 서식' : NO_SIDE + ' — 의뢰 측을 고르면 갑/을과 제출 문구가 정해집니다'}</span></div>` +
                // Merge of phase0 R23-4: the server gives the texts as the PDF draws them — a character
                // the PDF font has no glyph for is its \uNNNN code; said, so the codes are not a fault.
                (body.pdf_glyph_escapes ? '<p class="a4-glyph">PDF 글꼴에 없는 문자(히브리·아랍 문자, 이모지 등)는 PDF에서처럼 \\uXXXX 코드로 표시했습니다. 원래 이름은 장부와 서명된 JSON에 그대로 있습니다.</p>' : '') +
                `<div class="a4-title">${shown(body.title)}</div>` +
                `<section class="a4-box">${lines(body.case_lines || [])}${body.scan_root_line ? lines([body.scan_root_line]) : ''}${body.analysis_options_line ? lines([body.analysis_options_line]) : ''}</section>` +
                '<div class="a4-h a4-center">다        음</div>' +
                `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>` +
                boxes +
                ((body.unsigned || []).length ? `<section class="a4-box">${lines(body.unsigned)}</section>` : '') +
                `<div class="a4-signoff">${lines(body.signoff || [])}<p class="a4-court">${docHtml(body.court)}</p></div>` +
                '<p class="a4-sign">서명은 내보낼 때 서버가 서명 키로 붙입니다(키가 없으면 "서명 없음"). 문서는 서버가 보관한 이 검사 작업의 행으로 만들며 파일을 다시 분석하지 않습니다. 내보낼 때 검사 때 해시한 파일의 SHA-256을 다시 계산해, 검사 뒤 바뀌었거나 지워진 파일(경로 중간의 폴더가 링크로 바뀐 파일도)이 있으면 내보내기를 거부합니다. 올린 파일만 서명 제외(클라이언트 제공 결과)로 실립니다.</p>';
        }

        const EXPORT_SPECS = {
            evidence: ['evidence', 'application/pdf', 'deepfake-lens-evidence-statement.pdf', '증거설명서(PDF)'],
            pdf: ['pdf', 'application/pdf', 'deepfake-lens-forensic-report.pdf', '감정 보고서(PDF)'],
            html: ['html', 'text/html', 'deepfake-lens-report.html', 'HTML 보고서'],
            json: ['json', 'application/json', 'deepfake-lens-signed-report.json', '서명된 JSON'],
        };
        // GUI-REV G-1: every artifact carries every row of the scan (the summary
        // is the whole scan's). The group choice only names the evidence
        // statement's exhibits; the other rows go to its signed appendix.
        function exportPayload(kind) {
            const extra = { format: EXPORT_SPECS[kind][0] };
            // GUI-REV2 H-3: the forensic PDF's exhibit box follows the chosen side (갑/을).
            // J-6: no side, no 갑/을 (the export waits for the side anyway).
            if (kind === 'pdf') extra.exhibit_no = (submitterSide() ? (submitterSide() === 'defendant' ? '을 ' : '갑 ') : '') + '제        호증';
            // P0R-6: exhibit_paths are rows of the job, numbered in this order; only with a job.
            if (kind === 'evidence' && lastScanJob) extra.exhibit_paths = selectedEntries().map(e => e.item.path);
            return reportPayload(extra);
        }
        // A refused export names the rows behind it (up to five).
        const EXPORT_ERROR_PATHS = 5;
        // Merge of phase0 P0R-6: a 409 row is {path, reason} only — the server compares the job's files on disk
        // with their scan-time digests and says why each changed (no verdict or status of its own any more).
        function changeText(row) {
            return String((row && row.reason) || '검사 뒤 바뀜');
        }
        function exportErrorText(body, status) {
            const parts = [String((body && (body.error || body.detail)) || ('서버 오류 (HTTP ' + status + ')'))];
            const listed = (rows, render) => {
                const shownRows = rows.slice(0, EXPORT_ERROR_PATHS).map(render).join(', ');
                return shownRows + (rows.length > EXPORT_ERROR_PATHS ? ` 외 ${rows.length - EXPORT_ERROR_PATHS}개` : '');
            };
            // R19-13 (409): files changed after the scan, each with the server's reason.
            const changed = Array.isArray(body && body.changed_rows) ? body.changed_rows : [];
            if (changed.length) parts.push('검사 뒤 바뀐 파일: ' + listed(changed, row => String(row.path) + '(' + changeText(row) + ')'));
            return parts.join(' — ');
        }
        // R19-13: the rows the server found changed after the scan — marked in the ledger
        // until the folder is scanned again.
        let changedPaths = new Set();
        let changedInfo = new Map();  // K-4: path -> what changed, for the evidence panel
        function markChangedRows(rows) {
            changedPaths = new Set(rows.map(row => String(row.path)));
            changedInfo = new Map(rows.map(row => [String(row.path), changeText(row)]));
            if (results.length) renderList();
            if (currentView === 'report') renderReport();  // the checklist above the box first
            renderExportAlert();
        }
        // GUI-REV4 K-2: the refusal is said next to the export buttons (role=alert), where
        // the examiner pressed — the top error region is off screen there. GUI-REV5 L-2: every
        // export failure is said here (not only a 409), once (Info c: no second, assertive
        // copy in the error region), with 다시 검사 where scanning again is the remedy.
        let exportFailure = null;  // {seq, text, rescan} — the latest failed export of these results
        function renderExportAlert() {
            const box = $('export-alert');
            box.hidden = changedPaths.size === 0 && !exportFailure;
            if (box.hidden) return;
            const changedHead = `검사 뒤 바뀐 파일 ${changedPaths.size}개 — 내보내지 않았습니다. 폴더를 다시 검사하십시오.`;
            $('export-alert-text').textContent = changedPaths.size ? changedHead : exportFailure.text;
            const fail = $('export-alert-fail');
            fail.hidden = !(changedPaths.size && exportFailure);
            fail.textContent = fail.hidden ? '' : exportFailure.text;
            $('export-alert-rescan').hidden = !(changedPaths.size || exportFailure.rescan);
            const list = $('export-alert-list');
            list.innerHTML = '';
            const paths = [...changedPaths];
            paths.slice(0, 5).forEach(path => {
                const li = document.createElement('li');
                li.textContent = displayName(path) + ' — ' + (changedInfo.get(path) || '바뀜');
                list.appendChild(li);
            });
            if (paths.length > 5) {
                const li = document.createElement('li');
                li.textContent = `외 ${paths.length - 5}개`;
                list.appendChild(li);
            }
            list.hidden = paths.length === 0;
            // Brought into view where the export button was pressed (no focus move).
            if (currentView === 'report' && box.getClientRects().length) box.scrollIntoView({ block: 'nearest' });
        }
        // GUI-REV5 L-3: what a successful download does to the export feedback. Exports run
        // concurrently; one requested before a refusal can land after it. It never clears the
        // changed-file state (those files still differ) and the note says the file was made
        // before the refusal; a failure of a later-started export is kept too.
        function exportSuccessOutcome(label, seq, changedCount, failure) {
            if (changedCount > 0) {
                return { clearFailure: false, note: label + ` — 내려받았습니다. 다만 검사 뒤 바뀐 파일이 확인되기 전에 요청한 파일입니다 — 바뀐 파일 ${changedCount}개가 남아 있으니 다시 검사하십시오.` };
            }
            if (failure && failure.seq > seq) {
                return { clearFailure: false, note: label + ' — 내려받았습니다. 뒤에 요청한 내보내기는 실패했습니다(아래 알림).' };
            }
            return { clearFailure: true, note: label + ' — 내려받았습니다.' };
        }
        $('export-alert-rescan').addEventListener('click', () => rescanFolder());
        function rescanFolder() {
            if (lastScanRoot) $('folder-path').value = lastScanRoot;
            // GUI-REV4 K-3: the focus target goes with the navigation — focusing after it lost
            // to the hashchange, which focused the screen heading.
            navigate('scan', $('analyze-btn'));
        }
        // GUI-REV4 K-1: the results an export was asked for. A response that arrives after
        // other results replaced them is ignored — no marks, no error, no download.
        let resultsGeneration = 0;
        function exportResponseAction(startGen, nowGen, status, changedCount, ok) {
            if (startGen !== nowGen) return '무시';
            if (status === 409 && changedCount > 0) return '바뀜';
            return ok ? '받기' : '오류';
        }
        // GUI-REV5 L-1: every path that replaces the results on screen (new results, an
        // upload, clearing) resets the export state in one place — the changed-file marks,
        // the alert next to the export buttons and the checklist item went stale on 지우기.
        function resetExportState() {
            changedPaths = new Set();
            changedInfo = new Map();
            resultsGeneration += 1;  // K-1: earlier export responses no longer apply
            exportFailure = null;
            renderExportAlert();
            if (currentView === 'report') renderReport();
        }
        // GUI-REV5 Info (b): a note that stays in the export note until the next export action.
        let exportStickyNote = '';
        let exportSeq = 0;
        async function exportReport(kind) {
            if (exportBlocked()) return;
            const btn = $(EXPORT_BUTTONS[kind]);
            const sel = selectedEntries();
            exportStickyNote = '';
            const why = exportBlockReason(kind, sel);
            if (why) { $('export-note').textContent = why; return; }
            if (btn.dataset.busy) return;
            btn.dataset.busy = '1';
            const spec = EXPORT_SPECS[kind];
            const startGen = resultsGeneration;
            const seq = ++exportSeq;
            // GUI-REV4 K-7: the format name is followed by " — …" (no particle after it: "JSON를").
            $('export-note').textContent = spec[3] + ' — 만드는 중입니다. 서버가 이 검사 작업의 파일이 검사 뒤 바뀌지 않았는지 확인합니다.';
            const ignored = () => {
                exportStickyNote = spec[3] + ' 요청은 이전 검사의 것이라 무시했습니다. 지금 결과로 다시 내보내십시오.';
                toast(spec[3] + ': 이전 검사에 대한 응답이라 무시했습니다.');
                $('export-note').textContent = exportStickyNote;
            };
            const failed = (text, rescan) => {
                exportFailure = { seq, text: spec[3] + ' — 만들지 못했습니다: ' + text, rescan };
                renderExportAlert();
                $('export-note').textContent = spec[3] + ' — 만들지 못했습니다.';
            };
            try {
                const res = await apiFetch('/api/report?format=' + spec[0], {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(exportPayload(kind)),
                });
                const type = res.headers.get('Content-Type') || '';
                if (!res.ok || !type.includes(spec[1])) {
                    let message;
                    let changedRows = [];
                    let rescan = false;
                    if (exportResponseAction(startGen, resultsGeneration, res.status, 0, false) === '무시') { ignored(); return; }
                    if (type.includes('application/json')) {
                        try {
                            const errorBody = await res.json();
                            message = exportErrorText(errorBody, res.status);
                            if (res.status === 409 && Array.isArray(errorBody.changed_rows)) changedRows = errorBody.changed_rows;
                        } catch (e) { message = '서버 오류 (HTTP ' + res.status + ')'; }
                    } else {
                        message = await apiError(res);
                    }
                    // P0R-6: the server-held job is unknown or expired (404 — e.g. after a server restart with
                    // the job held in memory only) or unusable (a 409 that names no changed file): scanning again
                    // makes a new job.
                    rescan = Boolean(lastScanJob) && (res.status === 404 || (res.status === 409 && !changedRows.length));
                    const action = exportResponseAction(startGen, resultsGeneration, res.status, changedRows.length, false);
                    if (action === '무시') { ignored(); return; }
                    if (action === '바뀜') {
                        // Info (c): said once — in the alert next to the buttons (markChangedRows).
                        markChangedRows(changedRows);
                        $('export-note').textContent = spec[3] + ' — 만들지 못했습니다.';
                    } else {
                        // RISK B4: a job whose folder is no longer registered (403) says how to register it again.
                        if (res.status === 403) message += deniedFolderHint(message, defaultFolder, readRoots, desktopMode, true);
                        failed(message, rescan);
                    }
                    return;
                }
                const blob = await res.blob();
                if (exportResponseAction(startGen, resultsGeneration, res.status, 0, true) === '무시') { ignored(); return; }
                download(blob, spec[2]);
                const outcome = exportSuccessOutcome(spec[3], seq, changedPaths.size, exportFailure);
                if (outcome.clearFailure) exportFailure = null;
                renderExportAlert();
                $('export-note').textContent = outcome.note;
            } catch (e) {
                failed(e.message, false);
            } finally {
                delete btn.dataset.busy;
            }
        }
        Object.entries(EXPORT_BUTTONS).forEach(([kind, id]) => $(id).addEventListener('click', () => exportReport(kind)));

        /* ── quick check ───────────────────────────── */
        function startElapsed(el, text) {
            const t0 = Date.now();
            el.textContent = text;
            const timer = setInterval(() => {
                el.textContent = `${text} (${Math.floor((Date.now() - t0) / 1000)}초)`;
            }, 1000);
            return () => { clearInterval(timer); el.textContent = ''; };
        }
        $('qc-text-btn').addEventListener('click', () => {
            if (isBusyButton($('qc-text-btn'))) return;
            const text = $('qc-text').value;
            if (!text.trim()) { toast('검사할 텍스트를 입력하세요', true); $('qc-text').focus(); return; }
            const body = { text };
            const wm = $('qc-wm').value.trim();
            if (wm) body.watermark_secret = wm;
            runQuickCheck({ method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
        });
        function mediaTagFor(file, cls) {
            const ext = (file.name.split('.').pop() || '').toLowerCase();
            const img = ['png','jpg','jpeg','webp','gif','bmp'];
            const vid = ['mp4','mov','m4v','webm','mkv','avi'];
            const aud = ['wav','mp3','m4a','ogg','flac','aac','opus'];
            const tag = img.includes(ext) ? 'img' : vid.includes(ext) ? 'video' : aud.includes(ext) ? 'audio' : null;
            if (!tag) return null;
            const el = document.createElement(tag);
            el.className = cls;
            if (tag !== 'img') el.controls = true;
            el.src = URL.createObjectURL(file);
            return el;
        }

        $('qc-file-btn').addEventListener('click', () => { if (!isBusyButton($('qc-file-btn'))) $('qc-file').click(); });
        $('qc-file').addEventListener('change', e => {
            const file = e.target.files[0];
            e.target.value = '';
            if (!file) return;
            // Show what is being analyzed so the examiner can eyeball the
            // file against the reported signals.
            const pv = $('qc-preview');
            pv.innerHTML = '';
            const el = mediaTagFor(file, 'preview-media');
            if (el) pv.appendChild(el);
            const form = new FormData();
            form.append('file', file, file.name);
            runQuickCheck({ method: 'POST', body: form });
        });

        // R31-5 (round 31): the quick check's archive toast said "압축 해제 — N개 파일 분석됨" with N the number of rows —
        // the container row and unsupported members counted, and a corrupt or empty archive read "1개 파일 분석됨".
        // It counts member rows whose status is "analyzed", and says when the archive could not be opened (its
        // archive check failed or was skipped) or held nothing analyzed.
        function archiveCheckToast(items) {
            const members = items.filter(it => it && it.member);
            const analyzed = members.filter(it => it.status === 'analyzed').length;
            const container = items.find(it => it && !it.member) || {};
            if (analyzed > 0) {
                const rest = members.filter(it => it.status !== 'analyzed' && it.kind !== 'archive').length;
                return `압축 해제 — 구성 파일 ${analyzed}개 분석됨` + (rest ? `, ${rest}개 분석 못 함 — 원장에서 사유를 보십시오` : '');
            }
            const coverage = ((container.result || {}).coverage || []).find(c => c && c.check === 'archive');
            if (container.status !== 'expanded' && (!coverage || coverage.status !== 'ran')) {
                return '압축 파일을 열지 못했습니다 — 원장에서 사유를 보십시오';
            }
            return '압축 파일에서 분석한 구성 파일이 없습니다 — 원장에서 사유를 보십시오';
        }

        // GUI-REV7 F-2: the question asked before an archive's rows replace a folder scan's ('' — nothing to ask).
        function archiveReplaceQuestion(shownRows, scanJob) {
            if (!shownRows || !scanJob) return '';
            return `지금 보이는 검사 결과 ${shownRows}개를 이 압축 파일의 결과로 바꿉니다. 바꾼 뒤에는 폴더를 다시 검사하기 전까지 증거설명서를 만들 수 없습니다. 바꿀까요?`;
        }

        async function runQuickCheck(options) {
            const box = $('qc-out');
            const stop = startElapsed($('qc-status'), '전체 검사 실행 중… 뉴럴 모델 로딩 시 수십 초');
            ['qc-text-btn', 'qc-file-btn'].forEach(id => setBusyButton($(id), true));
            try {
                const data = await apiJson('/api/check', options);
                if (data.error) {
                    box.innerHTML = `<div class="verdict c-red">오류: ${shown(data.error)}</div>`;
                    $('qc-live').textContent = '빠른 검사 오류';
                    return;
                }
                if (data.mode === 'files') {
                    // Archive upload: members are analyzed as their own rows — they replace the rows on screen.
                    // GUI-REV7 F-2: a folder scan's rows (the only ones an evidence statement is made from) are
                    // replaced only after the examiner says so; 취소 leaves them and the server job as they are.
                    const question = archiveReplaceQuestion(results.length, lastScanJob);
                    if (question && !window.confirm(question)) {
                        const kept = document.createElement('p');
                        kept.className = 'hint';
                        kept.textContent = '압축 파일의 결과를 싣지 않았습니다 — 지금 보이는 폴더 검사 결과를 그대로 둡니다.';
                        box.replaceChildren(kept);
                        $('qc-live').textContent = kept.textContent;
                        return;
                    }
                    box.innerHTML = '';
                    lastScanRoot = '';
                    lastScanRootB64 = '';
                    lastUploadScope = 'check:' + Date.now().toString(36);
                    lastScanOptions = {};
                    lastScanJob = '';
                    lastScanJobNote = '';
                    retireErrors();
                    processResults(data);
                    dropRetiredErrors();  // GUI-REV7 F-4: the archive's rows replaced the scan they described
                    toast(archiveCheckToast(data.items || []));  // R31-5: what was analyzed, not the row count
                    navigate('ledger', $('sum-h'));
                    return;
                }
                renderQuickCheck(data);
            } catch (e) {
                box.innerHTML = `<div class="verdict c-red">검사 실패: ${shown(e.message)}</div>`;
                $('qc-live').textContent = '빠른 검사 실패';
            } finally {
                stop();
                ['qc-text-btn', 'qc-file-btn'].forEach(id => setBusyButton($(id), false));
            }
        }

        function layer(title, node) {
            return `<div class="layer"><div class="lt">${escapeHtml(title)}</div>${node}</div>`;
        }

        function provenanceNoteHtml(data) {
            const cov = data.coverage || {};
            const thr = data.thresholds || {};
            const parts = [];
            const wa = cov.weights_available, wt = cov.weights_total;
            if (wt !== undefined && (wa || 0) === 0) parts.push('신경망 미탑재(측정 게이트 미충족) — 재현 가능한 기록 근거만 반영');
            else if (wt !== undefined && wa < wt) parts.push(`신경망 가중치 일부 탑재(${wa}/${wt})`);
            if (thr.provisional || thr.source === 'builtin_defaults') parts.push('잠정 임계값(미측정)');
            if (thr.in_sample) parts.push('임계값 in-sample(참고)');
            if (!parts.length) return '';
            return `<div class="prov-note">${parts.join(' · ')} — 이 결과는 결론과 근거로 읽으십시오; 점수는 보정된 경우에만 표시됩니다.</div>`;
        }

        function renderQuickCheck(data) {
            const box = $('qc-out');
            const item = data.item || {};
            const r = item.result || {};
            const verdict = verdictOf(item);
            const mk = VERDICT_MARK[verdict];
            // Conclusion first; the per-layer detail is one disclosure below (a
            // failed check stays visible).
            const top = [];
            top.push(`<div class="qc-head"><span class="mk mk-lg mk-${mk}" aria-hidden="true"></span>
                <div><div class="d-verdict vt-${mk}">${shown(shortVerdict(item))}</div>
                ${verdictLineHeadHtml(item, 'h3 class="qc-line"')}
                <div class="note">${shown(item.name || '')}</div></div></div>`);
            // GUI-REV G-7: a text result leads with the fixed legal limitation, and
            // its reference signals are shown with it — not inside the disclosure.
            if (r.grade === 'reference') {
                top.push(`<p class="legal-note qc-legal">${escapeHtml(TEXT_LEGAL_LIMITATION)}</p>`);
                const soft = (r.evidence || []).filter(e => e.kind !== 'deterministic' && e.direction !== 'neutral');
                if (soft.length) top.push(`<p class="hint">참고 신호 (결론에 쓰지 않음)</p><ol class="ev-list qc-soft">${soft.map(evItemHtml).join('')}</ol>`);
            }
            const decisive = decisiveEvidence(r, verdict);
            if (decisive.length) top.push(`<ol class="ev-list">${decisive.map(evItemHtml).join('')}</ol>`);
            // P0R-12: why the conclusion could be wrong, outside the disclosure.
            if ((r.conclusion_caveats || []).length) top.push(`<section class="d-caveat"><h3>이 결론이 틀릴 수 있는 이유</h3><ul>${r.conclusion_caveats.map(c => `<li>${shown(c)}</li>`).join('')}</ul></section>`);
            const failed = failedChecks(item);
            if (failed.length) top.push(`<section class="d-fail"><h3>실패한 검사 ${failed.length}</h3><ul>${failed.map(c => `<li>${shown(coverageText(c))}</li>`).join('')}</ul></section>`);
            top.push(`<p class="band-advice">${escapeHtml(limitAdvice(item))}</p>`);
            const parts = [];
            if (item.error) parts.push(`<div class="verdict">분석 실패: ${shown(item.error)}</div>`);
            if (item.result) {
                parts.push(verdictHeadHtml(r));
                parts.push(evidenceHtml(r));
                parts.push(coverageHtml(r));
            }
            parts.push(provenanceNoteHtml(data));

            const ma = r.model_analysis;
            if (ma) {
                parts.push(layer('외부 모델', `<div class="dt">외부 모델 원점수(미보정, 결론 불참여) — ${modelScoreText(ma)}</div>` +
                    ((ma.models || []).length ? modelMembersHtml(ma) : `<div class="note">${shown(ma.detail || '')}</div>`)));
            }
            if (data.advanced) {
                // D1: layer diagnostic — raw numbers with the fixed notice,
                // never a band or a "+points" weight (G4).
                const a = data.advanced, body = a.diagnostic || a;
                const sig = (body.signals || []).map(s => `<li>${shown(s.title)} — ${shown(s.detail)}</li>`).join('');
                const lim = (body.limitations || []).map(l => `<li>${shown(l)}</li>`).join('');
                parts.push(layer(`스타일/지문 분석 · 계층 진단(참고 신호 · 미측정) — 원점수 ${rawScoreText(a.raw_score)}`,
                    (a.notice ? `<div class="note">${shown(guiText(a.notice))}</div>` : '') +
                    (sig ? `<ul>${sig}</ul>` : '<div class="note">발동 신호 없음</div>') + (lim ? `<ul class="c-amber">${lim}</ul>` : '')));
            }
            if (data.forensic && data.mode === 'text') {
                // G-7/U-10: pasted text is not a file — C2PA and file metadata do not apply,
                // so the provenance layer's "C2PA 판독 불가" is not shown for it.
                parts.push(layer('메타데이터 / C2PA', '<div class="note">붙여넣은 텍스트는 파일이 아니어서 C2PA·메타데이터 같은 출처 기록이 없습니다 — 이 계층은 해당하지 않습니다.</div>'));
            } else if (data.forensic) {
                const fr = data.forensic, body = fr.diagnostic || fr;
                const sig = (body.signals || []).map(s => `<li>${shown(s.title)} — ${shown(s.detail)}</li>`).join('');
                const prov = (body.provenance_records || []).map(p => `<li>${shown(p.kind || p.source || p.standard || 'record')}: ${shown(p.summary || p.detail || p.provider || '')}</li>`).join('');
                parts.push(layer(`메타데이터 / C2PA · 계층 진단(참고 신호 · 미측정) — 원점수 ${rawScoreText(fr.raw_score)}`,
                    (fr.notice ? `<div class="note">${shown(guiText(fr.notice))}</div>` : '') +
                    ((sig ? `<ul>${sig}</ul>` : '') + (prov ? `<ul>${prov}</ul>` : '') || '<div class="note">단서 없음</div>')));
            }
            if (data.watermark) {
                const w = data.watermark;
                parts.push(layer('워터마크 (KGW)',
                    `<div class="kv"><b>측정</b><span>${shown(w.reference_note || '')}</span><b>z 점수</b><span>${w.z_score != null ? w.z_score : '없음'}</span><b>참고 원점수</b><span>${w.score != null ? w.score : '없음'}</span></div>`));
            }
            parts.push(listItems('참고 신호(미측정 휴리스틱 — 결론 불참여)', (r.reference_signals || []).map(s => ({ title: `${s.title} (${s.weight})`, detail: s.detail }))));
            parts.push(listItems('다음 확인', r.next_checks));
            parts.push(adviceHtml(verdictAdvice(r.verdict_code || 'undetermined', r.grade)));
            parts.push(`<div class="caveat">결론은 재현 가능한 기록 근거로만 내립니다. 통계적·어휘적 근거와 검사 실패 내역은 근거·검사 범위 목록에 전부 남습니다.</div>`);
            box.innerHTML = top.join('') + `<details class="disc"><summary>세부 — 근거 종류별·검사 범위·참고 신호·계층 진단</summary><div class="disc-body">${parts.join('')}</div></details>`;
            // A11Y-6: only the one-line result is announced, not the whole block.
            $('qc-live').textContent = '빠른 검사 결과: ' + riskLabel(verdict);
        }

        /* ── compare ───────────────────────────────── */
        const cmpFiles = { a: null, b: null };
        function bindSlot(slotId, inputId, key) {
            const slot = $(slotId), input = $(inputId);
            slot.addEventListener('click', () => { if (!scanBusy) input.click(); });
            input.addEventListener('change', e => {
                const f = e.target.files[0];
                e.target.value = '';
                if (!f) return;
                cmpFiles[key] = f;
                slot.classList.add('filled');
                slot.querySelector('.sn').textContent = displayName(f.name);  // R12-6: a picked file's name, escaped
                const old = slot.querySelector('.pv');
                if (old) old.remove();
                const pv = mediaTagFor(f, 'pv');
                if (pv) { pv.controls = false; if (pv.tagName === 'IMG') pv.alt = ''; slot.appendChild(pv); }
                setBusyButton($('cmp-btn'), !(cmpFiles.a && cmpFiles.b));
            });
        }
        bindSlot('slot-a', 'cmp-a', 'a');
        bindSlot('slot-b', 'cmp-b', 'b');

        $('cmp-btn').addEventListener('click', async () => {
            if (!(cmpFiles.a && cmpFiles.b)) { $('cmp-status').textContent = '파일 A와 B를 모두 고르십시오.'; return; }
            if ($('cmp-btn').dataset.busy) return;
            const box = $('cmp-result');
            const form = new FormData();
            form.append('file_a', cmpFiles.a, cmpFiles.a.name);
            form.append('file_b', cmpFiles.b, cmpFiles.b.name);
            const stop = startElapsed($('cmp-status'), '비교 실행 중… (화자 모델 로딩 시 수십 초)');
            $('cmp-btn').dataset.busy = '1';
            setBusyButton($('cmp-btn'), true);
            try {
                const data = await apiJson('/api/compare', { method: 'POST', body: form });
                if (data.error) {
                    box.innerHTML = `<div class="verdict c-red">오류: ${shown(data.error)}</div>`;
                    return;
                }
                renderCompare(data);
            } catch (e) {
                box.innerHTML = `<div class="verdict c-red">비교 실패: ${shown(e.message)}</div>`;
            } finally {
                stop();
                delete $('cmp-btn').dataset.busy;
                setBusyButton($('cmp-btn'), false);
            }
        });

        // GUI-REV7 F-12: server notices are written for the CLI — "결론은 `scan`을 사용하십시오" names a command and
        // keeps its Markdown backticks. In the GUI the conclusion comes from the folder scan, and backticks are dropped.
        function guiText(text) {
            // GUI-REV7 F-15: only the install commands lose their backticks — the detail panel's texts also carry
            // file names, which are shown as they are.
            return String(text == null ? '' : text)
                .replace(/결론은 `scan`을 사용하십시오/g, '결론은 폴더 검사를 사용하십시오')
                .replace(/`(pip install [^`\n]*)`/g, '$1');
        }
        // GUI-REV7 F-12: an unmeasured comparison does not lead with its raw score in large type (read as
        // "99% 같은 사람"); the head says "참고 신호 — 미측정" and the number stays in the sentence
        // (the server's reference_note, "참고 원점수 99/100, 미측정 매핑"). Only a comparison the server marks
        // measured shows the large number.
        function compareHeadHtml(data, body) {
            const kind = body.kind === 'speaker' ? '화자 유사도 (음성)' : body.kind === 'stylometry' ? '필자 유사도 (텍스트)' : (body.kind || '비교');
            const what = `<div class="note">${shown(kind)}${body.method ? ' · ' + shown(body.method) : ''}</div>`;
            // R19-10: no score is "없음", never a measured-looking 0.
            const score = data.raw_score != null ? String(Number(data.raw_score) || 0) : rawScoreText(null);
            if (data.measured === true) {
                return `<div class="qc-head">
                <span class="big">${escapeHtml(score)}</span>
                <div><span class="band-pill band-unknown">계층 진단</span>${what}</div></div>`;
            }
            // the server's reference_note names the raw score ("참고 원점수 99/100, 미측정 매핑") — said once
            const named = /참고 원점수/.test(String(data.reference_note || ''));
            const sentence = named ? '' : `<div class="note">참고 원점수 ${escapeHtml(score)}${data.raw_score != null ? '/100' : ''} — 미측정</div>`;
            return `<div class="qc-head"><div><span class="band-pill band-unknown">참고 신호 — 미측정</span>${what}${sentence}</div></div>`;
        }
        function renderCompare(data) {
            // D1: similarity is a layer diagnostic — raw number + notice,
            // no same/different band.
            const box = $('cmp-result');
            const body = data.diagnostic || data;
            const parts = [];
            parts.push(compareHeadHtml(data, body));
            if (data.notice) parts.push(`<div class="note">${shown(guiText(data.notice))}</div>`);
            if (data.reference_note) parts.push(`<div class="verdict">${shown(data.reference_note)}</div>`);
            parts.push(provenanceNoteHtml(data));
            const kv = [];
            // R30-4: an unmeasured comparison (a side without audio features or text) has no distance — "없음", never 1.
            kv.push(`<b>거리</b><span>${body.distance != null ? escapeHtml(String(body.distance)) : '없음 — 측정하지 않음'}</span>`);
            parts.push(layer('측정값', `<div class="kv">${kv.join('')}</div>`));
            parts.push(listItems('한계', body.limitations, 'lim'));
            parts.push(`<div class="caveat">유사도는 측정되지 않은 참고 수치이며 동일인 증명이 아닙니다. 추가 증거와 함께 해석하세요.</div>`);
            box.innerHTML = parts.join('');
        }

        // U-8: the folder the server was started with is the scan default. GUI-REV7 F-3: the answer of
        // /api/stats arrives after the page is shown — it fills the folder box only when the box is empty
        // and not focused (it was written into a box the examiner had started typing in, mixing the two paths).
        function applyDefaultFolder(folder, folderB64) {
            defaultFolder = folder;
            defaultFolderB64 = folderB64;
            const field = $('folder-path');
            if (!field.value && document.activeElement !== field) field.value = folder;
        }

        /* ── desktop mode (DESK-2..7: `deepfake-lens desktop`) ── */
        // The product name as the top bar shows it (one place for it).
        const PRODUCT = document.querySelector('.brand-name').textContent.trim();
        const ROOTS_SHOWN = 5;
        // RISK B5: the dialog may take seconds to appear (a cold PowerShell) and its owner window is not on the taskbar.
        const PICK_WAIT = "폴더 선택 창을 여는 중입니다(처음에는 몇 초 걸릴 수 있습니다). 창이 안 보이면 Alt+Tab으로 '폴더 찾아보기' 창을 찾으십시오.";
        const PICK_CANCELLED = '폴더 선택을 취소했습니다';
        // DESK-3: the folder last chosen in the OS dialog, with its bytes (a non-UTF-8 name survives the scan request).
        let pickedFolder = null;
        // RISK B13: there is no desktop shortcut — the program is started from the folder the zip was extracted to.
        const LAUNCHER = `압축을 푼 폴더의 '${PRODUCT}.cmd'(macOS: '${PRODUCT}.command')`;
        const CONNECTION_LOST = `서버와 연결이 끊겼습니다 — ${LAUNCHER}를 다시 실행하십시오`;
        const RECONNECTED = '서버와 다시 연결되었습니다';  // REV2-D7
        // REV2-D5: the status line under the button stays short; the lasting text is the one under the path box.
        const PICK_REFUSED = '폴더를 쓸 수 없습니다 — 아래 안내를 보십시오';
        const PICK_NOTED = '폴더를 골랐습니다 — 아래 안내를 보십시오';
        // REV2-D2: 프로그램 종료 when the server is already gone.
        const ALREADY_SHUT_DOWN = '이미 종료되었습니다 — 이 탭을 닫으십시오.';
        // REV2-D6: the tab's title once the program has stopped.
        const SHUT_DOWN_TITLE = `종료됨 — ${PRODUCT}`;
        // REV2-D3: every folder this page has seen registered — a refusal for one of them after a restart says to
        // pick the same folder again.
        const seenRoots = new Set();
        function rememberRoots(roots) { roots.forEach(root => seenRoots.add(root)); }
        const HEARTBEAT_MS = 30000;
        const HEARTBEAT_TIMEOUT_MS = 10000;
        const SHUTDOWN_QUESTION = `${PRODUCT}를 종료할까요? 종료하면 이 화면은 더 쓸 수 없습니다 — 다시 쓰려면 ${LAUNCHER}를 다시 실행하십시오.`;
        // RISK B17: a browser told to block this page's dialogs makes window.confirm return false at once, showing nothing.
        const CONFIRM_BLOCKED = '확인 창이 차단되어 있습니다 — 브라우저 설정에서 이 사이트의 대화상자 차단을 해제하거나 콘솔 창을 닫아 종료하십시오';
        const CONFIRM_BLOCKED_MS = 50;
        const FORM_CONTROLS = [HTMLButtonElement, HTMLInputElement, HTMLSelectElement, HTMLTextAreaElement];

        // DESK-2..7: one call when /api/stats has answered. The plain web server says desktop false — what comes
        // before the desktop check below is filled there too; the desktop-only controls stay hidden.
        function applyDesktopStats(d) {
            desktopMode = d.desktop === true;
            readRoots = Array.isArray(d.roots) ? d.roots.filter(root => typeof root === 'string') : [];
            rememberRoots(readRoots);
            signingInfo = d.signing && typeof d.signing === 'object' ? d.signing : null;
            renderStatusChips();
            renderRoots();
            if (!desktopMode) return;
            $('desk-bar').hidden = false;
            $('pick-row').hidden = false;
            // REV2-D1: the list of folders that can be scanned goes with the button to the top of the screen (in
            // the plain web server it stays under the path box).
            $('pick-row').appendChild($('folder-roots'));
            $('folder-label').textContent = '또는 폴더 경로 직접 입력';
            $('guide-step1').textContent = '"검사할 폴더 고르기"를 누르고 폴더를 고르면 바로 검사가 시작됩니다.';
            // DESK-4: uploads are the exception here (unsigned, never an exhibit) — folded, unless files are waiting.
            if (!selectedFiles.length) $('upload-block').open = false;
            startHeartbeat();
        }

        // DESK-5: the desktop server exits by itself after a while without a page; the page says it is alive every
        // 30 s. Two failures in a row put a lasting box in the error region (not a scan's box); a success removes it.
        let heartbeatTimer = null;
        let heartbeatFailures = 0;
        let heartbeatInFlight = false;
        function heartbeatFailuresAfter(previous, ok) {
            return ok ? 0 : previous + 1;
        }
        function connectionLost(failures) {
            return failures >= 2;
        }
        function showConnectionLost(lost) {
            const old = $('conn-lost');
            if (!lost) {
                if (old) old.remove();
                return;
            }
            if (old) return;
            const box = document.createElement('div');
            box.className = 'err-item';
            box.id = 'conn-lost';
            const text = document.createElement('p');
            text.className = 'err-text';
            const head = document.createElement('b');
            head.textContent = '연결 끊김 ';
            const body = document.createElement('span');
            body.textContent = CONNECTION_LOST;
            text.append(head, body);
            box.append(text);
            $('error-region').prepend(box);
        }
        // REV2-D2: a request that could not reach the server at all — the box goes up now, as after two misses.
        function markConnectionLost() {
            if (appShutDown) return;
            heartbeatFailures = Math.max(heartbeatFailures, 2);
            showConnectionLost(true);
        }
        // REV2-D3: what the heartbeat does with its answer — the failures counted, and whether the server has just come
        // back after failures (`back`: refresh what a restarted server may have changed) and after the box (`said`).
        function heartbeatStep(previous, ok) {
            const failures = heartbeatFailuresAfter(previous, ok);
            return { failures, back: ok && previous > 0, said: ok && connectionLost(previous) };
        }
        // REV2-D3: a server started again on the same port knows only the folders of its start-up arguments — the
        // list on the page, the chips and the signing line are read again from /api/stats. REV2-D7: said once.
        async function serverIsBack(said) {
            if (said) toast(RECONNECTED);
            if ($('pick-note').textContent === CONNECTION_LOST) setPickNote('', false);  // REV2-D2's line no longer holds
            try {
                const d = await apiJson('/api/stats');
                if (Array.isArray(d.roots)) {
                    readRoots = d.roots.filter(root => typeof root === 'string');
                    rememberRoots(readRoots);
                }
                if (typeof d.pdf_available === 'boolean') pdfAvailable = d.pdf_available;
                if (d.signing && typeof d.signing === 'object') signingInfo = d.signing;
                lastStats = d;
                dflEmit('dfl:stats', { stats: d });
                renderStatusChips();
                renderRoots();
                if (currentView === 'report') renderReport();
            } catch (e) { /* the list stays as it was; a refusal refreshes it again */ }
        }
        async function sendHeartbeat() {
            if (heartbeatInFlight || appShutDown) return;
            heartbeatInFlight = true;
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), HEARTBEAT_TIMEOUT_MS);
            let ok = false;
            try {
                const res = await apiFetch('/api/heartbeat', { method: 'POST', signal: controller.signal });
                ok = res.ok;
            } catch (e) {
                ok = false;
            } finally {
                clearTimeout(timer);
                heartbeatInFlight = false;
            }
            if (appShutDown) return;
            const step = heartbeatStep(heartbeatFailures, ok);
            heartbeatFailures = step.failures;
            showConnectionLost(connectionLost(heartbeatFailures));
            if (step.back) serverIsBack(step.said);
        }
        function startHeartbeat() {
            if (heartbeatTimer || appShutDown) return;
            heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS);
        }
        function stopHeartbeat() {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
        // RISK A4/B3: a sleeping tab (Edge sleeping tabs, efficiency mode, Chrome memory saver) or a PC waking from
        // sleep missed the 30 s timer — the page says it is alive the moment it is seen again, besides the timer.
        // Only while the timer runs (desktop mode, not shut down); sendHeartbeat keeps one request in flight.
        function heartbeatOnReturn() {
            if (heartbeatTimer && !appShutDown && document.visibilityState === 'visible') sendHeartbeat();
        }
        document.addEventListener('visibilitychange', heartbeatOnReturn);
        window.addEventListener('focus', heartbeatOnReturn);
        window.addEventListener('pageshow', heartbeatOnReturn);

        // DESK-2: 종료 asks, then stops the server; the page is replaced by one sentence (role=alert) and every
        // control is disabled — nothing on it can reach the server any more. A refusal (409: a scan is running)
        // is said in the error region with the server's text.
        // RISK B17 heuristic: a person needs far more than CONFIRM_BLOCKED_MS to read the question and answer it, so a
        // "no" that comes back sooner after a real click (isTrusted — not a script's click()) is a dialog the browser
        // did not show ("이 페이지가 추가 대화상자를 만들지 못하도록 차단"). A missed case only leaves the old silence;
        // a false alarm only adds a hint — the confirm itself is unchanged (a <dialog> instead of it is backlog).
        function confirmLooksBlocked(answer, elapsedMs, trusted) {
            return answer === false && trusted === true && elapsedMs < CONFIRM_BLOCKED_MS;
        }
        $('shutdown-btn').addEventListener('click', async e => {
            const btn = $('shutdown-btn');
            if (appShutDown || isBusyButton(btn)) return;
            const asked = performance.now();
            const answer = window.confirm(SHUTDOWN_QUESTION);
            if (!answer) {
                // Said once, and it stays (the error region, until closed).
                if (confirmLooksBlocked(answer, performance.now() - asked, e.isTrusted) && !$('confirm-blocked')) {
                    showError(CONFIRM_BLOCKED, '종료하지 못했습니다').id = 'confirm-blocked';
                }
                return;
            }
            setBusyButton(btn, true);
            dropShutdownRefusal();
            try {
                const res = await apiFetch('/api/shutdown', { method: 'POST' });
                if (res.ok) { showShutdownPage(); return; }
                const refusal = showError(await apiError(res), '종료하지 못했습니다');
                // REV2-D7: 409 (a scan is running) goes when the scan ends (setBusy(false)).
                if (res.status === 409) refusal.id = 'shutdown-refused';
            } catch (e) {
                // REV2-D2: the server cannot be reached — it has already stopped; the page says so instead of a failure.
                if (e && e.connectionLost) { showShutdownPage(true); return; }
                showError(e.message, '종료하지 못했습니다');
            } finally {
                if (!appShutDown) setBusyButton(btn, false);
            }
        });
        // REV2-D7: the 409 refusal of 프로그램 종료 no longer holds once the scan has ended.
        function dropShutdownRefusal() {
            const box = $('shutdown-refused');
            if (!box) return;
            const hadFocus = box.contains(document.activeElement);
            box.remove();
            if (hadFocus) $('main').focus();
        }
        function showShutdownPage(already) {
            appShutDown = true;
            stopHeartbeat();
            clearTimeout(a4Timer);
            if ($('token-modal').open) $('token-modal').close();
            document.querySelectorAll('*').forEach(el => { if (FORM_CONTROLS.some(type => el instanceof type)) el.disabled = true; });
            [document.querySelector('.skip-link'), document.querySelector('.topbar'), $('main'), $('toasts')].forEach(el => {
                el.hidden = true;
                el.inert = true;
            });
            const head = document.createElement('h1');
            head.className = 'view-h';
            head.tabIndex = -1;
            head.textContent = already ? ALREADY_SHUT_DOWN : '종료되었습니다. 이 탭을 닫으십시오.';
            const next = document.createElement('p');
            next.textContent = `다시 쓰려면 ${LAUNCHER}를 다시 실행하십시오.`;
            $('shutdown-page').hidden = false;
            $('shutdown-alert').replaceChildren(head, next);
            document.title = SHUT_DOWN_TITLE;  // REV2-D6
            head.focus();
        }

        // DESK-6: the two chips next to the version — PDF and signing, as /api/stats says them (not said: no chip).
        function statusChipTexts(pdf, signing) {
            const pdfText = pdf === true ? 'PDF 가능' : pdf === false ? 'PDF 불가' : '';
            const signText = signing && typeof signing.available === 'boolean' ? (signing.available ? '서명 자동' : '서명 없음') : '';
            return [pdfText, signText];
        }
        function renderStatusChips() {
            const [pdfText, signText] = statusChipTexts(pdfAvailable, signingInfo);
            [['pdf-chip', pdfText, pdfAvailable === false], ['sign-chip', signText, Boolean(signingInfo) && signingInfo.available === false]].forEach(([id, text, off]) => {
                const chip = $(id);
                chip.textContent = text;
                chip.hidden = !text;
                chip.classList.toggle('chip-off', off);
            });
        }
        // DESK-6: the checklist line on the signing key. A key file of this PC verifies only where that file is.
        function signingCheck(signing) {
            if (!signing || typeof signing.available !== 'boolean') return null;
            if (!signing.available) {
                return { tag: 'warn', text: '서명 키가 없어 내보내는 문서에 서명이 붙지 않습니다(서명 없음) — 받는 쪽이 문서가 바뀌지 않았는지 검증할 수 없습니다.' };
            }
            const id = typeof signing.key_id === 'string' && signing.key_id ? ` (키 id ${signing.key_id})` : '';
            if (signing.source === 'file' || signing.source === 'created') return { tag: 'info', text: '서명 키: 이 PC의 사용자 폴더(report-key) — 다른 PC에서는 검증되지 않음' + id };
            if (signing.source === 'env') return { tag: 'info', text: '서명 키: 환경 변수(DEEPFAKE_LENS_REPORT_KEY)' + id };
            return { tag: 'info', text: '서명 키: 있음' + id };
        }

        // DESK-3: "지금 검사할 수 있는 폴더" under the folder box — at most five by name, the rest counted.
        function rootsView(roots, desktop) {
            if (!roots.length) {
                return {
                    head: desktop ? '아직 검사할 수 있는 폴더가 없습니다 — 위의 "검사할 폴더 고르기"로 고르면 그 폴더(그 안의 폴더 포함)를 검사할 수 있습니다.'
                        : '서버를 시작할 때 등록한 폴더(--folder, --allow-root) 안만 검사할 수 있습니다.',
                    items: [], more: '',
                };
            }
            const shown = roots.slice(0, ROOTS_SHOWN);
            return {
                head: desktop ? '지금 검사할 수 있는 폴더(그 안의 폴더 포함 — 다른 폴더는 "검사할 폴더 고르기"로):'
                    : '지금 검사할 수 있는 폴더(그 안의 폴더 포함 — 서버를 시작할 때 --folder, --allow-root로 등록):',
                items: shown,
                more: roots.length > shown.length ? `외 ${roots.length - shown.length}개` : '',
            };
        }
        function renderRoots() {
            const view = rootsView(readRoots, desktopMode);
            $('roots-head').textContent = view.head;
            const list = $('roots-list');
            list.replaceChildren(...view.items.concat(view.more ? [view.more] : []).map(text => {
                const li = document.createElement('li');
                li.textContent = displayName(text);
                return li;
            }));
            list.hidden = !list.children.length;
        }
        async function refreshRoots() {
            try {
                const data = await apiJson('/api/roots');
                if (Array.isArray(data.roots)) {
                    readRoots = data.roots.map(root => root && root.path).filter(path => typeof path === 'string');
                    rememberRoots(readRoots);
                    renderRoots();
                }
            } catch (e) { /* the list stays as it was; the next pick asks again */ }
        }

        // DESK-3: the answer of /api/pick-folder — chosen (registered as a read root), cancelled, or refused with the
        // server's own text (409: a dialog is already open or the folder list is full; 501: no dialog here; 400: not a folder).
        function pickOutcome(status, body) {
            if (status === 200 && body && typeof body.folder === 'string' && body.folder) {
                // RISK B5: a caution the server attaches to the folder (OneDrive), or null.
                return { kind: 'picked', folder: body.folder, b64: typeof body.folder_b64 === 'string' ? body.folder_b64 : '',
                    note: typeof body.note === 'string' ? body.note : '' };
            }
            if (status === 200 && body && body.folder === null) return { kind: 'cancel' };
            const text = body && typeof body.error === 'string' && body.error ? body.error : `폴더 선택 창을 열지 못했습니다 (HTTP ${status})`;
            return { kind: 'error', text, status };
        }
        function setPickNote(text, isError) {
            const note = $('pick-note');
            if (note.textContent !== text) note.textContent = text;
            note.classList.toggle('pick-err', Boolean(isError));
        }
        // RISK B5: a lasting hint under the path box — the OneDrive caution for the folder chosen, or the reason a pick
        // was refused. REV2-D5: the only place of that text (the status line under the button says only where to look).
        // REV2-D8: the note belongs to the box's value when it was said (the folder chosen, or what the box held when a
        // pick was refused or a scan did not start); it is hidden while the box says something else and comes back when
        // the box says it again. It is replaced by the next pick's answer (a cancelled pick keeps it).
        let folderNote = { text: '', isError: false, folder: '' };
        function setFolderNote(text, isError, folder) {
            folderNote = { text: text || '', isError: Boolean(isError), folder: typeof folder === 'string' ? folder.trim() : '' };
            showFolderNote();
        }
        function folderNoteShown(note, value) {
            return Boolean(note.text) && String(value).trim() === note.folder;
        }
        function showFolderNote() {
            const note = $('folder-note');
            const shown = folderNoteShown(folderNote, $('folder-path').value);
            const text = shown ? folderNote.text : '';
            if (note.textContent !== text) note.textContent = text;
            note.hidden = !shown;
            note.classList.toggle('pick-err', folderNote.isError);
        }
        $('folder-path').addEventListener('input', showFolderNote);
        $('pick-folder-btn').addEventListener('click', async () => {
            const btn = $('pick-folder-btn');
            if (!desktopMode || appShutDown || scanBusy || isBusyButton(btn) || isBusyButton($('analyze-btn'))) return;
            setBusyButton(btn, true);
            setPickNote(PICK_WAIT, false);
            let outcome;
            try {
                const res = await apiFetch('/api/pick-folder', { method: 'POST' });
                let body = null;
                try { body = await res.json(); } catch (e) { body = null; }
                outcome = pickOutcome(res.status, body);
            } catch (e) {
                outcome = { kind: 'error', text: e.message, status: 0 };
            } finally {
                setBusyButton(btn, false);
            }
            if (appShutDown) return;
            // RISK B17: a cancel is said in the status line (role=status) — before, the line only went blank.
            if (outcome.kind === 'cancel') { setPickNote(PICK_CANCELLED, false); return; }
            if (outcome.kind === 'error') {
                // Said in the folder box's own hint (aria-describedby of the box and of this button), not only in passing.
                // REV2-D5: once — the status line points to it. REV2-D2: a server that cannot be reached is said by the
                // lost-connection box; the status line says the same words and the note under the box is kept.
                if (outcome.status === 0) { setPickNote(outcome.text, true); return; }
                setPickNote(PICK_REFUSED, true);
                setFolderNote(outcome.text, true, $('folder-path').value);
                if (outcome.status === 501) $('folder-path').focus();
                // RISK B5: 400 — too wide (home, drive root, …) or not a folder: pick another one.
                if (outcome.status === 400) btn.focus();
                return;
            }
            refreshRoots();
            if (scanBusy) {
                setPickNote(`검사가 진행 중이라 고른 폴더로 시작하지 않았습니다 — 끝난 뒤 다시 고르십시오. 고른 폴더: ${displayName(outcome.folder)}`, true);
                return;
            }
            setPickNote(outcome.note ? PICK_NOTED : '', false);
            pickedFolder = { folder: outcome.folder, b64: outcome.b64 };
            $('folder-path').value = outcome.folder;
            setFolderNote(outcome.note, false, outcome.folder);
            $('scan-form').requestSubmit();
        });

        /* ── OMO-5: the bridge for the gui-*.js scripts (window.DFL) and the dfl:* events ── */
        // The last /api/stats answer (DFL.stats) and whether dfl:ready has been dispatched (DFL.isReady).
        let lastStats = null;
        let dflReady = false;
        function dflEmit(name, detail) {
            document.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
        }
        function dflSetSide(side) {
            const value = side === 'plaintiff' || side === 'defendant' ? side : '';
            document.querySelectorAll('input[name="submitter-side"]').forEach(el => { el.checked = el.value === value; });
        }
        window.DFL = Object.freeze({
            isReady: () => dflReady,
            caseConfirmed: () => !caseUnconfirmed(),
            toast: (message, isError) => toast(message, isError),
            // A job the history deleted is no longer the source of any export.
            detachJob: () => { lastScanJob = ''; },
            loadJob: async (jobId, finishedAt) => {
                if (scanBusy || typeof jobId !== 'string' || !jobId) return false;
                const shown = results;
                await runScanJob(jobId, '', {}, true);
                if (results === shown || lastScanJob !== jobId) return false;  // the error toast already said why
                if (typeof finishedAt === 'number') {
                    lastScanAt = new Date(finishedAt * 1000);
                    updateCaseLine();
                }
                dflEmit('dfl:job-loaded', { jobId });
                navigate('ledger');
                return true;
            },
            newScan: () => {
                const pick = desktopMode && !appShutDown && !$('pick-row').hidden;
                navigate('scan', pick ? $('pick-folder-btn') : $('folder-path'));
            },
            navigate: view => { if (VIEWS.includes(view)) navigate(view); },
            currentJob: () => lastScanJob,
            hasResults: () => results.length > 0,
            getCase: () => {
                const out = {};
                Object.entries(CASE_FIELDS).forEach(([id, key]) => { out[key] = $(id).value; });
                out.side = submitterSide();
                return out;
            },
            setCase: fields => {
                const given = fields && typeof fields === 'object' ? fields : {};
                Object.entries(CASE_FIELDS).forEach(([id, key]) => { $(id).value = typeof given[key] === 'string' ? given[key] : ''; });
                dflSetSide(given.side);
                caseFolder = caseScope();
                saveCase();
                updateCaseLine();
                if (currentView === 'report') renderReport();
            },
            isDesktop: () => desktopMode,
            pickFolder: () => { if (!$('pick-row').hidden && !$('pick-folder-btn').disabled) $('pick-folder-btn').click(); },
            applyScanDefaults: defaults => {
                if (scanBusy || !defaults || typeof defaults !== 'object') return;
                const radio = document.querySelector(`input[name="pixel-mode"][value="${['off', 'fast', 'deep'].includes(defaults.pixel) ? defaults.pixel : ''}"]`);
                if (radio) radio.checked = true;
                if (typeof defaults.recursive === 'boolean') $('opt-recursive').checked = defaults.recursive;
            },
            stats: () => lastStats,
            apiFetch: (path, options) => apiFetch(path, options),
            isScanning: () => scanBusy,
        });
        function dflAnnounceReady() {
            dflReady = true;
            dflEmit('dfl:ready', {});
        }
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', dflAnnounceReady, { once: true });
        else setTimeout(dflAnnounceReady, 0);

        /* ── initialization ───────────────────────── */
        updateCaseLine();
        showView(viewFromHash() || 'scan', null, true);
        renderResults();
        fetchReviewsFromServer().then(() => { if (results.length) renderList(); });
        apiFetch('/api/stats').then(r => r.json()).then(d => {
            if (d.version) $('ver-chip').textContent = 'v' + d.version;
            // U-8: the folder the server was started with is the scan default.
            if (typeof d.default_folder === 'string' && d.default_folder) {
                applyDefaultFolder(d.default_folder, typeof d.default_folder_b64 === 'string' ? d.default_folder_b64 : '');
            }
            if (typeof d.pdf_available === 'boolean') pdfAvailable = d.pdf_available;
            applyDesktopStats(d);
            lastStats = d;
            dflEmit('dfl:stats', { stats: d });
            if (currentView === 'report') renderReport();
        }).catch(() => {});
        resumeJob();
