import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { setAuthToken } from '@/auth/authStore';

import { apiFetch } from './client';

// Wire types mirror ``backend/app/schemas/auth.py``. Keep these in
// sync — the typecheck catches drift the moment a backend rename
// hits the API.

export interface User {
  id: string;
  email: string;
  created_at: string;
  /** Per-user forwarding-token. Sensitive — treat like a long-lived
   * bearer for the inbound-email surface. */
  inbox_token: string;
  /** Convenience-rendered ``receipts+<token>@<inbox_email_domain>``
   * — the address the user pastes into a Gmail filter. */
  inbox_address: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface Credentials {
  email: string;
  password: string;
}

// ----- queries -------------------------------------------------------------

/**
 * Fetch the currently authenticated user.
 *
 * Cached forever within a session (``staleTime: Infinity``) — the
 * profile rarely changes, and the cache is wiped on logout via
 * ``queryClient.clear()`` anyway. ``enabled: hasToken`` keeps the
 * call from firing on the public login page.
 */
export function useCurrentUser(hasToken: boolean) {
  return useQuery<User>({
    queryKey: ['auth', 'me'],
    queryFn: () => apiFetch<User>('/auth/me'),
    enabled: hasToken,
    staleTime: Infinity,
    retry: false,
  });
}

// ----- mutations -----------------------------------------------------------

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation<TokenResponse, Error, Credentials>({
    mutationFn: (credentials) =>
      apiFetch<TokenResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify(credentials),
      }),
    onSuccess: (data) => {
      setAuthToken(data.access_token);
      // Drop any stale per-route cache from a previous user so a
      // logout-then-login on the same machine never shows the wrong
      // person's data for a frame.
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
    },
  });
}

export function useRegister() {
  return useMutation<User, Error, Credentials>({
    mutationFn: (credentials) =>
      apiFetch<User>('/auth/register', {
        method: 'POST',
        body: JSON.stringify(credentials),
      }),
    // Registration doesn't auto-login — the user lands on the login
    // form with their email pre-filled. Two reasons: (1) some
    // products want email verification before a session is granted,
    // and we don't want to bake "auto-session" into the contract;
    // (2) the login mutation already covers the auth-state side
    // effects in one place.
  });
}
