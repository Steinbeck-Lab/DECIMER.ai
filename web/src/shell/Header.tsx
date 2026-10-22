import { useState } from 'react';
import { Wordmark } from '../brand/Wordmark';
import { strings } from '../strings';
import { hasWork, workSummary } from '../state/selectors';
import { useAppState, useController } from '../state/react';
import { ConfirmNewUpload } from './ConfirmNewUpload';
import { DownloadMenu } from './DownloadMenu';
import { hrefFor, navigate, type Route } from './router';

export function Header({ route }: { route: Route }) {
  const state = useAppState();
  const controller = useController();
  const [confirming, setConfirming] = useState(false);

  const home = route === '/';
  const hasResults = state.phase === 'results' && (state.job?.results.length ?? 0) > 0;
  // The big logo of the empty screen already is the wordmark.
  const showMark = !(home && state.phase === 'empty');
  const showNewUpload = state.phase !== 'empty';
  const work = workSummary(state.edits);

  const startNew = () => {
    controller.startOver();
    setConfirming(false);
    navigate('/');
  };

  return (
    <header className="dc-header">
      {showMark && (
        <a className="dc-header__logo" href={hrefFor('/')} aria-label={strings.shell.home}>
          <Wordmark height={32} />
        </a>
      )}
      <nav className="dc-nav" aria-label={strings.shell.mainNav}>
        {showNewUpload && (
          <button
            type="button"
            className="dc-nav__link"
            onClick={() => (hasWork(state.edits) && state.phase === 'results' ? setConfirming(true) : startNew())}
          >
            {strings.shell.newUpload}
          </button>
        )}
        <a className="dc-nav__link" href={hrefFor('/about')} aria-current={route === '/about' ? 'page' : undefined}>
          {strings.shell.about}
        </a>
        <a className="dc-nav__link" href={strings.shell.githubUrl} target="_blank" rel="noreferrer">
          {strings.shell.github}
        </a>
      </nav>
      {hasResults && state.job && (
        <div className="dc-header__tools">
          <span className="dc-capacity">
            {strings.results.capacity(state.job.results.length, state.job.pagesProcessed)}
          </span>
          <DownloadMenu placement="header" />
        </div>
      )}
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
