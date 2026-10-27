import type { Job } from '../api/contract';
import { strings } from '../strings';

/**
 * What to tell the user about a finished job's limits and warnings. Limits are
 * worded from the numbers (so they read the same whatever the server says), and
 * each is said once: the API also sends a `pages_truncated` and a
 * `structures_truncated` warning for the same fact, which are not repeated. A
 * warning code this version does not know is shown as the API worded it, and
 * a job that was truncated for no stated reason still says so. The warnings about one structure or page each are
 * joined by kind: "Structures 1, 4 and 9 could not be recognised." is one line, not three. So are the structures the
 * model cut off at its length limit (`incomplete`, made from the results, not from a warning).
 */
export function jobNotices(
  job: Pick<Job, 'pages' | 'pagesProcessed' | 'structuresDropped' | 'truncated' | 'warnings'>,
  /** The numbers of the structures the model cut off at its length limit and nobody has edited: the API sends no warning for them. */
  incomplete: readonly number[] = [],
): string[] {
  const notices: (string | Counted)[] = [];
  if (job.pages > job.pagesProcessed || job.warnings.some((w) => w.code === 'pages_truncated')) {
    notices.push(strings.results.truncatedPages(job.pages, job.pagesProcessed));
  }
  // Without a count to word it from, the API's own sentence is all there is, so it stays.
  const structuresWorded = job.structuresDropped > 0;
  if (structuresWorded) notices.push(strings.results.truncatedStructures(job.structuresDropped));
  // The warnings that are about one structure or page each are one sentence for each kind, where the first of the kind was.
  const groups = new Map<CountedCode, Counted>();
  for (const warning of job.warnings) {
    if (warning.code === 'pages_truncated') continue;
    if (warning.code === 'structures_truncated' && structuresWorded) continue;
    const code = warning.code as CountedCode;
    const number = isCounted(code) ? NUMBER.exec(warning.message)?.[1] : undefined;
    if (!isCounted(code) || number === undefined) {
      notices.push(warning.message);
      continue;
    }
    let group = groups.get(code);
    if (!group) {
      group = { code, numbers: [] };
      groups.set(code, group);
      notices.push(group);
    }
    if (!group.numbers.includes(Number(number))) group.numbers.push(Number(number));
  }
  // The cut-off structures, in one sentence, after the warnings the API sent.
  if (incomplete.length > 0) notices.push({ code: 'incomplete', numbers: [...new Set(incomplete)] });
  const lines = notices.map((notice) =>
    typeof notice === 'string' ? notice : strings.results.countedWarnings[notice.code]([...notice.numbers].sort((a, b) => a - b)),
  );
  if (lines.length === 0 && job.truncated) lines.push(strings.results.truncatedGeneric);
  return lines;
}

type CountedCode = keyof typeof strings.results.countedWarnings;
interface Counted {
  code: CountedCode;
  numbers: number[];
}

const isCounted = (code: string): code is CountedCode => Object.hasOwn(strings.results.countedWarnings, code);
/** The number the API puts first in such a warning: "Structure 13 could not be recognised.", "Page 2 could not be rendered...". */
const NUMBER = /^(?:Page|Structure) (\d+)\b/;
