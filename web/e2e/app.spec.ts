import { expect, type Page } from '@playwright/test';
import { test } from './returning';
import {
  chooseLargeFile,
  editorPanel,
  fixtureImage,
  fixturePdf,
  headerDownload,
  horizontalOverflow,
  waitForEditor,
  pasteImage,
  pasteText,
  tile,
  watchProblems,
} from './helpers';

// Steps of the mock job: long enough that every stage can be seen, short enough for a test.
const FAST = './?mockStepMs=250';
const SLOW = './?mockStepMs=1000';

async function upload(page: Page, name = 'figure2.png') {
  await page.locator('input[type=file]').setInputFiles(fixtureImage(name));
}

async function waitForResults(page: Page) {
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 30_000 });
}

test('empty → upload → working → results, with no console errors or CSP violations', async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto(SLOW);

  // Empty: the wordmark, the tagline and one way in.
  await expect(page.getByRole('heading', { level: 1, name: 'DECIMER.ai' })).toBeVisible();
  await expect(page.getByText('Extract chemical structures from PDFs and images')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();

  await upload(page);

  // Working: queued first, then the stages; only the current and the next one are spelled out.
  await expect(page.getByRole('heading', { level: 1, name: 'Waiting for a free worker' })).toBeVisible();
  await expect(page.getByText(/You are number 2 in the queue|You are next in line/)).toBeVisible();
  const stage = page.getByRole('heading', { level: 1, name: 'Finding structures on each page' });
  await expect(stage).toBeVisible();
  await expect(page.getByText('Step 3 of 5. Next: Recognising each structure.')).toBeVisible();
  await expect(page.getByText(/^Elapsed \d+:\d\d$/)).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Progress' })).toHaveAttribute('aria-valuenow', '2');

  // Results arrive with the finished job; until then a poll only carries the count.
  await expect(page.getByRole('heading', { level: 1, name: 'Recognising each structure' })).toBeVisible();
  await expect(page.getByText('8 structures found')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toHaveCount(0);

  // Results: the 8 compounds of Figure 2, numbered as in the paper.
  await waitForResults(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Structures' })).toBeVisible();
  const numbers = await page.locator('.dc-tile__number').allTextContents();
  expect(numbers).toEqual(['1', '2', '4', '5', '6', '7', '11', '12']);
  await expect(page.getByText('8 of 20 structures · 1 image')).toBeVisible(); // an image has no page limit to count against
  const counts = page.getByRole('list', { name: 'Status of the structures' });
  await expect(counts).toContainText('1 to review');
  await expect(counts).toContainText('1 not sure');
  await expect(counts).toContainText('6 unchecked');

  expect(problems).toEqual([]);
});

test('names: the hover says what kind of name it is and how it was checked; no verified name says so', async ({ page }) => {
  await page.goto(FAST);
  await upload(page);
  await waitForResults(page);
  await expect(tile(page, 1)).toContainText('No verified name');
  await expect(tile(page, 4)).toContainText('(2R)-3,3-bis(1H-indol-3-yl)propane-1,2-diol');

  // The mock names each compound differently: tier and way of checking (mocks/fixtures.ts NAME_CHECK).
  const expected: [number, string, string][] = [
    [4, 'Preferred IUPAC name', 'OPSIN reads this name back to exactly this structure'],
    [5, 'Preferred IUPAC name', 'OPSIN reads this name back to this structure drawn without dative bonds'],
    [6, 'Systematic name (not the preferred IUPAC name)', 'OPSIN confirms the connectivity; stereo checked by CIP labelling'],
    [7, 'Name in preferred IUPAC form; its preferred status is not certified', 'OPSIN reads an equivalent spelling back to this structure'],
    [11, 'Systematic name (not the preferred IUPAC name)', 'Exact match in a curated list'],
    [12, 'Preferred IUPAC name', 'OPSIN reads each component acid; together they rebuild this structure'],
  ];
  for (const [n, kind, how] of expected) {
    await tile(page, n).hover();
    const tooltip = page.getByRole('tooltip');
    await expect(tooltip, `structure ${n}`).toContainText(kind);
    await expect(tooltip, `structure ${n}`).toContainText(how);
    // The kind first, then how it was checked.
    const lines = await tooltip.locator('.dc-hover__line').allTextContents();
    expect(lines, `structure ${n}`).toEqual([kind, how]);
  }

  await tile(page, 1).hover();
  await expect(page.getByRole('tooltip')).toHaveText('No verified name');
  expect(await page.getByRole('tooltip').locator('.dc-hover__line').count()).toBe(1);

  // The same two lines sit under the name in the panel.
  await tile(page, 6).click();
  const panel = editorPanel(page);
  await expect(panel.locator('.dc-fact__tier')).toHaveText([
    'Systematic name (not the preferred IUPAC name)',
    'OPSIN confirms the connectivity; stereo checked by CIP labelling',
  ]);
});

test('pasting an image starts the job at once; pasting text does nothing', async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto(SLOW);

  expect(await pasteText(page, 'CCO')).toBe(false); // left to the browser
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();

  expect(await pasteImage(page)).toBe(true);
  await expect(page.getByRole('heading', { level: 1 })).toContainText(/Waiting for a free worker|Uploading your files/);
  await expect(page.getByText('pasted-image.png')).toBeVisible();
  await waitForResults(page);

  // On the results screen a paste must not replace the work.
  expect(await pasteImage(page)).toBe(false);
  await expect(tile(page, 1)).toBeVisible();
  expect(problems).toEqual([]);
});

test('upload errors: format mix, unsupported file, and the 429/503 messages', async ({ page }) => {
  await page.goto(FAST);
  const input = page.locator('input[type=file]');

  await input.setInputFiles([fixtureImage('a.png'), { name: 'paper.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4') }]);
  await expect(page.getByRole('alert')).toContainText('A PDF and images cannot be uploaded together');

  await input.setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('x') });
  await expect(page.getByRole('alert')).toContainText('notes.txt is not a supported file');

  await chooseLargeFile(page, 'big.png', 101 * 1024 * 1024);
  await expect(page.getByRole('alert')).toContainText('The limit is 100 MB');

  await input.setInputFiles(fixtureImage('busy.png'));
  await expect(page.getByRole('alert')).toContainText('Too many jobs are running from your network');

  await input.setInputFiles(fixtureImage('full.png'));
  await expect(page.getByRole('alert')).toContainText('The queue is full');
});

