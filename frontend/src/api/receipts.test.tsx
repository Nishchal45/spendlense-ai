import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { useUploadReceipt, isInFlight } from './receipts';

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

function withClient() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

function jsonResponse<T>(body: T, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('isInFlight', () => {
  it('treats categorised + failed as terminal', () => {
    expect(isInFlight('categorised')).toBe(false);
    expect(isInFlight('failed')).toBe(false);
  });

  it('treats early states as in-flight', () => {
    expect(isInFlight('uploaded')).toBe(true);
    expect(isInFlight('processing')).toBe(true);
    expect(isInFlight('parsed')).toBe(true);
  });
});

describe('useUploadReceipt', () => {
  it('POSTs FormData without forcing application/json', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          id: 'r1',
          user_id: 'u1',
          mime_type: 'image/jpeg',
          file_size_bytes: 4,
          status: 'uploaded',
          ocr_method: null,
          ocr_confidence: null,
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        },
        201,
      ),
    );
    const { wrapper } = withClient();
    const { result } = renderHook(() => useUploadReceipt(), { wrapper });

    const file = new File([new Uint8Array([1, 2, 3, 4])], 'r.jpg', { type: 'image/jpeg' });
    result.current.mutate(file);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);

    // Critically: the wrapper must NOT have set ``Content-Type`` —
    // ``fetch`` will set ``multipart/form-data; boundary=...`` itself,
    // but only when no caller-supplied content type already exists.
    const headers = new Headers(init.headers);
    expect(headers.get('Content-Type')).toBeNull();
  });
});
