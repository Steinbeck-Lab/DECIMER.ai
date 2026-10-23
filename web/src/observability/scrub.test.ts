import type { Event } from '@sentry/browser';
import { describe, expect, test } from 'vitest';
import {
  FILE,
  IP,
  JOB_ID,
  MAX_TEXT_CHARS,
  MOLFILE_REMOVED,
  SMILES,
  TRUNCATED,
  scrubEvent,
  scrubText,
} from './scrub';

const JOB = '0123456789abcdef0123456789abcdef';
const ASPIRIN = 'CC(=O)Oc1ccccc1C(=O)O';

/** A V2000 molfile as Ketcher writes it (ethanol). */
const MOLFILE = [
  'ethanol',
  '  Ketcher 10232612342D 1   1.00000     0.00000     0',
  '',
  '  3  2  0  0  0  0  0  0  0  0999 V2000',
  '    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0',
  '    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0',
  '    2.5981    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0',
  '  1  2  1  0     0  0',
  '  2  3  1  0     0  0',
  'M  END',
].join('\n');

/** Real structures, as the app shows them: every one must go, whatever is around it. */
const SMILES_STRINGS = [
  'CC(=O)Oc1ccccc1C(=O)O', // aspirin
  'CN1C=NC2=C1C(=O)N(C(=O)N2C)C', // caffeine
  'C1CC1',
  '[Na+].[Cl-]',
  'C[C@H](N)C(=O)O',
  'c1ccccc1',
  'CCCCO',
  'OC(=O)CCN',
  'C=C',
  'C#N',
  'F/C=C/F',
  '[13CH3][NH3+]',
  'ClC1=CC=CC=C1Br',
  'CCO',
  '[13CH3]C(=O)[O-]',
  'N#Cc1ccc(cc1)C(F)(F)F',
  'O=C(O)C(Cl)Br',
  'CC(C)Cc1ccc(cc1)C(C)C(=O)O',
  'c1cc[te]cc1',
];

/** Words and numbers that look a little like SMILES: they must stay. */
const SURVIVORS = [
  'RuntimeError',
  'TypeError',
  'Failed to fetch',
  '2026-10-23',
  '2026-10-23T12:30:45.123Z',
  '12:30:45',
  'v1.2.3',
  '1.2.3',
  'index-abc123.js',
  'decimer-web',
  'useEffect',
  'NetworkError when attempting to fetch resource',
  'INFO',
  'SPIN',
  'std::vector',
  'Foo::bar',
  'a :: b',
  '[object Object]',
  "Cannot read properties of undefined (reading 'x')",
  'at https://decimer.ai/assets/index-D4C4ykdf.js:1:2345',
  'Maximum update depth exceeded',
  'retry=3',
  '10s',
  '25C',
  '30s',
  '5MB',
  '1e5',
  '2 pages, 10 structures',
  'timed out after 30s with 25C and 5MB and 1e5 rows',
  'INFO started',
  'decimer_api.worker.pipeline',
  'redis.exceptions.ConnectionError: Error 111 connecting to redis:6379.',
  'ServiceUnavailable: seg unreachable (ConnectError)',
  'pid 1234 killed by SIGKILL after 30.0 s',
  'n=1 key=value can:be a:b x=2 id=7',
  'http://decimer.ai:8000/x',
  'ERROR INFO WARNING DEBUG',
  'really?',
  'what? no! yes, maybe; ok | fine {x} & y',
];

const FILES = [
  'structure_scan.PNG',
  'paper.pdf',
  'C:\\Users\\me\\paper.pdf',
  `/data/jobs/${JOB}/input/document.pdf`,
  'scan-01.jpeg',
  'a.b.c.mol',
  'Brenda_2020.sdf',
  'notes.TXT',
  'results.csv',
  'figure 3.tif', // not quoted: the part after the space is the name
];
const IPS = ['203.0.113.7', '192.168.0.12', '2001:db8::8a2e:370:7334', '::1', 'fe80::1', '2001:0db8:85a3:0000:0000:8a2e:0370:7334'];

