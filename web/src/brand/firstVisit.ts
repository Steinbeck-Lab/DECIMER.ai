const KEY = 'decimer.visited';
const PROBE = 'decimer.probe';
/** The earlier Laravel version of decimer.ai kept its own flag under this name in local storage, on the same origin. */
const LEGACY_KEY = 'visited';

/**
 * The flag lives in the tab's session storage, not the browser's local storage: the logo
 * animation plays when the site is opened in a new tab, and not again when the visitor comes back to the home page
 * (or reloads) within the same tab. The earlier version played it on the first visit to the browser only.
 */
function tabStorage(): Storage {
  return window.sessionStorage;
}

/**
 * Is this the first time the site is opened in this tab? Reads the flag and does not set it: the flag is set
 * (`markVisited`) only once the animation has actually been shown.
 *
 * Storage can be blocked (private windows, site data off) or full. Then the visit could never be remembered, and the
 * animation would play on every page load, so this answers false unless a write works.
 */
export function isFirstVisit(storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>): boolean {
  try {
    const store = storage ?? tabStorage();
    if (store.getItem(KEY)) return false;
    store.setItem(PROBE, '1');
    store.removeItem(PROBE);
    return true;
  } catch {
    return false;
  }
}

/** Remembers, for this tab, that the animation was shown. */
export function markVisited(storage?: Pick<Storage, 'setItem'>): void {
  try {
    (storage ?? tabStorage()).setItem(KEY, '1');
  } catch {
    // Nothing to do: the next page load in this tab will see the animation again.
  }
}

/**
 * A one-off cleanup of what the browser kept before the flag moved to the tab: this app's own earlier note in local
 * storage and the earlier version's `visited` (it set it on the same origin). Neither is read any more, and
 * the privacy text describes only the session note. Called each time the empty screen is shown; once the keys are
 * gone it does nothing.
 */
export function forgetOldFlags(storage?: Pick<Storage, 'removeItem'>): void {
  try {
    const store = storage ?? window.localStorage;
    store.removeItem(KEY);
    store.removeItem(LEGACY_KEY);
  } catch {
    // Storage blocked: there is nothing to clean up that we could reach.
  }
}
