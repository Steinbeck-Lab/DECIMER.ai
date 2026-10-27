import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from 'react';
import { STAGE_ORDER, type Job, type StageName } from '../api/contract';
import { strings } from '../strings';
import { useAppState } from '../state/react';
import { isPdfUpload, pagesToProcess, stageProgress } from '../state/selectors';
import { Steady } from '../ui/Steady';
import { Specimen } from './Specimen';
import { DocumentPanel, DocumentToggle } from './WorkingDocument';
import { fragmentStates, StructurePlate } from './StructurePlate';

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
 * Every loop on the page stops where it is while the tab is hidden or the view is scrolled out of the window, and goes
 * on when it is back (`[data-paused]` in run.css pauses every animation under it, also one that starts meanwhile).
 * `onResume` runs when it goes on: loops that stopped together are still in step, but one that began meanwhile is not.
 */
function usePauseWhenHidden(ref: RefObject<HTMLElement | null>, onResume: () => void) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let hidden = document.hidden;
    let inView = true;
    const sync = () => {
      const paused = hidden || !inView;
      if (el.hasAttribute('data-paused') && !paused) onResume();
      el.toggleAttribute('data-paused', paused);
    };
    const onVisibility = () => {
      hidden = document.hidden;
      sync();
    };
    document.addEventListener('visibilitychange', onVisibility);
    // Absent in jsdom.
    const observer =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver((entries) => {
            inView = entries.at(-1)?.isIntersecting ?? true;
            sync();
          });
    observer?.observe(el);
    sync();
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      observer?.disconnect();
    };
  }, [ref, onResume]);
}

/** How long one drawing of an active segment of the measuring line takes (`dc-measure` in run.css). */
const LOOP_MS = 3000;

/**
 * Puts the drawing of every segment that has just turned active on the document's clock: a negative delay of the time
 * the clock is into the loop, so each starts at the same point of the loop as the ones before it and all of them draw
 * in step. (A delay, not a start time set through the Web Animations API: that would take the animation out of the
 * hands of `animation-play-state`, which pauses it with the tab.) A segment that has a delay already keeps it.
 */
function syncLoops(root: ParentNode | null) {
  const phase = -(Number(document.timeline?.currentTime ?? 0) % LOOP_MS);
  for (const el of root?.querySelectorAll<HTMLElement>('[data-state="active"] > .dc-rule__fill') ?? []) {
    if (el.style.animationDelay === '') el.style.animationDelay = `${phase}ms`;
  }
}

/** Between which two ticks of the measuring line the calliper stands: from the first running stage to the end of the last one. */
interface Span {
  from: number;
  to: number;
}

/**
 * The calliper above the measuring line: a beam with two jaws on the ticks of the stages that are running, a scale of
 * graduations between them and a mono readout over the middle (on a stem down to the beam). It slides to a new span
 * when the stages change (see `.dc-cal` in run.css; a transform and a clip, never a layout). The readout is a real
 * number from the job or, where the job has none for the stage, the stage's name; it is decoration, hidden from
 * assistive technology, and not part of the announced region.
 */
function Calliper({ span, readout }: { span: Span; readout: string }) {
  const style = { '--a': `var(--x${span.from})`, '--b': `var(--x${span.to})` } as CSSProperties;
  return (
    <div className="dc-cal" aria-hidden="true" style={style}>
      <span className="dc-cal__grad" />
      <span className="dc-cal__beam" />
      <span className="dc-cal__jaw dc-cal__jaw--a" />
      <span className="dc-cal__jaw dc-cal__jaw--b" />
      <span className="dc-cal__stem" />
      <span className="dc-cal__read">
        <span className="dc-cal__chip">{readout}</span>
      </span>
    </div>
  );
}

/**
 * The stages as a dimension line, like the drop zone's: a copper rule with a tick at every boundary and one segment
 * per stage. A done segment is drawn in full, a pending one is a hairline, an active one is being measured (several
 * can be, the stages overlap, and they are drawn in step: the job reports no progress inside a stage, so the line
 * does not pretend to know it). The calliper above stands across the stages that are running. The names under it
 * repeat what the heading and the line below it say, so they are hidden from assistive technology; the bar's own
 * value and text say how far it is.
 */
