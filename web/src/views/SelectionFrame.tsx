import { strings } from '../strings';
import { useRenderedSize } from '../ui/useRenderedSize';

/** How far outside its tile the frame stands, in px (the home page's drop zone stands 7 px out). */
const OUTSET = 8;

/**
 * The inspection frame of the selected result: the home page's copper frame (four edges, four corner handles, a label
 * with the real size) laid around a tile, with page guides that stop at uneven lengths. It is drawn quickly when the
 * tile is selected (figure.css) and stands still after. Decoration only: hidden from assistive technology, never in
 * the way of the pointer. It measures itself, so the label prints the tile's size on screen.
 */
export function SelectionFrame({ number }: { number: number }) {
  const [ref, size] = useRenderedSize<HTMLSpanElement>();
  return (
    <span className="dc-sel" ref={ref} aria-hidden="true" style={{ inset: -OUTSET }}>
      <span className="dc-sel__guide dc-sel__guide--top" />
      <span className="dc-sel__guide dc-sel__guide--bottom" />
      <span className="dc-sel__guide dc-sel__guide--left" />
      <span className="dc-sel__guide dc-sel__guide--right" />
      <span className="dc-sel__line dc-sel__line--top" />
      <span className="dc-sel__line dc-sel__line--bottom" />
      <span className="dc-sel__line dc-sel__line--left" />
      <span className="dc-sel__line dc-sel__line--right" />
      <span className="dc-sel__handle dc-sel__handle--tl" />
      <span className="dc-sel__handle dc-sel__handle--tr" />
      <span className="dc-sel__handle dc-sel__handle--bl" />
      <span className="dc-sel__handle dc-sel__handle--br" />
      {size && (
        <span className="dc-sel__label">
          {strings.results.frameLabel(number, size.width - 2 * OUTSET, size.height - 2 * OUTSET)}
        </span>
      )}
    </span>
  );
}
