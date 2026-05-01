import { useState } from 'react';

import { useAuth } from '@/auth/useAuth';

// Renders the user's forward-to-email address with a copy button.
// Phase 5.5 ships the backend webhook + token; this card is the
// only frontend surface that exposes the address. The user pastes
// it into a Gmail filter or a forward rule and walks away.
//
// Two UX details worth flagging:
//
// * The address is wrapped in ``font-mono`` + ``break-all`` so the
//   32-char hex token doesn't overflow on mobile.
// * The "copied" feedback flips back to "copy" after 2 s. Without
//   it, the button looks frozen and the user copies twice.
export function InboxAddressCard() {
  const { user } = useAuth();
  const [copied, setCopied] = useState(false);

  if (!user) return null;

  async function handleCopy() {
    if (!user) return;
    try {
      await navigator.clipboard.writeText(user.inbox_address);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2_000);
    } catch {
      // Silently ignore — most likely a permission error in a
      // non-secure context. The address is still visible on screen.
    }
  }

  return (
    <section className="bg-white border border-slate-200 rounded-lg p-4 shadow-sm">
      <header className="mb-2">
        <h3 className="text-sm font-semibold text-slate-900">Your forwarding address</h3>
        <p className="text-xs text-slate-500 mt-1">
          Forward any receipt email here and SpendLens will OCR and categorise the attachments
          automatically. Set up a Gmail filter once and never copy a receipt again.
        </p>
      </header>

      <div className="flex items-center gap-2">
        <code
          className="flex-1 font-mono text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1.5 break-all"
          aria-label="Forwarding email address"
        >
          {user.inbox_address}
        </code>
        <button
          type="button"
          onClick={handleCopy}
          className="text-xs px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-100 text-slate-700 whitespace-nowrap"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </section>
  );
}
