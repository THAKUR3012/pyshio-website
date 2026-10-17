// Floating help & chat panel on every dashboard.
//  - Assistant tab: answers from /api/assistant, which only reads the signed-in user's own data.
//    That conversation lives in this browser tab only (sessionStorage).
//  - Chats tab (patients, pharmacies, labs): the chat with the pharmacy / lab the patient booked. It opens
//    by itself after booking (PyshioChat.open(id)); either side can close or reopen it.
(() => {
  const P = window.Pyshio;
  if (!P) return;
  const { escapeHtml } = P;
  const KEY = 'pyshio-assistant';
  let history = [], busy = false, intro = null, me = null;
  let tab = 'assistant', chatId = null, chatPoll = null, listPoll = null, knownChats = null;

  const css = `
  #pa-btn { position: fixed; right: 18px; bottom: calc(18px + env(safe-area-inset-bottom, 0px)); z-index: 90; width: 56px; height: 56px; border-radius: 50%;
    background: #0f766e; color: #fff; border: 0; box-shadow: 0 8px 24px rgba(15,118,110,.35); cursor: pointer; display: grid; place-items: center; }
  #pa-btn:focus-visible { outline: 3px solid #99f6e4; outline-offset: 2px; }
  #pa-badge { position: absolute; top: -2px; right: -2px; min-width: 20px; height: 20px; border-radius: 10px; background: #dc2626; color: #fff;
    font: 700 11px/20px system-ui, sans-serif; padding: 0 5px; text-align: center; }
  #pa-panel { position: fixed; right: 18px; bottom: calc(86px + env(safe-area-inset-bottom, 0px)); z-index: 91; width: min(420px, calc(100vw - 24px));
    height: min(620px, calc(100vh - 120px)); background: #fff; border: 1px solid #cbd5e1; border-radius: 18px; box-shadow: 0 20px 50px rgba(15,23,42,.25);
    display: grid; grid-template-rows: auto auto 1fr auto auto; overflow: hidden; font-size: 14px; color: #0f172a; }
  #pa-panel[hidden] { display: none; }
  #pa-head { padding: 12px 14px; background: #0f766e; color: #fff; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  #pa-head b { font-size: 15px; } #pa-head small { opacity: .85; display: block; font-size: 11.5px; }
  #pa-head button { background: transparent; border: 0; color: #fff; cursor: pointer; font-size: 18px; padding: 4px 6px; border-radius: 6px; }
  #pa-tabs { display: flex; border-bottom: 1px solid #e2e8f0; background: #fff; }
  #pa-tabs button { flex: 1; border: 0; background: #fff; padding: 9px; font: 600 13px system-ui, sans-serif; color: #64748b; cursor: pointer; border-bottom: 2px solid transparent; }
  #pa-tabs button[aria-selected="true"] { color: #0f766e; border-bottom-color: #0f766e; }
  #pa-tabs .n { background: #dc2626; color: #fff; border-radius: 9px; padding: 0 6px; font-size: 11px; margin-left: 4px; }
  #pa-log { overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; background: #f8fafc; }
  .pa-m { max-width: 92%; padding: 8px 11px; border-radius: 14px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
  .pa-me { align-self: flex-end; background: #ccfbf1; border-bottom-right-radius: 4px; }
  .pa-bot { align-self: flex-start; background: #fff; border: 1px solid #e2e8f0; border-bottom-left-radius: 4px; }
  .pa-sys { align-self: center; max-width: 95%; text-align: center; font-size: 12px; color: #475569; background: #e2e8f0; border-radius: 999px; padding: 4px 12px; }
  .pa-time { display: block; font-size: 10.5px; color: #64748b; margin-top: 2px; }
  .pa-bot table { border-collapse: collapse; width: 100%; font-size: 12.5px; margin-top: 8px; font-variant-numeric: tabular-nums; white-space: normal; }
  .pa-bot th { text-align: center; font-weight: 700; white-space: nowrap; padding: 4px 6px; background: #f1f5f9; border-bottom: 1px solid #e2e8f0; }
  .pa-bot td { padding: 4px 6px; border-bottom: 1px solid #f1f5f9; text-align: left; }
  .pa-bot td.num { text-align: right; white-space: nowrap; }
  .pa-scroll { overflow-x: auto; }
  .pa-links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .pa-links a { font-size: 12.5px; font-weight: 600; color: #0f766e; border: 1px solid #99f6e4; border-radius: 999px; padding: 3px 10px; text-decoration: none; }
  .pa-chat { all: unset; cursor: pointer; display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 9px 11px; }
  .pa-chat:hover { border-color: #0f766e; }
  .pa-chat b { font-size: 13.5px; } .pa-chat small { color: #64748b; font-size: 12px; grid-column: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pa-chat .n { grid-row: 1 / span 2; grid-column: 2; align-self: center; background: #dc2626; color: #fff; border-radius: 10px; padding: 1px 7px; font-size: 11px; font-weight: 700; }
  .pa-chat .closed { grid-row: 1 / span 2; grid-column: 2; align-self: center; color: #64748b; font-size: 11px; }
  #pa-thread-head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid #e2e8f0; background: #fff; }
  #pa-thread-head b { flex: 1; min-width: 0; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #pa-thread-head button { border: 1px solid #cbd5e1; background: #fff; border-radius: 8px; padding: 3px 9px; font-size: 12px; cursor: pointer; color: #0f172a; }
  #pa-chips { display: flex; gap: 6px; overflow-x: auto; padding: 8px 10px 0; background: #fff; }
  #pa-chips button { flex: none; font-size: 12.5px; border: 1px solid #cbd5e1; background: #fff; border-radius: 999px; padding: 4px 10px; cursor: pointer; color: #0f172a; }
  #pa-chips button:hover { border-color: #0f766e; color: #0f766e; }
  #pa-form { display: flex; gap: 8px; padding: 10px; background: #fff; }
  #pa-input { flex: 1; min-width: 0; border: 1px solid #cbd5e1; border-radius: 10px; padding: 8px 10px; font: inherit; color: #0f172a; background: #fff; }
  #pa-form button { border: 0; background: #0f766e; color: #fff; border-radius: 10px; padding: 0 14px; font-weight: 600; cursor: pointer; }
  #pa-form button:disabled, #pa-input:disabled { opacity: .5; }
  .pa-typing span { display: inline-block; width: 6px; height: 6px; margin-right: 3px; border-radius: 50%; background: #94a3b8; animation: pa-b 1s infinite; }
  .pa-typing span:nth-child(2) { animation-delay: .15s; } .pa-typing span:nth-child(3) { animation-delay: .3s; }
  @keyframes pa-b { 0%, 80%, 100% { opacity: .3; } 40% { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { .pa-typing span { animation: none; } }`;

  const $ = id => document.getElementById(id);
  const hasChats = () => me && ['patient', 'pharmacy', 'lab'].includes(me.role);
  const time = s => new Date(s).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });

  function tableHtml(t) {
    if (!t || !t.rows || !t.rows.length) return '';
    const head = '<th>Sr No</th>' + t.columns.map(c => `<th>${escapeHtml(c.label)}</th>`).join('');
    const body = t.rows.map((r, i) => `<tr><td class="num">${i + 1}</td>${r.map((v, j) =>
      `<td class="${t.columns[j] && t.columns[j].num ? 'num' : ''}">${escapeHtml(typeof v === 'number' ? v.toLocaleString('en-IN') : v)}</td>`).join('')}</tr>`).join('');
    return `<div class="pa-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  function add(kind, data) {
    const el = document.createElement('div');
    el.className = `pa-m ${kind === 'me' ? 'pa-me' : 'pa-bot'}`;
    if (kind === 'me') el.textContent = data;
    else el.innerHTML = escapeHtml(data.text) + tableHtml(data.table) + (data.links && data.links.length
      ? `<div class="pa-links">${data.links.map(l => `<a href="${escapeHtml(l.href)}">${escapeHtml(l.label)} →</a>`).join('')}</div>` : '');
    $('pa-log').appendChild(el);
    el.scrollIntoView({ block: 'end' });
    return el;
  }
  function chips(list) {
    $('pa-chips').innerHTML = (list || []).map(c => `<button type="button">${escapeHtml(c)}</button>`).join('');
  }
  function save() {
    try { sessionStorage.setItem(KEY, JSON.stringify(history.slice(-20))); } catch (_) {}
  }
  function setComposer(placeholder, disabled = false) {
    $('pa-input').placeholder = placeholder;
    $('pa-input').disabled = disabled;
    document.querySelector('#pa-form button').disabled = disabled;
  }

  // ---------- Assistant ----------
  async function ask(text) {
    busy = true;
    add('me', text);
    const typing = add('bot', { text: '' });
    typing.innerHTML = '<span class="pa-typing" aria-label="Thinking"><span></span><span></span><span></span></span>';
    try {
      const res = await P.api('/api/assistant', { method: 'POST', body: {
        message: text, history: history.slice(-6).map(h => ({ role: h.role, text: (h.text || '').slice(0, 900) })) } });
      typing.remove();
      add('bot', res);
      chips(res.suggestions);
      history.push({ role: 'user', text }, { role: 'assistant', text: res.text, table: res.table, links: res.links });
      save();
    } catch (err) {
      typing.remove();
      add('bot', { text: err.message || 'Something went wrong. Please try again.' });
    } finally { busy = false; }
  }
  async function showAssistant() {
    tab = 'assistant'; chatId = null; stopChatPoll();
    renderTabs();
    $('pa-thread-head').hidden = true;
    $('pa-chips').hidden = false;
    setComposer('Ask about your appointments, orders, reports…');
    $('pa-log').innerHTML = '';
    if (!intro) {
      try {
        intro = await P.api('/api/assistant');
        $('pa-mode').textContent = intro.mode === 'ai' ? 'AI assistant · your data only' : 'Answers from your own data only';
      } catch (err) { add('bot', { text: err.message }); return; }
    }
    if (!history.length) add('bot', intro);
    history.forEach(h => add(h.role === 'user' ? 'me' : 'bot', h.role === 'user' ? h.text : h));
    chips(history.length ? (intro.suggestions || []).slice(0, 4) : intro.suggestions);
  }

  // ---------- Chats ----------
  function stopChatPoll() { if (chatPoll) { clearInterval(chatPoll); chatPoll = null; } }
  async function refreshBadge() {
    if (!hasChats()) return;
    try {
      const data = await P.api('/api/chats');
      const n = data.unread;
      $('pa-badge').textContent = n;
      $('pa-badge').hidden = !n;
      const t = $('pa-tab-chats');
      if (t) t.innerHTML = `Chats${n ? `<span class="n">${n}</span>` : ''}`;
      // Pharmacies and labs get a heads-up when a patient books them and a new chat starts
      if (knownChats && me.role !== 'patient') {
        data.chats.filter(c => !knownChats.has(c.id)).forEach(c => P.toast(`New chat: ${c.with_name} booked you (${c.title.split(' · ')[0]})`, 'success'));
      }
      knownChats = new Set(data.chats.map(c => c.id));
      return data;
    } catch (_) { return null; }
  }
  async function showChats() {
    tab = 'chats'; chatId = null; stopChatPoll();
    renderTabs();
    $('pa-thread-head').hidden = true;
    $('pa-chips').hidden = true;
    setComposer('Open a chat to send a message', true);
    const data = await refreshBadge();
    const log = $('pa-log');
    log.innerHTML = '';
    if (!data || !data.chats.length) {
      log.innerHTML = `<p class="pa-sys">${me.role === 'patient' ? 'When you book a pharmacy or lab, a chat with them opens here.' : 'When a patient books you, a chat with them opens here.'}</p>`;
      return;
    }
    data.chats.forEach(c => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'pa-chat';
      b.innerHTML = `<b>${escapeHtml(c.with_name)}</b>${c.unread ? `<span class="n">${c.unread}</span>` : c.status === 'closed' ? '<span class="closed">Closed</span>' : ''}
        <small>${escapeHtml(c.title)} · ${escapeHtml(c.last_body || '')}</small>`;
      b.addEventListener('click', () => openThread(c.id));
      log.appendChild(b);
    });
  }
  function renderThread(t) {
    $('pa-thread-title').textContent = `${t.with_name} · ${t.title.split(' · ')[0]}`;
    $('pa-thread-toggle').textContent = t.status === 'open' ? 'Close chat' : 'Reopen';
    const log = $('pa-log');
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.innerHTML = '';
    t.messages.forEach(m => {
      const el = document.createElement('div');
      if (m.author_role === 'system') { el.className = 'pa-sys'; el.textContent = m.body; }
      else {
        el.className = `pa-m ${m.author_role === me.role ? 'pa-me' : 'pa-bot'}`;
        el.innerHTML = `${escapeHtml(m.body)}<span class="pa-time">${escapeHtml(time(m.created_at))}${m.author_role === me.role && m.id <= t.seen_by_other_up_to ? ' · Seen' : ''}</span>`;
      }
      log.appendChild(el);
    });
    if (atBottom || !log.dataset.thread || log.dataset.thread !== String(t.id)) log.scrollTop = log.scrollHeight;
    log.dataset.thread = t.id;
    setComposer(t.status === 'open' ? `Message ${t.with_name}…` : 'This chat is closed. Reopen it to write.', t.status !== 'open');
  }
  async function openThread(id) {
    tab = 'chats'; chatId = id;
    renderTabs();
    $('pa-thread-head').hidden = false;
    $('pa-chips').hidden = true;
    try { renderThread(await P.api(`/api/chats/${id}`)); refreshBadge(); }
    catch (err) { P.toast(err.message, 'error'); return showChats(); }
    stopChatPoll();
    chatPoll = setInterval(async () => {
      if ($('pa-panel').hidden || chatId !== id) return;
      try { renderThread(await P.api(`/api/chats/${id}`)); } catch (_) {}
    }, 5000);
  }
  async function sendChat(text) {
    busy = true;
    try { renderThread(await P.api(`/api/chats/${chatId}/messages`, { method: 'POST', body: { body: text } })); $('pa-log').scrollTop = $('pa-log').scrollHeight; }
    catch (err) { P.toast(err.message, 'error'); }
    finally { busy = false; }
  }

  // ---------- Shell ----------
  function renderTabs() {
    if (!hasChats()) { $('pa-tabs').hidden = true; return; }
    $('pa-tab-assistant').setAttribute('aria-selected', String(tab === 'assistant'));
    $('pa-tab-chats').setAttribute('aria-selected', String(tab === 'chats'));
  }
  function openPanel() {
    $('pa-panel').hidden = false;
    $('pa-btn').setAttribute('aria-expanded', 'true');
    $('pa-input').focus();
  }
  function close() {
    $('pa-panel').hidden = true;
    stopChatPoll();
    $('pa-btn').setAttribute('aria-expanded', 'false');
    $('pa-btn').focus();
  }

  async function mount() {
    me = (await P.me()).user;
    if (!me) return;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <button id="pa-btn" type="button" aria-label="Open the Pyshio assistant and chats" aria-expanded="false" aria-controls="pa-panel">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M8 10h8M8 14h5m-9 6 3.2-3H18a3 3 0 0 0 3-3V7a3 3 0 0 0-3-3H6a3 3 0 0 0-3 3v7a3 3 0 0 0 1 2.2z"/></svg>
        <span id="pa-badge" hidden></span>
      </button>
      <section id="pa-panel" hidden role="dialog" aria-label="Pyshio assistant and chats">
        <div id="pa-head"><div><b>Pyshio</b><small id="pa-mode">Answers from your own data only</small></div>
          <div><button type="button" id="pa-clear" title="Clear assistant chat" aria-label="Clear assistant chat">⟲</button><button type="button" id="pa-close" aria-label="Close">✕</button></div></div>
        <div id="pa-tabs" role="tablist"><button type="button" role="tab" id="pa-tab-assistant" aria-selected="true">Assistant</button>
          <button type="button" role="tab" id="pa-tab-chats" aria-selected="false">Chats</button></div>
        <div style="display:grid;grid-template-rows:auto 1fr;min-height:0">
          <div id="pa-thread-head" hidden><button type="button" id="pa-thread-back" aria-label="Back to chats">←</button><b id="pa-thread-title"></b>
            <button type="button" id="pa-thread-toggle">Close chat</button></div>
          <div id="pa-log" aria-live="polite"></div></div>
        <div id="pa-chips"></div>
        <form id="pa-form"><input id="pa-input" maxlength="2000" autocomplete="off" placeholder="Ask about your appointments, orders, reports…" aria-label="Your message" />
          <button type="submit">Send</button></form>
      </section>`;
    document.body.appendChild(wrap);
    try { history = JSON.parse(sessionStorage.getItem(KEY) || '[]'); } catch (_) { history = []; }
    renderTabs();
    $('pa-btn').addEventListener('click', () => {
      if (!$('pa-panel').hidden) return close();
      openPanel();
      (tab === 'chats' ? (chatId ? openThread(chatId) : showChats()) : showAssistant());
    });
    $('pa-close').addEventListener('click', close);
    $('pa-tab-assistant').addEventListener('click', showAssistant);
    $('pa-tab-chats').addEventListener('click', showChats);
    $('pa-thread-back').addEventListener('click', showChats);
    $('pa-thread-toggle').addEventListener('click', async () => {
      const closing = $('pa-thread-toggle').textContent === 'Close chat';
      try { renderThread(await P.api(`/api/chats/${chatId}/${closing ? 'close' : 'reopen'}`, { method: 'POST' })); }
      catch (err) { P.toast(err.message, 'error'); }
    });
    $('pa-clear').addEventListener('click', () => { history = []; save(); if (tab === 'assistant') showAssistant(); });
    $('pa-chips').addEventListener('click', e => { const b = e.target.closest('button'); if (b && !busy) ask(b.textContent); });
    $('pa-form').addEventListener('submit', e => {
      e.preventDefault();
      const input = $('pa-input'), text = input.value.trim();
      if (!text || busy) return;
      input.value = '';
      (tab === 'chats' && chatId ? sendChat(text) : ask(text));
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('pa-panel').hidden) close(); });
    if (hasChats()) { refreshBadge(); listPoll = setInterval(refreshBadge, 30000); }
  }

  // Pages call this right after a booking so the chat with the pharmacy / lab opens by itself
  window.PyshioChat = {
    async open(id) {
      if (!id) return;
      for (let i = 0; i < 20 && !$('pa-panel'); i++) await new Promise(r => setTimeout(r, 150));
      if (!$('pa-panel')) return;
      openPanel();
      openThread(id);
    },
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