function ProgressRule({ job, span, readout, resumes }: { job: Job | null; span: Span; readout: string; resumes: number }) {
  const { states, doneCount } = stageProgress(job);
  const stateOf = (name: StageName, i: number) => (job ? states[name] : i === 0 ? 'active' : 'pending');
  const ref = useRef<HTMLDivElement>(null);

  // A segment that turns active later would start its drawing out of phase with the others: put every drawing on the
  // document's clock, so they all start over at the same moment. (Not needed, and not there, without motion.) After the
  // loops have been paused (`resumes`) the drawings begin again, together.
  const key = STAGE_ORDER.map(stateOf).join();
  useLayoutEffect(() => syncLoops(ref.current), [key, resumes]);

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
      <Calliper span={span} readout={readout} />
      {STAGE_ORDER.map((name, i) => (
        <span key={name} className="dc-rule__segment" data-state={stateOf(name, i)} data-index={i}>
          <span key={resumes} className="dc-rule__fill" />
          <span className="dc-rule__name" aria-hidden="true">
            {strings.working.stageShort[name]}
          </span>
        </span>
      ))}
    </div>
  );
}

/** What each slot of text can say: a slot is as large as the largest of its texts, so nothing moves when the text changes. */
const HEADINGS = [strings.working.queuedHeading, ...STAGE_ORDER.map((name) => strings.working.stage[name])];
const LABELS = [strings.working.queuedLabel, ...STAGE_ORDER.map((name) => strings.working.stageShort[name])];

const stepDetail = (current: number) => {
  const next: StageName | undefined = STAGE_ORDER[current + 1];
  return `${strings.working.stepOf(current + 1, STAGE_ORDER.length)}. ${
    next ? strings.working.next(strings.working.stage[next]) : strings.working.last
  }`;
};
const DETAILS = [
  ...STAGE_ORDER.map((_, i) => stepDetail(i)),
  // A queue is not likely to hold more than two digits.
  strings.working.queuePosition(1),
  strings.working.queuePosition(88),
];
/** The quiet line says why the queue waits, or what else is running (the most it can say: three other stages at once). */
const QUIET = [strings.working.queueNote, strings.working.alongside(STAGE_ORDER.slice(1, 4))];
/** The hint under the line, whatever the job: for an unknown number of pages, one page, one image, three images and fifty. */
const NOTES = [
  strings.working.slowNote(null, true),
  strings.working.slowNote(1, true),
  strings.working.slowNote(1, false),
  strings.working.slowNote(3, false),
  strings.working.slowNote(50, false),
];

