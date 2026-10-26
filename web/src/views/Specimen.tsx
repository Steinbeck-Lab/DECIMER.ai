import { strings } from '../strings';
import type { SourceFile } from '../state/model';
import { IconFile, PillIcon } from '../ui/icons';

/** Sources shown by name; the rest are counted. */
const MAX_SHOWN = 3;

/** "812 KB", "2.4 MB": the way a measurement is printed, in whole units below a megabyte. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

interface Props {
  sources: readonly SourceFile[];
  /** The frame's label: the stage the job is in, or why it stopped. */
  label: string;
  /** The frame turns garnet. */
  stopped?: boolean;
}

/**
 * What was uploaded, framed like the drop zone was: a copper inspection frame 7 px out, four corner handles, a label
 * in mono and page guides that stop where they like. The frame is decoration (hidden from assistive technology; the
 * view's heading says the same in words) and is drawn in once, by the rules of the drop zone's frame in home.css.
 */
export function Specimen({ sources, label, stopped = false }: Props) {
  if (sources.length === 0) return null;
  const shown = sources.slice(0, MAX_SHOWN);
  const more = sources.length - shown.length;
  return (
    <div className="dc-specimen" data-stopped={stopped}>
      <div className="dc-frame" aria-hidden="true">
        <span className="dc-frame__guide dc-frame__guide--top" />
        <span className="dc-frame__guide dc-frame__guide--bottom" />
        <span className="dc-frame__guide dc-frame__guide--left" />
        <span className="dc-frame__guide dc-frame__guide--right" />
        <span className="dc-frame__line dc-frame__line--top" />
        <span className="dc-frame__line dc-frame__line--bottom" />
        <span className="dc-frame__line dc-frame__line--left" />
        <span className="dc-frame__line dc-frame__line--right" />
        <span className="dc-frame__handle dc-frame__handle--tl" />
        <span className="dc-frame__handle dc-frame__handle--tr" />
        <span className="dc-frame__handle dc-frame__handle--bl" />
        <span className="dc-frame__handle dc-frame__handle--br" />
        <span className="dc-frame__label">{label}</span>
      </div>
      <ul className="dc-specimen__list" data-many={sources.length > 1}>
        {shown.map((s, i) => (
          <li key={`${i}-${s.name}-${s.size}`} className="dc-specimen__item">
            {s.previewUrl ? (
              <img className="dc-specimen__thumb" src={s.previewUrl} alt={strings.working.sourcePreview(s.name)} />
            ) : (
              <PillIcon>
                <IconFile size={18} strokeWidth={1.8} />
              </PillIcon>
            )}
            <span className="dc-specimen__text">
              <span className="dc-specimen__name">{s.name}</span>
              <span className="dc-specimen__size">{formatSize(s.size)}</span>
            </span>
          </li>
        ))}
        {more > 0 && (
          <li className="dc-specimen__item dc-specimen__item--more">
            <span aria-hidden="true">+{more}</span>
            <span className="dc-sr">{strings.working.moreFiles(more)}</span>
          </li>
        )}
      </ul>
    </div>
  );
}
