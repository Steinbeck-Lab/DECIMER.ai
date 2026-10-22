import { strings } from '../strings';
import { StaticPage } from './StaticPage';

const t = strings.pages.privacy;

export function Privacy() {
  return (
    <StaticPage title={t.title} note={strings.pages.draftNotePrivacy}>
      {t.sections.map((section) => (
        <section key={section.title}>
          <h2>{section.title}</h2>
          {section.body.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </section>
      ))}
    </StaticPage>
  );
}
