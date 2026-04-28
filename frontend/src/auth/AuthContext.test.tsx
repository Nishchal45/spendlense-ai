import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AuthProvider } from './AuthContext';
import { _resetAuthStore, setAuthToken } from './authStore';
import { useAuth } from './useAuth';

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
  _resetAuthStore();
});

function renderWithProvider(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>{ui}</AuthProvider>
    </QueryClientProvider>,
  );
}

function Probe() {
  // Surface every interesting field so each assertion has a stable
  // anchor without needing a richer mock UI.
  const { token, user, isLoading, logout } = useAuth();
  return (
    <div>
      <span data-testid="token">{token ?? 'null'}</span>
      <span data-testid="email">{user?.email ?? 'none'}</span>
      <span data-testid="loading">{isLoading ? 'yes' : 'no'}</span>
      <button onClick={logout}>logout</button>
    </div>
  );
}

describe('AuthContext', () => {
  it('starts with no token and no user', () => {
    renderWithProvider(<Probe />);
    expect(screen.getByTestId('token')).toHaveTextContent('null');
    expect(screen.getByTestId('email')).toHaveTextContent('none');
  });

  it('reflects a token already in the store at mount time', async () => {
    setAuthToken('pre-existing');
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ id: 'u1', email: 'a@b.co', created_at: '2026-01-01T00:00:00Z' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    renderWithProvider(<Probe />);

    expect(screen.getByTestId('token')).toHaveTextContent('pre-existing');
    await waitFor(() => {
      expect(screen.getByTestId('email')).toHaveTextContent('a@b.co');
    });
  });

  it('logout clears the token', async () => {
    setAuthToken('to-be-cleared');
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ id: 'u1', email: 'a@b.co', created_at: '2026-01-01T00:00:00Z' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    renderWithProvider(<Probe />);
    await waitFor(() => {
      expect(screen.getByTestId('token')).toHaveTextContent('to-be-cleared');
    });

    act(() => {
      screen.getByText('logout').click();
    });

    expect(screen.getByTestId('token')).toHaveTextContent('null');
  });

  it('reacts to a store change made outside React (e.g. 401)', async () => {
    setAuthToken('living');
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ id: 'u1', email: 'a@b.co', created_at: '2026-01-01T00:00:00Z' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    renderWithProvider(<Probe />);

    act(() => {
      // Simulate the API client wiping the token after a 401 — the
      // tree must update without us re-rendering by hand.
      setAuthToken(null);
    });

    expect(screen.getByTestId('token')).toHaveTextContent('null');
  });
});
