/**
 * The error reports with the REAL Sentry SDK and a fake transport: what the SDK hands to the transport is what would
 * leave the browser. (The options passed to `init` are checked in sentry.init.test.ts, where `init` is a mock.)
 */
import type { BrowserOptions } from '@sentry/browser';
import * as Sentry from '@sentry/browser';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { resetErrorReportingForTests, startErrorReporting } from './sentry';

type TransportFactory = NonNullable<BrowserOptions['transport']>;
type TransportOptions = Parameters<TransportFactory>[0];

const ORIGIN = 'http://127.0.0.1:8090';
const DSN = 'http://k@127.0.0.1:8090/glitchtip/1';
const LOCATION = { origin: ORIGIN };

const SMILES = 'CC(=O)Oc1ccccc1C(=O)O';
const ADDRESS = '203.0.113.7';
const UPLOAD = 'Zou et al 2024 final.pdf';
const SCAN = 'structure_scan.PNG';
const SECRETS = [SMILES, ADDRESS, UPLOAD, SCAN, 'u-4711', 'someone@example.org', 'SECRET-EXTRA', 'SECRET-CONTEXT', 'SECRET-CRUMB'];

/** A transport that keeps what it is given, and the options it was made with. */
function fakeTransport() {
  const envelopes: unknown[] = [];
  const made: TransportOptions[] = [];
  const transport: TransportFactory = (options) => {
    made.push(options);
    return {
      send: async (envelope) => {
        envelopes.push(envelope);
        return {};
      },
      flush: async () => true,
    };
  };
  /** The `[header, payload]` of every item in every envelope. */
  type Item = [Record<string, unknown>, Record<string, any>]; // eslint-disable-line @typescript-eslint/no-explicit-any
  const items = () => envelopes.flatMap((envelope) => (envelope as [unknown, Item[]])[1]);
  return { transport, envelopes, made, items, events: () => items().filter(([header]) => header.type === 'event').map(([, event]) => event) };
}

let warn: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(async () => {
  await resetErrorReportingForTests();
  Sentry.getCurrentScope().setClient(undefined);
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('off', () => {
  test('with no DSN nothing starts and nothing is said', () => {
    const { transport, made } = fakeTransport();
    expect(startErrorReporting({ location: LOCATION, transport })).toBe(false); // VITE_SENTRY_DSN is unset in the tests
    expect(startErrorReporting({ dsn: '', location: LOCATION, transport })).toBe(false);
    expect(startErrorReporting({ dsn: '   ', location: LOCATION, transport })).toBe(false);
    expect(Sentry.isInitialized()).toBe(false);
    expect(made).toHaveLength(0);
    expect(warn).not.toHaveBeenCalled();
  });

  test('an empty VITE_SENTRY_DSN is off too', () => {
    vi.stubEnv('VITE_SENTRY_DSN', '');
    expect(startErrorReporting({ location: LOCATION })).toBe(false);
    expect(Sentry.isInitialized()).toBe(false);
  });
});

describe('a DSN that is refused: a console warning with the reason, no SDK, and the page goes on', () => {
  test.each([
    ['another host', 'https://k@sentry.example.org/glitchtip/1', /sentry\.example\.org.*127\.0\.0\.1:8090.*connect-src 'self'/s],
    ['another port', 'http://k@127.0.0.1:9000/glitchtip/1', /127\.0\.0\.1:9000/],
    ['another scheme', 'https://k@127.0.0.1:8090/glitchtip/1', /https:\/\/127\.0\.0\.1:8090/],
    ['a sibling host', 'http://k@glitchtip.127.0.0.1.nip.io:8090/glitchtip/1', /nip\.io/],
    ['not a URL', 'not a dsn', /not a URL/],
    ['no public key', 'http://127.0.0.1:8090/glitchtip/1', /public key/],
    ['no project id', 'http://k@127.0.0.1:8090/glitchtip/abc', /project id/],
    ['a scheme that is not http', 'ftp://k@127.0.0.1:8090/glitchtip/1', /scheme/],
  ])('%s', (_name, dsn, reason) => {
    const { transport, made } = fakeTransport();
    expect(startErrorReporting({ dsn, location: LOCATION, transport })).toBe(false);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]![0])).toMatch(reason);
    expect(Sentry.isInitialized()).toBe(false);
    expect(made).toHaveLength(0);
    Sentry.captureException(new Error('nobody listens')); // no client: nothing to send, nothing thrown
  });

  test('a page with no location cannot tell, so it refuses too; a refusal does not stop a later good start', () => {
    const { transport, made } = fakeTransport();
    expect(startErrorReporting({ dsn: DSN, location: { origin: undefined as unknown as string }, transport })).toBe(false);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(made).toHaveLength(0);
    expect(startErrorReporting({ dsn: DSN, location: LOCATION, transport })).toBe(true);
    expect(made).toHaveLength(1);
  });

  test('the default origin is the page\'s own (VITE_SENTRY_DSN of the build against window.location)', () => {
    const { transport, made } = fakeTransport();
    vi.stubEnv('VITE_SENTRY_DSN', 'https://k@elsewhere.example/glitchtip/1');
    expect(startErrorReporting({ transport })).toBe(false);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]![0])).toContain(window.location.origin);
    vi.stubEnv('VITE_SENTRY_DSN', `${window.location.origin.replace('://', '://k@')}/glitchtip/1`);
    expect(startErrorReporting({ transport })).toBe(true);
    expect(made).toHaveLength(1);
  });
});

