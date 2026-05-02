import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

// Reads the ``?gmail=connected`` / ``?gmail=error&reason=...`` query
// params Google's redirect back leaves on ``/receipts``, surfaces a
// banner, and strips the params so a refresh doesn't re-show the
// notice. On success we also invalidate the connections query so
// the new row appears without a manual refresh.
//
// Why a separate component instead of inlining the logic in
// ``ReceiptsPage``: keeping it isolated means the page test doesn't
// need to mock TanStack Query, and the banner can be moved to a
// settings page later without untangling the page-level layout.

const ERROR_MESSAGES: Record<string, string> = {
  bad_state: 'The Gmail connect link expired or was tampered with. Please try connecting again.',
  exchange_failed: 'Google rejected the consent. Please try connecting again.',
  not_configured:
    'Gmail integration is not configured on this server. Reach out to the operator if you expect this to work.',
  encryption_failed:
    'Could not securely store the Gmail token. Please try again — if it persists, reach out to the operator.',
};

const FALLBACK_ERROR_MESSAGE = 'Something went wrong connecting Gmail. Please try again.';

export function GmailRedirectBanner() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [variant, setVariant] = useState<'success' | 'error' | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const status = searchParams.get('gmail');
    if (!status) return;

    if (status === 'connected') {
      setVariant('success');
      setMessage('Gmail connected. New receipts will sync automatically.');
      // Pull the new row into the connections list so the card
      // updates without a refresh.
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'gmail'] });
    } else if (status === 'error') {
      const reason = searchParams.get('reason') ?? '';
      setVariant('error');
      setMessage(ERROR_MESSAGES[reason] ?? FALLBACK_ERROR_MESSAGE);
    }

    // Strip the query params so refreshing the page doesn't
    // re-trigger the banner. Use ``replace=true`` to avoid pushing a
    // history entry the user has to back through.
    const next = new URLSearchParams(searchParams);
    next.delete('gmail');
    next.delete('reason');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, queryClient]);

  if (!variant || !message) return null;

  const styles =
    variant === 'success'
      ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
      : 'bg-red-50 border-red-200 text-red-800';

  return (
    <div role="status" className={`border rounded-md px-3 py-2 text-sm ${styles}`}>
      <div className="flex items-start justify-between gap-3">
        <p>{message}</p>
        <button
          type="button"
          onClick={() => {
            setVariant(null);
            setMessage(null);
          }}
          className="text-xs text-slate-500 hover:text-slate-900"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
