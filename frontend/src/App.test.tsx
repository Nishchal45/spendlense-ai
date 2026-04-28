import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { App } from './App';

// Smoke test — renders the shell + a placeholder child route under
// fresh QueryClient + MemoryRouter so we never touch the real
// network. As pages get added, each gets its own narrowly-focused
// test; this one stays minimal.
describe('App shell', () => {
  it('renders the brand mark and footer', () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <Routes>
            <Route path="/" element={<App />}>
              <Route index element={<p>child</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText('SpendLens')).toBeInTheDocument();
    expect(screen.getByText(/Phase 7/)).toBeInTheDocument();
    expect(screen.getByText('child')).toBeInTheDocument();
  });
});
