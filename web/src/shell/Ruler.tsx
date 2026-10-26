/** Mono tick labels down the left edge: 64, 128, 192, ... (the ticks themselves are the background, see shell.css). */
const LABELS = Array.from({ length: 36 }, (_, i) => (i + 1) * 64);

/**
 * The thin ruler along the left edge of the page: the page is a measured sheet. Decorative (hidden from assistive
 * technology, and from phones by CSS); it is fixed to the window, so it measures the screen, not the document.
 */
export function Ruler() {
  return (
    <div className="dc-ruler" aria-hidden="true">
      {LABELS.map((n) => (
        <span key={n} style={{ top: n - 6 }}>
          {n}
        </span>
      ))}
    </div>
  );
}
