// Doctor's video-consultation settings (doctor Profile tab). window.VideoSettings.mount(element)
(() => {
  const P = window.Pyshio;
  window.VideoSettings = {
    async mount(el) {
      const s = await P.api('/api/doctor/video-settings');
      el.innerHTML = `<form id="video-form" class="mb-6 bg-white rounded-2xl border border-slate-200 p-6 max-w-3xl space-y-3">
        <h2 class="font-semibold">🎥 Video consultations</h2>
        <label class="flex items-start gap-3 text-sm text-slate-700"><input type="checkbox" name="offers_video" class="mt-1" ${s.offers_video ? 'checked' : ''} />
          <span><strong>Offer video consultations</strong><span class="block text-xs text-slate-500">Patients anywhere can book your normal slots as a video call. You both join a private room from the appointment, 10 minutes before the start; only a random room name is shared with the video service (Jitsi Meet).</span></span></label>
        <label class="block text-sm text-slate-600 max-w-xs">Video consultation fee (₹)
          <input name="video_fee" type="number" min="0" max="20000" placeholder="Same as the service fee" value="${s.video_fee ?? ''}" class="field" /></label>
        <button class="px-4 py-2 bg-sky-600 text-white text-sm font-semibold rounded-lg hover:bg-sky-700">Save</button>
      </form>`;
      el.querySelector('#video-form').addEventListener('submit', async e => {
        e.preventDefault();
        const f = e.target.elements;
        try {
          await P.api('/api/doctor/video-settings', { method: 'PUT', body: { offers_video: f.offers_video.checked,
            video_fee: f.video_fee.value === '' ? null : Number(f.video_fee.value) } });
          P.toast(f.offers_video.checked ? 'Video consultations are on' : 'Video consultations are off', 'success');
        } catch (err) { P.toast(err.message, 'error'); }
      });
    },
  };
})();
