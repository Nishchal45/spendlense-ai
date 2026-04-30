import { useQuery } from '@tanstack/react-query';

import type { ExpenseCategory } from './expenses';
import { apiFetch } from './client';

// Wire types mirror ``backend/app/schemas/insights.py`` and
// ``schemas/budget.py``. Decimals serialise as strings on the wire
// (same convention as expenses) — keep them as strings here and
// coerce at the render boundary so JS floats don't eat precision
// before the user sees the number.

// ----- monthly breakdown ---------------------------------------------------

export interface CategoryTotal {
  category: ExpenseCategory;
  total: string;
  count: number;
  average: string;
}

export interface MonthlyBreakdown {
  month: string; // ISO date — first of the month
  grand_total: string;
  grand_count: number;
  items: CategoryTotal[];
}

export function useMonthlyBreakdown(month?: string) {
  // ``month`` is the optional query param. We pass it through as-is —
  // the backend accepts any date inside the target month.
  const search = month ? `?month=${encodeURIComponent(month)}` : '';
  return useQuery<MonthlyBreakdown>({
    queryKey: ['insights', 'monthly', month ?? 'today'],
    queryFn: () => apiFetch<MonthlyBreakdown>(`/insights/monthly${search}`),
  });
}

// ----- trends --------------------------------------------------------------

export interface TrendBucket {
  month: string;
  category: ExpenseCategory;
  total: string;
}

export interface CategoryTrends {
  months: string[];
  categories: ExpenseCategory[];
  buckets: TrendBucket[];
}

export function useCategoryTrends(months: number = 12) {
  return useQuery<CategoryTrends>({
    queryKey: ['insights', 'trends', months],
    queryFn: () => apiFetch<CategoryTrends>(`/insights/trends?months=${months}`),
  });
}

// Pivot the dense (month × category) bucket grid into the row-per-
// month shape Recharts wants for stacked bars. One row looks like::
//
//   { month: "2026-04", food_dining: "12.50", groceries: "84.20", ... }
//
// Categories with no spend in any month are dropped at the page
// layer so the chart legend stays readable.
export function pivotTrendsForChart(trends: CategoryTrends): Array<Record<string, string>> {
  const byMonth: Record<string, Record<string, string>> = {};
  for (const month of trends.months) {
    byMonth[month] = { month };
  }
  for (const bucket of trends.buckets) {
    // ``byMonth[bucket.month]`` is guaranteed populated by the loop
    // above — the dense grid invariant means every bucket's month
    // is in ``trends.months``.
    byMonth[bucket.month]![bucket.category] = bucket.total;
  }
  return trends.months.map((m) => byMonth[m]!);
}

// ----- anomalies ----------------------------------------------------------

export interface Anomaly {
  expense_id: string;
  merchant_name: string;
  category: ExpenseCategory;
  amount: string;
  expense_date: string;
  z_score: number;
  baseline_mean: string;
  baseline_stddev: string;
  baseline_samples: number;
}

export interface AnomalyReport {
  lookback_start: string;
  baseline_start: string;
  z_threshold: number;
  anomalies: Anomaly[];
}

export function useAnomalies() {
  return useQuery<AnomalyReport>({
    queryKey: ['insights', 'anomalies'],
    queryFn: () => apiFetch<AnomalyReport>('/insights/anomalies'),
  });
}

// ----- budget status -------------------------------------------------------

export type BudgetPeriod = 'monthly';

export interface BudgetStatusItem {
  budget_id: string;
  category: ExpenseCategory;
  period: BudgetPeriod;
  amount: string;
  spent: string;
  remaining: string;
  ratio: number;
  alert_threshold_pct: number;
  alert_triggered: boolean;
  period_start: string;
  period_end: string;
}

export interface BudgetStatusReport {
  today: string;
  items: BudgetStatusItem[];
}

export function useBudgetStatus() {
  return useQuery<BudgetStatusReport>({
    queryKey: ['budgets', 'status'],
    queryFn: () => apiFetch<BudgetStatusReport>('/budgets/status'),
  });
}
