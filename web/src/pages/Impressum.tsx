import { strings } from '../strings';
import { StaticPage } from './StaticPage';

const t = strings.pages.impressum;

export function Impressum() {
  return (
    <StaticPage title={t.title} note={t.note}>
      {t.sections.map((section) => (
        <section key={section.title}>
          <h2>{section.title}</h2>
          <p>
            {section.lines.map((line) => (
              <span key={line} className="dc-page__line">
                {line}
              </span>
            ))}
          </p>
        </section>
      ))}
    </StaticPage>
  );
}
