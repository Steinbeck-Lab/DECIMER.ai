import { expect, test } from 'vitest';
import { ATOMS, BONDS, FRAGMENT_PARTS, FRAGMENTS, VIEW_BOX, type AtomId } from './caffeine';

const ids = Object.keys(ATOMS) as AtomId[];
const VALENCE = { C: 4, N: 3, O: 2 };

test('the drawing is caffeine, C8H10N4O2: every atom has its valence, filled up with hydrogen', () => {
  const orderSum = (id: AtomId) =>
    BONDS.filter((b) => b.from === id || b.to === id).reduce((sum, b) => sum + b.order, 0);
  const count = (element: string) => ids.filter((id) => ATOMS[id].element === element).length;
  expect([count('C'), count('N'), count('O')]).toEqual([8, 4, 2]);
  let hydrogens = 0;
  for (const id of ids) {
    const open = VALENCE[ATOMS[id].element] - orderSum(id);
    expect(open, id).toBeGreaterThanOrEqual(0);
    hydrogens += open;
  }
  // Three methyls and the CH of the imidazole.
  expect(hydrogens).toBe(10);
});

test('it is one molecule with two rings, and no bond is drawn twice', () => {
  const pairs = BONDS.map((b) => [b.from, b.to].sort().join('-'));
  expect(new Set(pairs).size).toBe(BONDS.length);
  expect(BONDS).toHaveLength(15);
  expect(BONDS.length - ids.length + 1).toBe(2); // bonds - atoms + 1 for a connected graph
  const reached = new Set<AtomId>(['N1']);
  for (let again = true; again; ) {
    again = false;
    for (const b of BONDS) {
      if (reached.has(b.from) !== reached.has(b.to)) {
        reached.add(b.from).add(b.to);
        again = true;
      }
    }
  }
  expect(reached.size).toBe(ids.length);
});

test('the double bonds are the two carbonyls and the C4=C5 and C8=N9 of the rings, and each has a second line', () => {
  const doubles = BONDS.filter((b) => b.order === 2).map((b) => `${b.from}=${b.to}`);
  expect(doubles.sort()).toEqual(['C2=O2', 'C4=C5', 'C6=O6', 'C8=N9']);
  expect(BONDS.every((b) => (b.order === 2) === (b.second !== undefined))).toBe(true);
});

test('the coordinates are a skeletal drawing: every bond is one bond length', () => {
  for (const b of BONDS) {
    const a = ATOMS[b.from];
    const c = ATOMS[b.to];
    expect(Math.hypot(a.x - c.x, a.y - c.y), `${b.from}-${b.to}`).toBeCloseTo(1, 2);
  }
});

test('only nitrogens and oxygens are written out, each in the fragment that draws its first bond', () => {
  for (const id of ids) {
    const atom = ATOMS[id];
    expect(atom.letterIn !== undefined, id).toBe(atom.element !== 'C');
    if (atom.letterIn === undefined) continue;
    const first = Math.min(...BONDS.filter((b) => b.from === id || b.to === id).map((b) => b.fragment));
    expect(atom.letterIn, id).toBe(first);
  }
  const letters = FRAGMENT_PARTS.map((part) => part.letters.map((l) => l.text).join(''));
  expect(letters).toEqual(['NN', 'NN', 'OO', '', '']);
});

test('five fragments, one per stage: the rings, the carbonyls, the methyls, and the bond orders last', () => {
  expect(FRAGMENTS).toHaveLength(5);
  expect(FRAGMENT_PARTS.map((part) => part.strokes.length)).toEqual([6, 4, 2, 3, 4]);
  // Every line once: 15 bonds plus the second line of each of the 4 double bonds.
  expect(FRAGMENT_PARTS.flatMap((part) => part.strokes)).toHaveLength(BONDS.length + 4);
  // The lines of a fragment are drawn in order, one after the other.
  for (const part of FRAGMENT_PARTS) expect(part.strokes.map((s) => s.order)).toEqual(part.strokes.map((_, i) => i));
});

test('every line is one straight stroke inside the view box, clear of the letters', () => {
  const [x0, y0, w, h] = VIEW_BOX.split(' ').map(Number) as [number, number, number, number];
  for (const { d } of FRAGMENT_PARTS.flatMap((part) => part.strokes)) {
    const numbers = d.match(/-?\d+(\.\d+)?/g)?.map(Number) ?? [];
    expect(d).toMatch(/^M-?[\d.]+ -?[\d.]+L-?[\d.]+ -?[\d.]+$/);
    for (const [i, n] of numbers.entries()) {
      const [min, size] = i % 2 === 0 ? [x0, w] : [y0, h];
      expect(n).toBeGreaterThan(min);
      expect(n).toBeLessThan(min + size);
    }
  }
  for (const letter of FRAGMENT_PARTS.flatMap((part) => part.letters)) {
    for (const { d } of FRAGMENT_PARTS.flatMap((part) => part.strokes)) {
      const [x1, y1, x2, y2] = (d.match(/-?\d+(\.\d+)?/g) ?? []).map(Number) as [number, number, number, number];
      // The closest the line comes to the letter's centre: at least the clear space, less a little for the second lines' offset.
      const length = Math.hypot(x2 - x1, y2 - y1);
      const t = Math.max(0, Math.min(1, ((letter.x - x1) * (x2 - x1) + (letter.y - y1) * (y2 - y1)) / length ** 2));
      expect(Math.hypot(x1 + t * (x2 - x1) - letter.x, y1 + t * (y2 - y1) - letter.y)).toBeGreaterThan(5);
    }
  }
});

test('a carbonyl is drawn as RDKit draws it: two parallel lines centred on the bond, both reaching the clear space round the oxygen', () => {
  const BOND = 26; // the drawing's bond length in its own units (caffeine.ts)
  const numbers = (d: string) => (d.match(/-?\d+(\.\d+)?/g) ?? []).map(Number) as [number, number, number, number];
  const strokes = FRAGMENT_PARTS.flatMap((part) => part.strokes.map((s) => ({ ...s, n: numbers(s.d) })));
  for (const [carbon, oxygen] of [['C2', 'O2'], ['C6', 'O6']] as const) {
    const o = [ATOMS[oxygen].x * BOND, ATOMS[oxygen].y * BOND] as const;
    const c = [ATOMS[carbon].x * BOND, ATOMS[carbon].y * BOND] as const;
    const lines = strokes.filter((s) => Math.hypot(s.n[2] - o[0], s.n[3] - o[1]) < 10);
    expect(lines.map((l) => l.fragment).sort()).toEqual([2, 4]);
    const [p, q] = lines as [(typeof lines)[number], (typeof lines)[number]];
    // Parallel, equally long, 4.6 apart.
    expect(q.n[2] - q.n[0]).toBeCloseTo(p.n[2] - p.n[0], 1);
    expect(q.n[3] - q.n[1]).toBeCloseTo(p.n[3] - p.n[1], 1);
    expect(Math.hypot(q.n[0] - p.n[0], q.n[1] - p.n[1])).toBeCloseTo(4.6, 1);
    // Centred on the bond: the middle of their starts is the carbon, the middle of their ends the oxygen's clear space.
    expect(Math.hypot((p.n[0] + q.n[0]) / 2 - c[0], (p.n[1] + q.n[1]) / 2 - c[1])).toBeLessThan(0.1);
    expect(Math.hypot((p.n[2] + q.n[2]) / 2 - o[0], (p.n[3] + q.n[3]) / 2 - o[1])).toBeCloseTo(8.5, 1);
  }
});