describe('scrubText: what goes', () => {
  test.each(SMILES_STRINGS)('the SMILES %s', (smiles) => {
    expect(scrubText(smiles)).toBe(SMILES);
    expect(scrubText(`could not parse '${smiles}' because`)).toBe(`could not parse '${SMILES}' because`);
    expect(scrubText(`invalid structure ${smiles}.`)).not.toContain(smiles);
    expect(scrubText(`smiles=${smiles} failed`)).toBe(`smiles=${SMILES} failed`);
    expect(scrubText(`{"smiles": "${smiles}"}`)).toBe(`{"smiles": "${SMILES}"}`);
    const wrapped = scrubText(`bad (${smiles}).`); // the brackets and the full stop are SMILES pieces too
    expect(wrapped.startsWith('bad ') && wrapped.includes(SMILES) && !wrapped.includes(smiles), wrapped).toBe(true);
  });

  // The probes of the privacy review (2026-10-23), as api/tests/test_scrub.py has them: a SMILES glued to punctuation,
  // a quote of another kind, a key of one letter, a query string, ... The text around it must survive.
  const GLUED_SMILES = [
    'id=CCO',
    'x:CC(=O)O',
    '[SMILES:CCO]',
    'smiles=[CCO]',
    '{CCO}',
    '`CCO`',
    '\u00abCCO\u00bb',
    '\u201cCCO\u201d',
    '\u2018CCO\u2019',
    'CCO|CCN',
    'c1cc[te]cc1',
    'CC(=O)O!',
    'CC(=O)O?',
    "'c1ccccc1?'",
    "KeyError('c1ccccc1?')",
    'c1ccccc1&',
    'smiles=CCO&n=1',
    '/api/jobs/x?smiles=CCO',
    'GET /api/x?smiles=CCO HTTP/1.1',
    'host:8000/x?smiles=CCO',
    '((CCO))',
    '[[CCO]]',
    'mol:{smiles:CCO}',
    'http://chem:8004/names/CCO',
    'GET /api/names/CC(=O)O/x',
    '([Na+].[Cl-]).',
    'saw ((((CCO)))) twice',
    'id=CCO/id=CCO/::1', // (the nesting budget of the first pass is spent: a second pass finishes it)
    'cannot parse \'c1ccccc1?\' for id=CCO (CCO|CCN) at /api/x?smiles=CCO',
  ];

  test.each(GLUED_SMILES)('a SMILES glued to punctuation: %s', (text) => {
    const cleaned = scrubText(text);
    for (const secret of ['CCO', 'CC(=O)O', 'c1ccccc1', '[te]', 'CCN', 'Na+', 'Cl-']) expect(cleaned, secret).not.toContain(secret);
  });

  test('the text around a glued SMILES stays', () => {
    expect(scrubText('bad `CCO`!')).toBe(`bad \`${SMILES}\`!`);
    expect(scrubText('saw {CCO} and \u00abCCN\u00bb')).toBe(`saw {${SMILES}} and \u00ab${SMILES}\u00bb`);
    expect(scrubText('GET /api/x?smiles=CCO HTTP/1.1')).toBe('GET /api/x HTTP/1.1');
    expect(scrubText('id=CCO')).toBe(`id=${SMILES}`);
    expect(scrubText('x:CC(=O)O')).toBe(`x:${SMILES}`);
    expect(scrubText('[SMILES:CCO]')).toBe(`[SMILES:${SMILES}]`);
    expect(scrubText('([Na+].[Cl-]).')).toBe(`(${SMILES}).`);
    expect(scrubText('http://chem:8004/names/CCO')).toBe(`http://chem:8004/names/${SMILES}`);
    expect(scrubText('CCO|CCN')).toBe(`${SMILES}|${SMILES}`);
    expect(scrubText('really?')).toBe('really?');
  });

  test('a query string is cut from a word with a path, with or without a scheme; a SMILES has a slash but never a ?', () => {
    expect(scrubText('/api/jobs/x?name=Zou&n=1')).toBe('/api/jobs/x');
    expect(scrubText('x/y?smiles=CCO#top')).toBe('x/y');
    expect(scrubText('see c1ccccc1/C=C/C?')).toBe(`see ${SMILES}`);
    expect(scrubText('host:8000/x?a=b')).toBe('host:8000/x');
    expect(scrubText('what?')).toBe('what?');
    expect(scrubText('?q=CCC')).toBe(`?q=${SMILES}`); // no path in front of it: not a query (the ? is a delimiter, the SMILES goes)
  });

  test('a SMILES that starts with a lower-case atom needs five characters: the counter n=1 is not a ring', () => {
    expect(scrubText('n=1')).toBe('n=1');
    expect(scrubText('c1cc')).toBe('c1cc');
    expect(scrubText('c1ccc')).toBe(SMILES);
    expect(scrubText('count n=1 of c1ccccc1')).toBe(`count n=1 of ${SMILES}`);
  });

  test('the first word of a text with a SMILES in brackets of every kind', () => {
    expect(scrubText('[CCO]')).toBe(`[${SMILES}]`);
    expect(scrubText('{CCO}.')).toBe(`{${SMILES}}.`);
  });

  test('a SMILES in brackets or ending a sentence: (CCO). and bad (<aspirin>).', () => {
    expect(scrubText('(CCO).')).toBe(`(${SMILES}).`);
    expect(scrubText('(CCO)')).toBe(`(${SMILES})`);
    expect(scrubText(`bad (${ASPIRIN}).`)).toBe(`bad (${SMILES}).`);
    expect(scrubText(`((${ASPIRIN})),`)).toBe(`((${SMILES})),`);
    expect(scrubText(`bad (${ASPIRIN}).`)).not.toContain(ASPIRIN);
    expect(scrubText('(10s).')).toBe('(10s).'); // the core of a wrapper must be a SMILES too
  });

  test('the sentence of the brief', () => {
    expect(scrubText("could not parse 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C' because")).toBe(`could not parse '${SMILES}' because`);
  });

  test('a SMILES after a name: smiles=CCO, mol:CCO, in a JSON text', () => {
    expect(scrubText('smiles=CC(=O)O')).toBe(`smiles=${SMILES}`);
    expect(scrubText('mol:CCCO')).toBe(`mol:${SMILES}`);
    expect(scrubText('{"smiles":"CC(=O)Oc1ccccc1C(=O)O","n":3}')).toBe(`{"smiles":"${SMILES}","n":3}`);
    expect(scrubText('(CC(=O)O)')).toBe(`(${SMILES})`);
  });

  test('a molfile: the whole text goes, with its markers (V2000, V3000, $$$$)', () => {
    expect(scrubText(MOLFILE)).toBe(MOLFILE_REMOVED);
    expect(scrubText(`Cannot read this structure:\n${MOLFILE}\n(Indigo)`)).toBe(MOLFILE_REMOVED);
    expect(scrubText('  0  0  0  0  0  0  0  0  0  0999 V3000')).toBe(MOLFILE_REMOVED);
    expect(scrubText('molecule V3000 stuff')).toBe(MOLFILE_REMOVED);
    expect(scrubText('record one\n$$$$\nrecord two')).toBe(MOLFILE_REMOVED);
    expect(scrubText('M  END')).toBe(MOLFILE_REMOVED);
  });

  test('IPv4 and IPv6 addresses, with a port, in brackets, in a URL', () => {
    expect(scrubText('connect to 192.168.0.12:8090 failed')).toBe(`connect to ${IP}:8090 failed`);
    expect(scrubText('from 203.0.113.7.')).toBe(`from ${IP}.`);
    expect(scrubText('client 2001:db8::8a2e:370:7334 and ::1 and fe80::1')).toBe(`client ${IP} and ${IP} and ${IP}`);
    expect(scrubText('2001:0db8:85a3:0000:0000:8a2e:0370:7334')).toBe(IP);
    expect(scrubText('2001:db8::7334: refused')).toBe(`${IP}: refused`);
    expect(scrubText('2001:0db8:85a3:0000:0000:8a2e:0370:7334: refused')).toBe(`${IP}: refused`);
    expect(scrubText('http://[::1]:8080/x')).toBe(`http://[${IP}]:8080/x`);
    expect(scrubText('::ffff:10.0.0.1')).toBe(`${IP}:${IP}`);
  });

  test('file names: quoted (with spaces), bare, with their directory, Windows and Unix paths', () => {
    expect(scrubText("could not read 'Zou et al 2024 final.pdf'.")).toBe(`could not read ${FILE}.`);
    expect(scrubText('upload "Zou et al 2024 final.pdf" failed')).toBe(`upload ${FILE} failed`);
    expect(scrubText('structure_scan.PNG')).toBe(FILE);
    expect(scrubText('C:\\Users\\me\\paper.pdf')).toBe(FILE);
    expect(scrubText(`/data/jobs/${JOB}/input/document.pdf`)).toBe(FILE);
    expect(scrubText('ENOENT: no such file, open /tmp/x/figure_3.TIFF')).toBe(`ENOENT: no such file, open ${FILE}`);
    expect(scrubText('could not read my_secret_structure.pdf.')).toBe(`could not read ${FILE}.`);
    expect(scrubText('read x.pdf. Then y.png, z.docx;')).toBe(`read ${FILE}. Then ${FILE}, ${FILE};`);
    expect(scrubText('(scan.jpg)')).toBe(`${FILE})`); // the delimiter of a name is white space or a quote
    expect(scrubText('a.zip and b.cdxml and c.smi and d.smiles and e.mol and f.heic and g.webp')).toBe(
      `${FILE} and ${FILE} and ${FILE} and ${FILE} and ${FILE} and ${FILE} and ${FILE}`,
    );
  });

  test.each(['paper.pptx', 'scan.avif', 'scan.jfif', 'foo.svg', 'sheet.xlsx', 'x.ket', 'y.mol2', 'z.tif', 'a.eps', 'b.cml', 'c.rxn', 'd.rdf', 'e.odt', 'f.rtf', 'g.cdx'])(
    'the file kind of %s',
    (name) => {
      expect(scrubText(`cannot read ${name} now`)).toBe(`cannot read ${FILE} now`);
    },
  );

  test.each(['`Zou et al 2024.pdf`', '\u00abZou 2024 draft.pdf\u00bb', '\u201cmy paper.PNG\u201d', '\u2018my paper.pdf\u2019', '"my paper.pdf"', "'my paper.pdf'", "\"it's my paper.pdf\""])(
    'a name with spaces in quotes of any kind: %s',
    (quoted) => {
      expect(scrubText(`upload ${quoted} failed`)).toBe(`upload ${FILE} failed`);
    },
  );

  test('a name is a token: it stops at every delimiter', () => {
    expect(scrubText('a.pdf,b.png;c.zip|d.csv{e.txt}f.svg!g.mol?h.sdf&i.smi')).toBe(
      `${FILE},${FILE};${FILE}|${FILE}{${FILE}}${FILE}!${FILE}?${FILE}&${FILE}`,
    );
    expect(scrubText('a.pdf,b.png;c.zip|d.csv')).toBe(`${FILE},${FILE};${FILE}|${FILE}`);
    expect(scrubText('{a.pdf}')).toBe(`{${FILE}}`);
    expect(scrubText('\u00aba.pdf\u00bb')).toBe(FILE); // (a quoted name)
    expect(scrubText('\u00ab a.pdf \u00bb')).toBe(`\u00ab ${FILE} \u00bb`);
  });

  test('a job id; a query string or fragment of a URL in a text', () => {
    expect(scrubText(`GET /api/jobs/${JOB}/result failed`)).toBe(`GET /api/jobs/${JOB_ID}/result failed`);
    expect(scrubText(JOB.toUpperCase())).toBe(JOB_ID);
    expect(scrubText('Failed to fetch https://decimer.ai/api/x?smiles=CCO&n=1#frag')).toBe('Failed to fetch https://decimer.ai/api/x');
    expect(scrubText(`GET https://decimer.ai/x/export?smiles=${ASPIRIN}#top failed`)).toBe('GET https://decimer.ai/x/export failed');
    expect(scrubText('see https://decimer.ai/#/jobs/abc')).toBe('see https://decimer.ai/');
    expect(scrubText('https://decimer.ai/a/scan.png?smiles=CC(=O)O')).toBe(FILE);
  });

  test('a text longer than the limit is cut before the patterns run, and the cut does not hide a structure', () => {
    const long = scrubText(`${'x'.repeat(MAX_TEXT_CHARS - 4)} ${'C'.repeat(50)}`);
    expect(long).toHaveLength(MAX_TEXT_CHARS - 4 + 1 + SMILES.length + 1 + TRUNCATED.length);
    expect(long.endsWith(`${SMILES} ${TRUNCATED}`)).toBe(true);
    expect(scrubText('x'.repeat(MAX_TEXT_CHARS + 5))).toHaveLength(MAX_TEXT_CHARS + 1 + TRUNCATED.length);
  });
});

