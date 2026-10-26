import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Job } from '../api/contract';
import { strings } from '../strings';
import type { Filter, Status } from '../state/model';
import { useAppState, useController, useResultViews } from '../state/react';
import { jobNotices } from '../state/notices';
import { countsOf, filterCounts, filterViews, isPdfUpload } from '../state/selectors';
import { preloadEditor } from '../editor/KetcherPanel';
import { DownloadMenu } from '../shell/DownloadMenu';
import { DocumentTile } from './DocumentTile';
import { Tile } from './Tile';
import { UploadedDocument } from './UploadedDocument';

const COUNT_ORDER: Status[] = ['review', 'notsure', 'edited', 'checked', 'unchecked'];

/** The filter pills' dot: the status the filter is about; "All" has none. */
const FILTER_DOT: Partial<Record<Filter, Status>> = { review: 'review', edited: 'edited' };

const FILTERS: { filter: Filter; label: string }[] = [
  { filter: 'all', label: strings.results.filterAll },
  { filter: 'review', label: strings.results.filterReview },
  { filter: 'edited', label: strings.results.filterEdited },
];

/** The job's limits and warnings (state/notices.ts), in one callout; nothing when there are none. */
function JobNotices({ job }: { job: Job }) {
  const notices = jobNotices(job);
  if (notices.length === 0) return null;
  return (
    <div className="dc-callout" data-kind="warning" role="note">
      {notices.map((notice) => (
        <p key={notice}>{notice}</p>
      ))}
    </div>
  );
}

export function ResultsView() {
  const state = useAppState();
  const controller = useController();
  const views = useResultViews(state);
  const { job, filter, selected } = state;

  // The tiles settle in once, when the results arrive; later filter changes do not replay it.
  const [arriving, setArriving] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setArriving(false), 1600);
    return () => clearTimeout(timer);
  }, []);

  // Stable, so memoised tiles are not re-rendered by every change elsewhere in the figure.
  const select = useCallback((index: number) => controller.select(index), [controller]);

  const counts = useMemo(() => countsOf(views), [views]);
  const chipCounts = useMemo(() => filterCounts(views), [views]);
  const shown = useMemo(() => filterViews(views, filter), [views, filter]);

  if (!job) return null;

  const pdf = isPdfUpload(state.sources);
  const selectedView = selected === null ? undefined : views.find((v) => v.index === selected);

  return (
    <main id="main" tabIndex={-1} className="dc-figure">
      <div className="dc-top">
        <div className="dc-slab dc-stats">
          <h1 className="dc-stats__title">{strings.results.title}</h1>
          <div className="dc-stats__body">
            <p className="dc-stats__total">{counts.total}</p>
            <ul className="dc-counts" aria-label={strings.results.countsLabel}>
              {COUNT_ORDER.filter((status) => counts[status] > 0).map((status) => (
                <li key={status} className="dc-count" data-status={status}>
                  <span className="dc-dot" data-status={status} aria-hidden="true" />
                  <b className="dc-count__n">{counts[status]}</b> {strings.results.countWords[status]}
                </li>
              ))}
            </ul>
          </div>
        </div>
        {/* Legacy shows the document for a PDF only; an image is its own picture, and the tile shows its crop. */}
        <DocumentTile
          names={state.sources.map((s) => s.name)}
          pages={job.pagesProcessed}
          pageUrls={job.pageUrls}
          views={views}
          selectedPage={selectedView ? selectedView.result.pageIndex : null}
          pdf={pdf}
        />
      </div>

      <JobNotices job={job} />

      <div className="dc-filters" role="group" aria-label={strings.results.filterLabel}>
        {FILTERS.map(({ filter: f, label }) => {
          const dot = FILTER_DOT[f];
          return (
            <button
              key={f}
              type="button"
              className="dc-filter"
              aria-pressed={filter === f}
              onClick={() => controller.setFilter(f)}
            >
              {dot && <span className="dc-dot" data-status={dot} aria-hidden="true" />}
              {label} <span className="dc-filter__count">{chipCounts[f]}</span>
            </button>
          );
        })}
      </div>

      {shown.length === 0 ? (
        <p className="dc-figure__empty" role="status">
          {strings.results.filterEmpty[filter]}
        </p>
      ) : (
        <ul
          className="dc-grid"
          aria-label={strings.results.gridLabel}
          data-arriving={arriving}
          // Start fetching the editor's code as soon as someone reaches for a structure.
          onPointerOver={preloadEditor}
          onFocus={preloadEditor}
        >
          {shown.map((view, order) => (
            <Tile
              key={view.index}
              view={view}
              selected={selected === view.index}
              order={Math.min(order, 12)}
              onSelect={select}
            />
          ))}
        </ul>
      )}

      <div className="dc-slab dc-figure__foot">
        <DownloadMenu placement="results" />
        <p>{strings.results.downloadNote}</p>
      </div>
    </main>
  );
}

/** "No structures were detected": no figure, no editor, a way to try again. */
export function NoStructuresView() {
  const controller = useController();
  const state = useAppState();
  return (
    <main id="main" tabIndex={-1} className="dc-working">
      <h1 className="dc-working__title">{strings.results.noneTitle}</h1>
      <p className="dc-working__detail">{strings.results.noneBody}</p>
      {state.job && <JobNotices job={state.job} />}
      {state.job && isPdfUpload(state.sources) && <UploadedDocument pageUrls={state.job.pageUrls} />}
      <button type="button" className="dc-pill dc-pill--ink dc-working__again" onClick={() => controller.startOver()}>
        {strings.results.uploadAnother}
      </button>
    </main>
  );
}

