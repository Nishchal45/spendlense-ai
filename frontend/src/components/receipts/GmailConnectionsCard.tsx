import { useState } from 'react';

import { fetchGmailConsentUrl, useDisconnectGmail, useGmailConnections } from '@/api/integrations';

// Renders the user's connected Gmail accounts and a "Connect Gmail"
// button that kicks off the OAuth flow. Phase 5.6 PR D — the
// frontend half of the zero-touch ingestion path. Sits next to
// ``InboxAddressCard`` so the user sees both ingestion options
// side-by-side.
//
// Why we navigate the *whole window* to the consent URL instead of
// opening a popup: Google's OAuth flow can chain through MFA, account
// pickers, and re-consent screens; popups get blocked on iOS and
// behave inconsistently across browsers. A full navigation has zero
// cross-window state and matches the expected "open Google" feel.
//
// Disconnect is wired through TanStack Query's mutation surface so
// the row vanishes the moment the API returns 204 (without a manual
// refetch). No optimistic update — the API call is fast and the
// rollback story for OAuth tokens isn't worth the complexity.
export function GmailConnectionsCard() {
  const { data, isPending, isError, error } = useGmailConnections();
  const disconnect = useDisconnectGmail();
  const [connectError, setConnectError] = useState<string | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);

  async function handleConnect() {
    setConnectError(null);
    setIsConnecting(true);
    try {
      const url = await fetchGmailConsentUrl();
      // ``assign`` (not ``replace``) so the back button returns the
      // user to the receipts page if they bail out of consent. Google
      // sends them back to ``/receipts?gmail=connected`` either way,
      // so the back button is a true "I changed my mind" affordance.
      window.location.assign(url);
    } catch {
      // The most likely failure mode here is a 503 — the integration
      // isn't configured on this server. Surface a single concise
      // string instead of leaking the underlying error.
      setConnectError(
        'Could not start the Gmail connect flow. The integration may not be configured on this server.',
      );
      setIsConnecting(false);
    }
  }

  return (
    <section className="bg-white border border-slate-200 rounded-lg p-4 shadow-sm">
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Gmail</h3>
          <p className="text-xs text-slate-500 mt-1">
            Connect your Gmail account and SpendLens will pull receipts the moment they arrive — no
            filter rule, no forwarding setup.
          </p>
        </div>
        <button
          type="button"
          onClick={handleConnect}
          disabled={isConnecting}
          className="text-xs px-3 py-1.5 rounded bg-slate-900 text-white hover:bg-slate-800 disabled:opacity-50 whitespace-nowrap"
        >
          {isConnecting ? 'Opening Google…' : 'Connect Gmail'}
        </button>
      </header>

      {connectError && (
        <p role="alert" className="text-xs text-red-600 mb-3">
          {connectError}
        </p>
      )}

      {isPending && <p className="text-xs text-slate-500">Loading connections…</p>}

      {isError && (
        <p role="alert" className="text-xs text-red-600">
          Could not load Gmail connections: {(error as Error).message}
        </p>
      )}

      {data && data.items.length === 0 && !isPending && (
        // Empty state framed as opportunity rather than absence —
        // the user hasn't done anything wrong, the feature just isn't
        // turned on yet.
        <p className="text-xs text-slate-500">No Gmail accounts connected yet.</p>
      )}

      {data && data.items.length > 0 && (
        <ul className="space-y-2">
          {data.items.map((connection) => (
            <li
              key={connection.id}
              className="flex items-center justify-between gap-3 border border-slate-200 rounded px-3 py-2"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-900 truncate">
                  {connection.google_email}
                </p>
                <p className="text-xs text-slate-500">
                  Connected {new Date(connection.created_at).toLocaleDateString()}
                </p>
              </div>
              <button
                type="button"
                onClick={() => disconnect.mutate(connection.id)}
                // Disable only the row that's currently disconnecting
                // so a slow network doesn't lock every row at once.
                disabled={disconnect.isPending && disconnect.variables === connection.id}
                className="text-xs px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-100 text-slate-700 disabled:opacity-50 whitespace-nowrap"
              >
                {disconnect.isPending && disconnect.variables === connection.id
                  ? 'Disconnecting…'
                  : 'Disconnect'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
