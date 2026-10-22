import { useFocusTrap } from '@mantine/hooks';
import type { Ketcher } from 'ketcher-core';
import { useCallback, useEffect, useRef, useState } from 'react';
import { strings } from '../strings';
import type { Status } from '../state/model';
import { useAppState, useController, useResultViews } from '../state/react';
import { pageNumber, stepList, type ResultView } from '../state/selectors';
import { IconCheck, IconChevronLeft, IconChevronRight, IconFlag, IconReset, IconX } from '../ui/icons';
import { statusLabel, tierLabel } from '../ui/format';
import { CopyButton } from './CopyButton';
import { KetcherPanel } from './KetcherPanel';
import { Links } from './Links';
import { useEditorSession } from './useEditorSession';

const LEGEND: Status[] = ['unchecked', 'review', 'notsure', 'edited', 'checked'];

function focusTile(index: number | null): void {
  const tile = index === null ? null : document.querySelector<HTMLElement>(`[data-result-tile="${index}"]`);
  (tile ?? document.getElementById('main'))?.focus();
}

/** Tells Ketcher its canvas changed size (it only listens to the window), e.g. when the panel opens on another breakpoint. */
function useCanvasResizeKick(host: React.RefObject<HTMLDivElement | null>, enabled: boolean) {
  useEffect(() => {
    const el = host.current;
    if (!el || !enabled || typeof ResizeObserver === 'undefined') return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const observer = new ResizeObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => window.dispatchEvent(new Event('resize')), 120);
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      clearTimeout(timer);
    };
  }, [host, enabled]);
}