describe('a DSN on the page\'s own origin starts the SDK, and the reports go to the edge\'s /glitchtip/ path', () => {
  test('a DSN with a path prefix is posted to <origin><prefix>/api/<project id>/envelope/ (the SDK\'s own URL)', () => {
    const { transport, made } = fakeTransport();
    expect(startErrorReporting({ dsn: DSN, location: LOCATION, transport })).toBe(true);
    expect(warn).not.toHaveBeenCalled();
    expect(made).toHaveLength(1);
    const url = new URL(made[0]!.url);
    expect(url.origin).toBe(ORIGIN); // the page's own origin: the CSP's connect-src 'self' covers it
    expect(url.pathname).toBe('/glitchtip/api/1/envelope/');
    expect(url.searchParams.get('sentry_key')).toBe('k');
  });

  test.each([
    ['http://k@127.0.0.1:8090/glitchtip/7', '/glitchtip/api/7/envelope/'],
    ['http://k@127.0.0.1:8090/a/b/12', '/a/b/api/12/envelope/'],
    ['http://k@127.0.0.1:8090/3', '/api/3/envelope/'],
  ])('%s -> %s', (dsn, path) => {
    const { transport, made } = fakeTransport();
    expect(startErrorReporting({ dsn, location: LOCATION, transport })).toBe(true);
    expect(new URL(made[0]!.url).pathname).toBe(path);
  });

  test('over the SDK\'s real fetch transport: one POST to that URL, without cookies, as an envelope', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    expect(startErrorReporting({ dsn: DSN, location: LOCATION })).toBe(true);
    Sentry.captureException(new Error('over the wire'));
    await Sentry.flush(2000);
    vi.unstubAllGlobals();
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(new URL(url).origin + new URL(url).pathname).toBe(`${ORIGIN}/glitchtip/api/1/envelope/`);
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('omit');
    expect(String(init.body)).toContain('over the wire');
  });

  test('it starts once, and again after a reset', async () => {
    const { transport, made } = fakeTransport();
    expect(startErrorReporting({ dsn: DSN, location: LOCATION, transport })).toBe(true);
    expect(startErrorReporting({ dsn: DSN, location: LOCATION, transport })).toBe(false);
    expect(made).toHaveLength(1);
    await resetErrorReportingForTests();
    expect(startErrorReporting({ dsn: DSN, location: LOCATION, transport })).toBe(true);
    expect(made).toHaveLength(2);
  });

  test('the SDK that starts is the cut-down one: three integrations, no sessions, no tracing', () => {
    const { transport } = fakeTransport();
    startErrorReporting({ dsn: DSN, location: LOCATION, transport });
    const client = Sentry.getClient()!;
    expect(client.getOptions().integrations.map((integration) => integration.name)).toEqual(['GlobalHandlers', 'Dedupe', 'LinkedErrors']);
    expect(client.getOptions().tracesSampleRate).toBeUndefined();
    expect(Sentry.getIsolationScope().getSession()).toBeUndefined();
  });
});

