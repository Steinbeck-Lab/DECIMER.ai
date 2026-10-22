/**
 * The API contract the UI works with.
 *
 * What the API serves today (jobs, results, stages, warnings) is DERIVED from the
 * types generated from its OpenAPI schema (`generated.ts`, by `npm run gen:api`,
 * TODO), so a change in the API shows up as a type error here and in the
 * code that uses it. `contract.check.ts` pins what the UI assumes about those
 * types. The validate, export and report calls are not in
 * the schema yet and stay hand-written and PROVISIONAL: their shapes may change
 * when the API builds them.
 */
import type { components } from './generated';

type Schemas = components['schemas'];

/** Limits shown to the user (ROADMAP §3). The server enforces them. */
export const LIMITS = {
  maxPages: 10,
  maxStructures: 20,
  maxUploadBytes: 100 * 1024 * 1024,
  /** Files per upload (the API's `max_upload_files`). */
  maxUploadFiles: 50,
} as const;

export type JobStatus = Schemas['JobStatus'];
export type StageName = Schemas['StageName'];
export type StageState = Schemas['StageState'];
export type Stage = Schemas['Stage'];
/** A machine-readable code (the UI picks the wording) and an English fallback message. */
export type JobWarning = Schemas['JobWarning'];

/** Pipeline order. The API returns every stage, in this order. `contract.check.ts` fails if a stage is missing here. */
export const STAGE_ORDER = ['upload', 'render', 'segment', 'recognise', 'check'] as const satisfies readonly StageName[];

/**
 * A result. `Required<>`: the API always sends every field (a default is sent, not left out), and `parseJob`
 * fills the ones a server version might leave out.
 *
 * `nameTier` and `nameVerified` are Orthonym's own ids for what kind of name `name` is and how it was checked
 * (decision D14). `nameVerified` is `opsin`, `opsin_split`, `opsin_constitution`, `opsin_twin`, `opsin_acids` or
 * `identity`; null when there is no name. The UI words the ids it knows and shows an unknown tier as it is.
 */
export type Result = Required<Schemas['ResultView']>;

export type Job = Omit<Required<Schemas['JobView']>, 'results'> & { results: Result[] };

/** `POST /api/jobs` answers 202 with the whole job; the app needs its id to poll. */
export interface CreateJobResponse {
  id: string;
}

/** PROVISIONAL. */
export interface ValidateRequest {
  molfile: string;
}

/** PROVISIONAL. `POST /api/jobs/{id}/results/{n}/validate`: values recomputed on the server. */
export interface ValidateResponse {
  smiles: string;
  inchikey: string;
  svg: string;
  valid: boolean;
  name: string | null;
  nameTier: string | null;
  /** How the name was checked; see `Result`. */
  nameVerified: string | null;
}

export type ExportFormat = 'zip' | 'sdf' | 'csv';

/** PROVISIONAL. `POST /api/jobs/{id}/export`. */
export interface ExportRequest {
  format: ExportFormat;
  /**
   * Edited molfiles keyed by `Result.index`. Results that were not edited are
   * absent: the server uses its own copy for those, and takes the order from
   * the job's results (never from the client).
   */
  molfiles: Record<number, string>;
}

export interface ExportResponse {
  blob: Blob;
  filename: string;
}

/**
 * `detail.code` of the API's one error shape, `{"detail": {"code", "message"}}`
 * (api/src/decimer_api/errors.py, ingest.py, routes.py), plus two the client
 * makes up: `network` (no answer) and `unknown` (anything unexpected).
 */
export const API_ERROR_CODES = [
  'empty_upload',
  'too_many_files',
  'unsupported',
  'mixed_upload',
  'multiple_pdfs',
  'corrupt_file',
  'encrypted_pdf',
  'too_many_pixels',
  'too_large',
  'invalid_request',
  'rate_limited',
  'queue_full',
  'unavailable',
  'not_found',
  'network',
  'unknown',
] as const;

export type ApiErrorCode = (typeof API_ERROR_CODES)[number];

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  /** `detail.message` from the server, in English; null when the error did not come from the API. */
  readonly serverMessage: string | null;
  /** The `Retry-After` header of a 429 or 503, in milliseconds; null when there was none. */
  readonly retryAfterMs: number | null;

  constructor(status: number, code: ApiErrorCode, serverMessage?: string, retryAfterMs: number | null = null) {
    super(serverMessage ?? code);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.serverMessage = serverMessage ?? null;
    this.retryAfterMs = retryAfterMs;
  }
}

export function isTerminal(job: Pick<Job, 'status'>): boolean {
  return job.status === 'done' || job.status === 'failed';
}
