// Shared helpers for all Pyshio pages.
window.Pyshio = (() => {
  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  async function api(url, options = {}) {
    const res = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      body: options.body && typeof options.body !== 'string' ? JSON.stringify(options.body) : options.body,
    });
    if (options.raw) {
      if (!res.ok) throw new Error('Request failed');
      return res;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      const message = typeof detail === 'string' ? detail : (detail && typeof detail.message === 'string' ? detail.message : null);
      const err = new Error(message || 'Something went wrong. Please try again.');
      err.status = res.status;
      err.detail = detail;
      throw err;
    }
    return data;
  }

  // Multipart upload (files); the browser sets the boundary header itself
  async function upload(url, formData, method = 'POST') {
    const res = await fetch(url, { method, body: formData, credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(typeof data.detail === 'string' ? data.detail : 'Upload failed. Please try again.');
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------- Dates & money ----------
  const pad = n => String(n).padStart(2, '0');
  // Local calendar date (toISOString() would shift to UTC and change the day in India)
  const isoDate = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const fromIso = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const today = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const prettyDate = s => fromIso(s).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  const longDate = d => d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  const prettyTime = t => { const [h, m] = t.split(':').map(Number); return `${((h + 11) % 12) + 1}:${pad(m)} ${h < 12 ? 'AM' : 'PM'}`; };
  const startOf = a => new Date(`${a.appointment_date}T${a.appointment_time}`);
  const rupees = n => '₹' + Number(n || 0).toLocaleString('en-IN');
  const num = n => Number(n || 0).toLocaleString('en-IN');

  const STATUS = {
    pending_payment: ['Awaiting payment', 'bg-amber-100 text-amber-800'],
    requested: ['Home visit requested', 'bg-amber-100 text-amber-800'],
    awaiting_patient: ['Charges sent · answer needed', 'bg-violet-100 text-violet-800'],
    confirmed: ['Confirmed', 'bg-sky-100 text-sky-800'],
    completed: ['Completed', 'bg-emerald-100 text-emerald-800'],
    no_show: ['No-show', 'bg-orange-100 text-orange-800'],
    cancelled: ['Cancelled', 'bg-slate-200 text-slate-600'],
    expired: ['Expired', 'bg-slate-200 text-slate-600'],
  };
  const PAYMENT = {
    unpaid: 'Pay at clinic', pending: 'Payment pending', paid: 'Paid', refunded: 'Refunded',
    refund_failed: 'Refund failed', not_refundable: 'Paid (non-refundable)',
  };
  const badge = s => { const [label, cls] = STATUS[s] || [s, 'bg-slate-100']; return `<span class="px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${cls}">${escapeHtml(label)}</span>`; };
  const paymentLabel = a => a.visit_type === 'video' && a.payment_method === 'clinic'
    ? (a.payment_status === 'paid' ? 'Paid' : 'Pay the doctor after the consult')
    : a.visit_type === 'home' && a.payment_method === 'clinic'
    ? (a.payment_status === 'paid' ? 'Paid at the visit' : 'Pay the doctor at the visit')
    : a.payment_method === 'clinic' && a.payment_status === 'paid' ? 'Paid at clinic' : (PAYMENT[a.payment_status] || a.payment_status);

  // ---------- Session & navigation ----------
  let mePromise;
  const me = (refresh) => (mePromise = (!refresh && mePromise) || api('/api/auth/me').catch(() => ({ user: null })));

  async function logout() {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
    location.href = '/';
  }

  const DASHBOARD = { patient: ['/account', 'My health'], doctor: ['/doctor', 'Doctor dashboard'],
    pharmacy: ['/pharmacy', 'Pharmacy dashboard'], admin: ['/admin', 'Admin'],
    company: ['/company', 'Company dashboard'], mr: ['/mr', 'MR dashboard'], lab: ['/lab', 'Lab dashboard'] };

  async function renderNav() {
    const el = $('site-nav');
    if (!el) return;
    const { user } = await me();
    const right = `<span id="nav-install"></span>` + (user
      ? `<span id="nav-bell"></span>
         <a href="${DASHBOARD[user.role][0]}" class="text-sm font-medium text-slate-700 hover:text-sky-700">${DASHBOARD[user.role][1]}</a>
         <span class="hidden sm:inline text-sm text-slate-500">${escapeHtml(user.name)}</span>
         <button id="nav-logout" class="text-sm px-3 py-1.5 border border-slate-300 rounded-lg hover:bg-slate-100">Log out</button>`
      : `<a href="/doctor" class="hidden md:inline text-sm font-medium text-slate-600 hover:text-sky-700">For doctors</a>
         <a href="/pharmacy" class="hidden md:inline text-sm font-medium text-slate-600 hover:text-sky-700">For pharmacies</a>
         <a href="/lab" class="hidden lg:inline text-sm font-medium text-slate-600 hover:text-sky-700">For labs</a>
         <a href="/company" class="hidden lg:inline text-sm font-medium text-slate-600 hover:text-sky-700">For pharma</a>
         <a href="/login?next=${encodeURIComponent(location.pathname + location.search)}" class="text-sm px-4 py-2 bg-sky-600 text-white font-semibold rounded-lg hover:bg-sky-700">Log in</a>`);
    el.innerHTML = `
      <nav class="bg-white shadow-sm sticky top-0 z-40">
        <div class="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <a href="/" class="flex items-center gap-2 shrink-0">
            <span class="w-9 h-9 rounded-lg bg-sky-600 flex items-center justify-center">
              <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"/></svg>
            </span>
            <span class="text-xl font-bold text-sky-800">Pyshio</span>
          </a>
          <div class="flex items-center gap-3 sm:gap-5">${right}</div>
        </div>
      </nav>`;
    const btn = $('nav-logout');
    if (btn) btn.addEventListener('click', logout);
    // Slices that live in the top bar (bell, install button) re-attach after every render
    document.dispatchEvent(new CustomEvent('pyshio:nav', { detail: { user } }));
  }

  // ---------- Slice scripts used on every page ----------
  const loadedScripts = {};
  function loadScript(src) {
    loadedScripts[src] = loadedScripts[src] || new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src; s.defer = true; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
    return loadedScripts[src];
  }
  const EVERY_PAGE = ['/features/pwa/pwa.js', '/features/notifications/bell.js'];

  // Redirect to login unless logged in with one of `roles`. Resolves to the /me payload.
  async function requireRole(...roles) {
    const data = await me();
    if (!data.user) {
      location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
      return new Promise(() => {});
    }
    if (roles.length && !roles.includes(data.user.role)) {
      location.href = DASHBOARD[data.user.role][0];
      return new Promise(() => {});
    }
    return data;
  }

  // ---------- UI bits ----------
  // Toasts stack in one live region so screen readers announce them and they never overlap
  function toast(message, kind = 'info') {
    const colours = { info: 'bg-slate-800', success: 'bg-emerald-600', error: 'bg-red-600' };
    let region = $('toast-region');
    if (!region) {
      region = document.createElement('div');
      region.id = 'toast-region';
      region.setAttribute('role', 'status');
      region.setAttribute('aria-live', 'polite');
      region.className = 'fixed bottom-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 w-max max-w-[90vw]';
      document.body.appendChild(region);
    }
    const el = document.createElement('div');
    el.className = `px-4 py-3 rounded-lg text-white text-sm shadow-lg ${colours[kind]}`;
    if (kind === 'error') el.setAttribute('role', 'alert');
    el.textContent = message;
    region.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  function setMessage(el, text, kind) {
    const styles = { error: 'text-red-600', success: 'text-emerald-700', info: 'text-slate-600' };
    el.className = `text-sm mt-2 ${styles[kind] || ''}`;
    el.textContent = text;
    el.classList.toggle('hidden', !text);
  }

  // Modal helper: <div id="x" class="modal hidden">…<button data-close>…</div>
  // Accessible: dialog role, focus moves in and returns, Escape closes, Tab stays inside, nested modals stack.
  const modalStack = [];
  function openModal(id) {
    const m = $(id);
    if (!m.hasAttribute('role')) {
      m.setAttribute('role', 'dialog');
      m.setAttribute('aria-modal', 'true');
      const title = m.querySelector('h2');
      if (title) { title.id = title.id || `${id}-title`; m.setAttribute('aria-labelledby', title.id); }
    }
    m.classList.remove('hidden');
    modalStack.push({ id, returnTo: document.activeElement });
    document.body.classList.add('overflow-hidden');
    const first = m.querySelector('input:not([type=hidden]), select, textarea, button:not([data-close])') || m.querySelector('button');
    if (first) setTimeout(() => first.focus(), 30);
  }
  function closeModal(id) {
    $(id).classList.add('hidden');
    const i = modalStack.map(x => x.id).lastIndexOf(id);
    const entry = i >= 0 ? modalStack.splice(i, 1)[0] : null;
    if (!modalStack.length) document.body.classList.remove('overflow-hidden');
    if (entry && entry.returnTo && document.contains(entry.returnTo)) entry.returnTo.focus();
  }
  document.addEventListener('click', e => {
    const closer = e.target.closest('[data-close]');
    if (closer) closeModal(closer.closest('.modal').id);
    else if (e.target.classList && e.target.classList.contains('modal')) closeModal(e.target.id);
  });
  document.addEventListener('keydown', e => {
    const top = modalStack[modalStack.length - 1];
    if (!top) return;
    if (e.key === 'Escape') { e.preventDefault(); closeModal(top.id); return; }
    if (e.key === 'Tab') {
      const items = [...$(top.id).querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select, textarea')]
        .filter(el => el.offsetParent !== null);
      if (!items.length) return;
      const [first, last] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  // Tabs: expose which one is selected (pages mark the active tab with bg-white)
  function syncTab(t) { t.setAttribute('aria-selected', t.classList.contains('bg-white') ? 'true' : 'false'); }
  function enhancePage(config) {
    document.querySelectorAll('.tab').forEach(t => {
      if (t.dataset.a11y) return;
      t.dataset.a11y = '1';
      t.setAttribute('role', 'tab');
      if (t.parentElement) t.parentElement.setAttribute('role', 'tablist');
      syncTab(t);
      new MutationObserver(() => syncTab(t)).observe(t, { attributes: true, attributeFilter: ['class'] });
    });
    // Inputs that only have a placeholder get an accessible name
    document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach(el => {
      if (!el.closest('label') && !el.getAttribute('aria-label') && !(el.id && document.querySelector(`label[for="${el.id}"]`)))
        el.setAttribute('aria-label', el.placeholder);
    });
    // Text that depends on settings (search radius, platform name)
    if (config) {
      document.querySelectorAll('[data-config]').forEach(el => {
        const v = config[el.dataset.config];
        if (v !== undefined && v !== null) el.textContent = v;
      });
    }
  }
  let configPromise;
  const siteConfig = () => (configPromise = configPromise || api('/api/config').catch(() => null));
  document.addEventListener('DOMContentLoaded', () => {
    enhancePage(null);
    siteConfig().then(c => enhancePage(c));
  });

  function messagesHtml(messages) {
    if (!messages.length) return '<p class="text-sm text-slate-500">No messages yet.</p>';
    return messages.map(m => `
      <div class="rounded-lg px-3 py-2 text-sm ${m.author_role === 'doctor' ? 'bg-sky-50 border border-sky-100' : 'bg-slate-50 border border-slate-200'}">
        <p class="text-xs text-slate-500 mb-1">${escapeHtml(m.author_name)} · ${escapeHtml(new Date(m.created_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }))}</p>
        <p class="whitespace-pre-wrap text-slate-800">${escapeHtml(m.body)}</p>
      </div>`).join('');
  }

  // Fill a <select> with free times for rescheduling
  async function loadRescheduleTimes(appt, dateStr, select) {
    select.innerHTML = '<option value="">Loading…</option>';
    try {
      const params = new URLSearchParams({ date: dateStr, service_id: appt.service_id, exclude: appt.id });
      const data = await api(`/api/doctors/${appt.doctor_slug}/slots?${params}`);
      select.innerHTML = data.slots.length
        ? '<option value="">Choose time</option>' + data.slots.map(t => `<option value="${t}">${prettyTime(t)}</option>`).join('')
        : `<option value="">${data.open ? 'No free times' : 'Not available'}</option>`;
    } catch (err) {
      select.innerHTML = `<option value="">${escapeHtml(err.message)}</option>`;
    }
  }

  // Razorpay Checkout. Resolves with the server's verify result, rejects if dismissed/failed.
  let razorpayLoaded;
  function loadRazorpay() {
    razorpayLoaded = razorpayLoaded || new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://checkout.razorpay.com/v1/checkout.js';
      s.onload = resolve;
      s.onerror = () => { razorpayLoaded = null; s.remove(); reject(new Error('Could not load the payment window. Check your connection and try again.')); };
      document.head.appendChild(s);
    });
    return razorpayLoaded;
  }

  async function payOnline(id, checkout, kind = "appointments") {
    await loadRazorpay();
    return new Promise((resolve, reject) => {
      const rzp = new window.Razorpay({
        key: checkout.key_id, order_id: checkout.order_id, amount: checkout.amount, currency: checkout.currency,
        name: checkout.name, description: checkout.description, prefill: checkout.prefill,
        theme: { color: '#0284c7' },
        handler: async (response) => {
          try {
            resolve(await api(`/api/${kind}/${id}/verify-payment`, { method: "POST", body: response }));
          } catch (err) { reject(err); }
        },
        modal: { ondismiss: () => reject(new Error(`Payment not completed. Your slot is held for ${checkout.hold_minutes} minutes; you can pay from My appointments.`)) },
      });
      rzp.on('payment.failed', r => toast(r.error && r.error.description ? r.error.description : 'Payment failed', 'error'));
      rzp.open();
    });
  }

  // ---------- Money in paise (medicines) & order status ----------
  const money = p => {
    p = Number(p || 0);
    const whole = '₹' + Math.floor(p / 100).toLocaleString('en-IN');
    return p % 100 ? `${whole}.${String(p % 100).padStart(2, '0')}` : whole;
  };
  const ORDER = {
    pending_payment: ['Awaiting payment', 'bg-amber-100 text-amber-800'], placed: ['Placed', 'bg-sky-100 text-sky-800'],
    accepted: ['Accepted', 'bg-indigo-100 text-indigo-800'], ready: ['Ready for pickup', 'bg-emerald-100 text-emerald-800'],
    out_for_delivery: ['Out for delivery', 'bg-emerald-100 text-emerald-800'], completed: ['Completed', 'bg-emerald-600 text-white'],
    rejected: ['Rejected', 'bg-red-100 text-red-700'], cancelled: ['Cancelled', 'bg-slate-200 text-slate-600'],
    expired: ['Expired', 'bg-slate-200 text-slate-600'],
  };
  const orderBadge = s => { const [l, c] = ORDER[s] || [s, 'bg-slate-100']; return `<span class="px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${c}">${escapeHtml(l)}</span>`; };
  const km = d => d == null ? '' : `${Number(d).toLocaleString('en-IN', { maximumFractionDigits: 1 })} km`;

  // ---------- Location: browser GPS + OpenStreetMap picker ----------
  function browserLocation() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(new Error('Location is not available in this browser'));
      navigator.geolocation.getCurrentPosition(
        p => resolve({ lat: +p.coords.latitude.toFixed(6), lng: +p.coords.longitude.toFixed(6) }),
        () => reject(new Error('Location permission denied. Search for your area instead.')),
        { enableHighAccuracy: true, timeout: 10000 });
    });
  }

  let leafletLoaded;
  function loadLeaflet() {
    leafletLoaded = leafletLoaded || new Promise((resolve, reject) => {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css';
      document.head.appendChild(css);
      const js = document.createElement('script');
      js.src = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js';
      js.onload = () => {
        // Leaflet guesses its marker image folder from the page URL; point it at the CDN instead
        L.Icon.Default.imagePath = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/';
        resolve();
      };
      js.onerror = () => { leafletLoaded = null; js.remove(); reject(new Error('Could not load the map. Check your connection and try again.')); };
      document.head.appendChild(js);
    });
    return leafletLoaded;
  }

  // Free address search (OpenStreetMap Nominatim; fine for low volume, see DEPLOY guide for heavy use)
  async function searchPlaces(q) {
    const url = `https://nominatim.openstreetmap.org/search?format=json&limit=5&countrycodes=in&q=${encodeURIComponent(q)}`;
    const res = await fetch(url, { headers: { 'Accept-Language': 'en' } });
    if (!res.ok) throw new Error('Address search is busy, try again in a moment');
    return (await res.json()).map(r => ({ lat: +(+r.lat).toFixed(6), lng: +(+r.lon).toFixed(6), label: r.display_name }));
  }

  // City and state for a map point (OpenStreetMap). Resolves to {} when unknown or offline.
  async function reverseGeocode(lat, lng) {
    try {
      const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&zoom=10&lat=${lat}&lon=${lng}`,
        { headers: { 'Accept-Language': 'en' } });
      if (!res.ok) return {};
      const a = (await res.json()).address || {};
      return { city: a.city || a.town || a.city_district || a.county || a.village || '', state: a.state || a.territory || '' };
    } catch (_) { return {}; }
  }

  // Fill a city input and a state <select> after the map pin moves (people can still edit them)
  async function fillCityState(point, cityInput, stateSelect) {
    if (!point) return;
    const found = await reverseGeocode(point.lat, point.lng);
    if (found.city && cityInput && !cityInput.dataset.touched) cityInput.value = found.city;
    if (found.state && stateSelect) {
      const match = [...stateSelect.options].find(o => o.value.toLowerCase() === found.state.toLowerCase().replace('&', 'and'));
      if (match && !stateSelect.dataset.touched) stateSelect.value = match.value;
    }
  }

  function stateOptions(states, selected, blank = 'Choose state') {
    return `<option value="">${escapeHtml(blank)}</option>` + (states || []).map(s =>
      `<option value="${escapeHtml(s)}" ${s === selected ? 'selected' : ''}>${escapeHtml(s)}</option>`).join('');
  }

  // Renders a location picker into `el`. Returns { value(): {lat,lng,label}|null, set(lat,lng) }.
  async function locationPicker(el, initial = {}, onChange = () => {}) {
    const id = 'lp' + Math.random().toString(36).slice(2, 8);
    el.innerHTML = `
      <div class="flex flex-wrap gap-2 mb-2">
        <input id="${id}-q" placeholder="Search area, street or landmark" class="flex-1 min-w-[12rem] px-3 py-2 border border-slate-300 rounded-lg text-sm" />
        <button type="button" id="${id}-search" class="px-3 py-2 border border-slate-300 rounded-lg text-sm hover:bg-slate-50">Search</button>
        <button type="button" id="${id}-gps" class="px-3 py-2 bg-slate-800 text-white rounded-lg text-sm">📍 Use my location</button>
      </div>
      <div id="${id}-results" class="text-sm space-y-1 mb-2"></div>
      <div id="${id}-map" class="h-64 rounded-lg border border-slate-300 z-0"></div>
      <p id="${id}-hint" class="text-xs text-slate-500 mt-1">Click the map or drag the pin to the exact spot.</p>`;
    await loadLeaflet();
    const L = window.L;
    let point = initial.lat != null ? { lat: initial.lat, lng: initial.lng, label: initial.label || '' } : null;
    const map = L.map(`${id}-map`).setView(point ? [point.lat, point.lng] : [20.59, 78.96], point ? 16 : 5);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
    let marker = null;
    function set(lat, lng, label, zoom) {
      point = { lat: +(+lat).toFixed(6), lng: +(+lng).toFixed(6), label: label || '' };
      if (!marker) {
        marker = L.marker([point.lat, point.lng], { draggable: true }).addTo(map);
        marker.on('dragend', () => { const p = marker.getLatLng(); set(p.lat, p.lng); });
      } else marker.setLatLng([point.lat, point.lng]);
      if (zoom) map.setView([point.lat, point.lng], zoom);
      $(`${id}-hint`).textContent = `Pinned at ${point.lat}, ${point.lng}. Drag the pin to adjust.`;
      onChange(point);
    }
    if (point) set(point.lat, point.lng, point.label);
    map.on('click', e => set(e.latlng.lat, e.latlng.lng));
    $(`${id}-gps`).addEventListener('click', async () => {
      try { const p = await browserLocation(); set(p.lat, p.lng, '', 17); }
      catch (err) { toast(err.message, 'error'); }
    });
    const doSearch = async () => {
      const q = $(`${id}-q`).value.trim();
      if (!q) return;
      try {
        const results = await searchPlaces(q);
        $(`${id}-results`).innerHTML = results.length ? results.map((r, i) =>
          `<button type="button" data-i="${i}" class="block w-full text-left px-2 py-1 rounded hover:bg-sky-50 truncate">${escapeHtml(r.label)}</button>`).join('')
          : '<p class="text-slate-500">No matches. Try a nearby landmark.</p>';
        $(`${id}-results`).onclick = e => {
          const b = e.target.closest('button[data-i]');
          if (!b) return;
          const r = results[b.dataset.i];
          set(r.lat, r.lng, r.label, 17);
          $(`${id}-results`).innerHTML = '';
        };
      } catch (err) { toast(err.message, 'error'); }
    };
    $(`${id}-search`).addEventListener('click', doSearch);
    $(`${id}-q`).addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } });
    setTimeout(() => map.invalidateSize(), 200);  // maps inside modals/tabs need a nudge
    return { value: () => point, set: (lat, lng) => set(lat, lng, '', 16), map };
  }

  // ---------- Tables (house style: Sr No first, bold centred headers, numbers right, text left) ----------
  // columns: [{ label, key?, render?(row), num? }]. Counts hidden for privacy arrive as "<5" and stay right-aligned.
  function table(columns, rows, { empty = 'Nothing to show yet.', footer = '' } = {}) {
    if (!rows || !rows.length) return `<p class="text-sm text-slate-500 py-3">${escapeHtml(empty)}</p>`;
    const head = ['<th class="px-3 py-2 text-center font-bold whitespace-nowrap">Sr No</th>']
      .concat(columns.map(c => `<th class="px-3 py-2 text-center font-bold whitespace-nowrap">${escapeHtml(c.label)}</th>`)).join('');
    const body = rows.map((r, i) => '<tr class="border-t border-slate-100 hover:bg-slate-50">' +
      `<td class="px-3 py-2 text-right tabular-nums">${i + 1}</td>` +
      columns.map(c => {
        const value = c.render ? c.render(r, i) : escapeHtml(c.num ? count(r[c.key]) : (r[c.key] ?? '—'));
        return `<td class="px-3 py-2 ${c.num ? 'text-right tabular-nums whitespace-nowrap' : 'text-left'}">${value}</td>`;
      }).join('') + '</tr>').join('');
    return `<div class="overflow-x-auto"><table class="w-full text-sm"><thead class="bg-slate-50 text-slate-700"><tr>${head}</tr></thead><tbody>${body}</tbody>${footer}</table></div>`;
  }
  // Numbers in Indian grouping; privacy-masked values ("<5") and blanks pass through
  const count = v => v == null || v === '' ? '—' : (typeof v === 'number' ? v.toLocaleString('en-IN') : String(v));
  const numeric = v => typeof v === 'number' ? v : 0;

  // ---------- Charts (Chart.js from cdnjs, loaded on first use) ----------
  let chartLoaded;
  const charts = {};
  function loadChart() {
    chartLoaded = chartLoaded || new Promise((resolve, reject) => {
      const js = document.createElement('script');
      js.src = 'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js';
      js.onload = resolve;
      js.onerror = () => { chartLoaded = null; js.remove(); reject(new Error('Could not load charts. Tables below still show every figure.')); };
      document.head.appendChild(js);
    });
    return chartLoaded;
  }
  const PALETTE = ['#0284c7', '#059669', '#d97706', '#7c3aed', '#db2777', '#0891b2', '#65a30d', '#dc2626', '#475569', '#ca8a04'];
  async function chart(canvasId, config) {
    try { await loadChart(); } catch (err) { const c = $(canvasId); if (c) c.replaceWith(Object.assign(document.createElement('p'), { className: 'text-sm text-slate-500', textContent: err.message })); return; }
    if (charts[canvasId]) charts[canvasId].destroy();
    const canvas = $(canvasId);
    if (!canvas) return;
    config.options = Object.assign({ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: (config.data.datasets || []).length > 1 } } }, config.options || {});
    charts[canvasId] = new window.Chart(canvas, config);
  }

  // ---------- Errors: never leave a blank page ----------
  function errorBox(el, err, retry) {
    const inner = `<div role="alert" class="rounded-lg border border-red-200 bg-red-50 text-red-700 text-sm p-4 flex flex-wrap items-center justify-between gap-3">
      <span>${escapeHtml(err.message || 'Something went wrong')}</span>
      ${retry ? '<button type="button" class="retry px-3 py-1 rounded bg-red-600 text-white text-xs font-semibold">Retry</button>' : ''}</div>`;
    if (el.tagName === 'TBODY') {
      const cols = (el.closest('table').querySelectorAll('thead th').length) || 1;
      el.innerHTML = `<tr><td colspan="${cols}" class="p-3">${inner}</td></tr>`;
    } else {
      el.innerHTML = inner;
    }
    const btn = el.querySelector('.retry');
    if (btn) btn.addEventListener('click', retry);
  }

  // Run fn; on failure show the error (with Retry) inside `el`, or as a toast if no element is given.
  async function safe(fn, el) {
    try { return await fn(); }
    catch (err) {
      if (el) errorBox(typeof el === 'string' ? $(el) : el, err, () => safe(fn, el));
      else toast(err.message || 'Something went wrong', 'error');
    }
  }
  // Wrap a loader function so every call is protected: loadX = P.guard(loadX, 'x-body')
  const guard = (fn, el) => (...args) => safe(() => fn(...args), el);

  // A page that can't start at all gets a clear message instead of an empty screen
  function fatal(err) {
    const main = document.querySelector('main') || document.body;
    const box = document.createElement('div');
    box.className = 'max-w-xl mx-auto my-6 px-4';
    main.prepend(box);
    errorBox(box, err, () => location.reload());
  }

  // Last line of defence for anything not wrapped above
  window.addEventListener('unhandledrejection', e => {
    const msg = e.reason && e.reason.message;
    if (msg) toast(msg, 'error');
  });

  document.addEventListener('DOMContentLoaded', () => {
    EVERY_PAGE.forEach(src => loadScript(src).catch(() => {}));
    renderNav();
  });

  return {
    $, api, escapeHtml, isoDate, fromIso, today, addDays, prettyDate, longDate, prettyTime, startOf, rupees, num,
    badge, paymentLabel, me, requireRole, logout, toast, setMessage, openModal, closeModal, messagesHtml,
    loadRescheduleTimes, payOnline, STATUS, money, orderBadge, km, browserLocation, locationPicker, ORDER,
    safe, guard, fatal, errorBox, renderNav, enhancePage, siteConfig,
    upload, reverseGeocode, fillCityState, stateOptions, table, count, numeric, chart, PALETTE, DASHBOARD, loadScript,
  };
})();
