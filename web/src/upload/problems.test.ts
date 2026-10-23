import { expect, test } from 'vitest';
import { API_ERROR_CODES, ApiError } from '../api/contract';
import { strings } from '../strings';
import { describeProblem, problemFromError } from './problems';

test('every API error code has its own user-facing sentence', () => {
  const seen = new Set<string>();
  for (const code of API_ERROR_CODES) {
    const { text } = describeProblem({ kind: 'api', code, message: null });
    expect(text.length, code).toBeGreaterThan(10);
    seen.add(text);
  }
  // Not one generic sentence for all of them: the codes that need different advice say different things.
  expect(seen.size).toBeGreaterThanOrEqual(API_ERROR_CODES.length - 3);
});

test('the codes named by the API (errors.py, ingest.py, routes.py) are all known', () => {
  for (const code of [
    'mixed_upload', 'multiple_pdfs', 'too_many_files', 'corrupt_file', 'encrypted_pdf', 'too_many_pixels',
    'unavailable', 'invalid_request', 'empty_upload', 'rate_limited', 'queue_full', 'too_large',
    'unsupported', 'not_found', 'processing_limit', 'busy', 'no_results', 'internal_error',
  ]) {
    expect(API_ERROR_CODES, code).toContain(code);
  }
});

test('the server\'s message is carried, and shown as detail where it names the file', () => {
  const error = new ApiError(400, 'corrupt_file', 'paper.pdf could not be read as a PDF document.');
  const problem = problemFromError(error);
  expect(problem).toEqual({ kind: 'api', code: 'corrupt_file', message: 'paper.pdf could not be read as a PDF document.' });
  expect(describeProblem(problem)).toEqual({
    text: strings.upload.errors.corrupt_file,
    detail: 'paper.pdf could not be read as a PDF document.',
  });
});

test('a generic server message is not repeated as detail', () => {
  const problem = problemFromError(new ApiError(429, 'rate_limited', 'Too many active jobs.'));
  expect(problem.kind === 'api' && problem.message).toBe('Too many active jobs.');
  expect(describeProblem(problem).detail).toBeNull();
});

test('an error that is not from the API becomes "unknown" with no detail', () => {
  expect(problemFromError(new TypeError('Failed to fetch'))).toEqual({ kind: 'api', code: 'unknown', message: null });
});
