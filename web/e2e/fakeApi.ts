import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Page, Route } from '@playwright/test';

// The job API's endpoints, answered by the test (page.route), for the build with VITE_API=http. The bodies are
// what FastAPI sends: camelCase, optional fields left out, errors as {"detail": {"code", "message"}}.

const FIXTURES = resolve(import.meta.dirname, '../src/mocks/fixtures');
const compounds = JSON.parse(readFileSync(resolve(FIXTURES, 'compounds.json'), 'utf8')) as {
  number: number;
  smiles: string;
  inchikey: string;
  iupac: string | null;
  molfile: string;
}[];

export const JOB_ID = '0123456789abcdef0123456789abcdef';
const STAGES = ['upload', 'render', 'segment', 'recognise', 'check'] as const;

export function wireResult(index: number) {
  const c = compounds[index];
  if (!c) throw new Error(`no compound ${index}`);
  return {
    index,
    number: index + 1, // the real API numbers from 1, in order
    pageIndex: 0,
    bbox: [10, 20, 310, 220],
    smiles: c.smiles,
    inchikey: c.inchikey,
    valid: true,
    isStructure: true,
    confidence: 0.95,
    molfile: c.molfile,
    cropUrl: `/api/jobs/${JOB_ID}/media/crop-${index}.png`,
    name: c.iupac,
    nameTier: c.iupac ? 'pin_verified' : null,
    nameVerified: c.iupac ? 'opsin' : null,
    // `svg` is left out: the API sends '' until the worker draws one.
  };
}

export function wireJob(kind: 'queued' | 'running' | 'done', resultCount = 3, queuePosition = 2) {
  const active = kind === 'running' ? 2 : -1; // "segment"
  return {
    id: JOB_ID,
    status: kind,
    ...(kind === 'queued' ? { queuePosition } : {}),
    stages: STAGES.map((name, i) => ({
      name,
      state: kind === 'done' ? 'done' : kind === 'queued' ? (i === 0 ? 'done' : 'pending') : i < active ? 'done' : i === active ? 'active' : 'pending',
    })),
    pages: 1,
    pagesProcessed: 1,
    truncated: false,
    structuresFound: kind === 'done' ? resultCount : 0,
    structuresDropped: 0,
    ...(kind === 'done' ? { results: Array.from({ length: resultCount }, (_, i) => wireResult(i)) } : {}),
    createdAt: '2026-10-22T12:00:00Z',
    expiresAt: '2026-10-22T13:00:00Z',
  };
}

export const apiError = (status: number, code: string, message: string, headers: Record<string, string> = {}) => ({
  status,
  headers: { 'Content-Type': 'application/json', ...headers },
  body: JSON.stringify({ detail: { code, message } }),
});

export interface Seen {
  /** Every request to the API, in order. */
  requests: { method: string; path: string; at: number; contentType: string | undefined; body: string }[];
}

/**
 * Routes `/api/**` to `handler`, which answers each request or returns undefined for the default (a crop image
 * for media, 404 otherwise). Returns what was seen.
 */
export async function fakeApi(page: Page, handler: (route: Route, seen: Seen) => Promise<boolean | void> | boolean | void): Promise<Seen> {
  const seen: Seen = { requests: [] };
  const crop = readFileSync(resolve(FIXTURES, 'crop5.png'));
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    seen.requests.push({
      method: request.method(),
      path,
      at: Date.now(),
      contentType: request.headers()['content-type'],
      body: request.postDataBuffer()?.toString('latin1') ?? '',
    });
    if (await handler(route, seen)) return;
    if (path.includes('/media/')) return route.fulfill({ status: 200, contentType: 'image/png', body: crop });
    return route.fulfill(apiError(404, 'not_found', 'Not found. The job may have expired.'));
  });
  return seen;
}
