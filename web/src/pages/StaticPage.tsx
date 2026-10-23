import type { ReactNode } from 'react';
import { strings } from '../strings';
import { hrefFor } from '../shell/router';
import { RichText } from './Rich';
import type { Rich } from './Rich';

/**
 * The shell of About, Privacy and Impressum: one readable column and a title with the figure rule. `note` is a callout
 * for a notice that applies to the whole page (the review the text still needs); `titleLang` marks a title that is not
 * English.
 */
export function StaticPage({
  title,
  titleLang,
  note,
  children,
}: {
  title: string;
  titleLang?: string;
  note?: Rich;
  children: ReactNode;
}) {
  return (
    <main id="main" tabIndex={-1} className="dc-page">
      <header className="dc-page__head">
        <h1 className="dc-page__title" lang={titleLang}>
          {title}
        </h1>
      </header>
      {note && (
        <p className="dc-callout" data-kind="warning" role="note">
          <RichText value={note} />
        </p>
      )}
      <div className="dc-page__body">{children}</div>
      <p className="dc-page__back">
        <a href={hrefFor('/')}>{strings.pages.back}</a>
      </p>
    </main>
  );
}
