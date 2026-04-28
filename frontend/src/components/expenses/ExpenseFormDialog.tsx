import { useEffect, useRef, useState, type FormEvent } from 'react';

import {
  EXPENSE_CATEGORIES,
  categoryLabel,
  useCreateExpense,
  useUpdateExpense,
  type Expense,
  type ExpenseCategory,
  type ExpenseCreatePayload,
} from '@/api/expenses';
import { ApiError } from '@/api/client';

interface Props {
  // ``null`` = create mode; an expense object = edit mode. Toggling
  // this prop is what shows / hides the dialog.
  expense: Expense | null | undefined;
  onClose: () => void;
}

interface FormState {
  merchant_name: string;
  amount: string;
  currency: string;
  category: ExpenseCategory;
  expense_date: string;
  description: string;
}

const EMPTY_FORM: FormState = {
  merchant_name: '',
  amount: '',
  currency: 'USD',
  category: 'other',
  expense_date: new Date().toISOString().slice(0, 10),
  description: '',
};

// Single dialog covers create + edit. Native ``<dialog>`` is plenty —
// a custom modal layer would mean focus-trap, a11y plumbing, scroll
// locking, etc. The browser handles all of that.
export function ExpenseFormDialog({ expense, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const createExpense = useCreateExpense();
  const updateExpense = useUpdateExpense();

  const isEdit = expense != null;

  // Open / close the native dialog when the prop flips. ``showModal``
  // gives us focus trap + backdrop + escape-to-close for free.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (expense !== undefined) {
      setForm(
        expense
          ? {
              merchant_name: expense.merchant_name,
              amount: expense.amount,
              currency: expense.currency,
              category: expense.category,
              expense_date: expense.expense_date,
              description: expense.description ?? '',
            }
          : EMPTY_FORM,
      );
      // Reset mutation state from a previous open so old errors
      // don't bleed into the new session.
      createExpense.reset();
      updateExpense.reset();
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) {
      dialog.close();
    }
    // ``createExpense`` / ``updateExpense`` are stable mutation
    // objects from React Query; including them would re-run on every
    // render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expense]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload: ExpenseCreatePayload = {
      merchant_name: form.merchant_name.trim(),
      amount: form.amount,
      currency: form.currency.toUpperCase(),
      category: form.category,
      expense_date: form.expense_date,
      ...(form.description.trim() ? { description: form.description.trim() } : {}),
    };

    if (isEdit && expense) {
      updateExpense.mutate({ id: expense.id, patch: payload }, { onSuccess: onClose });
    } else {
      createExpense.mutate(payload, { onSuccess: onClose });
    }
  }

  const mutation = isEdit ? updateExpense : createExpense;
  const errorMessage = formatMutationError(mutation.error);

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      className="rounded-lg p-0 backdrop:bg-black/40 max-w-md w-full"
    >
      <form onSubmit={handleSubmit} className="p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">{isEdit ? 'Edit expense' : 'New expense'}</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <Field label="Merchant">
          <input
            type="text"
            value={form.merchant_name}
            onChange={(event) => setForm((f) => ({ ...f, merchant_name: event.target.value }))}
            required
            maxLength={255}
            className="w-full border border-slate-300 rounded-md px-3 py-2"
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount">
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={form.amount}
              onChange={(event) => setForm((f) => ({ ...f, amount: event.target.value }))}
              required
              className="w-full border border-slate-300 rounded-md px-3 py-2 font-mono"
            />
          </Field>
          <Field label="Currency">
            <input
              type="text"
              value={form.currency}
              onChange={(event) =>
                setForm((f) => ({ ...f, currency: event.target.value.toUpperCase() }))
              }
              required
              maxLength={3}
              minLength={3}
              pattern="[A-Za-z]{3}"
              className="w-full border border-slate-300 rounded-md px-3 py-2 uppercase"
            />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Date">
            <input
              type="date"
              value={form.expense_date}
              onChange={(event) => setForm((f) => ({ ...f, expense_date: event.target.value }))}
              required
              className="w-full border border-slate-300 rounded-md px-3 py-2"
            />
          </Field>
          <Field label="Category">
            <select
              value={form.category}
              onChange={(event) =>
                setForm((f) => ({ ...f, category: event.target.value as ExpenseCategory }))
              }
              className="w-full border border-slate-300 rounded-md px-3 py-2 bg-white"
            >
              {EXPENSE_CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {categoryLabel(category)}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="Description (optional)">
          <textarea
            value={form.description}
            onChange={(event) => setForm((f) => ({ ...f, description: event.target.value }))}
            maxLength={1024}
            rows={2}
            className="w-full border border-slate-300 rounded-md px-3 py-2 resize-none"
          />
        </Field>

        {errorMessage && (
          <p role="alert" className="text-sm text-red-600">
            {errorMessage}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-md text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="px-4 py-2 rounded-md bg-brand-600 hover:bg-brand-700 disabled:bg-slate-300 text-white font-medium"
          >
            {mutation.isPending ? 'Saving…' : isEdit ? 'Save changes' : 'Create'}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-slate-500 mb-1">{label}</span>
      {children}
    </label>
  );
}

function formatMutationError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.status === 422)
      return 'One of the fields is invalid. Check the values and try again.';
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
  }
  return 'Could not save the expense. Please try again.';
}
