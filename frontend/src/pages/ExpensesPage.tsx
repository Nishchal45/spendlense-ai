import { useMemo, useState } from 'react';

import { useExpenses, type Expense, type ExpenseFilters } from '@/api/expenses';
import { ExpenseFilters as FiltersBar } from '@/components/expenses/ExpenseFilters';
import { ExpenseFormDialog } from '@/components/expenses/ExpenseFormDialog';
import { ExpenseTable } from '@/components/expenses/ExpenseTable';

// Top-level dashboard. Owns:
//
// * filters       — controlled state pushed down to ``FiltersBar``,
//                   read by ``useExpenses``
// * dialog target — ``undefined``: closed, ``null``: create mode,
//                   ``Expense``: edit mode. Tri-state lets a single
//                   dialog component cover both flows.
//
// Pagination is "Load more" (button), not auto-infinite-scroll. The
// finance UX wants explicit control — auto-fetching as the user
// scrolls also fires when they're just trying to read what's already
// on screen, and a list of expenses is something users actively
// scan, not consume passively.
export function ExpensesPage() {
  const [filters, setFilters] = useState<ExpenseFilters>({});
  const [dialogTarget, setDialogTarget] = useState<Expense | null | undefined>(undefined);

  const { data, isPending, isError, error, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useExpenses(filters);

  // Flatten pages into one list — components don't need the cursor
  // structure once the data is in the cache.
  const expenses = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data]);

  return (
    <section>
      <header className="flex items-end justify-between mb-4 gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Expenses</h2>
          <p className="text-sm text-slate-500 mt-1">
            Every charge SpendLens has seen — manual entries and receipt-derived rows.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setDialogTarget(null)}
          className="bg-brand-600 hover:bg-brand-700 text-white font-medium rounded-md px-4 py-2 transition-colors"
        >
          New expense
        </button>
      </header>

      <FiltersBar value={filters} onChange={setFilters} />

      {isPending && <p className="text-slate-500 text-center py-8">Loading expenses…</p>}

      {isError && (
        <p role="alert" className="text-red-600 text-center py-8">
          Failed to load expenses: {(error as Error).message}
        </p>
      )}

      {data && (
        <>
          <ExpenseTable expenses={expenses} onEdit={setDialogTarget} />

          {hasNextPage && (
            <div className="flex justify-center mt-4">
              <button
                type="button"
                onClick={() => void fetchNextPage()}
                disabled={isFetchingNextPage}
                className="text-sm text-brand-600 hover:underline disabled:text-slate-400"
              >
                {isFetchingNextPage ? 'Loading…' : 'Load more'}
              </button>
            </div>
          )}
        </>
      )}

      <ExpenseFormDialog expense={dialogTarget} onClose={() => setDialogTarget(undefined)} />
    </section>
  );
}
