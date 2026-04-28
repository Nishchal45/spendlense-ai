import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ShareTargetPage } from './ShareTargetPage';

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

// Minimal Cache Storage stub. jsdom doesn't ship one, so we hand-roll
// the surface the page actually uses: ``caches.open(name).match(url)``
// and ``cache.delete(url)``. Each test seeds the entry it wants.
class FakeCache {
  private store = new Map<string, Response>();

  async match(url: string): Promise<Response | undefined> {
    return this.store.get(url);
  }

  async put(url: string, response: Response): Promise<void> {
    this.store.set(url, response);
  }

  async delete(url: string): Promise<boolean> {
    return this.store.delete(url);
  }
}

class FakeCaches {
  private caches = new Map<string, FakeCache>();

  async open(name: string): Promise<FakeCache> {
    let cache = this.caches.get(name);
    if (!cache) {
      cache = new FakeCache();
      this.caches.set(name, cache);
    }
    return cache;
  }
}

let fakeCaches: FakeCaches;

beforeEach(() => {
  fakeCaches = new FakeCaches();
  // Stub only ``caches`` per-test. Calling ``vi.unstubAllGlobals``
  // here would tear down the file-scoped ``fetch`` stub too, which
  // races with sibling test files.
  vi.stubGlobal('caches', fakeCaches);
});

afterEach(() => {
  fetchMock.mockReset();
  // Replace the caches stub with an undefined to simulate "not
  // present" between tests rather than nuking every global stub.
  vi.stubGlobal('caches', undefined);
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/share-target']}>
        <Routes>
          <Route path="/share-target" element={<ShareTargetPage />} />
          <Route path="/receipts" element={<p>receipts page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function jsonResponse<T>(body: T, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('ShareTargetPage', () => {
  it('shows the no-file fallback when nothing is stashed', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/We didn't catch a shared file/i)).toBeInTheDocument();
    });
  });

  it('uploads a stashed file and redirects to /receipts', async () => {
    // Seed the cache with what the SW would have stored.
    const cache = await fakeCaches.open('spendlens-shared-v1');
    await cache.put(
      '/__shared-receipt',
      new Response('fake-jpeg-bytes', {
        headers: {
          'Content-Type': 'image/jpeg',
          'X-Shared-Filename': encodeURIComponent('IMG_4242.jpg'),
        },
      }),
    );

    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          id: 'r1',
          user_id: 'u1',
          mime_type: 'image/jpeg',
          file_size_bytes: 16,
          status: 'uploaded',
          ocr_method: null,
          ocr_confidence: null,
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        },
        201,
      ),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('receipts page')).toBeInTheDocument();
    });

    // Verify the upload went through with the right multipart body.
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);

    // Cache entry should be wiped so a refresh doesn't re-upload.
    const after = await cache.match('/__shared-receipt');
    expect(after).toBeUndefined();
  });

  it('shows an error when the upload fails', async () => {
    const cache = await fakeCaches.open('spendlens-shared-v1');
    await cache.put(
      '/__shared-receipt',
      new Response('bytes', {
        headers: { 'Content-Type': 'application/pdf' },
      }),
    );

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'too big' }), {
        status: 413,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/Couldn't upload/i);
  });
});
