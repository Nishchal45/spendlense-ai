import {
  isInFlight,
  useDeleteReceipt,
  useReceiptStatus,
  useRetryReceipt,
  type Receipt,
} from '@/api/receipts';
import { formatDate, formatMoney } from '@/lib/format';

import { StatusBadge } from './StatusBadge';

interface Props {
  receipt: Receipt;
}

// One card per receipt. Three responsibilities:
//
// * Show the freshest status from the polling status endpoint —
//   ``useReceiptStatus`` only subscribes while the row is in flight,
//   so a categorised receipt costs zero requests.
// * Render the parsed payload (merchant / total / date) once the
//   pipeline produces it.
// * Surface failures with the backend's ``error_message`` and offer a
//   one-click retry.
export function ReceiptCard({ receipt }: Props) {
  // Subscribe to the per-row status query while the row is in flight.
  // The hook itself stops polling when the status reaches a terminal
  // state, but we also stop *subscribing* once we know the row is
  // categorised — fewer running queries, less browser bookkeeping.
  const status = useReceiptStatus(receipt.id, isInFlight(receipt.status));

  // Fall back to the row from the list query if we don't have a per-
  // row status yet. The list endpoint doesn't carry parsed_payload
  // or error_message, but it does carry the basics; the moment the
  // status query lands, this object updates.
  const view = status.data ?? {
    id: receipt.id,
    status: receipt.status,
    ocr_method: receipt.ocr_method,
    ocr_confidence: receipt.ocr_confidence,
    error_message: null,
    parsed_payload: null,
    created_at: receipt.created_at,
    updated_at: receipt.updated_at,
  };

  const retry = useRetryReceipt();
  const remove = useDeleteReceipt();

  return (
    <article className="bg-white border border-slate-200 rounded-lg p-4 shadow-sm">
      <header className="flex items-center justify-between mb-3">
        <div className="text-xs text-slate-500">
          Uploaded {formatDate(receipt.created_at.slice(0, 10))}
        </div>
        <StatusBadge status={view.status} />
      </header>

      {view.parsed_payload && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm mb-3">
          <dt className="text-slate-500">Merchant</dt>
          <dd className="font-medium text-slate-900 truncate">
            {view.parsed_payload.merchant ?? '—'}
          </dd>
          <dt className="text-slate-500">Total</dt>
          <dd className="font-mono">
            {view.parsed_payload.total ? formatMoney(view.parsed_payload.total, 'USD') : '—'}
          </dd>
          <dt className="text-slate-500">Date</dt>
          <dd>
            {view.parsed_payload.transaction_date
              ? formatDate(view.parsed_payload.transaction_date)
              : '—'}
          </dd>
        </dl>
      )}

      {view.status === 'failed' && view.error_message && (
        <p role="alert" className="text-sm text-red-600 mb-3">
          {view.error_message}
        </p>
      )}

      {view.ocr_method && view.ocr_confidence != null && (
        <p className="text-xs text-slate-500 mb-3">
          OCR via {view.ocr_method === 'gpt4v' ? 'GPT-4V' : 'Tesseract'} ·{' '}
          {Number(view.ocr_confidence).toFixed(0)}% confidence
        </p>
      )}

      <footer className="flex items-center justify-end gap-3 text-sm">
        {view.status === 'failed' && (
          <button
            type="button"
            onClick={() => retry.mutate(receipt.id)}
            disabled={retry.isPending}
            className="text-brand-600 hover:underline disabled:text-slate-400"
          >
            {retry.isPending ? 'Retrying…' : 'Retry'}
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            if (window.confirm('Delete this receipt? The image will be removed.')) {
              remove.mutate(receipt.id);
            }
          }}
          disabled={remove.isPending}
          className="text-red-600 hover:underline disabled:text-slate-400"
        >
          Delete
        </button>
      </footer>
    </article>
  );
}
