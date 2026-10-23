import type { Rich } from './Rich';
import { RichText } from './Rich';

/** One block of a section: a paragraph, address-like lines, a bullet list, or a paragraph in bold. */
export type Block =
  | Rich
  | { readonly lines: readonly Rich[] }
  | { readonly list: readonly Rich[] }
  | { readonly strong: Rich };

export interface Section {
  readonly title: string;
  /** Language of the section when it is not English (BCP 47, e.g. `de`). */
  readonly lang?: string;
  readonly content?: readonly Block[];
  readonly sections?: readonly Section[];
}

function BlockView({ block }: { block: Block }) {
  if (typeof block === 'string' || Array.isArray(block)) {
    return (
      <p>
        <RichText value={block as Rich} />
      </p>
    );
  }
  const b = block as Exclude<Block, Rich>;
  if ('lines' in b) {
    return (
      <p>
        {b.lines.map((line, i) => (
          <span key={i} className="dc-page__line">
            <RichText value={line} />
          </span>
        ))}
      </p>
    );
  }
  if ('list' in b) {
    return (
      <ul className="dc-page__bullets">
        {b.list.map((item, i) => (
          <li key={i}>
            <RichText value={item} />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <p>
      <strong>
        <RichText value={b.strong} />
      </strong>
    </p>
  );
}

/** A section under the page title: its title is an h2, a sub-section's an h3 (the order of headings never skips). */
export function SectionView({ section, level = 2 }: { section: Section; level?: 2 | 3 }) {
  const Heading = level === 2 ? 'h2' : 'h3';
  return (
    <section lang={section.lang}>
      <Heading>{section.title}</Heading>
      {section.content?.map((block, i) => <BlockView key={i} block={block} />)}
      {section.sections?.map((sub) => <SectionView key={sub.title} section={sub} level={3} />)}
    </section>
  );
}
