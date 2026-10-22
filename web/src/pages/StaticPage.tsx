import type { ReactNode } from 'react';
import { strings } from '../strings';
import { hrefFor } from '../shell/router';

/** The shell of About, Privacy and Impressum: one readable column, a title with the figure rule, a draft note. */
export function StaticPage({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <main id="main" tabIndex={-1} className="dc-page">
      <header className="dc-page__head">
        <h1 className="dc-page__title">{title}</h1>
      </header>
      <p className="dc-callout" data-kind="warning" role="note">
        {note}
      </p>
      <div className="dc-page__body">{children}</div>
      <p className="dc-page__back">
        <a href={hrefFor('/')}>{strings.pages.back}</a>
      </p>
    </main>
  );
}
