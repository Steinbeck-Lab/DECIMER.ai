import { strings } from '../strings';
import { hrefFor } from './router';

/**
 * A copper cup with two lines of steam that rise and fade (still under reduced motion). Decorative: the text beside it
 * says "coffee". Each line of steam is an <svg> of its own, because an animation on a shape inside an SVG runs on the
 * main thread and lays the page out every frame, while one on an <svg> box runs on the compositor.
 */
function CoffeeCup() {
  const common = {
    width: 22,
    height: 24,
    viewBox: '0 0 22 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round',
    focusable: 'false',
  } as const;
  return (
    <span className="dc-cup" aria-hidden="true">
      <svg {...common}>
        <path d="M3 11h13v5a5 5 0 01-5 5H8a5 5 0 01-5-5v-5z" />
        <path d="M16 12.5h1.5a2.5 2.5 0 010 5H16" />
      </svg>
      <svg {...common} className="dc-cup__steam">
        <path d="M7 8c-1.2-1.4 1.2-2.6 0-4" />
      </svg>
      <svg {...common} className="dc-cup__steam dc-cup__steam--late">
        <path d="M11 8c-1.2-1.4 1.2-2.6 0-4" />
      </svg>
    </span>
  );
}

/** A link that leaves the site: a new tab, the referrer kept to itself, and a screen reader is told. */
function External({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
      <span className="dc-sr"> {strings.editor.external}</span>
    </a>
  );
}

/**
 * The footer, drawn as a piece of the measured sheet: a copper measuring line with ticks across the top, and under it,
 * on one row, the copyright (flush left), the coffee credit (centred on the page, on one line) and Contact, Privacy and
 * Impressum (flush right). About and GitHub are not here: they are in the header. Where the three do not fit side by
 * side the copyright and the links share the first row and the credit is the second; on a phone they are stacked, in the
 * order they are read in. `compact` is the same design with less air, for the empty screen, whose first
 * screen must fit a laptop window without scrolling.
 */
export function Footer({ compact = false }: { compact?: boolean }) {
  const c = strings.shell.credit;
  return (
    <footer className="dc-footer" data-compact={compact}>
      <div className="dc-footer__inner">
        <span className="dc-measure dc-measure--major" aria-hidden="true" />
        <div className="dc-footer__line">
          <p className="dc-footer__legal">{strings.shell.copyright(new Date().getFullYear())}</p>
          <p className="dc-footer__made">
            {strings.shell.builtWith} <CoffeeCup />
            <span className="dc-sr">{strings.shell.builtWithIcon}</span> {strings.shell.builtBy}{' '}
            <External href={strings.shell.builtByUrl}>{strings.shell.builtByName}</External> {c.at}{' '}
            <External href={c.groupUrl}>{c.group}</External>, <External href={c.universityUrl}>{c.university}</External>
          </p>
          <nav className="dc-footer__nav" aria-label={strings.shell.footerNav}>
            <a href={strings.shell.contactUrl}>{strings.shell.contact}</a>
            <a href={hrefFor('/privacy')}>{strings.shell.privacy}</a>
            <a href={hrefFor('/impressum')} lang="de">
              {strings.shell.impressum}
            </a>
          </nav>
        </div>
      </div>
    </footer>
  );
}
