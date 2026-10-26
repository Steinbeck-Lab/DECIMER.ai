import { afterEach, expect, test, vi } from 'vitest';
import { forgetOldFlags, isFirstVisit, markVisited } from './firstVisit';

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    has: (k: string) => data.has(k),
  };
}

afterEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

test('asking does not use the visit up: it stays the first visit until the animation has been shown', () => {
  const storage = memoryStorage();
  expect(isFirstVisit(storage)).toBe(true);
  expect(isFirstVisit(storage)).toBe(true);
  expect(storage.has('decimer.visited')).toBe(false);
  expect(storage.has('decimer.probe')).toBe(false); // the write probe cleans up after itself
});

test('after markVisited it is not the first visit of this tab any more', () => {
  const storage = memoryStorage();
  markVisited(storage);
  expect(isFirstVisit(storage)).toBe(false);
});

test('blocked storage means no animation, not an animation on every load', () => {
  const blocked = {
    getItem: vi.fn(() => {
      throw new DOMException('denied', 'SecurityError');
    }),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  };
  expect(isFirstVisit(blocked)).toBe(false);
});

test('storage that reads but cannot write (quota) also means no animation', () => {
  const full = {
    getItem: () => null,
    setItem: () => {
      throw new DOMException('full', 'QuotaExceededError');
    },
    removeItem: () => undefined,
  };
  expect(isFirstVisit(full)).toBe(false);
});

test('markVisited never throws, even when the write fails', () => {
  expect(() =>
    markVisited({
      setItem: () => {
        throw new DOMException('full', 'QuotaExceededError');
      },
    }),
  ).not.toThrow();
});

test('the flag is kept per browser tab: in session storage, never in local storage', () => {
  expect(isFirstVisit()).toBe(true);
  markVisited();
  expect(sessionStorage.getItem('decimer.visited')).toBe('1');
  expect(localStorage.getItem('decimer.visited')).toBeNull();
  expect(isFirstVisit()).toBe(false);
  // The write probe cleans up after itself.
  expect(sessionStorage.getItem('decimer.probe')).toBeNull();
});

test('a flag left in local storage by the old behaviour does not stop the animation of a new tab', () => {
  localStorage.setItem('decimer.visited', '1');
  expect(isFirstVisit()).toBe(true);
});

test('forgetOldFlags removes this app\'s old note and the legacy site\'s flag from local storage, and leaves the tab\'s flag', () => {
  localStorage.setItem('decimer.visited', '1');
  localStorage.setItem('visited', 'true');
  localStorage.setItem('ketcher-settings', 'kept'); // not ours to touch
  markVisited();
  forgetOldFlags();
  expect(localStorage.getItem('decimer.visited')).toBeNull();
  expect(localStorage.getItem('visited')).toBeNull();
  expect(localStorage.getItem('ketcher-settings')).toBe('kept');
  expect(sessionStorage.getItem('decimer.visited')).toBe('1');
  expect(() => forgetOldFlags()).not.toThrow(); // nothing left to remove
});

test('forgetOldFlags never throws when storage is blocked', () => {
  expect(() =>
    forgetOldFlags({
      removeItem: () => {
        throw new DOMException('denied', 'SecurityError');
      },
    }),
  ).not.toThrow();
});
