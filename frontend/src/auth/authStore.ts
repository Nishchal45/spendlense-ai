// In-memory token store, deliberately *not* React context.
//
// Two separate consumers of "what's the current token":
//
// 1. The API client (``apiFetch``) — runs *outside* React (e.g. in
//    a query function called by TanStack Query) and can't reach
//    ``useContext`` from there.
// 2. React components — render based on auth state, need to
//    re-render when the token changes.
//
// A tiny pub/sub keeps both honest: the store is the source of
// truth, the API client reads it on every request, and the
// ``AuthContext`` subscribes so components re-render on change.
// Token lives in memory only — refreshing the tab logs you out.
// Phase 8 swaps to httpOnly cookies + silent refresh.

let token: string | null = null;
const listeners = new Set<() => void>();

export function getAuthToken(): string | null {
  return token;
}

export function setAuthToken(next: string | null): void {
  if (token === next) return;
  token = next;
  // Snapshot the listener set in case a callback unsubscribes mid-flight.
  for (const listener of [...listeners]) {
    listener();
  }
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test-only: clear the store between tests so state doesn't leak. */
export function _resetAuthStore(): void {
  token = null;
  listeners.clear();
}
