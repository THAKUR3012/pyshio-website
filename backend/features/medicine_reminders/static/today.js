// "Today" for patients: doses by time of day with Taken / Skip, how regularly they're taken, and reminder settings.
// window.MedicineToday.mount(element)
(() => {
  const P = window.Pyshio;
  const { escapeHtml: esc } = P;
  const SLOT_ICON = { morning: '🌅', afternoon: '☀️', evening: '🌇', night: '🌙' };
  let root = null;
  let day = null;       // ISO date being shown
  let pingTimer = null;

  const iso = d => P.isoDate(d);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  function doseRow(d) {
    const done = d.status === 'taken', skipped = d.status === 'skipped';
    const key = `data-source="${d.source}" data-source-id="${esc(d.source_id)}" data-slot="${d.slot}"`;
    return `<li class="flex flex-wrap items-center gap-3 py-2.5">
      <span class="w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm ${done ? 'bg-emerald-100 text-emerald-700' : skipped ? 'bg-slate-100 text-slate-400' : 'bg-sky-50 text-sky-700'}" aria-hidden="true">${done ? '✓' : skipped ? '–' : '💊'}</span>
      <div class="flex-1 min-w-[8rem]">
        <p class="font-medium ${skipped ? 'text-slate-400 line-through' : 'text-slate-900'}">${esc(d.name)}${d.strength ? ` <span class="text-slate-500 font-normal">${esc(d.strength)}</span>` : ''}
          ${d.amount && d.amount !== '1' ? `<span class="text-xs text-slate-500">× ${esc(d.amount)}</span>` : ''}</p>
        <p class="text-xs text-slate-500">${d.for_name ? `<span class="inline-block px-1.5 rounded bg-violet-50 text-violet-700 mr-1">for ${esc(d.for_name)}</span>` : ''}${esc(d.instructions || d.dosage || '')}</p>
      </div>
      <div class="flex gap-2">
        <button type="button" ${key} data-mark="${done ? 'pending' : 'taken'}" class="px-3 py-1.5 rounded-lg text-sm font-semibold ${done ? 'bg-emerald-600 text-white' : 'border border-emerald-300 text-emerald-700 hover:bg-emerald-50'}" aria-pressed="${done}">${done ? 'Taken ✓' : 'Taken'}</button>
        <button type="button" ${key} data-mark="${skipped ? 'pending' : 'skipped'}" class="px-3 py-1.5 rounded-lg text-sm ${skipped ? 'bg-slate-600 text-white' : 'border border-slate-300 text-slate-600 hover:bg-slate-50'}" aria-pressed="${skipped}">${skipped ? 'Skipped' : 'Skip'}</button>
      </div>
    </li>`;
  }

  function scheduleHtml(s) {
    const today = iso(P.today());
    const minDay = iso(P.addDays(P.today(), -7));
    const pct = s.summary.due ? Math.round(100 * s.summary.taken / s.summary.due) : 0;
    const nav = `<div class="flex items-center gap-2 text-sm">
      <button type="button" data-day="${iso(P.addDays(P.fromIso(s.date), -1))}" ${s.date <= minDay ? 'disabled' : ''} class="px-2 py-1 rounded-md border border-slate-300 disabled:opacity-40" aria-label="Previous day">‹</button>
      <span class="font-medium text-slate-700 min-w-[6rem] text-center">${s.date === today ? 'Today' : esc(P.prettyDate(s.date))}</span>
      <button type="button" data-day="${iso(P.addDays(P.fromIso(s.date), 1))}" ${s.date >= today ? 'disabled' : ''} class="px-2 py-1 rounded-md border border-slate-300 disabled:opacity-40" aria-label="Next day">›</button></div>`;
    const groups = s.groups.length ? s.groups.map(g => {
      const pending = g.doses.filter(d => d.status === 'pending').length;
      return `<div class="rounded-xl border ${g.past && pending ? 'border-amber-300 bg-amber-50/40' : 'border-slate-200'} p-3">
        <div class="flex items-center justify-between">
          <p class="font-semibold text-slate-900">${SLOT_ICON[g.slot]} ${esc(g.label)} <span class="text-sm font-normal text-slate-500">· ${esc(P.prettyTime(g.time))}</span></p>
          ${g.past && pending ? `<span class="text-xs font-semibold text-amber-800">${pending} not marked yet</span>` : ''}
        </div>
        <ul class="divide-y divide-slate-100">${g.doses.map(doseRow).join('')}</ul></div>`;
    }).join('') : `<div class="rounded-xl border border-dashed border-slate-300 p-8 text-center text-slate-500">
        No daily medicines on ${s.date === today ? 'your list today' : 'this day'}.<br>
        Prescriptions from your doctor appear here automatically. <button type="button" data-goto-tab="records" class="text-sky-700 font-medium hover:underline">Add a medicine you already take</button></div>`;
    const asNeeded = s.as_needed.length ? `<div class="text-sm text-slate-600"><p class="font-medium text-slate-700 mb-1">As needed / not daily</p>
      <p>${s.as_needed.map(m => `${esc(m.name)} <span class="text-slate-400">(${esc(m.dosage || 'as directed')})</span>`).join(' · ')}</p></div>` : '';
    return `<div class="bg-white rounded-2xl border border-slate-200 p-5 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div><h2 class="text-lg font-semibold text-slate-900">Medicines</h2>
          <p class="text-sm text-slate-500">${s.summary.due ? `${s.summary.taken} of ${s.summary.due} doses taken${s.summary.skipped ? `, ${s.summary.skipped} skipped` : ''}` : 'Nothing due'}</p></div>
        ${nav}
      </div>
      ${s.summary.due ? `<div class="h-2 rounded-full bg-slate-100 overflow-hidden" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Doses taken"><div class="h-full bg-emerald-500 rounded-full" style="width:${pct}%"></div></div>` : ''}
      <div class="space-y-3">${groups}</div>
      ${asNeeded}
    </div>`;
  }

  function adherenceHtml(a) {
    if (!a.due) return `<div class="bg-white rounded-2xl border border-slate-200 p-5"><h2 class="font-semibold text-slate-900">How regularly you take them</h2>
      <p class="text-sm text-slate-500 mt-1">Mark doses as you take them and your record for the last 30 days shows here. Your doctor sees it too, which helps them adjust your treatment.</p></div>`;
    const tone = a.percent >= 80 ? 'text-emerald-700' : a.percent >= 50 ? 'text-amber-700' : 'text-red-700';
    return `<div class="bg-white rounded-2xl border border-slate-200 p-5 space-y-4">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div><h2 class="font-semibold text-slate-900">How regularly you take them</h2><p class="text-xs text-slate-500">Last ${a.days} days · doses you marked as taken</p></div>
        <p class="text-right"><span class="text-4xl font-bold tabular-nums ${tone}">${a.percent}%</span><span class="block text-xs text-slate-500">${a.taken.toLocaleString('en-IN')} of ${a.due.toLocaleString('en-IN')} doses</span></p>
      </div>
      <div><p class="text-sm font-medium text-slate-700 mb-1">Last 7 days</p><div class="h-36"><canvas id="adh-week" aria-label="Share of doses taken each of the last 7 days"></canvas></div></div>
      ${P.table([
        { label: 'Medicine', render: m => `${esc(m.name)}${m.strength ? ` <span class="text-slate-500">${esc(m.strength)}</span>` : ''}${m.for_name ? ` <span class="text-xs text-violet-700">for ${esc(m.for_name)}</span>` : ''}` },
        { label: 'Doses due', key: 'due', num: true },
        { label: 'Taken', key: 'taken', num: true },
        { label: 'Taken %', render: m => m.percent == null ? '—' : `${m.percent}%`, num: true },
      ], a.medicines)}
    </div>`;
  }

  function settingsHtml(r) {
    const canNotify = 'Notification' in window;
    const allowed = canNotify && Notification.permission === 'granted';
    return `<form id="rem-form" class="bg-white rounded-2xl border border-slate-200 p-5 space-y-3">
      <h2 class="font-semibold text-slate-900">Reminder times</h2>
      <div class="grid grid-cols-2 gap-3 text-sm">${r.slots.map(s => `<label class="block min-w-0 text-slate-600"><span class="whitespace-nowrap">${SLOT_ICON[s]} ${s[0].toUpperCase() + s.slice(1)}</span>
        <input type="time" name="${s}" value="${esc(r.times[s])}" required class="mt-1 block w-full min-w-0 px-2 py-2 border border-slate-300 rounded-lg" /></label>`).join('')}</div>
      <label class="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" name="email_digest" ${r.email_digest ? 'checked' : ''} /> Email me the day's medicines each morning</label>
      <div class="flex flex-wrap items-center justify-between gap-2">
        ${canNotify ? (allowed ? '<span class="text-xs text-emerald-700">✓ Pop-up reminders are on for this device</span>'
          : '<button type="button" id="rem-allow" class="text-sm text-sky-700 font-medium hover:underline">Turn on pop-up reminders on this device</button>') : '<span></span>'}
        <button class="px-4 py-2 bg-sky-600 text-white text-sm font-semibold rounded-lg hover:bg-sky-700">Save times</button>
      </div>
    </form>`;
  }

  async function render() {
    const [s, a, r] = await Promise.all([P.api(`/api/my/doses?date=${day}`), P.api('/api/my/adherence?days=30'), P.api('/api/my/reminders')]);
    root.innerHTML = `<div class="grid grid-cols-1 lg:grid-cols-5 gap-6"><div class="min-w-0 lg:col-span-3 space-y-6">${scheduleHtml(s)}</div>
      <div class="min-w-0 lg:col-span-2 space-y-6">${adherenceHtml(a)}${settingsHtml(r)}</div></div>`;
    if (a.due) {
      P.chart('adh-week', { type: 'bar', data: { labels: a.week.map(w => P.fromIso(w.date).toLocaleDateString('en-IN', { weekday: 'short' })),
        datasets: [{ label: 'Taken %', data: a.week.map(w => w.percent), backgroundColor: '#0284c7', borderRadius: 4, maxBarThickness: 28 }] },
        options: { scales: { y: { min: 0, max: 100, ticks: { callback: v => `${v}%` }, grid: { color: 'rgba(148,163,184,.25)' } }, x: { grid: { display: false } } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => {
            const w = a.week[c.dataIndex];
            return w.due ? `${w.taken} of ${w.due} doses (${w.percent}%)` : 'Nothing due';
          } } } } } });
    }
    startPings(s, r);
  }

  // While this page is open: a device pop-up when a dose time passes with doses still unmarked
  function startPings() {
    clearInterval(pingTimer);
    pingTimer = setInterval(async () => {
      if (!('Notification' in window) || Notification.permission !== 'granted' || day !== iso(P.today())) return;
      const s = await P.api(`/api/my/doses?date=${day}`).catch(() => null);
      if (!s) return;
      const now = new Date().toTimeString().slice(0, 5);
      for (const g of s.groups) {
        const pending = g.doses.filter(d => d.status === 'pending');
        const key = `pyshio-dose-${day}-${g.slot}`;
        if (pending.length && now >= g.time && !store.get(key)) {
          store.set(key, '1');
          const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration().catch(() => null);
          const opts = { body: pending.map(d => d.name).join(', '), icon: '/features/pwa/icon-192.png', tag: key, data: { link: '/account#today' } };
          try { reg ? reg.showNotification(`Time for your ${g.label.toLowerCase()} medicines`, opts) : new Notification(`Time for your ${g.label.toLowerCase()} medicines`, opts); } catch { /* unsupported */ }
        }
      }
    }, 60000);
  }

  async function mark(btn) {
    btn.disabled = true;
    try {
      await P.api('/api/my/doses', { method: 'POST', body: { source: btn.dataset.source, source_id: btn.dataset.sourceId,
        dose_date: day, slot: btn.dataset.slot, status: btn.dataset.mark } });
      await render();
    } catch (err) { P.toast(err.message, 'error'); btn.disabled = false; }
  }

  function wire() {
    root.addEventListener('click', e => {
      const m = e.target.closest('[data-mark]');
      if (m) return mark(m);
      const d = e.target.closest('[data-day]');
      if (d && !d.disabled) { day = d.dataset.day; return P.safe(render, root); }
      const t = e.target.closest('[data-goto-tab]');
      if (t) { location.hash = t.dataset.gotoTab; return; }
      if (e.target.id === 'rem-allow') {
        Notification.requestPermission().then(() => P.safe(render, root));
      }
    });
    root.addEventListener('submit', async e => {
      if (e.target.id !== 'rem-form') return;
      e.preventDefault();
      const f = e.target.elements;
      const times = {};
      ['morning', 'afternoon', 'evening', 'night'].forEach(s => { if (f[s]) times[s] = f[s].value; });
      try {
        await P.api('/api/my/reminders', { method: 'PUT', body: { times, email_digest: f.email_digest.checked } });
        P.toast('Reminder times saved', 'success');
        P.safe(render, root);
      } catch (err) { P.toast(err.message, 'error'); }
    });
  }

  window.MedicineToday = {
    mount(el) {
      if (root !== el) { root = el; wire(); }
      day = day || iso(P.today());
      return P.safe(render, root);
    },
  };
})();
