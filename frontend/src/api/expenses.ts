import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';

import { apiFetch } from './client';

// Wire types mirror ``backend/app/schemas/expense.py``. Money fields
// arrive as strings to dodge JSON-float rounding — we never coerce
// them to ``number`` until the moment they're rendered.

export const EXPENSE_CATEGORIES = [
  'food_dining',
  'groceries',
  'transportation',
  'shopping',
  'entertainment',
  'utilities',
  'healthcare',
  'housing',
  'travel',
  'education',
  'personal',
  'other',
] as const;

export type ExpenseCategory = (typeof EXPENSE_CATEGORIES)[number];

export type ExpenseSource = 'manual' | 'receipt' | 'import';

export interface Expense {
  id: string;
  user_id: string;
  merchant_name: string;
  amount: string;
  currency: string;
  category: ExpenseCategory;
  expense_date: string;
  description: string | null;
  source: ExpenseSource;
  receipt_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaginatedExpenses {
  items: Expense[];
  next_cursor: string | null;
}

// ``exactOptionalPropertyTypes`` makes ``foo?: T`` strictly mean
// "may be omitted" — assigning ``undefined`` is a separate signal.
// We want both to be legal here so ``onChange({...filters, x: undefined})``
// (the natural way to clear a filter) typechecks.
export interface ExpenseFilters {
  category?: ExpenseCategory | undefined;
  merchant?: string | undefined;
  date_from?: string | undefined;
  date_to?: string | undefined;
}

export interface ExpenseCreatePayload {
  merchant_name: string;
  amount: string;
  currency: string;
  category: ExpenseCategory;
  expense_date: string;
  description?: string | undefined;
}

export type ExpensePatchPayload = Partial<ExpenseCreatePayload>;

// ----- helpers -------------------------------------------------------------

function buildQueryString(filters: ExpenseFilters, cursor?: string): string {
  // ``URLSearchParams`` would happily encode ``undefined`` as the
  // string "undefined" — defeats the whole point of optional
  // filters. Build the entries manually.
  const entries: [string, string][] = [];
  if (filters.category) entries.push(['category', filters.category]);
  if (filters.merchant) entries.push(['merchant', filters.merchant]);
  if (filters.date_from) entries.push(['date_from', filters.date_from]);
  if (filters.date_to) entries.push(['date_to', filters.date_to]);
  if (cursor) entries.push(['cursor', cursor]);
  return entries.length === 0
    ? ''
    : `?${entries.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')}`;
}

// ----- queries -------------------------------------------------------------

/**
 * Cursor-paginated expense list, infinite-scroll friendly.
 *
 * The backend returns a base64url cursor; we round-trip it as opaque
 * string. ``getNextPageParam`` is the only place we know about the
 * shape of pagination — components consume ``data.pages`` and
 * ``fetchNextPage`` directly.
 */
export function useExpenses(filters: ExpenseFilters) {
  return useInfiniteQuery<PaginatedExpenses, Error>({
    queryKey: ['expenses', filters],
    queryFn: ({ pageParam }) =>
      apiFetch<PaginatedExpenses>(
        `/expenses${buildQueryString(filters, pageParam as string | undefined)}`,
      ),
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

// ----- mutations -----------------------------------------------------------

export function useCreateExpense() {
  const queryClient = useQueryClient();
  return useMutation<Expense, Error, ExpenseCreatePayload>({
    mutationFn: (payload) =>
      apiFetch<Expense>('/expenses', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      // The new row's position depends on date sort + filters — easier
      // to refetch the first page than to splice in optimistically.
      void queryClient.invalidateQueries({ queryKey: ['expenses'] });
    },
  });
}

export function useUpdateExpense() {
  const queryClient = useQueryClient();
  return useMutation<Expense, Error, { id: string; patch: ExpensePatchPayload }>({
    mutationFn: ({ id, patch }) =>
      apiFetch<Expense>(`/expenses/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      }),
    // Optimistic: replace the row in cache immediately, roll back on
    // error. Crucial for the inline category-edit path — the
    // dropdown should feel instant, not "click and wait".
    onMutate: async ({ id, patch }) => {
      await queryClient.cancelQueries({ queryKey: ['expenses'] });
      const previous = queryClient.getQueriesData<InfiniteData<PaginatedExpenses>>({
        queryKey: ['expenses'],
      });
      queryClient.setQueriesData<InfiniteData<PaginatedExpenses>>(
        { queryKey: ['expenses'] },
        (old) =>
          old
            ? {
                ...old,
                pages: old.pages.map((page) => ({
                  ...page,
                  items: page.items.map((item) =>
                    item.id === id ? applyPatch(item, patch) : item,
                  ),
                })),
              }
            : old,
      );
      return { previous };
    },
    onError: (_err, _vars, context) => {
      const previous = (context as { previous?: [unknown, unknown][] } | undefined)?.previous;
      previous?.forEach(([key, data]) => {
        queryClient.setQueryData(key as readonly unknown[], data);
      });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['expenses'] });
    },
  });
}

// Apply a patch onto a cached row, dropping ``undefined`` fields.
//
// Why not ``{...item, ...patch}``: ``ExpensePatchPayload`` fields are
// ``T | undefined`` (so callers can pass ``{x: undefined}`` for
// "don't touch") but the cached ``Expense`` rows have stricter types
// like ``string | null`` — a naive spread would smuggle ``undefined``
// into a place that doesn't accept it.
//
// The signature deliberately doesn't try to match the patch type
// against ``Partial<Expense>`` (the field-level union mismatch makes
// that a bigger headache than it's worth in this scope). Callers
// guarantee at the call site that the patch keys are real fields on
// the row; this helper just enforces "don't write undefined".
function applyPatch(item: Expense, patch: ExpensePatchPayload): Expense {
  const result = { ...item } as Record<string, unknown>;
  for (const [key, value] of Object.entries(patch)) {
    if (value !== undefined) {
      result[key] = value;
    }
  }
  // Two-step cast: ``Record<string, unknown>`` doesn't structurally
  // overlap with ``Expense`` enough for TS to allow a direct cast,
  // and silencing it via ``as unknown as Expense`` makes the intent
  // explicit. We know the shape is right because we started from a
  // valid ``Expense`` and only overlaid same-keyed values.
  return result as unknown as Expense;
}

export function useDeleteExpense() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => apiFetch<void>(`/expenses/${id}`, { method: 'DELETE' }),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ['expenses'] });
      const previous = queryClient.getQueriesData<InfiniteData<PaginatedExpenses>>({
        queryKey: ['expenses'],
      });
      queryClient.setQueriesData<InfiniteData<PaginatedExpenses>>(
        { queryKey: ['expenses'] },
        (old) =>
          old
            ? {
                ...old,
                pages: old.pages.map((page) => ({
                  ...page,
                  items: page.items.filter((item) => item.id !== id),
                })),
              }
            : old,
      );
      return { previous };
    },
    onError: (_err, _vars, context) => {
      const previous = (context as { previous?: [unknown, unknown][] } | undefined)?.previous;
      previous?.forEach(([key, data]) => {
        queryClient.setQueryData(key as readonly unknown[], data);
      });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['expenses'] });
    },
  });
}

// ----- view helpers --------------------------------------------------------

const CATEGORY_LABELS: Record<ExpenseCategory, string> = {
  food_dining: 'Food & dining',
  groceries: 'Groceries',
  transportation: 'Transportation',
  shopping: 'Shopping',
  entertainment: 'Entertainment',
  utilities: 'Utilities',
  healthcare: 'Healthcare',
  housing: 'Housing',
  travel: 'Travel',
  education: 'Education',
  personal: 'Personal',
  other: 'Other',
};

export function categoryLabel(category: ExpenseCategory): string {
  return CATEGORY_LABELS[category];
}
