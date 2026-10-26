// OMO-9 settings dialog: theme, scan defaults, shortcuts flag, record/key/retention facts.
(function () {
    'use strict';

    const KEY = 'dflens-settings-v1';
    const DEFAULTS = { theme: 'system', pixel: 'off', recursive: false, shortcuts: true, welcomeDismissed: false };
    const THEMES = ['system', 'light', 'dark'];
    const PIXELS = ['off', 'fast', 'deep'];

    function load() {
        const out = Object.assign({}, DEFAULTS);
        try {
            const raw = JSON.parse(localStorage.getItem(KEY) || 'null');
            if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
                if (THEMES.includes(raw.theme)) out.theme = raw.theme;
                if (PIXELS.includes(raw.pixel)) out.pixel = raw.pixel;
                ['recursive', 'shortcuts', 'welcomeDismissed'].forEach(k => {
                    if (typeof raw[k] === 'boolean') out[k] = raw[k];
                });
            }
        } catch (e) { /* corrupt or blocked storage: defaults */ }
        return out;
    }

    function save(patch) {
        const next = Object.assign(load(), patch);
        try { localStorage.setItem(KEY, JSON.stringify(next)); } catch (e) { /* storage blocked: keep going */ }
        return next;
    }

    function applyTheme(theme) {
        const root = document.documentElement;
        const meta = document.querySelector('meta[name="color-scheme"]');
        if (theme === 'light' || theme === 'dark') {
            root.dataset.theme = theme;
            if (meta) meta.setAttribute('content', theme);
        } else {
            delete root.dataset.theme;
            if (meta) meta.setAttribute('content', 'light dark');
        }
    }

    const el = id => document.getElementById(id);
    let opener = null;

    function applyDefaults() {
        const s = load();
        if (window.DFL && !window.DFL.isScanning()) window.DFL.applyScanDefaults({ pixel: s.pixel, recursive: s.recursive });
    }

    function fillControls() {
        const s = load();
        el('set-theme-' + s.theme).checked = true;
        el('set-pixel-' + s.pixel).checked = true;
        el('set-recursive').checked = s.recursive;
        el('set-shortcuts').checked = s.shortcuts;
    }

    function fillStats(stats) {
        if (!stats) return;
        el('set-version').textContent = typeof stats.version === 'string' && stats.version ? stats.version : '확인할 수 없음';
        const signing = stats.signing;
        el('set-key').textContent = signing && signing.available
            ? '있음 — 감정서에 서명합니다'
            : '없음 — 서명 없이 내보냅니다';
    }

    async function fillHistory() {
        el('set-store').textContent = '확인하는 중…';
        el('set-retention').textContent = '확인하는 중…';
        try {
            const res = await window.DFL.apiFetch('/api/history');
            const data = await res.json();
            if (!res.ok) throw new Error('history');
            el('set-store').textContent = data.store
                ? data.store
                : (data.note || '기록을 보관하지 않습니다');
            el('set-retention').textContent = typeof data.retention_days === 'number' ? data.retention_days + '일' : '확인할 수 없음';
        } catch (e) {
            el('set-store').textContent = '확인할 수 없음';
            el('set-retention').textContent = '확인할 수 없음';
        }
    }

    async function open() {
        const dlg = el('settings-dialog');
        if (!dlg || dlg.open) return;
        opener = document.activeElement;
        fillControls();
        fillStats(window.DFL.stats());
        dlg.showModal();
        fillHistory();
        if (!window.DFL.stats()) {
            try {
                const res = await window.DFL.apiFetch('/api/stats');
                if (res.ok) fillStats(await res.json());
            } catch (e) {
                el('set-version').textContent = '확인할 수 없음';
                el('set-key').textContent = '확인할 수 없음';
            }
        }
    }

    function close() {
        const dlg = el('settings-dialog');
        if (dlg && dlg.open) dlg.close();
    }

    function init() {
        applyTheme(load().theme);
        const dlg = el('settings-dialog');
        if (!dlg) return;

        document.querySelectorAll('input[name="theme"]').forEach(r => r.addEventListener('change', () => {
            if (!r.checked) return;
            save({ theme: r.value });
            applyTheme(r.value);
        }));
        document.querySelectorAll('input[name="default-pixel"]').forEach(r => r.addEventListener('change', () => {
            if (!r.checked) return;
            save({ pixel: r.value });
            applyDefaults();
        }));
        el('set-recursive').addEventListener('change', e => {
            save({ recursive: e.target.checked });
            applyDefaults();
        });
        el('set-shortcuts').addEventListener('change', e => { save({ shortcuts: e.target.checked }); });
        el('set-onboarding').addEventListener('click', () => {
            save({ welcomeDismissed: false });
            close();
            document.dispatchEvent(new CustomEvent('dfl:show-welcome', { detail: {} }));
        });
        el('settings-close').addEventListener('click', close);
        dlg.addEventListener('close', () => {
            if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
            opener = null;
        });
        document.addEventListener('dfl:open-settings', open);
        document.addEventListener('dfl:ready', applyDefaults, { once: true });
        if (window.DFL && window.DFL.isReady()) applyDefaults();
    }

    init();
})();
