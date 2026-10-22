import type { ApiClient } from './client';
import {
  API_ERROR_CODES,
  ApiError,
  type ApiErrorCode,
  type CreateJobResponse,
  type ExportFormat,
  type ExportRequest,
  type ExportResponse,
  type Job,
  type ValidateResponse,
} from './contract';
import type { components } from './generated';
import { parseJob, parseValidate } from './normalise';

/** The codes the API's error shape can carry. The other two of `API_ERROR_CODES` are the client's own. */
const SERVER_CODES: ReadonlySet<string> = new Set(API_ERROR_CODES.filter((c) => c !== 'network' && c !== 'unknown'));

/** What a status means when the body does not say (a proxy's HTML page, a code from a newer API). */
function codeForStatus(status: number): ApiErrorCode {
  switch (status) {
    case 400:
    case 422:
      return 'invalid_request';
    case 404:
      return 'not_found';
    case 413:
      return 'too_large';
    case 415:
      return 'unsupported';
    case 429:
      return 'rate_limited';
    case 502:
    case 503:
    case 504:
      return 'unavailable';
    default:
      return 'unknown';
  }
}

/** `Retry-After` is a number of seconds or an HTTP date. Milliseconds, or null when it is neither. */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  const text = value?.trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) return Number(text) * 1000;
  const date = Date.parse(text);
  return Number.isNaN(date) ? null : Math.max(0, date - now);
}

/** The API's one error shape is `{"detail": {"code", "message"}}`; FastAPI's own validation errors have a list. */
async function errorFrom(response: Response): Promise<ApiError> {
  const retryAfterMs = parseRetryAfter(response.headers.get('Retry-After'));
  let code: ApiErrorCode = codeForStatus(response.status);
  let message: string | undefined;
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const fields = detail as { code?: unknown; message?: unknown };
      if (typeof fields.code === 'string' && SERVER_CODES.has(fields.code)) code = fields.code as ApiErrorCode;
      if (typeof fields.message === 'string') message = fields.message;
    }
  } catch {
    // Not JSON: the status has to do.
  }
  return new ApiError(response.status, code, message, retryAfterMs);
}

function hasName(error: unknown, name: string): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: unknown }).name === name;
}

function isAbort(error: unknown): boolean {
  return hasName(error, 'AbortError');
}

/**
 * Reads a response body. One that stops arriving (the connection dropped mid-transfer) rejects with a TypeError:
 * that is no answer, like a failed fetch, so a network error. An abort stays an abort, and so does anything else
 * (a body that is not JSON), which the caller knows how to read.
 */
async function readBody<T>(response: Response, read: (response: Response) => Promise<T>): Promise<T> {
  try {
    return await read(response);
  } catch (error) {
    if (hasName(error, 'TypeError')) throw new ApiError(0, 'network');
    throw error;
  }
}

/** `%E2%82%AC` to bytes, then the charset's reading of them. Throws on a stray `%`, bad UTF-8 or an unknown charset. */
function decodeExtValue(charset: string, encoded: string): string {
  const bytes: number[] = [];
  for (let i = 0; i < encoded.length; i++) {
    if (encoded[i] === '%') {
      const hex = encoded.slice(i + 1, i + 3);
      if (!/^[0-9A-Fa-f]{2}$/.test(hex)) throw new URIError('bad percent-escape');
      bytes.push(parseInt(hex, 16));
      i += 2;
    } else {
      bytes.push(encoded.charCodeAt(i));
    }
  }
  return new TextDecoder(charset, { fatal: true }).decode(new Uint8Array(bytes));
}

/** Control characters (C0 and DEL) and the bidirectional controls that can make "fdp.exe" read as "exe.pdf". */
function isUnsafeChar(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  return code <= 0x1f || code === 0x7f || code === 0x200e || code === 0x200f || (code >= 0x202a && code <= 0x202e) || (code >= 0x2066 && code <= 0x2069);
}

/**
 * What the server sent as a file name, made safe to save under: only the part after the last `/` or `\` (no path
 * to climb out of the downloads folder), no control or bidi characters, and not empty or hidden (a leading `.`).
 * Null when nothing usable is left; the caller names the file.
 */
