import { describe, expect, test, vi } from 'vitest';
import { API_ERROR_CODES, ApiError, SERVER_ERROR_CODES } from './contract';
import type { components } from './generated';
import { HttpApiClient, parseRetryAfter } from './http';
import schema from './openapi.json';

type JobView = components['schemas']['JobView'];

const JOB_ID = 'a'.repeat(32);

/** A job as FastAPI sends it: every field the schema marks required, and the ones with defaults left out. */
const wireJob = (over: Partial<JobView> = {}): JobView => ({
  id: JOB_ID,
  status: 'queued',
  stages: [
    { name: 'upload', state: 'done' },
    { name: 'render', state: 'pending' },
    { name: 'segment', state: 'pending' },
    { name: 'recognise', state: 'pending' },
    { name: 'check', state: 'pending' },
  ],
  pages: 1,
  pagesProcessed: 1,
  truncated: false,
  structuresFound: 0,
  structuresDropped: 0,
  createdAt: '2026-10-22T12:00:00Z',
  expiresAt: '2026-10-22T13:00:00Z',
  ...over,
});

const wireResult = (index: number, over: Record<string, unknown> = {}) => ({
  index,
  number: index + 1,
  pageIndex: 0,
  bbox: [1, 2, 3, 4] as [number, number, number, number],
  smiles: 'CCO',
  inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N',
  valid: true,
  isStructure: true,
  confidence: 0.97,
  molfile: '\n  X\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n',
  cropUrl: `/api/jobs/${JOB_ID}/media/crop-${index}.png`,
  ...over,
});

function json(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json', ...init.headers }, ...init });
}

function clientWith(respond: (url: string, init: RequestInit) => Response | Promise<Response>, baseUrl = '') {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => respond(String(input), init ?? {}));
  return { api: new HttpApiClient({ baseUrl, fetch: fetchMock as unknown as typeof fetch }), fetchMock };
}

const call = (fetchMock: ReturnType<typeof vi.fn>, n = 0) => {
  const [url, init] = fetchMock.mock.calls[n] as [string, RequestInit];
  return { url, init, headers: new Headers(init.headers) };
};

// ---- POST /api/jobs ------------------------------------------------------------------------------

describe('createJob', () => {
  test('posts every file as a `file` part of a multipart body and returns the id of the 202 job', async () => {
    const { api, fetchMock } = clientWith(() => json(wireJob(), { status: 202 }));
    const a = new File(['aaa'], 'paper-1.png', { type: 'image/png' });
    const b = new File(['bbb'], 'paper-2.png', { type: 'image/png' });
    const controller = new AbortController();
    await expect(api.createJob([a, b], controller.signal)).resolves.toEqual({ id: JOB_ID });

    const { url, init, headers } = call(fetchMock);
    expect(url).toBe('/api/jobs');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    const parts = (init.body as FormData).getAll('file') as File[];
    expect(parts.map((f) => f.name)).toEqual(['paper-1.png', 'paper-2.png']);
    expect([...(init.body as FormData).keys()]).toEqual(['file', 'file']);
    // The browser has to write the multipart boundary itself.
    expect(headers.has('Content-Type')).toBe(false);
    expect(headers.get('Accept')).toBe('application/json');
    expect(init.signal).toBe(controller.signal);
  });

  test('a base URL goes in front of every path', async () => {
    const { api, fetchMock } = clientWith(() => json(wireJob(), { status: 202 }), 'https://decimer.example/');
    await api.createJob([new File(['x'], 'a.png')]);
    expect(call(fetchMock).url).toBe('https://decimer.example/api/jobs');
  });

  test('a 202 without a job id is an error, not a job that can never be polled', async () => {
    const { api } = clientWith(() => json({ nope: true }, { status: 202 }));
    await expect(api.createJob([new File(['x'], 'a.png')])).rejects.toMatchObject({ code: 'unknown' });
  });
});

