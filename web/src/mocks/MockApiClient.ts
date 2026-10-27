import type { ApiClient } from '../api/client';
import {
  ApiError,
  LIMITS,
  type CreateJobResponse,
  type ExportRequest,
  type ExportResponse,
  type Job,
  type Result,
  type ValidateResponse,
} from '../api/contract';
import { asCdkOnly, asIncomplete, fixtureResults } from './fixtures';
import { jobAt, QUEUE_STEPS, RUN_STEPS, type MockJobSpec } from './timeline';

export interface MockOptions {
  /** Length of one step (a queue position or a pipeline stage), ms. */
  stepMs?: number;
  /** Poll interval the app should use, ms. */
  pollIntervalMs?: number;
  now?: () => number;
  /** Fake network latency of the non-polling calls, ms. */
  latencyMs?: number;
}

export const MOCK_DEFAULT_STEP_MS = 700;

interface MockJob {
  spec: MockJobSpec;
  createdAt: number;
}

const isPdfName = (file: Pick<File, 'name'>) => file.name.toLowerCase().endsWith('.pdf');

/**
 * What the mock does for a given upload. The file name picks a scenario, so
 * every state of the UI can be reached in the browser and in e2e tests:
 *
 * - `empty`      no structures detected
 * - `twenty`     20 structures
 * - `truncated`  a 14-page PDF with 23 structures (10 pages, 20 structures kept)
 * - `fail`       the job fails
 * - `busy`       the upload is refused with 429
 * - `full`       the upload is refused with 503
 * - `corrupt`    refused with 400 corrupt_file; likewise `encrypted` (encrypted_pdf), `pixels` (too_many_pixels)
 * - `badmolfile` the first structure has a molfile the editor cannot load
 * - `cdkonly`    the third structure was refused by RDKit and accepted by CDK alone (`valid` false, `validBy` cdk)
 * - `incomplete` the third structure was cut off at the model's token limit (`incomplete` true, no name; D25)
 * - `nocls`      the classifier was unavailable: `isStructure` is null everywhere and a warning says so
 * - anything else: the 8 structures of Figure 2 of the golden paper
 */
export function scenarioFor(files: readonly Pick<File, 'name'>[]): {
  refuse?: ApiError;
  spec: Omit<MockJobSpec, 'id'>;
} {
  const names = files.map((f) => f.name.toLowerCase()).join(' ');
  const isPdf = files.some(isPdfName);
  const pages = isPdf ? LIMITS.maxPages : files.length;

  if (names.includes('corrupt')) {
    return { refuse: new ApiError(400, 'corrupt_file', `${files[0]?.name ?? 'The file'} could not be read as a PDF document.`), spec: emptySpec(pages) };
  }
  if (names.includes('encrypted')) {
    return { refuse: new ApiError(400, 'encrypted_pdf', `${files[0]?.name ?? 'The file'} is password-protected.`), spec: emptySpec(pages) };
  }
  if (names.includes('pixels')) {
    return { refuse: new ApiError(400, 'too_many_pixels', `${files[0]?.name ?? 'The image'} has more than 50 megapixels.`), spec: emptySpec(pages) };
  }
  if (names.includes('nocls')) {
    const results = fixtureResults(8, 'paper', { classifier: 'unavailable' });
    return {
      spec: {
        pages,
        results,
        structuresFound: results.length,
        truncated: false,
        warnings: [
          {
            code: 'classifier_unavailable',
            message: 'The classifier could not be reached, so it was not checked whether the images are chemical structures.',
          },
        ],
      },
    };
  }
  if (names.includes('cdkonly')) {
    const results = fixtureResults(8).map((r, i) => (i === 2 ? asCdkOnly(r) : r));
    return { spec: { pages, results, structuresFound: results.length, truncated: false } };
  }
  if (names.includes('incomplete')) {
    const results = fixtureResults(8).map((r, i) => (i === 2 ? asIncomplete(r) : r));
    return { spec: { pages, results, structuresFound: results.length, truncated: false } };
  }
  if (names.includes('badmolfile')) {
    const results = fixtureResults(8).map((r, i) => (i === 0 ? { ...r, molfile: 'this is not a molfile' } : r));
    return { spec: { pages, results, structuresFound: results.length, truncated: false } };
  }
  if (names.includes('busy')) return { refuse: new ApiError(429, 'rate_limited'), spec: emptySpec(pages) };
  if (names.includes('full')) return { refuse: new ApiError(503, 'queue_full'), spec: emptySpec(pages) };
  if (names.includes('fail')) {
    return { spec: { ...emptySpec(pages), failure: 'The pipeline stopped while recognising structures.' } };
  }
  if (names.includes('empty')) return { spec: emptySpec(pages) };
  if (names.includes('truncated')) {
    return {
      spec: {
        pages: 14,
        pdf: true,
        results: fixtureResults(LIMITS.maxStructures, 'sequential'),
        structuresFound: 23,
        truncated: true,
        warnings: [
          { code: 'pages_truncated', message: 'The document has 14 pages. Only the first 10 were processed.' },
          // The API sends this one as well; the UI words the cap once, from the count (state/notices.ts).
          { code: 'structures_truncated', message: '23 structures were found. Only the first 20 were processed.' },
        ],
      },
    };
  }
  if (names.includes('twenty')) {
    return {
      spec: {
        pages,
        results: fixtureResults(LIMITS.maxStructures, 'sequential'),
        structuresFound: LIMITS.maxStructures,
        truncated: false,
      },
    };
  }
  const results = fixtureResults(8);
  return { spec: { pages, results, structuresFound: results.length, truncated: false } };
}

