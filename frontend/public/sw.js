/* SpendLens service worker.
 *
 * This SW exists for one specific job: handling the manifest's
 * ``share_target`` POST. The browser POSTs the user's shared file to
 * ``/share-target``, which a static-asset Vite build can't actually
 * serve as a POST endpoint. The SW intercepts the request, stashes
 * the file in Cache Storage, then 303-redirects to ``/share-target``
 * as a GET — the React route at that path picks the file out of the
 * cache and uploads it through the normal authed API path.
 *
 * Why Cache Storage and not IndexedDB:
 *   The Cache API gives us a single-line ``cache.put(url, response)``
 *   round-trip with no schema. The page reads it back via
 *   ``caches.match('/__shared-receipt')`` which is the same primitive
 *   it would use for any HTTP fetch.
 *
 * Why not authenticate inside the SW:
 *   The SW would need access to the in-memory bearer token, which
 *   lives in the page's runtime. Passing it via ``postMessage`` is
 *   doable but races the SW lifecycle (SW can run with no clients
 *   connected). Stashing the file and letting the page do the
 *   authed upload sidesteps the whole problem.
 *
 * No offline caching, no precaching of assets. This SW is share-
 * target-only. Phase 8 brings a richer offline story.
 */

const SHARE_CACHE = 'spendlens-shared-v1';
const STASH_URL = '/__shared-receipt';

self.addEventListener('install', () => {
  // ``skipWaiting`` lets a fresh SW take over immediately on
  // refresh — the user shouldn't have to close every tab to pick
  // up an SW change. Safe here because we don't precache anything.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Only the share-target POST is interesting. Every other request
  // falls through to the network — vite's dev server, the API
  // proxy, and the production build all serve their own assets.
  if (event.request.method === 'POST' && url.pathname === '/share-target') {
    event.respondWith(handleSharedFile(event.request));
  }
});

async function handleSharedFile(request) {
  try {
    const formData = await request.formData();
    const file = formData.get('receipt');
    if (file instanceof File && file.size > 0) {
      const cache = await caches.open(SHARE_CACHE);
      // The stash response carries the file as its body and the
      // original metadata as headers — the page reconstructs a real
      // ``File`` from this for the actual upload.
      await cache.put(
        STASH_URL,
        new Response(file, {
          headers: {
            'Content-Type': file.type || 'application/octet-stream',
            'X-Shared-Filename': encodeURIComponent(file.name || 'receipt'),
          },
        }),
      );
    }
  } catch (err) {
    // The redirect still happens — the page gracefully falls back
    // to "no shared file, here's the manual uploader". Logging so
    // failures are visible in the SW console.
    console.error('share-target stash failed', err);
  }

  // 303 because the response represents the result of a POST, and
  // we want the browser to switch to GET for the redirect target.
  return Response.redirect('/share-target', 303);
}