// ---- GET /api/jobs/{id} ---------------------------------------------------------------------------

describe('getJob', () => {
  test('reads the job from the path, uncached', async () => {
    const { api, fetchMock } = clientWith(() => json(wireJob()));
    const job = await api.getJob(JOB_ID);
    const { url, init } = call(fetchMock);
    expect(url).toBe(`/api/jobs/${JOB_ID}`);
    expect(init.method ?? 'GET').toBe('GET');
    expect(init.cache).toBe('no-store');
    expect(job).toMatchObject({ id: JOB_ID, status: 'queued', pages: 1 });
  });

  test('fields the API leaves out come back as null, [] and "" (queue position, error, warnings, results, name…)', async () => {
    const { api } = clientWith(() =>
      json(wireJob({ status: 'done', structuresFound: 1, results: [wireResult(0)] as JobView['results'] })),
    );
    const job = await api.getJob(JOB_ID);
    expect(job.queuePosition).toBeNull();
    expect(job.error).toBeNull();
    expect(job.warnings).toEqual([]);
    expect(job.pageUrls).toEqual([]);
    expect(job.results).toHaveLength(1);
    expect(job.results[0]).toMatchObject({ name: null, nameTier: null, nameVerified: null, svg: '', number: 1, isStructure: true });
    expect(wireJob().results).toBeUndefined(); // (the wire job really did omit them)
  });

  // Who accepted the SMILES (decision D4): the server's word when it gives one, RDKit's for a valid result when it
  // does not (an older API), nobody for an invalid one.
  test.each([
    [{ valid: true, validBy: 'rdkit' }, 'rdkit'],
    [{ valid: false, validBy: 'cdk' }, 'cdk'],
    [{ valid: false, validBy: null }, null],
    [{ valid: true }, 'rdkit'],
    [{ valid: false }, null],
  ])('validBy of %j reaches the UI as %j', async (over, expected) => {
    const results = [wireResult(0, over)] as unknown as JobView['results'];
    const { api } = clientWith(() => json(wireJob({ status: 'done', results })));
    const [result] = (await api.getJob(JOB_ID)).results;
    expect(result?.validBy).toBe(expected);
    expect(result?.valid).toBe(over.valid);
  });

  // Cut off at the model's token limit (decision D25): the server's word, false when an older API says nothing.
  test.each([
    [{ incomplete: true }, true],
    [{ incomplete: false }, false],
    [{}, false],
  ])('incomplete of %j reaches the UI as %j', async (over, expected) => {
    const results = [wireResult(0, over)] as unknown as JobView['results'];
    const { api } = clientWith(() => json(wireJob({ status: 'done', results })));
    const [result] = (await api.getJob(JOB_ID)).results;
    expect(result?.incomplete).toBe(expected);
  });

  test('a job still running has structuresFound and no results yet', async () => {
    const { api } = clientWith(() => json(wireJob({ status: 'running', structuresFound: 12, queuePosition: null })));
    const job = await api.getJob(JOB_ID);
    expect(job.structuresFound).toBe(12);
    expect(job.results).toEqual([]);
  });

  test('a null isStructure (no classifier) and the name fields come through untouched', async () => {
    const results = [
      wireResult(0, { isStructure: null, name: '(2R)-butan-2-ol', nameTier: 'pin_verified', nameVerified: 'opsin' }),
    ] as unknown as JobView['results'];
    const { api } = clientWith(() => json(wireJob({ status: 'done', results })));
    const [result] = (await api.getJob(JOB_ID)).results;
    expect(result).toMatchObject({ isStructure: null, name: '(2R)-butan-2-ol', nameTier: 'pin_verified', nameVerified: 'opsin' });
  });

  // The API sends media paths from its own root; they go behind the same base as every request, which is what
  // VITE_API_URL says: nothing, a path the API is mounted at, an origin, or both (env.d.ts).
  test.each([
    ['', `/api/jobs/${JOB_ID}/media/crop-2.png`],
    ['/', `/api/jobs/${JOB_ID}/media/crop-2.png`],
    ['/beta', `/beta/api/jobs/${JOB_ID}/media/crop-2.png`],
    ['/beta/', `/beta/api/jobs/${JOB_ID}/media/crop-2.png`],
    ['https://decimer.example', `https://decimer.example/api/jobs/${JOB_ID}/media/crop-2.png`],
    ['https://decimer.example/', `https://decimer.example/api/jobs/${JOB_ID}/media/crop-2.png`],
    ['https://decimer.example/gateway', `https://decimer.example/gateway/api/jobs/${JOB_ID}/media/crop-2.png`],
    ['https://decimer.example/gateway/', `https://decimer.example/gateway/api/jobs/${JOB_ID}/media/crop-2.png`],
  ])('crop URLs go behind the base URL %j: %s', async (baseUrl, expected) => {
    const body = wireJob({ status: 'done', results: [wireResult(2)] as JobView['results'] });
    const { api, fetchMock } = clientWith(() => json(body), baseUrl);
    const job = await api.getJob(JOB_ID);
    expect(job.results[0]?.cropUrl).toBe(expected);
    // Behind the same base as the request that returned it.
    const requested = call(fetchMock).url;
    expect(expected.startsWith(requested.slice(0, requested.indexOf('/api/jobs/')))).toBe(true);
  });

  // The page images of the upload go behind the base exactly like the crops (same origin, so the CSP's img-src holds).
  test.each(['', '/beta', 'https://decimer.example/gateway'])('page URLs go behind the base URL %j, in page order', async (baseUrl) => {
    const pageUrls = [0, 1, 2].map((i) => `/api/jobs/${JOB_ID}/media/page-${i}.png`);
    const { api } = clientWith(() => json(wireJob({ status: 'done', pages: 3, pagesProcessed: 3, pageUrls })), baseUrl);
    const job = await api.getJob(JOB_ID);
    const base = baseUrl.replace(/\/$/, '');
    expect(job.pageUrls).toEqual(pageUrls.map((url) => `${base}${url}`));
  });

  test('a media URL that already names an origin is left as the server sent it', async () => {
    const abs = `https://cdn.example/crops/${JOB_ID}/crop-0.png`;
    const body = wireJob({ status: 'done', results: [wireResult(0, { cropUrl: abs }), wireResult(1, { cropUrl: '//cdn.example/c.png' })] as JobView['results'] });
    for (const baseUrl of ['', '/beta', 'https://decimer.example/gateway']) {
      const job = await clientWith(() => json(body), baseUrl).api.getJob(JOB_ID);
      expect(job.results.map((r) => r.cropUrl), baseUrl).toEqual([abs, '//cdn.example/c.png']);
    }
  });

  test('a media path without a leading slash is taken from the base', async () => {
    const body = wireJob({ status: 'done', results: [wireResult(0, { cropUrl: 'api/c.png' })] as JobView['results'] });
    expect((await clientWith(() => json(body), '/beta').api.getJob(JOB_ID)).results[0]?.cropUrl).toBe('/beta/api/c.png');
  });

  test('an id is escaped into the path', async () => {
    const { api, fetchMock } = clientWith(() => json(wireJob()));
    await api.getJob('a/b?c');
    expect(call(fetchMock).url).toBe('/api/jobs/a%2Fb%3Fc');
  });
});

