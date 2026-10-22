import { Tooltip } from '@mantine/core';
import { memo } from 'react';
import { strings } from '../strings';
import type { ResultView } from '../state/selectors';
import { IconAlert } from '../ui/icons';
import { imageSrc, nameHover, statusLabel } from '../ui/format';

interface Props {
  view: ResultView;
  selected: boolean;
  /** Position in the grid, for the arrival stagger. */
  order: number;
  onSelect: (index: number) => void;
}

/**
 * One structure of the figure: the depiction with the original crop as an
 * inset, the bold compound number with a status dot, the name (two lines at
 * most, Orthonym's tier on hover or focus) and the status word.
 */
export const Tile = memo(function Tile({ view, selected, order, onSelect }: Props) {
  const status = statusLabel(view.status);
  // What kind of name this is and how it was checked; while an edit is unanswered there is nothing to say yet.
  const hover = view.stale ? [strings.editor.updating] : nameHover(view.name, view.nameTier, view.nameVerified);
  // From the edit until the server answers, the name and depiction are from before it: say so rather than
  // show them (dimming text would take it below the contrast floor on a selected tile).
  const name = view.stale
    ? view.validating
      ? strings.editor.updatingShort
      : strings.editor.notUpdated
    : (view.name ?? strings.tile.noName);
  return (
    <li className="dc-tile" style={{ '--i': order } as React.CSSProperties}>
      <Tooltip
        label={hover.map((line) => (
          <span key={line} className="dc-hover__line">
            {line}
          </span>
        ))}
        disabled={hover.length === 0}
        position="bottom"
        withArrow
        multiline
        w={260}
        openDelay={250}
        events={{ hover: true, focus: true, touch: true }}
        radius="md"
      >
        <button
          type="button"
          className="dc-tile__button"
          data-result-tile={view.index}
          data-status={view.status}
          data-stale={view.stale}
          aria-busy={view.validating}
          aria-current={selected ? 'true' : undefined}
          onClick={() => onSelect(view.index)}
        >
          <span className="dc-tile__figure">
            {view.svg ? (
              <img className="dc-tile__structure" src={imageSrc(view.svg)} alt="" width={360} height={260} />
            ) : (
              <span className="dc-tile__structure dc-tile__structure--none" aria-hidden="true">
                {strings.tile.noDepiction}
              </span>
            )}
            <img className="dc-tile__crop" src={view.result.cropUrl} alt="" />
          </span>
          <span className="dc-tile__id">
            <span className="dc-sr">{strings.tile.structure} </span>
            <span className="dc-tile__number">{view.number}</span>
            <span className="dc-dot" data-status={view.status} aria-hidden="true" />
          </span>
          <span className="dc-tile__name" data-empty={view.stale || view.name === null}>
            {name}
          </span>
          <span className="dc-tile__flags">
            <span className="dc-status" data-status={view.status}>
              {status}
            </span>
            {view.invalid && !view.stale && (
              <span className="dc-status dc-status--invalid">
                <IconAlert size={12} />
                {strings.editor.invalidFlag}
              </span>
            )}
          </span>
        </button>
      </Tooltip>
    </li>
  );
});