function emptySpec(pages: number): Omit<MockJobSpec, 'id'> {
  return { pages, results: [], structuresFound: 0, truncated: false };
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Atoms in a molfile, from its counts line (V2000 or V3000); null when it does not look like one. */
export function atomCount(molfile: string): number | null {
  const v3000 = molfile.match(/^M {2}V30 COUNTS (\d+)/m);
  if (v3000?.[1]) return Number(v3000[1]);
  const counts = molfile.split('\n')[3];
  const v2000 = counts?.match(/^\s*(\d+)\s+\d+.*V2000/);
  return v2000?.[1] ? Number(v2000[1]) : null;
}

/** The CSV's `incomplete` column and the SDF's `INCOMPLETE` item. An edited structure is the editor's, never the cut-off text. */
function isIncomplete(r: Pick<Result, 'incomplete'>, edited: boolean): boolean {
  return r.incomplete && !edited;
}

/** The CSV's `valid` column, as the API words it: `true` (RDKit accepts it), `cdk` (RDKit refused it, CDK read it) or `false`. */
function validity(r: Pick<Result, 'valid' | 'validBy'>): string {
  if (r.valid) return 'true';
  return r.validBy === 'cdk' ? 'cdk' : 'false';
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

/**
 * A fake backend that replays a job through the stages over a few seconds. It
 * stands in for the real API until Phase 3; the interface is
 * `ApiClient`, so swapping it is one line in `createApiClient`.
 */
export class MockApiClient implements ApiClient {
  readonly pollIntervalMs: number;
  private readonly stepMs: number;
  private readonly now: () => number;
  private readonly latencyMs: number;
  private readonly jobs = new Map<string, MockJob>();
  private counter = 0;

  constructor(options: MockOptions = {}) {
    this.stepMs = options.stepMs ?? MOCK_DEFAULT_STEP_MS;
    this.pollIntervalMs = options.pollIntervalMs ?? Math.max(50, Math.round(this.stepMs / 2));
    this.now = options.now ?? (() => Date.now());
    this.latencyMs = options.latencyMs ?? 150;
  }

  /** Total time from upload to done for a normal job, ms. */
  get durationMs(): number {
    return (QUEUE_STEPS + RUN_STEPS) * this.stepMs;
  }

  async createJob(files: File[], signal?: AbortSignal): Promise<CreateJobResponse> {
    await delay(this.latencyMs, signal);
    const { refuse, spec } = scenarioFor(files);
    if (refuse) throw refuse;
    const id = `mock-${++this.counter}`;
    const createdAt = this.now();
    this.jobs.set(id, {
      spec: {
        pdf: files.some(isPdfName),
        ...spec,
        id,
        createdAt: new Date(createdAt).toISOString(),
        expiresAt: new Date(createdAt + 60 * 60 * 1000).toISOString(),
      },
      createdAt,
    });
    return { id };
  }

  async getJob(id: string): Promise<Job> {
    const job = this.jobs.get(id);
    if (!job) throw new ApiError(404, 'not_found', `no job ${id}`);
    return structuredClone(jobAt(job.spec, this.now() - job.createdAt, this.stepMs));
  }

  async deleteJob(id: string): Promise<void> {
    if (!this.jobs.delete(id)) throw new ApiError(404, 'not_found', `no job ${id}`);
  }

  async validate(jobId: string, index: number, molfile: string, signal?: AbortSignal): Promise<ValidateResponse> {
    await delay(this.latencyMs, signal);
    const result = this.jobs.get(jobId)?.spec.results.find((r) => r.index === index);
    if (!result) throw new ApiError(404, 'not_found');
    if (!molfile.trim()) throw new ApiError(422, 'invalid_request');
    // The mock cannot run RDKit. A molfile with no atoms is not a structure (the real RDKit would say
    // the same), which lets a test reach the "invalid after an edit" state; anything else comes back as
    // valid with the recognised SMILES. The name is recomputed after an edit and the mock's Orthonym
    // declines, like the real one often does.
    if (atomCount(molfile) === 0) {
      return { smiles: '', inchikey: '', svg: '', valid: false, name: null, nameTier: null, nameVerified: null };
    }
    return {
      smiles: result.smiles,
      inchikey: result.inchikey,
      svg: result.svg,
      valid: true,
      name: null,
      nameTier: null,
      nameVerified: null,
    };
  }

  async exportJob(jobId: string, request: ExportRequest, signal?: AbortSignal): Promise<ExportResponse> {
    await delay(this.latencyMs, signal);
    const job = this.jobs.get(jobId);
    if (!job) throw new ApiError(404, 'not_found');
    // Result order comes from the job, never from the request.
    const rows = job.spec.results.map((r) => {
      const edited = request.molfiles[r.index];
      return { r, molfile: edited ?? r.molfile, edited: edited !== undefined };
    });

    if (request.format === 'sdf') {
      const sdf = rows
        .map(
          ({ r, molfile, edited }) =>
            `${molfile.trimEnd()}\n> <NUMBER>\n${r.number}\n\n> <SMILES>\n${r.smiles}\n\n> <NAME>\n${r.name ?? ''}\n\n> <EDITED>\n${edited}\n\n${validity(r) === 'cdk' ? '> <VALID>\ncdk\n\n' : ''}${isIncomplete(r, edited) ? '> <INCOMPLETE>\ntrue\n\n' : ''}$$$$\n`,
        )
        .join('');
      return { blob: new Blob([sdf], { type: 'chemical/x-mdl-sdfile' }), filename: `decimer-${jobId}.sdf` };
    }
    if (request.format === 'csv') {
      const lines = ['number,smiles,inchikey,valid,name,edited,incomplete'];
      for (const { r, molfile, edited } of rows) {
        // An edit is judged by RDKit alone (the mock's validate says valid), so an edited row is never `cdk`.
        const valid = edited ? (atomCount(molfile) === 0 ? 'false' : 'true') : validity(r);
        lines.push([r.number, csvCell(r.smiles), r.inchikey, valid, csvCell(r.name ?? ''), edited, isIncomplete(r, edited)].join(','));
      }
      return { blob: new Blob([`${lines.join('\n')}\n`], { type: 'text/csv' }), filename: `decimer-${jobId}.csv` };
    }
    const text = [
      'MOCK ZIP: the real API returns a ZIP archive of crops, molfiles and a SMILES list.',
      `job: ${jobId}`,
      ...rows.map(
        ({ r, molfile, edited }) =>
          `\n== structure ${r.number} (${edited ? 'edited' : 'original'}) ==\n${r.smiles}\t${r.name ?? ''}\n${molfile.trimEnd()}`,
      ),
      '',
    ].join('\n');
    return { blob: new Blob([text], { type: 'text/plain' }), filename: `decimer-${jobId}.zip.txt` };
  }

  async report(jobId: string, index: number, signal?: AbortSignal): Promise<void> {
    await delay(this.latencyMs, signal);
    if (!this.jobs.get(jobId)?.spec.results.some((r) => r.index === index)) throw new ApiError(404, 'not_found');
  }
}
