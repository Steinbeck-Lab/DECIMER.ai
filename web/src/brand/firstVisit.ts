const KEY = 'decimer.visited';
const PROBE = 'decimer.probe';

/**
 * Is this the browser's first visit? Reads the flag and does not set it: the
 * flag is set (`markVisited`) only once the animation has actually been shown.
 *
 * Storage can be blocked (private windows, site data off) or full. Then the
 * visit could never be remembered, and the animation would play on every page
 * load, so this answers false unless a write works.
 */
export function isFirstVisit(storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>): boolean {
  try {
    const store = storage ?? window.localStorage;
    if (store.getItem(KEY)) return false;
    store.setItem(PROBE, '1');
    store.removeItem(PROBE);
    return true;
  } catch {
    return false;
  }
}

/** Remembers that the animation was shown. */
export function markVisited(storage?: Pick<Storage, 'setItem'>): void {
  try {
    (storage ?? window.localStorage).setItem(KEY, '1');
  } catch {
    // Nothing to do: the next visit will see the animation again.
  }
}
