/**
 * Fixtures for the mock API: the structures of Figure 2 of Zou et al., BJOC
 * 2024 (CC BY 4.0), the golden-set paper in `golden/`.
 *
 * Provenance of the rasters and drawings in ./fixtures:
 * - cropN.png: crops of Figure 2 of the paper (CC BY 4.0), cut by DECIMER
 *   Segmentation.
 * - page.png: page 3 of the paper (the one with Figure 2), rendered at 72 DPI and reduced to 64 colours; every
 *   page of a mock job is this one image (`pageUrls`, the "Display uploaded document" strip).
 * - molN.svg: RDKit depictions of the label SMILES.
 * - compounds.json: SMILES, InChIKeys and IUPAC names from the checked golden
 *   labels (a name is null where Orthonym gave none); 2D molfiles generated from
 *   the SMILES with RDKit (rdDepictor, MolToMolBlock). Only 8 of the 12
 *   compounds of the figure have crops here, numbered as in the paper.
 *
 * The statuses are examples: 6 has a low confidence ("review") and 12 is
 * flagged by the classifier ("not sure it is a structure"), as in the
 * design.
 */
import type { Result } from '../api/contract';
import compounds from './fixtures/compounds.json';

const crops = import.meta.glob<string>('./fixtures/crop*.png', {
  eager: true,
  query: '?url',
  import: 'default',
});
/** The image behind every page URL of a mock job. */
export { default as FIXTURE_PAGE_URL } from './fixtures/page.png';

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

/**
 * How Orthonym named each compound: the tier and the way the name was checked. Mixed on purpose,
 * so the hover text shows each kind. Compounds 1 and 2 have no verified name.
 */
const NAME_CHECK: Record<number, { tier: string; verified: string }> = {
  4: { tier: 'pin_verified', verified: 'opsin' },
  5: { tier: 'pin_verified', verified: 'opsin_split' },
  6: { tier: 'systematic_verified', verified: 'opsin_constitution' },
  7: { tier: 'pin_unverified', verified: 'opsin_twin' },
  11: { tier: 'systematic_verified', verified: 'identity' },
  12: { tier: 'pin_verified', verified: 'opsin_acids' },
};
const NOT_A_STRUCTURE = new Set([12]);

/** Page of the document (0-based) the figure sits on. The paper's Figure 2 is on page 3. */
const FIGURE_PAGE = 2;

/**
 * The result as the API leaves one that RDKit refused and only CDK accepted: `valid` is false (RDKit's
 * word), `validBy` is `cdk`, there is no InChIKey and no name. The SMILES is DECIMER's own text; the molfile and the
 * drawing stay the fixture's (the real ones are CDK's molblock and RDKit's drawing of it, which a mock cannot make).
 */
export function asCdkOnly(result: Result): Result {
  return { ...result, valid: false, validBy: 'cdk', inchikey: '', name: null, nameTier: null, nameVerified: null };
}

/**
 * The result as the API leaves one the model cut off at its token limit: `incomplete` is true, the SMILES
 * is the cut-off text kept as the earlier Laravel version of decimer.ai kept it, and no name is looked up for it. RDKit's reading (`valid`, `validBy`,
 * `inchikey`, the drawing and the molfile) stays the fixture's: a mock cannot cut a structure off and read it again, so
 * this is a structure RDKit still reads (`valid` true, `validBy` rdkit), which is what a cut-off chain gives.
 */
export function asIncomplete(result: Result): Result {
  return { ...result, incomplete: true, confidence: 0.55, name: null, nameTier: null, nameVerified: null };
}

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
      validBy: 'rdkit' as const,
      isStructure: classifierUnavailable ? null : !NOT_A_STRUCTURE.has(c.number),
      confidence: CONFIDENCE[c.number] ?? 0.95,
      incomplete: false,
      name: c.iupac,
      nameTier: c.iupac ? (NAME_CHECK[c.number]?.tier ?? 'pin_verified') : null,
      nameVerified: c.iupac ? (NAME_CHECK[c.number]?.verified ?? 'opsin') : null,
    };
  });
}
