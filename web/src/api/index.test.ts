import { afterEach, expect, test, vi } from 'vitest';
import { HttpApiClient } from './http';
import { createApiClient } from './index';

afterEach(() => vi.unstubAllEnvs());

test('VITE_API=http selects the HTTP client; a base URL on the page\'s own origin is used in front of every path', async () => {
  vi.stubEnv('VITE_API', 'http');
  vi.stubEnv('VITE_API_URL', `${window.location.origin}/gateway`);
  const api = await createApiClient();
  expect(api).toBeInstanceOf(HttpApiClient);
  expect(api.pollIntervalMs).toBe(2000);
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ id: 'x' }), { status: 202 }));
  vi.stubGlobal('fetch', fetchMock);
  await api.createJob([new File(['x'], 'a.png')]);
  expect(fetchMock.mock.calls[0]?.[0]).toBe(`${window.location.origin}/gateway/api/jobs`);
  vi.unstubAllGlobals();
});

test('no VITE_API_URL, or a path only, means the page\'s own origin', async () => {
  vi.stubEnv('VITE_API', 'http');
  for (const url of ['', '/beta']) {
    vi.stubEnv('VITE_API_URL', url);
    expect(await createApiClient()).toBeInstanceOf(HttpApiClient);
  }
});

// The CSP is connect-src 'self' and img-src 'self' (vite.config.ts, deploy/nginx/security-headers.conf): another
// origin would have every request and every crop blocked, with nothing on the page to say why.
test('a VITE_API_URL on another origin is an error at start-up that names both origins and the CSP', async () => {
  vi.stubEnv('VITE_API', 'http');
  for (const url of ['https://decimer.example', 'https://decimer.example/api-prefix', `${window.location.protocol}//${window.location.hostname}:1`, '//other.example']) {
    vi.stubEnv('VITE_API_URL', url);
    const failure = createApiClient();
    await expect(failure).rejects.toThrow(/VITE_API_URL/);
    await expect(failure).rejects.toThrow(window.location.origin);
    await expect(failure).rejects.toThrow(/Content-Security-Policy/);
  }
});

test('a VITE_API_URL that is not a URL at all is an error at start-up too', async () => {
  vi.stubEnv('VITE_API', 'http');
  vi.stubEnv('VITE_API_URL', 'http://');
  await expect(createApiClient()).rejects.toThrow(/VITE_API_URL/);
});

test('the mock ignores VITE_API_URL: there is no API to be on another origin', async () => {
  vi.stubEnv('VITE_API', 'mock');
  vi.stubEnv('VITE_API_URL', 'https://decimer.example');
  await expect(createApiClient()).resolves.toBeDefined();
});

test('without VITE_API the mock is used (until the HTTP client is the default, P3-12)', async () => {
  vi.stubEnv('VITE_API', '');
  const api = await createApiClient();
  expect(api).not.toBeInstanceOf(HttpApiClient);
  expect(api.pollIntervalMs).toBeLessThan(2000);
});

test('an unknown value is an error at start-up, not a silent fallback to the mock', async () => {
  vi.stubEnv('VITE_API', 'carrier-pigeon');
  await expect(createApiClient()).rejects.toThrow(/VITE_API=carrier-pigeon/);
});