describe('scrubText: what stays', () => {
  test.each(SURVIVORS)('%s', (text) => {
    expect(scrubText(text)).toBe(text);
  });

  test('the placeholders themselves, next to a port or a full stop', () => {
    for (const placeholder of [FILE, IP, SMILES, JOB_ID, MOLFILE_REMOVED, `${IP}:8090`, `${FILE}.`, `[${IP}]:8080`]) {
      expect(scrubText(placeholder), placeholder).toBe(placeholder);
    }
  });

  test('an empty text, and text with nothing in it', () => {
    expect(scrubText('')).toBe('');
    expect(scrubText('   ')).toBe('   ');
    expect(scrubText('Ünïcödé 変数 ✓')).toBe('Ünïcödé 変数 ✓');
  });
});

describe('scrubText: its properties', () => {
  // A small seeded generator: the same strings on every run.
  const random = (seed: number) => () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const pick = <T,>(next: () => number, items: readonly T[]): T => items[Math.floor(next() * items.length)]!;
  const FRAGMENTS = [
    ...SMILES_STRINGS,
    ...SURVIVORS,
    ...IPS,
    ...FILES,
    JOB,
    `'Zou et al 2024 final.pdf'`,
    'https://decimer.ai/x?q=CCO#f',
    '[smiles]',
    '[file]',
    '[ip]',
    ':id',
    '[molfile removed]',
    'smiles=CCO',
    '[ip]:8090',
    '1.2.3.4',
    '10s',
    '25C',
    '(CCO).',
    '((c1ccccc1)),',
    'id=CCO',
    '{CCO}',
    '`CCN`',
    '\u00abCCO\u00bb',
    'CCO|CCN',
    '/api/x?smiles=CCO',
    'c1cc[te]cc1',
    'http://chem:8004/names/CCO',
    '([Na+].[Cl-]).',
    'n=1',
    '\u201cmy paper.pdf\u201d',
    '`a b.pdf`',
    '2001:db8::7334:',
    '::',
    '[',
    ']',
    '(',
    ')',
    '.',
    ':',
    '=',
    '/',
    '\\',
    '"',
    "'",
  ];
  const SEPARATORS = ['', ' ', '  ', '\n', ', ', '; ', ' "', '" ', " '", "' ", ' (', ') ', '.', ':', '/', '<', '>'];

  test('scrubText(scrubText(x)) === scrubText(x), for 4000 strings built from every kind of fragment', () => {
    const next = random(20261023);
    const failures: string[] = [];
    for (let i = 0; i < 4000; i++) {
      let text = '';
      for (let n = 1 + Math.floor(next() * 8); n > 0; n--) text += pick(next, FRAGMENTS) + pick(next, SEPARATORS);
      const once = scrubText(text);
      if (scrubText(once) !== once) failures.push(JSON.stringify([text, once, scrubText(once)]));
    }
    expect(failures.slice(0, 5)).toEqual([]);
  });

  test('a structure, an address, a job id or a file name is gone from the text around it, whatever delimits it', () => {
    const next = random(42);
    const around = [' ', '\n', ', ', '; ', ' "', '" ', " '", "' ", ' (', ') ', ' <', '> ', ': '];
    const secrets = [...SMILES_STRINGS, ...IPS, JOB, ...FILES.filter((file) => !file.includes(' '))];
    const leaks: string[] = [];
    for (let i = 0; i < 3000; i++) {
      const secret = pick(next, secrets);
      const text = `Error${pick(next, around)}${secret}${pick(next, around)}at end`;
      const scrubbed = scrubText(text);
      if (scrubbed.includes(secret)) leaks.push(JSON.stringify([text, scrubbed]));
    }
    expect(leaks.slice(0, 5)).toEqual([]);
  });

  test('it never throws and always returns a string, for odd input', () => {
    const odd = [
      'C'.repeat(100_000),
      '['.repeat(50_000),
      `${'a.'.repeat(30_000)}pdf`,
      ':'.repeat(40_000),
      '1:'.repeat(30_000),
      'x'.repeat(40_000) + '\u0000\ud800',
      '\n'.repeat(1000),
      `${'a'.repeat(19_000)}.png`,
      '/'.repeat(19_000),
      '[C'.repeat(9_000),
      'C'.repeat(19_000),
      'x'.repeat(500_000),
      `${'('.repeat(10_000)}CCO${')'.repeat(10_000)}`,
      `${'(['.repeat(5_000)}${'.,'.repeat(5_000)}`,
      'a/'.repeat(9_000),
      `${'a='.repeat(9_000)}CCO`,
      '?/'.repeat(9_000),
      '[{('.repeat(6_000),
    ];
    const started = Date.now();
    for (const text of odd) {
      const cleaned = scrubText(text);
      expect(typeof cleaned).toBe('string');
      expect(cleaned.length).toBeLessThan(21_000);
    }
    expect(Date.now() - started).toBeLessThan(5000); // no pattern is quadratic on these
    expect(scrubText(undefined as unknown as string)).toBe('[scrub failed]');
    expect(scrubText({ toString: () => 'x' } as unknown as string)).toBe('[scrub failed]');
  });
});

