// Follow-ups the doctor asked for, with one-tap booking. window.FollowUps.mount(element)
(() => {
  const P = window.Pyshio;
  const esc = P.escapeHtml;

  function card(f) {
    const when = f.overdue ? `<span class="text-red-700 font-semibold">${-f.days_left} day${f.days_left === -1 ? '' : 's'} overdue</span>`
      : f.days_left === 0 ? '<span class="text-amber-700 font-semibold">due today</span>'
      : `in ${f.days_left} day${f.days_left === 1 ? '' : 's'}`;
    return `<div class="flex flex-wrap items-center gap-3 rounded-xl border ${f.booked_for ? 'border-emerald-200 bg-emerald-50/40' : f.overdue ? 'border-red-200 bg-red-50/40' : 'border-teal-200 bg-teal-50/40'} p-4">
      <span class="text-2xl" aria-hidden="true">🩺</span>
      <div class="flex-1 min-w-[12rem]">
        <p class="font-semibold text-slate-900">Follow-up with ${esc(f.doctor_name)}${f.for_name ? ` <span class="text-sm font-normal text-violet-700">for ${esc(f.for_name)}</span>` : ''}</p>
        <p class="text-sm text-slate-600">${esc(P.prettyDate(f.follow_up_on))} · ${f.booked_for ? `booked for ${esc(P.prettyDate(f.booked_for))}` : when} · prescription ${esc(f.reference)}</p>
      </div>
      ${f.booked_for ? '<span class="text-sm text-emerald-700 font-medium">✓ Booked</span>'
        : `<a href="${esc(f.book_path)}" class="px-4 py-2 bg-teal-600 text-white text-sm font-semibold rounded-lg hover:bg-teal-700">Book follow-up</a>`}
    </div>`;
  }

  window.FollowUps = {
    async mount(el) {
      const list = await P.api('/api/my/follow-ups').catch(() => []);
      el.innerHTML = list.length ? `<div class="space-y-2"><h2 class="font-semibold text-slate-900">Follow-ups your doctors asked for</h2>${list.map(card).join('')}</div>` : '';
      return list;
    },
  };
})();
