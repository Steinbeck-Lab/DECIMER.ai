import wordmark from '../assets/wordmark.png';
import { strings } from '../strings';

interface WordmarkProps {
  /** Height in CSS px; the width follows. */
  height?: number;
  /** The big logo of the empty screen. */
  hero?: boolean;
  /**
   * The wordmark filling the width of its parent in velvet forest (the About page's inspected specimen): the same
   * file, used as a mask. Decorative (the page's heading says the name), so it carries no text of its own.
   */
  fill?: boolean;
}

/**
 * The DECIMER wordmark (DEC|MER). It is black on transparent, cut from the
 * legacy `DECIMER_Clean.png` (word only: the tagline is real text).
 */
export function Wordmark({ height = 32, hero = false, fill = false }: WordmarkProps) {
  if (fill) return <span className="dc-wordmark dc-wordmark--fill" aria-hidden="true" />;
  if (hero) {
    // 1200 by 304 is the file's own size; CSS sets the displayed width and places it where the first-visit GIF ends.
    return (
      <img
        className="dc-wordmark dc-wordmark--hero"
        src={wordmark}
        alt={strings.appName}
        width={1200}
        height={304}
      />
    );
  }
  return (
    <img
      className="dc-wordmark"
      src={wordmark}
      alt={strings.appName}
      height={height}
      style={{ height, width: 'auto' }}
    />
  );
}
