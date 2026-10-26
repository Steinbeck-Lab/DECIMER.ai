import { strings } from '../strings';
import { useAppState, useController } from '../state/react';
import { IconArrowUp, IconReset, PillIcon } from '../ui/icons';
import { Specimen } from './Specimen';

export function FailedView() {
  const { failure, sources } = useAppState();
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
    <main id="main" tabIndex={-1} className="dc-run">
      <Specimen sources={sources} label={strings.working.stoppedLabel} stopped />
      <div className="dc-run__live">
        <h1 className="dc-run__title">{strings.working.failedHeading}</h1>
        <p className="dc-callout" data-kind="problem" role="alert">
          {message}
        </p>
      </div>
      <div className="dc-run__actions">
        {canResume && (
          <button type="button" className="dc-pill dc-pill--ink" onClick={() => controller.resume()}>
            {strings.working.tryAgain}
            <PillIcon>
              <IconReset size={16} strokeWidth={1.8} />
            </PillIcon>
          </button>
        )}
        {canResume ? (
          <button type="button" className="dc-pill dc-pill--quiet" onClick={() => controller.startOver()}>
            {strings.working.startOver}
          </button>
        ) : (
          <button type="button" className="dc-pill dc-pill--ink" onClick={() => controller.startOver()}>
            {strings.working.startOver}
            <PillIcon>
              <IconArrowUp size={16} strokeWidth={1.8} />
            </PillIcon>
          </button>
        )}
      </div>
    </main>
  );
}
