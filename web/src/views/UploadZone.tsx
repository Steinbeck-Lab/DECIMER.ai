import { useDropzone } from 'react-dropzone';
import { strings } from '../strings';
import { ACCEPT } from '../upload/files';
import { describeProblem } from '../upload/problems';
import type { Problem } from '../state/model';
import { IconUpload } from '../ui/icons';

function ProblemNotice({ problem }: { problem: Problem }) {
  const { text, detail } = describeProblem(problem);
  return (
    <p className="dc-callout" data-kind="problem" role="alert">
      {text}
      {detail && <span className="dc-callout__detail">{detail}</span>}
    </p>
  );
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

  return (
    <div className="dc-upload">
      <div {...getRootProps({ className: 'dc-drop', 'data-active': isDragActive ? 'true' : 'false' })}>
        <input {...getInputProps({ 'aria-hidden': true })} />
        <IconUpload size={32} className="dc-drop__icon" />
        <p className="dc-drop__title">{isDragActive ? strings.upload.dropActive : strings.upload.title}</p>
        <p className="dc-drop__hint">{strings.upload.orPaste}</p>
        <button type="button" className="dc-pill dc-pill--ink dc-drop__button">
          {strings.upload.browse}
        </button>
        <p className="dc-drop__limits">
          {strings.upload.formats} {strings.upload.limits}
        </p>
      </div>
      {problem && <ProblemNotice problem={problem} />}
      <p className="dc-upload__privacy">{strings.upload.privacy}</p>
    </div>
  );
}
