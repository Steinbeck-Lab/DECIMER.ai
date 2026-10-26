import { useId, useState, type ReactNode } from 'react';
import { strings } from '../strings';
import { IconChevronRight, PillIcon } from '../ui/icons';

interface Props {
  pageUrls: readonly string[];
  /**
   * In the results bento: what the document's tile shows above the toggle (the file name, the structures per page).
   * The toggle then sits in that tile and the pages open as a tile of their own below the bento's top row. Without it
   * (the "no structures" screen) the toggle and the strip stand alone.
   */
  head?: ReactNode;
}

/**
 * "Display uploaded document" (legacy's toggle): the pages of the upload, side by side in a strip that scrolls
 * inside its own box. Shut, it renders no image at all, so no page is fetched until someone asks for it; open, the
 * images load lazily as the strip is scrolled. The strip is focusable, so the arrow keys scroll it.
 */
export function UploadedDocument({ pageUrls, head }: Props) {
  const [open, setOpen] = useState(false);
  // A page the worker could not render has no file, so its image fails: say so in its place.
  const [missing, setMissing] = useState<ReadonlySet<number>>(new Set());
  const stripId = useId();
  const total = pageUrls.length;
  if (total === 0) return null;

  const toggle = (
    <button
      type="button"
      className="dc-pill dc-pill--quiet dc-pill--small dc-doc__toggle"
      aria-expanded={open}
      aria-controls={stripId}
      onClick={() => setOpen((was) => !was)}
    >
      {strings.results.document.toggle}
      <PillIcon>
        <span className="dc-doc__chevron">
          <IconChevronRight size={16} strokeWidth={2} />
        </span>
      </PillIcon>
    </button>
  );

  const strip = (
    <div
      id={stripId}
      className="dc-doc__strip"
      hidden={!open}
      // A scrolling box must be reachable by keyboard to be scrolled; the name says what it holds.
      role="region"
      tabIndex={0}
      aria-label={strings.results.document.region(total)}
    >
      {open && (
        <ol className="dc-doc__pages">
          {pageUrls.map((url, index) => (
            <li key={index} className="dc-doc__page">
              {missing.has(index) ? (
                <p className="dc-doc__missing">{strings.results.document.pageMissing(index + 1)}</p>
              ) : (
                <img
                  src={url}
                  alt={strings.results.document.pageAlt(index + 1, total)}
                  loading="lazy"
                  decoding="async"
                  onError={() => setMissing((was) => new Set(was).add(index))}
                />
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );

  if (head === undefined) {
    return (
      <div className="dc-doc">
        {toggle}
        {strip}
      </div>
    );
  }
  return (
    <div className="dc-doc" data-tile="true">
      <div className="dc-doc__tile">
        {head}
        {toggle}
      </div>
      {strip}
    </div>
  );
}