// ---- errors ----------------------------------------------------------------------------------------

describe('error mapping', () => {
  const detail = (code: string, message = `message for ${code}`) => ({ detail: { code, message } });

  test.each([
    [400, 'empty_upload'],
    [400, 'too_many_files'],
    [415, 'unsupported'],
    [400, 'mixed_upload'],
    [400, 'multiple_pdfs'],
    [400, 'corrupt_file'],
    [400, 'encrypted_pdf'],
    [400, 'too_many_pixels'],
    [413, 'too_large'],
    [400, 'invalid_request'],
    [429, 'rate_limited'],
    [503, 'queue_full'],
    [503, 'unavailable'],
    [404, 'not_found'],
    [413, 'processing_limit'],
    [503, 'busy'],
    [409, 'no_results'],
    [405, 'method_not_allowed'],
    [418, 'http_error'],
    [500, 'internal_error'],
  ])('%i %s: the API\'s code, status and message', async (status, code) => {
    const { api } = clientWith(() => json(detail(code), { status }));
    const error = await api.createJob([new File(['x'], 'a.png')]).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code, serverMessage: `message for ${code}` });
  });

  test('the codes the client reads are exactly the schema\'s ErrorCode enum (the committed openapi.json)', () => {
    const documented = schema.components.schemas.ErrorCode.enum;
    expect([...SERVER_ERROR_CODES].sort()).toEqual([...documented].sort());
    expect([...API_ERROR_CODES].sort()).toEqual([...documented, 'network', 'unknown'].sort());
  });

  test('a code from a newer API falls back to what the status says, and keeps the message', async () => {
    const { api } = clientWith(() => json(detail('brand_new_code', 'Something new.'), { status: 429 }));
    await expect(api.getJob(JOB_ID)).rejects.toMatchObject({ code: 'rate_limited', status: 429, serverMessage: 'Something new.' });
  });

  test('the two codes the client makes up are never taken from the server', async () => {
    const { api } = clientWith(() => json(detail('network'), { status: 400 }));
    await expect(api.getJob(JOB_ID)).rejects.toMatchObject({ code: 'invalid_request' });
  });

  test('FastAPI\'s own validation error (detail is a list) is invalid_request', async () => {
    const { api } = clientWith(() => json({ detail: [{ loc: ['body', 'file'], msg: 'Field required', type: 'missing' }] }, { status: 422 }));
    await expect(api.createJob([new File(['x'], 'a.png')])).rejects.toMatchObject({ code: 'invalid_request', status: 422, serverMessage: null });
  });

  test.each([
    [404, 'not_found'],
    [413, 'too_large'],
    [415, 'unsupported'],
    [429, 'rate_limited'],
    [502, 'unavailable'],
    [503, 'unavailable'],
    [504, 'unavailable'],
    [500, 'unknown'],
  ])('%i with a body that is not the API\'s (a proxy\'s HTML page) is %s', async (status, code) => {
    const { api } = clientWith(() => new Response('<html>Bad gateway</html>', { status, headers: { 'Content-Type': 'text/html' } }));
    await expect(api.getJob(JOB_ID)).rejects.toMatchObject({ status, code, serverMessage: null });
  });

  test('no answer at all is a network error, status 0', async () => {
    const { api } = clientWith(() => {
      throw new TypeError('Failed to fetch');
    });
    await expect(api.getJob(JOB_ID)).rejects.toMatchObject({ status: 0, code: 'network' });
  });

  test('an aborted request stays an AbortError (the caller ended it; it is not a failure)', async () => {
    const { api, fetchMock } = clientWith(() => {
      throw new DOMException('aborted', 'AbortError');
    });
    const controller = new AbortController();
    const error = await api.getJob(JOB_ID, controller.signal).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe('AbortError');
    expect(call(fetchMock).init.signal).toBe(controller.signal);
  });

  // fetch resolves at the headers; the body can still fail on the way (the connection drops: a TypeError).
  const droppedBody = (error: unknown) => new Response(new ReadableStream({ start: (stream) => stream.error(error) }), { status: 200 });
  const BODY_CALLS: [string, (api: HttpApiClient) => Promise<unknown>][] = [
    ['createJob', (api) => api.createJob([new File(['x'], 'a.png')])],
    ['getJob', (api) => api.getJob(JOB_ID)],
    ['validate', (api) => api.validate(JOB_ID, 0, 'M')],
    ['exportJob', (api) => api.exportJob(JOB_ID, { format: 'zip', molfiles: {} })],
  ];

  test.each(BODY_CALLS)('%s: a connection that drops while the body is read is a network error, like no answer at all', async (_name, run) => {
    const { api } = clientWith(() => droppedBody(new TypeError('terminated')));
    const error = await run(api).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: 'network' });
  });

  test.each(BODY_CALLS)('%s: an abort while the body is read stays an AbortError', async (_name, run) => {
    const { api } = clientWith(() => droppedBody(new DOMException('aborted', 'AbortError')));
    const error = await run(api).catch((e: unknown) => e);
    expect(error).not.toBeInstanceOf(ApiError);
    expect((error as { name?: string }).name).toBe('AbortError');
  });

  test('a 200 that is not JSON is not mistaken for a dropped connection', async () => {
    const { api } = clientWith(() => new Response('<html>hello</html>', { status: 200 }));
    const error = await api.getJob(JOB_ID).catch((e: unknown) => e);
    expect(error).not.toMatchObject({ code: 'network' });
    await expect(api.createJob([new File(['x'], 'a.png')])).rejects.toMatchObject({ code: 'unknown', status: 200 });
  });

  test('Retry-After on a 429 or 503 is carried on the error, in milliseconds', async () => {
    const limited = clientWith(() => json(detail('rate_limited'), { status: 429, headers: { 'Retry-After': '7' } }));
    await expect(limited.api.getJob(JOB_ID)).rejects.toMatchObject({ retryAfterMs: 7000 });
    const full = clientWith(() => json(detail('unavailable'), { status: 503, headers: { 'Retry-After': '5' } }));
    await expect(full.api.getJob(JOB_ID)).rejects.toMatchObject({ retryAfterMs: 5000 });
    const none = clientWith(() => json(detail('not_found'), { status: 404 }));
    await expect(none.api.getJob(JOB_ID)).rejects.toMatchObject({ retryAfterMs: null });
  });
});

