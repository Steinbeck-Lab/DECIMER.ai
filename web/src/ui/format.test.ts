import { expect, test } from 'vitest';
import { imageSrc, nameHover } from './format';

test('a URL is used as it is', () => {
  expect(imageSrc('/assets/mol1.svg')).toBe('/assets/mol1.svg');
});

test('inline SVG becomes a data URL, so it is shown as an image and never parsed into the page', () => {
  const out = imageSrc("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>");
  expect(out.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
  expect(out).not.toContain('<');
});

// ---- the name hover text (P4-15, decision D14) --------------------------------------------------

test.each([
  ['pin_verified', 'Preferred IUPAC name'],
  // Only a certified PIN may be called "the preferred IUPAC name".
  ['pin_unverified', 'Name in preferred IUPAC form; its preferred status is not certified'],
  ['systematic_verified', 'Systematic name (not the preferred IUPAC name)'],
])('tier %s reads "%s"', (tier, line) => {
  expect(nameHover('x', tier, null)).toEqual([line]);
});

test.each([
  ['opsin', 'OPSIN reads this name back to exactly this structure'],
  ['opsin_split', 'OPSIN reads this name back to this structure drawn without dative bonds'],
  ['opsin_constitution', 'OPSIN confirms the connectivity; stereo checked by CIP labelling'],
  ['opsin_twin', 'OPSIN reads an equivalent spelling back to this structure'],
  ['opsin_acids', 'OPSIN reads each component acid; together they rebuild this structure'],
  ['identity', 'Exact match in a curated list'],
])('verified %s reads "%s", under the tier line', (verified, line) => {
  expect(nameHover('x', 'pin_verified', verified)).toEqual(['Preferred IUPAC name', line]);
  expect(nameHover('x', 'systematic_verified', verified)).toEqual(['Systematic name (not the preferred IUPAC name)', line]);
});

test('a verified value this version does not know shows only the tier line: nothing is made up', () => {
  expect(nameHover('x', 'pin_verified', 'opsin_future_method')).toEqual(['Preferred IUPAC name']);
  expect(nameHover('x', 'systematic_verified', '')).toEqual(['Systematic name (not the preferred IUPAC name)']);
});

test('a tier this version does not know is shown as Orthonym\'s own id, never read as a kind of PIN', () => {
  // A new pin_* tier must not inherit the claim "Preferred IUPAC name" by its prefix.
  expect(nameHover('x', 'pin_anything_new', null)).toEqual(['Orthonym tier: pin_anything_new']);
  expect(nameHover('x', 'best_effort', null)).toEqual(['Orthonym tier: best_effort']);
  expect(nameHover('x', 'best_effort', 'opsin')).toEqual(['Orthonym tier: best_effort', 'OPSIN reads this name back to exactly this structure']);
});

test('a name with neither a tier nor a verification (an older API) has nothing to say about it', () => {
  expect(nameHover('x', null, null)).toEqual([]);
  expect(nameHover('x', null, 'opsin')).toEqual(['OPSIN reads this name back to exactly this structure']);
});

test('no name: the hover states the fact and no cause, whatever tier and verified say', () => {
  // Names may be off altogether (no chemistry service), and Orthonym also returns none on a timeout, with the engine
  // unavailable, for an invalid SMILES, or when the tier is not shipped: "declined" or "failed the OPSIN check" would
  // claim a cause the page does not know, and "Orthonym gave" would blame a service that may not be running.
  expect(nameHover(null, null, null)).toEqual(['No verified name']);
  expect(nameHover(null, 'pin_verified', 'opsin')).toEqual(['No verified name']);
  const [line] = nameHover(null, null, null);
  expect(line).not.toMatch(/declin|OPSIN check|refus|fail|Orthonym/i);
});

// RDKit's MolDraw2DSVG output begins with an XML prolog, then the <svg> element.
const RDKIT_SVG = `<?xml version='1.0' encoding='iso-8859-1'?>
<svg version='1.1' baseProfile='full' xmlns='http://www.w3.org/2000/svg' width='360px' height='260px' viewBox='0 0 360 260'>
<rect style='opacity:1.0;fill:#FFFFFF;stroke:none' width='360.0' height='260.0' x='0.0' y='0.0'> </rect>
<path class='bond-0' d='M 10,10 L 20,20' style='fill:none;stroke:#000000' />
</svg>`;

test('RDKit SVG with an XML prolog is inline SVG, not a URL', () => {
  const out = imageSrc(RDKIT_SVG);
  expect(out.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
  expect(decodeURIComponent(out.slice(out.indexOf(',') + 1))).toBe(RDKIT_SVG);
});

test('inline SVG is recognised after leading whitespace, a doctype or a comment', () => {
  for (const svg of ['  \n<svg xmlns="http://www.w3.org/2000/svg"/>', '<!DOCTYPE svg><svg/>', '<!-- made by RDKit --><svg/>']) {
    expect(imageSrc(svg).startsWith('data:image/svg+xml')).toBe(true);
  }
});

test('anything that does not start with < is a URL, including ones that merely contain svg', () => {
  expect(imageSrc('/api/jobs/abc/media/depiction-3.svg')).toBe('/api/jobs/abc/media/depiction-3.svg');
  expect(imageSrc('https://example.org/a.svg')).toBe('https://example.org/a.svg');
});
