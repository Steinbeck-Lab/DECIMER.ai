import { useMemo } from 'react';
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
 * The document tile of the results bento: what was uploaded and where its structures are, page by page. Pages
 * with none are muted; the page of the open structure takes the room. For a PDF it also holds the "Display uploaded
 * document" toggle.
 */
export function DocumentTile({ names, pages, pageUrls, views, selectedPage, pdf }: Props) {
  const counts = useMemo(() => structuresPerPage(views, pages), [views, pages]);
  const head = (
    <>
      <p className="dc-doc__name" title={fileLabel(names)}>
        {fileLabel(names)}
      </p>
      {counts.length > 1 && (
        <ol className="dc-pages" aria-label={strings.results.pagesLabel}>
          {counts.map((n, page) => (
            <li
              key={page}
              className="dc-pages__page"
              data-empty={n === 0}
              data-current={selectedPage === page}
            >
              <span className="dc-pages__no" aria-hidden="true">
                {page + 1}
              </span>
              {n > 0 && (
                <b className="dc-pages__n" aria-hidden="true">
                  {n}
                </b>
              )}
              <span className="dc-sr">{strings.results.pageStructures(page + 1, n)}</span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
  return pdf && pageUrls.length > 0 ? (
    <UploadedDocument pageUrls={pageUrls} head={head} />
  ) : (
    <div className="dc-doc__tile">{head}</div>
  );
}