function Idle() {
  return (
    <div className="dc-editor__idle">
      <h2 className="dc-editor__idle-title">{strings.editor.idleTitle}</h2>
      <p>{strings.editor.idleBody}</p>
      <h3 className="dc-editor__legend-title">{strings.editor.legend}</h3>
      <ul className="dc-legend">
        {LEGEND.map((status) => (
          <li key={status} className="dc-legend__item">
            <span className="dc-dot" data-status={status} aria-hidden="true" />
            <span>
              <strong>{statusLabel(status)}.</strong> {strings.editor.legendItems[status]}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Why the result needs a look. The reasons about the recognition (low confidence, the classifier's
 * doubt) go once the user has acted on the result. The ones about the structure as it is now stay:
 * an invalid SMILES must be visible after an edit, and after "checked" too. While the server has not
 * answered an edit, nothing is known about the new structure, so none of those is shown.
 */
function Reasons({ view }: { view: ResultView }) {
  const unresolved = view.status === 'review' || view.status === 'notsure';
  const shown = view.reasons.filter((reason) => {
    if (reason === 'invalid' || reason === 'rgroup') return !view.stale;
    return unresolved;
  });
  if (shown.length === 0) return null;
  const text = (reason: ResultView['reasons'][number]) =>
    reason === 'lowConfidence'
      ? strings.editor.reasons.lowConfidence(Math.round(view.result.confidence * 100))
      : strings.editor.reasons[reason];
  return (
    <ul className="dc-reasons">
      {shown.map((reason) => (
        <li
          key={reason}
          className="dc-callout"
          data-kind={reason === 'notStructure' || reason === 'invalid' ? 'problem' : 'warning'}
        >
          {text(reason)}
        </li>
      ))}
    </ul>
  );
}

/**
 * The one editor panel. A sticky column beside the figure on wide screens, a
 * full-screen sheet on phones (CSS only, so there is one DOM and one Ketcher).
 * It traps Tab while a structure is open and puts focus back on the structure's
 * tile when it closes. Ketcher mounts on the first selection and then stays
 * mounted: closing only hides it.
 */
export function EditorPanel({ visible, sheet }: { visible: boolean; sheet: boolean }) {
  const state = useAppState();
  const controller = useController();
  const views = useResultViews(state);
  const [ketcher, setKetcher] = useState<Ketcher | null>(null);
  const session = useEditorSession(ketcher);
  const canvasHost = useRef<HTMLDivElement>(null);
  const [reporting, setReporting] = useState(false);
  // The result whose structure Ketcher could not load; the header must not claim it while the canvas is empty.
  const [loadFailed, setLoadFailed] = useState<number | null>(null);
  // Ketcher moves focus to its hidden clipboard textarea when it starts and when a molecule loads.
  // Unless the user chose the canvas themselves, focus goes back to where they were in the panel.
  const chromeFocus = useRef<HTMLElement | null>(null);
  const canvasChosen = useRef(false);

  const selectedIndex = state.selected;
  const view = selectedIndex === null ? undefined : views.find((v) => v.index === selectedIndex);
  const open = visible && view !== undefined;
  const trapRef = useFocusTrap(open);

  useCanvasResizeKick(canvasHost, Boolean(ketcher));

  const inCanvas = (node: EventTarget | Element | null) =>
    node instanceof Element && canvasHost.current?.contains(node) === true;
  const settleFocus = () => {
    if (!canvasChosen.current && inCanvas(document.activeElement)) chromeFocus.current?.focus({ preventScroll: true });
  };

  // A new job reuses result indexes: forget the last job's baselines.
  useEffect(() => {
    void session?.forget();
  }, [session, state.jobId]);

  // Load a result into the editor (its edit, if it has one). Reads the store directly so typing, which
  // changes the edit on every keystroke, never reloads the canvas.
  const loadResult = useCallback(
    (index: number) => {
      if (!session) return;
      const { job, edits } = controller.store.getState();
      const result = job?.results.find((r) => r.index === index);
      if (!result) return;
      session
        .load(result.index, result.molfile, edits[result.index]?.molfile ?? null)
        .then(() => setLoadFailed((failed) => (failed === index ? null : failed)))
        .catch(() => setLoadFailed(index));
    },
    [session, controller],
  );

  useEffect(() => {
    if (selectedIndex !== null) loadResult(selectedIndex);
  }, [loadResult, selectedIndex]);

  const list = stepList(views, state.filter, selectedIndex);
  const position = view ? list.findIndex((v) => v.index === view.index) : -1;
  const previous = position > 0 ? list[position - 1] : undefined;
  const next = position >= 0 ? list[position + 1] : undefined;

  const onReady = (instance: Ketcher) => {
    setKetcher(instance);
    setTimeout(settleFocus, 200);
  };

  const close = () => {
    const index = selectedIndex;
    controller.select(null);
    // Once React has hidden the panel, the tile can take focus back.
    requestAnimationFrame(() => focusTile(index));
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    // Escape belongs to Ketcher while the canvas has focus (it cancels a tool).
    if (event.key !== 'Escape' || (event.target as Element).closest('.dc-editor__canvas')) return;
    event.stopPropagation();
    close();
  };

  const reset = async () => {
    if (!view) return;
    // The canvas goes back first, so an edit still waiting in the debounce cannot come back after the reset.
    await session?.reset(view.index, view.result.molfile);
    controller.reset(view.index);
  };

  const report = async () => {
    if (!view) return;
    setReporting(true);
    try {
      await controller.report(view.index);
    } finally {
      setReporting(false);
    }
  };

  return (
    <aside
      ref={trapRef}
      className="dc-editor"
      // A column beside the figure on wide screens; a modal sheet below 900 px. The page behind the sheet is inert (App).
      role={sheet ? 'dialog' : undefined}
      aria-modal={sheet && open ? true : undefined}
      aria-label={strings.editor.region}
      data-open={open}
      // display:none would collapse Ketcher's canvas, so a closed panel is moved away by CSS instead.
      data-hidden={!visible}
      onKeyDown={onKeyDown}
      onFocusCapture={(event) => {
        if (!inCanvas(event.target)) {
          chromeFocus.current = event.target as HTMLElement;
          canvasChosen.current = false;
        }
      }}
      onPointerDownCapture={(event) => {
        if (inCanvas(event.target)) canvasChosen.current = true;
      }}
    >
      {!view && <Idle />}

      {view && (
        <header className="dc-editor__head">
          <h2 className="dc-editor__number" tabIndex={-1} data-autofocus aria-label={loadFailed === view.index ? strings.editor.loadFailedHeading(view.number) : `${strings.editor.editing} ${view.number}`}>
            {view.number}
          </h2>
          <span className="dc-editor__editing" aria-hidden="true" data-failed={loadFailed === view.index}>
            {loadFailed === view.index ? strings.editor.loadFailedShort : strings.editor.editing}
          </span>
          <span className="dc-editor__position">{strings.editor.position(position + 1, list.length)}</span>
          <div className="dc-editor__nav">
            <button
              type="button"
              className="dc-icon-button"
              aria-label={strings.editor.previous}
              title={strings.editor.previous}
              disabled={!previous}
              onClick={() => previous && controller.select(previous.index)}
            >
              <IconChevronLeft size={18} />
            </button>
            <button
              type="button"
              className="dc-icon-button"
              aria-label={strings.editor.next}
              title={strings.editor.next}
              disabled={!next}
              onClick={() => next && controller.select(next.index)}
            >
              <IconChevronRight size={18} />
            </button>
            <button type="button" className="dc-pill dc-pill--small dc-pill--quiet" onClick={close}>
              <IconX size={16} />
              {strings.editor.close}
            </button>
          </div>
        </header>
      )}

      <div
        ref={canvasHost}
        className="dc-editor__canvas"
        data-active={open}
        role="group"
        aria-label={strings.editor.canvasLabel}
      >
        {state.editorMounted && <KetcherPanel onReady={onReady} />}
        {view && loadFailed === view.index && (
          <div className="dc-editor__load-error" role="alert">
            <p>{strings.editor.loadFailed}</p>
            <button type="button" className="dc-pill dc-pill--small dc-pill--ink" onClick={() => loadResult(view.index)}>
              {strings.editor.retryLoad}
            </button>
          </div>
        )}
      </div>

      {view && (
        <div className="dc-editor__body">
          <Reasons view={view} />

          <div className="dc-facts">
            <figure className="dc-fact dc-fact--crop">
              <img src={view.result.cropUrl} alt={strings.tile.cropAlt(view.number)} />
              <figcaption>{strings.editor.original(pageNumber(view))}</figcaption>
            </figure>
            <dl className="dc-fact dc-fact--text">
              <dt>{strings.editor.name}</dt>
              <dd>
                {view.stale ? (
                  <span className="dc-fact__value" data-empty="true">
                    {view.validating ? strings.editor.updatingShort : strings.editor.notUpdated}
                  </span>
                ) : (
                  <>
                    <span className="dc-fact__value" data-empty={view.name === null}>
                      {view.name ?? strings.tile.noName}
                    </span>
                    {view.name && <span className="dc-fact__tier">{strings.tile.tier(tierLabel(view.nameTier))}</span>}
                    {view.name && <CopyButton value={view.name} label={strings.editor.copyName} />}
                  </>
                )}
              </dd>
              <dt>{strings.editor.change}</dt>
              <dd>
                <span className="dc-fact__value">
                  {view.modified ? strings.editor.changeEdited : strings.editor.changeNone}
                </span>
              </dd>
            </dl>
          </div>

          <div className="dc-code" aria-busy={view.validating}>
            <div className="dc-code__head">
              <span className="dc-code__label" id="dc-smiles-label">
                {strings.editor.smiles}
              </span>
              <CopyButton value={view.stale || !view.valid ? '' : view.smiles} label={strings.editor.copySmiles} />
            </div>
            <code className="dc-code__text" aria-labelledby="dc-smiles-label" data-stale={view.stale}>
              {view.stale
                ? view.validating
                  ? strings.editor.updatingShort
                  : strings.editor.notUpdated
                : view.valid && view.smiles
                  ? view.smiles
                  : strings.editor.noSmiles}
            </code>
            <p className="dc-code__status" role="status">
              {view.validating && strings.editor.updating}
              {view.validationFailed && strings.editor.updateFailed}
            </p>
            <details className="dc-molfile">
              <summary>{strings.editor.molfile}</summary>
              <div className="dc-molfile__head">
                <CopyButton value={view.molfile} label={strings.editor.copyMolfile} />
              </div>
              <pre className="dc-molfile__text">{view.molfile}</pre>
            </details>
          </div>
        </div>
      )}

      {view && (
        <div className="dc-editor__foot">
          <div className="dc-actions">
            <button
              type="button"
              className="dc-pill dc-pill--mint"
              aria-pressed={view.checked}
              onClick={() => controller.setChecked(view.index, !view.checked)}
            >
              <IconCheck size={18} />
              {view.checked ? strings.editor.undoChecked : strings.editor.markChecked}
            </button>
            <button
              type="button"
              className="dc-pill dc-pill--quiet"
              onClick={() => void reset()}
              disabled={!view.modified}
              title={strings.editor.resetHint}
            >
              <IconReset size={18} />
              {strings.editor.reset}
            </button>
          </div>

          <div className="dc-followups">
            <Links key={view.index} inchikey={view.valid ? view.inchikey : ''} stale={view.stale} />
            {state.reported[view.index] ? (
              <p className="dc-thanks" role="status">
                {strings.editor.reported}
              </p>
            ) : (
              <button
                type="button"
                className="dc-link dc-link--button"
                onClick={() => void report()}
                disabled={reporting}
              >
                <IconFlag size={14} />
                {reporting ? strings.editor.reporting : strings.editor.report}
              </button>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
