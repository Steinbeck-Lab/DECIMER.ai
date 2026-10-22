import { strings } from '../strings';
import { useAppState, useController } from '../state/react';

export function FailedView() {
  const { failure } = useAppState();
  const controller = useController();
  const message =
    failure?.kind === 'connection'
      ? strings.working.failedConnection
      : (failure?.message ?? strings.working.failedGeneric);
  return (
    <main id="main" tabIndex={-1} className="dc-working">
      <h1 className="dc-working__title">{strings.working.failedHeading}</h1>
      <p className="dc-callout" data-kind="problem" role="alert">
        {message}
      </p>
      <button type="button" className="dc-pill dc-pill--ink dc-working__again" onClick={() => controller.startOver()}>
        {strings.working.startOver}
      </button>
    </main>
  );
}
