import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { useCurrentUser, type User } from '@/api/auth';

import { getAuthToken, setAuthToken, subscribeAuth } from './authStore';

// Internal context value. Exported so ``useAuth`` can read it; the
// shape is pinned here next to the provider that produces it.
export interface AuthContextValue {
  token: string | null;
  user: User | undefined;
  isLoading: boolean;
  logout: () => void;
}

// HMR caveat: ``react-refresh/only-export-components`` flags a
// context export from a ``.tsx`` file even though it's a constant.
// Splitting a 50-line module into three files for a fast-refresh
// edge case is the wrong trade — context, provider, and the value
// type belong together. Disabling the warning here, not project-
// wide, so future drift in other files still gets caught.
// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthContextValue | null>(null);

// React provider that mirrors the in-memory ``authStore`` into the
// React tree. The store is the source of truth; this provider just
// makes the value reactive (so components re-render on change) and
// bundles the side-effects (cache wipe on logout) so callers don't
// have to remember them.
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null>(getAuthToken);

  // Bridge: store changes → React state. The store fires
  // synchronously on every ``setAuthToken`` call, so login, logout,
  // and 401-driven clears all reach the tree without callers having
  // to know about the context.
  useEffect(() => subscribeAuth(() => setToken(getAuthToken())), []);

  const { data: user, isLoading } = useCurrentUser(Boolean(token));

  const logout = useCallback(() => {
    setAuthToken(null);
    // Wipe query cache so the next user's session can't see a flicker
    // of the previous user's expenses/receipts. ``clear`` is more
    // aggressive than ``invalidateQueries`` — exactly what we want
    // for a security boundary.
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo(
    () => ({ token, user, isLoading, logout }),
    [token, user, isLoading, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
