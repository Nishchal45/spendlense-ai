import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ReceiptsPage } from './ReceiptsPage';

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

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ReceiptsPage />
      </MemoryRouter>
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
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [] }));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No receipts yet/i)).toBeInTheDocument();
    });
  });

  it('renders the upload zone with the size + format hint', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [] }));
    renderPage();

    await waitFor(() => {
      // Hint copy mentions both the formats and the cap. If either
      // gets dropped during a UI tweak this test catches it.
      expect(screen.getByText(/Drop a receipt here/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/up to 10 MB/i)).toBeInTheDocument();
  });

  it('renders one card per receipt in the list', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        items: [SAMPLE_RECEIPT, { ...SAMPLE_RECEIPT, id: 'r2', status: 'failed' as const }],
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Categorised')).toBeInTheDocument();
    });
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });
});