// --- the event ----------------------------------------------------------------------------------------------------

const CAFFEINE = 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C';
const UPLOAD = 'Zou et al 2024 final.pdf';
const SECRETS = {
  smiles: [ASPIRIN, CAFFEINE],
  addresses: ['203.0.113.7', '198.51.100.9', '2001:db8::8a2e:370:7334'],
  files: [UPLOAD, 'structure_scan.PNG', 'paper_draft.docx'],
  job: JOB,
  others: [
    'user-4711',
    'someone@example.org',
    'SECRET-COOKIE-VALUE',
    'SECRET-UA',
    'SECRET-TOKEN',
    'SECRET-QUERY',
    'SECRET-HANDLER',
    'SECRET-CONTEXT',
    'SECRET-HOST',
    'SECRET-VAR',
    'SECRET-SOURCE-LINE',
    'SECRET-EXTRA',
    'SECRET-CRUMB',
    'SECRET-DEVICE',
    'SECRET-MODULE',
    'SECRET-SPAN',
    'SECRET-FRAGMENT',
    'SECRET-BODY',
    'Berlin',
  ],
};
const ALL_SECRETS = [...SECRETS.smiles, ...SECRETS.addresses, ...SECRETS.files, SECRETS.job, ...SECRETS.others, 'V2000', 'M  END'];

