import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { userEvent } from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ExpensesPage } from './ExpensesPage';

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

// Native ``<dialog>`` isn't implemented in jsdom yet — every test
// that opens the form would crash on ``showModal``. Stub the two
// methods we actually call so the dialog component renders without
// blowing up. The visual modal behaviour is a browser-runtime
// concern, not a unit-test concern.
beforeEach(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute('open');
    };
  }
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
        <ExpensesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SAMPLE_EXPENSE = {
  id: 'exp-1',
  user_id: 'u1',
  merchant_name: 'Blue Bottle',
  amount: '4.75',
  currency: 'USD',
  category: 'food_dining',
  expense_date: '2026-04-25',
  description: null,
  source: 'manual',
  receipt_id: null,
  created_at: '2026-04-25T00:00:00Z',
  updated_at: '2026-04-25T00:00:00Z',
};

describe('ExpensesPage', () => {
  it('renders the merchant + amount once data loads', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [SAMPLE_EXPENSE], next_cursor: null }));

    renderPage();

    expect(screen.getByText('Loading expenses…')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('Blue Bottle')).toBeInTheDocument();
    });
    expect(screen.getByText(/4\.75/)).toBeInTheDocument();
  });

  it('shows the empty-state when no expenses match', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No expenses match these filters/i)).toBeInTheDocument();
    });
  });

  it('shows a "Load more" button when next_cursor is present', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [SAMPLE_EXPENSE], next_cursor: 'cursor-xyz' }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
    });
  });

  it('opens the create dialog when "New expense" is clicked', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No expenses match/i)).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /new expense/i }));
    expect(screen.getByText('New expense', { selector: 'h2' })).toBeInTheDocument();
  });
});
