import { useId } from 'react';
import { Wordmark } from '../brand/Wordmark';
import { strings } from '../strings';
import { IconArrowRight, PillIcon } from '../ui/icons';
import { InspectionFrame } from '../ui/InspectionFrame';
import { useRenderedSize } from '../ui/useRenderedSize';
import { Link, RichText } from './Rich';
import type { Rich } from './Rich';
import { BlockView } from './Sections';
import type { Section } from './Sections';

const t = strings.pages.about;

interface Citation {
  readonly title: string;
  readonly authors: string;
  readonly where: Rich;
  readonly doi: string;
}

interface Component {
  readonly name: string;
  readonly what: string;
  readonly url: string;
  readonly licence?: string;
  /** Not one of the three DECIMER engines: the tile is tinted egg nog. */
  readonly ext?: boolean;
}

const citations: readonly Citation[] = t.citations;
const components: readonly Component[] = t.components;
const before: readonly Section[] = t.before;
const after: readonly Section[] = t.after;

/** "10.1038/s41467-023-40782-0" from "https://doi.org/10.1038/s41467-023-40782-0". */
const doiOf = (url: string) => url.replace(/^https?:\/\/(dx\.)?doi\.org\//, '');

/**
 * The wordmark as a specimen under inspection: the drop zone's copper frame (home.css) 7 px out, its chip, page guides
 * that stop at uneven lengths, and dimension lines with the size the wordmark is drawn at. Decoration (the heading says
 * the name), so it is hidden from assistive technology.
 */
function Specimen() {
  const [frameRef, size] = useRenderedSize<HTMLDivElement>();
  return (
    <figure className="dc-about__specimen" aria-hidden="true">
      <div className="dc-about__framed">
        <InspectionFrame
          className="dc-frame--about"
          frameRef={frameRef}
          guides={['top', 'bottom', 'left', 'right']}
          dimWidth
          dimHeight
          captionWidth={size && t.frameSize(size.width, size.height)}
          label={t.frameLabel}
        />
        <Wordmark fill />
      </div>
    </figure>
  );
}

function Tile({ section, className }: { section: Section; className: string }) {
  return (
    <section className={`dc-about__tile ${className}`} lang={section.lang}>
      <h2>{section.title}</h2>
      {section.content?.map((block, i) => <BlockView key={i} block={block} />)}
    </section>
  );
}

/** The limits as big figures on a short measuring line, then the sentence that says the same in words. */
function Limits({ section }: { section: Section }) {
  return (
    <section className="dc-about__tile dc-about__limits">
      <h2>{section.title}</h2>
      <dl className="dc-about__figures" aria-hidden="true">
        {t.limitFigures.map((figure) => (
          <div key={figure.unit}>
            <dt>{figure.unit}</dt>
            <dd>{figure.value}</dd>
          </div>
        ))}
      </dl>
      <span className="dc-measure dc-measure--thirds dc-about__ticks" aria-hidden="true" />
      {section.content?.map((block, i) => <BlockView key={i} block={block} />)}
    </section>
  );
}

/** One publication as a card. Its link is the DOI, so the link is described by the title above it. */
function CitationCard({ citation: c }: { citation: Citation }) {
  const titleId = useId();
  return (
    <li className="dc-cite">
      <h3 id={titleId}>{c.title}</h3>
      <p className="dc-cite__authors">{c.authors}</p>
      <p className="dc-cite__where">
        <RichText value={c.where} />
      </p>
      <p className="dc-cite__doi">
        <Link href={c.doi} text={doiOf(c.doi)} describedBy={titleId} />
      </p>
    </li>
  );
}

export function About() {
  const [how, limits] = before;
  const [about] = after;
  return (
    <main id="main" tabIndex={-1} className="dc-about">
      <div className="dc-about__inner">
        <div className="dc-about__hero">
          <div>
            <h1 className="dc-about__title">{t.title}</h1>
            <p className="dc-about__lead">{t.lead}</p>
          </div>
          <Specimen />
        </div>

        <div className="dc-about__bento">
          {how && <Tile section={how} className="dc-about__how" />}
          {limits && <Limits section={limits} />}
          {about && <Tile section={about} className="dc-about__about" />}
        </div>

        <section className="dc-about__sec">
          <h2>{t.componentsTitle}</h2>
          <div className="dc-pipe">
            <span className="dc-pipe__io dc-pipe__io--in" aria-hidden="true">
              {t.componentsIn}
            </span>
            <span className="dc-pipe__io dc-pipe__io--out" aria-hidden="true">
              {t.componentsOut}
            </span>
            <span className="dc-measure dc-pipe__rule" aria-hidden="true" />
            <ol className="dc-pipe__stages">
              {components.map((c, i) => (
                <li key={c.name} className="dc-stage" data-ext={c.ext === true}>
                  <div className="dc-stage__tile">
                    <h3>
                      <Link href={c.url} text={c.name} />
                    </h3>
                    <p>{c.what}</p>
                    {c.licence && <span className="dc-stage__licence">{c.licence}</span>}
                  </div>
                  {i === components.length - 1 && <span className="dc-stage__end" aria-hidden="true" />}
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="dc-about__sec">
          <h2>{t.citeTitle}</h2>
          <p className="dc-about__intro">{t.citeLead}</p>
          <ol className="dc-cites">
            {citations.map((c) => (
              <CitationCard key={c.doi} citation={c} />
            ))}
          </ol>
          <p className="dc-about__more">
            <Link
              className="dc-pill"
              href={t.moreResearchUrl}
              text={t.moreResearch}
              trail={
                <PillIcon>
                  <IconArrowRight size={16} strokeWidth={1.8} />
                </PillIcon>
              }
            />
          </p>
        </section>
      </div>
    </main>
  );
}