/** An event the way the SDK, and a few integrations we do not use, would build it: full of things that must not leave. */
function crafted(): Event {
  const event = {
    event_id: 'e'.repeat(32),
    timestamp: 1792764039.5,
    platform: 'javascript',
    level: 'error',
    release: 'abc123',
    environment: 'production',
    server_name: 'SECRET-HOST',
    dist: 'SECRET-DIST',
    sdk: {
      name: 'sentry.javascript.browser',
      version: '11.6.0',
      integrations: ['SECRET-INTEGRATION'],
      packages: [{ name: 'npm:@sentry/browser', version: '11.6.0' }],
      settings: { infer_ip: 'never', nested: { a: 1 } },
    },
    fingerprint: ['{{ default }}', 42],
    message: `Could not read '${UPLOAD}' (${ASPIRIN}) from 203.0.113.7`,
    logentry: { message: 'bad structure %s', formatted: `bad structure ${ASPIRIN}`, params: [CAFFEINE] },
    logger: 'decimer.web',
    transaction: `/jobs/${JOB}/result`,
    modules: { 'SECRET-MODULE': '1.0.0' },
    user: { id: 'user-4711', email: 'someone@example.org', ip_address: '203.0.113.7', username: 'SECRET-EXTRA' },
    extra: { molfile: MOLFILE, smiles: ASPIRIN, nested: { file: 'structure_scan.PNG' }, note: 'SECRET-EXTRA paper_draft.docx' },
    breadcrumbs: [
      { type: 'ui.click', message: 'uploaded structure_scan.PNG SECRET-CRUMB', data: { url: `https://decimer.ai/?smiles=${CAFFEINE}` } },
      { category: 'console', message: ASPIRIN },
    ],
    tags: { stage: `parse ${ASPIRIN}`, attempt: 3, nested: { a: SECRETS.job }, flag: true, ok: 'fine', 'bad-job': JOB },
    contexts: {
      device: { model: 'SECRET-DEVICE', screen_resolution: '1x1' },
      browser: { name: 'SECRET-CONTEXT' },
      culture: { locale: 'de-DE', timezone: 'Europe/Berlin' },
      trace: { trace_id: 't'.repeat(32), span_id: 's'.repeat(16) },
      response: { status_code: 500 },
      runtime: { name: 'Chrome', version: '122.0', SECRET_CONTEXT_KEY: 'SECRET-CONTEXT', nested: { x: 'SECRET-CONTEXT' } },
      os: { name: 'Linux', version: '6.8', kernel_version: 'SECRET-CONTEXT' },
    },
    debug_meta: { images: [{ type: 'sourcemap', code_file: 'SECRET-MODULE' }] },
    threads: { values: [{ id: 1, name: 'SECRET-MODULE', stacktrace: { frames: [{ function: 'a', vars: { x: 'SECRET-VAR' } }] } }] },
    spans: [{ description: 'SECRET-SPAN', span_id: 'x' }],
    measurements: { lcp: { value: 1 } },
    sdkProcessingMetadata: { capturedSpanScope: { user: { id: 'user-4711' } }, normalizeDepth: 3 },
    _meta: { message: { '': { rem: [['SECRET-EXTRA', 's']] } } },
    request: {
      method: 'POST',
      url: `https://decimer.ai/api/jobs/${JOB}/crops?q=${CAFFEINE}&k=SECRET-QUERY#SECRET-FRAGMENT-${ASPIRIN}`,
      query_string: `q=${CAFFEINE}&k=SECRET-QUERY`,
      cookies: { session: 'SECRET-COOKIE-VALUE' },
      data: { file: UPLOAD, smiles: ASPIRIN, note: 'SECRET-BODY' },
      env: { REMOTE_ADDR: '203.0.113.7' },
      headers: {
        'Content-Type': 'application/json',
        'content-length': '123',
        Cookie: 'session=SECRET-COOKIE-VALUE',
        'User-Agent': 'Mozilla/5.0 SECRET-UA',
        'X-Forwarded-For': '198.51.100.9',
        'X-Real-IP': '198.51.100.9',
        Referer: `https://decimer.ai/?smiles=${ASPIRIN}`,
        Authorization: 'Bearer SECRET-TOKEN',
        Origin: 'https://decimer.ai',
      },
    },
    exception: {
      values: [
        {
          type: 'TypeError',
          value: `cannot parse '${ASPIRIN}' in '${UPLOAD}' from 203.0.113.7 (2001:db8::8a2e:370:7334), job ${JOB}`,
          module: 'decimer',
          thread_id: 7,
          mechanism: {
            type: 'onerror',
            handled: false,
            synthetic: true,
            data: { handler: 'SECRET-HANDLER', smiles: CAFFEINE },
          },
          stacktrace: {
            frames: [
              {
                filename: 'https://decimer.ai/assets/index-abc123.js?v=SECRET-QUERY#SECRET-FRAGMENT',
                abs_path: 'https://decimer.ai/assets/index-abc123.js?v=SECRET-QUERY',
                function: 'parseStructure',
                module: 'app/parse',
                lineno: 10,
                colno: 5,
                in_app: true,
                context_line: `const s = '${ASPIRIN}'; // SECRET-SOURCE-LINE`,
                pre_context: ['// SECRET-SOURCE-LINE'],
                post_context: [`return '${CAFFEINE}'`],
                vars: { smiles: ASPIRIN, file: 'structure_scan.PNG', note: 'SECRET-VAR' },
                debug_id: 'SECRET-MODULE',
              },
              { filename: 'native', function: 'Array.map' },
            ],
          },
        },
        { type: 'Error', value: 'second', stacktrace: { frames: [] } },
      ],
    },
  };
  return event as unknown as Event;
}

