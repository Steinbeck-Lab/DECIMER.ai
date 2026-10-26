import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { STAGE_ORDER, type Job, type StageName } from '../api/contract';
import { strings } from '../strings';
import { useAppState } from '../state/react';
import { isPdfUpload, pagesToProcess, stageProgress } from '../state/selectors';
import { Specimen } from './Specimen';

function formatElapsed(ms: number): string {
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

/**
 * The stages as a dimension line, like the drop zone's: a copper rule with a tick at every boundary and one segment
 * per stage. A done segment is drawn in full, a pending one is a hairline, an active one is being measured (several
 * can be, the stages overlap, and they are drawn in step). The names under it repeat what the heading and the line
 * below it say, so they are hidden from assistive technology; the bar's own value and text say how far it is.
 */
function ProgressRule({ job }: { job: Job | null }) {
  const { states, doneCount } = stageProgress(job);
  const stateOf = (name: StageName, i: number) => (job ? states[name] : i === 0 ? 'active' : 'pending');
  const ref = useRef<HTMLDivElement>(null);

  // A segment that turns active later would start its drawing out of phase with the others: put every drawing on the
  // document's clock, so they all start over at the same moment. (Not needed, and not there, without motion.)
  const key = STAGE_ORDER.map(stateOf).join();
  useLayoutEffect(() => {
    const drawn = ref.current?.querySelectorAll('[data-state="active"] > :is(.dc-rule__fill, .dc-rule__head)') ?? [];
    for (const el of drawn) {
      // Absent in jsdom.
      if (typeof el.getAnimations !== 'function') continue;
      for (const animation of el.getAnimations()) animation.startTime = 0;
    }
  }, [key]);

  return (
    <div
      ref={ref}
      className="dc-rule"
      role="progressbar"
      aria-label={strings.working.progressLabel}
      aria-valuemin={0}
      aria-valuemax={STAGE_ORDER.length}
      aria-valuenow={doneCount}
      aria-valuetext={strings.working.stepsDone(doneCount, STAGE_ORDER.length)}
    >
      {STAGE_ORDER.map((name, i) => (
        <span key={name} className="dc-rule__segment" data-state={stateOf(name, i)} data-index={i}>
          <span className="dc-rule__fill" />
          <span className="dc-rule__head" />
          <span className="dc-rule__name" aria-hidden="true">
            {strings.working.stageShort[name]}
          </span>
        </span>
      ))}
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
    <main id="main" tabIndex={-1} className="dc-run">
      <Specimen sources={state.sources} label={queued ? strings.working.queuedLabel : strings.working.stageShort[stage]} />
      {/* Only this region is announced; the ticking clock below is not. */}
      <div className="dc-run__live" role="status" aria-live="polite" aria-atomic="true">
        <h1 className="dc-run__title">{heading}</h1>
        <p className="dc-run__detail">{detail}</p>
        {together && <p className="dc-run__detail dc-run__detail--quiet">{together}</p>}
        {queued && <p className="dc-run__detail dc-run__detail--quiet">{strings.working.queueNote}</p>}
      </div>
      <div className="dc-run__measure">
        <ProgressRule job={job} />
        <p className="dc-run__meta">
          <span>{strings.working.elapsed(formatElapsed(elapsed))}</span>
          {rendering && <span>{strings.working.pages(job.pagesProcessed, pagesToProcess(job, pdf))}</span>}
          {/* The results themselves arrive with the finished job; until then a poll only carries this count. */}
          {job && job.structuresFound > 0 && <span>{strings.working.found(job.structuresFound)}</span>}
        </p>
        <p className="dc-run__note">{strings.working.slowNote(expectedPages, pdf)}</p>
      </div>
    </main>
  );
}
