// Health profile: the patient's own form (Profile tab), and the doctor's summary strip with allergy checks.
// window.HealthProfile.mount(element)                       – patient
// window.HealthProfile.strip(patientId, memberId) -> html   – doctor
// window.HealthProfile.check(patientId, memberId, items)    – doctor: allergy warnings for medicines being written
(() => {
  const P = window.Pyshio;
  const esc = P.escapeHtml;
  const BLOOD = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];
  const field = 'mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg';
  let root = null;

  async function render() {
    const p = await P.api('/api/my/health-profile');
    root.innerHTML = `<form id="hp-form" class="bg-white rounded-xl border border-slate-200 p-6 space-y-3">
      <h2 class="font-semibold">Health profile</h2>
      <p class="text-sm text-slate-600">Doctors you book see this before your visit, and are warned if they prescribe something you're allergic to.</p>
      <div class="grid sm:grid-cols-2 gap-3 text-sm">
        <label class="text-slate-600">Blood group<select name="blood_group" class="${field}"><option value="">Not known</option>${BLOOD.map(b => `<option ${p.blood_group === b ? 'selected' : ''}>${b}</option>`).join('')}</select></label>
        <label class="text-slate-600">Allergies<input name="allergies" maxlength="500" placeholder="e.g. Penicillin, sulfa, peanuts" value="${esc(p.allergies || '')}" class="${field}" /></label>
        <label class="text-slate-600 sm:col-span-2">Long-term conditions<input name="chronic_conditions" maxlength="500" placeholder="e.g. Diabetes, high blood pressure" value="${esc(p.chronic_conditions || '')}" class="${field}" /></label>
        <label class="text-slate-600">Emergency contact<input name="emergency_name" maxlength="100" placeholder="Name" value="${esc(p.emergency_name || '')}" class="${field}" /></label>
        <label class="text-slate-600">Their phone<input name="emergency_phone" maxlength="20" placeholder="+91 …" value="${esc(p.emergency_phone || '')}" class="${field}" /></label>
      </div>
      <button class="px-4 py-2 bg-sky-600 text-white font-semibold rounded-lg hover:bg-sky-700">Save health profile</button>
    </form>`;
  }

  function wire() {
    root.addEventListener('submit', async e => {
      if (e.target.id !== 'hp-form') return;
      e.preventDefault();
      const f = e.target.elements;
      const body = {};
      ['blood_group', 'allergies', 'chronic_conditions', 'emergency_name', 'emergency_phone'].forEach(k => { body[k] = f[k].value.trim() || null; });
      try { await P.api('/api/my/health-profile', { method: 'PUT', body }); P.toast('Health profile saved', 'success'); }
      catch (err) { P.toast(err.message, 'error'); }
    });
  }

  async function strip(patientId, memberId) {
    const p = await P.api(`/api/doctor/patients/${patientId}/health-profile${memberId ? `?member_id=${memberId}` : ''}`).catch(() => null);
    if (!p) return '';
    const bits = [
      p.age != null ? `${p.age} y${p.sex ? ' · ' + esc(p.sex) : ''}` : (p.sex ? esc(p.sex) : ''),
      p.blood_group ? `Blood group <strong>${esc(p.blood_group)}</strong>` : '',
      p.chronic_conditions ? `Conditions: ${esc(p.chronic_conditions)}` : '',
      p.emergency_name ? `Emergency: ${esc(p.emergency_name)}${p.emergency_phone ? ` (${esc(p.emergency_phone)})` : ''}` : '',
    ].filter(Boolean);
    return `<div class="rounded-lg border ${p.allergies ? 'border-red-200 bg-red-50' : 'border-slate-200 bg-slate-50'} px-3 py-2 text-sm space-y-0.5">
      <p class="font-semibold text-slate-800">Health profile · ${esc(p.name)}${p.relation && p.relation !== 'self' ? ` <span class="font-normal text-violet-700">(${esc(p.relation)})</span>` : ''}</p>
      ${p.allergies ? `<p class="text-red-700 font-semibold">⚠ Allergies: ${esc(p.allergies)}</p>` : '<p class="text-slate-500">No allergies recorded</p>'}
      ${bits.length ? `<p class="text-slate-600">${bits.join(' · ')}</p>` : ''}
    </div>`;
  }

  async function check(patientId, memberId, items) {
    if (!items.length) return [];
    const res = await P.api('/api/doctor/allergy-check', { method: 'POST',
      body: { patient_id: patientId, family_member_id: memberId || null, items } }).catch(() => ({ warnings: [] }));
    return res.warnings;
  }

  window.HealthProfile = {
    async mount(el) {
      if (root !== el) { root = el; wire(); }
      return P.safe(render, root);
    },
    strip,
    check,
  };
})();
