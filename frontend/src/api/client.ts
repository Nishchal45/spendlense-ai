// Thin fetch wrapper for talking to the SpendLens API.
//
// Every request goes through here so cross-cutting concerns
// (authorization, base URL, JSON encoding, error surfaces) live in one
// place. Auth integration lands in PR #B; this scaffold-stage version
// is auth-less so the health probe wires up cleanly.
//
// Why not axios: the standard ``fetch`` API plus a 30-line wrapper
// covers the surface we need (JSON in / JSON out, bearer tokens, timed
// errors). Adding a runtime dependency for that would be cargo cult.

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
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('Accept', 'application/json');

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 204) {
    // No body to parse. Cast through ``unknown`` because callers that
    // type a 204 endpoint as ``void`` get the right shape; the few
    // that don't will get a runtime ``undefined`` they can guard on.
    return undefined as unknown as T;
  }

  const body = (await response.json().catch(() => null)) as unknown;

  if (!response.ok) {
    throw new ApiError(response.status, body, `API ${response.status} on ${path}`);
  }

  return body as T;
}