describe('parseRetryAfter', () => {
  test('seconds, an HTTP date, and nonsense', () => {
    expect(parseRetryAfter('5')).toBe(5000);
    expect(parseRetryAfter(' 0 ')).toBe(0);
    expect(parseRetryAfter('Wed, 21 Oct 2026 07:28:30 GMT', Date.parse('Wed, 21 Oct 2026 07:28:00 GMT'))).toBe(30_000);
    expect(parseRetryAfter('Wed, 21 Oct 2026 07:28:30 GMT', Date.parse('Thu, 22 Oct 2026 07:28:00 GMT'))).toBe(0); // already past
    expect(parseRetryAfter('soon')).toBeNull();
    expect(parseRetryAfter('')).toBeNull();
    expect(parseRetryAfter(null)).toBeNull();
  });
});

// ---- validate, export, report, delete -----------------------------------

describe('delete', () => {
  test('sends DELETE to the job\'s path and resolves on 204', async () => {
    const { api, fetchMock } = clientWith(() => new Response(null, { status: 204 }));
    const controller = new AbortController();
    await expect(api.deleteJob(JOB_ID, controller.signal)).resolves.toBeUndefined();
    const { url, init } = call(fetchMock);
    expect(url).toBe(`/api/jobs/${JOB_ID}`);
    expect(init.method).toBe('DELETE');
    expect(init.body).toBeUndefined();
    expect(init.signal).toBe(controller.signal);
  });

  test('an id is escaped into the path, and a base URL goes in front', async () => {
    const { api, fetchMock } = clientWith(() => new Response(null, { status: 204 }), '/beta');
    await api.deleteJob('a/b c');
    expect(call(fetchMock).url).toBe('/beta/api/jobs/a%2Fb%20c');
  });

  test('a job that is already gone is a not_found error, which the caller may ignore', async () => {
    const { api } = clientWith(() => json({ detail: { code: 'not_found', message: 'Not found.' } }, { status: 404 }));
    await expect(api.deleteJob(JOB_ID)).rejects.toMatchObject({ status: 404, code: 'not_found' });
  });
});

