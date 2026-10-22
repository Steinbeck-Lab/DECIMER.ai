import { ApiError } from '../api/contract';
import type { Problem } from '../state/model';
import { strings } from '../strings';
import { describeSelectionError } from './files';

/** An error from the API, or anything else thrown while uploading, as the problem the empty screen shows. */
export function problemFromError(error: unknown): Problem {
  if (error instanceof ApiError) return { kind: 'api', code: error.code, message: error.serverMessage };
  return { kind: 'api', code: 'unknown', message: null };
}

/** Codes whose server message names the file or the limit that was hit: worth showing under our sentence. */
const SHOWS_SERVER_MESSAGE = new Set(['corrupt_file', 'encrypted_pdf', 'too_many_pixels', 'unsupported', 'too_many_files']);

export function describeProblem(problem: Problem): { text: string; detail: string | null } {
  if (problem.kind === 'selection') return { text: describeSelectionError(problem.error), detail: null };
  return {
    text: strings.upload.errors[problem.code],
    detail: problem.message && SHOWS_SERVER_MESSAGE.has(problem.code) ? problem.message : null,
  };
}
