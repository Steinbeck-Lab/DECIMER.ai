/**
 * Caffeine (1,3,7-trimethylxanthine, `Cn1cnc2c1c(=O)n(C)c(=O)n2C`, C8H10N4O2) as a skeletal drawing, for the working
 * screen's structure that draws itself (StructurePlate.tsx). Static data: the atom coordinates are RDKit's
 * `Compute2DCoords` for that SMILES, turned and mirrored (y grows downwards here) so that the six-membered ring is on
 * the left and the imidazole on the right, and centred; one unit is one bond length. Nothing is computed by RDKit in
 * the browser.
 *
 * Atoms carry the purine numbering (N1, C2, N3, C4, C5, C6, N7, C8, N9); `Me1`, `Me3` and `Me7` are the methyl
 * carbons on N1, N3 and N7, `O2` and `O6` the carbonyl oxygens. Skeletal conventions: carbons and methyl groups are
 * not written out, nitrogens and oxygens are letters with a clear space around them, a ring double bond has its
 * second line inside the ring, and a carbonyl is drawn as RDKit draws it: two parallel lines centred on the bond, both
 * running from the carbon to the clear space round the oxygen.
 *
 * The drawing is cut into five fragments, one per stage of the job, each drawn bond by bond while its stage runs:
 *   0 Uploading          the six-membered ring (outline, with its two nitrogens)
 *   1 Reading pages      the five-membered ring (with its two nitrogens)
 *   2 Finding structures the two carbonyl groups (one line of each, and its oxygen)
 *   3 Recognising        the three N-methyl groups
 *   4 Checking           the bond orders: the two ring double bonds and the second line of each carbonyl
 * Together the five fragments are exactly caffeine, with every bond and every atom once.
 */

export type AtomId = 'N1' | 'C2' | 'N3' | 'C4' | 'C5' | 'C6' | 'N7' | 'C8' | 'N9' | 'O2' | 'O6' | 'Me1' | 'Me3' | 'Me7';

export interface Atom {
  element: 'C' | 'N' | 'O';
  /** In bond lengths from the middle of the drawing; y grows downwards, as in SVG. */
  x: number;
  y: number;
  /** The fragment in which its letter arrives (only nitrogens and oxygens are written out). */
  letterIn?: Fragment;
}

export type Fragment = 0 | 1 | 2 | 3 | 4;
/** One fragment per stage of the job (`STAGE_ORDER`). */
export const FRAGMENTS: readonly Fragment[] = [0, 1, 2, 3, 4];

export const ATOMS: Record<AtomId, Atom> = {
  N1: { element: 'N', x: -0.8606, y: 0.866, letterIn: 0 },
  C2: { element: 'C', x: -1.3606, y: 0 },
  N3: { element: 'N', x: -0.8606, y: -0.866, letterIn: 0 },
  C4: { element: 'C', x: 0.1394, y: -0.866 },
  C5: { element: 'C', x: 0.6394, y: 0 },
  C6: { element: 'C', x: 0.1394, y: 0.866 },
  N7: { element: 'N', x: 1.6175, y: -0.2079, letterIn: 1 },
  C8: { element: 'C', x: 1.722, y: -1.2024 },
  N9: { element: 'N', x: 0.8085, y: -1.6092, letterIn: 1 },
  O2: { element: 'O', x: -2.3606, y: 0, letterIn: 2 },
  O6: { element: 'O', x: 0.6394, y: 1.7321, letterIn: 2 },
  Me1: { element: 'C', x: -1.3606, y: 1.7321 },
  Me3: { element: 'C', x: -1.3606, y: -1.7321 },
  Me7: { element: 'C', x: 2.3606, y: 0.4612 },
};

