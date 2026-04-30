import { AnomaliesCard } from '@/components/insights/AnomaliesCard';
import { BudgetsCard } from '@/components/insights/BudgetsCard';
import { MonthlyBreakdownCard } from '@/components/insights/MonthlyBreakdownCard';
import { TrendsCard } from '@/components/insights/TrendsCard';

// Top-level surface for Phase 6's analytics. Four cards arranged in a
// dashboard layout — monthly breakdown anchors the page (the
// canonical "where did my money go?" view), trends sits below for
// the year-at-a-glance, anomalies + budgets occupy the right
// column on desktop where eye-tracks tend to settle for "what do I
// need to do next?".
export function InsightsPage() {
  return (
    <section>
      <header className="mb-6">
        <h2 className="text-2xl font-semibold text-slate-900">Insights</h2>
        <p className="text-sm text-slate-500 mt-1">
          Monthly breakdown, trends, anomalies, and budget progress at a glance.
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <MonthlyBreakdownCard />
          <TrendsCard />
        </div>
        <div className="space-y-6">
          <BudgetsCard />
          <AnomaliesCard />
        </div>
      </div>
    </section>
  );
}
