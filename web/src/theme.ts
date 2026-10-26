import { createTheme, type CSSVariablesResolver, type MantineColorsTuple } from '@mantine/core';

/**
 * The whole visual world in one place (direction contract: .impeccable/surfaces/web-src-app-tsx.md): a calm, flat
 * egg-nog page, velvet-forest ink, and a copper inspection frame. CSS reads these as `--dc-*` variables (see
 * `cssVariablesResolver`); nothing else hard-codes a colour. Surfaces are flat: no gradients, no dark mode.
 *
 * Text pairs are checked for WCAG 4.5:1 in theme.test.ts. Copper and mustard on their own are not text colours on the
 * cream grounds: text uses the `*Ink` tokens, which are the same hues, darkened.
 */
export const palette = {
  /** The white behind scanned crops and page images (they are white by nature) and Ketcher's canvas. */
  white: '#ffffff',

  /** The page. */
  page: '#FBF5E4',
  /** Tiles and the drop pill: the lightest cream, above the page. */
  tile: '#FFFBF1',
  /** Egg nog: fields, the selected tile, the editor panel, the secondary porcelain pill, the "unchecked" fill. */
  egg: '#FBEDC3',
  /** Egg nog pressed: a drag over the drop zone, a pressed secondary pill. */
  eggDeep: '#F5E2A8',
  /** Mustard: "needs review". */
  mustard: '#CCB363',
  /** Copper: the inspection frame, icon circles, measuring lines, "edited". */
  copper: '#C27E35',
  /** Garnet: problems and hover emphasis, "not sure it is a structure". */
  garnet: '#762908',
  /** Velvet forest: ink and primary, "checked". */
  forest: '#1E3226',

  /** Text on the cream grounds. */
  ink: '#1E3226',
  /** Secondary text: names, captions. */
  ink2: '#2F4538',
  /** Tertiary text: nav, capacity line, mono labels, hints. */
  muted: '#4F6156',
  /** Italic "no verified name". */
  mutedItalic: '#4F6156',
  /** Copper as text (labels, measuring-line captions): the same hue, dark enough to read. */
  copperInk: '#8A5319',
  /** Text on a copper fill (the frame's label, the About ribbon): the darkest forest, the only ink that reaches 4.5:1 on copper. */
  onCopper: '#14221A',
  /** Hairline between surfaces: forest at 12%. */
  line: 'rgba(30, 50, 38, 0.12)',
  /** The edge of a control that must be seen (3:1 on the page): a field, an icon button. */
  lineStrong: '#7A8A7E',
  link: '#1E3226',
  linkHover: '#762908',

  /*
   * State vocabulary of a result: fill, the text on that fill, and the dot. Unchecked is egg nog with `muted` text.
   */
  /** Checked (forest). */
  checked: '#DDE6D3',
  checkedInk: '#1E3226',
  checkedDot: '#1E3226',
  checkedButton: '#C9D8C0',
  checkedButtonInk: '#1E3226',
  /** Needs review (mustard). */
  review: '#F3E4AD',
  reviewInk: '#5E490C',
  reviewDot: '#CCB363',
  /** Edited (copper). */
  edited: '#F5DEC2',
  editedInk: '#7A4814',
  editedDot: '#C27E35',
  /** Not sure it is a structure (garnet), and upload problems. */
  notSure: '#F1D9CA',
  notSureInk: '#762908',
  notSureDot: '#762908',
} as const;

export const fonts = {
  display: "'Wix Madefor Display', 'Wix Madefor Text', system-ui, sans-serif",
  ui: "'Wix Madefor Text', system-ui, -apple-system, 'Segoe UI', sans-serif",
  /** SMILES, measurements, labels: a face where 1 and l, 0 and O are told apart. */
  mono: "'Geist Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace",
} as const;

/** Forest ramp for Mantine components (Menu hover, Tooltip, Modal close). Shade 8 is the ink. */
const ink: MantineColorsTuple = [
  '#f6f1e2',
  '#ede7d2',
  '#dfdac6',
  '#c4c9b6',
  '#9aa899',
  '#728577',
  '#4f6156',
  '#2f4538',
  '#1e3226',
  '#14221a',
];

export const theme = createTheme({
  primaryColor: 'ink',
  primaryShade: 8,
  colors: { ink },
  black: palette.ink,
  white: palette.tile,
  fontFamily: fonts.ui,
  fontFamilyMonospace: fonts.mono,
  headings: { fontFamily: fonts.display, fontWeight: '500' },
  defaultRadius: 'md',
  cursorType: 'pointer',
  other: { palette, fonts },
});

const CSS_NAMES: Record<keyof typeof palette, string> = {
  white: 'white',
  page: 'page',
  tile: 'tile',
  egg: 'egg',
  eggDeep: 'egg-deep',
  mustard: 'mustard',
  copper: 'copper',
  garnet: 'garnet',
  forest: 'forest',
  ink: 'ink',
  ink2: 'ink-2',
  muted: 'muted',
  mutedItalic: 'muted-italic',
  copperInk: 'copper-ink',
  onCopper: 'on-copper',
  line: 'line',
  lineStrong: 'line-strong',
  link: 'link',
  linkHover: 'link-hover',
  checked: 'checked',
  checkedInk: 'checked-ink',
  checkedDot: 'checked-dot',
  checkedButton: 'checked-button',
  checkedButtonInk: 'checked-button-ink',
  review: 'review',
  reviewInk: 'review-ink',
  reviewDot: 'review-dot',
  edited: 'edited',
  editedInk: 'edited-ink',
  editedDot: 'edited-dot',
  notSure: 'not-sure',
  notSureInk: 'not-sure-ink',
  notSureDot: 'not-sure-dot',
};

/** Publishes the palette and fonts as `--dc-*` CSS variables on :root. */
export const cssVariablesResolver: CSSVariablesResolver = () => {
  const variables: Record<string, string> = {
    '--dc-font-display': fonts.display,
    '--dc-font-ui': fonts.ui,
    '--dc-font-mono': fonts.mono,
  };
  for (const key of Object.keys(palette) as (keyof typeof palette)[]) {
    variables[`--dc-${CSS_NAMES[key]}`] = palette[key];
  }
  return { variables, light: {}, dark: {} };
};
