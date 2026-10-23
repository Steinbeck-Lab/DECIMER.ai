/**
 * The API contract the UI works with.
 *
 * Everything the API serves (jobs, results, stages, warnings, the validate answer, the export
 * request, the error codes) is DERIVED from the types generated from its OpenAPI schema
 * (`generated.ts`, by `npm run gen:api`, TODO), so a change in the API shows up as a type
 * error here and in the code that uses it. `contract.check.ts` pins what the UI assumes about
 * those types.
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

/** `POST /api/jobs/{id}/results/{n}/validate`, `n` the 0-based `Result.index`: the request body. */
export type ValidateRequest = Schemas['ValidateRequest'];

/**
 * The answer: values recomputed on the server for an edited molfile, the same ones a recognised structure has.
 * `Required<>` as for `Result`: the API sends every field, and `parseValidate` fills the ones a server version
 * might leave out. `name`, `nameTier` and `nameVerified` are null when chem is off, slow or declines.
 */
export type ValidateResponse = Required<Schemas['ValidateView']>;

export type ExportFormat = Schemas['ExportFormat'];

/** `POST /api/jobs/{id}/export`. */
export interface ExportRequest {
  format: ExportFormat;
  /**
   * Edited molfiles keyed by `Result.index`. Results that were not edited are
   * absent: the server uses its own copy for those, and takes the order from
   * the job's results (never from the client). A key that is not a result of
   * the job, or a molfile the server cannot use, refuses the whole export.
   */
  molfiles: Record<number, string>;
}

export interface ExportResponse {
  blob: Blob;
  filename: string;
}

/**
 * `detail.code` of the API's one error shape, `{"detail": {"code", "message"}}`: the codes of the schema's
 * `ErrorCode` enum (api/src/decimer_api/errors.py), plus two the client makes up: `network` (no answer) and
 * `unknown` (anything unexpected).
 *
 * The list below must name every code of the schema and nothing else: `satisfies Record<ServerErrorCode, true>`
 * is a type error for a code the API has and the list does not, and for one the list has and the API does not.
 * So `npm run typecheck` fails after `npm run gen:api` brings a new code, until it is added here and given a
 * sentence in `strings.ts`.
 */
export type ServerErrorCode = Schemas['ErrorDetail']['code'];

const SERVER_CODES = {
  empty_upload: true,
  too_many_files: true,
  unsupported: true,
  mixed_upload: true,
  multiple_pdfs: true,
  corrupt_file: true,
  encrypted_pdf: true,
  too_many_pixels: true,
  processing_limit: true,
  too_large: true,
  invalid_request: true,
  rate_limited: true,
  queue_full: true,
  busy: true,
  unavailable: true,
  not_found: true,
  no_results: true,
  method_not_allowed: true,
  http_error: true,
  internal_error: true,
} as const satisfies Record<ServerErrorCode, true>;

export const SERVER_ERROR_CODES = Object.keys(SERVER_CODES) as readonly ServerErrorCode[];

export const API_ERROR_CODES: readonly ApiErrorCode[] = [...SERVER_ERROR_CODES, 'network', 'unknown'];

export type ApiErrorCode = ServerErrorCode | 'network' | 'unknown';

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