test('upload works from the keyboard: Tab to the button, Enter opens the file picker', async ({ page }) => {
  await page.goto(FAST);
  // The skip link and the header links come first; the button is reachable by Tab.
  const choose = page.getByRole('button', { name: 'Choose files' });
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab');
    if (await choose.evaluate((el) => el === document.activeElement)) break;
  }
  await expect(choose).toBeFocused();
  const chooser = page.waitForEvent('filechooser');
  await page.keyboard.press('Enter');
  expect(await chooser).toBeTruthy();
});

test('the working view says what to expect for this upload: an image is quick, a 10-page PDF is not', async ({ page }) => {
  await page.goto(SLOW);
  await upload(page);
  await expect(page.getByText('A single image usually takes only a few seconds.')).toBeVisible();
  await expect(page.getByText(/PDF/)).toHaveCount(0);

  await page.goto(SLOW);
  await page.locator('input[type=file]').setInputFiles(fixturePdf());
  await expect(page.getByText('10 pages can take a minute or more.')).toBeVisible();
});

test('limits and edge states: truncation warning, no structures, a failed job', async ({ page }) => {
  await page.goto(FAST);
  await page.locator('input[type=file]').setInputFiles(fixturePdf('truncated.pdf'));
  await waitForResults(page);
  await expect(page.getByRole('note')).toContainText('3 more structures were found. Only the first 20 are shown.');
  await expect(page.getByRole('note')).toContainText('This document has 14 pages. Only the first 10 were processed.');
  // Each limit once: the API's own structures_truncated and pages_truncated sentences are not shown as well.
  await expect(page.getByRole('note').locator('p')).toHaveCount(2);
  await expect(page.getByRole('note')).not.toContainText('23 structures were found');
  await expect(page.locator('.dc-tile')).toHaveCount(20);

  await page.goto(FAST);
  await upload(page, 'empty.png');
  await expect(page.getByRole('heading', { level: 1, name: 'No structures were detected' })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Upload another file' })).toBeVisible();

  await page.goto(FAST);
  await upload(page, 'fail.png');
  await expect(page.getByRole('heading', { level: 1, name: 'The job did not finish' })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Start over' }).click();
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
});

test('filters and the status counts follow what the user does; Mark as checked updates both', async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto(FAST);
  await upload(page);
  await waitForResults(page);

  // Open structure 6, the one flagged for review. The panel shows why.
  await tile(page, 6).click();
  const panel = editorPanel(page);
  await expect(panel.getByRole('heading', { name: 'Editing 6' })).toBeVisible();
  await expect(panel).toContainText('The model was not confident about this structure (62%).');
  await expect(tile(page, 6)).toHaveAttribute('aria-current', 'true');

  await panel.getByRole('button', { name: 'Mark as checked' }).click();
  await expect(tile(page, 6)).toContainText('Checked');
  const counts = page.getByRole('list', { name: 'Status of the structures' });
  await expect(counts).toContainText('1 checked');
  await expect(counts).not.toContainText('to review');
  await expect(panel.getByRole('button', { name: 'Undo check' })).toBeVisible();

  const filters = page.getByRole('group', { name: 'Show structures' });
  await expect(filters.getByRole('button', { name: /Needs review/ })).toContainText('1'); // only 12 is left
  await filters.getByRole('button', { name: /Needs review/ }).click();
  await expect(page.locator('.dc-tile')).toHaveCount(1);
  await expect(tile(page, 12)).toBeVisible();
  await filters.getByRole('button', { name: /Edited/ }).click();
  await expect(page.getByText('You have not edited any structure yet.')).toBeVisible();
  await filters.getByRole('button', { name: /All/ }).click();
  await expect(page.locator('.dc-tile')).toHaveCount(8);

  expect(problems).toEqual([]);
});

test('PubChem and COCONUT links by InChIKey; the stereo toggle only where the key carries stereo', async ({ page }) => {
  await page.goto(FAST);
  await upload(page);
  await waitForResults(page);
  const panel = editorPanel(page);

  // Structure 1 (shentonin A) has stereocentres.
  await tile(page, 1).click();
  const pubchem = panel.getByRole('link', { name: /^PubChem/ });
  await expect(pubchem).toHaveAttribute('href', 'https://pubchem.ncbi.nlm.nih.gov/#query=GFYJLJXYTUQVEV-DKSSEZFCSA-N');
  await expect(panel.getByRole('link', { name: /^COCONUT/ })).toHaveAttribute(
    'href',
    'https://coconut.naturalproducts.net/search?q=GFYJLJXYTUQVEV-DKSSEZFCSA-N',
  );
  await panel.getByRole('switch', { name: 'Search PubChem with stereochemistry' }).uncheck();
  await expect(pubchem).toHaveAttribute('href', 'https://pubchem.ncbi.nlm.nih.gov/#query=GFYJLJXYTUQVEV');

  // Structure 2 has none: no toggle, the full key.
  await tile(page, 2).click();
  await expect(panel.getByRole('heading', { name: 'Editing 2' })).toBeVisible();
  await expect(panel.getByRole('switch')).toHaveCount(0);
  await expect(panel.getByRole('link', { name: /^PubChem/ })).toHaveAttribute(
    'href',
    'https://pubchem.ncbi.nlm.nih.gov/#query=DGPFSUGNVRYGDD-UHFFFAOYSA-N',
  );
});

test('report: the thank-you message replaces the button', async ({ page }) => {
  await page.goto(FAST);
  await upload(page);
  await waitForResults(page);
  const panel = editorPanel(page);
  await tile(page, 12).click();
  await expect(panel).toContainText('We are not sure this is a chemical structure.');
  await panel.getByRole('button', { name: 'Report a problem' }).click();
  await expect(panel.getByText('Thank you for your report')).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Report a problem' })).toHaveCount(0);
});

test('Download offers ZIP, SDF and CSV and delivers each', async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto(FAST);
  await upload(page);
  await waitForResults(page);

  const read = async (label: RegExp, menu = headerDownload(page)) => {
    await menu.click();
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('menuitem', { name: label }).click()]);
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    return { name: download.suggestedFilename(), text: Buffer.concat(chunks).toString('utf8') };
  };

  const zip = await read(/^ZIP/);
  expect(zip.name).toMatch(/\.zip/);
  const order = [...zip.text.matchAll(/== structure (\d+) \((\w+)\)/g)].map((m) => `${m[1]}:${m[2]}`);
  expect(order).toEqual(['1:original', '2:original', '4:original', '5:original', '6:original', '7:original', '11:original', '12:original']);

  const sdf = await read(/^SDF/);
  expect(sdf.name).toMatch(/\.sdf$/);
  expect(sdf.text.split('$$$$').length - 1).toBe(8);

  const csv = await read(/^CSV/);
  expect(csv.name).toMatch(/\.csv$/);
  expect(csv.text.trim().split('\n')).toHaveLength(9);
  await expect(page.getByRole('status').filter({ hasText: 'Your download has started.' })).toBeVisible();

  // The same menu at the end of the figure (ROADMAP §6 item 14: in the header and in the results area).
  const atTheEnd = page.locator('.dc-figure__foot').getByRole('button', { name: /^Download/ });
  await expect(atTheEnd).toBeVisible();
  const again = await read(/^SDF/, atTheEnd);
  expect(again.text.split('$$$$').length - 1).toBe(8);
  expect(problems).toEqual([]);
});

