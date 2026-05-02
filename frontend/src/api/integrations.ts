import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { apiFetch } from './client';

// Wire types mirror ``backend/app/schemas/gmail_connection.py``. Tokens
// never appear on the wire — the encrypted refresh token stays in the
// database and the access token only lives long enough for the
// callback to call userinfo.

export interface GmailConnection {
  id: string;
  google_email: string;
  last_history_id: string | null;
  watch_expiration: string | null;
  created_at: string;
  updated_at: string;
}

export interface GmailConnectionsList {
  items: GmailConnection[];
}

interface GmailConnectURL {
  url: string;
}

// ----- queries -------------------------------------------------------------

export function useGmailConnections() {
  return useQuery<GmailConnectionsList>({
    queryKey: ['integrations', 'gmail'],
    queryFn: () => apiFetch<GmailConnectionsList>('/integrations/gmail'),
  });
}

// ----- mutations -----------------------------------------------------------

export function useDisconnectGmail() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => apiFetch<void>(`/integrations/gmail/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'gmail'] });
    },
  });
}

// ----- imperative helpers --------------------------------------------------

/**
 * Fetch the Google consent URL for the current user.
 *
 * Imperative rather than a hook because the caller redirects the
 * browser the moment it has the URL — there's no UI to render around
 * a loading state that's about to be replaced by a full-page navigate.
 * Returning the URL (instead of a 302 from the API) keeps the auth-
 * required guard sensible: a fetch() following a 302 is transparent
 * and would obscure failures from the caller.
 */
export async function fetchGmailConsentUrl(): Promise<string> {
  const body = await apiFetch<GmailConnectURL>('/integrations/gmail/connect');
  return body.url;
}
