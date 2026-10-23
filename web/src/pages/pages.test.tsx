import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';
import { Footer } from '../shell/Footer';
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
    expect(inCopy).toBeGreaterThan(0);
    expect(shown).toBe(inCopy);
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
    const items = [...document.querySelectorAll('.dc-page__citations li')];
    expect(items).toHaveLength(legacy.length + 1); // plus the Orthonym preprint
    legacy.forEach(([doi, title, authors, journal, volume, rest], i) => {
      const item = items[i]!;
      const link = item.querySelector('a')!;
      expect(link.getAttribute('href')).toBe(doi);
      expect(link.textContent).toBe(`${title} ${strings.editor.external}`);
      const text = item.querySelector('span:not(.dc-sr)')!;
      expect(text.querySelector('em')?.textContent).toBe(journal);
      expect(text.querySelector('strong')?.textContent).toBe(volume);
      expect(text.textContent).toBe(`${authors} ${journal}, ${volume}${rest}`);
    });
  });

  test('cites the Orthonym preprint in place of STOUT, and links its repository as a component', () => {
    render(<About />);
    const last = [...document.querySelectorAll('.dc-page__citations li')].at(-1)!;
    // As Crossref records it (10.26434/chemrxiv.15009769/v1, posted 2026-10-02).
    expect(last.querySelector('a')?.textContent).toBe(`Orthonym: verified IUPAC names for chemical structures in the wild ${strings.editor.external}`);
    expect(last.querySelector('a')?.getAttribute('href')).toBe('https://doi.org/10.26434/chemrxiv.15009769/v1');
    expect(last.textContent).toContain('Rajan, K., Zielesny, A., Steinbeck, C.');
    expect(last.textContent).toContain('ChemRxiv (2026), preprint.');
    expect(last.textContent).not.toMatch(/1\.0\.8|in preparation|zenodo/i);
    expect(document.querySelector('a[href="https://github.com/Steinbeck-Lab/Orthonym"]')).toBeTruthy();
    expect(document.querySelector('a[href*="s13321-021-00512-4"]')).toBeNull(); // the STOUT paper
    expect(document.querySelectorAll('.dc-page__citations li')).toHaveLength(6); // OPSIN has no entry of its own
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

  test('keeps no draft notice of its own besides the one for the data-protection review', () => {
    render(<Privacy />);
    expect(document.querySelector('.dc-callout')?.textContent).toMatch(/data-protection officer/);
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
    expect(document.querySelector('footer a[href="#/about"]')?.getAttribute('lang')).toBeNull();
  });
});
