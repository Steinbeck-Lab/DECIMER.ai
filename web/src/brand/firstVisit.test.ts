import { expect, test, vi } from 'vitest';
import { isFirstVisit, markVisited } from './firstVisit';

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    has: (k: string) => data.has(k),
  };
}

test('asking does not use the visit up: it stays the first visit until the animation has been shown', () => {
  const storage = memoryStorage();
  expect(isFirstVisit(storage)).toBe(true);
  expect(isFirstVisit(storage)).toBe(true);
  expect(storage.has('decimer.visited')).toBe(false);
  expect(storage.has('decimer.probe')).toBe(false); // the write probe cleans up after itself
});

test('after markVisited it is not the first visit any more', () => {
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
