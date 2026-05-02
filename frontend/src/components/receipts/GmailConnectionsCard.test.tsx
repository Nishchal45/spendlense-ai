import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { GmailConnectionsCard } from './GmailConnectionsCard';

// Mocks every ``fetch`` the component fires. Tests are explicit
// about which URL gets which response — same pattern as the inbox-
// address tests, scaled to the integrations endpoint surface.

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

// ``window.location.assign`` is what the connect button calls. We
// stub the whole ``location`` getter so JSDOM's read-only default
// doesn't blow up when the component tries to navigate.
const assignMock = vi.fn<(url: string) => void>();
Object.defineProperty(window, 'location', {
  value: { assign: assignMock },
  writable: true,
});

afterEach(() => {
  fetchMock.mockReset();
  assignMock.mockReset();
});

function jsonResponse<T>(body: T, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function withQueryClient(): {
  wrapper: (props: { children: ReactNode }) => JSX.Element;
} {
  // Fresh client per test so cache state doesn't leak across cases.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

const SAMPLE_CONNECTION = {
  id: 'conn-1',
  google_email: 'alice@gmail.com',
  last_history_id: '12345',
  watch_expiration: null,
  created_at: '2026-04-28T00:00:00Z',
  updated_at: '2026-04-28T00:00:00Z',
};

describe('GmailConnectionsCard', () => {
  it('renders an empty state when no connections exist', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [] }));
    render(<GmailConnectionsCard />, withQueryClient());

    await waitFor(() => {
      expect(screen.getByText(/No Gmail accounts connected/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /connect gmail/i })).toBeInTheDocument();
  });

  it('lists each connection with a disconnect button', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        items: [
          SAMPLE_CONNECTION,
          { ...SAMPLE_CONNECTION, id: 'conn-2', google_email: 'work@gmail.com' },
        ],
      }),
    );
    render(<GmailConnectionsCard />, withQueryClient());

    await waitFor(() => {
      expect(screen.getByText('alice@gmail.com')).toBeInTheDocument();
    });
    expect(screen.getByText('work@gmail.com')).toBeInTheDocument();
    // Two list rows = two disconnect buttons.
    expect(screen.getAllByRole('button', { name: /disconnect/i })).toHaveLength(2);
  });

  it('navigates to the consent URL when Connect Gmail is clicked', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      // Second call: the ``/connect`` endpoint returns the URL.
      .mockResolvedValueOnce(
        jsonResponse({ url: 'https://accounts.google.com/o/oauth2/v2/auth?stub=1' }),
      );
    render(<GmailConnectionsCard />, withQueryClient());

    await waitFor(() => {
      expect(screen.getByText(/No Gmail accounts/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /connect gmail/i }));

    await waitFor(() => {
      expect(assignMock).toHaveBeenCalledWith(
        'https://accounts.google.com/o/oauth2/v2/auth?stub=1',
      );
    });
  });

  it('shows an error message when the consent endpoint 503s', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ detail: 'not configured' }, 503));
    render(<GmailConnectionsCard />, withQueryClient());

    await waitFor(() => {
      expect(screen.getByText(/No Gmail accounts/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /connect gmail/i }));

    await waitFor(() => {
      expect(screen.getByText(/Could not start the Gmail connect flow/i)).toBeInTheDocument();
    });
    // The browser is not redirected on a failed start.
    expect(assignMock).not.toHaveBeenCalled();
  });

  it('removes a row when its disconnect button succeeds', async () => {
    // Initial list → connection. Then DELETE → 204. Then refetch
    // after invalidation → empty list.
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ items: [SAMPLE_CONNECTION] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }));

    render(<GmailConnectionsCard />, withQueryClient());

    await waitFor(() => {
      expect(screen.getByText('alice@gmail.com')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /disconnect/i }));

    await waitFor(() => {
      expect(screen.getByText(/No Gmail accounts connected/i)).toBeInTheDocument();
    });
  });

  it('surfaces a load error from the connections endpoint', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'unauthorized' }, 401));
    render(<GmailConnectionsCard />, withQueryClient());

    await waitFor(() => {
      expect(screen.getByText(/Could not load Gmail connections/i)).toBeInTheDocument();
    });
  });
});
