import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiFetch } from './client';

// Fetch is patched per-test so we never actually hit a network.
// Vitest's automatic restore wipes the spy in ``afterEach`` so test
// ordering can't leak a stub.
const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

describe('apiFetch', () => {
  it('parses JSON responses', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const result = await apiFetch<{ status: string }>('/health');
    expect(result).toEqual({ status: 'ok' });
  });

  it('returns undefined on 204', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const result = await apiFetch<void>('/expenses/abc');
    expect(result).toBeUndefined();
  });

  it('throws ApiError with status + body on non-OK', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'forbidden' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(apiFetch('/private')).rejects.toMatchObject({
      name: 'ApiError',
      status: 403,
      body: { detail: 'forbidden' },
    });
  });

  it('sets JSON Content-Type when a body is provided', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200 }));

    await apiFetch('/expenses', { method: 'POST', body: JSON.stringify({ x: 1 }) });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
  });

  it('exposes ApiError as a real Error subclass', () => {
    // Important for ``instanceof`` checks in components — we need
    // these to survive minification / source-map round trips.
    const err = new ApiError(404, null, 'gone');
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ApiError);
  });
});
