import { useBudgetStatus } from '@/api/insights';
import { categoryLabel } from '@/api/expenses';
import { formatMoney } from '@/lib/format';

// One progress bar per active budget. Three colour states:
//
// * green    — under threshold (healthy)
// * amber    — alert_threshold_pct crossed but still under 100%
// * red      — over budget (>= 100%)
//
// The bar caps visually at 100% but the percentage label keeps
// climbing — a 150% row reads as "150%" even though the fill stops
// at the right edge. The clamp lives here, not in the API, so the
// service can stay numerically honest.
function progressColour(ratio: number, alertTriggered: boolean): string {
  if (ratio >= 1) return 'bg-red-500';
  if (alertTriggered) return 'bg-amber-500';
  return 'bg-emerald-500';
}

export function BudgetsCard() {
  const { data, isPending, isError, error } = useBudgetStatus();

  return (
    <section className="bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <header className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">Budgets</h3>
        <p className="text-xs text-slate-500">Spend vs. budget for the current month.</p>
      </header>

      {isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {isError && (
        <p role="alert" className="text-sm text-red-600">
          Couldn&apos;t load budgets: {(error as Error).message}
        </p>
      )}
      {data && data.items.length === 0 && (
        <p className="text-sm text-slate-500">
          No active budgets. Set one up from the budgets page to track a category.
        </p>
      )}
      {data && data.items.length > 0 && (
        <ul className="space-y-4">
          {data.items.map((item) => {
            const fillPct = Math.min(item.ratio * 100, 100);
            const labelPct = Math.round(item.ratio * 100);
            return (
              <li key={item.budget_id}>
                <div className="flex items-baseline justify-between mb-1">
                  <span className="text-sm font-medium text-slate-900">
                    {categoryLabel(item.category)}
                  </span>
                  <span className="text-xs text-slate-500">
                    {formatMoney(item.spent, 'USD')} of {formatMoney(item.amount, 'USD')} (
                    {labelPct}%)
                  </span>
                </div>
                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    role="progressbar"
                    aria-valuenow={labelPct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${categoryLabel(item.category)} budget progress`}
                    className={`h-full ${progressColour(item.ratio, item.alert_triggered)}`}
                    style={{ width: `${fillPct}%` }}
                  />
                </div>
                {item.alert_triggered && (
                  <p className="mt-1 text-xs text-amber-700">
                    {item.ratio >= 1
                      ? 'Over budget'
                      : `Past your ${item.alert_threshold_pct}% alert threshold`}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
