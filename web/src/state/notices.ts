import type { Job } from '../api/contract';
import { strings } from '../strings';

/**
 * What to tell the user about a finished job's limits and warnings. Limits are
 * worded from the numbers (so they read the same whatever the server says),
 * a warning code this version does not know is shown as the API worded it, and
 * a job that was truncated for no stated reason still says so.
 */
export function jobNotices(
  job: Pick<Job, 'pages' | 'pagesProcessed' | 'structuresDropped' | 'truncated' | 'warnings'>,
): string[] {
  const notices: string[] = [];
  if (job.pages > job.pagesProcessed || job.warnings.some((w) => w.code === 'pages_truncated')) {
    notices.push(strings.results.truncatedPages(job.pages, job.pagesProcessed));
  }
  if (job.structuresDropped > 0) notices.push(strings.results.truncatedStructures(job.structuresDropped));
  for (const warning of job.warnings) {
    if (warning.code !== 'pages_truncated') notices.push(warning.message);
  }
  if (notices.length === 0 && job.truncated) notices.push(strings.results.truncatedGeneric);
  return notices;
}
