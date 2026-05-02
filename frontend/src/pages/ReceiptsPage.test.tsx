import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AuthContext, type AuthContextValue } from '@/auth/AuthContext';
import { ReceiptsPage } from './ReceiptsPage';

// Auth-context stub so the page's ``InboxAddressCard`` can render
// without the real ``AuthProvider`` (which would fire its own
// ``/auth/me`` query and complicate the fetch mocks).
const AUTHED: AuthContextValue = {
  token: 'tok',
  user: {
    id: 'u1',
    email: 'me@example.com',
    created_at: '2026-04-30T00:00:00Z',
    inbox_token: '0'.repeat(32),
    inbox_address: `receipts+${'0'.repeat(32)}@inbox.spendlens.local`,
  },
  isLoading: false,
  logout: vi.fn(),
};

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

function jsonResponse<T>(body: T, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

// The page fires queries against several endpoints in parallel
// (receipts list + Gmail connections). Routing the mock by URL
// substring keeps the test resilient to query ordering — without
// this, the receipts mock would race the Gmail mock and either
// could win.
function routeFetchByUrl(routes: Record<string, () => Response>): void {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = input instanceof URL ? input.pathname : String(input);
    for (const [path, response] of Object.entries(routes)) {
      if (url.includes(path)) return response();
    }
    return new Response(JSON.stringify({ detail: `unmocked ${url}` }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  });
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={AUTHED}>
        <MemoryRouter>
          <ReceiptsPage />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  );
}

const SAMPLE_RECEIPT = {
  id: 'r1',
  user_id: 'u1',
  mime_type: 'image/jpeg',
  file_size_bytes: 12_345,
  status: 'categorised' as const,
  ocr_method: 'tesseract' as const,
  ocr_confidence: '78.50',
  created_at: '2026-04-28T00:00:00Z',
  updated_at: '2026-04-28T00:00:01Z',
};

describe('ReceiptsPage', () => {
  it('shows the empty-state when no receipts exist', async () => {
    routeFetchByUrl({
      '/receipts': () => jsonResponse({ items: [] }),
      '/integrations/gmail': () => jsonResponse({ items: [] }),
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No receipts yet/i)).toBeInTheDocument();
    });
  });

  it('renders the upload zone with the size + format hint', async () => {
    routeFetchByUrl({
      '/receipts': () => jsonResponse({ items: [] }),
      '/integrations/gmail': () => jsonResponse({ items: [] }),
    });
    renderPage();

    await waitFor(() => {
      // Hint copy mentions both the formats and the cap. If either
      // gets dropped during a UI tweak this test catches it.
      expect(screen.getByText(/Drop a receipt here/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/up to 10 MB/i)).toBeInTheDocument();
  });

  it('renders one card per receipt in the list', async () => {
    routeFetchByUrl({
      '/receipts': () =>
        jsonResponse({
          items: [SAMPLE_RECEIPT, { ...SAMPLE_RECEIPT, id: 'r2', status: 'failed' as const }],
        }),
      '/integrations/gmail': () => jsonResponse({ items: [] }),
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Categorised')).toBeInTheDocument();
    });
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });
});
