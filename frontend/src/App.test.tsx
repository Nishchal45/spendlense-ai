import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { App } from './App';
import { AuthProvider } from './auth/AuthContext';
import { _resetAuthStore } from './auth/authStore';

// Stub fetch — the App shell itself doesn't call any endpoints, but
// ``AuthProvider`` will trigger ``GET /auth/me`` if a token gets set.
// Pinning fetch keeps the smoke test offline.
const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
  _resetAuthStore();
});

// Smoke test — renders the shell + a placeholder child route under
// fresh QueryClient + AuthProvider + MemoryRouter so we never touch
// the real network. As pages get added, each gets its own narrowly-
// focused test; this one stays minimal.
describe('App shell', () => {
  it('renders the brand mark and footer', () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <AuthProvider>
          <MemoryRouter>
            <Routes>
              <Route path="/" element={<App />}>
                <Route index element={<p>child</p>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>,
    );

    expect(screen.getByText('SpendLens')).toBeInTheDocument();
    expect(screen.getByText(/Phase 7/)).toBeInTheDocument();
    expect(screen.getByText('child')).toBeInTheDocument();
  });
});