function safeFilename(raw: string): string | null {
  const clean = [...raw].filter((char) => !isUnsafeChar(char)).join('');
  const name = clean.slice(Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\')) + 1).trim();
  return name && !name.startsWith('.') ? name : null;
}

/**
 * The file name in a Content-Disposition header (RFC 6266, RFC 8187), made safe by `safeFilename`.
 * `filename*=UTF-8''caf%C3%A9.zip` is percent-encoded with a charset and wins; `filename="plain.zip"` is literal
 * text and is never decoded ("100%.zip" is a name, not a broken escape). A name that cannot be read or used is
 * null, and the caller names the file: it is no reason to fail the download.
 */
function filenameFrom(disposition: string | null): string | null {
  if (!disposition) return null;
  const extended = /(?:^|;)\s*filename\*\s*=\s*([^;]*)/i.exec(disposition)?.[1]?.trim();
  if (extended) {
    try {
      const parts = /^([^']*)'[^']*'(.*)$/s.exec(extended);
      if (parts?.[1] && parts[2] !== undefined) {
        const name = safeFilename(decodeExtValue(parts[1], parts[2]));
        if (name) return name;
      }
    } catch {
      // Unreadable: the plain filename, if there is one.
    }
  }
  const plain = /(?:^|;)\s*filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;\s]+))/i.exec(disposition);
  const name = plain?.[1] !== undefined ? plain[1].replace(/\\(.)/g, '$1') : plain?.[2];
  return name ? safeFilename(name) : null;
}

const EXTENSION: Record<ExportFormat, string> = { zip: 'zip', sdf: 'sdf', csv: 'csv' };

export interface HttpApiClientOptions {
  /** In front of every path, for an API on another origin. Empty: the same origin as the page. */
  baseUrl?: string;
  /** For tests. */
  fetch?: typeof fetch;
  pollIntervalMs?: number;
}

/**
 * The ApiClient over the job API's real endpoints (selected by `VITE_API=http`).
 *
 * Built and tested against the API as it is today: `POST /api/jobs` and `GET /api/jobs/{id}`, with the media
 * URLs as the job returns them. The calls that come with P3-06 (validate), P3-07 (export) and P3-08 (report) use
 * the agreed paths and the request and response shapes of `contract.ts`. They are PROVISIONAL until those
 * endpoints exist and their types are generated.
 *
 * Every failure is an `ApiError` (the API's `{"detail": {"code", "message"}}`, or the status when the body is
 * not that), except an abort, which stays an `AbortError`: whoever aborted knows.
 */
export class HttpApiClient implements ApiClient {
  readonly pollIntervalMs: number;
  private readonly baseUrl: string;
  private readonly fetchFn: typeof fetch;

  constructor(options: HttpApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? '').replace(/\/+$/, '');
    this.fetchFn = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.pollIntervalMs = options.pollIntervalMs ?? 2000;
  }

  private url(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  /**
   * A media URL as the page can load it. The API sends paths from its own root (`/api/jobs/{id}/media/crop-0.png`),
   * so they go behind the same base as every other call, whether that is another origin (`https://host`), a
   * mount point on the same one (`/beta`) or both (`https://host/gateway`). A URL that already names an origin
   * (`https://host/...`, `//host/...`) is the server's to say and is left alone.
   */
  private resolveMedia = (url: string): string => {
    if (!this.baseUrl || /^([a-z][a-z0-9+.-]*:|\/\/)/i.test(url)) return url;
    return `${this.baseUrl}${url.startsWith('/') ? '' : '/'}${url}`;
  };

  private async send(path: string, init: RequestInit): Promise<Response> {
    let response: Response;
    try {
      response = await this.fetchFn(this.url(path), {
        credentials: 'same-origin',
        ...init,
        headers: { Accept: 'application/json', ...init.headers },
      });
    } catch (error) {
      if (isAbort(error)) throw error;
      throw new ApiError(0, 'network');
    }
    if (!response.ok) throw await errorFrom(response);
    return response;
  }

  private postJson(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
    return this.send(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
  }

  async createJob(files: File[], signal?: AbortSignal): Promise<CreateJobResponse> {
    const form = new FormData();
    for (const file of files) form.append('file', file, file.name);
    // No Content-Type: the browser writes it, with the multipart boundary.
    const response = await this.send('/api/jobs', { method: 'POST', body: form, signal });
    // A body that is not JSON is no job; one that never arrived is a network error (readBody), and an abort is not swallowed.
    const job = (await readBody(response, (r) =>
      r.json().catch((error: unknown) => {
        if (hasName(error, 'TypeError') || isAbort(error)) throw error;
        return null;
      }),
    )) as Partial<components['schemas']['JobView']> | null;
    if (typeof job?.id !== 'string' || !job.id) throw new ApiError(response.status, 'unknown', 'The server did not return a job.');
    return { id: job.id };
  }

  async getJob(id: string, signal?: AbortSignal): Promise<Job> {
    const response = await this.send(`/api/jobs/${encodeURIComponent(id)}`, { cache: 'no-store', signal });
    return parseJob((await readBody(response, (r) => r.json())) as components['schemas']['JobView'], this.resolveMedia);
  }

  /** PROVISIONAL: `POST /api/jobs/{id}/results/{n}/validate`, n the 0-based result index. */
  async validate(jobId: string, index: number, molfile: string, signal?: AbortSignal): Promise<ValidateResponse> {
    const response = await this.postJson(`/api/jobs/${encodeURIComponent(jobId)}/results/${index}/validate`, { molfile }, signal);
    return parseValidate((await readBody(response, (r) => r.json())) as Partial<ValidateResponse>);
  }

  /** PROVISIONAL: `POST /api/jobs/{id}/export`. */
  async exportJob(jobId: string, request: ExportRequest, signal?: AbortSignal): Promise<ExportResponse> {
    const response = await this.postJson(`/api/jobs/${encodeURIComponent(jobId)}/export`, request, signal);
    const filename = filenameFrom(response.headers.get('Content-Disposition')) ?? `decimer-${jobId}.${EXTENSION[request.format]}`;
    return { blob: await readBody(response, (r) => r.blob()), filename };
  }

  /** PROVISIONAL: `POST /api/jobs/{id}/report`. */
  async report(jobId: string, index: number, signal?: AbortSignal): Promise<void> {
    await this.postJson(`/api/jobs/${encodeURIComponent(jobId)}/report`, { index }, signal);
  }
}
