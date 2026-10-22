import { expect, test } from 'vitest';
import { imageSrc, tierLabel, tierText } from './format';

test('a URL is used as it is', () => {
  expect(imageSrc('/assets/mol1.svg')).toBe('/assets/mol1.svg');
});

test('inline SVG becomes a data URL, so it is shown as an image and never parsed into the page', () => {
  const out = imageSrc("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>");
  expect(out.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
  expect(out).not.toContain('<');
});

test('tiers: a known one reads well, an unknown one is made readable', () => {
  expect(tierLabel('pin_verified')).toBe('PIN verified');
  expect(tierLabel('some_new_tier')).toBe('some new tier');
});

test('hover text says why there is no name', () => {
  expect(tierText('x', 'pin_verified')).toBe('Orthonym: PIN verified (OPSIN round trip)');
  expect(tierText(null, null)).toContain('Orthonym declined');
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
