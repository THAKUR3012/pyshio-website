// Pyshio service worker: makes the site installable and shows a friendly page when the network is down.
// Pages and API data always come fresh from the network (health data is never cached); only the app shell is.
const VERSION = 'pyshio-v1';
const SHELL = ['/offline', '/assets/app.js', '/assets/favicon.svg', '/features/pwa/icon-192.png',
  '/features/notifications/bell.js', '/features/pwa/pwa.js'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(VERSION).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  if (req.mode === 'navigate') {
    // Pages: network first, offline page if that fails
    event.respondWith(fetch(req).catch(() => caches.match('/offline')));
    return;
  }
  if (SHELL.includes(url.pathname)) {
    // Shell files: network first so updates arrive, cache as a fallback
    event.respondWith(fetch(req).then(res => {
      const copy = res.clone();
      caches.open(VERSION).then(cache => cache.put(req, copy));
      return res;
    }).catch(() => caches.match(req)));
  }
});

// Clicking a notification shown by the page focuses the app on the right screen
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.link) || '/';
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const open = list.find(c => new URL(c.url).origin === location.origin);
    if (open) { open.navigate(target); return open.focus(); }
    return self.clients.openWindow(target);
  }));
});
