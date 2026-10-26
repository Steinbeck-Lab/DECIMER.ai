import { expect, type Page } from '@playwright/test';
import { test } from './returning';
import { HTTP_API_PORT } from '../playwright.config';
import { fakeApi, JOB_ID, wireJob, wireResult } from './fakeApi';
import { atomCount, editorPanel, fixtureImage, layoutOnCanvas, tile, waitForCanvas, waitForEditor, watchProblems } from './helpers';

// Structures that RDKit refused and only CDK read (decision D4): the panel shows DECIMER's SMILES with a note, and the
// editor loads CDK's molblock, hypervalent atoms included, without the load-failure overlay.

const CDK_NOTE = 'Accepted by CDK only (RDKit could not read it)';

/** Select all, Layout. The panel is taller than the window: having used its buttons below the canvas, scroll the canvas back into view first. */
async function layoutVisibleCanvas(page: Page) {
  await page.locator('.dc-editor__canvas[data-active=true]').evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await layoutOnCanvas(page);
}

test('a structure only CDK accepted shows DECIMER\'s SMILES with the CDK note and a working copy button; an edit hands the verdict to RDKit', async ({ page, context }) => {
  test.setTimeout(240_000);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const problems = watchProblems(page);
  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixtureImage('cdkonly.png'));
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 30_000 });
  // The third structure of the scenario is number 4.
  await expect(tile(page, 4)).toContainText('Accepted by CDK only');
  await tile(page, 4).click();
  const panel = editorPanel(page);
  await expect(panel.getByRole('heading', { name: 'Editing 4' })).toBeVisible();

  const smiles = panel.locator('code.dc-code__text');
  await expect(panel.getByText(CDK_NOTE)).toBeVisible();
  await expect(smiles).not.toHaveText('No valid SMILES');
  const shown = (await smiles.textContent()) ?? '';
  expect(shown.length).toBeGreaterThan(3);
  await panel.getByRole('button', { name: 'Copy SMILES' }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(shown);

  // An edit is judged by RDKit alone (the mock's validate says valid): the note and the CDK flag are gone.
  await waitForEditor(page);
  await waitForCanvas(page);
  await layoutVisibleCanvas(page);
  await expect(tile(page, 4)).toContainText('Edited', { timeout: 15_000 });
  await expect(panel.getByText(CDK_NOTE)).toHaveCount(0, { timeout: 15_000 });
  await expect(tile(page, 4)).not.toContainText('Accepted by CDK only');
  await expect(smiles).not.toHaveText('No valid SMILES');
  expect(problems).toEqual([]);
});

// The molblocks CDK 2.8 writes for the SMILES that RDKit's valence rules refuse (services/chem/tests/test_cdk_integration.py),
// laid out by hand: an atom with more neighbours or bonds than its valence allows.
type Atom = [element: string, x: number, y: number];
type Bond = [from: number, to: number, order: 1 | 2];
function cdkMolblock(atoms: Atom[], bonds: Bond[]): string {
  const num = (n: number, width: number) => String(n).padStart(width);
  const coord = (v: number) => v.toFixed(4).padStart(10);
  return [
    '',
    '  CDK     1023262100 2D',
    '',
    `${num(atoms.length, 3)}${num(bonds.length, 3)}  0  0  0  0  0  0  0  0999 V2000`,
    ...atoms.map(([element, x, y]) => `${coord(x)}${coord(y)}${coord(0)} ${element.padEnd(3)} 0  0  0  0  0  0  0  0  0  0  0  0`),
    ...bonds.map(([from, to, order]) => `${num(from, 3)}${num(to, 3)}${num(order, 3)}  0  0  0  0`),
    'M  END',
    '',
  ].join('\n');
}

