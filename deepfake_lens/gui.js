        let results = [];
        let lastProvenance = { coverage: null, thresholds: null, summary: null };
        // X2: the options of the last folder scan — /api/report re-analyzes
        // every row on the server with them (the report signs only server results).
        let lastScanOptions = {};
        let selectedFiles = [];
        let lastScanRoot = '';
        const RENDER_WINDOW = 200;
        let renderedRows = 0;
        let progressTimer = null;
        let scanBusy = false;
        let currentJobId = null;
        let bandFilter = null;
        let textFilter = '';
        // D16: default 결론순 — manipulation → undetermined → authenticity → other.
        let sortMode = 'verdict';
        let revFilter = false;
        let kbIndex = -1;

        /* Review marks stored locally and synchronized with backend API */
        const REVIEW_KEY = 'dflens-review-v1';
        let reviewStore = {};
        try { reviewStore = JSON.parse(localStorage.getItem(REVIEW_KEY) || '{}'); } catch (e) { reviewStore = {}; }
        function itemKey(item) { return item.path || item.name || ''; }
        let reviewPushTimer = null;
        function saveReviewStore(changedKey) {
            try { localStorage.setItem(REVIEW_KEY, JSON.stringify(reviewStore)); } catch (e) { /* quota — non-fatal */ }
            // Server-side store keeps examiner marks across browsers/devices
            // (chain of custody); localStorage stays as offline fallback.
            // Debounced so per-keystroke note edits send one update.
            if (!changedKey) return;
            clearTimeout(reviewPushTimer);
            reviewPushTimer = setTimeout(() => {
                pushReviewToServer(changedKey, reviewStore[changedKey] || {});
            }, 400);
        }
        async function pushReviewToServer(key, entry) {
            if (!key) return;
            try {
                // POST /api/review exists on both the builtin web server and the
                // FastAPI api_server; explicit blank fields let the store prune
                // the entry when the examiner clears every mark.
                await apiFetch('/api/review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Object.assign({artifact_id: key, star: false, note: '', verdict: 'unreviewed'}, entry)),
                });
            } catch (e) {
                // Background sync failure is non-fatal; localStorage retains
            }
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

        function toggleReview(item, card) {
            const key = itemKey(item);
            const entry = reviewStore[key] || {};
            entry.star = !entry.star;
            entry.ts = Date.now();
            if (!entry.star && !entry.note && !entry.verdict) delete reviewStore[key];
            else reviewStore[key] = entry;
            saveReviewStore(key);
            card.classList.toggle('reviewed', !!entry.star);
            card.querySelector('.rev-star').setAttribute('aria-pressed', entry.star ? 'true' : 'false');
            const sub = card.querySelector('.res-sub');
            if (entry.star && !sub.querySelector('.rev-badge')) {
                const b = document.createElement('span');
                b.className = 'rev-badge'; b.textContent = '검토됨';
                sub.appendChild(b);
            } else if (!entry.star) {
                const b = sub.querySelector('.rev-badge');
                if (b) b.remove();
            }
        }

        /* ── infra ─────────────────────────────────── */
        const $ = id => document.getElementById(id);

        function toast(msg, isErr) {
            const el = document.createElement('div');
            el.className = 'toast' + (isErr ? ' err' : '');
            el.textContent = msg;
            $('toasts').appendChild(el);
            setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 300); }, isErr ? 5000 : 2800);
        }

        /* ── token modal (async replacement for window.prompt) ── */
        let tokenPromptPending = null;
        function requestToken() {
            if (tokenPromptPending) return tokenPromptPending;
            tokenPromptPending = new Promise(resolve => {
                const modal = $('token-modal'), input = $('token-modal-input');
                const done = value => {
                    modal.hidden = true;
                    input.value = '';
                    input.removeEventListener('keydown', onKey);
                    $('token-modal-ok').onclick = null;
                    $('token-modal-cancel').onclick = null;
                    tokenPromptPending = null;
                    resolve(value);
                };
                const onKey = e => {
                    if (e.key === 'Enter') done(input.value.trim() || null);
                    if (e.key === 'Escape') done(null);
                };
                $('token-modal-ok').onclick = () => done(input.value.trim() || null);
                $('token-modal-cancel').onclick = () => done(null);
                input.addEventListener('keydown', onKey);
                modal.hidden = false;
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
                throw new Error('서버에 연결할 수 없습니다 — deepfake-lens web 프로세스가 실행 중인지 확인하세요');
            }
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

        function provenanceBannerHtml() {
            const cov = lastProvenance.coverage || {};
            const thr = lastProvenance.thresholds || {};
            const parts = [];
            const wa = cov.weights_available, wt = cov.weights_total;
            if (wt !== undefined) {
                if ((wa || 0) === 0) parts.push('<b>신경망 미탑재(측정 게이트 미충족) — 결정적 근거만 반영</b> — 탑재된 신경망 가중치가 없어 통계적 근거는 결론에 참여하지 않습니다.');
                else if (wa < wt) parts.push(`신경망 가중치 일부 탑재 (${wa}/${wt}) — 미탑재 엔진의 판단이 빠져 있습니다.`);
            }
            if (thr.provisional || thr.source === 'builtin_defaults') {
                parts.push('판정 임계값: <b>미측정 잠정값</b> — 라벨 코퍼스로 보정되기 전에는 임계값을 근거로 쓰지 마십시오.');
            }
            if (thr.in_sample) parts.push('판정 임계값: <b>in-sample(참고)</b> — 적합에 쓴 같은 표본에서 평가된 값이라 감정 근거가 아닙니다.');
            // N8: a non-recursive scan never omits subfolders silently.
            const skippedDirs = (lastProvenance.summary || {}).subfolders_skipped || 0;
            if (skippedDirs) parts.push(`하위 폴더 <b>${skippedDirs}개는 검사하지 않았습니다</b> — 포함하려면 '하위 폴더' 옵션을 켜고 다시 검사하십시오.`);
            // X1: files beyond the file-count cap are never silently omitted.
            const overCap = (lastProvenance.summary || {}).files_over_cap || 0;
            if (overCap) parts.push(`파일 수 상한에 도달해 <b>${overCap}개 파일은 검사·기록하지 않았습니다</b> — '최대 파일 수'를 늘려 다시 검사하십시오.`);
            if (!parts.length) return '';
            return `<div class="prov-banner" role="status">${parts.map(p => `<p>${p}</p>`).join('')}<p class="note">이 결과는 결론과 근거로 읽으십시오; 점수는 보정된 경우에만 표시됩니다. 유죄·불법성의 확정 판정이 아닙니다.</p></div>`;
        }

        function escapeHtml(value) {
            return String(value).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
        }

        /* ── onboarding guide ────────────────────── */
        const guideCard = $('guide-card');
        function setGuide(open) {
            guideCard.style.display = open ? '' : 'none';
            localStorage.setItem('dfl.guide', open ? 'open' : 'closed');
        }
        $('guide-close').addEventListener('click', () => setGuide(false));
        $('guide-btn').addEventListener('click', () => setGuide(guideCard.style.display === 'none'));
        if (localStorage.getItem('dfl.guide') === 'closed') guideCard.style.display = 'none';
        guideCard.querySelectorAll('.tile').forEach(tile => {
            const go = () => {
                const target = $(tile.dataset.target);
                if (!target) return;
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                target.classList.remove('flash');
                void target.offsetWidth;
                target.classList.add('flash');
            };
            tile.addEventListener('click', go);
            tile.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
        });

        /* Result contract v2: three verdicts, evidence by kind, coverage.
           Statistical (model) and lexical (keyword) evidence never decide;
           a failed check leaves the file undetermined. */
        const VERDICT_LABELS = {
            manipulation_evidence: '조작·생성 근거 있음',
            authenticity_evidence: '원본성 근거 있음',
            undetermined: '판단 불가',
        };
        const KIND_LABELS = { deterministic: '결정적 근거', statistical: '통계적 근거', lexical: '어휘적 근거' };
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
            archive_member: '압축 구성 파일',
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
            if (verdict === 'manipulation_evidence') return '결정적 근거(메타데이터·C2PA 등)가 조작·생성을 가리킵니다 — 근거 항목과 원본 파일을 대조해 감정서에 인용하세요.';
            if (verdict === 'authenticity_evidence') return '결정적 근거가 원본성을 가리킵니다 — 서명자·해시 검증 내역을 감정서에 함께 기재하세요.';
            return '결론을 낼 결정적 근거가 없거나 검사가 실패했습니다 — "원본"이라는 뜻이 아닙니다. 아래 검사 범위의 미실행·실패 사유를 확인하세요.';
        }
        function evidenceHtml(r) {
            const ev = r.evidence || [];
            if (!ev.length) return '<div class="dgroup ev-group"><div class="dt">근거</div><div class="note">근거 항목 없음</div></div>';
            return ['deterministic', 'statistical', 'lexical'].map(kind => {
                const rows = ev.filter(e => e.kind === kind);
                if (!rows.length) return '';
                const lis = rows.map(e => {
                    const prob = (e.probability != null && e.calibration_id)
                        ? ` · p=${Number(e.probability).toFixed(2)}${e.probability_ci ? ` (95% CI ${Number(e.probability_ci[0]).toFixed(2)}–${Number(e.probability_ci[1]).toFixed(2)})` : ''} · 보정 ${escapeHtml(e.calibration_id)}`
                        : '';
                    return `<li class="ev-item ev-${escapeHtml(e.direction)}"><b>${escapeHtml(e.title)}</b> <span class="ev-badge">${escapeHtml(DIRECTION_LABELS[e.direction] || e.direction)}·${escapeHtml(STRENGTH_LABELS[e.strength] || e.strength)}${prob}</span><div class="note">${escapeHtml(e.detail || '')}</div></li>`;
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
            const li = (c, cls) => `<li class="${cls}">${escapeHtml(coverageText(c))}</li>`;
            return `<div class="dgroup cov-group"><div class="dt">검사 범위 — 실행 ${ran.length} · 미실행 ${skipped.length} · 실패 ${failed.length}</div><ul>` +
                failed.map(c => li(c, 'cov-failed')).join('') +
                skipped.map(c => li(c, 'cov-skipped')).join('') +
                (ran.length ? `<li class="cov-ran">실행: ${ran.map(c => escapeHtml(checkLabel(c.check))).join(', ')}</li>` : '') +
                '</ul></div>';
        }
        function verdictHeadHtml(r) {
            const v = r.verdict_code || 'undetermined';
            const grade = r.grade === 'reference' ? '참고' : '감정 근거로 사용 가능';
            const parts = [`<div class="verdict-head v-${escapeHtml(v)}" data-verdict="${escapeHtml(v)}"><span class="band-pill band-${bandCls(v)}">${escapeHtml(VERDICT_LABELS[v] || v)}</span> <span class="grade-pill">${escapeHtml(grade)}</span></div>`];
            if (r.grade === 'reference') parts.push(`<div class="legal-note">${escapeHtml(TEXT_LEGAL_LIMITATION)}</div>`);
            if (r.verdict) parts.push(`<div class="verdict">${escapeHtml(r.verdict)}</div>`);
            return parts.join('');
        }

        function startElapsed(el, text) {
            if (progressTimer) clearInterval(progressTimer);
            const t0 = Date.now();
            el.textContent = text;
            progressTimer = setInterval(() => {
                el.textContent = `${text} (${Math.floor((Date.now() - t0) / 1000)}초)`;
            }, 1000);
        }
        function stopElapsed(el) {
            if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
            if (el) el.textContent = '';
        }

        /* ── scan controls ─────────────────────────── */
        let pixelMode = 'off';
        $('pixel-seg').querySelectorAll('button').forEach(b => {
            b.addEventListener('click', () => {
                if (scanBusy) return;
                pixelMode = b.dataset.v;
                $('pixel-seg').querySelectorAll('button').forEach(x => {
                    x.classList.toggle('on', x === b);
                    x.setAttribute('aria-checked', x === b ? 'true' : 'false');
                });
            });
        });

        const SCAN_INPUTS = ['folder-path', 'analyze-btn', 'max-files',
            'opt-recursive', 'opt-dedupe', 'opt-heatmaps', 'opt-model',
            'opt-deep', 'dir-picker-btn', 'upload-btn', 'upload-clear'];

        function setBusy(busy, text) {
            scanBusy = busy;
            $('progress').classList.toggle('on', busy);
            $('progress').setAttribute('aria-busy', busy ? 'true' : 'false');
            SCAN_INPUTS.forEach(id => { const el = $(id); if (el) el.disabled = busy; });
            if (busy) startElapsed($('progress-text'), text);
            else stopElapsed($('progress-text'));
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
        function openFilePicker() {
            if (scanBusy) return;
            const input = document.createElement('input');
            input.type = 'file';
            input.multiple = true;
            input.accept = '.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.gif,.txt,.md,.pdf,.docx,.xlsx,.pptx,.hwp,.wav,.mp3,.flac,.ogg,.m4a,.aac,.opus,.mp4,.mov,.m4v,.mkv,.webm,.avi,.zip,.7z,.rar,.tar,.tgz,.gz,.bz2,.xz';
            input.onchange = e => addFiles(Array.from(e.target.files));
            input.click();
        }
        dropZone.addEventListener('click', openFilePicker);
        dropZone.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFilePicker(); }
        });
        $('dir-picker-btn').addEventListener('click', () => { if (!scanBusy) $('dir-picker').click(); });
        $('dir-picker').addEventListener('change', e => { addFiles(Array.from(e.target.files)); e.target.value = ''; });
        $('upload-clear').addEventListener('click', () => { selectedFiles = []; renderFileChips(); });

        function renderFileChips() {
            const list = $('file-list');
            list.innerHTML = '';
            selectedFiles.forEach((file, i) => {
                const name = file.webkitRelativePath || file.name;
                const chip = document.createElement('span');
                chip.className = 'chipfile';
                chip.innerHTML = `<span class="nm">${escapeHtml(name)}</span><span>${(file.size/1024).toFixed(0)}K</span>`;
                const x = document.createElement('button');
                x.className = 'x'; x.textContent = '✕'; x.setAttribute('aria-label', '제거');
                x.addEventListener('click', ev => { ev.stopPropagation(); selectedFiles.splice(i, 1); renderFileChips(); });
                chip.appendChild(x);
                list.appendChild(chip);
            });
            $('upload-row').hidden = selectedFiles.length === 0;
            $('upload-n').textContent = selectedFiles.length;
        }

        function addFiles(files) {
            files.forEach(file => {
                const name = file.webkitRelativePath || file.name;
                if (!selectedFiles.find(f => (f.webkitRelativePath || f.name) === name && f.size === file.size)) {
                    selectedFiles.push(file);
                }
            });
            renderFileChips();
        }

        /* ── folder scan (async job + poll) ────────── */
        $('analyze-btn').addEventListener('click', async () => {
            const folderPath = $('folder-path').value;
            if (!folderPath) { toast('폴더 경로를 입력하세요', true); return; }
            const params = new URLSearchParams({
                folder: folderPath,
                pixel: pixelMode,
                max_files: $('max-files').value,
                recursive: $('opt-recursive').checked,
                dedupe: $('opt-dedupe').checked,
                heatmaps: $('opt-heatmaps').checked,
                deep_signals: $('opt-deep').checked,
                async: '1',
            });
            if (!$('opt-model').checked) params.set('no_default_engine', 'true');
            lastScanOptions = {
                pixel: pixelMode,
                heatmaps: String($('opt-heatmaps').checked),
                deep_signals: String($('opt-deep').checked),
                no_default_engine: String(!$('opt-model').checked),
            };
            setBusy(true, '폴더 분석 중… 모델 로딩 시 수 분 걸릴 수 있습니다');
            try {
                const job = await apiJson('/api/scan?' + params.toString());
                if (job.error) { toast('오류: ' + job.error, true); return; }
                currentJobId = job.job_id;
                $('scan-cancel').hidden = false;
                let data = null;
                // Polling is bounded — a dead backend must not spin forever.
                const deadline = Date.now() + 30 * 60 * 1000;
                while (true) {
                    if (Date.now() > deadline) {
                        toast('분석 시간 초과 (30분) — 파일 수를 줄이거나 서버 로그를 확인하세요', true);
                        return;
                    }
                    const state = await apiJson('/api/scan-status?job=' + encodeURIComponent(job.job_id));
                    if (state.error) { toast('오류: ' + state.error, true); return; }
                    if (state.status !== 'running') { data = state.result; break; }
                    await new Promise(res => setTimeout(res, 1500));
                }
                if (!data || data.error) { toast('오류: ' + (data && data.error ? data.error : '스캔 결과 없음'), true); return; }
                lastScanRoot = folderPath;
                processResults(data);
                toast(`분석 완료 — ${(data.items || []).length}개 파일`);
            } catch (error) {
                toast('분석 중 오류: ' + error.message, true);
            } finally {
                currentJobId = null;
                $('scan-cancel').hidden = true;
                setBusy(false);
            }
        });

        $('scan-cancel').addEventListener('click', async () => {
            if (!currentJobId) return;
            $('scan-cancel').disabled = true;
            try {
                const res = await apiFetch('/api/scan-cancel?job=' + encodeURIComponent(currentJobId));
                const data = await res.json();
                if (data.error) toast('취소 실패: ' + data.error, true);
                else toast('취소 요청됨 — 분석된 항목까지만 표시됩니다');
            } catch (e) {
                toast('취소 실패: ' + e.message, true);
            } finally {
                $('scan-cancel').disabled = false;
            }
        });

        /* ── upload scan (batched) ─────────────────── */
        $('upload-btn').addEventListener('click', async () => {
            if (!selectedFiles.length) return;
            const BATCH = 20;
            setBusy(true, '업로드 파일 분석 중…');
            try {
                const allItems = [];
                const total = selectedFiles.length;
                for (let i = 0; i < total; i += BATCH) {
                    const batch = selectedFiles.slice(i, i + BATCH);
                    const form = new FormData();
                    batch.forEach(f => form.append('files', f, f.webkitRelativePath || f.name));
                    const data = await apiJson('/api/analyze-upload', { method: 'POST', body: form });
                    if (data.error) { toast('오류: ' + data.error, true); return; }
                    allItems.push(...(data.items || []));
                    if (data.coverage) lastProvenance.coverage = data.coverage;
                    if (data.thresholds) lastProvenance.thresholds = data.thresholds;
                    if (total > BATCH) startElapsed($('progress-text'), `업로드 분석 중… (${Math.min(i + BATCH, total)}/${total})`);
                }
                lastScanRoot = '';  // uploads are temp files; heatmaps unavailable
                processResults({ items: allItems });
                toast(`분석 완료 — ${allItems.length}개 파일`);
                selectedFiles = [];
                renderFileChips();
            } catch (error) {
                toast('분석 중 오류: ' + error.message, true);
            } finally {
                setBusy(false);
            }
        });

        /* ── results rendering ─────────────────────── */
        function processResults(data) {
            results = (data.items || []).map(item => ({ item }));
            if (data.coverage) lastProvenance.coverage = data.coverage;
            if (data.thresholds) lastProvenance.thresholds = data.thresholds;
            lastProvenance.summary = data.summary || null;
            const label = $('stat-model-label');
            if (label) {
                const cov = lastProvenance.coverage || {};
                const wa = cov.weights_available, wt = cov.weights_total;
                label.textContent = wt !== undefined
                    ? (wa ? `뉴럴 ${wa}/${wt}` : '신경망 미탑재(측정 게이트 미충족) — 결정적 근거만 반영')
                    : `뉴럴 ${data.summary && data.summary.external_model_active ? data.summary.external_model_active : 0}`;
            }
            const banner = $('prov-banner');
            if (banner) {
                banner.innerHTML = provenanceBannerHtml();
                banner.hidden = !banner.innerHTML;
            }
            renderResults(data.summary || {});
        }

        function reportPayload(extra) {
            const out = {
                items: results.map(r => r.item),
                summary: lastProvenance.summary || {},
                coverage: lastProvenance.coverage || {},
                thresholds: lastProvenance.thresholds || {},
                options: lastScanOptions,
            };
            const map = { 'case-no': 'case_no', 'case-name': 'case_name',
                          'case-plaintiff': 'plaintiff', 'case-defendant': 'defendant',
                          'case-court': 'court', 'case-firm': 'law_firm',
                          'case-center': 'center', 'case-contact': 'contact' };
            for (const [id, key] of Object.entries(map)) {
                const el = $(id);
                if (el && el.value.trim()) out[key] = el.value.trim();
            }
            return Object.assign(out, extra || {});
        }

        function riskLabel(verdict) {
            return VERDICT_LABELS[verdict] || '미지원·실패';
        }
        // Verdict -> existing colour classes: red = manipulation evidence,
        // orange = undetermined (needs the examiner), green = authenticity.
        const VERDICT_CLS = { manipulation_evidence: 'high', undetermined: 'medium', authenticity_evidence: 'low' };
        function bandColor(verdict) {
            const cls = VERDICT_CLS[verdict];
            return cls === 'high' ? 'var(--red)' : cls === 'medium' ? 'var(--orange)' : cls === 'low' ? 'var(--green)' : 'var(--muted)';
        }
        // Class-name variant — bandColor() is only used in markup templates
        // where a CSP-safe class is required instead of an inline style.
        function bandCls(verdictOrBand) {
            if (VERDICT_CLS[verdictOrBand]) return VERDICT_CLS[verdictOrBand];
            return verdictOrBand === 'high' || verdictOrBand === 'medium' || verdictOrBand === 'low' ? verdictOrBand : 'other';
        }

        function listItems(title, values, cls) {
            if (!values || !values.length) return '';
            const rows = values.map(v => {
                const text = typeof v === 'string' ? v : (v.title ? `${v.title}: ${v.detail || ''}` : JSON.stringify(v));
                return `<li>${escapeHtml(text)}</li>`;
            }).join('');
            return `<div class="dgroup ${cls || ''}"><div class="dt">${title}</div><ul>${rows}</ul></div>`;
        }

        function detailHtml(item) {
            const r = item.result || {};
            const parts = [];
            if (item.error) parts.push(`<div class="verdict">분석 실패: ${escapeHtml(item.error)}</div>`);
            // Order: verdict, then evidence grouped by kind, then coverage.
            if (item.result) {
                parts.push(verdictHeadHtml(r));
                parts.push(evidenceHtml(r));
                parts.push(coverageHtml(r));
            }
            const hasPreview = lastScanRoot && item.path && !item.path.includes('::') &&
                !/^[a-zA-Z]:[\\/]|^\//.test(item.path) &&
                (item.kind === 'image' || item.kind === 'video' || item.kind === 'audio');
            const abs = hasPreview ? (lastScanRoot.replace(/[\\/]+$/, '') + '/' + item.path) : '';
            const hasHeatmap = Boolean(item.heatmap_path && lastScanRoot);

            if (item.kind === 'image' && hasPreview && hasHeatmap) {
                parts.push(`<div class="forensic-studio-slot" data-pv="${escapeHtml(abs)}" data-hm="${escapeHtml(item.heatmap_path)}"><span class="note">포렌식 비교 스튜디오 준비 중…</span></div>`);
            } else {
                if (hasPreview) {
                    parts.push(`<div class="dgroup"><div class="dt">미리보기</div><div class="pv-slot" data-pv="${escapeHtml(abs)}" data-kind="${escapeHtml(item.kind)}"><span class="note">로딩 중…</span></div></div>`);
                }
                if (hasHeatmap) {
                    parts.push(`<div class="dgroup"><div class="dt">픽셀 히트맵</div><div class="hm-slot" data-hm="${escapeHtml(item.heatmap_path)}"><span class="note">히트맵 로딩 중…</span></div></div>`);
                }
            }
            const ma = r.model_analysis;
            if (ma) {
                const members = (ma.models || []).map(m =>
                    `<li>${escapeHtml(m.display_name || m.model || '모델')} — 원점수 ${m.score != null ? m.score : '없음'}${m.available === false ? ' (사용 불가)' : ''}</li>`).join('');
                parts.push(`<div class="dgroup"><div class="dt">외부 모델 원점수(미보정, 결론 불참여) — ${ma.score != null ? ma.score : '없음'}</div>${members ? `<ul>${members}</ul>` : `<div class="note">${escapeHtml(ma.detail || '')}</div>`}</div>`);
            }
            // Unmeasured heuristics (pixel ensemble, fusion, legacy audio/
            // video heuristics) — displayed for reference, never decide.
            parts.push(listItems('참고 신호(미측정 휴리스틱 — 결론 불참여)', (r.reference_signals || []).map(s => ({ title: `${s.title} (${s.weight})`, detail: s.detail }))));
            parts.push(listItems('한계', r.limitations, 'lim'));
            parts.push(listItems('다음 확인', r.next_checks));
            const sg = r.source_guess;
            if (sg && sg.label) {
                const reasons = (sg.reasons || []).map(escapeHtml).join(' ');
                parts.push(`<div class="dgroup"><div class="dt">출처 추정 (${escapeHtml(sourceConfidenceLabel(r, sg))})</div><div class="note">${escapeHtml(sg.label)} — ${reasons}</div></div>`);
            }
            parts.push(`<div class="band-advice">${escapeHtml(verdictAdvice(r.verdict_code || 'undetermined', r.grade))}</div>`);
            parts.push(`<div class="caveat">결론은 결정적 근거로만 내립니다. 통계적·어휘적 근거와 검사 실패 내역은 근거·검사 범위 목록에 전부 남습니다.</div>`);
            return parts.join('');
        }

        async function loadPreview(slot) {
            const abs = slot.dataset.pv, kind = slot.dataset.kind;
            try {
                const res = await apiFetch('/api/preview?path=' + encodeURIComponent(abs) + '&root=' + encodeURIComponent(lastScanRoot));
                if (!res.ok) { slot.innerHTML = `<span class="note">미리보기 불가 (${res.status})</span>`; return; }
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
                slot.innerHTML = `<span class="note">미리보기 실패: ${escapeHtml(e.message)}</span>`;
            }
        }

        async function loadHeatmap(slot) {
            const hmPath = slot.dataset.hm;
            try {
                const res = await apiFetch('/api/heatmap?path=' + encodeURIComponent(hmPath) + '&root=' + encodeURIComponent(lastScanRoot));
                if (!res.ok) { slot.innerHTML = `<span class="note">히트맵을 불러올 수 없습니다 (${res.status})</span>`; return; }
                const blob = await res.blob();
                const img = document.createElement('img');
                img.className = 'heatmap-img';
                img.alt = '픽셀 분석 히트맵';
                img.src = URL.createObjectURL(blob);
                slot.innerHTML = '';
                slot.appendChild(img);
            } catch (e) {
                slot.innerHTML = `<span class="note">히트맵 로딩 실패: ${escapeHtml(e.message)}</span>`;
            }
        }

        async function loadForensicStudio(slot) {
            const pvPath = slot.dataset.pv;
            const hmPath = slot.dataset.hm;
            slot.innerHTML = `<span class="note">포렌식 레이어 로딩 중 (원본 & 히트맵)…</span>`;

            try {
                const [resPv, resHm] = await Promise.all([
                    apiFetch('/api/preview?path=' + encodeURIComponent(pvPath) + '&root=' + encodeURIComponent(lastScanRoot)),
                    apiFetch('/api/heatmap?path=' + encodeURIComponent(hmPath) + '&root=' + encodeURIComponent(lastScanRoot)),
                ]);

                if (!resPv.ok || !resHm.ok) {
                    slot.innerHTML = `<span class="note">포렌식 비교 이미지 로드 실패 (원본: ${resPv.status}, 히트맵: ${resHm.status})</span>`;
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
                            <span>🔬</span>
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
                slot.innerHTML = `<span class="note">스튜디오 로딩 실패: ${escapeHtml(e.message)}</span>`;
            }
        }

        function renderCard(list, entry) {
            const item = entry.item;
            const r = item.result || {};
            const verdict = verdictOf(item);
            const band = bandCls(verdict);
            // The ring shows a number only for a calibrated probability;
            // otherwise a verdict glyph (no uncalibrated number on display).
            const calibrated = Boolean(r.score_is_calibrated);
            const ringText = calibrated ? String(Number(r.score) || 0) : ({ high: '!', low: '✓', medium: '?' }[band] || '–');
            const ringFill = calibrated ? (Number(r.score) || 0) : (band === 'other' ? 0 : 100);
            const evCounts = ['deterministic', 'statistical', 'lexical'].map(k => (r.evidence || []).filter(e => e.kind === k).length);
            const failedChecks = (r.coverage || []).filter(c => c.status === 'failed').length;
            const tool = (r.source_guess && r.source_guess.label) || '알 수 없음';
            const rev = reviewStore[itemKey(item)] || {};

            const card = document.createElement('div');
            card.className = 'res' + (rev.star ? ' reviewed' : '');
            card.style.setProperty('--res-accent', bandColor(verdict));
            card.dataset.verdict = verdict;
            card.innerHTML = `
                <div class="res-main">
                    <div class="ring">
                        <svg viewBox="0 0 36 36"><circle class="bg" cx="18" cy="18" r="15.9"></circle>
                        <circle class="val" cx="18" cy="18" r="15.9" pathLength="100"
                            stroke-dasharray="${Math.max(0, Math.min(100, ringFill))} 100"
                            class="bs-${band}"></circle></svg>
                        <span class="num bc-${band}">${escapeHtml(ringText)}</span>
                    </div>
                    <div class="res-info">
                        <div class="res-name">${escapeHtml(item.name || item.path || '파일')}</div>
                        <div class="res-sub">
                            <span class="band-pill band-${band}">${escapeHtml(riskLabel(verdict))}</span>
                            ${r.grade === 'reference' ? '<span class="grade-pill">참고</span>' : ''}
                            <span class="ev-counts" title="결정적·통계적·어휘적 근거 수">결정 ${evCounts[0]} · 통계 ${evCounts[1]} · 어휘 ${evCounts[2]}</span>
                            ${failedChecks ? `<span class="c-red">검사 실패 ${failedChecks}</span>` : ''}
                            <span>${escapeHtml(tool)}</span>
                            ${r.model_analysis && r.model_analysis.available !== false ? '<span class="nn-badge">NN</span>' : ''}
                            ${rev.star ? '<span class="rev-badge">검토됨</span>' : ''}
                            ${item.error ? '<span class="c-red">분석 실패</span>' : ''}
                        </div>
                    </div>
                    <button class="rev-star" title="검토 표시 토글" aria-pressed="${rev.star ? 'true' : 'false'}">★</button>
                    <span class="chev">›</span>
                </div>
                <div class="res-detail"></div>`;

            card.querySelector('.rev-star').addEventListener('click', e => {
                e.stopPropagation();
                toggleReview(item, card);
            });

            const detail = card.querySelector('.res-detail');
            let built = false;
            const toggle = () => {
                card.classList.toggle('open');
                if (card.classList.contains('open') && !built) {
                    built = true;
                    detail.innerHTML = detailHtml(item);
                    const fb = document.createElement('div');
                    fb.className = 'fb-row';
                    fb.innerHTML = `<span class="note">검토자 라벨:</span>
                        <button class="btn btn-sm" data-label="synthetic">실제 합성/AI</button>
                        <button class="btn btn-sm" data-label="real">실제 실물</button>
                        <span class="fb-status"></span>`;
                    fb.querySelectorAll('button[data-label]').forEach(b => b.addEventListener('click', e => {
                        e.stopPropagation();
                        submitFeedback(item, b.dataset.label, fb.querySelector('.fb-status'));
                    }));
                    detail.appendChild(fb);
                    const revBox = document.createElement('div');
                    revBox.className = 'rev-box';
                    const note = document.createElement('textarea');
                    note.className = 'rev-note';
                    note.placeholder = '검토 메모 — 분석관 의견 및 특이사항 입력 (서버 자동 동기화)';
                    note.value = rev.note || '';
                    note.addEventListener('click', e => e.stopPropagation());

                    const revBar = document.createElement('div');
                    revBar.className = 'rev-toolbar';
                    revBar.innerHTML = `
                        <div class="rev-row">
                            <label class="rev-lbl">검토 판정:</label>
                            <select class="rev-verdict-sel">
                                <option value="unreviewed">미검토</option>
                                <option value="synthetic">인공합성 의심</option>
                                <option value="authentic">원본 정상</option>
                                <option value="inconclusive">판단 보류</option>
                            </select>
                        </div>
                        <span class="rev-sync-status"></span>
                    `;
                    const verdictSel = revBar.querySelector('.rev-verdict-sel');
                    verdictSel.value = rev.verdict || 'unreviewed';
                    verdictSel.addEventListener('click', e => e.stopPropagation());

                    const syncStatus = revBar.querySelector('.rev-sync-status');
                    let syncTimeout = null;
                    function saveAndSync() {
                        const k = itemKey(item);
                        const cur = reviewStore[k] || {};
                        cur.note = note.value;
                        cur.verdict = verdictSel.value;
                        cur.ts = Date.now();
                        reviewStore[k] = cur;
                        saveReviewStore(k);
                        syncStatus.className = 'rev-sync-status saving';
                        syncStatus.textContent = '● 동기화 중…';
                        clearTimeout(syncTimeout);
                        syncTimeout = setTimeout(async () => {
                            await pushReviewToServer(k, cur);
                            syncStatus.className = 'rev-sync-status saved';
                            syncStatus.textContent = '● 서버 동기화됨';
                        }, 500);
                    }

                    note.addEventListener('input', saveAndSync);
                    verdictSel.addEventListener('change', saveAndSync);

                    revBox.appendChild(note);
                    revBox.appendChild(revBar);
                    detail.appendChild(revBox);

                    detail.querySelectorAll('.forensic-studio-slot').forEach(loadForensicStudio);
                    detail.querySelectorAll('.hm-slot').forEach(loadHeatmap);
                    detail.querySelectorAll('.pv-slot').forEach(loadPreview);
                }
            };
            card.querySelector('.res-main').addEventListener('click', toggle);
            card.querySelector('.res-main').setAttribute('tabindex', '0');
            card.querySelector('.res-main').addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
            });
            list.appendChild(card);
        }

        function itemBand(entry) {
            return verdictOf(entry.item);
        }

        const BAND_ORDER = { manipulation_evidence: 0, undetermined: 1, authenticity_evidence: 2, other: 3 };
        function filteredResults() {
            const needle = textFilter.trim().toLowerCase();
            const filtered = results.filter(entry => {
                if (bandFilter && itemBand(entry) !== bandFilter) return false;
                if (revFilter && !(reviewStore[itemKey(entry.item)] || {}).star) return false;
                if (!needle) return true;
                const item = entry.item;
                return ((item.name || '') + ' ' + (item.path || '')).toLowerCase().includes(needle);
            });
            const byName = (a, b) => String(a.item.name || a.item.path || '').localeCompare(String(b.item.name || b.item.path || ''), 'ko');
            if (sortMode === 'name') {
                filtered.sort(byName);
            } else {
                // 결론순 (D16): verdict order, then name. The uncalibrated
                // score is always 0 in phase 0 and never orders results.
                filtered.sort((a, b) => (BAND_ORDER[itemBand(a)] - BAND_ORDER[itemBand(b)]) || byName(a, b));
            }
            return filtered;
        }

        function renderResults(summary, scroll = true) {
            const list = $('res-list');
            list.innerHTML = '';

            // Canonical band counts — every pill plus the total must agree.
            const bands = { manipulation_evidence: 0, undetermined: 0, authenticity_evidence: 0, other: 0 };
            let modelCount = 0;
            results.forEach(entry => {
                bands[itemBand(entry)]++;
                if ((entry.item.result || {}).model_analysis) modelCount++;
            });
            const modelActive = summary.external_model_active != null ? summary.external_model_active : modelCount;
            $('stat-manip').textContent = bands.manipulation_evidence;
            $('stat-undet').textContent = bands.undetermined;
            $('stat-auth').textContent = bands.authenticity_evidence;
            $('stat-other').textContent = bands.other;
            $('stat-total').textContent = results.length;
            // N5: archive container rows are counted by verdict; say how many.
            const containers = summary.container_rows != null
                ? summary.container_rows
                : results.filter(entry => entry.item.kind === 'archive' && entry.item.result).length;
            $('stat-model-label').textContent = (containers ? `압축 파일 ${containers}건 포함 · ` : '') + `뉴럴 ${modelActive}`;
            const total = Math.max(1, results.length);
            $('distbar').innerHTML =
                `<div class="bg-red"></div>` +
                `<div class="bg-orange"></div>` +
                `<div class="bg-green"></div>` +
                `<div class="bg-line"></div>`;
            // el.style.width is a JS property assignment — not an inline
            // style attribute, so it is allowed under style-src 'self'.
            const segs = $('distbar').children;
            [bands.manipulation_evidence, bands.undetermined, bands.authenticity_evidence, bands.other].forEach((n, i) => {
                if (segs[i]) segs[i].style.width = (n / total * 100) + '%';
            });

            document.querySelectorAll('#stat-pills .pill-stat').forEach(p => {
                const active = (p.dataset.band || null) === bandFilter;
                p.classList.toggle('active', active);
                p.setAttribute('aria-pressed', active ? 'true' : 'false');
            });

            kbIndex = -1;
            const filtered = filteredResults();
            if (!filtered.length) {
                list.innerHTML = results.length
                    ? '<div class="empty"><div class="e-icon">🔍</div>필터와 일치하는 결과가 없습니다</div>'
                    : '<div class="empty"><div class="e-icon">📭</div>분석 결과가 없습니다</div>';
            }
            renderedRows = Math.min(RENDER_WINDOW, filtered.length);
            filtered.slice(0, renderedRows).forEach(e => renderCard(list, e));
            updateMore(list, filtered.length);

            $('results-section').hidden = false;
            if (scroll) $('results-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function updateMore(list, filteredLen) {
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
            filtered.slice(prev, renderedRows).forEach(e => renderCard(list, e));
            updateMore(list, filtered.length);
        });

        /* Band pills and the text box are the single filter surface —
           clicking the active pill again clears the filter. */
        document.querySelectorAll('#stat-pills .pill-stat').forEach(p => {
            const apply = () => {
                bandFilter = (p.dataset.band || null) === bandFilter ? null : (p.dataset.band || null);
                renderResults({}, false);
            };
            p.addEventListener('click', apply);
            p.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); apply(); } });
        });
        $('res-filter').addEventListener('input', e => { textFilter = e.target.value; renderResults({}, false); });
        $('res-sort').addEventListener('change', e => { sortMode = e.target.value; renderResults({}, false); });
        $('rev-filter').addEventListener('click', () => {
            revFilter = !revFilter;
            $('rev-filter').setAttribute('aria-pressed', revFilter ? 'true' : 'false');
            $('rev-filter').classList.toggle('btn-primary', revFilter);
            renderResults({}, false);
        });

        /* Keyboard review: arrows/j/k walk the rendered cards, Enter opens,
           Esc closes open cards or clears filters. Skipped while typing. */
        document.addEventListener('keydown', e => {
            const tag = (e.target.tagName || '').toLowerCase();
            const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;
            if ($('results-section').hidden) return;
            if (e.key === 'Escape') {
                const open = document.querySelector('.res.open');
                if (open) { open.classList.remove('open'); return; }
                if (bandFilter || revFilter || textFilter) {
                    bandFilter = null; revFilter = false; textFilter = '';
                    $('res-filter').value = '';
                    $('rev-filter').setAttribute('aria-pressed', 'false');
                    $('rev-filter').classList.remove('btn-primary');
                    renderResults({}, false);
                }
                return;
            }
            if (typing) return;
            const cards = [...document.querySelectorAll('#res-list .res')];
            if (!cards.length) return;
            const move = { ArrowDown: 1, j: 1, J: 1, ArrowUp: -1, k: -1, K: -1 }[e.key];
            if (move == null) {
                if (e.key === 'Enter' && kbIndex >= 0 && cards[kbIndex]) {
                    e.preventDefault();
                    cards[kbIndex].querySelector('.res-main').click();
                }
                return;
            }
            e.preventDefault();
            kbIndex = Math.max(0, Math.min(cards.length - 1, kbIndex + move));
            cards.forEach((c, i) => c.classList.toggle('kb-focus', i === kbIndex));
            cards[kbIndex].scrollIntoView({ block: 'nearest' });
        });

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
                statusEl.textContent = data.ok ? '기록됨 ✓' : ('실패: ' + (data.error || ''));
            } catch (e) {
                statusEl.textContent = '실패: ' + e.message;
            }
        }

        /* ── export ────────────────────────────────── */
        function download(blob, name) {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = name; a.click();
            URL.revokeObjectURL(url);
        }
        $('exp-json').addEventListener('click', () => {
            if (!results.length) { toast('분석 결과가 없습니다', true); return; }
            const enriched = results.map(e => {
                const rev = reviewStore[itemKey(e.item)];
                return rev ? { ...e.item, review: rev } : e.item;
            });
            download(new Blob([JSON.stringify(enriched, null, 2)], { type: 'application/json' }), 'deepfake-lens-results.json');
        });
        $('exp-csv').addEventListener('click', () => {
            if (!results.length) { toast('분석 결과가 없습니다', true); return; }
            const f = v => /[",\r\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
            // BOM prefix keeps Korean intact when the CSV is opened in Excel.
            let csv = '﻿결론,등급,결정적 근거,통계적 근거,어휘적 근거,검사 실패,출처 추정,모델,파일명,검토,메모,판정 문장\n';
            results.forEach(e => {
                const item = e.item, r = item.result || {};
                const rev = reviewStore[itemKey(item)] || {};
                const ev = r.evidence || [];
                const titles = kind => ev.filter(x => x.kind === kind).map(x => x.title).join(' / ');
                const failedCov = (r.coverage || []).filter(c => c.status === 'failed').map(coverageText).join(' / ');
                csv += [f(riskLabel(verdictOf(item))), f(r.grade === 'reference' ? '참고' : (r.grade ? '근거' : '')), f(titles('deterministic')), f(titles('statistical')), f(titles('lexical')), f(failedCov), f((r.source_guess && r.source_guess.label) || ''), f((r.model_analysis && (r.model_analysis.display_name || r.model_analysis.model)) || ''), f(item.name || item.path), f(rev.star ? '검토됨' : ''), f(rev.note || ''), f(r.verdict || '')].join(',') + '\n';
            });
            download(new Blob([csv], { type: 'text/csv;charset=utf-8' }), 'deepfake-lens-results.csv');
        });
        $('exp-report').addEventListener('click', async () => {
            if (!results.length) { toast('분석 결과가 없습니다', true); return; }
            try {
                const res = await apiFetch('/api/report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(reportPayload()),
                });
                const type = res.headers.get('Content-Type') || '';
                if (!type.includes('text/html')) {
                    toast('리포트 생성 실패: ' + await apiError(res), true);
                    return;
                }
                download(await res.blob(), 'deepfake-lens-report.html');
            } catch (e) { toast('리포트 오류: ' + e.message, true); }
        });
        $('exp-pdf').addEventListener('click', async () => {
            if (!results.length) { toast('분석 결과가 없습니다', true); return; }
            try {
                toast('포렌식 감정 PDF 생성 중…');
                const res = await apiFetch('/api/report?format=pdf', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(reportPayload({ format: 'pdf', exhibit_no: '갑 제        호증' })),
                });
                const type = res.headers.get('Content-Type') || '';
                if (!type.includes('application/pdf')) {
                    toast('PDF 생성 실패: ' + await apiError(res), true);
                    return;
                }
                download(await res.blob(), 'deepfake-lens-forensic-report.pdf');
                toast('포렌식 감정서(PDF)가 다운로드되었습니다.');
            } catch (e) { toast('PDF 생성 오류: ' + e.message, true); }
        });
        $('exp-evidence').addEventListener('click', async () => {
            if (!results.length) { toast('분석 결과가 없습니다', true); return; }
            try {
                toast('전자소송 증거설명서(PDF) 생성 중…');
                const res = await apiFetch('/api/report?format=evidence', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(reportPayload({ format: 'evidence' })),
                });
                const type = res.headers.get('Content-Type') || '';
                if (!type.includes('application/pdf')) {
                    toast('증거설명서 생성 실패: ' + await apiError(res), true);
                    return;
                }
                download(await res.blob(), 'deepfake-lens-evidence-statement.pdf');
                toast('전자소송 증거설명서(PDF)가 다운로드되었습니다.');
            } catch (e) { toast('증거설명서 오류: ' + e.message, true); }
        });
        $('exp-clear').addEventListener('click', () => {
            results = [];
            $('results-section').hidden = true;
        });

        /* ── quick check ───────────────────────────── */
        $('qc-text-btn').addEventListener('click', () => {
            const text = $('qc-text').value;
            if (!text.trim()) { toast('검사할 텍스트를 입력하세요', true); return; }
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

        $('qc-file-btn').addEventListener('click', () => $('qc-file').click());
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

        async function runQuickCheck(options) {
            const status = $('qc-status'), box = $('qc-out');
            startElapsed(status, '전체 검사 실행 중… 뉴럴 모델 로딩 시 수십 초');
            ['qc-text-btn', 'qc-file-btn'].forEach(id => $(id).disabled = true);
            try {
                const data = await apiJson('/api/check', options);
                if (data.error) {
                    box.className = 'qc-out on';
                    box.innerHTML = `<div class="verdict c-red">오류: ${escapeHtml(data.error)}</div>`;
                    return;
                }
                if (data.mode === 'files') {
                    // Archive upload: members are analyzed as their own rows.
                    box.className = 'qc-out';
                    box.innerHTML = '';
                    processResults(data);
                    toast(`압축 해제 — ${(data.items || []).length}개 파일 분석됨`);
                    return;
                }
                renderQuickCheck(data);
            } catch (e) {
                box.className = 'qc-out on';
                box.innerHTML = `<div class="verdict c-red">검사 실패: ${escapeHtml(e.message)}</div>`;
            } finally {
                stopElapsed(status);
                ['qc-text-btn', 'qc-file-btn'].forEach(id => $(id).disabled = false);
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
            if (wt !== undefined && (wa || 0) === 0) parts.push('신경망 미탑재(측정 게이트 미충족) — 결정적 근거만 반영');
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
            const band = bandCls(verdict);
            const parts = [];
            parts.push(`<div class="qc-head">
                <span class="big bc-${band}">${escapeHtml(r.score_is_calibrated ? String(Number(r.score) || 0) : ({ high: '!', low: '✓', medium: '?' }[band] || '–'))}</span>
                <div><span class="band-pill band-${band}">${escapeHtml(riskLabel(verdict))}</span>
                <div class="note mt-4">${escapeHtml(item.name || '')}</div></div></div>`);
            if (item.error) parts.push(`<div class="verdict">분석 실패: ${escapeHtml(item.error)}</div>`);
            if (item.result) {
                parts.push(verdictHeadHtml(r));
                parts.push(evidenceHtml(r));
                parts.push(coverageHtml(r));
            }
            parts.push(provenanceNoteHtml(data));

            const ma = r.model_analysis;
            if (ma) {
                const members = (ma.models || []).map(m =>
                    `<li>${escapeHtml(m.display_name || m.model || '모델')} — ${m.score != null ? m.score : '없음'}${m.available === false ? ' (사용 불가)' : ''}</li>`).join('');
                parts.push(layer(`외부 모델 원점수(미보정, 결론 불참여) — ${ma.score != null ? ma.score : '없음'}`,
                    members ? `<ul>${members}</ul>` : `<div class="note">${escapeHtml(ma.detail || '')}</div>`));
            }
            if (data.advanced) {
                // D1: layer diagnostic — raw numbers with the fixed notice,
                // never a band or a "+points" weight (G4).
                const a = data.advanced, body = a.diagnostic || a;
                const sig = (body.signals || []).map(s => `<li>${escapeHtml(s.title)} — ${escapeHtml(s.detail)}</li>`).join('');
                const lim = (body.limitations || []).map(l => `<li>${escapeHtml(l)}</li>`).join('');
                parts.push(layer(`스타일/지문 분석 · 계층 진단(참고 신호 · 미측정) — 원점수 ${a.raw_score != null ? a.raw_score : 0}`,
                    (a.notice ? `<div class="note">${escapeHtml(a.notice)}</div>` : '') +
                    (sig ? `<ul>${sig}</ul>` : '<div class="note">발동 신호 없음</div>') + (lim ? `<ul class="c-amber">${lim}</ul>` : '')));
            }
            if (data.forensic) {
                const fr = data.forensic, body = fr.diagnostic || fr;
                const sig = (body.signals || []).map(s => `<li>${escapeHtml(s.title)} — ${escapeHtml(s.detail)}</li>`).join('');
                const prov = (body.provenance_records || []).map(p => `<li>${escapeHtml(p.kind || p.source || p.standard || 'record')}: ${escapeHtml(p.summary || p.detail || p.provider || '')}</li>`).join('');
                parts.push(layer(`메타데이터 / C2PA · 계층 진단(참고 신호 · 미측정) — 원점수 ${fr.raw_score != null ? fr.raw_score : 0}`,
                    (fr.notice ? `<div class="note">${escapeHtml(fr.notice)}</div>` : '') +
                    ((sig ? `<ul>${sig}</ul>` : '') + (prov ? `<ul>${prov}</ul>` : '') || '<div class="note">단서 없음</div>')));
            }
            if (data.watermark) {
                const w = data.watermark;
                parts.push(layer('워터마크 (KGW)',
                    `<div class="kv"><b>측정</b><span>${escapeHtml(w.reference_note || '')}</span><b>z 점수</b><span>${w.z_score != null ? w.z_score : '없음'}</span><b>참고 원점수</b><span>${w.score != null ? w.score : '없음'}</span></div>`));
            }
            parts.push(listItems('참고 신호(미측정 휴리스틱 — 결론 불참여)', (r.reference_signals || []).map(s => ({ title: `${s.title} (${s.weight})`, detail: s.detail }))));
            parts.push(listItems('다음 확인', r.next_checks));
            parts.push(`<div class="band-advice">${escapeHtml(verdictAdvice(r.verdict_code || 'undetermined', r.grade))}</div>`);
            parts.push(`<div class="caveat">결론은 결정적 근거로만 내립니다. 통계적·어휘적 근거와 검사 실패 내역은 근거·검사 범위 목록에 전부 남습니다.</div>`);
            box.innerHTML = parts.join('');
            box.className = 'qc-out on';
            box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        /* ── compare ───────────────────────────────── */
        const cmpFiles = { a: null, b: null };
        function bindSlot(slotId, inputId, key) {
            const slot = $(slotId), input = $(inputId);
            const pick = () => { if (!scanBusy) input.click(); };
            slot.addEventListener('click', pick);
            slot.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
            input.addEventListener('change', e => {
                const f = e.target.files[0];
                e.target.value = '';
                if (!f) return;
                cmpFiles[key] = f;
                slot.classList.add('filled');
                slot.querySelector('.sn').textContent = f.name;
                const old = slot.querySelector('.pv');
                if (old) old.remove();
                const pv = mediaTagFor(f, 'pv');
                if (pv) { pv.controls = false; slot.appendChild(pv); }
                $('cmp-btn').disabled = !(cmpFiles.a && cmpFiles.b);
            });
        }
        bindSlot('slot-a', 'cmp-a', 'a');
        bindSlot('slot-b', 'cmp-b', 'b');

        $('cmp-btn').addEventListener('click', async () => {
            if (!(cmpFiles.a && cmpFiles.b)) return;
            const status = $('cmp-status'), box = $('cmp-result');
            const form = new FormData();
            form.append('file_a', cmpFiles.a, cmpFiles.a.name);
            form.append('file_b', cmpFiles.b, cmpFiles.b.name);
            startElapsed(status, '비교 실행 중… (화자 모델 로딩 시 수십 초)');
            $('cmp-btn').disabled = true;
            try {
                const data = await apiJson('/api/compare', { method: 'POST', body: form });
                box.classList.add('on');
                if (data.error) {
                    box.innerHTML = `<div class="verdict c-red">오류: ${escapeHtml(data.error)}</div>`;
                    return;
                }
                renderCompare(data);
            } catch (e) {
                box.classList.add('on');
                box.innerHTML = `<div class="verdict c-red">비교 실패: ${escapeHtml(e.message)}</div>`;
            } finally {
                stopElapsed(status);
                $('cmp-btn').disabled = false;
            }
        });

        function renderCompare(data) {
            // D1: similarity is a layer diagnostic — raw number + notice,
            // no same/different band.
            const box = $('cmp-result');
            const body = data.diagnostic || data;
            const kind = body.kind === 'speaker' ? '화자 유사도 (음성)' : body.kind === 'stylometry' ? '필자 유사도 (텍스트)' : (body.kind || '비교');
            const score = data.raw_score != null ? data.raw_score : 0;
            const parts = [];
            parts.push(`<div class="qc-head">
                <span class="big bc-other">${Number(score) || 0}</span>
                <div><span class="band-pill band-unknown">계층 진단(참고 신호 · 미측정)</span>
                <div class="note mt-4">${escapeHtml(kind)}${body.method ? ' · ' + escapeHtml(body.method) : ''}</div></div></div>`);
            if (data.notice) parts.push(`<div class="note">${escapeHtml(data.notice)}</div>`);
            if (data.reference_note) parts.push(`<div class="verdict">${escapeHtml(data.reference_note)}</div>`);
            parts.push(provenanceNoteHtml(data));
            const kv = [];
            if (body.distance != null) kv.push(`<b>거리</b><span>${escapeHtml(String(body.distance))}</span>`);
            parts.push(layer('측정값', `<div class="kv">${kv.join('')}</div>`));
            parts.push(listItems('한계', body.limitations, 'lim'));
            parts.push(`<div class="caveat">유사도는 측정되지 않은 참고 수치이며 동일인 증명이 아닙니다. 추가 증거와 함께 해석하세요.</div>`);
            box.innerHTML = parts.join('');
        }

        /* ── initialization ───────────────────────── */
        fetchReviewsFromServer();

        /* ── version chip ──────────────────────────── */
        apiFetch('/api/stats').then(r => r.json()).then(d => {
            if (d.version) $('ver-chip').textContent = 'v' + d.version;
        }).catch(() => {});
