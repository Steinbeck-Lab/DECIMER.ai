import { useEffect, useState } from 'react';
import { strings } from '../strings';
import { IconChevronRight, PillIcon } from '../ui/icons';

/** How long a page that is not there yet waits before it is asked for again, while the pages are being read. */
const RETRY_MS = 3000;

interface ToggleProps {
  open: boolean;
  onToggle: () => void;
  /** The id of the panel it opens (`DocumentPanel`). */
  controls: string;
}

/**
 * "Display uploaded document": a quiet porcelain pill under the working screen's numbers. It only opens and closes the panel
 * below the working block (the page grows downwards; nothing above it moves).
 */
export function DocumentToggle({ open, onToggle, controls }: ToggleProps) {
  return (
    <button
      type="button"
      className="dc-pill dc-pill--quiet dc-pill--small dc-doc__toggle"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
    >
      {open ? strings.working.document.hide : strings.working.document.show}
      <PillIcon>
        <span className="dc-doc__chevron">
          <IconChevronRight size={16} strokeWidth={2} />
        </span>
      </PillIcon>
    </button>
  );
}

interface PanelProps {
  id: string;
  open: boolean;
  /**
   * One entry per page of the upload, in order: the address of its picture, or null where there is none to ask for yet
   * (an image the browser cannot show, before the job has its own). An empty list: the pages are not known yet.
   */
  pages: readonly (string | null)[];
  /** The stage that reads the pages is over: a page that is still not there will not come. */
  pagesRead: boolean;
}

/**
 * The document being processed, page by page, big, side by side in a strip that scrolls inside its own box with the
 * pages snapping into place (stacked, full width, on a phone), each with its number under it. Shut, it renders no image at all, so nothing is fetched until someone
 * asks; open, the pictures load lazily as the strip is scrolled. A page that has no picture yet (the worker has not
 * rendered it: the address answers 404) is a calm "not read yet" in its place and is asked for again every few
 * seconds until the pages have been read; one that is still missing then says it could not be shown.
 */
export function DocumentPanel({ id, open, pages, pagesRead }: PanelProps) {
  // The pages whose picture failed, each with the round (see below) it failed in: a page counts as missing only in
  // that round, so asking again needs no clearing.
  const [failed, setFailed] = useState<ReadonlyMap<number, string>>(new Map());
  const [attempt, setAttempt] = useState(0);
  const [wasOpen, setWasOpen] = useState(open);
  // Shut, nothing is remembered: it is asked afresh when it is opened again.
  if (open !== wasOpen) {
    setWasOpen(open);
    setFailed(new Map());
  }
  const total = pages.length;
  // A new round every few seconds while the pages are being read, and once more when they have been.
  const round = `${attempt}:${pagesRead}`;
  const isMissing = (index: number) => failed.get(index) === round;

  useEffect(() => {
    if (!open || pagesRead) return;
    const timer = setInterval(() => setAttempt((n) => n + 1), RETRY_MS);
    return () => clearInterval(timer);
  }, [open, pagesRead]);

  return (
    <div className="dc-doc dc-workdoc" hidden={!open}>
      <div
        id={id}
        className="dc-doc__strip"
        // A scrolling box must be reachable by keyboard to be scrolled; the name says what it holds.
        role="region"
        tabIndex={0}
        aria-label={total > 0 ? strings.results.document.region(total) : strings.working.document.region}
      >
        {open && total === 0 && <p className="dc-workdoc__note">{strings.working.document.none}</p>}
        {open && total > 0 && (
          <ol className="dc-doc__pages">
            {pages.map((src, index) => (
              <li key={index} className="dc-doc__page">
                {src === null || isMissing(index) ? (
                  <p className="dc-doc__missing">
                    {pagesRead ? strings.results.document.pageMissing(index + 1) : strings.working.document.waiting(index + 1)}
                  </p>
                ) : (
                  <img
                    src={src}
                    alt={strings.results.document.pageAlt(index + 1, total)}
                    loading="lazy"
                    decoding="async"
                    onError={() => setFailed((was) => new Map(was).set(index, round))}
                  />
                )}
                {/* The page's number under it, for the eye (the picture's own text and the placeholder say it in words). */}
                <span className="dc-workdoc__no" aria-hidden="true">
                  {strings.working.document.caption(index + 1, total)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