const HYPERVALENT = [
  {
    smiles: 'CS(=O)(=O)(=O)(=O)C', // sulfur with six bonds and four oxygens
    molfile: cdkMolblock(
      [['C', -1.8, 0], ['S', 0, 0], ['C', 1.8, 0], ['O', 0.9, 1.4], ['O', -0.9, 1.4], ['O', 0.9, -1.4], ['O', -0.9, -1.4]],
      [[1, 2, 1], [2, 3, 1], [2, 4, 2], [2, 5, 2], [2, 6, 2], [2, 7, 2]],
    ),
    atoms: 7,
    shown: 'CS(C)(=O)(=O)=O',
  },
  {
    smiles: 'C[N](C)(C)(C)C', // nitrogen with five neighbours
    molfile: cdkMolblock(
      [['N', 0, 0], ['C', 1.6, 0], ['C', -1.6, 0], ['C', 0, 1.6], ['C', 0, -1.6], ['C', 1.1, 1.1]],
      [[1, 2, 1], [1, 3, 1], [1, 4, 1], [1, 5, 1], [1, 6, 1]],
    ),
    atoms: 6,
    shown: 'C[N](C)(C)(C)C',
  },
  {
    smiles: 'CC(C)(C)(C)c1ccccc1', // carbon with five neighbours, next to a kekulised ring
    molfile: cdkMolblock(
      [
        ['C', 0, 0], ['C', 1.5, 0], ['C', -1.5, 0], ['C', 0, 1.5], ['C', 0, -1.5],
        ['C', 1.1, 1.1], ['C', 2.4, 1.85], ['C', 3.7, 1.1], ['C', 3.7, -0.4], ['C', 2.4, -1.15], ['C', 1.1, -0.4],
      ],
      [[1, 2, 1], [1, 3, 1], [1, 4, 1], [1, 5, 1], [1, 6, 1], [6, 7, 2], [7, 8, 1], [8, 9, 2], [9, 10, 1], [10, 11, 2], [11, 6, 1]],
    ),
    atoms: 11,
    shown: 'CC(C)(C)(C)c1ccccc1',
  },
];

test.describe('the editor loads the molblocks CDK writes, with their hypervalent atoms', () => {
  test.use({ baseURL: `http://127.0.0.1:${HTTP_API_PORT}/` });

  test('each opens without the load-failure overlay, is editable, and an edit is validated by the server', async ({ page }) => {
    test.setTimeout(240_000);
    const problems = watchProblems(page);
    const validated: { path: string; molfile: string }[] = [];
    const job = wireJob('done', HYPERVALENT.length);
    job.results = HYPERVALENT.map((h, index) => ({
      ...wireResult(index),
      smiles: h.shown,
      molfile: h.molfile,
      valid: false,
      validBy: 'cdk',
      inchikey: '',
      name: null,
      nameTier: null,
      nameVerified: null,
    })) as typeof job.results;

    await fakeApi(page, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === 'POST' && path === '/api/jobs') {
        await route.fulfill({ status: 202, contentType: 'application/json', headers: { Location: `/api/jobs/${JOB_ID}` }, body: JSON.stringify(job) });
        return true;
      }
      if (request.method() === 'GET' && path === `/api/jobs/${JOB_ID}`) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(job) });
        return true;
      }
      if (request.method() === 'POST' && /\/results\/\d+\/validate$/.test(path)) {
        const { molfile } = request.postDataJSON() as { molfile: string };
        validated.push({ path, molfile });
        // RDKit's verdict on what Ketcher wrote: a SMILES it can read.
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ smiles: 'CS(C)(=O)=O', inchikey: 'HHVIBTZHLRERCL-UHFFFAOYSA-N', svg: '', valid: true, name: null }),
        });
        return true;
      }
      return false;
    });

    await page.goto('./');
    await page.locator('input[type=file]').setInputFiles(fixtureImage());
    await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 30_000 });
    const panel = editorPanel(page);

    for (const [i, h] of HYPERVALENT.entries()) {
      const n = i + 1;
      await tile(page, n).click();
      await expect(panel.getByRole('heading', { name: `Editing ${n}` })).toBeVisible();
      if (i === 0) await waitForEditor(page);
      await waitForCanvas(page);
      // Ketcher took the molblock: no overlay, no "not loaded" heading, the note and DECIMER's SMILES are in the panel.
      await expect(page.locator('.dc-editor__load-error')).toHaveCount(0);
      await expect(panel.getByRole('heading', { name: `Editing ${n}` })).toBeVisible();
      await expect(panel.getByText(CDK_NOTE)).toBeVisible();
      await expect(panel.locator('code.dc-code__text')).toHaveText(h.shown);

      // It is on the canvas, whole: an edit made there carries every atom, and the server is asked to validate it.
      await layoutOnCanvas(page);
      await expect(tile(page, n)).toContainText('Edited', { timeout: 15_000 });
      await expect.poll(() => validated.length, { timeout: 15_000 }).toBe(n);
      expect(atomCount(validated[i]?.molfile ?? '')).toBe(h.atoms);
      expect(validated[i]?.path).toBe(`/api/jobs/${JOB_ID}/results/${i}/validate`);
      await expect(panel.getByText(CDK_NOTE)).toHaveCount(0, { timeout: 15_000 });
      await expect(panel.locator('code.dc-code__text')).toHaveText('CS(C)(=O)=O');
    }
    expect(problems).toEqual([]);
  });
});