describe('what reaches the transport', () => {
  function start(extra: { release?: string } = {}) {
    if (extra.release) vi.stubEnv('VITE_RELEASE', extra.release);
    const sink = fakeTransport();
    expect(startErrorReporting({ dsn: DSN, location: LOCATION, transport: sink.transport })).toBe(true);
    return sink;
  }

  test('an error with a structure, an address and a file name in it, in every place the SDK takes them from', async () => {
    const { events, items, envelopes } = start();
    // (an isolation scope of its own: what the test sets on the scope is gone with it)
    Sentry.withIsolationScope(() => {
      Sentry.setUser({ id: 'u-4711', email: 'someone@example.org', ip_address: ADDRESS });
      Sentry.setExtra('structure', SMILES);
      Sentry.setExtras({ file: SCAN, note: 'SECRET-EXTRA' });
      Sentry.setTag('upload', `'${UPLOAD}'`); // (a name with spaces is found by its quotes)
      Sentry.setTag('from', ADDRESS);
      Sentry.setContext('structure', { smiles: SMILES, note: 'SECRET-CONTEXT' });
      Sentry.addBreadcrumb({ category: 'ui', message: `opened ${SCAN} SECRET-CRUMB`, data: { smiles: SMILES } });
      const error = new TypeError(`cannot parse '${SMILES}' in '${UPLOAD}' from ${ADDRESS}`);
      Sentry.captureException(error, {
        extra: { structure: SMILES },
        tags: { file: SCAN },
        user: { id: 'u-4711' },
        contexts: { structure: { smiles: SMILES } },
      });
      Sentry.captureMessage(`message about ${SMILES} (${SCAN}) at ${ADDRESS}`, { level: 'warning', extra: { structure: SMILES } });
    });
    await Sentry.flush(2000);

    expect(events()).toHaveLength(2);
    const json = JSON.stringify(envelopes);
    for (const secret of SECRETS) expect(json, secret).not.toContain(secret);

    const [crash, message] = events();
    expect(crash!.exception.values[0].type).toBe('TypeError');
    expect(crash!.exception.values[0].value).toBe("cannot parse '[smiles]' in [file] from [ip]");
    expect(crash!.exception.values[0].stacktrace.frames.length).toBeGreaterThan(0);
    expect(crash!.exception.values[0].stacktrace.frames.at(-1).function).toBeDefined();
    expect(crash!.tags).toEqual({ upload: '[file]', from: '[ip]', file: '[file]' });
    expect(crash!.level).toBe('error');
    expect(crash!.environment).toBe('test');
    expect(crash!.platform).toBe('javascript');
    expect(crash!.sdk.name).toBe('sentry.javascript.browser');
    expect(crash!.sdk.settings).toEqual({ infer_ip: 'never' }); // the server is told not to take the address from the connection
    expect(message!.level).toBe('warning');
    expect(message!.message).toBe('message about [smiles] [file]) at [ip]');
    for (const event of [crash!, message!]) {
      expect(Object.keys(event).filter((key) => ['user', 'extra', 'breadcrumbs', 'request', 'server_name', 'modules'].includes(key))).toEqual([]);
      expect(Object.keys(event.contexts ?? {})).toEqual([]);
    }
    // only events: no session, no client report, no trace header
    expect(items().map(([header]) => header.type)).toEqual(['event', 'event']);
    for (const envelope of envelopes) expect(Object.keys((envelope as [Record<string, unknown>])[0]).sort()).toEqual(['event_id', 'sdk', 'sent_at']);
  });

  test('the probes of the privacy review: a SMILES glued to a quote, a key, a bar and a query is gone from the report', async () => {
    const { events, envelopes } = start();
    Sentry.captureException(new Error("cannot parse 'c1ccccc1?' for id=CCO (CCO|CCN) at /api/x?smiles=CCO"));
    await Sentry.flush(2000);
    expect(events()[0]!.exception.values[0].value).toBe(`cannot parse '[smiles]?' for id=[smiles] ([smiles]|[smiles] at /api/x`);
    const json = JSON.stringify(envelopes);
    for (const secret of ['CCO', 'CCN', 'c1ccccc1']) expect(json, secret).not.toContain(secret);
  });

  test('the release is the one the build names, when it names one', async () => {
    const { events } = start({ release: 'abc123' });
    Sentry.captureException(new Error('x'));
    await Sentry.flush(2000);
    expect(events()[0]!.release).toBe('abc123');
  });

  test('no release is invented', async () => {
    const { events } = start();
    Sentry.captureException(new Error('x'));
    await Sentry.flush(2000);
    expect(events()[0]!.release).toBeUndefined();
  });

  test('an uncaught error of the page is reported (the global handler), and so is a rejected promise, both scrubbed', async () => {
    const { events } = start();
    const error = new RangeError(`bad ${SMILES} in ${SCAN}`);
    // The SDK's handlers are `window.onerror` and `window.onunhandledrejection`: called as the browser calls them (a
    // dispatched event would be an uncaught exception to the test runner as well).
    window.onerror!(error.message, `${ORIGIN}/assets/index-abc.js`, 10, 5, error);
    const rejection = new Event('unhandledrejection') as Event & { reason?: unknown };
    rejection.reason = new Error(`rejected ${SMILES} from ${ADDRESS}`);
    window.onunhandledrejection!(rejection as unknown as PromiseRejectionEvent);
    await Sentry.flush(2000);

    const reported = events().map((event) => event.exception.values[0]);
    expect(reported.map((value) => value.type)).toEqual(['RangeError', 'Error']);
    expect(reported.map((value) => value.value)).toEqual(['bad [smiles] in [file]', 'rejected [smiles] from [ip]']);
    expect(reported.map((value) => value.mechanism)).toEqual([
      { type: 'auto.browser.global_handlers.onerror', handled: false },
      { type: 'auto.browser.global_handlers.onunhandledrejection', handled: false },
    ]);
    const json = JSON.stringify(events());
    for (const secret of [SMILES, SCAN, ADDRESS]) expect(json).not.toContain(secret);
  });

  test('the same error twice in a row is reported once (dedupe), and a cause is kept as a linked error', async () => {
    const { events } = start();
    const error = new Error('same', { cause: new Error(`the cause ${SMILES}`) });
    Sentry.captureException(error);
    Sentry.captureException(error);
    await Sentry.flush(2000);
    expect(events()).toHaveLength(1);
    const values = events()[0]!.exception.values;
    expect(values.map((value: { value: string }) => value.value)).toEqual(['the cause [smiles]', 'same']);
  });

  test('the SDK keeps no breadcrumbs and hooks nothing but the two global handlers', async () => {
    const hooked = () => [
      console.log, console.error, window.fetch, XMLHttpRequest.prototype.open, XMLHttpRequest.prototype.send,
      history.pushState, history.replaceState, EventTarget.prototype.addEventListener, window.setTimeout, window.requestAnimationFrame,
    ];
    const before = hooked();
    const { events } = start();
    hooked().forEach((hook, index) => expect(hook, String(index)).toBe(before[index]));
    Sentry.addBreadcrumb({ message: 'x' });
    Sentry.captureException(new Error('y'));
    await Sentry.flush(2000);
    expect(Sentry.getIsolationScope().getScopeData().breadcrumbs).toEqual([]);
    expect(events()[0]!.breadcrumbs).toBeUndefined();
  });
});
