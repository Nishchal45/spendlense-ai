import { useContext } from 'react';

import { AuthContext, type AuthContextValue } from './AuthContext';

/**
 * Read the current auth state from React context.
 *
 * Throws if used outside an ``<AuthProvider>``. That's intentional —
 * silently returning ``null`` would let consumers assume "logged out"
 * when the real bug is "wrong place in the tree".
 */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be called inside an <AuthProvider>');
  }
  return ctx;
}
