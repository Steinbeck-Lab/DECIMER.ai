import type { ApiClient } from '../api/client';
import { MockApiClient, MOCK_DEFAULT_STEP_MS } from './MockApiClient';

/** A positive number from the query string, or undefined. */
function queryNumber(name: string): number | undefined {
  const raw = new URLSearchParams(globalThis.location?.search ?? '').get(name);
  const value = raw ? Number(raw) : NaN;
  return Number.isFinite(value) && value > 0 ? value : undefined;
}

/**
 * The mock for the page. Two knobs for tests and screenshots, in the query string:
 * `?mockStepMs=200` (length of a queue position or a stage) and `?mockLatencyMs=1500`
 * (network delay of upload, validate, export and report).
 */
export function createMockApiClient(): ApiClient {
  return new MockApiClient({
    stepMs: queryNumber('mockStepMs') ?? MOCK_DEFAULT_STEP_MS,
    latencyMs: queryNumber('mockLatencyMs'),
  });
}
