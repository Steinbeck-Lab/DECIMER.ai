import { expect, test } from 'vitest';
import { palette } from './theme';

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// Every text-on-fill pair the UI uses. Body text and small text need 4.5:1 (WCAG 1.4.3).
const TEXT_PAIRS: [string, keyof typeof palette, keyof typeof palette][] = [
  ['ink on white', 'ink', 'white'],
  ['ink on sky', 'ink', 'sky'],
  ['ink on deep sky (drag over)', 'ink', 'skyDeep'],
  ['ink2 on white', 'ink2', 'white'],
  ['ink2 on sky', 'ink2', 'sky'],
  ['muted on white', 'muted', 'white'],
  ['muted on sky', 'muted', 'sky'],
  ['muted italic on white', 'mutedItalic', 'white'],
  ['muted italic on sky', 'mutedItalic', 'sky'],
  ['link on white', 'link', 'white'],
  ['link on sky', 'link', 'sky'],
  ['link hover on white', 'linkHover', 'white'],
  ['white on ink (Download)', 'white', 'ink'],
  ['muted on paper (unchecked)', 'muted', 'paper'],
  ['checked: mint ink on mint', 'mintInk', 'mint'],
  ['review: butter ink on butter', 'butterInk', 'butter'],
  ['edited: lilac ink on lilac', 'lilacInk', 'lilac'],
  ['not sure: peach ink on peach', 'peachInk', 'peach'],
  ['mark as checked button', 'mintButtonInk', 'mintButton'],
  ['ink on butter (truncation notice)', 'ink', 'butter'],
  ['ink on peach (not-a-structure notice)', 'ink', 'peach'],
];

test.each(TEXT_PAIRS)('%s reaches 4.5:1', (_name, fg, bg) => {
  expect(contrast(palette[fg], palette[bg])).toBeGreaterThanOrEqual(4.5);
});

test('the contrast helper agrees with a known pair (black on white is 21:1)', () => {
  expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 5);
  // WCAG's own example: #777 on white is 4.48:1 and fails.
  expect(contrast('#777777', '#ffffff')).toBeLessThan(4.5);
});
