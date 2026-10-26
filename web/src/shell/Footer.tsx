import { strings } from '../strings';
import { InspectionFrame } from '../ui/InspectionFrame';
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

/**
 * The footer, drawn as a piece of the measured sheet: a copper measuring line with ticks across the top, and under it
 * the credit and the copyright inside a copper inspection frame whose guides rise to meet that line (shell.css; the
 * frame is the drop zone's, home.css). Then, on one line, the legal pages, GitHub and who develops the site. About is
 * not here: it is in the header. `compact` is the same design with shorter guides and less air, for the empty screen,
 * whose first screen must fit a laptop window without scrolling.
 */
export function Footer({ compact = false }: { compact?: boolean }) {
  const c = strings.shell.credit;
  return (
    <footer className="dc-footer" data-compact={compact}>
      <div className="dc-footer__inner">
        <span className="dc-measure dc-measure--major" aria-hidden="true" />
        <div className="dc-footer__made">
          <InspectionFrame className="dc-frame--credit" guides={['top', 'bottom']}>
            <span className="dc-frame__rise dc-frame__rise--left" />
            <span className="dc-frame__rise dc-frame__rise--right" />
            <span className="dc-frame__rise-head dc-frame__rise-head--left" />
            <span className="dc-frame__rise-head dc-frame__rise-head--right" />
          </InspectionFrame>
          <p className="dc-footer__coffee">
            {strings.shell.builtWith} <CoffeeCup />
            <span className="dc-sr">{strings.shell.builtWithIcon}</span> {strings.shell.builtBy}
          </p>
          <p className="dc-footer__legal">{strings.shell.copyright(new Date().getFullYear())}</p>
        </div>
        <div className="dc-footer__line">
          <nav className="dc-footer__nav" aria-label={strings.shell.footerNav}>
            <a href={hrefFor('/privacy')}>{strings.shell.privacy}</a>
            <a href={hrefFor('/impressum')} lang="de">
              {strings.shell.impressum}
            </a>
            <a href={strings.shell.githubUrl} target="_blank" rel="noreferrer">
              {strings.shell.github}
            </a>
          </nav>
          <p className="dc-footer__dev">
            {c.before}
            <a href={c.groupUrl}>{c.group}</a>
            {c.at}
            <a href={c.universityUrl}>{c.university}</a>
            {c.after} <a href={strings.shell.contactUrl}>{strings.shell.contact}</a>{' '}
            <a href={strings.shell.issuesUrl}>{strings.shell.reportIssue}</a>.
          </p>
        </div>
      </div>
    </footer>
  );
}
