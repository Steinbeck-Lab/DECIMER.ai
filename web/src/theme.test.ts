import { expect, test } from 'vitest';
import { palette } from './theme';

/** The tokens that are a plain hex colour (the hairline is a translucent forest and has no contrast of its own). */
type Hex = Exclude<keyof typeof palette, 'line'>;

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
const TEXT_PAIRS: [string, Hex, Hex][] = [
  ['ink on page', 'ink', 'page'],
  ['ink on tile', 'ink', 'tile'],
  ['ink on egg nog', 'ink', 'egg'],
  ['ink on deep egg nog (drag over)', 'ink', 'eggDeep'],
  ['ink2 on page', 'ink2', 'page'],
  ['ink2 on tile', 'ink2', 'tile'],
  ['ink2 on egg nog', 'ink2', 'egg'],
  ['muted on page', 'muted', 'page'],
  ['muted on tile', 'muted', 'tile'],
  ['muted on egg nog (unchecked)', 'muted', 'egg'],
  ['muted on deep egg nog', 'muted', 'eggDeep'],
  ['muted italic on tile', 'mutedItalic', 'tile'],
  ['muted italic on egg nog', 'mutedItalic', 'egg'],
  ['copper ink on page', 'copperInk', 'page'],
  ['copper ink on tile', 'copperInk', 'tile'],
  ['copper ink on egg nog', 'copperInk', 'egg'],
  ['frame label (dark forest) on copper', 'onCopper', 'copper'],
  ['About ribbon label (dark forest on the copper cloth)', 'onCopper', 'copper'],
  ['GitHub ribbon label (egg nog on the forest cloth)', 'egg', 'forest'],
  ['link on page', 'link', 'page'],
  ['link on tile', 'link', 'tile'],
  ['link on egg nog', 'link', 'egg'],
  ['link hover on page', 'linkHover', 'page'],
  ['link hover on tile', 'linkHover', 'tile'],
  ['garnet on page (hover emphasis)', 'garnet', 'page'],
  ['egg nog on forest (primary pill)', 'egg', 'forest'],
  ['tile on forest', 'tile', 'forest'],
  ['egg nog on garnet', 'egg', 'garnet'],
  ['checked: ink on checked fill', 'checkedInk', 'checked'],
  ['review: ink on review fill', 'reviewInk', 'review'],
  ['edited: ink on edited fill', 'editedInk', 'edited'],
  ['not sure: ink on not-sure fill', 'notSureInk', 'notSure'],
  ['mark as checked button', 'checkedButtonInk', 'checkedButton'],
  ['ink on review fill (truncation notice)', 'ink', 'review'],
  ['ink on not-sure fill (not-a-structure notice)', 'ink', 'notSure'],
  ['muted on checked fill', 'muted', 'checked'],
];

test.each(TEXT_PAIRS)('%s reaches 4.5:1', (_name, fg, bg) => {
  expect(contrast(palette[fg], palette[bg])).toBeGreaterThanOrEqual(4.5);
});

// The ribbon labels are printed on shaded cloth: at rest the cloth faces the viewer and is drawn at 95.3% of its base
// colour (cloth.ts shadeTriangle: 0.7 + 0.36 x cos(angle to the light)). The About label must still read there.
test('the About ribbon label reaches 4.5:1 on the copper cloth at its resting shade', () => {
  const shaded = (hex: string, m: number) => {
    const n = parseInt(hex.slice(1), 16);
    const part = (shift: number) => Math.round(((n >> shift) & 255) * m);
    return `#${[16, 8, 0].map((shift) => part(shift).toString(16).padStart(2, '0')).join('')}`;
  };
  expect(contrast(palette.onCopper, shaded(palette.copper, 0.953))).toBeGreaterThanOrEqual(4.5);
});

// Lines that carry meaning need 3:1 against what they sit on (WCAG 1.4.11).
const GRAPHIC_PAIRS: [string, Hex, Hex][] = [
  ['field and icon-button edge on page', 'lineStrong', 'page'],
  ['field and icon-button edge on tile', 'lineStrong', 'tile'],
  ['field edge on egg nog', 'lineStrong', 'egg'],
  ['inspection frame (copper) on page', 'copper', 'page'],
];

test.each(GRAPHIC_PAIRS)('%s reaches 3:1', (_name, fg, bg) => {
  expect(contrast(palette[fg], palette[bg])).toBeGreaterThanOrEqual(3);
});

// The state dots sit on a tile (the result tiles) and on egg nog (the legend and the counts in the panel). A dot never
// stands alone: the state is always written beside it ("Checked", "Review", ...), so it is redundant decoration and
// WCAG 1.4.11 does not apply to it. Where a dot is nevertheless 3:1 or better, that is kept; the ones that are not are
// listed here on purpose, so that a new one cannot slip below it unnoticed.
const DOTS: Hex[] = ['checkedDot', 'reviewDot', 'editedDot', 'notSureDot'];
const DOT_GROUNDS: Hex[] = ['tile', 'egg'];
const DOTS_BELOW_THREE = new Set(['reviewDot on tile', 'reviewDot on egg', 'editedDot on egg']);
const ALL_DOT_PAIRS = DOTS.flatMap((dot) => DOT_GROUNDS.map((ground) => [`${dot} on ${ground}`, dot, ground] as const));

test.each(ALL_DOT_PAIRS.filter(([name]) => !DOTS_BELOW_THREE.has(name)))('dot: %s reaches 3:1', (_name, dot, ground) => {
  expect(contrast(palette[dot], palette[ground])).toBeGreaterThanOrEqual(3);
});

test('the dots below 3:1 are exactly the documented ones (each has its state word beside it)', () => {
  const below = ALL_DOT_PAIRS.filter(([, dot, ground]) => contrast(palette[dot], palette[ground]) < 3).map(([name]) => name);
  expect(new Set(below)).toEqual(DOTS_BELOW_THREE);
});

test('the pinned colours are the ones the direction contract names', () => {
  expect(palette.page).toBe('#FBF5E4');
  expect(palette.tile).toBe('#FFFBF1');
  expect(palette.egg).toBe('#FBEDC3');
  expect(palette.mustard).toBe('#CCB363');
  expect(palette.copper).toBe('#C27E35');
  expect(palette.garnet).toBe('#762908');
  expect(palette.forest).toBe('#1E3226');
  expect(palette.ink).toBe(palette.forest);
  expect(palette.line).toBe('rgba(30, 50, 38, 0.12)');
});

test('the contrast helper agrees with a known pair (black on white is 21:1)', () => {
  expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 5);
  // WCAG's own example: #777 on white is 4.48:1 and fails.
  expect(contrast('#777777', '#ffffff')).toBeLessThan(4.5);
});
