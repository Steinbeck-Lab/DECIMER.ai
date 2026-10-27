import { useId, useMemo } from 'react';
import { strings } from '../strings';
import type { ResultView } from '../state/selectors';
import { UploadedDocument } from './UploadedDocument';

interface Props {
  /** The upload's file names, in order; none for an image pasted from the clipboard. */
  names: readonly string[];
  /** The pages the job processed (a PDF's) or the images it read. */
  pages: number;
  pageUrls: readonly string[];
  views: readonly ResultView[];
  /** The page (0-based) of the structure open in the editor: its place in the strip stands out. */
  selectedPage: number | null;
  /** A PDF has a document to show; an image is its own picture, and the tile shows its crop. */
  pdf: boolean;
  /** The page (0-based) the results are narrowed to, and what a chip does to it: choose its page, or let go of it (null). */
  pageFilter?: number | null;
  onPageFilter?: (page: number | null) => void;
}

function fileLabel(names: readonly string[]): string {
  const first = names[0];
  if (!first) return strings.results.pastedSource;
  return names.length > 1 ? strings.results.moreSources(first, names.length - 1) : first;
}

/** How many structures each page has (index = page). Pages beyond the job's own count are kept: no result is left out of the strip. */
function structuresPerPage(views: readonly ResultView[], pages: number): number[] {
  const last = views.reduce((m, v) => Math.max(m, v.result.pageIndex), -1);
  const counts = Array.from({ length: Math.max(pages, last + 1) }, () => 0);
  for (const v of views) counts[v.result.pageIndex] = (counts[v.result.pageIndex] ?? 0) + 1;
  return counts;
}

/**
 * The document tile of the results bento: what was uploaded and where its structures are, page by page, as labelled
 * chips ("p. 2" and the count; "none" for a page without). A chip with structures is a button that narrows the results to
 * its page (and back); the page of the open structure carries a copper outline and an "open" tag. For a PDF the tile
 * also holds the "Display uploaded document" toggle.
 */
export function DocumentTile({ names, pages, pageUrls, views, selectedPage, pdf, pageFilter = null, onPageFilter }: Props) {
  const counts = useMemo(() => structuresPerPage(views, pages), [views, pages]);
  const titleId = useId();
  const head = (
    <>
      <p className="dc-doc__name" title={fileLabel(names)}>
        {fileLabel(names)}
      </p>
      {counts.length > 1 && (
        <>
          <p className="dc-pages__title" id={titleId}>
            {pdf ? strings.results.pagesLabel : strings.results.imagesLabel}
          </p>
          <ol className="dc-pages" aria-labelledby={titleId}>
            {counts.map((n, page) => {
              const open = selectedPage === page;
              const face = (
                <>
                  <span className="dc-pages__no">{strings.results.pageChip(page + 1, pdf)}</span>{' '}
                  <b className="dc-pages__n">{n > 0 ? n : strings.results.pageNone}</b>
                </>
              );
              return (
                <li key={page} className="dc-pages__page" data-empty={n === 0} data-current={open}>
                  {n > 0 && onPageFilter ? (
                    // The button is named by what it shows ("p. 3", "8") and the words that complete it, so that its name
                    // holds its visible text (WCAG 2.5.3): "p. 3 8 structures, open in the editor". The "open" tag is drawn by CSS.
                    <button
                      type="button"
                      className="dc-pages__button"
                      data-page-chip={page}
                      aria-pressed={pageFilter === page}
                      onClick={() => onPageFilter(pageFilter === page ? null : page)}
                    >
                      {face}{' '}
                      <span className="dc-sr">{strings.results.pageStructuresWords(n, open)}</span>
                    </button>
                  ) : (
                    <>
                      <span aria-hidden="true" className="dc-pages__face">
                        {face}
                      </span>
                      <span className="dc-sr">{strings.results.pageStructures(page + 1, n, pdf)}</span>
                    </>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </>
  );
  return pdf && pageUrls.length > 0 ? (
    <UploadedDocument pageUrls={pageUrls} head={head} />
  ) : (
    <div className="dc-doc__tile">{head}</div>
  );
}
