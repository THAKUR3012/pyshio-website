// Sales & prescribing insights shared by the pharma company and MR dashboards.
// Doctors who opted in appear by name; everyone else as "Doctor 7KQ2" (category + city only).
// Patients never appear. Disease figures under 5 arrive as "<5".
window.PyshioInsights = (() => {
  const P = window.Pyshio;
  const { escapeHtml } = P;

  function render(el, data, { chartPrefix = 'ins', doctorSearch = true } = {}) {
    const t = data.totals;
    const card = (label, value, sub = '', cls = '') => `<div class="bg-white rounded-xl border border-slate-200 p-3 ${cls}"><p class="text-xs text-slate-500">${label}</p>
      <p class="text-xl font-bold text-slate-900 text-right tabular-nums">${value}</p>${sub ? `<p class="text-xs text-slate-500 text-right">${sub}</p>` : ''}</div>`;
    const pct = v => v == null ? '—' : `${v}%`;
    const ownBadge = '<span class="ml-1 text-[11px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-800 font-semibold">Your brand</span>';
    el.innerHTML = `
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        ${card(`Sales (${data.days} days)`, P.money(t.sales_paise), `${P.num(t.units_sold)} units sold`, 'border-violet-200 bg-violet-50/40')}
        ${card('Prescriptions with your brands', P.num(t.own_prescriptions), `${P.num(t.own_units)} units prescribed`)}
        ${card('Your share of these salts', pct(t.own_share_percent), `of ${P.num(t.market_prescriptions)} prescriptions`)}
        ${card('Doctors prescribing you', P.num(t.doctors_prescribing), `of ${P.num(t.doctors_in_scope)} in area`)}
        ${card('Doctors named', P.num(t.named_doctors), 'others anonymous')}
        ${card('Your products', P.num(t.products))}
      </div>
      ${t.products ? '' : '<p class="rounded-lg bg-amber-50 border border-amber-200 text-amber-900 text-sm px-4 py-3">List your brands (company dashboard → Brands) to see your sales, share and doctor-wise prescribing.</p>'}
      <div class="grid lg:grid-cols-2 gap-4">
        <div class="bg-white rounded-xl border border-slate-200 p-4"><h3 class="font-semibold mb-2">Sales &amp; prescriptions by month</h3><div class="h-60"><canvas id="${chartPrefix}-trend"></canvas></div></div>
        <div class="bg-white rounded-xl border border-slate-200 p-4"><h3 class="font-semibold mb-2">Diseases your brands are prescribed for</h3><div class="h-60"><canvas id="${chartPrefix}-diseases"></canvas></div></div>
      </div>
      <div class="bg-white rounded-xl border border-slate-200 p-4"><h3 class="font-semibold mb-1">Your products</h3>
        <p class="text-xs text-slate-500 mb-2">Prescribed = written by doctors; sold = handed over by pharmacies on Pyshio. Share = prescriptions with your brand among all brands of the same salt.</p>${P.table([
          { label: 'Product', render: p => `<p class="font-medium">${escapeHtml(p.name)}</p><p class="text-xs text-slate-500">${escapeHtml([p.generic_name, p.strength].filter(Boolean).join(' '))}</p>` },
          { label: 'Doctors', key: 'doctors', num: true },
          { label: 'Prescriptions', key: 'prescriptions', num: true },
          { label: 'Units prescribed', key: 'units', num: true },
          { label: 'Units sold', key: 'units_sold', num: true },
          { label: 'Sales', num: true, render: p => P.money(p.sales_paise) },
          { label: 'Sold vs prescribed', num: true, render: p => pct(p.conversion_percent) },
          { label: 'Salt share', num: true, render: p => pct(p.salt_share_percent) },
          { label: 'Top diseases', render: p => p.top_conditions.length ? p.top_conditions.map(c => `${escapeHtml(c.name)} <span class="text-xs text-slate-500">(${escapeHtml(P.count(c.prescriptions))})</span>`).join('<br>') : '<span class="text-slate-400">—</span>' },
        ], data.products, { empty: 'No products listed yet.' })}</div>
      <div class="bg-white rounded-xl border border-slate-200 p-4"><div class="flex flex-wrap items-center justify-between gap-2 mb-1">
          <h3 class="font-semibold">Doctor-wise prescribing</h3>
          ${doctorSearch ? `<input id="${chartPrefix}-docq" placeholder="Search doctor, category or city" class="px-3 py-1.5 border border-slate-300 rounded-lg text-sm" />` : ''}</div>
        <p class="text-xs text-slate-500 mb-2">Every doctor in the area who prescribed these salts. <span class="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">Named</span> doctors chose to share; others show a code that stays the same for your company (only category and city). Where very few doctors of a type practise in a city they are grouped.</p>
        <div id="${chartPrefix}-doctors"></div></div>
      <div class="grid xl:grid-cols-2 gap-4">
        <div class="bg-white rounded-xl border border-slate-200 p-4"><h3 class="font-semibold mb-2">By disease</h3>${P.table([
          { label: 'Disease', render: d => `${escapeHtml(d.condition)} <span class="text-xs text-slate-400">${escapeHtml(d.code)}</span>` },
          { label: 'Care area', key: 'care_area' },
          { label: 'With your brands', key: 'own_prescriptions', num: true },
          { label: 'All brands (your salts)', key: 'market_prescriptions', num: true },
          { label: 'Your share', num: true, render: d => pct(d.own_share_percent) },
          { label: 'Top product', key: 'top_product' }], data.diseases, { empty: 'No coded diagnoses yet for these salts.' })}</div>
        <div class="bg-white rounded-xl border border-slate-200 p-4"><h3 class="font-semibold mb-2">Sales by city</h3>${P.table([
          { label: 'City', render: c => `${escapeHtml(c.city)}<p class="text-xs text-slate-500">${escapeHtml(c.state || '')}</p>` },
          { label: 'Units sold', key: 'units_sold', num: true },
          { label: 'Sales', num: true, render: c => P.money(c.sales_paise) }], data.sales_by_city, { empty: 'No completed sales yet.' })}
          <h3 class="font-semibold mt-5 mb-2">Most prescribed in your care areas (all companies)</h3>${P.table([
          { label: 'Medicine', render: m => `<p>${escapeHtml(m.name)}${m.own ? ownBadge : ''}</p><p class="text-xs text-slate-500">${escapeHtml([m.generic_name, m.strength].filter(Boolean).join(' '))}${m.manufacturer ? ' · ' + escapeHtml(m.manufacturer) : ''}</p>` },
          { label: 'Prescriptions', key: 'prescriptions', num: true },
          { label: 'Units', key: 'units', num: true }], data.top_medicines, { empty: 'No prescriptions in this period.' })}</div>
      </div>
      <p class="text-xs text-slate-500">No patient names, ages or contact details are ever included; disease counts under ${data.min_group_size} show as “&lt;${data.min_group_size}”. Use this to plan visits and supply, not to pressure prescribing (UCPMP 2024).</p>`;

    const renderDoctors = () => {
      const q = (document.getElementById(`${chartPrefix}-docq`) || {}).value || '';
      const rows = data.doctors.filter(d => !q || `${d.label} ${d.category} ${d.city} ${d.speciality || ''}`.toLowerCase().includes(q.toLowerCase()));
      document.getElementById(`${chartPrefix}-doctors`).innerHTML = P.table([
        { label: 'Doctor', render: d => `<p class="font-medium">${escapeHtml(d.label)} ${d.named ? '<span class="text-[11px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">Named</span>'
            : d.pooled ? `<span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">${d.doctors} doctor(s) grouped</span>` : '<span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">Anonymous</span>'}</p>
            <p class="text-xs text-slate-500">${escapeHtml(d.named ? `${d.clinic_name}, ${d.city}` : `${d.category} · ${d.city}`)}</p>` },
        { label: 'With your brands', key: 'own_prescriptions', num: true },
        { label: 'Units', key: 'own_units', num: true },
        { label: 'All brands (your salts)', key: 'market_prescriptions', num: true },
        { label: 'Your share', num: true, render: d => pct(d.share_percent) },
        { label: 'Your products prescribed', render: d => d.products.length ? d.products.map(p => `${escapeHtml(p.name)} <span class="text-xs text-slate-500">× ${P.num(p.prescriptions)} (${P.num(p.units)} units)</span>`).join('<br>') : '<span class="text-amber-700 text-xs">None yet</span>' },
        { label: 'Top competitor', render: d => escapeHtml(d.top_competitor || '—') },
      ], rows, { empty: 'No doctors match.' });
    };
    renderDoctors();
    const search = document.getElementById(`${chartPrefix}-docq`);
    if (search) search.addEventListener('input', renderDoctors);

    const num = P.numeric;
    P.chart(`${chartPrefix}-trend`, { type: 'bar', data: { labels: data.trend.map(m => m.month), datasets: [
      { type: 'bar', label: 'Sales (₹)', data: data.trend.map(m => Math.round(m.sales_paise / 100)), backgroundColor: '#c4b5fd', yAxisID: 'y1' },
      { type: 'line', label: 'Prescriptions with your brands', data: data.trend.map(m => m.own_prescriptions), borderColor: '#7c3aed', backgroundColor: '#7c3aed', tension: .3, yAxisID: 'y' }] },
      options: { plugins: { legend: { display: true } }, scales: { y: { beginAtZero: true, position: 'left', ticks: { precision: 0 } },
        y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false } } } } });
    const top = data.diseases.slice(0, 8);
    P.chart(`${chartPrefix}-diseases`, { type: 'bar', data: { labels: top.map(d => d.condition), datasets: [
      { label: 'With your brands', data: top.map(d => num(d.own_prescriptions)), backgroundColor: '#7c3aed' },
      { label: 'All brands of your salts', data: top.map(d => num(d.market_prescriptions)), backgroundColor: '#cbd5e1' }] },
      options: { indexAxis: 'y', plugins: { legend: { display: true } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } } });
  }

  return { render };
})();
