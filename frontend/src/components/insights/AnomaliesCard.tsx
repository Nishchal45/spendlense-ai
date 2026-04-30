import { useAnomalies } from '@/api/insights';
import { categoryLabel } from '@/api/expenses';
import { formatDate, formatMoney } from '@/lib/format';

// "Recent unusual activity" — the smart-feature beat that turns the
// dashboard from a passive log into something that calls out
// what's worth a second look. Empty state is deliberately positive
// ("nothing unusual"), not "no data" — the most common reason
// nothing renders is that the user simply isn't an outlier this
// month, which is the good case.
export function AnomaliesCard() {
  const { data, isPending, isError, error } = useAnomalies();

  return (
    <section className="bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <header className="mb-4">
        <h3 className="text-base font-semibold text-slate-900">Unusual spending</h3>
        <p className="text-xs text-slate-500">
          Recent expenses that stand out against your category baselines.
        </p>
      </header>

      {isPending && <p className="text-sm text-slate-500">Loading…</p>}
      {isError && (
        <p role="alert" className="text-sm text-red-600">
          Couldn&apos;t load anomalies: {(error as Error).message}
        </p>
      )}
      {data && data.anomalies.length === 0 && (
        <p className="text-sm text-slate-500">
          Nothing unusual this month. Your spending is within the typical range for every category.
        </p>
      )}
      {data && data.anomalies.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {data.anomalies.map((anomaly) => (
            <li key={anomaly.expense_id} className="py-3 flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-900">{anomaly.merchant_name}</p>
                <p className="text-xs text-slate-500">
                  {categoryLabel(anomaly.category)} · {formatDate(anomaly.expense_date)} · usual ~
                  {formatMoney(anomaly.baseline_mean, 'USD')}
                </p>
              </div>
              <div className="text-right">
                <p className="text-sm font-mono font-medium text-slate-900">
                  {formatMoney(anomaly.amount, 'USD')}
                </p>
                <p className="text-xs text-amber-700">{anomaly.z_score.toFixed(1)}× the baseline</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
