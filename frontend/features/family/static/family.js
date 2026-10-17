// Family members: manage profiles (patient Profile tab) and offer them in "Who is this for?" pickers.
// window.FamilyCard.mount(element) · window.FamilyCard.list() · window.FamilyCard.options(list, selectedId)
(() => {
  const P = window.Pyshio;
  const esc = P.escapeHtml;
  const RELATIONS = ['spouse', 'son', 'daughter', 'father', 'mother', 'brother', 'sister', 'grandfather', 'grandmother', 'grandchild', 'other'];
  const BLOOD = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];
  const cap = s => s[0].toUpperCase() + s.slice(1);
  const field = 'mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg';
  let root = null, editing = null, members = [];

  const list = () => P.api('/api/my/family');

  function form(m) {
    m = m || {};
    return `<form id="fam-form" class="grid sm:grid-cols-3 gap-3 text-sm border border-sky-200 bg-sky-50/50 rounded-lg p-3">
      <label class="text-slate-600">Name<input name="name" required maxlength="80" value="${esc(m.name || '')}" class="${field}" /></label>
      <label class="text-slate-600">Relation<select name="relation" required class="${field}">${RELATIONS.map(r => `<option value="${r}" ${m.relation === r ? 'selected' : ''}>${cap(r)}</option>`).join('')}</select></label>
      <label class="text-slate-600">Date of birth<input name="dob" type="date" max="${P.isoDate(P.today())}" value="${esc(m.dob || '')}" class="${field}" /></label>
      <label class="text-slate-600">Sex<select name="sex" class="${field}"><option value="">—</option>${[['F', 'Female'], ['M', 'Male'], ['O', 'Other']].map(([v, l]) => `<option value="${v}" ${m.sex === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="text-slate-600">Blood group<select name="blood_group" class="${field}"><option value="">Not known</option>${BLOOD.map(b => `<option ${m.blood_group === b ? 'selected' : ''}>${b}</option>`).join('')}</select></label>
      <label class="text-slate-600">Allergies<input name="allergies" maxlength="500" placeholder="e.g. Penicillin, peanuts" value="${esc(m.allergies || '')}" class="${field}" /></label>
      <label class="text-slate-600 sm:col-span-2">Long-term conditions<input name="conditions" maxlength="500" placeholder="e.g. Asthma" value="${esc(m.conditions || '')}" class="${field}" /></label>
      <div class="flex items-end justify-end gap-2"><button type="button" id="fam-cancel" class="px-3 py-2 border border-slate-300 rounded-lg">Cancel</button>
        <button class="px-4 py-2 bg-sky-600 text-white font-semibold rounded-lg">${m.id ? 'Save' : 'Add'}</button></div>
    </form>`;
  }

  async function render() {
    members = await list();
    root.innerHTML = `<div class="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div><h2 class="font-semibold">Family members</h2>
          <p class="text-sm text-slate-600">Book doctors, keep reports and get medicines for your family from this account. Doctors see the family member's own age, allergies and blood group.</p></div>
        ${editing === null && members.length < 10 ? '<button type="button" id="fam-add" class="px-3 py-1.5 bg-sky-600 text-white text-sm font-semibold rounded-lg">+ Add family member</button>' : ''}
      </div>
      ${editing !== null ? form(members.find(m => m.id === editing)) : ''}
      ${P.table([
        { label: 'Name', render: m => `<span class="font-medium">${esc(m.name)}</span>` },
        { label: 'Relation', render: m => cap(m.relation) },
        { label: 'Age', render: m => m.age == null ? '—' : String(m.age), num: true },
        { label: 'Blood group', render: m => esc(m.blood_group || '—') },
        { label: 'Allergies', render: m => m.allergies ? `<span class="text-red-700">${esc(m.allergies)}</span>` : '—' },
        { label: '', render: m => `<button type="button" data-fam-edit="${m.id}" class="text-xs text-sky-700 hover:underline">Edit</button> <button type="button" data-fam-del="${m.id}" class="text-xs text-red-600 hover:underline">Remove</button>` },
      ], members, { empty: 'No family members yet.' })}
    </div>`;
  }

  function wire() {
    root.addEventListener('click', async e => {
      if (e.target.id === 'fam-add') { editing = 0; return render(); }
      if (e.target.id === 'fam-cancel') { editing = null; return render(); }
      const ed = e.target.closest('[data-fam-edit]');
      if (ed) { editing = Number(ed.dataset.famEdit); return render(); }
      const del = e.target.closest('[data-fam-del]');
      if (del && confirm('Remove this family member? Their past visits and reports stay in your records.')) {
        try { await P.api(`/api/my/family/${del.dataset.famDel}`, { method: 'DELETE' }); P.toast('Removed', 'success'); render(); }
        catch (err) { P.toast(err.message, 'error'); }
      }
    });
    root.addEventListener('submit', async e => {
      if (e.target.id !== 'fam-form') return;
      e.preventDefault();
      const f = e.target.elements;
      const body = { name: f.name.value.trim(), relation: f.relation.value, dob: f.dob.value || null, sex: f.sex.value || null,
        blood_group: f.blood_group.value || null, allergies: f.allergies.value.trim() || null, conditions: f.conditions.value.trim() || null };
      try {
        await P.api(editing ? `/api/my/family/${editing}` : '/api/my/family', { method: editing ? 'PUT' : 'POST', body });
        P.toast(editing ? 'Saved' : `${body.name} added`, 'success');
        editing = null;
        render();
      } catch (err) { P.toast(err.message, 'error'); }
    });
  }

  window.FamilyCard = {
    async mount(el) {
      if (root !== el) { root = el; wire(); }
      return P.safe(render, root);
    },
    list,
    options(items, selected) {
      return '<option value="">Myself</option>' + items.map(m =>
        `<option value="${m.id}" ${String(selected) === String(m.id) ? 'selected' : ''}>${esc(m.label)}</option>`).join('');
    },
  };
})();