export function WorkingView() {
  const state = useAppState();
  const { job } = state;
  const elapsed = useElapsed(state.startedAt);
  const queued = job?.status === 'queued';
  const { states, current, active, alongside } = stageProgress(job);
  const mainRef = useRef<HTMLElement>(null);
  const [resumes, setResumes] = useState(0);
  const [documentOpen, setDocumentOpen] = useState(false);
  const documentId = useId();
  usePauseWhenHidden(mainRef, useCallback(() => setResumes((n) => n + 1), []));

  // The stages overlap: the heading names the latest one that is running, and says what else is.
  const stage: StageName = STAGE_ORDER[current] ?? 'upload';

  const heading = queued ? strings.working.queuedHeading : strings.working.stage[stage];
  const detail = queued ? strings.working.queuePosition(job?.queuePosition ?? 1) : stepDetail(current);
  const quiet = queued ? strings.working.queueNote : alongside.length === 0 ? '' : strings.working.alongside(alongside);

  const rendering = !queued && states.render !== 'done' && job && job.pages > 0;
  // How long to expect depends on what was uploaded: the job's pages once it is known, else the images chosen (a
  // PDF's page count is only known to the server).
  const pdf = isPdfUpload(state.sources);
  const jobPages = job && job.pages > 0 ? pagesToProcess(job, pdf) : 0;
  const expectedPages = jobPages || (pdf ? 0 : state.sources.length) || null;

  // The calliper stands across the running stages. Waiting in the queue it is closed, on the tick after the upload.
  // Before the job exists the upload is running, as the measuring line says.
  const span: Span = !job
    ? { from: 0, to: 1 }
    : active.length > 0
      ? { from: active[0] ?? current, to: (active.at(-1) ?? current) + 1 }
      : { from: current, to: queued ? current : Math.min(current + 1, STAGE_ORDER.length) };
  // What it reads is only what the job knows: its place in the queue; the structures it found, once it is known how
  // many (the job reports no count of those recognised or checked so far); the pages it is working through; else the
  // name of the stage (the clock is right below, as "Elapsed"). The API reports no progress inside a stage, so there is
  // no "page 3 of 10" to make up.
  const found = job?.structuresFound ?? 0;
  const readout = queued
    ? strings.working.readout.queue(job?.queuePosition ?? 1)
    : current >= STAGE_ORDER.indexOf('recognise') && found > 0
      ? strings.working.readout.structures(found)
      : current >= STAGE_ORDER.indexOf('render') && jobPages > 0
        ? strings.working.readout.pages(jobPages, pdf)
        : strings.working.stageShort[stage];

  // The document, for the panel under the working block: a PDF's pages as the job lists them (the worker has rendered
  // some of them yet, or none: the panel asks, see WorkingDocument.tsx); an upload of images is the images the browser
  // already holds (the job's own pages stand in for one it cannot show, HEIC).
  const documentPages: (string | null)[] = pdf
    ? [...(job?.pageUrls ?? [])]
    : state.sources.map((source, i) => source.previewUrl ?? job?.pageUrls[i] ?? null);
  const pagesRead = job !== null && states.render === 'done';

  // The panel opens right under the toggle, inside the working block, which grows downwards. That block is centred in what
  // the window leaves, so opening would move what is above up as the free room is used: its content is pinned to the
  // place it has (the offset of its first line from the top) while the panel is open, and let go when it is shut. Then
  // the toggle is scrolled to the top of the window, with the panel under it.
  const toggleDocument = () => {
    const main = mainRef.current;
    const first = main?.firstElementChild;
    if (main && first) {
      if (documentOpen) {
        main.style.justifyContent = '';
        main.style.paddingTop = '';
      } else {
        const offset =
          first.getBoundingClientRect().top - main.getBoundingClientRect().top - (parseFloat(getComputedStyle(first).marginTop) || 0);
        main.style.justifyContent = 'flex-start';
        main.style.paddingTop = `${offset}px`;
      }
    }
    setDocumentOpen(!documentOpen);
  };
  useEffect(() => {
    if (!documentOpen) return;
    const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.querySelector('.dc-run__doc')?.scrollIntoView?.({ block: 'start', behavior: reduced ? 'auto' : 'smooth' });
  }, [documentOpen]);

  const label = <Steady value={queued ? strings.working.queuedLabel : strings.working.stageShort[stage]} sizers={LABELS} />;

  return (
    <main id="main" tabIndex={-1} className="dc-run" ref={mainRef}>
      <Specimen
        sources={state.sources}
        label={label}
        plate={<StructurePlate states={fragmentStates(states, current, queued)} />}
      />
      {/* Only this region is announced; the ticking clock below is not. Each line is a slot that keeps its size. */}
      <div className="dc-run__live" role="status" aria-live="polite" aria-atomic="true">
        <h1 className="dc-run__title">
          <Steady value={heading} sizers={HEADINGS} align="end" />
        </h1>
        <p className="dc-run__detail">
          <Steady value={detail} sizers={DETAILS} />
        </p>
        <p className="dc-run__detail dc-run__detail--quiet">
          <Steady value={quiet} sizers={QUIET} />
        </p>
      </div>
      <div className="dc-run__measure">
        <ProgressRule job={job} span={span} readout={readout} resumes={resumes} />
        <p className="dc-run__meta">
          <span className="dc-run__clock">{strings.working.elapsed(formatElapsed(elapsed))}</span>
          <span className="dc-run__count">
            {rendering && <span>{strings.working.pages(job.pagesProcessed, pagesToProcess(job, pdf), pdf)}</span>}
            {/* The results themselves arrive with the finished job; until then a poll only carries this count. */}
            {job && job.structuresFound > 0 && <span>{strings.working.found(job.structuresFound)}</span>}
          </span>
        </p>
        <p className="dc-run__note">
          <Steady value={strings.working.slowNote(expectedPages, pdf)} sizers={NOTES} />
        </p>
        <div className="dc-run__doc">
          <DocumentToggle open={documentOpen} onToggle={toggleDocument} controls={documentId} />
        </div>
      </div>
      <DocumentPanel id={documentId} open={documentOpen} pages={documentPages} pagesRead={pagesRead} />
    </main>
  );
}
