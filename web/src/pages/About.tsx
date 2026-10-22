import { strings } from '../strings';
import { IconExternal } from '../ui/icons';
import { StaticPage } from './StaticPage';

const t = strings.pages.about;

export function About() {
  return (
    <StaticPage title={t.title} note={strings.pages.draftNote}>
      <p className="dc-page__lead">{t.lead}</p>

      <h2>{t.howTitle}</h2>
      {t.how.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}

      <h2>{t.limitsTitle}</h2>
      <p>{t.limits}</p>

      <h2>{t.componentsTitle}</h2>
      <ul className="dc-page__list">
        {t.components.map((c) => (
          <li key={c.name}>
            <a href={c.url} target="_blank" rel="noreferrer">
              {c.name}
              <IconExternal size={14} />
            </a>
            <span>{c.what}</span>
          </li>
        ))}
      </ul>

      <h2>{t.citeTitle}</h2>
      <p>{t.citeLead}</p>
      <ol className="dc-page__citations">
        {t.citations.map((c) => (
          <li key={c.doi}>
            <a href={c.doi} target="_blank" rel="noreferrer">
              {c.title}
            </a>
            <span>
              {c.authors} {c.where}.
            </span>
          </li>
        ))}
      </ol>
      <p>
        <a href={t.moreResearchUrl} target="_blank" rel="noreferrer">
          {t.moreResearch}
          <IconExternal size={14} />
        </a>
      </p>

      <h2>{t.aboutTitle}</h2>
      {t.aboutBody.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
    </StaticPage>
  );
}
