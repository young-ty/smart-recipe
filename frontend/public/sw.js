/* Service worker：手写，不引第三方依赖（和 /metrics 一样，能用标准 API 就不多加一个包）。
   只做两件事：应用外壳离线可打开、静态资源缓存优先。接口请求一律走网络——
   识别结果和菜谱不能拿缓存糊弄，那会让页面显示过期的菜名与做法。 */

const CACHE = 'recipe-shell-v1'
const SCOPE = self.registration.scope
const SHELL = [`${SCOPE}`, `${SCOPE}index.html`, `${SCOPE}manifest.webmanifest`]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  // 版本号变了就清掉旧缓存，避免升级后一直吃到上一版的脚本
  const clearStale = () =>
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
    )

  event.waitUntil(clearStale().then(() => self.clients.claim()))
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') {
    return
  }
  const url = new URL(request.url)
  if (url.origin !== self.location.origin || url.pathname.includes('/api/')) {
    return
  }

  if (request.mode === 'navigate') {
    // 导航优先走网络（拿最新版本），断网时回退到外壳
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(`${SCOPE}index.html`).then((cached) => cached ?? Response.error()),
      ),
    )
    return
  }

  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ??
        fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone()
            void caches.open(CACHE).then((cache) => cache.put(request, copy))
          }
          return response
        }),
    ),
  )
})
