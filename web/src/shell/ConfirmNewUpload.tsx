import { Modal } from '@mantine/core';
import { palette } from '../theme';
import { strings } from '../strings';
import { confirmBody } from './confirm';

interface Props {
  opened: boolean;
  edited: number;
  checked: number;
  onCancel: () => void;
  onConfirm: () => void;
}

/** Starting over drops every edit and check, so it asks first when there are any. Mantine's Modal traps focus and returns it. */
export function ConfirmNewUpload({ opened, edited, checked, onCancel, onConfirm }: Props) {
  return (
    <Modal
      opened={opened}
      onClose={onCancel}
      title={strings.shell.confirmNewTitle}
      centered
      radius="lg"
      classNames={{ title: 'dc-modal__title', content: 'dc-modal', header: 'dc-modal__header' }}
      overlayProps={{ color: palette.forest, backgroundOpacity: 0.45 }}
    >
      <p className="dc-modal__body">{confirmBody(edited, checked)}</p>
      <div className="dc-modal__actions">
        <button type="button" className="dc-pill dc-pill--quiet" onClick={onCancel} data-autofocus>
          {strings.shell.confirmNewKeep}
        </button>
        <button type="button" className="dc-pill dc-pill--ink" onClick={onConfirm}>
          {strings.shell.confirmNewGo}
        </button>
      </div>
    </Modal>
  );
}
