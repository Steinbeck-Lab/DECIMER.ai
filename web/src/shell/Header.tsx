import { useState } from 'react';
import { Wordmark } from '../brand/Wordmark';
import { palette } from '../theme';
import { strings } from '../strings';
import { hasResults, hasWork, isPdfUpload, workSummary } from '../state/selectors';
import { useAppState, useController } from '../state/react';
import { IconGithub, IconInfo } from '../ui/icons';
import { ClothRibbon } from './ClothRibbon';
import { ConfirmNewUpload } from './ConfirmNewUpload';
import { DownloadMenu } from './DownloadMenu';
import { hrefFor, navigate, type Route } from './router';

export function Header({ route }: { route: Route }) {
  const state = useAppState();
  const controller = useController();
  const [confirming, setConfirming] = useState(false);

  const home = route === '/';
  // The big logo of the empty screen already is the wordmark, and its About and GitHub hang from the top edge as ribbons.
  const empty = home && state.phase === 'empty';
  const showNewUpload = state.phase !== 'empty';
  const work = workSummary(state.edits);

  const startNew = () => {
    controller.startOver();
    setConfirming(false);
    navigate('/');
  };

  return (
    <header className="dc-header" data-ribbons={empty}>
      {!empty && (
        <a className="dc-header__logo" href={hrefFor('/')} aria-label={strings.shell.home}>
          <Wordmark height={32} />
        </a>
      )}
      {hasResults(state) && state.job && (
        <span className="dc-capacity">
          {strings.results.capacity(state.job.results.length, state.job.pagesProcessed, isPdfUpload(state.sources))}
        </span>
      )}
      {empty ? (
        <nav className="dc-ribbons" aria-label={strings.shell.mainNav}>
          <ClothRibbon
            href={hrefFor('/about')}
            name={strings.shell.about}
            color={palette.copper}
            ink={palette.onCopper}
            foldShade={0.74}
            length={158}
            icon={<IconInfo size={16} strokeWidth={1.8} />}
          />
          <ClothRibbon
            href={strings.shell.githubUrl}
            name={strings.shell.github}
            color={palette.forest}
            ink={palette.egg}
            foldShade={0.5}
            length={140}
            phase={3.7}
            external
            icon={<IconGithub size={16} />}
          />
        </nav>
      ) : (
        <nav className="dc-nav" aria-label={strings.shell.mainNav}>
          <a className="dc-nav__link" href={hrefFor('/about')} aria-current={route === '/about' ? 'page' : undefined}>
            {strings.shell.about}
          </a>
          <a className="dc-nav__link" href={strings.shell.githubUrl} target="_blank" rel="noreferrer">
            {strings.shell.github}
          </a>
          {showNewUpload && (
            <button
              type="button"
              className="dc-pill dc-pill--quiet dc-pill--small"
              onClick={() => (hasWork(state.edits) && state.phase === 'results' ? setConfirming(true) : startNew())}
            >
              {strings.shell.newUpload}
            </button>
          )}
        </nav>
      )}
      {hasResults(state) && state.job && <DownloadMenu placement="header" />}
      <ConfirmNewUpload
        opened={confirming}
        edited={work.edited}
        checked={work.checked}
        onCancel={() => setConfirming(false)}
        onConfirm={startNew}
      />
    </header>
  );
}
