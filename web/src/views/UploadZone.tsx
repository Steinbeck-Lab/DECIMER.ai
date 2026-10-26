import { useId } from 'react';
import { useDropzone } from 'react-dropzone';
import { strings } from '../strings';
import { ACCEPT } from '../upload/files';
import { describeProblem } from '../upload/problems';
import type { Problem } from '../state/model';
import { IconArrowUp, PillIcon } from '../ui/icons';
import { InspectionFrame } from '../ui/InspectionFrame';
import { useRenderedSize } from '../ui/useRenderedSize';

function ProblemNotice({ problem }: { problem: Problem }) {
  const { text, detail } = describeProblem(problem);
  return (
    <p className="dc-callout" data-kind="problem" role="alert">
      {text}
      {detail && <span className="dc-callout__detail">{detail}</span>}
    </p>
  );
}

/** Is this a Mac (or an iPad or iPhone with a keyboard)? Decides which paste shortcut the pill names. */
function isMacLike(): boolean {
  if (typeof navigator === 'undefined') return false;
  const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? navigator.platform ?? '';
  return /mac|iphone|ipad|ipod/i.test(platform || navigator.userAgent);
}

interface Props {
  onFiles: (files: File[]) => void;
  problem: Problem | null;
}

/**
 * The one drop / browse / paste target. The whole area takes a drop or a click;
 * the button inside is the keyboard way in (Enter or Space press it, and the
 * click opens the picker). Paste is handled at document level (usePasteToUpload),
 * so react-dropzone's own paste handling is off to avoid a second submit.
 *
 * Around the pill, the inspection frame: a copper rectangle 7 px out with four corner handles, a label with the
 * zone's real size, page guides that stop at uneven lengths, and two dimension lines (the accepted formats along
 * the width, the size limit along the height). All of it is decoration (hidden from assistive technology) and is
 * drawn in about a second on load by home.css; the facts it prints are also in the sentence the button is
 * described by.
 */
export function UploadZone({ onFiles, problem }: Props) {
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: ACCEPT,
    multiple: true,
    noKeyboard: true,
    noPaste: true,
    // Files the picker's filter turned away still go to our own check, which names them.
    onDrop: (accepted, rejections) => onFiles([...accepted, ...rejections.map((r) => r.file)]),
  });
  // The frame spans the zone exactly (it is inset: 0 inside it), so its size is the zone's.
  const [frameRef, size] = useRenderedSize<HTMLDivElement>();
  const mac = isMacLike();
  const factsId = useId();

  return (
    <div className="dc-upload">
      <div {...getRootProps({ className: 'dc-drop', 'data-active': isDragActive ? 'true' : 'false' })}>
        <input {...getInputProps({ 'aria-hidden': true })} />
        <InspectionFrame
          frameRef={frameRef}
          guides={['top', 'bottom', 'left', 'right']}
          dimWidth
          dimHeight
          captionWidth={strings.upload.formatsLine}
          captionHeight={strings.upload.sizeLimitLine}
          label={size && strings.upload.zoneLabel(size.width, size.height)}
        />
        <div className="dc-drop__pill">
          <div className="dc-drop__text">
            <p className="dc-drop__title">{isDragActive ? strings.upload.dropActive : strings.upload.title}</p>
            <p className="dc-drop__hint">
              {strings.upload.pasteLead}{' '}
              {mac ? (
                <>
                  <span aria-hidden="true">{strings.upload.pasteKeyMac}</span>
                  <span className="dc-sr">{strings.upload.pasteKeyMacSpoken}</span>
                </>
              ) : (
                strings.upload.pasteKeyOther
              )}
            </p>
          </div>
          <button type="button" className="dc-pill dc-pill--ink dc-drop__button" aria-describedby={factsId}>
            {strings.upload.browse}
            <PillIcon>
              <IconArrowUp size={18} strokeWidth={1.8} />
            </PillIcon>
          </button>
        </div>
        <p id={factsId} className="dc-sr">
          {strings.upload.formats} {strings.upload.limits} {strings.upload.privacy}
        </p>
      </div>
      {problem && <ProblemNotice problem={problem} />}
      <p className="dc-upload__quiet">{strings.upload.quietLine}</p>
    </div>
  );
}
