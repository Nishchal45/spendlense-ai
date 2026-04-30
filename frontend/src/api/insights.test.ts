import { describe, expect, it } from 'vitest';

import { pivotTrendsForChart, type CategoryTrends } from './insights';

describe('pivotTrendsForChart', () => {
  it('returns one row per month with category columns', () => {
    const trends: CategoryTrends = {
      months: ['2026-01-01', '2026-02-01', '2026-03-01'],
      categories: ['food_dining', 'groceries'],
      buckets: [
        { month: '2026-01-01', category: 'food_dining', total: '10.00' },
        { month: '2026-01-01', category: 'groceries', total: '50.00' },
        { month: '2026-02-01', category: 'food_dining', total: '0.00' },
        { month: '2026-02-01', category: 'groceries', total: '0.00' },
        { month: '2026-03-01', category: 'food_dining', total: '12.50' },
        { month: '2026-03-01', category: 'groceries', total: '40.00' },
      ],
    };

    const rows = pivotTrendsForChart(trends);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toEqual({
      month: '2026-01-01',
      food_dining: '10.00',
      groceries: '50.00',
    });
    expect(rows[2]).toEqual({
      month: '2026-03-01',
      food_dining: '12.50',
      groceries: '40.00',
    });
  });

  it('preserves month order from trends.months', () => {
    // Even if buckets are out of order, the pivoted rows must follow
    // the canonical ``trends.months`` sequence — Recharts renders in
    // input order.
    const trends: CategoryTrends = {
      months: ['2026-01-01', '2026-02-01'],
      categories: ['food_dining'],
      buckets: [
        { month: '2026-02-01', category: 'food_dining', total: '5.00' },
        { month: '2026-01-01', category: 'food_dining', total: '3.00' },
      ],
    };
    const rows = pivotTrendsForChart(trends);
    expect(rows[0]?.month).toBe('2026-01-01');
    expect(rows[1]?.month).toBe('2026-02-01');
  });
});
