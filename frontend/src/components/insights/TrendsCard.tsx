import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { pivotTrendsForChart, useCategoryTrends } from '@/api/insights';
import { categoryLabel } from '@/api/expenses';

// Same palette as the donut so a category renders the same colour
// across the page. ``CATEGORY_COLOURS`` lives here too rather than
// in a shared module — colour-binding is a per-component concern
// and pulling it into a shared file would be a premature DRY.
const CATEGORY_COLOURS = [
  '#1d4ed8',
  '#0891b2',
  '#16a34a',
  '#ca8a04',
  '#dc2626',
  '#9333ea',
  '#db2777',
  '#0d9488',
  '#ea580c',
  '#65a30d',
  '#525b6e',
  '#7c3aed',
];

function formatMonthTick(iso: string): string {
  // "2026-04-01" → "Apr". The dashboard never crosses years on a
  // single chart, so the year suffix is noise.
  const date = new Date(iso);
  return date.toLocaleString('en', { month: 'short' });
}

export function TrendsCard() {
  const { data, isPending, isError, error } = useCategoryTrends(12);

  return (
    <section className="bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <header className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">12-month trend</h3>
        <p className="text-xs text-slate-500">Stacked monthly spending by category.</p>
      </header>

      {isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {isError && (
        <p role="alert" className="text-sm text-red-600">
          Couldn&apos;t load trends: {(error as Error).message}
        </p>
      )}
      {data && data.categories.length === 0 && (
        <p className="text-sm text-slate-500">
          Once you have spending across a couple of months, this chart will fill in.
        </p>
      )}
      {data && data.categories.length > 0 && (
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={pivotTrendsForChart(data)}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis
                dataKey="month"
                tickFormatter={formatMonthTick}
                stroke="#64748b"
                fontSize={12}
              />
              <YAxis stroke="#64748b" fontSize={12} />
              <Tooltip
                labelFormatter={formatMonthTick}
                formatter={(value: number | string, name: string) => [
                  `$${Number(value).toFixed(2)}`,
                  categoryLabel(name as never),
                ]}
              />
              <Legend formatter={(value: string) => categoryLabel(value as never)} />
              {data.categories.map((category, index) => (
                <Bar
                  key={category}
                  dataKey={category}
                  stackId="spend"
                  fill={CATEGORY_COLOURS[index % CATEGORY_COLOURS.length]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
