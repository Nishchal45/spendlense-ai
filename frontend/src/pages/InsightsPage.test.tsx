import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { InsightsPage } from './InsightsPage';

const fetchMock = vi.fn<typeof fetch>();
vi.stubGlobal('fetch', fetchMock);

afterEach(() => {
  fetchMock.mockReset();
});

function jsonResponse<T>(body: T): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <InsightsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Each card hits a different endpoint; the page mounts all four
// simultaneously. We seed every endpoint with a useful empty / zero
// state so the page renders without retry storms.
function seedEmptyEndpoints() {
  fetchMock.mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url.includes('/insights/monthly')) {
      return jsonResponse({
        month: '2026-04-01',
        grand_total: '0.00',
        grand_count: 0,
        items: [],
      });
    }
    if (url.includes('/insights/trends')) {
      return jsonResponse({
        months: [],
        categories: [],
        buckets: [],
      });
    }
    if (url.includes('/insights/anomalies')) {
      return jsonResponse({
        lookback_start: '2026-04-01',
        baseline_start: '2025-10-30',
        z_threshold: 2.0,
        anomalies: [],
      });
    }
    if (url.includes('/budgets/status')) {
      return jsonResponse({
        today: '2026-04-30',
        items: [],
      });
    }
    return new Response('not stubbed', { status: 500 });
  });
}

describe('InsightsPage', () => {
  it('renders the four card headers', async () => {
    seedEmptyEndpoints();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('This month')).toBeInTheDocument();
    });
    expect(screen.getByText('12-month trend')).toBeInTheDocument();
    expect(screen.getByText('Unusual spending')).toBeInTheDocument();
    expect(screen.getByText('Budgets')).toBeInTheDocument();
  });

  it('shows the empty state when nothing is spent yet', async () => {
    seedEmptyEndpoints();
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No spending recorded this month yet/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Nothing unusual this month/i)).toBeInTheDocument();
    expect(screen.getByText(/No active budgets/i)).toBeInTheDocument();
  });

  it('renders the budget progress bars when budgets exist', async () => {
    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/insights/monthly')) {
        return jsonResponse({
          month: '2026-04-01',
          grand_total: '0.00',
          grand_count: 0,
          items: [],
        });
      }
      if (url.includes('/insights/trends')) {
        return jsonResponse({ months: [], categories: [], buckets: [] });
      }
      if (url.includes('/insights/anomalies')) {
        return jsonResponse({
          lookback_start: '2026-04-01',
          baseline_start: '2025-10-30',
          z_threshold: 2.0,
          anomalies: [],
        });
      }
      if (url.includes('/budgets/status')) {
        return jsonResponse({
          today: '2026-04-30',
          items: [
            {
              budget_id: 'b1',
              category: 'food_dining',
              period: 'monthly',
              amount: '200.00',
              spent: '180.00',
              remaining: '20.00',
              ratio: 0.9,
              alert_threshold_pct: 80,
              alert_triggered: true,
              period_start: '2026-04-01',
              period_end: '2026-05-01',
            },
          ],
        });
      }
      return new Response('not stubbed', { status: 500 });
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
    });
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '90');
    // Alert copy fires when threshold is crossed but ratio < 1.
    expect(screen.getByText(/Past your 80% alert threshold/i)).toBeInTheDocument();
  });

  it('renders an anomaly row with the z-score multiplier', async () => {
    fetchMock.mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/insights/monthly')) {
        return jsonResponse({
          month: '2026-04-01',
          grand_total: '0.00',
          grand_count: 0,
          items: [],
        });
      }
      if (url.includes('/insights/trends')) {
        return jsonResponse({ months: [], categories: [], buckets: [] });
      }
      if (url.includes('/insights/anomalies')) {
        return jsonResponse({
          lookback_start: '2026-04-01',
          baseline_start: '2025-10-30',
          z_threshold: 2.0,
          anomalies: [
            {
              expense_id: 'a1',
              merchant_name: 'Suspicious Latte',
              category: 'food_dining',
              amount: '60.00',
              expense_date: '2026-04-25',
              z_score: 3.4,
              baseline_mean: '5.00',
              baseline_stddev: '0.50',
              baseline_samples: 12,
            },
          ],
        });
      }
      if (url.includes('/budgets/status')) {
        return jsonResponse({ today: '2026-04-30', items: [] });
      }
      return new Response('not stubbed', { status: 500 });
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Suspicious Latte')).toBeInTheDocument();
    });
    expect(screen.getByText(/3.4× the baseline/i)).toBeInTheDocument();
  });
});
