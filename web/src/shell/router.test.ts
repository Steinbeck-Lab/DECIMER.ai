import { expect, test } from 'vitest';
import { hrefFor, parseHash } from './router';

test('known routes parse, with or without a trailing slash', () => {
  expect(parseHash('#/about')).toBe('/about');
  expect(parseHash('#/privacy/')).toBe('/privacy');
  expect(parseHash('#/impressum')).toBe('/impressum');
});

test('an empty hash is home, and so is anything unknown', () => {
  expect(parseHash('')).toBe('/');
  expect(parseHash('#')).toBe('/');
  expect(parseHash('#/')).toBe('/');
  expect(parseHash('#/nope')).toBe('/');
  expect(parseHash('#main')).toBe('/');
});

test('links are hash links', () => {
  expect(hrefFor('/about')).toBe('#/about');
});
