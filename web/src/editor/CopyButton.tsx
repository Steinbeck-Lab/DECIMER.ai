import { useClipboard } from '@mantine/hooks';
import { strings } from '../strings';
import { IconCheck, IconCopy } from '../ui/icons';

/**
 * A button that copies `value`. The label says what; after copying it says "Copied" for a moment. `icon` is a small
 * round porcelain button with the copy glyph (it flips to a tick); `pill` is a small porcelain pill with the word.
 */
export function CopyButton({ value, label, variant = 'icon' }: { value: string; label: string; variant?: 'icon' | 'pill' }) {
  const clipboard = useClipboard({ timeout: 1600 });
  const name = clipboard.copied ? strings.editor.copied : label;
  if (variant === 'pill') {
    return (
      <button
        type="button"
        className="dc-pill dc-pill--quiet dc-pill--small dc-copy"
        aria-label={name}
        title={name}
        disabled={!value}
        onClick={() => clipboard.copy(value)}
      >
        {clipboard.copied ? strings.editor.copied : strings.editor.copy}
      </button>
    );
  }
  return (
    <button
      type="button"
      className="dc-icon-button"
      aria-label={name}
      title={name}
      disabled={!value}
      onClick={() => clipboard.copy(value)}
    >
      {clipboard.copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
    </button>
  );
}
