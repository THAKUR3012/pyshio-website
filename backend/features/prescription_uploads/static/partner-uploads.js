// "Uploaded prescriptions" panel shared by the pharmacy and lab dashboards:
// open the patient's prescription image, write a quotation line by line, then work the booking.
window.PartnerUploads = (() => {
  const P = window.Pyshio;
  const { $, api, escapeHtml } = P;
  let root, role, requests = [], orders = [], orderStatus = 'open';

  const STATUS = { placed: ['New', 'bg-red-100 text-red-700'], accepted: ['Accepted', 'bg-indigo-100 text-indigo-800'],
    ready: ['Ready · waiting for code', 'bg-emerald-100 text-emerald-800'], sample_collected: ['Sample collected', 'bg-amber-100 text-amber-800'],
    report_ready: ['Report sent', 'bg-emerald-100 text-emerald-800'], completed: ['Completed', 'bg-emerald-600 text-white'],
    rejected: ['Rejected', 'bg-slate-200 text-slate-600'], cancelled: ['Cancelled', 'bg-slate-200 text-slate-600'] };
  const HOW = { pickup: 'Pickup', delivery: 'Home delivery', visit: 'Walk-in', home: 'Home collection' };
  const pill = s => { const [l, c] = STATUS[s] || [s, 'bg-slate-100']; return `<span class="px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${c}">${escapeHtml(l)}</span>`; };

  function lineRow(l = {}) {
    const hint = l.hint ? `<p class="col-span-12 text-[11px] ${l.check ? 'text-amber-700' : 'text-slate-500'}">${escapeHtml(l.hint)}</p>` : '';
    return `<div class="u-line grid grid-cols-12 gap-2 items-end" data-medicine-id="${l.medicine_id || ''}">${hint}
      <label class="col-span-12 sm:col-span-5 text-xs text-slate-600">${role === 'lab' ? 'Test' : 'Medicine'}<input class="u-name field" value="${escapeHtml(l.name || '')}" placeholder="${role === 'lab' ? 'e.g. HbA1c' : 'e.g. Dolo 650'}" /></label>
      <label class="col-span-3 sm:col-span-1 text-xs text-slate-600">Qty<input class="u-qty field" type="number" min="1" max="1000" value="${l.quantity || 1}" /></label>
      <label class="col-span-4 sm:col-span-2 text-xs text-slate-600">${role === 'lab' ? 'Price' : 'Unit price'} (₹)<input class="u-price field" type="number" min="0" step="0.01" value="${l.unit_price_paise ? l.unit_price_paise / 100 : ''}" /></label>
      <label class="col-span-5 sm:col-span-2 text-xs text-slate-600">MRP (₹)<input class="u-mrp field" type="number" min="0" step="0.01" value="${l.mrp_paise ? l.mrp_paise / 100 : ''}" /></label>
      <label class="col-span-8 sm:col-span-1 flex items-center gap-1 text-xs text-slate-600 pb-3"><input type="checkbox" class="u-av" ${l.available === false ? '' : 'checked'} /> Have</label>
      <button type="button" class="u-del col-span-4 sm:col-span-1 text-red-600 text-xs pb-3">Remove</button></div>`;
  }

  async function load() {
    [requests, orders] = await Promise.all([api('/api/partner/uploads'), api(`/api/partner/upload-orders${orderStatus ? `?status=${orderStatus}` : ''}`)]);
    const homeLabel = role === 'lab' ? 'Home sample collection' : 'Home delivery';
    root.innerHTML = `
      <div class="space-y-3">
        <p class="text-sm text-slate-500">Patients nearby uploaded these prescriptions. Open the file, write what you can supply and your price; the patient sees all quotations sorted cheapest and fastest first.${role === 'pharmacy' ? ' Check the original prescription before handing over prescription-only medicines.' : ''}</p>
        ${requests.length ? requests.map((r, n) => `<form data-u="${n}" class="bg-white rounded-xl border ${r.my_quote ? 'border-emerald-200' : 'border-slate-200'} p-4 space-y-3 text-sm">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <p class="font-semibold">${escapeHtml(r.reference)} · ${P.km(r.distance_km)} away ${r.my_quote ? '<span class="text-xs text-emerald-700">· quotation sent</span>' : ''}</p>
            <span class="flex gap-2"><button type="button" data-prefill="${n}" class="px-3 py-1.5 rounded-lg border border-cyan-300 text-cyan-800 text-xs font-semibold hover:bg-cyan-50">Pre-fill from prescription</button>
            <a href="${escapeHtml(r.file_url)}" target="_blank" class="px-3 py-1.5 rounded-lg bg-slate-800 text-white text-xs font-semibold">Open prescription ↗</a></span></div>
          ${r.note ? `<p class="text-slate-600">Patient's note: “${escapeHtml(r.note)}”</p>` : ''}
          <div class="u-lines space-y-2">${(r.my_quote ? r.my_quote.lines : [{}]).map(lineRow).join('')}</div>
          <button type="button" class="u-add text-cyan-700 text-xs font-semibold hover:underline">+ Add ${role === 'lab' ? 'test' : 'medicine'}</button>
          <div class="flex flex-wrap items-end gap-3 border-t border-slate-100 pt-3">
            <label class="flex items-center gap-2 text-slate-600 pb-1"><input type="checkbox" class="u-home" ${r.my_quote && r.my_quote.delivery_available ? 'checked' : ''} /> ${homeLabel}</label>
            <label class="text-slate-600 text-xs">Fee (₹)<input class="u-fee field w-24" type="number" min="0" value="${r.my_quote ? r.my_quote.delivery_fee_paise / 100 : 0}" /></label>
            <label class="text-slate-600 text-xs">${role === 'lab' ? 'Report in (hours)' : 'Ready in (minutes)'}<input class="u-ready field w-24" type="number" min="0" value="${r.my_quote && r.my_quote.ready_minutes != null ? (role === 'lab' ? r.my_quote.ready_minutes / 60 : r.my_quote.ready_minutes) : (role === 'lab' ? 24 : 15)}" /></label>
            ${role === 'pharmacy' ? `<label class="text-slate-600 text-xs">Delivery in (minutes)<input class="u-deliv field w-24" type="number" min="5" value="${(r.my_quote && r.my_quote.delivery_minutes) || 45}" /></label>` : ''}
            <label class="flex-1 min-w-[10rem] text-slate-600 text-xs">Note<input class="u-note field" maxlength="500" value="${escapeHtml((r.my_quote && r.my_quote.note) || '')}" placeholder="e.g. Generic available at lower price" /></label>
            <button class="px-4 py-2 bg-cyan-600 text-white font-semibold rounded-lg">${r.my_quote ? 'Update quotation' : 'Send quotation'}</button>
            <button type="button" data-decline="${r.id}" class="px-3 py-2 border border-slate-300 rounded-lg">Can't supply</button></div>
          <p class="u-total text-right text-slate-700 font-semibold"></p></form>`).join('')
          : '<div class="bg-white rounded-xl border border-dashed border-slate-300 p-6 text-center text-slate-500">No uploaded prescriptions waiting near you.</div>'}
        <div class="bg-white rounded-xl border border-slate-200 p-3 space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2"><h3 class="font-semibold">Bookings from uploaded prescriptions</h3>
            <div class="flex gap-1 text-sm u-filter">${[['open', 'Open'], ['completed', 'Completed'], ['', 'All']].map(([k, l]) =>
              `<button type="button" data-st="${k}" class="px-3 py-1 rounded-lg border border-slate-300 ${k === orderStatus ? 'bg-cyan-50' : ''}">${l}</button>`).join('')}</div></div>
          ${P.table([
            { label: 'Booked', render: o => `<p class="whitespace-nowrap">${escapeHtml(P.prettyDate(o.created_at.slice(0, 10)))}</p><p class="text-xs text-slate-500">${escapeHtml(o.reference)}</p>` },
            { label: 'Patient', render: o => `<p class="font-medium">${escapeHtml(o.patient_name)}</p><p class="text-xs text-slate-500">${escapeHtml(o.patient_phone || '')}</p>` },
            { label: 'Items', render: o => o.lines.map(l => `${escapeHtml(l.name)} × ${l.quantity}`).join('<br>') + `<p><a href="${o.prescription_file}" target="_blank" class="text-xs text-sky-700 hover:underline">Prescription ↗</a></p>` },
            { label: 'How', render: o => `${HOW[o.fulfilment]}${o.address ? `<p class="text-xs text-slate-500">${escapeHtml(o.address)}</p>` : ''}${o.preferred_date ? `<p class="text-xs">${escapeHtml(P.prettyDate(o.preferred_date))} ${o.preferred_time ? escapeHtml(P.prettyTime(o.preferred_time)) : ''}</p>` : ''}` },
            { label: 'Total', num: true, render: o => P.money(o.total_paise) },
            { label: 'Status', render: o => pill(o.status) },
            { label: 'Action', render: o => actions(o) },
          ], orders, { empty: 'No bookings here.' })}
        </div></div>`;
    root.querySelectorAll('form[data-u]').forEach(updateTotal);
  }

  function actions(o) {
    const b = (s, label, cls = 'border border-slate-300') => `<button type="button" data-o="${o.id}" data-to="${s}" class="px-2 py-1 rounded text-xs ${cls}">${label}</button>`;
    const go = 'bg-cyan-600 text-white';
    if (o.status === 'placed') return b('accepted', 'Accept', go) + ' ' + b('rejected', 'Reject');
    if (o.status === 'accepted') return role === 'lab' ? b('sample_collected', 'Sample collected', go) + ' ' + b('rejected', 'Reject')
      : b('ready', o.fulfilment === 'delivery' ? 'Out for delivery' : 'Ready for pickup', go) + ' ' + b('rejected', 'Reject');
    if (o.status === 'ready') return b('completed', 'Handed over', go);
    if (o.status === 'sample_collected') return `<button type="button" data-report="${o.id}" class="px-2 py-1 rounded text-xs bg-emerald-600 text-white">Upload report</button>`;
    if (o.status === 'report_ready') return b('completed', 'Paid & done', go);
    return o.report_record_id ? `<a href="/api/records/${o.report_record_id}/file" target="_blank" class="text-xs text-sky-700 hover:underline">Report ↗</a>` : '';
  }

  function updateTotal(form) {
    let sub = 0, mrp = 0, n = 0;
    form.querySelectorAll('.u-line').forEach(r => {
      if (!r.querySelector('.u-av').checked) return;
      const q = Number(r.querySelector('.u-qty').value || 0), p = Number(r.querySelector('.u-price').value || 0), m = Number(r.querySelector('.u-mrp').value || 0);
      sub += q * p; mrp += q * (m || p); n += 1;
    });
    form.querySelector('.u-total').textContent = n ? `Quotation: ${P.money(Math.round(sub * 100))}${mrp > sub ? ` · ${Math.round(100 * (mrp - sub) / mrp)}% off MRP` : ''}` : '';
  }

  async function sendQuote(form) {
    const r = requests[form.dataset.u];
    const lines = [...form.querySelectorAll('.u-line')].map(x => ({
      name: x.querySelector('.u-name').value.trim(), quantity: Number(x.querySelector('.u-qty').value || 1),
      medicine_id: x.dataset.medicineId ? Number(x.dataset.medicineId) : null,
      available: x.querySelector('.u-av').checked, unit_price: Number(x.querySelector('.u-price').value || 0),
      mrp: x.querySelector('.u-mrp').value ? Number(x.querySelector('.u-mrp').value) : null })).filter(l => l.name);
    if (!lines.length) return P.toast(`Add at least one ${role === 'lab' ? 'test' : 'medicine'}`, 'error');
    const ready = Number(form.querySelector('.u-ready').value || 0);
    const body = { lines, delivery_available: form.querySelector('.u-home').checked, delivery_fee: Number(form.querySelector('.u-fee').value || 0),
      ready_minutes: role === 'lab' ? Math.round(ready * 60) : ready, note: form.querySelector('.u-note').value.trim() || null };
    if (role === 'pharmacy') body.delivery_minutes = Number(form.querySelector('.u-deliv').value || 45);
    try { await api(`/api/partner/uploads/${r.id}/quote`, { method: 'POST', body }); P.toast('Quotation sent. The patient sees it right away.', 'success'); load(); }
    catch (err) { P.toast(err.message, 'error'); }
  }

  // Report upload for labs
  function reportModal(orderId) {
    let m = $('pu-report');
    if (!m) {
      m = document.createElement('div');
      m.id = 'pu-report';
      m.className = 'modal hidden fixed inset-0 z-50 bg-slate-900/50 flex items-center justify-center p-4';
      m.innerHTML = `<form class="w-full max-w-xl bg-white rounded-2xl shadow-xl p-6 space-y-3 text-sm">
        <div class="flex items-center justify-between"><h2 class="font-semibold">Upload report</h2><button type="button" data-close class="p-1 rounded hover:bg-slate-100" aria-label="Close">✕</button></div>
        <label class="block text-slate-600">Report file (PDF, JPG or PNG)<input name="file" type="file" required accept=".pdf,.jpg,.jpeg,.png" class="mt-1 w-full" /></label>
        <p class="text-slate-600">Key results (optional)</p>
        <div class="pu-vals space-y-2"></div>
        <button type="button" class="pu-addv text-cyan-700 text-xs hover:underline">+ Add result</button>
        <button class="w-full py-2.5 bg-emerald-600 text-white font-semibold rounded-lg">Upload and send to patient</button></form>`;
      document.body.appendChild(m);
      const addVal = () => m.querySelector('.pu-vals').insertAdjacentHTML('beforeend', `<div class="pu-v grid grid-cols-12 gap-2">
        <input class="col-span-4 field" placeholder="Result, e.g. HbA1c" /><input class="col-span-2 field" placeholder="Value" />
        <input class="col-span-2 field" placeholder="Unit" /><input class="col-span-2 field" type="number" step="any" placeholder="Low" />
        <input class="col-span-2 field" type="number" step="any" placeholder="High" /></div>`);
      m.querySelector('.pu-addv').addEventListener('click', addVal);
      m.addVal = addVal;
      m.querySelector('form').addEventListener('submit', async e => {
        e.preventDefault();
        const values = [...m.querySelectorAll('.pu-v')].map(r => { const i = r.querySelectorAll('input');
          return { name: i[0].value.trim(), value: i[1].value.trim(), unit: i[2].value.trim() || null,
            ref_low: i[3].value === '' ? null : Number(i[3].value), ref_high: i[4].value === '' ? null : Number(i[4].value) }; }).filter(x => x.name && x.value);
        const fd = new FormData();
        fd.append('file', e.target.elements.file.files[0]);
        fd.append('values', JSON.stringify(values));
        try { await P.upload(`/api/partner/upload-orders/${m.dataset.order}/report`, fd); P.closeModal('pu-report'); P.toast('Report sent to the patient', 'success'); load(); }
        catch (err) { P.toast(err.message, 'error'); }
      });
    }
    m.dataset.order = orderId;
    m.querySelector('form').reset();
    m.querySelector('.pu-vals').innerHTML = '';
    m.addVal();
    P.openModal('pu-report');
  }

  // Lines read from the photo, priced from this store's / lab's own list. Every line is checked before sending.
  async function prefill(form) {
    const r = requests[form.dataset.u];
    try {
      const s = await api(`/api/partner/uploads/${r.id}/suggested-lines`);
      if (s.status === 'pending' || s.status === 'running') return P.toast('Still reading the prescription. Try again in a minute.', 'info');
      if (s.status !== 'done' || !s.lines.length) return P.toast(s.legible === false ? "The photo isn't clear enough to read automatically. Please open it." : 'Nothing to pre-fill. Please open the prescription.', 'info');
      form.querySelector('.u-lines').innerHTML = s.lines.map(l => lineRow({
        name: l.name, medicine_id: l.medicine_id, quantity: l.quantity, available: l.available,
        unit_price_paise: l.unit_price != null ? Math.round(l.unit_price * 100) : null, mrp_paise: l.mrp != null ? Math.round(l.mrp * 100) : null,
        check: !l.available || (l.confidence != null && l.confidence < 0.9) || !l.medicine_id,
        hint: `Written as “${l.written_as}”` + (l.substitute ? ' · same-salt substitute from your list' : '')
          + (l.unit_price == null ? ' · not on your price list: enter a price or untick' : '')
          + (l.confidence != null && l.confidence < 0.9 ? ' · check this match' : '') })).join('');
      updateTotal(form);
      P.toast('Pre-filled. Check each line against the prescription before sending.', 'success');
    } catch (err) { P.toast(err.message, 'error'); }
  }

  function init(el, partnerRole) {
    root = el; role = partnerRole;
    root.addEventListener('submit', e => { const f = e.target.closest('form[data-u]'); if (f) { e.preventDefault(); sendQuote(f); } });
    root.addEventListener('input', e => { const f = e.target.closest('form[data-u]'); if (f) updateTotal(f); });
    root.addEventListener('change', e => { const f = e.target.closest('form[data-u]'); if (f) updateTotal(f); });
    root.addEventListener('click', async e => {
      const add = e.target.closest('.u-add'), del = e.target.closest('.u-del'), dec = e.target.closest('[data-decline]');
      const st = e.target.closest('[data-o]'), flt = e.target.closest('.u-filter [data-st]'), rep = e.target.closest('[data-report]');
      const pre = e.target.closest('[data-prefill]');
      if (pre) return prefill(pre.closest('form[data-u]'));
      if (add) { add.previousElementSibling.insertAdjacentHTML('beforeend', lineRow()); return; }
      if (del) { const lines = del.closest('.u-lines'); if (lines.children.length > 1) del.closest('.u-line').remove(); updateTotal(del.closest('form') || lines.closest('form')); return; }
      if (flt) { orderStatus = flt.dataset.st; return load(); }
      if (rep) return reportModal(rep.dataset.report);
      try {
        if (dec) { await api(`/api/partner/uploads/${dec.dataset.decline}/decline`, { method: 'POST' }); return load(); }
        if (st) {
          const body = { status: st.dataset.to };
          if (body.status === 'rejected') { body.reason = prompt('Reason for the patient:'); if (!body.reason) return; }
          if (body.status === 'completed' && role === 'pharmacy') { body.code = prompt("Patient's 4-digit handover code:"); if (!body.code) return; }
          await api(`/api/partner/upload-orders/${st.dataset.o}/status`, { method: 'POST', body });
          load();
        }
      } catch (err) { P.toast(err.message, 'error'); }
    });
  }

  // "Patients' decisions": which requests this partner won or lost in the last 14 days (all request types)
  async function outcomes(el) {
    const data = await api('/api/partner/outcomes');
    if (!data.outcomes.length) { el.innerHTML = ''; return; }
    const other = role === 'lab' ? 'another lab' : 'another pharmacy';
    el.innerHTML = `<div class="bg-white rounded-xl border border-slate-200 p-3 text-sm space-y-2">
      <div class="flex flex-wrap items-center justify-between gap-2"><h3 class="font-semibold">Patients' decisions · last 14 days</h3>
        <span class="text-xs text-slate-500">Chose you: <b class="text-emerald-700">${data.won}</b> · Chose ${other}: <b class="text-slate-700">${data.lost}</b></span></div>
      <div class="flex flex-wrap gap-2">${data.outcomes.slice(0, 8).map(o => `<span class="px-2.5 py-1 rounded-full text-xs border ${o.outcome === 'chosen'
        ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-slate-300 bg-slate-50 text-slate-600'}">${escapeHtml(o.reference)} · ${o.outcome === 'chosen'
        ? 'chose you' : `chose ${other}`}${o.quoted ? '' : ' (you had not quoted)'}</span>`).join('')}</div></div>`;
  }

  return { init, load: (...a) => P.safe(load, root), outcomes: el => P.safe(() => outcomes(el), el) };
})();
