import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useUploadReceipt } from '@/api/receipts';

const STASH_URL = '/__shared-receipt';

// Landing route for the PWA ``share_target``. The browser-issued
// share-sheet POST is intercepted by the service worker, which
// stashes the file in Cache Storage and 303-redirects here. We pick
// the file out of the cache, run it through the normal authed upload
// path, then send the user to the receipts list.
//
// Three observable outcomes:
//
// * **Cache hit** → upload succeeds → ``navigate('/receipts')``.
// * **Cache miss** (e.g. user typed the URL directly, or the SW
//   isn't installed yet) → friendly "open the receipts tab"
//   message instead of a confusing "stuck loading" state.
// * **Upload error** → surface the message; user can either retry
//   from the receipts page or share the file again.
export function ShareTargetPage() {
  // ``ran`` guards against React 18 strict-mode double-invocation:
  // the effect fires twice in dev, but we only want one upload.
  const ran = useRef(false);
  const navigate = useNavigate();
  const upload = useUploadReceipt();
  const [phase, setPhase] = useState<'pending' | 'no-file' | 'uploading' | 'error'>('pending');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    (async () => {
      // Browsers that don't support Cache Storage / no SW installed:
      // pretend we got nothing and route the user to the manual
      // uploader.
      if (!('caches' in window)) {
        setPhase('no-file');
        return;
      }

      const cache = await caches.open('spendlens-shared-v1');
      const response = await cache.match(STASH_URL);
      if (!response) {
        setPhase('no-file');
        return;
      }

      // Always wipe the stash so a refresh of this page doesn't
      // re-upload the same file. Done before the upload so a slow /
      // hung upload still clears the cache entry.
      await cache.delete(STASH_URL);

      const blob = await response.blob();
      const filename =
        decodeURIComponent(response.headers.get('X-Shared-Filename') ?? '') || 'receipt';
      const file = new File([blob], filename, { type: blob.type });

      setPhase('uploading');
      upload.mutate(file, {
        onSuccess: () => navigate('/receipts', { replace: true }),
        onError: (err) => {
          setPhase('error');
          setErrorMessage(err instanceof Error ? err.message : 'Upload failed');
        },
      });
    })();
    // ``upload`` and ``navigate`` are stable references; not
    // including them keeps the effect from re-firing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section className="max-w-md mx-auto bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <h2 className="text-lg font-semibold mb-2">Receiving shared receipt…</h2>

      {phase === 'pending' && (
        <p className="text-sm text-slate-500">Reading the file you shared…</p>
      )}

      {phase === 'uploading' && <p className="text-sm text-slate-500">Uploading to SpendLens…</p>}

      {phase === 'no-file' && (
        <p className="text-sm text-slate-600">
          We didn&apos;t catch a shared file. The OS share sheet sends files via a service worker
          that&apos;s installed on first visit — try opening the app once, then sharing again. Or
          upload directly from the{' '}
          <a href="/receipts" className="text-brand-600 hover:underline">
            Receipts page
          </a>
          .
        </p>
      )}

      {phase === 'error' && (
        <p role="alert" className="text-sm text-red-600">
          Couldn&apos;t upload that file: {errorMessage}.{' '}
          <a href="/receipts" className="text-brand-600 hover:underline">
            Try uploading manually
          </a>
          .
        </p>
      )}
    </section>
  );
}
