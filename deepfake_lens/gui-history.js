// OMO-1 app shell: sidebar scan history list.
// OMO-8: the finished scans the server keeps (GET /api/history) are listed newest first; a click reopens one with its
// case fields (DFL.loadJob + GET /api/job-case), 지우기 deletes it after a confirmation (POST /api/history-delete), a
// running scan shows its progress at the top, and the case fields are saved with the scan (POST /api/job-case).
(function () {
    'use strict';

    const DFL = window.DFL;
    const list = document.getElementById('sb-history');
    const empty = document.getElementById('sb-history-empty');
    const note = document.getElementById('sb-history-note');
    const dialog = document.getElementById('history-confirm');
    const dialogText = document.getElementById('history-confirm-text');
    const dialogYes = document.getElementById('history-confirm-yes');
    const dialogNo = document.getElementById('history-confirm-no');
    if (!DFL || !list) return;

    const JSON_HEADERS = { 'Content-Type': 'application/json' };
    let items = [];
    let running = null;
    let pendingDelete = null;
    let refreshing = null;
    let caseTimer = 0;

    function setNote(text) {
        note.textContent = text || '';
        note.hidden = !text;
    }

    async function errorText(res) {
        try {
            const body = await res.json();
            if (body && (body.error || body.detail)) return String(body.error || body.detail);
        } catch (e) { /* not JSON */ }
        return `서버 오류 (HTTP ${res.status})`;
    }

    function pad(n) { return String(n).padStart(2, '0'); }
    function when(seconds) {
        if (typeof seconds !== 'number') return '';
        const d = new Date(seconds * 1000);
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function titleOf(item) {
        const c = item.case || {};
        const label = [c.case_no, c.case_name].filter(Boolean).join(' · ');
        return label || item.folder_name || '이름 없는 검사';
    }

    function folderName(path) {
        const parts = String(path || '').split(/[\\/]+/).filter(Boolean);
        return parts.length ? parts[parts.length - 1] : '';
    }

    function caseTitle(fields) {
        return [fields.case_no, fields.case_name].map(v => String(v || '').trim()).filter(Boolean).join(' · ');
    }

    function hasCase(fields) {
        return Object.keys(fields || {}).some(k => String(fields[k] || '').trim());
    }

    function countsNode(summary) {
        const wrap = document.createElement('span');
        wrap.className = 'sb-counts';
        const rows = [
            ['manipulation_evidence', 'c-manip', '조작·생성 근거'],
            ['authenticity_evidence', 'c-auth', '원본성 근거'],
            ['undetermined', 'c-undet', '판단 불가'],
            ['unsupported_or_failed', 'c-na', '분석 못 함'],
        ];
        for (const [key, cls, label] of rows) {
            if (!summary || typeof summary[key] !== 'number') continue;
            const span = document.createElement('span');
            span.className = cls;
            span.textContent = String(summary[key]);
            span.title = label;
            span.setAttribute('aria-label', `${label} ${summary[key]}개`);
            wrap.appendChild(span);
        }
        return wrap.childElementCount ? wrap : null;
    }

    function runningNode() {
        const li = document.createElement('li');
        li.className = 'sb-item is-running';
        const box = document.createElement('div');
        box.className = 'sb-open';
        const title = document.createElement('span');
        title.className = 'sb-title';
        title.textContent = running.title || '검사';
        const status = document.createElement('span');
        status.className = 'sb-status';
        status.setAttribute('role', 'status');
        status.textContent = running.total ? `검사 중 ${running.done} / ${running.total}` : '검사 중';
        const bar = document.createElement('progress');
        bar.className = 'sb-progress';
        if (running.total) { bar.max = running.total; bar.value = Math.min(running.done, running.total); }
        bar.setAttribute('aria-label', '검사 진행');
        box.append(title, status, bar);
        li.appendChild(box);
        return li;
    }

    function itemNode(item) {
        const li = document.createElement('li');
        li.className = 'sb-item';
        li.dataset.job = item.job_id;
        if (DFL.currentJob() === item.job_id) li.setAttribute('aria-current', 'true');
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'sb-open';
        const title = document.createElement('span');
        title.className = 'sb-title';
        title.textContent = titleOf(item);
        const meta = document.createElement('span');
        meta.className = 'sb-meta';
        let metaText = `${when(item.finished)} · ${item.files}개 파일`;
        if (item.status === 'cancelled') metaText += ' · 부분 결과';
        meta.textContent = metaText;
        open.append(title, meta);
        const counts = countsNode(item.summary);
        if (counts) open.appendChild(counts);
        open.addEventListener('click', () => openItem(item, li));
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'sb-del';
        del.textContent = '지우기';
        del.setAttribute('aria-label', `「${titleOf(item)}」 기록 지우기`);
        del.addEventListener('click', () => askDelete(item, del));
        li.append(open, del);
        return li;
    }

    function render() {
        list.replaceChildren();
        if (running) list.appendChild(runningNode());
        for (const item of items) {
            if (running && item.job_id === running.jobId) continue;
            list.appendChild(itemNode(item));
        }
        empty.hidden = list.childElementCount > 0;
        document.dispatchEvent(new CustomEvent('dfl:history', { detail: { count: items.length } }));
    }

    async function refresh() {
        if (refreshing) return refreshing;
        refreshing = (async () => {
            try {
                const res = await DFL.apiFetch('/api/history');
                if (!res.ok) { setNote(await errorText(res)); return; }
                const body = await res.json();
                items = Array.isArray(body.items) ? body.items : [];
                setNote(body.note || '');
                render();
            } catch (e) {
                setNote(e && e.message ? e.message : '검사 기록을 불러오지 못했습니다');
            } finally {
                refreshing = null;
            }
        })();
        return refreshing;
    }

    async function openItem(item, li) {
        if (DFL.isScanning()) { DFL.toast('검사가 진행 중입니다 — 검사가 끝난 뒤 기록을 여십시오', true); return; }
        let ok = false;
        try {
            ok = await DFL.loadJob(item.job_id, item.finished);
        } catch (e) {
            setNote(e && e.message ? e.message : '검사 기록을 열지 못했습니다');
            return;
        }
        if (!ok) return;
        list.querySelectorAll('.sb-item[aria-current]').forEach(el => el.removeAttribute('aria-current'));
        li.setAttribute('aria-current', 'true');
        let fields = {};
        try {
            const res = await DFL.apiFetch('/api/job-case?job=' + encodeURIComponent(item.job_id));
            if (res.ok) fields = (await res.json()).case || {};
            else setNote(await errorText(res));
        } catch (e) {
            setNote(e && e.message ? e.message : '사건 정보를 불러오지 못했습니다');
        }
        DFL.setCase(fields);  // always: a scan without saved fields must not show another case's 사건번호
    }

    function askDelete(item, button) {
        pendingDelete = { item, button };
        dialogText.textContent = `「${titleOf(item)}」 검사 기록을 지울까요? 이 PC에 보관한 결과와 사건 정보가 지워집니다(원본 파일은 그대로입니다).`;
        if (typeof dialog.showModal === 'function') dialog.showModal();
        else dialog.setAttribute('open', '');
        dialogNo.focus();
    }

    function closeDialog() {
        if (dialog.open) dialog.close();
        const back = pendingDelete && pendingDelete.button;
        pendingDelete = null;
        if (back && back.isConnected) back.focus();
        else document.getElementById('sb-new').focus();
    }

    async function confirmDelete() {
        const target = pendingDelete && pendingDelete.item;
        if (!target) { closeDialog(); return; }
        try {
            const res = await DFL.apiFetch('/api/history-delete', {
                method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ job: target.job_id }),
            });
            if (!res.ok) {
                setNote(await errorText(res));
            } else {
                setNote('');
                if (DFL.currentJob() === target.job_id) {
                    DFL.detachJob();
                    DFL.toast('이 검사 기록을 지웠습니다 — 감정서를 내보내려면 폴더를 다시 검사하십시오', true);
                }
            }
        } catch (e) {
            setNote(e && e.message ? e.message : '검사 기록을 지우지 못했습니다');
        }
        pendingDelete = Object.assign({}, pendingDelete, { button: null });
        closeDialog();
        await refresh();
    }

    async function saveCase(jobId, fields) {
        try {
            const res = await DFL.apiFetch('/api/job-case', {
                method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ job: jobId, case: fields }),
            });
            if (!res.ok) { setNote(await errorText(res)); return false; }
            return true;
        } catch (e) {
            setNote(e && e.message ? e.message : '사건 정보를 저장하지 못했습니다');
            return false;
        }
    }

    function startRunning(jobId, folder, caseForScan) {
        // the case label only when the case belongs to the scanned folder (as when it is saved on finish)
        const fields = caseForScan ? DFL.getCase() : null;
        running = { jobId, title: (fields && caseTitle(fields)) || folderName(folder) || '검사', done: 0, total: 0 };
        render();
    }

    dialogYes.addEventListener('click', confirmDelete);
    dialogNo.addEventListener('click', closeDialog);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(); });

    document.addEventListener('dfl:scan-started', event => {
        const d = event.detail || {};
        startRunning(d.jobId, d.folder, d.caseForScan === true);
    });
    document.addEventListener('dfl:scan-progress', event => {
        const d = event.detail || {};
        if (!running) startRunning(d.jobId, '', DFL.caseConfirmed());
        running.done = Number.isInteger(d.done) ? d.done : running.done;
        running.total = Number.isInteger(d.total) ? d.total : running.total;
        render();
    });
    document.addEventListener('dfl:scan-finished', async event => {
        const d = event.detail || {};
        running = null;
        if (d.status === 'error') { render(); return; }
        const fields = DFL.getCase();
        if (d.jobId && DFL.caseConfirmed() && hasCase(fields)) await saveCase(d.jobId, fields);
        await refresh();
    });
    document.addEventListener('dfl:job-loaded', () => render());
    document.addEventListener('dfl:ready', () => refresh());

    const caseBox = document.getElementById('case-fields');
    function caseEdited() {
        clearTimeout(caseTimer);
        caseTimer = setTimeout(async () => {
            const job = DFL.currentJob();
            if (!job || DFL.isScanning() || !DFL.caseConfirmed()) return;
            if (await saveCase(job, DFL.getCase())) await refresh();
        }, 500);
    }
    if (caseBox) {
        caseBox.addEventListener('change', caseEdited);
        caseBox.addEventListener('input', caseEdited);
    }
    document.querySelectorAll('input[name="submitter-side"]').forEach(el => el.addEventListener('change', caseEdited));

    // A reload during a scan: gui.js resumed it before this script loaded, so read the state now.
    if (DFL.isScanning() && DFL.currentJob()) startRunning(DFL.currentJob(), '');
    if (DFL.isReady()) refresh();
})();
