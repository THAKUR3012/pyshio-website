// Installable app: manifest + theme colour on every page, the service worker, and an "Install app" button.
(() => {
  const head = document.head;
  if (!document.querySelector('link[rel="manifest"]')) {
    head.insertAdjacentHTML('beforeend', '<link rel="manifest" href="/manifest.webmanifest">'
      + '<meta name="theme-color" content="#0284c7">'
      + '<link rel="apple-touch-icon" href="/features/pwa/icon-192.png">');
  }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
  }

  let installEvent = null;
  function showButton() {
    const slot = document.getElementById('nav-install');
    if (!slot) return;
    if (!installEvent) { slot.innerHTML = ''; return; }
    slot.innerHTML = `<button type="button" class="hidden sm:inline-flex items-center gap-1 text-sm px-3 py-1.5 rounded-lg border border-sky-200 text-sky-700 hover:bg-sky-50" title="Install Pyshio on this device">
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16"/></svg>Install app</button>`;
    slot.querySelector('button').addEventListener('click', async () => {
      const ev = installEvent;
      installEvent = null;
      showButton();
      ev.prompt();
      await ev.userChoice.catch(() => {});
    });
  }
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); installEvent = e; showButton(); });
  window.addEventListener('appinstalled', () => { installEvent = null; showButton(); });
  document.addEventListener('pyshio:nav', showButton);
})();
