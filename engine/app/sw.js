/* ShelfSense — offline support: keeps the app itself on the phone so it opens without internet. */
const VERSION = "__VERSION__";
const SHELL = ["./", "index.html", "config.js", "adapters.js", "manifest.webmanifest", "icon-192.png", "icon-512.png", "vendor/zxing.min.js"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.includes("/api/")) return;
  // app files: answer from the phone at once, refresh in the background
  e.respondWith(caches.open(VERSION).then(async cache => {
    const hit = await cache.match(e.request, {ignoreSearch: true});
    const fresh = fetch(e.request).then(r => { if (r.ok) cache.put(e.request, r.clone()); return r; }).catch(() => hit);
    return hit || fresh;
  }));
});
