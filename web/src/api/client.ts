import type {
  CreateJobResponse,
  ExportRequest,
  ExportResponse,
  Job,
  ValidateResponse,
} from './contract';

/**
 * Everything the app asks of the backend. `MockApiClient` implements it today;
 * an HTTP client over the generated types will implement it
 * later. Methods reject with `ApiError` for HTTP-level failures.
 */
export interface ApiClient {
  /** How often the app polls `getJob`, in ms (2000 for the real API). */
  readonly pollIntervalMs: number;
  /** `POST /api/jobs`: one PDF, or one or more images. */
  createJob(files: File[], signal?: AbortSignal): Promise<CreateJobResponse>;
  /** `GET /api/jobs/{id}` */
  getJob(id: string, signal?: AbortSignal): Promise<Job>;
  /**
   * `POST /api/jobs/{id}/results/{n}/validate` (decision D12) where `n` is the 0-based `Result.index`, not
   * the display number: canonical SMILES, InChIKey, SVG and name for an edited molfile. The request is
   * aborted when a newer edit of the same result supersedes it.
   */
  validate(jobId: string, index: number, molfile: string, signal?: AbortSignal): Promise<ValidateResponse>;
  /** `POST /api/jobs/{id}/export` */
  exportJob(jobId: string, request: ExportRequest, signal?: AbortSignal): Promise<ExportResponse>;
  /** `POST /api/jobs/{id}/report`: a problem report for one result. */
  report(jobId: string, index: number, signal?: AbortSignal): Promise<void>;
}
