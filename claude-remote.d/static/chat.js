// claude-remote chat view: a live mirror of the real Claude session.
'use strict';

window.CRChat = (function () {
  const $ = (s) => document.querySelector(s);
  const { el, icon, showInvisible, md } = UI;
  let send = () => {};
  const items = new Map();      // id → {item, el}
  const order = [];             // item ids, sorted by timestamp
  const perms = new Map();      // perm id → card element
  const streams = new Map();    // MessageDisplay message id → element
  let menu = null, held = [], live = {}, snapshotDone = false;
  let attachments = [];         // {name, path, size, el, done}
  let unread = 0, moreHistory = false, sessionId = null, lastMode = null, modeTapAt = 0;

  // ── scrolling ───────────────────────────────────────────────────────────
  const scroller = () => $('#scroller');
  function atBottom() { const s = scroller(); return s.scrollHeight - s.scrollTop - s.clientHeight < 80; }
  function toBottom(smooth) {
    const s = scroller();
    s.scrollTo({ top: s.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    unread = 0; renderJump();
  }
  function renderJump() {
    $('#jump').hidden = atBottom();
    $('#unread').textContent = unread ? String(unread) : '';
  }

  // ── tool cards ──────────────────────────────────────────────────────────
  const TOOL_ICON = {
    Bash: 'terminal', BashOutput: 'terminal', KillShell: 'terminal', Read: 'file', Write: 'pencil', Edit: 'pencil',
    MultiEdit: 'pencil', NotebookEdit: 'pencil', Glob: 'search', Grep: 'search', WebFetch: 'globe', WebSearch: 'globe',
    Task: 'sparkles', Agent: 'sparkles', TodoWrite: 'list', TaskCreate: 'list', TaskUpdate: 'list', ExitPlanMode: 'map',
    AskUserQuestion: 'help', Skill: 'sparkles', ToolSearch: 'search',
  };
  function toolIcon(name) { return icon(TOOL_ICON[name] || (String(name).startsWith('mcp__') ? 'plug' : 'tool')); }
  function toolLabel(name) { return String(name).startsWith('mcp__') ? name.split('__').slice(1).join(' · ') : name; }
  function toolSummary(it) {
    const i = it.input || {};
    if (it.name === 'Read' && i.file_path) return i.file_path + (i.offset ? ` (from line ${i.offset})` : '');
    if (it.name === 'Grep') return (i.pattern || '') + (i.path ? '  in ' + i.path : '');
    if (it.name === 'TodoWrite' && Array.isArray(i.todos)) {
      const done = i.todos.filter((t) => t.status === 'completed').length;
      return `${done}/${i.todos.length} done`;
    }
    return i.command || i.file_path || i.notebook_path || i.pattern || i.url || i.query || i.description || i.prompt || i.skill || '';
  }
  function block(text, cls) { const p = el('pre', 'block ' + (cls || '')); p.textContent = showInvisible(text); return p; }
  function toolBody(name, input) {
    const box = el('div', 't-body');
    if ((name === 'Edit' || name === 'MultiEdit') && (input.old_string !== undefined || Array.isArray(input.edits))) {
      if (input.file_path) box.appendChild(el('div', 't-label', input.file_path));
      const edits = Array.isArray(input.edits) ? input.edits : [input];
      edits.forEach((e) => { box.appendChild(block(e.old_string || '', 'del')); box.appendChild(block(e.new_string || '', 'add')); });
    } else if (name === 'Write' && input.content !== undefined) {
      if (input.file_path) box.appendChild(el('div', 't-label', input.file_path));
      box.appendChild(block(input.content, 'add'));
    } else if (name === 'Bash') {
      box.appendChild(block(input.command || '', 'cmd'));
      if (input.description) box.appendChild(el('div', 't-label', input.description));
    } else if (name === 'TodoWrite' && Array.isArray(input.todos)) {
      const ul = el('ul', 'todos');
      input.todos.forEach((t) => {
        const li = el('li', t.status === 'completed' ? 'done' : t.status === 'in_progress' ? 'active' : '');
        li.appendChild(el('span', '', t.status === 'completed' ? '☑' : t.status === 'in_progress' ? '◐' : '☐'));
        li.appendChild(el('span', '', t.content || t.activeForm || ''));
        ul.appendChild(li);
      });
      box.appendChild(ul);
    } else {
      box.appendChild(block(JSON.stringify(input, null, 2)));
    }
    return box;
  }
  function renderTool(it) {
    const e = el('details', 'msg tool' + (it.is_error ? ' error' : ''));
    const s = el('summary');
    const ic = el('span', 't-ico'); ic.appendChild(toolIcon(it.name)); s.appendChild(ic);
    s.appendChild(el('span', 't-name', toolLabel(it.name)));
    s.appendChild(el('span', 't-sum', showInvisible(toolSummary(it)).slice(0, 300)));
    const st = el('span', 't-state ' + (it.result === null ? 'run' : it.is_error ? 'err' : 'ok'));
    if (it.result !== null) st.appendChild(icon(it.is_error ? 'x' : 'check'));
    s.appendChild(st);
    const ch = el('span', 't-chev'); ch.appendChild(icon('chevron')); s.appendChild(ch);
    e.appendChild(s);
    // body is built lazily on first open (keeps long sessions light)
    e.addEventListener('toggle', () => {
      if (!e.open || e.dataset.built) return;
      e.dataset.built = '1';
      const body = toolBody(it.name, it.input || {});
      if (it.result !== null && it.result !== undefined) {
        body.appendChild(el('div', 't-label', it.is_error ? 'Error' : 'Output'));
        body.appendChild(block(it.result || '(no output)'));
      }
      e.appendChild(body);
    });
    return e;
  }

  // ── file cards (cr-send) ────────────────────────────────────────────────
  function fileIcon(kind, name) {
    if (kind === 'image' || kind === 'svg') return 'image';
    if (/\.(csv|tsv)$/i.test(name)) return 'table';
    if (kind === 'text') return 'code';
    return 'doc';
  }
  function renderFile(it) {
    const e = el('div', 'msg file');
    const url = 'f/' + encodeURIComponent(it.share) + '/' + encodeURIComponent(it.name);
    const head = el('div', 'file-head');
    const ic = el('div', 'f-ico'); ic.appendChild(icon(fileIcon(it.filekind, it.name))); head.appendChild(ic);
    const meta = el('div', 'f-meta');
    meta.appendChild(el('div', 'f-name', it.name));
    meta.appendChild(el('div', 'f-sub', UI.fmtSize(it.size) + ' · ' + it.path));
    head.appendChild(meta);
    const dl = el('a', '', 'Download'); dl.href = url + '?dl=1'; dl.rel = 'noopener';
    head.appendChild(dl);
    e.appendChild(head);
    if (it.caption) e.appendChild(el('div', 'caption', it.caption));
    if (it.changed) e.appendChild(el('div', 't-label', 'This file has changed since Claude sent it — showing the current version.'));
    if (it.filekind === 'image' || it.filekind === 'svg') {
      const img = el('img', 'preview'); img.src = url; img.alt = it.name; img.loading = 'lazy';
      img.addEventListener('click', () => UI.lightbox(url));
      e.appendChild(img);
    } else if (it.filekind === 'text') {
      const box = el('div', 'file-text');
      const pre = el('pre', '', 'Loading preview…');
      box.appendChild(pre);
      e.appendChild(box);
      fetch(url, { cache: 'force-cache' }).then((r) => r.text()).then((t) => {
        if (/\.(csv|tsv)$/i.test(it.name)) { box.replaceChildren(csvTable(t, /\.tsv$/i.test(it.name) ? '\t' : ',')); return; }
        if (/\.md$/i.test(it.name)) { box.replaceChildren(md(t.slice(0, 200000))); box.style.padding = '10px 12px'; return; }
        const lines = t.split('\n');
        const code = el('code');
        code.textContent = showInvisible(lines.slice(0, 500).join('\n'));
        try { const r = hljs.highlightAuto(code.textContent); code.innerHTML = r.value; } catch (err) { /* plain */ }
        pre.replaceChildren(code);
        if (lines.length > 500) box.appendChild(el('div', 't-label', `… ${lines.length - 500} more lines — use Download for the whole file`));
      }).catch(() => { pre.textContent = '(preview unavailable)'; });
    }
    const acts = el('div', 'file-actions');
    const reply = el('button'); reply.appendChild(icon('reply')); reply.appendChild(document.createTextNode(' Reply about this'));
    reply.addEventListener('click', () => { const c = $('#composer'); c.value = `About ${it.path}: ` + c.value; c.focus(); autosize(); });
    acts.appendChild(reply);
    e.appendChild(acts);
    return e;
  }
  function csvTable(text, sep) {
    const wrap = el('div'); wrap.style.maxHeight = '360px'; wrap.style.overflow = 'auto';
    const t = el('table', 'csv');
    text.split('\n').filter((l) => l.trim()).slice(0, 80).forEach((l, i) => {
      const tr = el('tr');
      l.split(sep).forEach((c) => tr.appendChild(el(i === 0 ? 'th' : 'td', '', c)));
      t.appendChild(tr);
    });
    wrap.appendChild(t);
    return wrap;
  }

  // ── items ───────────────────────────────────────────────────────────────
  const ATTACH_RE = /\n*\[Attached for Claude to read\]\n((?:- .+\n?)+)$/;
  function renderItem(it) {
    let e;
    switch (it.kind) {
      case 'user': {
        e = el('div', 'msg user');
        let text = it.text || '';
        const m = text.match(ATTACH_RE);
        if (m) text = text.slice(0, m.index);
        e.appendChild(el('div', 'text', showInvisible(text)));
        if (m) m[1].trim().split('\n').forEach((line) => {
          const a = el('div', 'attach-line'); a.appendChild(icon('file'));
          a.appendChild(document.createTextNode(line.replace(/^- /, '').replace(/\s+\(.*\)$/, '').split('/').pop()));
          e.appendChild(a);
        });
        if (it.queued) e.appendChild(el('span', 'tag', 'sent while Claude was busy'));
        break;
      }
      case 'assistant': e = el('div', 'msg assistant'); e.appendChild(md(it.text)); break;
      case 'thinking': {
        e = el('details', 'msg thinking');
        e.appendChild(el('summary', '', 'Thinking'));
        e.addEventListener('toggle', () => { if (e.open && !e.dataset.built) { e.dataset.built = 1; e.appendChild(el('div', 'body', it.text)); } });
        break;
      }
      case 'tool': e = renderTool(it); break;
      case 'command': {
        e = el('div', 'msg command');
        e.appendChild(el('code', '', ((it.name || '') + ' ' + (it.args || '')).trim()));
        if (it.output) e.appendChild(el('span', '', it.output));
        break;
      }
      case 'notice': e = el('div', 'msg notice' + (/compacted/i.test(it.text) ? ' divider' : ''), it.text); break;
      case 'file': e = renderFile(it); break;
      default: e = el('div', 'msg notice', '[' + it.kind + ']');
    }
    return e;
  }

  function upsert(it, animate) {
    const stick = atBottom();
    const old = items.get(it.id);
    const node = renderItem(it);
    if (old) {
      if (old.el.open) { node.open = true; }
      old.el.replaceWith(node); old.item = it; old.el = node;
    } else {
      items.set(it.id, { item: it, el: node });
      let idx = order.length;
      const ts = it.ts || '';
      while (idx > 0 && (items.get(order[idx - 1]).item.ts || '') > ts) idx--;
      order.splice(idx, 0, it.id);
      const nextId = order[idx + 1];
      if (nextId) $('#msgs').insertBefore(node, items.get(nextId).el); else $('#msgs').appendChild(node);
      if (animate) { node.classList.add('fresh'); if (!stick) unread++; }
    }
    $('#empty').hidden = true;
    if (stick) toBottom(false); else renderJump();
  }
  function prepend(list) {
    const s = scroller(); const before = s.scrollHeight;
    const first = order.length ? items.get(order[0]).el : null;
    list.forEach((it) => {
      if (items.has(it.id)) return;
      const node = renderItem(it);
      items.set(it.id, { item: it, el: node });
      order.splice(0, 0, it.id);
      $('#msgs').insertBefore(node, first || null);
    });
    // keep the reader's position
    order.sort((a, b) => (items.get(a).item.ts || '').localeCompare(items.get(b).item.ts || ''));
    s.scrollTop += s.scrollHeight - before;
  }
  function reset(list) {
    items.clear(); order.length = 0;
    const msgs = $('#msgs');
    [...msgs.children].forEach((c) => { if (c.id !== 'earlier') c.remove(); });
    list.forEach((it) => upsert(it, false));
    toBottom(false);
  }

  // ── streaming text (MessageDisplay deltas) ──────────────────────────────
  function stream(mid, text) {
    let e = streams.get(mid);
    const stick = atBottom();
    if (!e) {
      e = el('div', 'msg assistant streaming fresh');
      streams.set(mid, e);
      $('#streams').appendChild(e);
    }
    e.replaceChildren(md(text));
    if (stick) toBottom(false);
  }
  function clearStreams(mids) {
    (mids || [...streams.keys()]).forEach((mid) => {
      const e = streams.get(mid);
      if (!e) return;
      streams.delete(mid);
      e.remove();
    });
  }

  // ── approvals ───────────────────────────────────────────────────────────
  function shortPath(p) { const parts = String(p).split('/').filter(Boolean); return parts.length > 3 ? '…/' + parts.slice(-2).join('/') : p; }
  function suggestionText(s) {
    if (!s) return '';
    if (s.rules) return s.rules.map((r) => r.toolName + (r.ruleContent ? `(${r.ruleContent})` : '')).join(', ');
    if (s.type === 'addDirectories') return 'access to ' + (s.directories || []).map(shortPath).join(', ');
    if (s.type === 'setMode') return 'switch to ' + s.mode + ' mode';
    return s.type || '';
  }
  function button(label, cls, fn) { const b = el('button', cls || '', label); b.addEventListener('click', fn); return b; }
  function answer(p, msg) {
    const c = perms.get(p.id);
    if (c) c.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    send(Object.assign({ type: 'perm_answer', id: p.id }, msg));
  }
  function renderPerm(p) {
    const c = el('div', 'card perm');
    const title = el('div', 'card-title');
    if (p.tool_name === 'AskUserQuestion') {
      title.appendChild(el('span', 'badge', 'Question'));
      title.appendChild(el('span', '', 'Claude needs your input'));
      c.appendChild(title);
      const answers = {};
      (p.tool_input.questions || []).forEach((q) => {
        const box = el('div', 'question');
        box.appendChild(el('div', 'q', q.question));
        const picked = new Set();
        (q.options || []).forEach((o) => {
          const b = el('button', 'opt');
          b.appendChild(el('span', '', o.label));
          if (o.description && o.description !== o.label) b.appendChild(el('span', 'o-desc', o.description));
          b.addEventListener('click', () => {
            if (q.multiSelect) { picked.has(o.label) ? picked.delete(o.label) : picked.add(o.label); b.classList.toggle('on'); answers[q.question] = [...picked]; }
            else { box.querySelectorAll('.opt').forEach((x) => x.classList.remove('on')); b.classList.add('on'); answers[q.question] = o.label; }
          });
          box.appendChild(b);
        });
        const other = el('input'); other.placeholder = 'Or type your own answer…';
        other.addEventListener('input', () => { if (other.value.trim()) answers[q.question] = other.value; });
        box.appendChild(other);
        c.appendChild(box);
      });
      const row = el('div', 'row');
      row.appendChild(button('Send answers', 'primary', () => answer(p, { choice: 'answers', answers })));
      row.appendChild(button('Skip', '', () => answer(p, { choice: 'deny', message: 'The user skipped the question.' })));
      c.appendChild(row);
      return c;
    }
    if (p.tool_name === 'ExitPlanMode') {
      title.appendChild(el('span', 'badge', 'Plan'));
      title.appendChild(el('span', '', 'Ready to start — approve the plan?'));
      c.appendChild(title);
      const plan = el('div', 'plan'); plan.appendChild(md(p.tool_input.plan || '')); c.appendChild(plan);
      const fb = el('input', 'reason'); fb.placeholder = 'Optional: what should change?';
      c.appendChild(fb);
      const row = el('div', 'row');
      row.appendChild(button('Approve plan', 'primary', () => answer(p, { choice: 'allow' })));
      row.appendChild(button('Keep planning', '', () => answer(p, { choice: 'deny', message: fb.value || 'Keep planning; the user has not approved yet.' })));
      c.appendChild(row);
      return c;
    }
    title.appendChild(el('span', 'badge', 'Approval'));
    title.appendChild(el('span', '', `Claude wants to use ${toolLabel(p.tool_name)}`));
    c.appendChild(title);
    c.appendChild(toolBody(p.tool_name, p.tool_input || {}));
    const why = el('input', 'reason'); why.placeholder = 'Optional: tell Claude why, or what to do instead';
    c.appendChild(why);
    const row = el('div', 'row');
    row.appendChild(button('Allow', 'primary', () => answer(p, { choice: 'allow' })));
    const rules = (p.suggestions || []).filter((s) => s.type !== 'setMode');
    if (rules.length) row.appendChild(button('Always allow', '', () => answer(p, { choice: 'always' })));
    row.appendChild(button('Deny', 'danger', () => answer(p, { choice: 'deny', message: why.value })));
    c.appendChild(row);
    if (rules.length) c.appendChild(el('div', 'note', '“Always allow” also permits: ' + rules.map(suggestionText).join('; ')));
    return c;
  }
  function addPerm(p, quiet) {
    if (perms.has(p.id)) return;
    const c = renderPerm(p);
    perms.set(p.id, c);
    $('#cards').appendChild(c);
    if (!quiet) alertUser('Claude needs your approval', toolLabel(p.tool_name) + ': ' + (p.tool_input.command || p.tool_input.file_path || ''));
    updateTitle();
  }
  function donePerm(id) {
    const c = perms.get(id);
    if (!c) return;
    perms.delete(id);
    c.classList.add('leaving');
    setTimeout(() => c.remove(), 200);
    updateTitle();
  }

  // ── dialogs mirrored from the terminal ──────────────────────────────────
  function renderMenu() {
    const box = $('#menucard');
    box.replaceChildren();
    box.hidden = !menu;
    if (!menu) return;
    const title = el('div', 'card-title');
    title.appendChild(el('span', 'badge', 'Claude asks'));
    title.appendChild(el('span', '', menu.title));
    box.appendChild(title);
    menu.options.forEach((o, i) => {
      const b = el('button', 'opt' + (i === menu.selected ? ' on' : ''));
      b.appendChild(el('span', '', (menu.numbered ? (i + 1) + '. ' : '') + o));
      b.addEventListener('click', () => { box.querySelectorAll('button').forEach((x) => { x.disabled = true; }); send({ type: 'menu_pick', fp: menu.fp, index: i }); });
      box.appendChild(b);
    });
    box.style.animation = 'none'; void box.offsetWidth; box.style.animation = '';
  }

  // ── status: working pill, chips, send/stop ──────────────────────────────
  const SPIN = ['·', '✢', '✳', '✶', '✻', '✽', '✻', '✶', '✳', '✢'];
  let spinI = 0;
  setInterval(() => {
    const w = $('#working');
    if (w && !w.hidden) {
      spinI = (spinI + 1) % SPIN.length;
      w.querySelector('.spinner').textContent = SPIN[spinI];
      const a = live.activity;
      if (a && a.since) $('#working-time').textContent = UI.fmtDuration(Date.now() / 1000 - a.since);
      else $('#working-time').textContent = '';
    }
  }, 140);

  const MODE = {   // [label, short label for phones, style]
    default: ['Ask before acting', 'Ask first', ''], manual: ['Ask before acting', 'Ask first', ''],
    acceptEdits: ['Accept edits', 'Auto-edit', 'edits'], plan: ['Plan mode', 'Plan', 'plan'], auto: ['Auto mode', 'Auto', 'edits'],
    bypassPermissions: ['Skip permissions', 'No prompts', 'bypass'], dontAsk: ["Don't ask", "Don't ask", 'bypass'],
  };
  function prettyModel(m) {
    if (!m) return 'model';
    let s = String(m);
    const big = /\[1m\]$/i.test(s);
    s = s.replace(/\[1m\]$/i, '');
    const mm = s.match(/^(?:claude-)?([a-z]+)-(\d+)(?:-(\d+))?(?:-\d{8})?$/i);
    if (mm) s = mm[1][0].toUpperCase() + mm[1].slice(1) + ' ' + mm[2] + (mm[3] ? '.' + mm[3] : '');
    s = s.charAt(0).toUpperCase() + s.slice(1);
    return s + (big ? ' (1M)' : '');
  }
  function renderStatus() {
    const s = live || {};
    $('#st-model').textContent = prettyModel(s.model);
    $('#st-effort').textContent = s.effort ? 'effort ' + s.effort : 'effort';
    const m = MODE[s.mode] || [s.mode || 'mode', s.mode || 'mode', ''];
    $('#st-mode').textContent = window.innerWidth < 480 ? m[1] : m[0];
    const chip = $('#modechip');
    const changed = lastMode !== null && s.mode && s.mode !== lastMode;
    chip.className = 'chip ' + m[2] + (changed ? ' bump' : '');
    if (changed && Date.now() - modeTapAt < 4000) UI.toast('Mode: ' + m[0], 1800);
    if (s.mode) lastMode = s.mode;
    renderContext(s.context);
    const w = $('#working');
    w.classList.toggle('wait', s.waiting === 'permission');
    if (s.waiting === 'permission') { w.hidden = false; $('#working-text').textContent = 'Waiting for your approval'; }
    else if (s.busy) {
      w.hidden = false;
      const a = s.activity;
      $('#working-text').textContent = a ? `${toolLabel(a.tool)} · ${a.summary || ''}` : (s.subagents ? `Working (${s.subagents} agent${s.subagents > 1 ? 's' : ''})…` : 'Thinking…');
    } else w.hidden = true;
    syncSend();
    const h = $('#heldnote');
    h.hidden = !held.length;
    h.textContent = held.length ? `${held.length} message${held.length > 1 ? 's' : ''} queued — sent after the question above is answered.` : '';
    updateTitle();
  }
  function fmtK(n) { return n >= 1e6 ? (n / 1e6).toFixed(n % 1e6 ? 1 : 0) + 'M' : Math.round(n / 1000) + 'k'; }
  function renderContext(c) {
    const chip = $('#ctxchip');
    if (!c || !c.window) { chip.hidden = true; return; }
    const pct = Math.min(100, Math.round(100 * c.used / c.window));
    chip.hidden = false;
    chip.style.setProperty('--p', pct);
    chip.classList.toggle('warn', pct >= 50 && pct < 80);
    chip.classList.toggle('bad', pct >= 80);
    $('#st-ctx').textContent = window.innerWidth < 480 ? pct + '%' : `${fmtK(c.used)} / ${fmtK(c.window)}`;
    chip.title = `Context: ${c.used.toLocaleString()} of ${c.window.toLocaleString()} tokens (${pct}%) — tap to compact`;
    chip.dataset.used = c.used; chip.dataset.window = c.window; chip.dataset.pct = pct;
  }
  function syncSend() {
    const btn = $('#sendbtn');
    const empty = !$('#composer').value.trim() && !attachments.length;
    const stop = empty && live.busy;
    btn.classList.toggle('stop', stop);
    btn.setAttribute('aria-label', stop ? 'Stop Claude' : 'Send');
    btn.disabled = empty && !stop;
  }
  function updateTitle() {
    const node = (window.CRStatus && window.CRStatus.node) || '';
    const prefix = perms.size || menu ? '⚠ ' : live.busy ? '✻ ' : '';
    document.title = prefix + 'Claude' + (node ? ' · ' + node : '');
  }
  function alertUser(title, body) {
    if (navigator.vibrate) { try { navigator.vibrate([60, 40, 60]); } catch (e) { /* ignore */ } }
    if (document.visibilityState === 'visible') return;
    try { if (window.Notification && Notification.permission === 'granted') new Notification(title, { body, tag: 'claude-remote' }); } catch (e) { /* ignore */ }
  }

  // ── composer + attachments ──────────────────────────────────────────────
  function autosize() { const c = $('#composer'); c.style.height = 'auto'; c.style.height = Math.min(c.scrollHeight, 200) + 'px'; syncSend(); }
  function submit() {
    const btn = $('#sendbtn');
    if (btn.classList.contains('stop')) { send({ type: 'key', key: 'esc' }); UI.toast('Interrupting Claude…'); return; }
    if (attachments.some((a) => !a.done)) { UI.toast('Waiting for uploads to finish…'); return; }
    const c = $('#composer');
    let text = c.value.trim();
    if (!text && !attachments.length) return;
    if (attachments.length) {
      text = (text || 'Please look at the attached file(s).') + '\n\n[Attached for Claude to read]\n' +
        attachments.map((a) => `- ${a.path} (${a.name})`).join('\n');
    }
    send({ type: 'send', text, cid: UI.uuid() });
    c.value = '';
    attachments.forEach((a) => a.el.remove()); attachments = []; $('#attachments').hidden = true;
    autosize();
    toBottom(true);
  }
  function upload(file) {
    const a = { name: file.name, size: file.size, done: false, path: '' };
    const chip = el('div', 'att');
    chip.appendChild(el('span', 'a-name', file.name));
    const bar = el('div', 'a-bar'); bar.style.width = '0%'; chip.appendChild(bar);
    const x = el('button', '', '×'); x.setAttribute('aria-label', 'Remove');
    chip.appendChild(x);
    a.el = chip;
    $('#attachments').appendChild(chip); $('#attachments').hidden = false;
    attachments.push(a);
    const xhr = new XMLHttpRequest();
    x.addEventListener('click', () => { xhr.abort(); chip.remove(); attachments = attachments.filter((y) => y !== a); if (!attachments.length) $('#attachments').hidden = true; syncSend(); });
    xhr.open('POST', 'upload?name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.style.width = (100 * e.loaded / e.total).toFixed(0) + '%'; };
    xhr.onload = () => {
      try {
        const r = JSON.parse(xhr.responseText);
        if (r.ok) { a.path = r.path; a.done = true; bar.style.width = '100%'; setTimeout(() => { bar.style.opacity = 0; }, 400); }
        else { UI.toast('Upload failed: ' + (r.error || xhr.status)); chip.remove(); attachments = attachments.filter((y) => y !== a); }
      } catch (e) { UI.toast('Upload failed'); chip.remove(); attachments = attachments.filter((y) => y !== a); }
      syncSend();
    };
    xhr.onerror = () => { UI.toast('Upload failed (connection)'); chip.remove(); attachments = attachments.filter((y) => y !== a); syncSend(); };
    xhr.send(file);
    syncSend();
  }

  function init(sendFn) {
    send = sendFn;
    const coarse = window.matchMedia && matchMedia('(pointer: coarse)').matches;
    const c = $('#composer');
    c.addEventListener('input', autosize);
    c.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !coarse && !ev.isComposing) { ev.preventDefault(); submit(); }
    });
    $('#sendbtn').addEventListener('click', submit);
    $('#modechip').addEventListener('click', () => {
      const chip = $('#modechip');
      if (chip.classList.contains('pending')) return;
      chip.classList.add('pending');
      modeTapAt = Date.now();
      send({ type: 'key', key: 'btab' });
      setTimeout(() => chip.classList.remove('pending'), 2500);
    });
    $('#attach').addEventListener('click', () => $('#filepick').click());
    $('#ctxchip').addEventListener('click', () => {
      const c = $('#ctxchip').dataset;
      const msg = `Context: ${Number(c.used).toLocaleString()} of ${Number(c.window).toLocaleString()} tokens (${c.pct}%).\n\n` +
        'Compact the conversation now? Claude summarises older messages to free space (/compact).';
      if (confirm(msg)) { send({ type: 'send', text: '/compact', cid: UI.uuid() }); UI.toast('Compacting…'); }
    });
    $('#filepick').addEventListener('change', (e) => { [...e.target.files].forEach(upload); e.target.value = ''; });
    $('#pick-model').addEventListener('change', (e) => { if (e.target.value) { send({ type: 'send', text: '/model ' + e.target.value, cid: UI.uuid() }); UI.toast('Switching model…'); } e.target.value = ''; });
    $('#pick-effort').addEventListener('change', (e) => { if (e.target.value) { send({ type: 'send', text: '/effort ' + e.target.value, cid: UI.uuid() }); UI.toast('Setting effort…'); } e.target.value = ''; });
    $('#scroller').addEventListener('scroll', () => { if (atBottom()) unread = 0; renderJump(); }, { passive: true });
    $('#jump').addEventListener('click', () => toBottom(true));
    $('#earlier').addEventListener('click', () => { $('#earlier').disabled = true; send({ type: 'history' }); });
    // keep the dock height in a CSS variable (positions the jump button)
    new ResizeObserver(() => document.documentElement.style.setProperty('--dock-h', $('#dock').offsetHeight + 'px')).observe($('#dock'));
    syncSend();
  }

  function handle(m) {
    switch (m.type) {
      case 'snapshot':
        reset(m.items || []);
        clearStreams();
        Object.entries(m.streams || {}).forEach(([mid, text]) => stream(mid, text));
        [...perms.values()].forEach((c) => c.remove()); perms.clear();
        (m.perms || []).forEach((p) => addPerm(p, true));
        menu = m.menu; renderMenu();
        held = m.held || []; live = m.status || {};
        moreHistory = !!m.truncated; $('#earlier').hidden = !moreHistory; $('#earlier').disabled = false;
        sessionId = m.session_id;
        $('#empty').hidden = (m.items || []).length > 0;
        $('#empty-sub').textContent = m.session_id ? 'Send a message to start.' : 'Starting Claude… if this folder is new, answer the trust question below or in the Terminal tab.';
        snapshotDone = true;
        renderStatus();
        return true;
      case 'items': (m.ops || []).forEach(([, it]) => upsert(it, snapshotDone)); return true;
      case 'history':
        prepend(m.items || []);
        moreHistory = !!m.more; $('#earlier').hidden = !moreHistory; $('#earlier').disabled = false;
        if (!(m.items || []).length && moreHistory) send({ type: 'history' });
        return true;
      case 'status2': live = m.status || {}; renderStatus(); return true;
      case 'perm': addPerm(m.perm); return true;
      case 'perm_done': donePerm(m.id); return true;
      case 'menu': menu = m.menu; renderMenu(); if (menu) alertUser('Claude is asking', menu.title); updateTitle(); return true;
      case 'held': held = m.held || []; renderStatus(); return true;
      case 'stream': stream(m.mid, m.text); return true;
      case 'stream_clear': clearStreams(m.mids); return true;
      case 'send_failed': {
        const c = $('#composer'); if (!c.value) { c.value = m.text; autosize(); }
        UI.toast('Not sent: ' + m.why, 6000);
        return true;
      }
      case 'sent': return true;
    }
    return false;
  }

  return { init, handle, get sessionId() { return sessionId; } };
})();