export interface Bond {
  /** Drawn from `from` to `to`. */
  from: AtomId;
  to: AtomId;
  order: 1 | 2;
  /** The fragment of the bond's first line. */
  fragment: Fragment;
  /**
   * A double bond's second line: always in the last fragment. `inside` names the ring it lies in (the first line is on the
   * bond, the second inside the ring, a little shorter); `centred` is a carbonyl's, two lines of full length at
   * either side of the bond (the first line is at one side already, in the bond's own fragment).
   */
  second?: { inside: 'six' | 'five' } | { centred: true };
}

export const BONDS: readonly Bond[] = [
  // The six-membered ring, round from N1 (C4 to C5 is the double bond they share with the five-membered ring).
  { from: 'N1', to: 'C2', order: 1, fragment: 0 },
  { from: 'C2', to: 'N3', order: 1, fragment: 0 },
  { from: 'N3', to: 'C4', order: 1, fragment: 0 },
  { from: 'C4', to: 'C5', order: 2, fragment: 0, second: { inside: 'six' } },
  { from: 'C5', to: 'C6', order: 1, fragment: 0 },
  { from: 'C6', to: 'N1', order: 1, fragment: 0 },
  // The five-membered ring.
  { from: 'C5', to: 'N7', order: 1, fragment: 1 },
  { from: 'N7', to: 'C8', order: 1, fragment: 1 },
  { from: 'C8', to: 'N9', order: 2, fragment: 1, second: { inside: 'five' } },
  { from: 'N9', to: 'C4', order: 1, fragment: 1 },
  // The carbonyls.
  { from: 'C2', to: 'O2', order: 2, fragment: 2, second: { centred: true } },
  { from: 'C6', to: 'O6', order: 2, fragment: 2, second: { centred: true } },
  // The N-methyl groups.
  { from: 'N1', to: 'Me1', order: 1, fragment: 3 },
  { from: 'N3', to: 'Me3', order: 1, fragment: 3 },
  { from: 'N7', to: 'Me7', order: 1, fragment: 3 },
];

const SIX_RING: readonly AtomId[] = ['N1', 'C2', 'N3', 'C4', 'C5', 'C6'];
const FIVE_RING: readonly AtomId[] = ['C5', 'N7', 'C8', 'N9', 'C4'];

/** The fragment that holds the second line of every double bond (the bond orders are what "Checking" settles). */
const LAST_FRAGMENT: Fragment = 4;

/** Drawing constants, in the drawing's own units (the SVG's user units). */
const BOND_LENGTH = 26;
/** Clear space between a letter's centre and the end of a bond that meets it. */
const LETTER_GAP = 8.5;
/** Distance of a double bond's second line from the first. */
const SECOND_LINE_OFFSET = 4.6;
/** How much shorter than the first line a ring's second line is, at each end. */
const SECOND_LINE_TRIM = 3.4;
/** A carbonyl's two lines are this far either side of the bond (as far apart as a ring's two lines are). */
const CARBONYL_OFFSET = SECOND_LINE_OFFSET / 2;

/** The drawing's view box: wide enough for the oxygen on the left and the methyl on the right, and the letters. */
export const VIEW_BOX = '-72 -54 144 108';

type Point = readonly [number, number];

const pointOf = (id: AtomId): Point => [ATOMS[id].x * BOND_LENGTH, ATOMS[id].y * BOND_LENGTH];
const centroid = (ids: readonly AtomId[]): Point => {
  const points = ids.map(pointOf);
  return [points.reduce((s, p) => s + p[0], 0) / points.length, points.reduce((s, p) => s + p[1], 0) / points.length];
};
const round = (n: number) => Math.round(n * 100) / 100;
const line = (a: Point, b: Point) => `M${round(a[0])} ${round(a[1])}L${round(b[0])} ${round(b[1])}`;

type Line = readonly [Point, Point];

