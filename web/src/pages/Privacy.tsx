import { strings } from '../strings';
import { RichText } from './Rich';
import type { Rich } from './Rich';
import { SectionView } from './Sections';
import type { Section } from './Sections';
import { StaticPage } from './StaticPage';

const t = strings.pages.privacy;

const intro: readonly Rich[] = t.intro;
const sections: readonly Section[] = t.sections;
const closing: readonly Rich[] = t.closing;

export function Privacy() {
  return (
    <StaticPage title={t.title}>
      {intro.map((paragraph, i) => (
        <p key={i}>
          <RichText value={paragraph} />
        </p>
      ))}
      {sections.map((section) => (
        <SectionView key={section.title} section={section} />
      ))}
      {closing.map((paragraph, i) => (
        <p key={i} className="dc-page__credit">
          <RichText value={paragraph} />
        </p>
      ))}
    </StaticPage>
  );
}
