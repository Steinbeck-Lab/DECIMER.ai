import { strings } from '../strings';
import { IconCoffee } from '../ui/icons';
import { hrefFor } from './router';

export function Footer() {
  const c = strings.shell.credit;
  return (
    <footer className="dc-footer">
      <p className="dc-footer__credit">
        {c.before}
        <a href={c.groupUrl}>{c.group}</a>
        {c.at}
        <a href={c.universityUrl}>{c.university}</a>
        {c.after} <a href={strings.shell.contactUrl}>{strings.shell.contact}</a>{' '}
        <a href={strings.shell.issuesUrl}>{strings.shell.reportIssue}</a>.
      </p>
      <div className="dc-footer__row">
        <p className="dc-footer__coffee">
          {strings.shell.builtWith}{' '}
          <IconCoffee size={16} className="dc-footer__cup" />
          <span className="dc-sr">{strings.shell.builtWithIcon}</span> {strings.shell.builtBy}
        </p>
        <nav className="dc-footer__nav" aria-label={strings.shell.footerNav}>
          <a href={hrefFor('/about')}>{strings.shell.about}</a>
          <a href={hrefFor('/privacy')}>{strings.shell.privacy}</a>
          <a href={hrefFor('/impressum')} lang="de">
            {strings.shell.impressum}
          </a>
          <a href={strings.shell.githubUrl} target="_blank" rel="noreferrer">
            {strings.shell.github}
          </a>
        </nav>
      </div>
    </footer>
  );
}
