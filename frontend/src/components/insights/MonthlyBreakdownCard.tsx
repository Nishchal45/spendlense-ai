import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

import { useMonthlyBreakdown } from '@/api/insights';
import { categoryLabel } from '@/api/expenses';
import { formatMoney } from '@/lib/format';

// Category palette for the donut. Keep the brand-blue at the top of
// the rotation so the dominant slice is on-brand on most months;
// the rest cycles through Tailwind's mid-saturation hues so adjacent
// slices stay distinguishable on a colour-blind-friendly palette.
const CATEGORY_COLOURS = [
  '#1d4ed8', // brand blue
  '#0891b2', // cyan
  '#16a34a', // green
  '#ca8a04', // amber
  '#dc2626', // red
  '#9333ea', // purple
  '#db2777', // pink
  '#0d9488', // teal
  '#ea580c', // orange
  '#65a30d', // lime
  '#525b6e', // slate
  '#7c3aed', // violet
];

export function MonthlyBreakdownCard() {
  const { data, isPending, isError, error } = useMonthlyBreakdown();

  return (
    <section className="bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <header className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">This month</h3>
        <p className="text-xs text-slate-500">
          Spending by category for the current calendar month.
        </p>
      </header>

      {isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {isError && (
        <p role="alert" className="text-sm text-red-600">
          Couldn&apos;t load breakdown: {(error as Error).message}
        </p>
      )}
      {data && data.items.length === 0 && (
        <p className="text-sm text-slate-500">No spending recorded this month yet.</p>
      )}
      {data && data.items.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-[200px_1fr] gap-6 items-start">
          <div className="h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data.items}
                  dataKey={(item) => Number(item.total)}
                  nameKey="category"
                  innerRadius={50}
                  outerRadius={80}
                  paddingAngle={1}
                  // Recharts' default labels overlap on small donuts.
                  // Suppress and let the legend table do the work.
                  label={false}
                >
                  {data.items.map((item, index) => (
                    <Cell
                      key={item.category}
                      fill={CATEGORY_COLOURS[index % CATEGORY_COLOURS.length]}
                    />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value: number | string) => formatMoney(String(value), 'USD')}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>

          <table className="w-full text-sm">
            <thead className="text-left text-slate-500 text-xs uppercase tracking-wide">
              <tr>
                <th className="pb-2 font-medium">Category</th>
                <th className="pb-2 font-medium text-right">Total</th>
                <th className="pb-2 font-medium text-right">Count</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item, index) => (
                <tr key={item.category} className="border-t border-slate-100">
                  <td className="py-2 flex items-center gap-2">
                    <span
                      aria-hidden
                      className="w-2.5 h-2.5 rounded-sm"
                      style={{
                        backgroundColor: CATEGORY_COLOURS[index % CATEGORY_COLOURS.length],
                      }}
                    />
                    {categoryLabel(item.category)}
                  </td>
                  <td className="py-2 font-mono text-right">{formatMoney(item.total, 'USD')}</td>
                  <td className="py-2 text-right text-slate-500">{item.count}</td>
                </tr>
              ))}
              <tr className="border-t border-slate-200 font-medium">
                <td className="py-2">Total</td>
                <td className="py-2 font-mono text-right">
                  {formatMoney(data.grand_total, 'USD')}
                </td>
                <td className="py-2 text-right text-slate-500">{data.grand_count}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
