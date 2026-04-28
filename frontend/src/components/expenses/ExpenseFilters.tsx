import { useEffect, useState } from 'react';

import { EXPENSE_CATEGORIES, categoryLabel, type ExpenseFilters } from '@/api/expenses';
import { useDebounced } from '@/lib/useDebounced';

interface Props {
  value: ExpenseFilters;
  onChange: (next: ExpenseFilters) => void;
}

const MERCHANT_DEBOUNCE_MS = 300;

// Filter bar at the top of the list. Every field is controlled
// locally so typing feels instant; only the *committed* state — the
// debounced merchant + the immediate category/date selections — is
// pushed to the parent. The parent owns the actual filter state and
// passes it back via ``value`` so external changes (e.g. URL state in
// a future PR) round-trip cleanly.
export function ExpenseFilters({ value, onChange }: Props) {
  const [merchant, setMerchant] = useState(value.merchant ?? '');
  const debouncedMerchant = useDebounced(merchant, MERCHANT_DEBOUNCE_MS);

  // Only push merchant changes once the user pauses typing. Direct
  // controls (category/date) bypass the debounce.
  useEffect(() => {
    if (debouncedMerchant === (value.merchant ?? '')) return;
    onChange({ ...value, merchant: debouncedMerchant || undefined });
    // ``value`` is intentionally not in the dep array — pushing the
    // *full filters object* into deps would re-run this effect every
    // time any other filter changes, undoing the debounce.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedMerchant]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
      <label className="block">
        <span className="block text-xs font-medium text-slate-500 mb-1">Category</span>
        <select
          value={value.category ?? ''}
          onChange={(event) =>
            onChange({
              ...value,
              category:
                event.target.value === ''
                  ? undefined
                  : (event.target.value as ExpenseFilters['category']),
            })
          }
          className="w-full border border-slate-300 rounded-md px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">All categories</option>
          {EXPENSE_CATEGORIES.map((category) => (
            <option key={category} value={category}>
              {categoryLabel(category)}
            </option>
          ))}
        </select>
      </label>

      <label className="block">
        <span className="block text-xs font-medium text-slate-500 mb-1">Merchant</span>
        <input
          type="search"
          placeholder="e.g. starbucks"
          value={merchant}
          onChange={(event) => setMerchant(event.target.value)}
          className="w-full border border-slate-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
      </label>

      <label className="block">
        <span className="block text-xs font-medium text-slate-500 mb-1">From</span>
        <input
          type="date"
          value={value.date_from ?? ''}
          onChange={(event) => onChange({ ...value, date_from: event.target.value || undefined })}
          className="w-full border border-slate-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
      </label>

      <label className="block">
        <span className="block text-xs font-medium text-slate-500 mb-1">To</span>
        <input
          type="date"
          value={value.date_to ?? ''}
          onChange={(event) => onChange({ ...value, date_to: event.target.value || undefined })}
          className="w-full border border-slate-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
      </label>
    </div>
  );
}
