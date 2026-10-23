import { useEffect, useState } from 'react';
import { STAGE_ORDER, type Job, type StageName } from '../api/contract';
import { strings } from '../strings';
import { useAppState } from '../state/react';
import type { SourceFile } from '../state/model';
import { isPdfUpload, pagesToProcess, stageProgress } from '../state/selectors';
import { IconFile } from '../ui/icons';

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

/** Elapsed time since `startedAt`, ticking once a second. */
function useElapsed(startedAt: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return startedAt === null ? 0 : now - startedAt;
}

function SourceStrip({ sources }: { sources: SourceFile[] }) {
  const shown = sources.slice(0, 3);
  const more = sources.length - shown.length;
  return (
    <ul className="dc-sources">
      {shown.map((s) => (
        <li key={`${s.name}-${s.size}`} className="dc-source">
          {s.previewUrl ? (
            <img className="dc-source__thumb" src={s.previewUrl} alt={strings.working.sourcePreview(s.name)} />
          ) : (
            <span className="dc-source__thumb dc-source__thumb--file">
              <IconFile size={18} />
            </span>
          )}
          <span className="dc-source__name">{s.name}</span>
        </li>
      ))}
      {more > 0 && <li className="dc-source dc-source--more">+{more}</li>}
    </ul>
  );
}

/** Five segments, one per stage, each in its own state: done ink, active ink and breathing (several can be, the stages overlap), pending pale. */
function ProgressRule({ job }: { job: Job | null }) {
  const { states, current, doneCount } = stageProgress(job);
  const queued = job?.status === 'queued';
  return (
    <div
      className="dc-rule"
      role="progressbar"
      aria-label={strings.working.progressLabel}
      aria-valuemin={0}
      aria-valuemax={STAGE_ORDER.length}
      aria-valuenow={doneCount}
      aria-valuetext={strings.working.stepsDone(doneCount, STAGE_ORDER.length)}
    >
      {STAGE_ORDER.map((name, i) => {
        const state = job ? states[name] : i === 0 && !queued ? 'active' : 'pending';
        return <span key={name} className="dc-rule__segment" data-state={queued ? 'pending' : state} data-index={i} data-current={i === current} />;
      })}
    </div>
  );
}

export function WorkingView() {
  const state = useAppState();
  const { job } = state;
  const elapsed = useElapsed(state.startedAt);
  const queued = job?.status === 'queued';
  const { states, current, alongside } = stageProgress(job);

  // The stages overlap: the heading names the latest one that is running, and says what else is.
  const stage: StageName = STAGE_ORDER[current] ?? 'upload';
  const next: StageName | undefined = STAGE_ORDER[current + 1];

  const heading = queued ? strings.working.queuedHeading : strings.working.stage[stage];
  const detail = queued
    ? strings.working.queuePosition(job?.queuePosition ?? 1)
    : `${strings.working.stepOf(current + 1, STAGE_ORDER.length)}. ${
        next ? strings.working.next(strings.working.stage[next]) : strings.working.last
      }`;
  const together = queued || alongside.length === 0 ? null : strings.working.alongside(alongside);

  const rendering = !queued && states.render !== 'done' && job && job.pages > 0;
  // How long to expect depends on what was uploaded: the job's pages once it is known, else the images chosen (a
  // PDF's page count is only known to the server).
  const pdf = isPdfUpload(state.sources);
  const jobPages = job && job.pages > 0 ? pagesToProcess(job, pdf) : 0;
  const expectedPages = jobPages || (pdf ? 0 : state.sources.length) || null;

  return (
    <main id="main" tabIndex={-1} className="dc-working">
      <SourceStrip sources={state.sources} />
      {/* Only this region is announced; the ticking clock below is not. */}
      <div className="dc-working__live" role="status" aria-live="polite" aria-atomic="true">
        <h1 className="dc-working__title">{heading}</h1>
        <p className="dc-working__detail">{detail}</p>
        {together && <p className="dc-working__detail">{together}</p>}
        {queued && <p className="dc-working__detail">{strings.working.queueNote}</p>}
      </div>
      <ProgressRule job={job} />
      <p className="dc-working__meta">
        <span>{strings.working.elapsed(formatElapsed(elapsed))}</span>
        {rendering && <span>{strings.working.pages(job.pagesProcessed, pagesToProcess(job, pdf))}</span>}
        {/* The results themselves arrive with the finished job; until then a poll only carries this count. */}
        {job && job.structuresFound > 0 && <span>{strings.working.found(job.structuresFound)}</span>}
      </p>
      <p className="dc-working__note">{strings.working.slowNote(expectedPages, pdf)}</p>
    </main>
  );
}
