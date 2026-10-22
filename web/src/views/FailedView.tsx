import { strings } from '../strings';
import { useAppState, useController } from '../state/react';

export function FailedView() {
  const { failure } = useAppState();
  const controller = useController();
  const message =
    failure?.kind === 'connection'
      ? strings.working.failedConnection
      : failure?.kind === 'expired'
        ? strings.working.failedExpired
        : (failure?.message ?? strings.working.failedGeneric);
  // Only a lost connection leaves a job that may still be fine: a failed or expired one would only fail again.
  const canResume = failure?.kind === 'connection';
  return (
    <main id="main" tabIndex={-1} className="dc-working">
      <h1 className="dc-working__title">{strings.working.failedHeading}</h1>
      <p className="dc-callout" data-kind="problem" role="alert">
        {message}
      </p>
      <div className="dc-working__actions">
        {canResume && (
          <button type="button" className="dc-pill dc-pill--ink" onClick={() => controller.resume()}>
            {strings.working.tryAgain}
          </button>
        )}
        <button
          type="button"
          className={canResume ? 'dc-pill dc-pill--quiet' : 'dc-pill dc-pill--ink'}
          onClick={() => controller.startOver()}
        >
          {strings.working.startOver}
        </button>
      </div>
    </main>
  );
}
