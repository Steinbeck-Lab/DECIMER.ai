import { strings } from '../strings';
import { useRenderedSize } from '../ui/useRenderedSize';

/** How far outside its tile the frame stands, in px (the home page's drop zone stands 7 px out). */
const OUTSET = 8;

/**
 * The inspection frame of the selected result: the drop zone's frame (the `.dc-frame` rules of home.css: four edges,
 * four corner handles, a label with the real size) laid around a tile, with page guides that stop at uneven lengths.
 * `dc-frame--sel` (figure.css) sets what differs: it is drawn in a quarter of a second, the label stands on the top
 * edge and the garnet tint comes with the pointer or keyboard focus on the tile. Decoration only: hidden from
 * assistive technology, never in the way of the pointer. It measures itself, so the label prints the tile's size on
 * screen.
 */
export function SelectionFrame({ number }: { number: number }) {
  const [ref, size] = useRenderedSize<HTMLDivElement>();
  return (
    <div className="dc-frame dc-frame--sel" ref={ref} aria-hidden="true" style={{ inset: -OUTSET }}>
      <span className="dc-frame__guide dc-frame__guide--top" />
      <span className="dc-frame__guide dc-frame__guide--bottom" />
      <span className="dc-frame__guide dc-frame__guide--left" />
      <span className="dc-frame__guide dc-frame__guide--right" />
      <span className="dc-frame__line dc-frame__line--top" />
      <span className="dc-frame__line dc-frame__line--bottom" />
      <span className="dc-frame__line dc-frame__line--left" />
      <span className="dc-frame__line dc-frame__line--right" />
      <span className="dc-frame__handle dc-frame__handle--tl" />
      <span className="dc-frame__handle dc-frame__handle--tr" />
      <span className="dc-frame__handle dc-frame__handle--bl" />
      <span className="dc-frame__handle dc-frame__handle--br" />
      {size && (
        <span className="dc-frame__label">
          {strings.results.frameLabel(number, size.width - 2 * OUTSET, size.height - 2 * OUTSET)}
        </span>
      )}
    </div>
  );
}