/** The bond itself, from where it leaves its first atom to where it reaches its second (clear of a letter at either end). */
function axis(bond: Bond): Line {
  const a = pointOf(bond.from);
  const b = pointOf(bond.to);
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const ux = (b[0] - a[0]) / length;
  const uy = (b[1] - a[1]) / length;
  const startGap = ATOMS[bond.from].letterIn === undefined ? 0 : LETTER_GAP;
  const endGap = ATOMS[bond.to].letterIn === undefined ? 0 : LETTER_GAP;
  return [
    [a[0] + ux * startGap, a[1] + uy * startGap],
    [b[0] - ux * endGap, b[1] - uy * endGap],
  ];
}

/** A line moved sideways by `distance` (positive: to the right of its direction, as seen on the screen). */
function moved([a, b]: Line, distance: number): Line {
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const nx = (-(b[1] - a[1]) / length) * distance;
  const ny = ((b[0] - a[0]) / length) * distance;
  return [
    [a[0] + nx, a[1] + ny],
    [b[0] + nx, b[1] + ny],
  ];
}

/** A bond's first line: on the bond, or (a carbonyl, whose second line is the mirror of it) half a double bond to one side. */
function firstLine(bond: Bond): Line {
  const line = axis(bond);
  return bond.second && 'centred' in bond.second ? moved(line, -CARBONYL_OFFSET) : line;
}

/** A double bond's second line: inside its ring, parallel and a little shorter, or the other side of a carbonyl's bond. */
function secondLine(bond: Bond & { second: NonNullable<Bond['second']> }): Line {
  const line = axis(bond);
  if ('centred' in bond.second) return moved(line, CARBONYL_OFFSET);
  const [a, b] = line;
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const ux = (b[0] - a[0]) / length;
  const uy = (b[1] - a[1]) / length;
  // Towards the middle of the ring it lies in.
  const ring = centroid(bond.second.inside === 'six' ? SIX_RING : FIVE_RING);
  const middle: Point = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const sign = Math.sign(-uy * (ring[0] - middle[0]) + ux * (ring[1] - middle[1]));
  const trimmed: Line = [
    [a[0] + ux * SECOND_LINE_TRIM, a[1] + uy * SECOND_LINE_TRIM],
    [b[0] - ux * SECOND_LINE_TRIM, b[1] - uy * SECOND_LINE_TRIM],
  ];
  return moved(trimmed, sign * SECOND_LINE_OFFSET);
}

export interface Stroke {
  /** An SVG path of one straight line. */
  d: string;
  fragment: Fragment;
  /** Its place in the fragment's drawing: the lines of a fragment are drawn one after the other, in this order. */
  order: number;
}

export interface Letter {
  text: 'N' | 'O';
  x: number;
  y: number;
  fragment: Fragment;
  /** Arrives after the lines of its fragment, in this order. */
  order: number;
}

/** What a fragment is made of. */
export interface FragmentParts {
  strokes: readonly Stroke[];
  letters: readonly Letter[];
}

function build(): readonly FragmentParts[] {
  const strokes: Stroke[][] = FRAGMENTS.map(() => []);
  const push = (fragment: Fragment, d: string) => strokes[fragment]?.push({ d, fragment, order: strokes[fragment].length });
  for (const bond of BONDS) push(bond.fragment, line(...firstLine(bond)));
  for (const bond of BONDS) if (bond.second) push(LAST_FRAGMENT, line(...secondLine({ ...bond, second: bond.second })));
  return FRAGMENTS.map((fragment) => {
    const mine = strokes[fragment] ?? [];
    const letters: Letter[] = [];
    for (const [id, atom] of Object.entries(ATOMS) as [AtomId, Atom][]) {
      if (atom.letterIn !== fragment) continue;
      const [x, y] = pointOf(id);
      letters.push({ text: atom.element === 'O' ? 'O' : 'N', x: round(x), y: round(y), fragment, order: mine.length + letters.length });
    }
    return { strokes: mine, letters };
  });
}

/** The five fragments in drawing order: the lines (each one drawn by its stroke-dashoffset) and the letters (faded in). */
export const FRAGMENT_PARTS: readonly FragmentParts[] = build();