describe('validate, export and report', () => {
  test('validate posts the molfile to the result\'s own path, n being the 0-based index', async () => {
    const { api, fetchMock } = clientWith(() => json({ smiles: 'CCO', inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', svg: '<svg/>', valid: true, name: 'ethanol', nameTier: 'pin_verified', nameVerified: 'opsin' }));
    const controller = new AbortController();
    const answer = await api.validate(JOB_ID, 3, 'MOLFILE', controller.signal);
    const { url, init, headers } = call(fetchMock);
    expect(url).toBe(`/api/jobs/${JOB_ID}/results/3/validate`);
    expect(init.method).toBe('POST');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual({ molfile: 'MOLFILE' });
    expect(init.signal).toBe(controller.signal);
    expect(answer).toEqual({ smiles: 'CCO', inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', svg: '<svg/>', valid: true, name: 'ethanol', nameTier: 'pin_verified', nameVerified: 'opsin' });
  });

  test('validate fills what the server leaves out, and an invalid structure has no key', async () => {
    const { api } = clientWith(() => json({ valid: false }));
    expect(await api.validate(JOB_ID, 0, 'M')).toEqual({ smiles: '', inchikey: '', svg: '', valid: false, name: null, nameTier: null, nameVerified: null });
  });

  test('export posts the format and the edited molfiles by index, and returns the file with the server\'s name for it', async () => {
    const { api, fetchMock } = clientWith(
      () => new Response('ZIPBYTES', { headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="decimer-abc.zip"' } }),
    );
    const out = await api.exportJob(JOB_ID, { format: 'zip', molfiles: { 2: 'EDIT-2', 7: 'EDIT-7' } });
    const { url, init, headers } = call(fetchMock);
    expect(url).toBe(`/api/jobs/${JOB_ID}/export`);
    expect(init.method).toBe('POST');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual({ format: 'zip', molfiles: { '2': 'EDIT-2', '7': 'EDIT-7' } });
    expect(out.filename).toBe('decimer-abc.zip');
    expect(await out.blob.text()).toBe('ZIPBYTES');
  });

  test('export asks for the kind of file it is getting, not for JSON', async () => {
    const { api, fetchMock } = clientWith(() => new Response('x'));
    await api.exportJob(JOB_ID, { format: 'zip', molfiles: {} });
    await api.exportJob(JOB_ID, { format: 'sdf', molfiles: {} });
    await api.exportJob(JOB_ID, { format: 'csv', molfiles: {} });
    expect([0, 1, 2].map((n) => call(fetchMock, n).headers.get('Accept'))).toEqual(['application/zip', 'chemical/x-mdl-sdfile', 'text/csv']);
  });

  test('an export the server refuses (409 no results, 413, 422, 429) is an ApiError with the server\'s code', async () => {
    for (const [status, code] of [[409, 'no_results'], [413, 'too_large'], [422, 'invalid_request'], [429, 'rate_limited']] as const) {
      const { api } = clientWith(() => json({ detail: { code, message: 'no' } }, { status }));
      await expect(api.exportJob(JOB_ID, { format: 'zip', molfiles: {} })).rejects.toMatchObject({ status, code });
    }
  });

  // RFC 6266 and RFC 8187: `filename*` is percent-encoded with a charset, `filename` is literal text. A name that
  // cannot be read is not a reason to fail the download: the page names the file itself.
  test.each<[string, string | null, string]>([
    ['a plain filename with a percent sign is literal text, not an escape', 'attachment; filename="100%.zip"', '100%.zip'],
    ['a plain filename is never percent-decoded', 'attachment; filename="a%20b.zip"', 'a%20b.zip'],
    ['an unquoted token', 'attachment; filename=report.csv', 'report.csv'],
    ['a quoted name with a semicolon and an escaped quote', 'attachment; filename="a;\\"b\\".zip"', 'a;"b".zip'],
    ['filename* is percent-decoded as UTF-8', "attachment; filename*=UTF-8''caf%C3%A9.zip", 'café.zip'],
    ['filename* with a language tag', "attachment; filename*=UTF-8'en'caf%C3%A9.zip", 'café.zip'],
    ['filename* in ISO-8859-1', "attachment; filename*=iso-8859-1''caf%E9.zip", 'café.zip'],
    ['filename* wins over filename when it comes second', 'attachment; filename="fallback.zip"; filename*=UTF-8\'\'real%20name.zip', 'real name.zip'],
    ['filename* wins when it comes first', 'attachment; filename*=UTF-8\'\'real%20name.zip; filename="fallback.zip"', 'real name.zip'],
    ['a filename* that cannot be decoded falls back to filename', 'attachment; filename*=UTF-8\'\'%zz.zip; filename="ok.zip"', 'ok.zip'],
    ['a filename* with invalid UTF-8 falls back to filename', 'attachment; filename*=UTF-8\'\'%FF%FE.zip; filename="ok.zip"', 'ok.zip'],
    ['a filename* with an unknown charset falls back to filename', 'attachment; filename*=x-nope\'\'a.zip; filename="ok.zip"', 'ok.zip'],
    ['nothing readable: the page names the file', "attachment; filename*=UTF-8''%zz.zip", `decimer-${JOB_ID}.zip`],
    ['an empty name: the page names the file', 'attachment; filename=""', `decimer-${JOB_ID}.zip`],
    ['no filename at all: the page names the file', 'attachment', `decimer-${JOB_ID}.zip`],
    // The name is saved on the user's disk: only a bare name comes out, whatever the server sent.
    ['a path in filename* keeps the last part (..%2F..%2Fx.zip)', "attachment; filename*=UTF-8''..%2F..%2Fx.zip", 'x.zip'],
    ['a Windows path in filename* keeps the last part (..%5C..%5Cx.zip)', "attachment; filename*=UTF-8''..%5C..%5Cx.zip", 'x.zip'],
    ['an absolute path in filename* keeps the last part', "attachment; filename*=UTF-8''%2Fetc%2Fcron.d%2Fx", 'x'],
    ['a path in a quoted filename keeps the last part', 'attachment; filename="../../x.zip"', 'x.zip'],
    ['an escaped Windows path in a quoted filename keeps the last part', 'attachment; filename="..\\\\..\\\\x.zip"', 'x.zip'],
    ['a backslash path in an unquoted filename keeps the last part', 'attachment; filename=C:\\dir\\x.zip', 'x.zip'],
    ['newlines and NULs are dropped (%0A, %0D, %00)', "attachment; filename*=UTF-8''a%0Ab%0D%00c.zip", 'abc.zip'],
    ['DEL and other C0 controls are dropped (%7F, %1B)', "attachment; filename*=UTF-8''a%7Fb%1Bc.zip", 'abc.zip'],
    ['a right-to-left override is dropped (U+202E: "evil\u202Efdp.zip" would read "evilpdf.zip")', "attachment; filename*=UTF-8''evil%E2%80%AEfdp.zip", 'evilfdp.zip'],
    ['the other bidi controls are dropped (U+202A, U+202D, U+2066, U+2069, U+200E, U+200F)', "attachment; filename*=UTF-8''a%E2%80%AAb%E2%80%ADc%E2%81%A6d%E2%81%A9e%E2%80%8Ef%E2%80%8Fg.zip", 'abcdefg.zip'],
    ['a path hidden behind a control character still loses its path', "attachment; filename*=UTF-8''..%2F%00..%2Fx.zip", 'x.zip'],
    ['only control characters: the page names the file', "attachment; filename*=UTF-8''%0A%00%E2%80%AE", `decimer-${JOB_ID}.zip`],
    ['a name that is only a directory: the page names the file', "attachment; filename*=UTF-8''dir%2F", `decimer-${JOB_ID}.zip`],
    ['a hidden file name: the page names the file', 'attachment; filename=".htaccess"', `decimer-${JOB_ID}.zip`],
    ['a dot-dot name: the page names the file', 'attachment; filename=".."', `decimer-${JOB_ID}.zip`],
    ['a hidden filename* falls back to filename', 'attachment; filename*=UTF-8\'\'.bashrc; filename="ok.zip"', 'ok.zip'],
    ['a filename* that is only controls falls back to filename', 'attachment; filename*=UTF-8\'\'%0A; filename="ok.zip"', 'ok.zip'],
    ['text after a dot is fine: only a leading dot hides a file', 'attachment; filename="report.v2.zip"', 'report.v2.zip'],
  ])('export file name: %s', async (_label, disposition, expected) => {
    const { api } = clientWith(() => new Response('x', { headers: { 'Content-Disposition': disposition ?? '' } }));
    await expect(api.exportJob(JOB_ID, { format: 'zip', molfiles: {} })).resolves.toMatchObject({ filename: expected });
  });

  test('export names the file itself when the server does not', async () => {
    const { api } = clientWith(() => new Response('x'));
    expect((await api.exportJob(JOB_ID, { format: 'sdf', molfiles: {} })).filename).toBe(`decimer-${JOB_ID}.sdf`);
    expect((await api.exportJob(JOB_ID, { format: 'csv', molfiles: {} })).filename).toBe(`decimer-${JOB_ID}.csv`);
  });

  test('validate sends nothing but the molfile, and a 429 or 413 is an ApiError with its code', async () => {
    const { api, fetchMock } = clientWith(() => json({ detail: { code: 'rate_limited', message: 'slow down' } }, { status: 429, headers: { 'Retry-After': '12' } }));
    await expect(api.validate(JOB_ID, 1, 'M')).rejects.toMatchObject({ status: 429, code: 'rate_limited', retryAfterMs: 12000 });
    expect(Object.keys(JSON.parse(call(fetchMock).init.body as string) as object)).toEqual(['molfile']);
    const big = clientWith(() => json({ detail: { code: 'too_large', message: 'big' } }, { status: 413 }));
    await expect(big.api.validate(JOB_ID, 1, 'M')).rejects.toMatchObject({ status: 413, code: 'too_large' });
  });

  test('report posts the result index', async () => {
    const { api, fetchMock } = clientWith(() => new Response(null, { status: 204 }));
    await expect(api.report(JOB_ID, 5)).resolves.toBeUndefined();
    const { url, init } = call(fetchMock);
    expect(url).toBe(`/api/jobs/${JOB_ID}/report`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ index: 5 });
    expect(Object.keys(JSON.parse(init.body as string) as object)).toEqual(['index']); // nothing else is sent: the server has the result
  });

  test('errors of these calls are mapped like the others', async () => {
    const { api } = clientWith(() => json({ detail: { code: 'not_found', message: 'Not found. The job may have expired.' } }, { status: 404 }));
    await expect(api.validate(JOB_ID, 0, 'M')).rejects.toMatchObject({ code: 'not_found' });
    await expect(api.exportJob(JOB_ID, { format: 'zip', molfiles: {} })).rejects.toMatchObject({ code: 'not_found' });
    await expect(api.report(JOB_ID, 0)).rejects.toMatchObject({ code: 'not_found' });
  });
});

test('the app polls every 2 s unless told otherwise', () => {
  expect(new HttpApiClient().pollIntervalMs).toBe(2000);
  expect(new HttpApiClient({ pollIntervalMs: 500 }).pollIntervalMs).toBe(500);
});
