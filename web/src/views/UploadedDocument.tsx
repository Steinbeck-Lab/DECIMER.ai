import { useId, useState } from 'react';
import { strings } from '../strings';
import { IconChevronRight } from '../ui/icons';

/**
 * "Display uploaded document" (legacy's toggle): the pages of the upload, side by side in a strip that scrolls
 * inside its own box. Shut, it renders no image at all, so no page is fetched until someone asks for it; open, the
 * images load lazily as the strip is scrolled. The strip is focusable, so the arrow keys scroll it.
 */
export function UploadedDocument({ pageUrls }: { pageUrls: readonly string[] }) {
  const [open, setOpen] = useState(false);
  // A page the worker could not render has no file, so its image fails: say so in its place.
  const [missing, setMissing] = useState<ReadonlySet<number>>(new Set());
  const stripId = useId();
  const total = pageUrls.length;
  if (total === 0) return null;

  return (
    <div className="dc-doc">
      <button
        type="button"
        className="dc-doc__toggle"
        aria-expanded={open}
        aria-controls={stripId}
        onClick={() => setOpen((was) => !was)}
      >
        <IconChevronRight size={18} className="dc-doc__chevron" />
        {strings.results.document.toggle}
      </button>
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
    </div>
  );
}
