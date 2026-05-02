import { useReceipts } from '@/api/receipts';
import { GmailConnectionsCard } from '@/components/receipts/GmailConnectionsCard';
import { GmailRedirectBanner } from '@/components/receipts/GmailRedirectBanner';
import { InboxAddressCard } from '@/components/receipts/InboxAddressCard';
import { ReceiptCard } from '@/components/receipts/ReceiptCard';
import { ReceiptUploader } from '@/components/receipts/ReceiptUploader';

// Top-level surface for the OCR pipeline. Two zones:
//
// 1. **Uploader** — drag-drop / click-to-pick. New uploads land in
//    ``uploaded`` and start travelling through the pipeline.
// 2. **Recent receipts** — grid of ``ReceiptCard``s, each subscribing
//    to per-row polling while in flight, then settling once the
//    pipeline finishes.
//
// We keep the layout single-column on mobile and two-up on
// desktop — most users have a stack of 5-15 receipts in flight at
// any time, not hundreds, so a grid + "look at the last 50" is
// plenty.
export function ReceiptsPage() {
  const { data, isPending, isError, error } = useReceipts();

  return (
    <section>
      <header className="mb-6">
        <h2 className="text-2xl font-semibold text-slate-900">Receipts</h2>
        <p className="text-sm text-slate-500 mt-1">
          Upload a photo or PDF and watch SpendLens read, parse, and categorise it.
        </p>
      </header>

      <div className="space-y-4 mb-8">
        <GmailRedirectBanner />
        <ReceiptUploader />
        <InboxAddressCard />
        <GmailConnectionsCard />
      </div>

      {isPending && <p className="text-slate-500 text-center py-8">Loading receipts…</p>}

      {isError && (
        <p role="alert" className="text-red-600 text-center py-8">
          Failed to load receipts: {(error as Error).message}
        </p>
      )}

      {data && data.items.length === 0 && (
        <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-slate-500">
          No receipts yet. Drop one above to get started.
        </div>
      )}

      {data && data.items.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {data.items.map((receipt) => (
            <ReceiptCard key={receipt.id} receipt={receipt} />
          ))}
        </div>
      )}
    </section>
  );
}
