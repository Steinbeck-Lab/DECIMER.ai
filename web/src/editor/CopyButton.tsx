import { useClipboard } from '@mantine/hooks';
import { strings } from '../strings';
import { IconCheck, IconCopy } from '../ui/icons';

/** A small icon button that copies `value`. The label says what, the icon flips for a moment after copying. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const clipboard = useClipboard({ timeout: 1600 });
  return (
    <button
      type="button"
      className="dc-icon-button"
      aria-label={clipboard.copied ? strings.editor.copied : label}
      title={clipboard.copied ? strings.editor.copied : label}
      disabled={!value}
      onClick={() => clipboard.copy(value)}
    >
      {clipboard.copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
    </button>
  );
}
