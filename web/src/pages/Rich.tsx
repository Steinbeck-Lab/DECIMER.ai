import type { ReactNode } from 'react';
import { strings } from '../strings';
import { hrefFor } from '../shell/router';
import type { Route } from '../shell/router';
import { IconExternal } from '../ui/icons';

/** The parts of a paragraph with links: plain text, a link, a link to another page of this site, emphasis. */
export type RichPart =
  | string
  | { readonly text: string; readonly href: string; readonly lang?: string }
  | { readonly text: string; readonly route: Route }
  | { readonly em: string }
  | { readonly strong: string };

export type Rich = string | readonly RichPart[];

/**
 * `[CONFIRM: ...]` marks copy nobody has confirmed yet (see the `pages` comment in strings.ts). The release gate
 * (release.test.ts) looks for `[CONFIRM: ` and a letter in the build, so this file must never spell that out.
 */
export const CONFIRM_MARKER = /\[CONFIRM: [^\]]*\]/g;

/** Text, with each `[CONFIRM: ...]` marker highlighted so that a reviewer cannot miss it. */
function Text({ children }: { children: string }) {
  const pieces = children.split(/(\[CONFIRM: [^\]]*\])/);
  return (
    <>
      {pieces.map((piece, i) =>
        piece.startsWith('[CONFIRM: ') ? (
          <mark key={i} className="dc-confirm">
            {piece}
          </mark>
        ) : (
          piece
        ),
      )}
    </>
  );
}

interface LinkProps {
  text: string;
  href: string;
  lang?: string;
  className?: string;
  /** The id of the element that says what the link is about (a link whose text is only a DOI, say, is described by the title). */
  describedBy?: string;
  /** What follows the text of an external link instead of the small "opens in a new tab" icon (the round icon of a pill). */
  trail?: ReactNode;
}

/** A link. One that leaves the site opens in a new tab, and says so with an icon and, to a screen reader, in words. */
export function Link({ text, href, lang, className, describedBy, trail }: LinkProps) {
  if (!/^https?:/.test(href)) {
    return (
      <a href={href} lang={lang} className={className} aria-describedby={describedBy}>
        {text}
      </a>
    );
  }
  return (
    <a href={href} lang={lang} className={className} aria-describedby={describedBy} target="_blank" rel="noreferrer">
      {text}
      {trail ?? <IconExternal size={14} />}
      <span className="dc-sr"> {strings.editor.external}</span>
    </a>
  );
}

function Part({ part }: { part: RichPart }) {
  if (typeof part === 'string') return <Text>{part}</Text>;
  if ('route' in part) return <a href={hrefFor(part.route)}>{part.text}</a>;
  if ('href' in part) return <Link {...part} />;
  if ('em' in part) return <em>{part.em}</em>;
  return <strong>{part.strong}</strong>;
}

export function RichText({ value }: { value: Rich }) {
  if (typeof value === 'string') return <Text>{value}</Text>;
  return (
    <>
      {value.map((part, i) => (
        <Part key={i} part={part} />
      ))}
    </>
  );
}
