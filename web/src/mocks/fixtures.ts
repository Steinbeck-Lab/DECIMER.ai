/**
 * Fixtures for the mock API: the structures of Figure 2 of Zou et al., BJOC
 * 2024 (CC BY 4.0), the golden-set paper in `golden/`.
 *
 * Provenance of the rasters and drawings in ./fixtures:
 * - cropN.png: crops of Figure 2 of the paper (CC BY 4.0), cut by DECIMER
 *   Segmentation for the design mock-ups, 2026-10-22.
 * - molN.svg: RDKit depictions of the label SMILES.
 * - compounds.json: SMILES, InChIKeys and IUPAC names from the checked golden
 *   labels (a name is null where Orthonym declined); 2D molfiles generated from
 *   the SMILES with RDKit (rdDepictor, MolToMolBlock). Only 8 of the 12
 *   compounds of the figure have crops here, numbered as in the paper.
 *
 * The statuses are examples: 6 has a low confidence ("review") and 12 is
 * flagged by the classifier ("not sure it is a structure"), as in the approved
 * mock-up.
 */
import type { Result } from '../api/contract';
import compounds from './fixtures/compounds.json';

const crops = import.meta.glob<string>('./fixtures/crop*.png', {
  eager: true,
  query: '?url',
  import: 'default',
});
const drawings = import.meta.glob<string>('./fixtures/mol*.svg', {
  eager: true,
  query: '?url',
  import: 'default',
});

export interface FixtureCompound {
  number: number;
  common: string;
  iupac: string | null;
  smiles: string;
  inchikey: string;
  molfile: string;
  cropUrl: string;
  svgUrl: string;
}

function asset(map: Record<string, string>, key: string): string {
  const url = map[key];
  if (!url) throw new Error(`mock fixture missing: ${key}`);
  return url;
}

export const FIXTURE_COMPOUNDS: FixtureCompound[] = compounds.map((c) => ({
  ...c,
  cropUrl: asset(crops, `./fixtures/crop${c.number}.png`),
  svgUrl: asset(drawings, `./fixtures/mol${c.number}.svg`),
}));

const CONFIDENCE: Record<number, number> = { 6: 0.62 };
const NOT_A_STRUCTURE = new Set([12]);

/** Page of the document (0-based) the figure sits on. The paper's Figure 2 is on page 3. */
const FIGURE_PAGE = 2;

/**
 * `count` results. With more than the 8 fixtures the compounds repeat, which
 * only the "twenty structures" scenario uses. `numbering: 'paper'` keeps the
 * paper's compound numbers; otherwise results are numbered 1..count.
 */
export function fixtureResults(
  count: number,
  numbering: 'paper' | 'sequential' = 'paper',
  options: { classifier?: 'answers' | 'unavailable' } = {},
): Result[] {
  const classifierUnavailable = options.classifier === 'unavailable';
  return Array.from({ length: count }, (_, index) => {
    const c = FIXTURE_COMPOUNDS[index % FIXTURE_COMPOUNDS.length];
    if (!c) throw new Error('no fixtures');
    const col = index % 4;
    const row = Math.floor(index / 4);
    return {
      index,
      number: numbering === 'paper' && count <= FIXTURE_COMPOUNDS.length ? c.number : index + 1,
      pageIndex: FIGURE_PAGE,
      bbox: [60 + col * 330, 90 + row * 300, 60 + col * 330 + 300, 90 + row * 300 + 270] as const,
      cropUrl: c.cropUrl,
      svg: c.svgUrl,
      smiles: c.smiles,
      inchikey: c.inchikey,
      molfile: c.molfile,
      valid: true,
      isStructure: classifierUnavailable ? null : !NOT_A_STRUCTURE.has(c.number),
      confidence: CONFIDENCE[c.number] ?? 0.95,
      name: c.iupac,
      nameTier: c.iupac ? 'pin_verified' : null,
    };
  });
}
