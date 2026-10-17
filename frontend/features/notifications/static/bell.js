// Notification bell in the top bar, for every role: unread count, latest updates, mark read, open the right page.
// Polls every 30 s while the tab is visible; new updates show a toast and (if allowed) a device notification.
(() => {
  const P = window.Pyshio;
  const POLL_MS = 30000;
  const ICONS = { appointment: '📅', order: '💊', lab: '🧪', quote: '🏷️', prescription: '📝', reminder: '⏰',
    account: '👤', update: '🔔' };
  let state = { unread: 0, items: [] };
  let timer = null;
  let lastSeenId = null;
  let open = false;

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const h = Math.round(mins / 60);
    if (h < 24) return `${h} h ago`;
    const d = Math.round(h / 24);
    return d === 1 ? 'yesterday' : `${d} days ago`;
  }

  function render() {
    const slot = document.getElementById('nav-bell');
    if (!slot) return;
    const n = state.unread;
    slot.innerHTML = `<div class="relative">
      <button type="button" id="bell-btn" class="relative p-2 rounded-lg text-slate-600 hover:bg-slate-100 hover:text-sky-700"
        aria-label="Notifications${n ? `, ${n} unread` : ''}" aria-haspopup="true" aria-expanded="${open}">
        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 00-4-5.7V5a2 2 0 10-4 0v.3C7.7 6.2 6 8.4 6 11v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/></svg>
        ${n ? `<span class="absolute -top-0.5 -right-0.5 min-w-[1.25rem] h-5 px-1 rounded-full bg-red-600 text-white text-[11px] font-bold flex items-center justify-center">${n > 99 ? '99+' : n}</span>` : ''}
      </button>
      ${open ? panel() : ''}
    </div>`;
    slot.querySelector('#bell-btn').addEventListener('click', e => { e.stopPropagation(); toggle(); });
    if (open) wirePanel(slot);
  }

  function panel() {
    const canAsk = 'Notification' in window && Notification.permission === 'default';
    const items = state.items.length ? state.items.map(i => `
      <li><button type="button" data-id="${i.id}" data-link="${P.escapeHtml(i.link || '')}"
          class="w-full text-left px-4 py-3 flex gap-3 hover:bg-slate-50 ${i.read ? '' : 'bg-sky-50/60'}">
        <span class="text-lg leading-none mt-0.5" aria-hidden="true">${ICONS[i.kind] || ICONS.update}</span>
        <span class="min-w-0 flex-1">
          <span class="block text-sm ${i.read ? 'text-slate-700' : 'font-semibold text-slate-900'}">${P.escapeHtml(i.title)}</span>
          ${i.body ? `<span class="block text-xs text-slate-500 mt-0.5 line-clamp-2">${P.escapeHtml(i.body)}</span>` : ''}
          <span class="block text-[11px] text-slate-400 mt-1">${ago(i.created_at)}</span>
        </span>
        ${i.read ? '' : '<span class="w-2 h-2 rounded-full bg-sky-600 mt-2 shrink-0" aria-label="unread"></span>'}
      </button></li>`).join('')
      : '<li class="px-4 py-10 text-center text-sm text-slate-500">No notifications yet.<br>Updates about your bookings, orders and reports appear here.</li>';
    return `<div id="bell-panel" role="dialog" aria-label="Notifications"
        class="fixed sm:absolute inset-x-2 sm:inset-x-auto sm:right-0 top-16 sm:top-12 sm:w-96 bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden z-50">
      <div class="flex items-center justify-between px-4 py-3 border-b border-slate-100">
        <p class="font-semibold text-slate-900">Notifications</p>
        <button type="button" id="bell-all" class="text-xs font-medium text-sky-700 hover:underline ${state.unread ? '' : 'invisible'}">Mark all read</button>
      </div>
      <ul class="max-h-[60vh] overflow-y-auto divide-y divide-slate-100">${items}</ul>
      ${canAsk ? `<div class="px-4 py-3 border-t border-slate-100 bg-slate-50 text-xs text-slate-600 flex items-center justify-between gap-2">
        <span>Get a pop-up on this device for new updates</span>
        <button type="button" id="bell-allow" class="px-2.5 py-1 rounded-md bg-sky-600 text-white font-semibold">Turn on</button></div>` : ''}
    </div>`;
  }

  function wirePanel(slot) {
    const p = slot.querySelector('#bell-panel');
    p.addEventListener('click', e => e.stopPropagation());
    p.querySelectorAll('button[data-id]').forEach(b => b.addEventListener('click', async () => {
      const id = Number(b.dataset.id);
      const item = state.items.find(i => i.id === id);
      if (item && !item.read) {
        await P.api('/api/notifications/read', { method: 'POST', body: { ids: [id] } }).catch(() => {});
      }
      const link = b.dataset.link;
      if (link && link.startsWith('/')) {
        const here = location.pathname + location.hash;
        location.href = link;
        if (link.split('#')[0] === location.pathname && link !== here) location.reload();
      } else { refresh(); }
    }));
    const all = p.querySelector('#bell-all');
    if (all) all.addEventListener('click', async () => {
      await P.api('/api/notifications/read', { method: 'POST', body: { all: true } }).catch(() => {});
      refresh();
    });
    const allow = p.querySelector('#bell-allow');
    if (allow) allow.addEventListener('click', async () => {
      await Notification.requestPermission().catch(() => {});
      render();
    });
  }

  function toggle() {
    open = !open;
    render();
    if (open && state.unread === 0) refresh();
  }

  async function popUp(item) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const options = { body: item.body || '', icon: '/features/pwa/icon-192.png', tag: `pyshio-${item.id}`, data: { link: item.link } };
    try {
      const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
      if (reg) reg.showNotification(item.title, options);
      else new Notification(item.title, options);
    } catch { /* not supported */ }
  }

  async function refresh() {
    let data;
    try { data = await P.api('/api/notifications?limit=30'); } catch { return; }
    const newest = data.items.length ? data.items[0].id : 0;
    if (lastSeenId !== null && newest > lastSeenId) {
      const fresh = data.items.filter(i => i.id > lastSeenId && !i.read).reverse();
      fresh.slice(-3).forEach(i => { P.toast(`${ICONS[i.kind] || '🔔'} ${i.title}`); popUp(i); });
    }
    lastSeenId = Math.max(newest, lastSeenId || 0);
    store.set('pyshio-bell-last', String(lastSeenId));
    state = data;
    render();
  }

  function start(user) {
    if (!user) { clearInterval(timer); timer = null; return; }
    const saved = Number(store.get('pyshio-bell-last'));
    if (lastSeenId === null && saved) lastSeenId = saved;
    render();
    if (!timer) {
      refresh();
      timer = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
      document.addEventListener('click', () => { if (open) { open = false; render(); } });
      document.addEventListener('keydown', e => { if (e.key === 'Escape' && open) { open = false; render(); } });
    }
  }

  document.addEventListener('pyshio:nav', e => start(e.detail && e.detail.user));
  // The bar may already be drawn by the time this script loads
  P.me().then(d => { if (document.getElementById('nav-bell')) start(d.user); });
  window.PyshioBell = { refresh };
})();
