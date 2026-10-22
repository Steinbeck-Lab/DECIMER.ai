import { useAppState } from '../state/react';

/** One polite live region for short messages (download started, an error). Always in the DOM, so changes are announced. */
export function NoticeRegion() {
  const { notice } = useAppState();
  return (
    <div className="dc-notice-region" role="status" aria-live="polite">
      {notice && (
        <p key={notice.id} className="dc-notice" data-kind={notice.kind}>
          {notice.text}
        </p>
      )}
    </div>
  );
}
