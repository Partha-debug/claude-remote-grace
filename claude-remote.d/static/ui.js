// claude-remote — small shared UI helpers (no framework).
'use strict';

window.UI = (function () {
  const ICONS = {
    terminal: 'M4 6l5 5-5 5M11 17h9',
    file: 'M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V9zM14 3v6h6',
    pencil: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
    search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4',
    globe: 'M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
    sparkles: 'M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8zM18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z',
    list: 'M10 6h10M10 12h10M10 18h10M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2',
    map: 'M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14',
    help: 'M12 3a9 9 0 100 18 9 9 0 000-18zM9.5 9.5a2.5 2.5 0 114 2c-1 .6-1.5 1.2-1.5 2.5M12 17h.01',
    plug: 'M9 2v6M15 2v6M7 8h10v3a5 5 0 01-10 0zM12 16v6',
    tool: 'M14.5 5.5a4 4 0 00-5.4 5.2L4 15.8 8.2 20l5.1-5.1a4 4 0 005.2-5.4l-2.6 2.6-2.8-.6-.6-2.8z',
    image: 'M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9h.01',
    code: 'M8 8l-4 4 4 4M16 8l4 4-4 4M13 5l-2 14',
    doc: 'M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V9zM14 3v6h6M8 13h8M8 17h6',
    table: 'M4 5h16v14H4zM4 10h16M4 15h16M10 5v14',
    download: 'M12 4v12M6 11l6 6 6-6M4 20h16',
    copy: 'M9 9h11v11H9zM5 15V4h11',
    check: 'M5 12l5 5L20 7',
    x: 'M6 6l12 12M18 6L6 18',
    chevron: 'M9 6l6 6-6 6',
    shell: 'M4 5h16v14H4zM7 9l3 3-3 3M12 15h5',
    reply: 'M10 9L4 14l6 5M4 14h10a6 6 0 006-6V5',
  };

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function icon(name) {
    const ns = 'http://www.w3.org/2000/svg';
    const s = document.createElementNS(ns, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', ICONS[name] || ICONS.tool);
    s.appendChild(p);
    return s;
  }

  const INVIS = /[​-‏‪-‮⁦-⁩﻿]/g;
  function showInvisible(s) {
    return String(s == null ? '' : s).replace(INVIS, (c) => '⟦U+' + c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0') + '⟧');
  }

  function fmtSize(n) {
    if (n >= 1 << 30) return (n / (1 << 30)).toFixed(1) + ' GB';
    if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' KB';
    return n + ' B';
  }
  function fmtDuration(sec) {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + 's';
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return m + 'm ' + String(s).padStart(2, '0') + 's';
    return Math.floor(m / 60) + 'h ' + (m % 60) + 'm';
  }
  function relTime(epochSec) {
    const d = Date.now() / 1000 - epochSec;
    if (d < 60) return 'just now';
    if (d < 3600) return Math.floor(d / 60) + ' min ago';
    if (d < 86400) return Math.floor(d / 3600) + ' h ago';
    if (d < 7 * 86400) return Math.floor(d / 86400) + ' d ago';
    return new Date(epochSec * 1000).toLocaleDateString();
  }

  // ── toasts ──────────────────────────────────────────────────────────────
  function toast(text, ms) {
    const box = document.getElementById('toasts');
    const t = el('div', 'toast', text);
    box.appendChild(t);
    setTimeout(() => { t.classList.add('leaving'); setTimeout(() => t.remove(), 260); }, ms || 3200);
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast('Copied'); }
    catch (e) {
      const ta = el('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); toast('Copied'); } catch (e2) { toast('Copy failed'); }
      ta.remove();
    }
  }

  // ── markdown (sanitised; code blocks highlighted with a copy button) ────
  function md(text) {
    const raw = marked.parse(String(text || ''), { breaks: true, gfm: true });
    const html = DOMPurify.sanitize(raw, { FORBID_TAGS: ['style', 'form', 'input', 'textarea', 'select', 'button'], FORBID_ATTR: ['style'] });
    const d = el('div', 'md');
    d.innerHTML = html;
    d.querySelectorAll('a').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    d.querySelectorAll('img').forEach((i) => i.remove());     // no remote images on the shared portal origin
    d.querySelectorAll(':not(pre) > code').forEach((c) => { if (c.textContent.length <= 28) c.classList.add('nw'); });
    d.querySelectorAll('pre > code').forEach((code) => {
      const pre = code.parentElement;
      const lang = (code.className.match(/language-([\w+-]+)/) || [])[1] || '';
      try {
        if (lang && hljs.getLanguage(lang)) hljs.highlightElement(code);
        else if (code.textContent.length < 20000) { const r = hljs.highlightAuto(code.textContent); code.innerHTML = r.value; }
      } catch (e) { /* plain text is fine */ }
      const box = el('div', 'codeblock');
      const head = el('div', 'cb-head');
      head.appendChild(el('span', '', lang || 'code'));
      const btn = el('button', '', 'Copy');
      btn.addEventListener('click', () => copyText(code.textContent));
      head.appendChild(btn);
      pre.replaceWith(box);
      box.appendChild(head);
      box.appendChild(pre);
    });
    return d;
  }

  function lightbox(src) {
    const lb = document.getElementById('lightbox');
    const img = lb.querySelector('img');
    img.src = src;
    lb.hidden = false;
    lb.onclick = () => { lb.hidden = true; img.src = ''; };
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'id-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  const store = {
    get(k, d) { try { const v = localStorage.getItem('cr_' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('cr_' + k, JSON.stringify(v)); } catch (e) { /* storage may be blocked */ } },
  };

  return { el, icon, showInvisible, fmtSize, fmtDuration, relTime, toast, copyText, md, lightbox, uuid, store };
})();