/** `scrubEvent` that fails the test if it drops the event. */
function scrubbed(event: Event) {
  const result = scrubEvent(event);
  expect(result).not.toBeNull();
  return JSON.parse(JSON.stringify(result)); // as it would go over the wire (and untyped, to read it freely)
}

describe('scrubEvent', () => {
  test('nothing of the sensitive strings is anywhere in the result', () => {
    const json = JSON.stringify(scrubbed(crafted()));
    for (const secret of ALL_SECRETS) expect(json, secret).not.toContain(secret);
    // the fixture really holds them all, so the test above cannot pass for the wrong reason
    const before = JSON.stringify(crafted());
    for (const secret of ALL_SECRETS) expect(before, secret).toContain(secret);
  });

  test('what a crash report needs is still there', () => {
    const event = scrubbed(crafted());
    expect(event.event_id).toBe('e'.repeat(32));
    expect(event.timestamp).toBe(1792764039.5);
    expect([event.platform, event.level, event.release, event.environment]).toEqual(['javascript', 'error', 'abc123', 'production']);
    expect(event.sdk).toEqual({ name: 'sentry.javascript.browser', version: '11.6.0', settings: { infer_ip: 'never' } });
    expect(event.fingerprint).toEqual(['{{ default }}']);
    expect(event.logger).toBe('decimer.web');
    expect(event.message).toBe(`Could not read ${FILE} (${SMILES}) from ${IP}`);
    expect(event.logentry).toEqual({ message: `bad structure ${SMILES}` });
    expect(event.transaction).toBe(`/jobs/${JOB_ID}/result`);
    expect(event.tags).toEqual({ stage: `parse ${SMILES}`, attempt: '3', flag: 'true', ok: 'fine', 'bad-job': JOB_ID });
    expect(event.contexts).toEqual({ runtime: { name: 'Chrome', version: '122.0' }, os: { name: 'Linux', version: '6.8' } });

    const [first, second] = event.exception.values;
    expect(first.type).toBe('TypeError');
    expect(first.value).toBe(`cannot parse '${SMILES}' in ${FILE} from ${IP} (${IP}), job ${JOB_ID}`);
    expect(first.module).toBe('decimer');
    expect(first.mechanism).toEqual({ type: 'onerror', handled: false });
    expect(first.thread_id).toBeUndefined();
    expect(first.stacktrace.frames).toEqual([
      {
        filename: 'https://decimer.ai/assets/index-abc123.js',
        abs_path: 'https://decimer.ai/assets/index-abc123.js',
        function: 'parseStructure',
        module: 'app/parse',
        lineno: 10,
        colno: 5,
        in_app: true,
      },
      { filename: 'native', function: 'Array.map' },
    ]);
    expect(second).toEqual({ type: 'Error', value: 'second', stacktrace: { frames: [] } });
  });

  test('the request keeps its method and its URL without query and fragment, and two headers', () => {
    const { request } = scrubbed(crafted());
    expect(Object.keys(request).sort()).toEqual(['headers', 'method', 'url']);
    expect(request.method).toBe('POST');
    expect(request.url).toBe(`https://decimer.ai/api/jobs/${JOB_ID}/crops`);
    expect(request.headers).toEqual({ 'Content-Type': 'application/json', 'content-length': '123' });
  });

  test('a URL without a scheme loses its query and fragment too (nothing else would strip it)', () => {
    const event = {
      request: { url: '/assets/x?q=SECRET-QUERY#SECRET-FRAGMENT' },
      exception: { values: [{ stacktrace: { frames: [{ filename: '/assets/index.js?v=SECRET-QUERY#SECRET-FRAGMENT', abs_path: 'index.js?v=SECRET-QUERY' }] } }] },
    };
    const result = scrubbed(event as unknown as Event);
    expect(result.request.url).toBe('/assets/x');
    expect(result.exception.values[0].stacktrace.frames[0]).toEqual({ filename: '/assets/index.js', abs_path: 'index.js' });
  });

  test('only the allowlisted keys of an event survive, whatever else the SDK adds', () => {
    const event = crafted() as Record<string, unknown>;
    event.some_new_field = { from: 'a newer SDK', smiles: ASPIRIN };
    const result = scrubbed(event as Event);
    expect(Object.keys(result).sort()).toEqual(
      [
        'contexts', 'environment', 'event_id', 'exception', 'fingerprint', 'level', 'logentry', 'logger', 'message',
        'platform', 'release', 'request', 'sdk', 'tags', 'timestamp', 'transaction',
      ].sort(),
    );
    expect(Object.keys(result.contexts).sort()).toEqual(['os', 'runtime']);
    expect(Object.keys(result.exception.values[0]).sort()).toEqual(['mechanism', 'module', 'stacktrace', 'type', 'value']);
    expect(Object.keys(result.exception.values[0].stacktrace.frames[0]).sort()).toEqual(
      ['abs_path', 'colno', 'filename', 'function', 'in_app', 'lineno', 'module'],
    );
  });

  test('the headers are compared without regard to case, and a header without a value is skipped', () => {
    const event = { request: { headers: { 'CONTENT-TYPE': 'text/plain', 'Content-Length': '5', 'COOKIE': 'a=b', Accept: 'x', 'content-type-x': 'no', 'X-Empty': null } } };
    expect(scrubbed(event as unknown as Event).request.headers).toEqual({ 'CONTENT-TYPE': 'text/plain', 'Content-Length': '5' });
  });

  test('it builds a new event: the input is not changed, and the output shares nothing with it', () => {
    const event = crafted();
    const result = scrubbed(event);
    expect(event).toEqual(crafted());
    type Crash = { exception: { values: { stacktrace: { frames: object[] } }[] } };
    const [input, output] = [event, scrubEvent(event)].map((e) => (e as unknown as Crash).exception.values[0]!.stacktrace.frames[0]);
    expect(output).not.toBe(input);
    expect(result.exception.values[0].stacktrace.frames[0]).toBeDefined();
  });

  test('an event of any odd shape gives a (small) event, and one that cannot be checked is dropped', () => {
    expect(scrubEvent({})).toEqual({});
    expect(scrubEvent({ exception: { values: 'x' }, tags: 'x', contexts: 3, request: [], logentry: 'x' } as unknown as Event)).toEqual({
      exception: { values: [] },
    });
    expect(scrubbed({ message: 5, exception: { values: [null, 'x', { value: { a: 1 } }] } } as unknown as Event)).toEqual({
      message: '5',
      exception: { values: [{}, {}, { value: '[object Object]' }] },
    });
    const hostile = {} as Event;
    Object.defineProperty(hostile, 'message', {
      enumerable: true,
      get() {
        throw new Error('boom');
      },
    });
    expect(scrubEvent(hostile)).toBeNull(); // better no report than one that could not be checked
  });
});
