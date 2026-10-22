import type { ApiClient } from './client';

/**
 * The backend the app talks to, chosen at build time by `VITE_API`. The mock is
 * loaded with a dynamic import behind that flag, so a build with another value
 * carries no mock code and no fixtures. There is no HTTP client yet (Phase 3),
 * so `mock` is the default. TODO: add `http` and make it the default.
 */
export async function createApiClient(): Promise<ApiClient> {
  const backend = import.meta.env.VITE_API ?? 'mock';
  if (backend === 'mock') {
    const { createMockApiClient } = await import('../mocks');
    return createMockApiClient();
  }
  throw new Error(`VITE_API=${backend}: there is no HTTP client yet`);
}
