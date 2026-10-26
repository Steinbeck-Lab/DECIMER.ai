import type { ReactNode, Ref } from 'react';

type Edge = 'top' | 'bottom' | 'left' | 'right';

interface Props {
  /** A modifier class: what differs from the drop zone's frame (where it stands, how far its guides run, how fast it is drawn). */
  className?: string;
  /** Measures the frame (see `useRenderedSize`): its size is what a label or a caption prints. */
  frameRef?: Ref<HTMLDivElement>;
  /** Marks drawn before the rest of the frame (so beneath it). */
  children?: ReactNode;
  /** The page guides to draw, along the edges named, in this order. */
  guides?: readonly Edge[];
  /** The dimension line under the frame, and the one beside it. */
  dimWidth?: boolean;
  dimHeight?: boolean;
  /** Captions for the dimension lines: the width one is centred under its line, the height one beside its line. */
  captionWidth?: ReactNode;
  captionHeight?: ReactNode;
  /** The chip on the top edge. Left out (while a size is still being measured, say) it is not drawn. */
  label?: ReactNode;
}

/**
 * The copper inspection frame, once: four edges, four corner handles, and as many of the page guides, dimension lines,
 * captions and chip as a use asks for. The drop zone (UploadZone), the uploaded document (Specimen), the selected
 * result (SelectionFrame), the About wordmark and the footer's credit all render it. Every rule is `.dc-frame` in
 * home.css (edges, handles, label, guides, the draw-in on load and the reduced-motion rule); a modifier class in the
 * stylesheet of the use says what differs. Decoration only: hidden from assistive technology and never in the way of
 * the pointer. It fills its positioned parent (`inset: 0`, or what a modifier sets).
 */
export function InspectionFrame({
  className,
  frameRef,
  children,
  guides = [],
  dimWidth = false,
  dimHeight = false,
  captionWidth,
  captionHeight,
  label,
}: Props) {
  return (
    <div className={className ? `dc-frame ${className}` : 'dc-frame'} ref={frameRef} aria-hidden="true">
      {children}
      {guides.map((edge) => (
        <span key={edge} className={`dc-frame__guide dc-frame__guide--${edge}`} />
      ))}
      {dimWidth && <span className="dc-frame__dim dc-frame__dim--width" />}
      {dimHeight && <span className="dc-frame__dim dc-frame__dim--height" />}
      {captionWidth && <span className="dc-frame__caption dc-frame__caption--width">{captionWidth}</span>}
      {captionHeight && <span className="dc-frame__caption dc-frame__caption--height">{captionHeight}</span>}
      <span className="dc-frame__line dc-frame__line--top" />
      <span className="dc-frame__line dc-frame__line--bottom" />
      <span className="dc-frame__line dc-frame__line--left" />
      <span className="dc-frame__line dc-frame__line--right" />
      <span className="dc-frame__handle dc-frame__handle--tl" />
      <span className="dc-frame__handle dc-frame__handle--tr" />
      <span className="dc-frame__handle dc-frame__handle--bl" />
      <span className="dc-frame__handle dc-frame__handle--br" />
      {label && <span className="dc-frame__label">{label}</span>}
    </div>
  );
}
