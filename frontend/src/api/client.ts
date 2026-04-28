// Thin fetch wrapper for talking to the SpendLens API.
//
// Every request goes through here so cross-cutting concerns
// (authorization, base URL, JSON encoding, error surfaces) live in one
// place. The bearer token is read from ``authStore`` per request so
// refresh / logout takes effect immediately without rewiring callers.
//
// Why not axios: the standard ``fetch`` API plus a 30-line wrapper
// covers the surface we need (JSON in / JSON out, bearer tokens, timed
// errors). Adding a runtime dependency for that would be cargo cult.

import { getAuthToken, setAuthToken } from '@/auth/authStore';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export class ApiError extends Error {
  // Surfacing the status separately lets callers branch on 401 vs 404
  // without parsing the message. Body is exposed for endpoints that
  // ship structured ``detail`` payloads (FastAPI default).
  constructor(
    public readonly status: number,
    public readonly body: unknown,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export type ApiRequestInit = RequestInit;

export async function apiFetch<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  // ``FormData`` bodies need the browser to set the
  // ``multipart/form-data; boundary=...`` header itself — pinning
  // ``application/json`` here would break the upload silently. Same
  // logic for ``Blob`` bodies, where the caller's mime type is
  // already on the blob.
  const isStructuredBody =
    init.body != null && !(init.body instanceof FormData) && !(init.body instanceof Blob);
  if (!headers.has('Content-Type') && isStructuredBody) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('Accept', 'application/json');

  // Attach the bearer token if the user is logged in. Reading the
  // token *per request* (vs. closure-capturing once) means a logout
  // mid-session invalidates the next request immediately.
  const token = getAuthToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 204) {
    // No body to parse. Cast through ``unknown`` because callers that
    // type a 204 endpoint as ``void`` get the right shape; the few
    // that don't will get a runtime ``undefined`` they can guard on.
    return undefined as unknown as T;
  }

  const body = (await response.json().catch(() => null)) as unknown;

  if (!response.ok) {
    if (response.status === 401 && token) {
      // The server rejected our token — wipe it so the next
      // ``ProtectedRoute`` render redirects to login. We don't
      // touch the store on 401 *without* a token (the login call
      // itself, for example) so a bad-credentials response doesn't
      // double-clear an already-empty store.
      setAuthToken(null);
    }
    throw new ApiError(response.status, body, `API ${response.status} on ${path}`);
  }

  return body as T;
}
