import {
  Button,
  createTheme,
  type CSSVariablesResolver,
  type MantineColorsTuple,
} from '@mantine/core';

/**
 * The whole visual world of "Journal Scheme" in one place (direction contract:
 * .impeccable/surfaces/web-src-app-tsx.md). CSS reads these as `--dc-*`
 * variables (see `cssVariablesResolver`); nothing else hard-codes a colour.
 *
 * Text pairs are checked for WCAG 4.5:1 in theme.test.ts.
 */
export const palette = {
  white: '#ffffff',
  ink: '#1b1b24',
  /** Secondary text: names, captions. */
  ink2: '#33334a',
  /** Tertiary text: nav, capacity line, hints. */
  muted: '#4b4b5a',
  /** Italic "no verified name". */
  mutedItalic: '#5c5c70',
  /** Hairlines on white (crop inset, field edges). */
  line: '#dcdce6',
  /** Quiet fill for "unchecked" and buttons on sky. */
  paper: '#f3f3f8',
  link: '#2b5bb5',
  linkHover: '#173f8a',

  /** Checked. */
  mint: '#d6f5e3',
  mintInk: '#17603a',
  mintDot: '#7fd3a6',
  mintButton: '#b7e8cc',
  mintButtonInk: '#10432a',
  /** Needs review. */
  butter: '#fff3c4',
  butterInk: '#6b4f00',
  butterDot: '#f0cc55',
  /** Edited. */
  lilac: '#ece2ff',
  lilacInk: '#563aa0',
  lilacDot: '#b39af0',
  /** Not sure it is a structure. */
  peach: '#ffe2d4',
  peachInk: '#8a3516',
  peachDot: '#f2a385',
  /** Selected tile and the editor panel. */
  sky: '#eaf2ff',
  skyDeep: '#dcebff',
  skyLine: '#c3cde6',
  skyHover: '#f3f7ff',
} as const;

export const fonts = {
  display: "'Archivo', 'Source Sans 3', system-ui, sans-serif",
  ui: "'Source Sans 3', system-ui, -apple-system, 'Segoe UI', sans-serif",
  mono: "'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, monospace",
} as const;

/** Ink ramp for Mantine components (Menu hover, Tooltip, Modal close). Shade 8 is the ink. */
const ink: MantineColorsTuple = [
  '#f5f5f9',
  '#ebebf2',
  '#dcdce6',
  '#c3c3d3',
  '#9d9db2',
  '#7a7a92',
  '#5c5c70',
  '#33334a',
  '#1b1b24',
  '#101017',
];

export const theme = createTheme({
  primaryColor: 'ink',
  primaryShade: 8,
  colors: { ink },
  black: palette.ink,
  white: palette.white,
  fontFamily: fonts.ui,
  fontFamilyMonospace: fonts.mono,
  headings: { fontFamily: fonts.display, fontWeight: '800' },
  defaultRadius: 'md',
  cursorType: 'pointer',
  other: { palette, fonts },
  components: {
    Button: Button.extend({ defaultProps: { radius: 'xl' } }),
  },
});

const CSS_NAMES: Record<keyof typeof palette, string> = {
  white: 'white',
  ink: 'ink',
  ink2: 'ink-2',
  muted: 'muted',
  mutedItalic: 'muted-italic',
  line: 'line',
  paper: 'paper',
  link: 'link',
  linkHover: 'link-hover',
  mint: 'mint',
  mintInk: 'mint-ink',
  mintDot: 'mint-dot',
  mintButton: 'mint-button',
  mintButtonInk: 'mint-button-ink',
  butter: 'butter',
  butterInk: 'butter-ink',
  butterDot: 'butter-dot',
  lilac: 'lilac',
  lilacInk: 'lilac-ink',
  lilacDot: 'lilac-dot',
  peach: 'peach',
  peachInk: 'peach-ink',
  peachDot: 'peach-dot',
  sky: 'sky',
  skyDeep: 'sky-deep',
  skyLine: 'sky-line',
  skyHover: 'sky-hover',
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
