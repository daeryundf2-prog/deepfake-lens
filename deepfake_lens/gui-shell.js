/* Deepfake Lens — app shell (OMO-7). Talks to gui.js only through window.DFL and dfl:* events. */
(function () {
    'use strict';

    var DFL = window.DFL;
    var sidebar = document.getElementById('sidebar');
    var toggle = document.getElementById('sb-toggle');
    var closeBtn = document.getElementById('sb-close');
    var newBtn = document.getElementById('sb-new');
    var settingsBtn = document.getElementById('sb-settings');
    if (!DFL || !sidebar || !toggle) return;

    var WIDE = window.matchMedia('(min-width: 1181px)');
    var backdrop = document.createElement('div');
    backdrop.className = 'sb-backdrop';
    document.body.appendChild(backdrop);

    function isOpen() { return sidebar.classList.contains('is-open'); }

    function setOpen(open, restoreFocus) {
        sidebar.classList.toggle('is-open', open);
        backdrop.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        toggle.setAttribute('aria-label', open ? '검사 기록 닫기' : '검사 기록 열기');
        if (open) {
            if (newBtn) newBtn.focus();
        } else if (restoreFocus) {
            toggle.focus();
        }
    }

    function closeDrawer(restoreFocus) {
        if (isOpen()) setOpen(false, restoreFocus);
    }

    function settings() {
        document.dispatchEvent(new CustomEvent('dfl:open-settings', { detail: {} }));
    }

    function shortcutsOn() {
        try {
            var saved = JSON.parse(window.localStorage.getItem('dflens-settings-v1') || '{}');
            return !(saved && saved.shortcuts === false);
        } catch (e) {
            return true;
        }
    }

    toggle.addEventListener('click', function () { setOpen(!isOpen(), true); });
    if (closeBtn) closeBtn.addEventListener('click', function () { closeDrawer(true); });
    backdrop.addEventListener('click', function () { closeDrawer(true); });
    if (newBtn) newBtn.addEventListener('click', function () { closeDrawer(false); DFL.newScan(); });
    if (settingsBtn) settingsBtn.addEventListener('click', function () { closeDrawer(false); settings(); });

    function modalOpen() {
        return !!document.querySelector('dialog[open]');
    }

    document.addEventListener('keydown', function (ev) {
        if (ev.isComposing || ev.keyCode === 229) return;
        if (ev.key === 'Escape' && isOpen() && !modalOpen()) {
            ev.preventDefault();
            closeDrawer(true);
            return;
        }
        if (!(ev.ctrlKey || ev.metaKey) || ev.altKey || ev.shiftKey) return;
        var key = ev.key.toLowerCase();
        if (key !== 'n' && key !== ',') return;
        if (!shortcutsOn() || modalOpen()) return;
        ev.preventDefault();
        closeDrawer(false);
        if (key === 'n') DFL.newScan();
        else settings();
    });

    // Widening past the breakpoint leaves no drawer state behind.
    function onResize() { if (WIDE.matches && isOpen()) setOpen(false, false); }
    if (WIDE.addEventListener) WIDE.addEventListener('change', onResize);
    else if (WIDE.addListener) WIDE.addListener(onResize);

    sidebar.hidden = false;
    toggle.hidden = false;
    document.body.classList.add('has-shell');
})();
