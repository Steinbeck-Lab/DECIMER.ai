import { strings } from '../strings';
import { RichText } from './Rich';
import type { Rich } from './Rich';
import { SectionView } from './Sections';
import type { Section } from './Sections';
import { StaticPage } from './StaticPage';

const t = strings.pages.impressum;

const sections: readonly Section[] = t.sections;
const closing: readonly Rich[] = t.closing;

/** The legal disclosure the law asks for. The text is German; only the lead-in is English. */
export function Impressum() {
  return (
    <StaticPage title={t.title} titleLang="de">
      <p>{t.lead}</p>
      {sections.map((section) => (
        <SectionView key={section.title} section={section} />
      ))}
      {closing.map((paragraph, i) => (
        <p key={i} lang="de" className="dc-page__credit">
          <RichText value={paragraph} />
        </p>
      ))}
    </StaticPage>
  );
}
