import wordmark from '../assets/wordmark.png';
import { strings } from '../strings';

interface WordmarkProps {
  /** Height in CSS px; the width follows. */
  height?: number;
  /** The big logo of the empty screen. */
  hero?: boolean;
}

/**
 * The DECIMER wordmark (DEC|MER). It is black on transparent, cut from the
 * legacy `DECIMER_Clean.png` (word only: the tagline is real text).
 */
export function Wordmark({ height = 32, hero = false }: WordmarkProps) {
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
