import { statusLabel, type ReceiptStatus } from '@/api/receipts';

interface Props {
  status: ReceiptStatus;
}

// Colour mapping doubles as a visual reference for the state machine
// in ADR-0005:
//
//   uploaded → processing → parsed → categorised   (terminal: green)
//                                  → failed         (terminal: red)
//
// Active states (uploaded, processing, parsed) are warm-blue so the
// user reads them as "in progress, no action needed". Terminal
// success is green, terminal failure is red.
const COLOURS: Record<ReceiptStatus, string> = {
  uploaded: 'bg-slate-100 text-slate-700',
  processing: 'bg-brand-50 text-brand-700',
  parsed: 'bg-amber-50 text-amber-700',
  categorised: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700',
};

export function StatusBadge({ status }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${COLOURS[status]}`}
    >
      {(status === 'uploaded' || status === 'processing') && (
        <span aria-hidden className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      )}
      {statusLabel(status)}
    </span>
  );
}
