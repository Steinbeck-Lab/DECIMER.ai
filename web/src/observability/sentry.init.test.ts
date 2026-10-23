/**
 * The options `startErrorReporting` gives `Sentry.init`: `init` is a mock here, so each option is read as it is set.
 * (What the SDK then does with them is in sentry.test.ts, against the real SDK.)
 */
import * as Sentry from '@sentry/browser';
import type { BrowserOptions } from '@sentry/browser';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { resetErrorReportingForTests, startErrorReporting } from './sentry';
import { scrubEvent } from './scrub';

vi.mock('@sentry/browser', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@sentry/browser')>();
  return { ...actual, init: vi.fn(), close: vi.fn(async () => true) };
});

const LOCATION = { origin: 'https://decimer.ai' };
const DSN = 'https://publickey@decimer.ai/glitchtip/1';

function startedOptions(): BrowserOptions {
  expect(startErrorReporting({ dsn: DSN, location: LOCATION })).toBe(true);
  expect(Sentry.init).toHaveBeenCalledTimes(1);
  return vi.mocked(Sentry.init).mock.calls[0]![0]!;
}

beforeEach(async () => {
  vi.mocked(Sentry.init).mockReset();
  await resetErrorReportingForTests();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('the options of Sentry.init', () => {
  test('the DSN, the environment, and the scrubber as beforeSend', () => {
    const options = startedOptions();
    expect(options.dsn).toBe(DSN);
    expect(options.environment).toBe('test'); // import.meta.env.MODE: "production" in a build
    expect(options.beforeSend).toBe(scrubEvent);
    expect(options).not.toHaveProperty('release'); // no VITE_RELEASE
  });

  test('the release is VITE_RELEASE, when there is one', () => {
    vi.stubEnv('VITE_RELEASE', 'a1b2c3d');
    expect(startedOptions().release).toBe('a1b2c3d');
  });

  test('no default integrations; the explicit list is the global handlers, dedupe and linked errors, and nothing else', () => {
    const options = startedOptions();
    expect(options.defaultIntegrations).toBe(false);
    expect(Array.isArray(options.integrations)).toBe(true);
    const names = (options.integrations as { name: string }[]).map((integration) => integration.name);
    expect(names).toEqual(['GlobalHandlers', 'Dedupe', 'LinkedErrors']);
    // none of the ones that watch the page, the network or the user
    for (const forbidden of [
      'Breadcrumbs', 'Console', 'BrowserApiErrors', 'HttpContext', 'BrowserSession', 'BrowserTracing', 'Replay', 'ReplayCanvas',
      'Feedback', 'HttpClient', 'ContextLines', 'CultureContext', 'ConversationId', 'WebVitals',
    ]) {
      expect(names).not.toContain(forbidden);
    }
  });

  test('no personal data: every part of dataCollection is off (the 11.x SDK has no sendDefaultPii, and its defaults collect)', () => {
    const options = startedOptions();
    expect(options).not.toHaveProperty('sendDefaultPii', true);
    expect(options.dataCollection).toEqual({
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      stackFrameVariables: false,
      frameContextLines: 0,
      graphQL: { document: false, variables: false },
      genAI: { inputs: false, outputs: false },
      databaseQueryData: false,
      queues: false,
    });
  });

  test('no breadcrumbs, no client reports, no tracing, no replay, no logs', () => {
    const options = startedOptions() as Record<string, unknown>;
    expect(options.maxBreadcrumbs).toBe(0);
    expect((options.beforeBreadcrumb as (crumb: object) => unknown)({ message: 'a click on CCO' })).toBeNull();
    expect(options.sendClientReports).toBe(false);
    expect(options.attachStacktrace).toBe(true);
    for (const tracing of ['tracesSampleRate', 'tracesSampler', 'tracePropagationTargets', 'profilesSampleRate']) {
      expect(options[tracing], tracing).toBeUndefined();
    }
    for (const other of ['replaysSessionSampleRate', 'replaysOnErrorSampleRate', 'enableLogs', 'tunnel', 'spotlight', 'debug']) {
      expect(options[other], other).toBeUndefined();
    }
  });

  test('the report is sent without the site\'s cookies, and the app\'s error text is left alone', () => {
    const options = startedOptions();
    expect(options.transportOptions).toEqual({ fetchOptions: { credentials: 'omit' } });
    expect(options.enhanceFetchErrorMessages).toBe(false);
  });

  test('a transport given by the caller (tests) is passed on', () => {
    const transport = vi.fn();
    expect(startErrorReporting({ dsn: DSN, location: LOCATION, transport: transport as never })).toBe(true);
    expect(vi.mocked(Sentry.init).mock.calls[0]![0]!.transport).toBe(transport);
  });

  test('none is invented: the SDK\'s own fetch transport is used', () => {
    expect(Object.keys(startedOptions())).not.toContain('transport');
  });
});

describe('when the SDK cannot start', () => {
  test('init that throws is a warning, not an error of the page', () => {
    vi.mocked(Sentry.init).mockImplementationOnce(() => {
      throw new Error('boom');
    });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(startErrorReporting({ dsn: DSN, location: LOCATION })).toBe(false);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(startErrorReporting({ dsn: DSN, location: LOCATION })).toBe(true); // and it may try again
  });

  test('no DSN: init is not called', () => {
    expect(startErrorReporting({ location: LOCATION })).toBe(false);
    expect(Sentry.init).not.toHaveBeenCalled();
  });

  test('the DSN of the build (VITE_SENTRY_DSN) is the one used when none is passed', () => {
    vi.stubEnv('VITE_SENTRY_DSN', DSN);
    expect(startErrorReporting({ location: LOCATION })).toBe(true);
    expect(vi.mocked(Sentry.init).mock.calls[0]![0]!.dsn).toBe(DSN);
  });
});
