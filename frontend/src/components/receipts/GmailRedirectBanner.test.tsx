import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { GmailRedirectBanner } from './GmailRedirectBanner';

// ``LocationProbe`` reads the current router URL into the DOM so a
// test can assert that the banner stripped the ``?gmail=...`` params
// after rendering.
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location">{location.pathname + location.search}</span>;
}

function renderAt(url: string): { invalidateSpy: ReturnType<typeof vi.fn> } {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.fn();
  // Spy on the query-client's invalidate so we can assert the
  // success path forces a refetch of the connections list. Patching
  // the instance keeps the test free of TanStack-Query internals.
  client.invalidateQueries = invalidateSpy as typeof client.invalidateQueries;

  function Wrapper({ children }: { children: ReactNode }): JSX.Element {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }

  render(
    <Wrapper>
      <GmailRedirectBanner />
      <LocationProbe />
    </Wrapper>,
  );

  return { invalidateSpy };
}

describe('GmailRedirectBanner', () => {
  it('renders nothing when no gmail query param is present', () => {
    renderAt('/receipts');
    // Banner uses role=status; absence proves it didn't fire.
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('renders a success banner and strips the param', async () => {
    const { invalidateSpy } = renderAt('/receipts?gmail=connected');

    await waitFor(() => {
      expect(screen.getByText(/Gmail connected/i)).toBeInTheDocument();
    });
    // Cache is invalidated so the connections card refetches.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['integrations', 'gmail'] });
    // URL no longer carries the param — refresh-safe.
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe('/receipts');
    });
  });

  it('renders an error banner with a known reason', async () => {
    renderAt('/receipts?gmail=error&reason=bad_state');

    await waitFor(() => {
      expect(screen.getByText(/expired or was tampered/i)).toBeInTheDocument();
    });
  });

  it('falls back to a generic message for unknown error reasons', async () => {
    renderAt('/receipts?gmail=error&reason=who_knows');

    await waitFor(() => {
      expect(screen.getByText(/Something went wrong connecting Gmail/i)).toBeInTheDocument();
    });
  });

  it('hides the banner when the dismiss button is clicked', async () => {
    renderAt('/receipts?gmail=connected');

    await waitFor(() => {
      expect(screen.getByText(/Gmail connected/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(screen.queryByRole('status')).toBeNull();
  });
});
