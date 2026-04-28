import { afterEach, describe, expect, it, vi } from 'vitest';

import { _resetAuthStore, getAuthToken, setAuthToken, subscribeAuth } from './authStore';

afterEach(() => {
  _resetAuthStore();
});

describe('authStore', () => {
  it('starts empty', () => {
    expect(getAuthToken()).toBeNull();
  });

  it('round-trips a token', () => {
    setAuthToken('abc.def.ghi');
    expect(getAuthToken()).toBe('abc.def.ghi');
  });

  it('notifies subscribers on change', () => {
    const listener = vi.fn();
    subscribeAuth(listener);
    setAuthToken('one');
    setAuthToken('two');
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('does not fire when the value is unchanged', () => {
    setAuthToken('same');
    const listener = vi.fn();
    subscribeAuth(listener);
    setAuthToken('same');
    expect(listener).not.toHaveBeenCalled();
  });

  it('returns an unsubscriber', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeAuth(listener);
    unsubscribe();
    setAuthToken('after-unsub');
    expect(listener).not.toHaveBeenCalled();
  });

  it('survives a listener that unsubscribes itself mid-fire', () => {
    // Real-world case: a component unmounts inside a token-change
    // callback. The snapshot copy in the store must keep the
    // remaining listeners reachable.
    const a = vi.fn();
    let unsubB = () => {};
    const b = vi.fn(() => unsubB());
    const c = vi.fn();
    subscribeAuth(a);
    unsubB = subscribeAuth(b);
    subscribeAuth(c);
    setAuthToken('once');
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    expect(c).toHaveBeenCalledTimes(1);
  });
});
