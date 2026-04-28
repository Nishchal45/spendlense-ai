import {
  EXPENSE_CATEGORIES,
  categoryLabel,
  useDeleteExpense,
  useUpdateExpense,
  type Expense,
} from '@/api/expenses';
import { formatDate, formatMoney } from '@/lib/format';

interface Props {
  expenses: Expense[];
  onEdit: (expense: Expense) => void;
}

// Tabular view of expenses. Two interactions live inline on each
// row: change category (the dominant edit flow given the
// corrections-feedback loop in the OCR pipeline) and delete.
// Anything richer — editing the merchant name, amount, date, or
// description — opens the same form dialog used for create, via
// ``onEdit``. Keeps the row small.
export function ExpenseTable({ expenses, onEdit }: Props) {
  const updateExpense = useUpdateExpense();
  const deleteExpense = useDeleteExpense();

  if (expenses.length === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-slate-500">
        No expenses match these filters.
      </div>
    );
  }

  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs font-medium text-slate-500 uppercase tracking-wide">
          <tr>
            <th className="px-4 py-3">Date</th>
            <th className="px-4 py-3">Merchant</th>
            <th className="px-4 py-3">Category</th>
            <th className="px-4 py-3 text-right">Amount</th>
            <th className="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {expenses.map((expense) => (
            <tr key={expense.id} className="hover:bg-slate-50">
              <td className="px-4 py-3 whitespace-nowrap text-slate-700">
                {formatDate(expense.expense_date)}
              </td>
              <td className="px-4 py-3">
                <div className="font-medium text-slate-900">{expense.merchant_name}</div>
                {expense.description && (
                  <div className="text-xs text-slate-500 truncate max-w-xs">
                    {expense.description}
                  </div>
                )}
                {expense.source === 'receipt' && (
                  <span className="inline-block text-[10px] uppercase tracking-wide bg-brand-50 text-brand-700 px-1.5 py-0.5 rounded mt-1">
                    Receipt
                  </span>
                )}
              </td>
              <td className="px-4 py-3">
                <select
                  value={expense.category}
                  onChange={(event) =>
                    updateExpense.mutate({
                      id: expense.id,
                      patch: { category: event.target.value as Expense['category'] },
                    })
                  }
                  className="text-sm border border-slate-200 rounded-md px-2 py-1 bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
                  aria-label={`Category for ${expense.merchant_name}`}
                >
                  {EXPENSE_CATEGORIES.map((category) => (
                    <option key={category} value={category}>
                      {categoryLabel(category)}
                    </option>
                  ))}
                </select>
              </td>
              <td className="px-4 py-3 text-right font-mono tabular-nums text-slate-900">
                {formatMoney(expense.amount, expense.currency)}
              </td>
              <td className="px-4 py-3 text-right whitespace-nowrap">
                <button
                  type="button"
                  onClick={() => onEdit(expense)}
                  className="text-brand-600 hover:underline text-sm mr-3"
                >
                  Edit
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Delete the ${expense.merchant_name} expense?`)) {
                      deleteExpense.mutate(expense.id);
                    }
                  }}
                  className="text-red-600 hover:underline text-sm"
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
