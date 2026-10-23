import type { Job } from '../api/contract';
import { strings } from '../strings';

/**
 * What to tell the user about a finished job's limits and warnings. Limits are
 * worded from the numbers (so they read the same whatever the server says), and
 * each is said once: the API also sends a `pages_truncated` and a
 * `structures_truncated` warning for the same fact, which are not repeated. A
 * warning code this version does not know is shown as the API worded it, and
 * a job that was truncated for no stated reason still says so.
 */
export function jobNotices(
  job: Pick<Job, 'pages' | 'pagesProcessed' | 'structuresDropped' | 'truncated' | 'warnings'>,
): string[] {
  const notices: string[] = [];
  if (job.pages > job.pagesProcessed || job.warnings.some((w) => w.code === 'pages_truncated')) {
    notices.push(strings.results.truncatedPages(job.pages, job.pagesProcessed));
  }
  // Without a count to word it from, the API's own sentence is all there is, so it stays.
  const structuresWorded = job.structuresDropped > 0;
  if (structuresWorded) notices.push(strings.results.truncatedStructures(job.structuresDropped));
  for (const warning of job.warnings) {
    if (warning.code === 'pages_truncated') continue;
    if (warning.code === 'structures_truncated' && structuresWorded) continue;
    notices.push(warning.message);
  }
  if (notices.length === 0 && job.truncated) notices.push(strings.results.truncatedGeneric);
  return notices;
}
