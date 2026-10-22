/**
 * PROVISIONAL API CONTRACT.
 *
 * Hand-written from ROADMAP §3 and §4 (Phase 3) so the frontend can be built
 * before the API exists. When TODO lands, these types are generated from
 * FastAPI's OpenAPI schema (`openapi-typescript`) and this file is replaced by
 * a thin re-export. Field names and shapes may change then; nothing outside
 * `web/src/api/` and `web/src/mocks/` should depend on anything but these
 * names.
 */

/** Limits shown to the user (ROADMAP §3). The server enforces them. */
export const LIMITS = {
  maxPages: 10,
  maxStructures: 20,
  maxUploadBytes: 100 * 1024 * 1024,
  /** Files per upload (the API's `max_upload_files`). */
  maxUploadFiles: 50,
} as const;

export type JobStatus = 'queued' | 'running' | 'done' | 'failed';
export type StageName = 'upload' | 'render' | 'segment' | 'recognise' | 'check';
export type StageState = 'pending' | 'active' | 'done';

/** Pipeline order. The API returns every stage, in this order. */
export const STAGE_ORDER: readonly StageName[] = [
  'upload',
  'render',
  'segment',
  'recognise',
  'check',
];

export interface Stage {
  name: StageName;
  state: StageState;
}

export interface Result {
  /** Stable key of the result inside the job (0-based). Edits and exports use it. */
  index: number;
  /** Display number (1-based): the compound number shown everywhere. */
  number: number;
  /** 0-based page of the document the structure was found on. */
  pageIndex: number;
  /** Bounding box on the rendered page, pixels: [x0, y0, x1, y1]. */
  bbox: readonly [number, number, number, number];
  /** Image of the original crop. */
  cropUrl: string;
  /** RDKit depiction: a URL, or an inline SVG string (starts with `<svg`). */
  svg: string;
  smiles: string;
  /** Empty when the SMILES is invalid. */
  inchikey: string;
  /** V2000 molfile with 2D coordinates. */
  molfile: string;
  /** RDKit accepts the SMILES. */
  valid: boolean;
  /**
   * Classifier: this image is a chemical structure. `null` when the classifier gave no answer (the worker
   * could not reach it; a job warning says so). That is "unknown", not "not sure it is a structure".
   */
  isStructure: boolean | null;
  /** Recognition confidence, 0 to 1. */
  confidence: number;
  /** IUPAC name that passed Orthonym's OPSIN round trip, or null ("no verified name"). */
  name: string | null;
  /** Orthonym tier of `name` (for example `pin_verified`), null when there is no name. */
  nameTier: string | null;
}

/** A machine-readable code (the UI picks the wording) and an English fallback message. */
export interface JobWarning {
  code: string;
  message: string;
}

export interface Job {
  id: string;
  status: JobStatus;
  /** 1-based position while `status` is `queued`, otherwise null. */
  queuePosition: number | null;
  stages: Stage[];
  /** Pages in the document (1 for a single image). */
  pages: number;
  /** Pages actually processed (at most LIMITS.maxPages). */
  pagesProcessed: number;
  /** A page or structure limit cut the job short. */
  truncated: boolean;
  /** Structures found before the cap. */
  structuresFound: number;
  /** Structures found but not returned because of the cap. */
  structuresDropped: number;
  results: Result[];
  /** Things the user should know about the job (the API's `warnings`). */
  warnings: JobWarning[];
  /** Set when `status` is `failed`, otherwise null. */
  error: string | null;
  /** ISO 8601. */
  createdAt: string;
  /** ISO 8601: when the job and its files are deleted (one hour after the job ends). */
  expiresAt: string;
}

export interface CreateJobResponse {
  id: string;
}

export interface ValidateRequest {
  molfile: string;
}

/** `POST /jobs/{id}/results/{index}/validate`: values recomputed on the server. */
export interface ValidateResponse {
  smiles: string;
  inchikey: string;
  svg: string;
  valid: boolean;
  name: string | null;
  nameTier: string | null;
}

export type ExportFormat = 'zip' | 'sdf' | 'csv';

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

  constructor(status: number, code: ApiErrorCode, serverMessage?: string) {
    super(serverMessage ?? code);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.serverMessage = serverMessage ?? null;
  }
}

export function isTerminal(job: Pick<Job, 'status'>): boolean {
  return job.status === 'done' || job.status === 'failed';
}
