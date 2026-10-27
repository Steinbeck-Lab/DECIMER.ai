import { useCallback, useEffect, useId, useMemo, useState } from 'react';
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
const FILTER_DOT: Partial<Record<Filter, Status>> = {
  review: 'review',
  edited: 'edited',
};

const FILTERS: { filter: Filter; label: string }[] = [
  { filter: 'all', label: strings.results.filterAll },
  { filter: 'review', label: strings.results.filterReview },
  { filter: 'edited', label: strings.results.filterEdited },
];

/**
 * The job's limits and warnings (state/notices.ts), in one callout; nothing when there are none. In the results (`calm`) it
 * is the second half of the green box: a Velvet Forest tile under the counts, a mustard dot to a line; alone on the "no
 * structures" screen it is the yellow callout.
 */
function JobNotices({ job, calm = false, incomplete = [] }: { job: Job; calm?: boolean; incomplete?: readonly number[] }) {
  const notices = jobNotices(job, incomplete);
  if (notices.length === 0) return null;
  return (
    <div className={calm ? 'dc-callout dc-notices' : 'dc-callout'} data-kind="warning" role="note">
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
  const { job, filter, selected, pageFilter } = state;
  const downloadNoteId = useId();

  // The tiles settle in once, when the results arrive; later filter changes do not replay it.
  const [arriving, setArriving] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setArriving(false), 1600);
    return () => clearTimeout(timer);
  }, []);

  // Stable, so memoised tiles are not re-rendered by every change elsewhere in the figure.
  const select = useCallback((index: number) => controller.select(index), [controller]);

  const counts = useMemo(() => countsOf(views), [views]);
  // The structures the model cut off at its length limit that nobody has edited or checked yet (an edit is the user's
  // structure; a check means they have looked at it, so "check it in the editor" is done).
  const cutOff = useMemo(
    () => views.filter((v) => v.result.incomplete && !v.modified && !v.checked).map((v) => v.number),
    [views],
  );
  // "All" counts every page, whichever page is chosen: pressing it shows them all. The others are counted on the chosen page.
  const chipCounts = useMemo(() => ({ ...filterCounts(views, pageFilter), all: views.length }), [views, pageFilter]);
  const shown = useMemo(() => filterViews(views, filter, pageFilter), [views, filter, pageFilter]);
  const setPageFilter = useCallback((page: number | null) => controller.setPageFilter(page), [controller]);

  if (!job) return null;

  const pdf = isPdfUpload(state.sources);
  const selectedView = selected === null ? undefined : views.find((v) => v.index === selected);

  // The pill that clears the page is gone once it is pressed, and the focus with it: it goes to that page's chip in the
  // document tile (or to the first filter, where the page has no chip).
  const clearPageFilter = (page: number) => {
    controller.setPageFilter(null);
    requestAnimationFrame(() =>
      (
        document.querySelector<HTMLElement>(`[data-page-chip="${page}"]`) ??
        document.querySelector<HTMLElement>('.dc-filters .dc-filter')
      )?.focus(),
    );
  };

  return (
    <main id="main" tabIndex={-1} className="dc-figure">
      <div className="dc-top">
        {/* The green box in two: the counts, and under them what the job could not do (nothing, and the counts have it all). */}
        <div className="dc-top__left">
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
          <JobNotices job={job} calm incomplete={cutOff} />
        </div>
        {/* Legacy shows the document for a PDF only; an image is its own picture, and the tile shows its crop. */}
        <DocumentTile
          names={state.sources.map((s) => s.name)}
          pages={job.pagesProcessed}
          pageUrls={job.pageUrls}
          views={views}
          selectedPage={selectedView ? selectedView.result.pageIndex : null}
          pdf={pdf}
          pageFilter={pageFilter}
          onPageFilter={setPageFilter}
        />
      </div>

      {/* The filters, and the one Download of the screen at the end of the row (on a phone, on a line under them). */}
      <div className="dc-filterbar">
        <div className="dc-filters" role="group" aria-label={strings.results.filterLabel}>
          {FILTERS.map(({ filter: f, label }) => {
            const dot = FILTER_DOT[f];
            return (
              <button
                key={f}
                type="button"
                className="dc-filter"
                // "All" is everything: it is not pressed while the results are narrowed to a page, and pressing it lets go of the page.
                aria-pressed={filter === f && (f !== 'all' || pageFilter === null)}
                onClick={() => controller.setFilter(f)}
              >
                {dot && <span className="dc-dot" data-status={dot} aria-hidden="true" />}
                {label} <span className="dc-filter__count">{chipCounts[f]}</span>
              </button>
            );
          })}
          {pageFilter !== null && (
            <button
              type="button"
              className="dc-filter dc-filter--page"
              aria-label={strings.results.pageFilterName(pageFilter + 1, pdf)}
              onClick={() => clearPageFilter(pageFilter)}
            >
              {strings.results.pageFilter(pageFilter + 1, pdf)}
              <span className="dc-filter__clear" aria-hidden="true">
                ×
              </span>
            </button>
          )}
        </div>
        <div className="dc-filterbar__download">
          <DownloadMenu describedBy={downloadNoteId} />
          <span id={downloadNoteId} className="dc-sr">
            {strings.results.downloadNote}
          </span>
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="dc-figure__empty" role="status">
          {pageFilter === null
            ? strings.results.filterEmpty[filter]
            : strings.results.filterEmptyOnPage[filter](pageFilter + 1, pdf)}
        </p>
      ) : (
        <ul
          className="dc-grid"
          aria-label={strings.results.gridLabel}
          data-arriving={arriving}
          data-editing={selected !== null}
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
