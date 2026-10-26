// OMO-10: the first-run welcome panel (#welcome): the three steps and one way in.
(function () {
    'use strict';

    const SETTINGS_KEY = 'dflens-settings-v1';
    const DEFAULTS = { theme: 'system', pixel: 'off', recursive: false, shortcuts: true, welcomeDismissed: false };

    const panel = document.getElementById('welcome');
    const startBtn = document.getElementById('welcome-start');
    const dismissBtn = document.getElementById('welcome-dismiss');
    if (!panel || !startBtn || !dismissBtn || !window.DFL) return;

    let historyCount = 0;   // the last dfl:history count; 0 until the history says otherwise

    function readSettings() {
        try {
            const parsed = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
            return Object.assign({}, DEFAULTS, parsed && typeof parsed === 'object' ? parsed : {});
        } catch (e) {
            return Object.assign({}, DEFAULTS);
        }
    }

    function saveDismissed() {
        const next = readSettings();
        next.welcomeDismissed = true;
        try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(next)); } catch (e) { /* storage unavailable: hidden for this visit only */ }
    }

    function setShown(shown) {
        panel.hidden = !shown;
        document.body.classList.toggle('has-welcome', shown);
    }

    function startLabel() {
        startBtn.textContent = window.DFL.isDesktop() ? '검사할 폴더 고르기' : '폴더 경로 입력하기';
    }

    function evaluate() {
        const shown = !readSettings().welcomeDismissed && historyCount === 0
            && !window.DFL.hasResults() && !window.DFL.isScanning();
        if (shown) startLabel();
        setShown(shown);
    }

    startBtn.addEventListener('click', () => {
        if (window.DFL.isDesktop()) window.DFL.pickFolder();
        else window.DFL.newScan();
    });

    dismissBtn.addEventListener('click', () => {
        saveDismissed();
        setShown(false);
        const target = document.getElementById('sb-new');
        if (target) target.focus();
    });

    document.addEventListener('dfl:ready', evaluate);
    document.addEventListener('dfl:scan-started', () => setShown(false));
    document.addEventListener('dfl:job-loaded', () => setShown(false));
    document.addEventListener('dfl:history', event => {
        const count = event.detail && typeof event.detail.count === 'number' ? event.detail.count : 0;
        historyCount = count;
        if (count > 0) setShown(false);
        else evaluate();
    });
    document.addEventListener('dfl:show-welcome', () => {
        startLabel();
        setShown(true);
    });

    // gui.js starts before this script loads: read the state now instead of waiting for a past event.
    if (window.DFL.isReady()) evaluate();
})();
