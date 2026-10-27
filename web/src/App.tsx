import { useMediaQuery } from '@mantine/hooks';
import { useEffect, useRef } from 'react';
import { trackRoute } from './analytics/matomo';
import { EditorPanel } from './editor/EditorPanel';
import { About } from './pages/About';
import { Impressum } from './pages/Impressum';
import { Privacy } from './pages/Privacy';
import { Footer } from './shell/Footer';
import { useStrayDropGuard, useUnloadGuard } from './shell/guards';
import { Header } from './shell/Header';
import { NoticeRegion } from './shell/NoticeRegion';
import { Ruler } from './shell/Ruler';
import { useRoute, type Route } from './shell/router';
import { hasResults, hasWork } from './state/selectors';
import { useAppState, useController } from './state/react';
import { strings } from './strings';
import { usePasteToUpload } from './upload/paste';
import { EmptyView } from './views/EmptyView';
import { FailedView } from './views/FailedView';
import { NoStructuresView, ResultsView } from './views/ResultsView';
import { WorkingView } from './views/WorkingView';

const PAGE_TITLES: Record<Route, string> = {
  '/': strings.appName,
  '/about': `${strings.shell.about} · ${strings.appName}`,
  '/privacy': `${strings.shell.privacy} · ${strings.appName}`,
  '/impressum': `${strings.shell.impressum} · ${strings.appName}`,
};

export function App() {
  const route = useRoute();
  const state = useAppState();
  const controller = useController();

  // Pasting an image starts the job at once, but only where a job can start: the empty screen.
  // On the results screen a paste must never replace edited work.
  usePasteToUpload(route === '/' && state.phase === 'empty', (files) => void controller.submit(files));

  // No silent loss of work: leaving the tab with edits or checks asks first, and a file dropped
  // outside a drop zone cannot navigate the tab away from the results.
  useUnloadGuard(hasWork(state.edits));
  useStrayDropGuard(state.phase !== 'empty');

  // A new page: top of the page, focus on its main region (a hash route does not move focus by itself).
  const firstRoute = useRef(true);
  useEffect(() => {
    document.title = PAGE_TITLES[route];
    if (firstRoute.current) {
      firstRoute.current = false;
      return;
    }
    window.scrollTo(0, 0);
    // Focus the page's heading, so a screen reader announces where it landed.
    const heading = document.querySelector<HTMLElement>('#main h1');
    heading?.setAttribute('tabindex', '-1');
    (heading ?? document.getElementById('main'))?.focus({ preventScroll: true });
  }, [route]);

  // The visit counter learns of a move to another page (it counted the first page itself).
  useEffect(() => trackRoute(route), [route]);

  const split = route === '/' && hasResults(state);

  // Below 900 px the editor is a modal sheet: the page behind it is inert (no focus, no clicks, hidden from
  // assistive technology) for as long as it is open. The editor and the notices stay outside the inert parts.
  const sheet = useMediaQuery('(max-width: 899px)');
  const sheetOpen = sheet && split && state.selected !== null;

  let content;
  if (route === '/about') content = <About />;
  else if (route === '/privacy') content = <Privacy />;
  else if (route === '/impressum') content = <Impressum />;
  else if (state.phase === 'working') content = <WorkingView />;
  else if (state.phase === 'failed') content = <FailedView />;
  else if (state.phase === 'results') content = hasResults(state) ? <ResultsView /> : <NoStructuresView />;
  else content = <EmptyView />;

  return (
    <div className="dc-app">
      <div className="dc-contents" inert={sheetOpen}>
        <Ruler />
        <button type="button" className="dc-skip" onClick={() => document.getElementById('main')?.focus()}>
          {strings.shell.skipToContent}
        </button>
        <Header route={route} />
      </div>
      <div className="dc-body" data-split={split} data-editing={split && state.selected !== null}>
        <div className="dc-contents" inert={sheetOpen}>
          {content}
        </div>
        {(split || state.editorMounted) && <EditorPanel visible={split} sheet={sheet} />}
      </div>
      <div className="dc-contents" inert={sheetOpen}>
        <Footer compact={route === '/' && state.phase === 'empty'} />
      </div>
      <NoticeRegion />
    </div>
  );
}
