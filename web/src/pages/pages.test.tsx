import { cleanup, render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, describe, expect, test } from 'vitest';
import { Footer } from '../shell/Footer';
import { LIMITS } from '../api/contract';
import { strings } from '../strings';
import { About } from './About';
import { Impressum } from './Impressum';
import { Privacy } from './Privacy';
import { MATOMO_URL } from '../analytics/matomo';
import { allStrings } from '../test/copy';
import { CONFIRM_MARKER } from './Rich';

afterEach(cleanup);

const PAGES = [
  { name: 'About', page: <About />, tree: strings.pages.about },
  { name: 'Privacy', page: <Privacy />, tree: strings.pages.privacy },
  { name: 'Impressum', page: <Impressum />, tree: strings.pages.impressum },
] as const;

const headings = (root: ParentNode) =>
  [...root.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((h) => ({ level: Number(h.tagName[1]), text: h.textContent ?? '' }));

/** The `[CONFIRM: ...]` markers a page shows, in order. */
const markersOn = (page: ReactElement): string[] => {
  render(page);
  const found = (document.body.textContent ?? '').match(CONFIRM_MARKER) ?? [];
  cleanup();
  return found;
};

/** One section of the Privacy page, found by its heading: what it claims, and the open questions it still holds. */
function privacySection(title: string): { text: string; open: string[] } {
  render(<Privacy />);
  const heading = [...document.querySelectorAll('h3')].find((h) => h.textContent === title);
  expect(heading, title).toBeTruthy();
  const text = heading!.closest('section')!.textContent ?? '';
  return { text: text.replace(CONFIRM_MARKER, ''), open: text.match(CONFIRM_MARKER) ?? [] };
}

describe.each(PAGES)('$name', ({ page, tree }) => {
  test('has one h1 and never skips a heading level', () => {
    render(page);
    const levels = headings(document.body).map((h) => h.level);
    expect(levels.filter((l) => l === 1)).toHaveLength(1);
    expect(levels[0]).toBe(1);
    levels.forEach((level, i) => {
      if (i > 0) expect(level).toBeLessThanOrEqual(levels[i - 1]! + 1);
    });
  });

  test('shows its title, then every section of its copy as a heading, in order', () => {
    render(page);
    const shown = headings(document.body).map((h) => h.text);
    expect(shown[0]).toBe(tree.title);
    // A section of the copy is an object with a title and content or sub-sections (the other objects with a title are
    // citations); the walk meets them in the order the page shows them.
    const sectionTitles: string[] = [];
    const walk = (node: unknown) => {
      if (Array.isArray(node)) node.forEach(walk);
      else if (node && typeof node === 'object') {
        const n = node as Record<string, unknown>;
        if (n !== tree && typeof n.title === 'string' && ('content' in n || 'sections' in n)) sectionTitles.push(n.title);
        Object.values(n).forEach(walk);
      }
    };
    walk(tree);
    expect(sectionTitles.length).toBeGreaterThanOrEqual(2);
    expect(shown).toEqual(expect.arrayContaining(sectionTitles));
    if (tree !== strings.pages.about) expect(shown.slice(1)).toEqual(sectionTitles);
  });

  test('every external link opens in a new tab, says so to a screen reader and keeps the referrer to itself', () => {
    render(page);
    const links = [...document.querySelectorAll<HTMLAnchorElement>('a[href^="http"]')];
    expect(links.length).toBeGreaterThan(0);
    for (const a of links) {
      expect(a.target, a.href).toBe('_blank');
      expect(a.rel, a.href).toContain('noreferrer');
      expect(a.textContent, a.href).toContain(strings.editor.external);
    }
  });
});

describe('no placeholder, no STOUT, no Google Analytics', () => {
  const NEEDLE = ['TODO', 'P4-10'].join(' '); // built in two pieces so that this file does not contain it

  test('no copy of the site mentions STOUT or Google Analytics', () => {
    const all = allStrings(strings);
    expect(all.length).toBeGreaterThan(100);
    for (const text of all) {
      expect(text).not.toMatch(/STOUT/i);
      expect(text).not.toMatch(/Google Analytics/i);
    }
  });

  test('no page shows a draft note or a P4-10 placeholder', () => {
    for (const { page } of PAGES) {
      render(page);
      const text = document.body.textContent ?? '';
      expect(text).not.toContain(NEEDLE);
      expect(text).not.toMatch(/Draft text/);
      expect(text).not.toMatch(/STOUT|Google Analytics/i);
      cleanup();
    }
  });

  test('no source file of the pages or the strings carries the placeholder', () => {
    const sources = import.meta.glob(['./*.ts', './*.tsx', '!./*.test.*', '../strings.ts', '../shell/Footer.tsx'], {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>;
    expect(Object.keys(sources).length).toBeGreaterThan(5);
    for (const [file, source] of Object.entries(sources)) expect(source, file).not.toContain(NEEDLE);
  });
});

describe('[CONFIRM: ...] markers', () => {
  test('are well formed, and every one in the copy of a page is shown highlighted', () => {
    let inCopy = 0;
    let shown = 0;
    for (const { page, tree } of PAGES) {
      const texts = allStrings(tree);
      const opened = texts.join('\n').split('[CONFIRM').length - 1;
      const whole = texts.join('\n').match(CONFIRM_MARKER) ?? [];
      expect(whole.length, 'every [CONFIRM has its closing bracket and a colon').toBe(opened);
      for (const marker of whole) {
        expect(marker, 'a marker says what to confirm, in words').toMatch(/^\[CONFIRM: [A-Za-z].{30,}\]$/s);
      }
      inCopy += whole.length;
      render(page);
      shown += document.querySelectorAll('mark.dc-confirm').length;
      cleanup();
    }
    // None is left (owner, 2026-10-26); any new marker must still be well formed and shown highlighted.
    expect(shown).toBe(inCopy);
  });

  test('no question is left open: the owner confirmed all four statements on 2026-10-26 (draft notice, university proxy, GlitchTip, Impressum review)', () => {
    expect(markersOn(<About />)).toEqual([]);
    expect(markersOn(<Privacy />)).toEqual([]);
    expect(markersOn(<Impressum />)).toEqual([]);
    // The release gate (release.test.ts) looks for the same text in the copy: none is left.
    expect(allStrings(strings).filter((text) => text.includes('[CONFIRM'))).toHaveLength(0);
  });
});

describe('About', () => {
  test('cites the five DECIMER papers as the legacy site does, in its order and style', () => {
    // Written from the legacy view (resources/views/about.blade.php), not from the copy under test. The one change is
    // the comma after "Agea, I.A." that the legacy text lacks; the second title keeps its full stop, as there.
    const legacy = [
      ['https://doi.org/10.1038/s41467-023-40782-0', 'DECIMER.ai: an open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications', 'Rajan, K., Brinkhaus, H.O., Agea, I.A., Zielesny, A., Steinbeck, C.', 'Nat Commun', '14', ', 5045 (2023).'],
      ['https://doi.org/10.1186/s13321-024-00872-7', 'Advancements in hand-drawn chemical structure recognition through an enhanced DECIMER architecture.', 'Rajan, K., Brinkhaus, H.O., Zielesny, A., Steinbeck, C.', 'J Cheminform', '16', ', 78 (2024).'],
      ['https://doi.org/10.1186/s13321-020-00469-w', 'DECIMER: towards deep learning for chemical image recognition', 'Rajan, K., Zielesny, A., Steinbeck, C.', 'J Cheminform', '12', ', 65 (2020).'],
      ['https://doi.org/10.1186/s13321-021-00496-1', 'DECIMER-Segmentation: Automated extraction of chemical structure depictions from scientific literature', 'Rajan, K., Brinkhaus, H.O., Sorokina, M. et al.', 'J Cheminform', '13', ', 20 (2021).'],
      ['https://doi.org/10.1186/s13321-021-00538-8', 'DECIMER 1.0: deep learning for chemical image recognition using transformers', 'Rajan, K., Zielesny, A., Steinbeck, C.', 'J Cheminform', '13', ', 61 (2021).'],
    ] as const;
    render(<About />);
    const items = [...document.querySelectorAll('.dc-cite')];
    expect(items).toHaveLength(legacy.length + 1); // plus the Orthonym preprint
    legacy.forEach(([doi, title, authors, journal, volume, rest], i) => {
      const item = items[i]!;
      // A card: the title as its heading, the authors, the journal in italics with the volume in bold, and the DOI as
      // the link to the paper (the title was the link in the legacy list; the destination is the same).
      expect(item.querySelector('h3')?.textContent).toBe(title);
      const link = item.querySelector('a')!;
      expect(link.getAttribute('href')).toBe(doi);
      expect(link.textContent).toBe(`${doi.replace('https://doi.org/', '')} ${strings.editor.external}`);
      expect(item.querySelectorAll('a')).toHaveLength(1);
      expect(item.querySelector('.dc-cite__authors')?.textContent).toBe(authors);
      const where = item.querySelector('.dc-cite__where')!;
      expect(where.querySelector('em')?.textContent).toBe(journal);
      expect(where.querySelector('strong')?.textContent).toBe(volume);
      expect(where.textContent).toBe(`${journal}, ${volume}${rest}`);
    });
  });

  test('cites the Orthonym preprint in place of STOUT, and links its repository as a component', () => {
    render(<About />);
    const last = [...document.querySelectorAll('.dc-cite')].at(-1)!;
    // As Crossref records it (10.26434/chemrxiv.15009769/v1, posted 2026-10-02).
    expect(last.querySelector('h3')?.textContent).toBe('Orthonym: verified IUPAC names for chemical structures in the wild');
    expect(last.querySelector('a')?.textContent).toBe(`10.26434/chemrxiv.15009769/v1 ${strings.editor.external}`);
    expect(last.querySelector('a')?.getAttribute('href')).toBe('https://doi.org/10.26434/chemrxiv.15009769/v1');
    expect(last.textContent).toContain('Rajan, K., Zielesny, A., Steinbeck, C.');
    expect(last.textContent).toContain('ChemRxiv (2026), preprint.');
    expect(last.textContent).not.toMatch(/1\.0\.8|in preparation|zenodo/i);
    expect(document.querySelector('a[href="https://github.com/Steinbeck-Lab/Orthonym"]')).toBeTruthy();
    expect(document.querySelector('a[href*="s13321-021-00512-4"]')).toBeNull(); // the STOUT paper
    expect(document.querySelectorAll('.dc-cite')).toHaveLength(6); // OPSIN has no entry of its own
  });

  test('shows the limits as big figures from the limits the upload checks, and says them in words once', () => {
    render(<About />);
    const figures = [...document.querySelectorAll('.dc-about__figures > div')].map((d) => [d.querySelector('dt')?.textContent, d.querySelector('dd')?.textContent]);
    expect(figures).toEqual([
      ['PDF pages', String(LIMITS.maxPages)],
      ['structures', String(LIMITS.maxStructures)],
      ['MB', String(LIMITS.maxUploadBytes / 1024 / 1024)],
    ]);
    // The figures are for the eye: the sentence under them is what a screen reader reads.
    expect(document.querySelector('.dc-about__figures')?.getAttribute('aria-hidden')).toBe('true');
    const sentence = `Up to ${LIMITS.maxPages} PDF pages and ${LIMITS.maxStructures} structures per upload, and ${LIMITS.maxUploadBytes / 1024 / 1024} MB per upload.`;
    expect(document.querySelector('.dc-about__limits')?.textContent).toContain(sentence);
  });

  test('lays the five components on a measuring line from what goes in to what comes out, Orthonym and Ketcher apart', () => {
    render(<About />);
    expect(document.querySelector('.dc-pipe__io--in')?.textContent).toBe('PDF · image');
    expect(document.querySelector('.dc-pipe__io--out')?.textContent).toBe('ZIP · SDF · CSV');
    const stages = [...document.querySelectorAll('.dc-stage')];
    expect(stages.map((s) => s.querySelector('h3')?.textContent?.replace(strings.editor.external, '').trim())).toEqual([
      'DECIMER Segmentation',
      'DECIMER Image Classifier',
      'DECIMER Image Transformer',
      'Orthonym',
      'Ketcher',
    ]);
    expect(stages.map((s) => s.getAttribute('data-ext'))).toEqual(['false', 'false', 'false', 'true', 'true']);
    expect(stages[3]?.textContent).toContain('IUPAC names, each checked by an OPSIN round trip');
    expect(stages[3]?.textContent).toContain('MIT licence');
    // A stage is one link, to the project.
    expect(stages.map((s) => s.querySelectorAll('a').length)).toEqual([1, 1, 1, 1, 1]);
  });

  test('each DOI link is described by the title of its card, and the line-end labels are for the eye only', () => {
    render(<About />);
    for (const card of document.querySelectorAll('.dc-cite')) {
      const title = card.querySelector('h3')!;
      expect(title.id).not.toBe('');
      expect(card.querySelector('a')?.getAttribute('aria-describedby')).toBe(title.id);
    }
    const ids = [...document.querySelectorAll('.dc-cite h3')].map((h) => h.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const label of document.querySelectorAll('.dc-pipe__io')) expect(label.getAttribute('aria-hidden')).toBe('true');
  });

  test('the link to the research is the porcelain pill, an external link like the others', () => {
    render(<About />);
    const pill = document.querySelector<HTMLAnchorElement>('a.dc-pill')!;
    expect(pill.getAttribute('href')).toBe('https://cheminf.uni-jena.de/research/deep-learning/');
    expect(pill.target).toBe('_blank');
    expect(pill.rel).toContain('noreferrer');
    expect(pill.textContent).toBe(`Learn more about our research ${strings.editor.external}`);
    expect(pill.querySelector('.dc-pill__icon')?.getAttribute('aria-hidden')).toBe('true');
  });

  test('keeps its heading order: the title, a heading for each part, and the components and citations under theirs', () => {
    render(<About />);
    const shown = headings(document.body);
    expect(shown.filter((h) => h.level === 2).map((h) => h.text)).toEqual([
      'How to use the DECIMER web app',
      'Limits',
      'About DECIMER',
      'Project components',
      'Cite us',
    ]);
    expect(shown.filter((h) => h.level === 3)).toHaveLength(5 + 6);
  });

  test('says a name is worked out again after an edit, with nothing left to confirm', () => {
    render(<About />);
    expect(document.body.textContent).toContain('A name is worked out again after you edit a structure.');
    expect(document.querySelector('mark.dc-confirm')).toBeNull();
  });

  test('carries over the legacy sections and links, and points to the privacy and Impressum pages', () => {
    render(<About />);
    const shown = headings(document.body).map((h) => h.text);
    for (const title of ['How to use the DECIMER web app', 'Project components', 'Cite us', 'About DECIMER']) {
      expect(shown).toContain(title);
    }
    for (const href of [
      'https://lifescience.opensource.epam.com/ketcher/',
      'https://github.com/Kohulan/DECIMER-Image-Segmentation',
      'https://github.com/Steinbeck-Lab/DECIMER-Image-Classifier',
      'https://github.com/Kohulan/DECIMER-Image_Transformer',
      'https://cheminf.uni-jena.de/research/deep-learning/',
      'https://www.uni-jena.de/',
    ]) {
      expect(document.querySelector(`a[href="${href}"]`), href).toBeTruthy();
    }
    expect(document.querySelector('a[href="#/privacy"]')).toBeTruthy();
    expect(document.querySelector('a[href="#/impressum"]')).toBeTruthy();
  });
});

describe('Privacy', () => {
  test('keeps the legacy parts (controller, rights, server data) and describes this stack', () => {
    render(<Privacy />);
    const shown = headings(document.body).map((h) => h.text);
    for (const title of [
      'I. Information about us as controllers of your data',
      'II. The rights of users and data subjects',
      'III. Information about the data processing',
      'Your uploads and results',
      'Problem reports',
      'Fair use and your network address',
      'Server logs',
      'Cookies and your browser',
      'Visit statistics (Matomo)',
      'Error reports and usage counts',
    ]) {
      expect(shown).toContain(title);
    }
    const text = document.body.textContent ?? '';
    expect(text).toContain('Lessingstraße 8');
    expect(text).toContain('+49 3641 948181');
    expect(text).toContain('Art. 21 GDPR');
    expect(text).toContain('Prof. Dr. Christoph Steinbeck');
    expect(text).toContain('about one hour after your job ends');
    expect(text).toContain('about six hours at most');
    expect(text).toContain('Redis');
    expect(text).toContain('HMAC');
    expect(text).toContain('the application does not store the address');
    expect(text).toContain('rate limits');
    expect(text).not.toContain('DSGVO');
    expect(document.querySelector('a[href="mailto:christoph.steinbeck@uni-jena.de"]')).toBeTruthy();
  });

  test('says what the legacy Matomo code does: it sets _pk_ cookies and may track the links you click', () => {
    render(<Privacy />);
    const text = (document.body.textContent ?? '').replace(CONFIRM_MARKER, ''); // what the page claims, not its open questions
    expect(text).toContain(new URL(MATOMO_URL).host); // the server the code in analytics/matomo.ts talks to
    expect(text).toContain('does not switch Matomo\'s cookies off');
    expect(text).toContain('"_pk_"');
    expect(text).toContain('may also record the address of a link to another website that you click');
    expect(text).not.toMatch(/cookieless|in a way that sets no cookies|This site sets no cookies/i);
    expect(text).toContain('This site itself sets no cookies');
    expect(text).toContain('decimer.visited');
  });

  test('cookies and your browser: the animated logo\'s note is in session storage, gone with the tab', () => {
    const { text, open } = privacySection('Cookies and your browser');
    expect(open).toEqual([]);
    expect(text).toContain('session storage');
    expect(text).toContain('"decimer.visited"');
    expect(text).toContain('only the first time you open the site in a browser tab');
    expect(text).toContain('it is gone when you close the tab');
    // The note is no longer in local storage: only the editor's own settings are said to be there.
    expect(text).not.toContain('only on your first visit');
    expect(text).toContain('own settings in the local storage of your browser');
  });

  test('uploads and edits: the server does not keep an edit, and the legal basis is given', () => {
    const { text, open } = privacySection('Your uploads and results');
    expect(open).toEqual([]);
    // The two statements about edits are the server's behaviour (api/src/decimer_api/result_routes.py: validate
    // answers and stores nothing, export streams a file and stores nothing).
    expect(text).toContain(
      'When you edit a structure, the edited structure is sent to the server, which works out its SMILES, InChIKey, picture and name and sends them back; the server does not keep the edit.',
    );
    expect(text).toContain('A download sends your edits once more to build the file, which the server does not keep either.');
    expect(text).toContain('The basis for processing your uploads, your edits and your results is Art. 6 Para. 1 lit. f) GDPR.');
    expect(text).toContain('Our legitimate interest lies in providing the free service.');
    expect(text).not.toMatch(/not built yet|so that it can work out/);
    // The job folder holds more than the upload and the crops: the rendered pages too (page-<n>.png, worker/pipeline.py).
    expect(text).toContain('in a job folder on disk, the uploaded file, the page images made from it and the structure images cut out of them');
  });

  test('problem reports: what one keeps, who can open it, when it goes, and that no backup holds it', () => {
    const { text, open } = privacySection('Problem reports');
    expect(open).toEqual([]);
    // What api/src/decimer_api/reports.py `_record` writes, and no more.
    expect(text).toContain('the picture of that structure and the result as it was recognised (for example SMILES, molfile, name and confidence)');
    expect(text).toContain('the number of pages and the warnings of the job');
    expect(text).toContain('the random identifier of the job');
    expect(text).toContain('the versions of the models and the time');
    expect(text).toContain('It keeps no IP address and none of your edits.');
    expect(text).toContain('in a folder that only its administrators can open');
    expect(text).toContain('A report is deleted once we have analysed it, and at the latest after 12 months');
    expect(text).toContain('Reports are not included in any backup.');
    expect(text).not.toMatch(/not built yet|private storage area/);
  });

  test('server logs: the access log has no address, the error log has one and is gone after seven days', () => {
    const { text, open } = privacySection('Server logs');
    // deploy/nginx/edge.conf: log_format edge is the time, the method, the path with job ids replaced, the status,
    // the time taken and the size.
    expect(text).toContain('It records no IP address, no user agent (browser type or operating system), no referring page and no query string.');
    // deploy/nginx/edge-logs.sh deletes a day of the error log a week after it began (api/tests/test_edge_logs.py).
    // ... and what an nginx error line holds: the client address, the request line, the host and the referrer, for any
    // server error (a failed proxy attempt, an upload over the limit, a media file that cannot be opened).
    expect(text).toContain('If the server hits an error, for example when it cannot pass a request on, when an upload is too large or when a file cannot be opened');
    expect(text).toContain('its error log can name the IP address of the request, the request itself (the page address, which for a job includes the job identifier), the name of the site that was asked for and the page that referred to it');
    expect(text).toContain('The error log is the only place where this site writes an IP address down.');
    expect(text).toContain('It is split into one file per day, and no line in it is kept longer than seven days.');
    expect(text).not.toContain('as long as the server\'s log rotation keeps them');
    expect(text).not.toContain('names the IP address of that request and the page address'); // too narrow: any error, and the host and referrer
    expect(open).toEqual([]);
  });

  test('Matomo: the legal basis and how to opt out are given, and nothing is claimed about what NFDI4Chem keeps', () => {
    const { text, open } = privacySection('Visit statistics (Matomo)');
    expect(open).toEqual([]);
    const host = new URL(MATOMO_URL).host;
    expect(text).toContain('The basis for counting visits is Art. 6 Para. 1 lit. f) GDPR. Our legitimate interest lies in seeing how the site is used.');
    expect(text).toContain(`block cookies or block scripts from ${host}, or use a tracker blocker`);
    expect(text).toContain('The site works without Matomo.');
    // The legacy text said nothing about retention or IP shortening, and nobody has confirmed either for NFDI4Chem's server.
    expect(text).not.toMatch(/shorten|anonymi[sz]|truncat|retention|(deleted|kept|stored) (for|after)/i);
  });

  test('error reports and usage counts: GlitchTip, 90 days, no files or addresses', () => {
    const { text, open } = privacySection('Error reports and usage counts');
    expect(text).toContain('an error report goes to an error tracker (GlitchTip) that runs on our own server in Jena');
    expect(text).toContain('never your files, structures or IP addresses, and is kept for 90 days');
    expect(text).toContain('The server counts how many jobs run per day, without IP addresses.');
    expect(open).toEqual([]);
  });

  test('shows no draft notice (removed when the owner confirmed the text, 2026-10-26)', () => {
    render(<Privacy />);
    expect(document.querySelector('.dc-callout')).toBeNull();
  });
});

describe('Impressum', () => {
  test('is the legacy German text, marked as German, with the provider and contact data', () => {
    render(<Impressum />);
    const title = document.querySelector('h1')!;
    expect(title.textContent).toBe('Impressum');
    expect(title.getAttribute('lang')).toBe('de');
    const german = [...document.querySelectorAll('main section[lang="de"]')];
    expect(german.map((s) => s.querySelector('h2')?.textContent)).toEqual([
      'Angaben gemäß § 5 DDG',
      'Repräsentiert von',
      'Kontaktdaten',
      'Haftungsausschluss',
    ]);
    // The English lead-in is not inside German markup.
    const lead = [...document.querySelectorAll('.dc-page__body > p')].find((p) => p.textContent?.startsWith('This legal disclosure'));
    expect(lead?.closest('[lang]')?.getAttribute('lang')).not.toBe('de');
    const text = document.body.textContent ?? '';
    for (const fact of [
      'Lessingstr. 8',
      '07743 Jena',
      'Institut für Anorganische und Analytische Chemie',
      'Ansprechpartner: Prof. Dr. Christoph Steinbeck',
      'Telefon: +49 3641 948181',
      'Verantwortlichkeit für Inhalte',
      'Verantwortlichkeit für Links',
      'Paragraph 106 strafbar',
      // The typos of the legacy text, corrected one by one.
      'dass wir nicht verantwortlich sind',
      'die versandt werden',
      'verfolgen wir mögliche illegale Aktivitäten in diesen',
      'festgestellt, folgen wir unserer Verpflichtung, die entsprechenden',
      'die Wiedergabe, Kopie',
      'Dieses Impressum wurde mithilfe von impressumgeneratorenglisch.de',
    ]) {
      expect(text, fact).toContain(fact);
    }
    for (const typo of ['Abschnitt 5 TMG', 'das wir', 'versand werden', 'in mögliche', 'Widergabe', 'Diese Impressum']) {
      expect(text, typo).not.toContain(typo);
    }
    expect(document.querySelector('a[href="mailto:christoph.steinbeck@uni-jena.de"]')).toBeTruthy();
    expect(document.querySelector('a[href="https://cheminf.uni-jena.de/"]')).toBeTruthy();
  });

  test('the footer link to it is marked as German', () => {
    render(<Footer />);
    expect(document.querySelector('footer a[href="#/impressum"]')?.getAttribute('lang')).toBe('de');
  });
});
