// claude-remote web page: connection, tabs, header, sheet.
'use strict';

(function () {
  if (window.opener) {   // a page that opened us could script us (all portal apps share one origin)
    document.body.textContent = 'For safety, open this link directly (not from another page).';
    return;
  }
  const $ = (s) => document.querySelector(s);
  const { el } = UI;
  const base = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
  const wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + base + 'ws';
  let ws = null, lastMsg = 0, retry = 0, status = null, skew = 0, tab = 'chat', connected = false;
  window.CRStatus = {};

  function send(m) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); }

  // ── keep the layout above the phone keyboard (iOS ignores interactive-widget) ──
  function fitViewport() {
    const vv = window.visualViewport;
    const h = vv ? vv.height : window.innerHeight;
    document.documentElement.style.setProperty('--app-h', h + 'px');
    if (vv && vv.offsetTop) window.scrollTo(0, 0);
  }
  if (window.visualViewport) { visualViewport.addEventListener('resize', fitViewport); visualViewport.addEventListener('scroll', fitViewport); }
  window.addEventListener('resize', fitViewport);
  fitViewport();

  // ── tabs ────────────────────────────────────────────────────────────────
  function showTab(name) {
    tab = name;
    document.querySelectorAll('#tabs button').forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
    $('#chat').hidden = name !== 'chat';
    $('#term').hidden = name !== 'term';
    if (name === 'term') CRTerm.show(); else CRTerm.hide();
    try { sessionStorage.setItem('cr_tab', name); } catch (e) { /* ignore */ }
  }
  document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

  // ── header ──────────────────────────────────────────────────────────────
  function fmtLeft(sec) {
    if (sec <= 0) return 'ending';
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
    return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m} min`;
  }
  function setConn(state) { $('#dot').className = 'dot ' + state; connected = state === 'on'; }
  function banner(text, link) {
    const b = $('#banner');
    if (!text) { b.hidden = true; return; }
    b.replaceChildren();
    if (link) { const a = el('a', '', text); a.href = link; b.appendChild(a); } else b.textContent = text;
    b.hidden = false;
  }
  function renderStatus() {
    if (!status) return;
    window.CRStatus.node = status.node;
    $('#node').textContent = status.node;
    $('#node').title = status.cwd;
    const left = status.end - (Date.now() / 1000 - skew);
    const pill = $('#left');
    pill.hidden = !status.end;
    pill.textContent = fmtLeft(left);
    pill.classList.toggle('warn', left < 900);
    if (!status.claude_running) banner('Claude has exited — the job is ending.');
    else if (status.ending_soon || (status.end && left < 600)) banner('This job reaches its wall time in under 10 minutes. Resume later with claude-remote -c.');
    else if (connected) banner(null);
  }
  setInterval(renderStatus, 15000);

  // ── sheet: conversations + job ──────────────────────────────────────────
  function openSheet() {
    $('#backdrop').hidden = false; $('#sheet').hidden = false;
    $('#sessions').replaceChildren(el('div', 'muted', 'Loading…'));
    send({ type: 'sessions' });
    const info = $('#jobinfo'); info.replaceChildren();
    if (status) {
      [['Node', status.node], ['Job', status.jobid], ['Folder', status.cwd],
       ['Time left', fmtLeft(status.end - (Date.now() / 1000 - skew))]].forEach(([k, v]) => {
        info.appendChild(el('div', 'k', k)); info.appendChild(el('div', 'v', String(v)));
      });
    }
    const nb = $('#notifbtn');
    const perm = window.Notification ? Notification.permission : 'unsupported';
    nb.textContent = perm === 'granted' ? 'Notifications on' : perm === 'denied' ? 'Notifications blocked' : 'Enable notifications';
    nb.disabled = perm !== 'default';
  }
  function closeSheet() { $('#backdrop').hidden = true; $('#sheet').hidden = true; }
  function renderSessions(list) {
    const box = $('#sessions'); box.replaceChildren();
    if (!list.length) { box.appendChild(el('div', 'muted', 'No saved conversations in this folder yet.')); return; }
    list.forEach((s) => {
      const b = el('button', 'item' + (s.current ? ' current' : ''));
      b.appendChild(el('span', 'i-title', s.title || '(untitled conversation)'));
      b.appendChild(el('span', 'i-sub', (s.current ? 'current · ' : '') + UI.relTime(s.mtime) + ' · ' + UI.fmtSize(s.size)));
      b.addEventListener('click', () => {
        if (s.current) { closeSheet(); return; }
        if (!confirm('Switch Claude to this conversation? (/resume)')) return;
        send({ type: 'send', text: '/resume ' + s.id, cid: UI.uuid() });
        closeSheet(); showTab('chat'); UI.toast('Resuming conversation…');
      });
      box.appendChild(b);
    });
  }
  $('#menubtn').addEventListener('click', openSheet);
  $('#backdrop').addEventListener('click', closeSheet);
  $('#newconv').addEventListener('click', () => {
    if (!confirm('Start a new conversation? The current one stays saved (/clear).')) return;
    send({ type: 'send', text: '/clear', cid: UI.uuid() }); closeSheet(); showTab('chat');
  });
  $('#copylink').addEventListener('click', () => UI.copyText(location.href.split('#')[0]));
  $('#notifbtn').addEventListener('click', async () => {
    try { await Notification.requestPermission(); } catch (e) { /* ignore */ }
    openSheet();
  });
  $('#endjob').addEventListener('click', () => {
    if (confirm('End this SLURM job? Claude stops; the conversation is saved (resume with claude-remote -c).')) { send({ type: 'end_job' }); closeSheet(); }
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#sheet').hidden) closeSheet(); });

  // ── connection ──────────────────────────────────────────────────────────
  function connect() {
    setConn('connecting');
    ws = new WebSocket(wsUrl);
    ws.onopen = () => {
      retry = 0; setConn('on'); lastMsg = Date.now();
      send({ type: 'chat_open' });
      CRTerm.reconnected();
      if (status) renderStatus();
    };
    ws.onmessage = (ev) => {
      lastMsg = Date.now();
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === 'status') {
        status = m; skew = Date.now() / 1000 - m.now; renderStatus();
        if (m.live) CRChat.handle({ type: 'status2', status: m.live });
        if (m.terms) CRTerm.handle({ type: 'terms', terms: m.terms });
        return;
      }
      if (m.type === 'notice') { UI.toast(m.text, 4000); return; }
      if (m.type === 'sessions') { renderSessions(m.list || []); return; }
      if (m.type === 'pong') return;
      if (CRTerm.handle(m)) return;
      CRChat.handle(m);
    };
    ws.onclose = () => {
      setConn('off');
      if (navigator.onLine === false) banner('You are offline — reconnecting when the network is back…');
      else banner('Connection lost — reconnecting…');
      const delay = Math.min(15000, 800 * Math.pow(2, retry++));
      setTimeout(checkThenConnect, delay);
    };
  }

  // The portal answers with a redirect to its login page once the portal session expires.
  async function checkThenConnect() {
    try {
      const r = await fetch(base, { redirect: 'manual', cache: 'no-store' });
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 302) {
        banner('Your portal login expired — tap here to log in again', location.href);
        return;
      }
      if ([403, 404, 502, 503].includes(r.status)) {
        banner('This claude-remote job has ended or is not reachable.');
        setTimeout(checkThenConnect, 30000);
        return;
      }
    } catch (e) { /* network down: just retry */ }
    connect();
  }

  setInterval(() => { if (ws && ws.readyState === 1 && Date.now() - lastMsg > 45000) ws.close(); }, 5000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (!ws || ws.readyState > 1) checkThenConnect(); else send({ type: 'ping' });
  });
  window.addEventListener('online', () => { if (!ws || ws.readyState > 1) checkThenConnect(); });

  if (navigator.serviceWorker && navigator.serviceWorker.controller) {
    banner('Warning: a service worker controls this page — another app on the portal may be interfering.');
  }

  CRChat.init(send);
  CRTerm.init(send);
  try { tab = sessionStorage.getItem('cr_tab') || 'chat'; } catch (e) { /* ignore */ }
  showTab(tab);
  connect();
})();
