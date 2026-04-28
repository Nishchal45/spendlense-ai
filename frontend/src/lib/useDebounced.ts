import { useEffect, useState } from 'react';

/**
 * Debounced mirror of ``value``.
 *
 * Returns a value that lags ``value`` by ``delayMs`` milliseconds —
 * typing in a search input updates ``value`` on every keystroke but
 * the debounced output only changes after the user pauses. Used for
 * the merchant-search filter so we don't fire a query per keystroke.
 *
 * Re-running the effect is intentional: each value change cancels
 * the pending timeout and starts a new one, so the latest value
 * always wins.
 */
export function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);

  return debounced;
}