test('New upload goes straight back to the empty screen when nothing was edited', async ({ page }) => {
  await page.goto(FAST);
  await upload(page);
  await waitForResults(page);
  await page.getByRole('button', { name: 'New upload' }).click();
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
});

test('About, Privacy and Impressum are pages of the same site, and the footer links to them', async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto(FAST);
  const footer = page.getByRole('contentinfo');
  await expect(footer.getByText('Built with')).toBeVisible();
  await expect(footer.getByText('by Kohulan')).toBeVisible();
  // Legacy's line, with the current year.
  await expect(footer.getByText(`© ${new Date().getFullYear()} DECIMER Project. All rights reserved.`)).toBeVisible();
  await expect(footer.getByRole('link', { name: 'Steinbeck group' })).toBeVisible();
  await expect(footer.getByRole('navigation', { name: 'Footer' }).getByRole('link', { name: 'GitHub' })).toHaveAttribute('href', 'https://github.com/Steinbeck-Lab/DECIMER.ai');

  // The footer has no About link: it is in the header (on the empty screen, the copper ribbon).
  await expect(footer.getByRole('link', { name: 'About' })).toHaveCount(0);
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'About' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'About DECIMER.ai' })).toBeFocused();
  await expect(page.getByRole('heading', { level: 2, name: 'Cite us' })).toBeVisible();
  const references = page.locator('.dc-cite');
  await expect(references).toHaveCount(6);
  await expect(references.nth(5)).toContainText('Orthonym'); // in the place of the STOUT paper
  await expect(page.getByRole('link', { name: 'Orthonym' }).first()).toHaveAttribute('href', 'https://github.com/Steinbeck-Lab/Orthonym');

  await footer.getByRole('link', { name: 'Privacy' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Privacy' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 3, name: 'Problem reports' })).toBeVisible();

  await footer.getByRole('link', { name: 'Impressum' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Impressum' })).toHaveAttribute('lang', 'de');
  await expect(page.getByRole('heading', { level: 2, name: 'Haftungsausschluss' })).toBeVisible();

  // No page keeps its placeholder, and none speaks of the removed tools. (A `[CONFIRM: ...]` marker is a decision
  // still open, listed in the hand-off; it is not a placeholder.)
  for (const route of ['about', 'privacy', 'impressum']) {
    await page.goto(`./#/${route}`);
    await page.locator('main h1').waitFor();
    const text = (await page.locator('main').textContent()) ?? '';
    expect(text, route).not.toMatch(/TODO|Draft text/);
    expect(text, route).not.toMatch(/STOUT/i);
    expect(text, route).not.toMatch(/Google Analytics/i);
  }
  expect(problems).toEqual([]);
});

test('a phone (390 px) never scrolls sideways, in any state or page', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('./?mockStepMs=900');
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

  await upload(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Waiting for a free worker' })).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

  await waitForResults(page);
  await page.waitForTimeout(1800); // the arrival animation has settled
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

  // The editor is a full-screen sheet.
  await tile(page, 6).click();
  const panel = editorPanel(page);
  await expect(panel.getByRole('heading', { name: 'Editing 6' })).toBeVisible();
  const box = await panel.boundingBox();
  expect(box?.width).toBeGreaterThanOrEqual(389);
  expect(box?.height).toBeGreaterThanOrEqual(843);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  await panel.getByRole('button', { name: 'Close' }).click();
  await expect(panel).toBeHidden();

  for (const hash of ['#/about', '#/privacy', '#/impressum']) {
    await page.goto(`./${hash}`);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  }
});

test('the editor panel traps focus while open and hands it back to the tile on close', async ({ page }) => {
  await page.goto(FAST);
  await upload(page);
  await waitForResults(page);

  await tile(page, 5).focus();
  await page.keyboard.press('Enter');
  const panel = editorPanel(page);
  await expect(panel.getByRole('heading', { name: 'Editing 5' })).toBeFocused();

  // Ketcher mounts and starts while the panel is open, which adds some 70 controls to the tab order and moves
  // focus once. Cycling is tested on the finished panel, past more than one full lap.
  await waitForEditor(page, 800);
  await expect(panel.getByRole('heading', { name: 'Editing 5' })).toBeFocused();

  // Tab cycles inside the panel and never reaches the page behind it, and no press ends on the body (P4-14: Ketcher's
  // toolbar scroll arrows, which used to take focus and vanish, are out of the tab order; toolbar-focus.spec.ts).
  const state = () =>
    panel.evaluate((el) => ({
      inside: el.contains(document.activeElement),
      body: document.activeElement === document.body,
      tag: document.activeElement?.tagName ?? '',
    }));
  for (let i = 0; i < 160; i++) {
    await page.keyboard.press('Tab');
    const now = await state();
    expect(now.body, `Tab ${i + 1}: focus is on the body`).toBe(false);
    expect(now.inside, `Tab ${i + 1} ended on <${now.tag}> outside the panel`).toBe(true);
  }

  // Escape (from the panel's own controls) closes it and focus returns to the tile.
  await panel.getByRole('button', { name: 'Next structure' }).focus();
  await page.keyboard.press('Escape');
  await expect(tile(page, 5)).toBeFocused();
});
