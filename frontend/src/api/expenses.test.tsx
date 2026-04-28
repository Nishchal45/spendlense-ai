import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { useCreateExpense, useDeleteExpense, useExpenses } from './expenses';

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

function withClient(): { wrapper: ({ children }: { children: ReactNode }) => JSX.Element } {
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

describe('useExpenses query string', () => {
  it("omits filters that aren't set", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }));
    const { wrapper } = withClient();
    renderHook(() => useExpenses({}), { wrapper });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toMatch(/\/expenses$/); // no ``?`` suffix
  });

  it('includes only the filters that are set', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }));
    const { wrapper } = withClient();
    renderHook(
      () =>
        useExpenses({
          category: 'groceries',
          merchant: 'whole foods',
          date_from: '2026-04-01',
        }),
      { wrapper },
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain('category=groceries');
    expect(url).toContain('merchant=whole%20foods');
    expect(url).toContain('date_from=2026-04-01');
    expect(url).not.toContain('date_to=');
  });
});

describe('useCreateExpense', () => {
  it('POSTs the payload as JSON', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          id: '1',
          user_id: 'u1',
          merchant_name: 'Acme',
          amount: '1.00',
          currency: 'USD',
          category: 'other',
          expense_date: '2026-04-28',
          description: null,
          source: 'manual',
          receipt_id: null,
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        },
        201,
      ),
    );
    const { wrapper } = withClient();
    const { result } = renderHook(() => useCreateExpense(), { wrapper });

    result.current.mutate({
      merchant_name: 'Acme',
      amount: '1.00',
      currency: 'USD',
      category: 'other',
      expense_date: '2026-04-28',
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.body).toContain('"merchant_name":"Acme"');
  });
});

describe('useDeleteExpense', () => {
  it('issues a DELETE to the /expenses/{id} path', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const { wrapper } = withClient();
    const { result } = renderHook(() => useDeleteExpense(), { wrapper });

    result.current.mutate('expense-id-123');
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const url = fetchMock.mock.calls[0]?.[0] as string;
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(url).toContain('/expenses/expense-id-123');
    expect(init.method).toBe('DELETE');
  });
});
