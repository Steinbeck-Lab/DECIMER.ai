import type { Job, Result, ValidateResponse } from './contract';
import type { components } from './generated';

type WireJob = components['schemas']['JobView'];
type WireResult = components['schemas']['ResultView'];

/**
 * A job as the API sends it, as the UI's `Job`. The OpenAPI schema marks fields that have a default as optional;
 * the API sends them all, but a client should not fall over the day a server version leaves one out.
 *
 * `resolveUrl` turns the API's media URLs (`/api/jobs/{id}/media/crop-0.png`, `page-0.png`) into ones the page can load.
 */
export function parseJob(wire: WireJob, resolveUrl: (url: string) => string = (url) => url): Job {
  return {
    ...wire,
    queuePosition: wire.queuePosition ?? null,
    error: wire.error ?? null,
    warnings: wire.warnings ?? [],
    pageUrls: (wire.pageUrls ?? []).map(resolveUrl),
    results: (wire.results ?? []).map((result) => parseResult(result, resolveUrl)),
  };
}

function parseResult(wire: WireResult, resolveUrl: (url: string) => string): Result {
  return {
    ...wire,
    name: wire.name ?? null,
    nameTier: wire.nameTier ?? null,
    nameVerified: wire.nameVerified ?? null,
    // A server that does not say who accepted the SMILES (older than D4's CDK fallback): a valid result is RDKit's.
    validBy: wire.validBy ?? (wire.valid ? 'rdkit' : null),
    svg: wire.svg ?? '',
    molfile: wire.molfile ?? '',
    cropUrl: resolveUrl(wire.cropUrl),
  };
}

/** The answer of the validate call, with what the server leaves out filled in (as `parseJob` does for a result). */
export function parseValidate(wire: components['schemas']['ValidateView']): ValidateResponse {
  return {
    smiles: wire.smiles ?? '',
    inchikey: wire.inchikey ?? '',
    svg: wire.svg ?? '',
    valid: wire.valid ?? false,
    name: wire.name ?? null,
    nameTier: wire.nameTier ?? null,
    nameVerified: wire.nameVerified ?? null,
  };
}
