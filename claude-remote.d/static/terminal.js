// claude-remote Terminal tab: the real Claude screen, plus shell tabs on the same node.
'use strict';

window.CRTerm = (function () {
  const $ = (s) => document.querySelector(s);
  const { el, icon } = UI;
  let send = () => {};
  let term = null, fit = null, webgl = null;
  let active = UI.store.get('term_active', 'claude');
  let terms = [{ name: 'claude', label: 'Claude' }];
  let open = false, visible = false, ctrl = false, hideTimer = null, resizeTimer = null;
  let fontSize = UI.store.get('term_font', window.innerWidth < 600 ? 12 : 13.5);

  const THEME = {
    background: '#1a1918', foreground: '#e8e6e0', cursor: '#e0896a', cursorAccent: '#1a1918',
    selectionBackground: '#5a4a40aa',
    black: '#1a1918', red: '#e06c66', green: '#8fcf9f', yellow: '#e2c27a', blue: '#7fa8e6', magenta: '#d39ad0', cyan: '#7cc9c9', white: '#d8d5cc',
    brightBlack: '#6f6b63', brightRed: '#f08a84', brightGreen: '#a8e0b5', brightYellow: '#f0d394', brightBlue: '#a2c1f2', brightMagenta: '#e5b5e2', brightCyan: '#9be0e0', brightWhite: '#f6f4ee',
  };

  async function create() {
    // measure cells with the bundled font (it contains every symbol Claude draws)
    try { await Promise.race([document.fonts.load(`${fontSize}px "CR Mono"`), new Promise((r) => setTimeout(r, 2500))]); } catch (e) { /* fall back */ }
    term = new Terminal({
      fontFamily: '"CR Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
      fontSize, lineHeight: 1.0, letterSpacing: 0, cursorBlink: true, scrollback: 10000,
      allowProposedApi: true, customGlyphs: true, rescaleOverlappingGlyphs: true, drawBoldTextInBrightColors: false,
      macOptionIsMeta: true, theme: THEME,
    });
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    try { const u = new Unicode11Addon.Unicode11Addon(); term.loadAddon(u); term.unicode.activeVersion = '11'; } catch (e) { /* older widths */ }
    term.open($('#xterm'));
    try {
      webgl = new WebglAddon.WebglAddon();
      webgl.onContextLoss(() => { try { webgl.dispose(); } catch (e) { /* ignore */ } webgl = null; });
      term.loadAddon(webgl);
    } catch (e) { webgl = null; /* DOM renderer fallback */ }
    term.onData((d) => {
      if (ctrl && d.length === 1) {
        const c = d.toLowerCase().charCodeAt(0);
        if (c >= 97 && c <= 122) d = String.fromCharCode(c - 96);
        else if (d === '[') d = '\x1b'; else if (d === ' ') d = '\x00';
        setCtrl(false);
      }
      send({ type: 'term_in', data: d });
    });
    new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(refit, 120); }).observe($('#xterm-wrap'));
  }

  function refit() {
    if (!term || !visible) return;
    try { fit.fit(); } catch (e) { return; }
    if (open) send({ type: 'term_resize', cols: term.cols, rows: term.rows });
  }

  function attach(name) {
    if (!term) return;
    active = name;
    UI.store.set('term_active', name);
    renderTabs();
    refit();
    term.reset();
    open = true;
    send({ type: 'term_open', name, cols: term.cols, rows: term.rows });
    if (!matchMedia('(pointer: coarse)').matches) term.focus();
  }
  function detach() {
    if (open) send({ type: 'term_close' });
    open = false;
  }

  // ── tabs ────────────────────────────────────────────────────────────────
  function renderTabs() {
    const box = $('#termtabs');
    box.replaceChildren();
    terms.forEach((t) => {
      const b = el('div', 'ttab' + (t.name === active ? ' on' : ''));
      b.setAttribute('role', 'tab');
      const glyph = el('span', 't-glyph');
      if (t.name === 'claude') glyph.textContent = '✻'; else glyph.appendChild(icon('shell'));
      b.appendChild(glyph);
      b.appendChild(el('span', '', t.label));
      if (t.name !== 'claude') {
        const x = el('button', 'x'); x.setAttribute('aria-label', 'Close ' + t.label); x.appendChild(icon('x'));
        x.addEventListener('click', (ev) => {
          ev.stopPropagation();
          if (confirm(`Close ${t.label}? Anything running in it is stopped.`)) send({ type: 'term_kill', name: t.name });
        });
        b.appendChild(x);
      }
      b.addEventListener('click', () => { if (t.name !== active) attach(t.name); });
      box.appendChild(b);
    });
    const add = el('button', 'ttab add', '+ Shell');
    add.title = 'Open a shell on this compute node (same job, same folder)';
    add.addEventListener('click', () => send({ type: 'term_new' }));
    box.appendChild(add);
  }

  // ── key bar ─────────────────────────────────────────────────────────────
  const KEYS = { esc: '\x1b', tab: '\t', btab: '\x1b[Z', up: '\x1b[A', down: '\x1b[B', right: '\x1b[C', left: '\x1b[D', enter: '\r', ctrlc: '\x03', ctrld: '\x04' };
  function setCtrl(v) { ctrl = v; const b = document.querySelector('#keys [data-key="ctrl"]'); if (b) b.classList.toggle('on', v); }
  function setFont(n) {
    fontSize = Math.max(9, Math.min(22, n));
    UI.store.set('term_font', fontSize);
    if (term) { term.options.fontSize = fontSize; refit(); }
  }
  function initKeys() {
    document.querySelectorAll('#keys button').forEach((b) => {
      b.addEventListener('pointerdown', (e) => e.preventDefault());      // keep the phone keyboard open
      b.addEventListener('click', async () => {
        const k = b.dataset.key;
        if (k === 'ctrl') { setCtrl(!ctrl); return; }
        if (k === 'paste') {
          try { const t = await navigator.clipboard.readText(); if (t) term.paste(t); }
          catch (e) { UI.toast('Paste not allowed here — long-press in the terminal instead'); }
          return;
        }
        if (k === 'smaller') return setFont(fontSize - 1);
        if (k === 'bigger') return setFont(fontSize + 1);
        send({ type: 'term_in', data: KEYS[k] });
      });
    });
  }

  // ── visibility: detach while hidden so a pocketed phone doesn't keep resizing the laptop's view
  function onPageVisibility() {
    if (!visible) return;
    if (document.visibilityState === 'hidden') {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(detach, 15000);
    } else {
      clearTimeout(hideTimer);
      if (!open) attach(active);
    }
  }

  async function show() {
    visible = true;
    if (!term) await create();
    renderTabs();
    attach(terms.some((t) => t.name === active) ? active : 'claude');
  }
  function hide() { visible = false; clearTimeout(hideTimer); detach(); }

  function init(sendFn) {
    send = sendFn;
    initKeys();
    renderTabs();
    document.addEventListener('visibilitychange', onPageVisibility);
  }
  function reconnected() { open = false; if (visible) attach(active); }

  function handle(m) {
    switch (m.type) {
      case 'term': if (term && m.name === active) term.write(m.data); return true;
      case 'term_closed':
        if (m.name === active) {
          open = false;
          if (term) term.write('\r\n\x1b[2m[' + (m.name === 'claude' ? 'Claude session ended' : 'shell closed') + ']\x1b[0m\r\n');
          if (m.name !== 'claude' && visible) setTimeout(() => attach('claude'), 600);
        }
        return true;
      case 'terms':
        terms = m.terms && m.terms.length ? m.terms : terms;
        if (!terms.some((t) => t.name === active) && visible) attach('claude');
        renderTabs();
        return true;
      case 'term_created': if (m.name && visible) attach(m.name); return true;
    }
    return false;
  }

  return { init, show, hide, handle, reconnected, setTerms(list) { if (list && list.length) { terms = list; renderTabs(); } } };
})();
