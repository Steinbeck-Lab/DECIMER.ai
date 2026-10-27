import { useState } from 'react';

interface Props {
  /** What the slot says now. This is the only text in the document; everything else in the slot is drawn by CSS. */
  value: string;
  /**
   * Every string the slot can say. They are laid out, unseen, in the same grid cell as the text, so the slot is as
   * tall (and as wide) as the largest of them at the current width and never changes size when the text does. They
   * are written by CSS (`content: attr(data-text)`), so they are neither text in the document nor read out.
   */
  sizers: readonly string[];
  /** Where a text shorter than the slot sits in it. Default: at the start (a heading sits at the end, next to what follows). */
  align?: 'start' | 'end';
}

/**
 * A line of text that changes without moving anything: the slot keeps its size (see `sizers`) and a new text takes the
 * place of the old one in it, the old leaving upward and the new arriving from a quarter of a line below (opacity and
 * transform only, the rules are `.dc-steady` in run.css). The first text is simply there. Under reduced motion the
 * change is a plain cross-fade.
 */
export function Steady({ value, sizers, align = 'start' }: Props) {
  // The text that was there before the last change, to be faded out beneath the new one (derived state, set in render).
  const [shown, setShown] = useState(value);
  const [gone, setGone] = useState<string | null>(null);
  if (value !== shown) {
    setShown(value);
    setGone(shown);
  }
  return (
    <span className="dc-steady" data-align={align}>
      {[...new Set(sizers)].map((text) => (
        <span key={text} className="dc-steady__sizer" data-text={text} aria-hidden="true" />
      ))}
      {gone !== null && gone !== '' && <span key={`gone ${gone}`} className="dc-steady__gone" data-text={gone} aria-hidden="true" />}
      <span key={value} className={gone === null ? 'dc-steady__now' : 'dc-steady__now dc-steady__now--arriving'}>
        {value}
      </span>
    </span>
  );
}
