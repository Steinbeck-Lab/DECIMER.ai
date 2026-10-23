import { strings } from '../strings';
import { Link, RichText } from './Rich';
import type { Rich } from './Rich';
import { SectionView } from './Sections';
import type { Section } from './Sections';
import { StaticPage } from './StaticPage';

const t = strings.pages.about;

interface Citation {
  readonly title: string;
  readonly authors: string;
  readonly where: Rich;
  readonly doi: string;
}

const citations: readonly Citation[] = t.citations;
const before: readonly Section[] = t.before;
const after: readonly Section[] = t.after;

export function About() {
  return (
    <StaticPage title={t.title}>
      <p className="dc-page__lead">{t.lead}</p>

      {before.map((section) => (
        <SectionView key={section.title} section={section} />
      ))}

      <h2>{t.componentsTitle}</h2>
      <ul className="dc-page__list">
        {t.components.map((c) => (
          <li key={c.name}>
            <Link href={c.url} text={c.name} />
            <span>{c.what}</span>
          </li>
        ))}
      </ul>

      <h2>{t.citeTitle}</h2>
      <p>{t.citeLead}</p>
      <ol className="dc-page__citations">
        {citations.map((c) => (
          <li key={c.doi}>
            <Link href={c.doi} text={c.title} />
            <span>
              {c.authors} <RichText value={c.where} />
            </span>
          </li>
        ))}
      </ol>
      <p className="dc-page__more">
        <Link href={t.moreResearchUrl} text={t.moreResearch} />
      </p>

      {after.map((section) => (
        <SectionView key={section.title} section={section} />
      ))}
    </StaticPage>
  );
}
