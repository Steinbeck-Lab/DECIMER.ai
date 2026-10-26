import { strings } from '../strings';
import { InspectionFrame } from '../ui/InspectionFrame';
import { useRenderedSize } from '../ui/useRenderedSize';

/** How far outside its tile the frame stands, in px: `inset` of `.dc-frame--sel` in figure.css says the same (the drop zone stands 7 px out). */
const OUTSET = 8;

/**
 * The inspection frame of the selected result: the drop zone's frame (InspectionFrame, and the `.dc-frame` rules of
 * home.css: four edges, four corner handles, a label with the real size) laid around a tile, with page guides that stop
 * at uneven lengths.
 * `dc-frame--sel` (figure.css) sets what differs: it is drawn in a quarter of a second, the label stands on the top
 * edge and the garnet tint comes with the pointer or keyboard focus on the tile. Decoration only: hidden from
 * assistive technology, never in the way of the pointer. It measures itself, so the label prints the tile's size on
 * screen.
 */
export function SelectionFrame({ number }: { number: number }) {
  const [ref, size] = useRenderedSize<HTMLDivElement>();
  return (
    <InspectionFrame
      className="dc-frame--sel"
      frameRef={ref}
      guides={['top', 'bottom', 'left', 'right']}
      label={size && strings.results.frameLabel(number, size.width - 2 * OUTSET, size.height - 2 * OUTSET)}
    />
  );
}
