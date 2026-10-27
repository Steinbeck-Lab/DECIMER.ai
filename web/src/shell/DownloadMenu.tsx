import { Menu } from '@mantine/core';
import type { ExportFormat } from '../api/contract';
import { strings } from '../strings';
import { useAppState, useController } from '../state/react';
import { IconArrowDown, PillIcon } from '../ui/icons';

const ITEMS: { format: ExportFormat; label: string }[] = [
  { format: 'zip', label: strings.results.downloadZip },
  { format: 'sdf', label: strings.results.downloadSdf },
  { format: 'csv', label: strings.results.downloadCsv },
];

/**
 * The Download menu, the one on the results screen (at the end of the filter row): ZIP, SDF or CSV of the structures as
 * edited on screen. One export at a time: while it runs the button is off (the editor is flushed first, see the controller).
 * `describedBy` names the element that says what the download holds.
 */
export function DownloadMenu({ describedBy }: { describedBy?: string }) {
  const controller = useController();
  const busy = useAppState().exporting;

  return (
    <Menu
      position="bottom-end"
      shadow="md"
      radius="lg"
      width={300}
      withinPortal
      zIndex={500}
      classNames={{ dropdown: 'dc-menu', item: 'dc-menu__item' }}
    >
      <Menu.Target>
        <button
          type="button"
          className="dc-pill dc-pill--ink dc-download"
          aria-busy={busy}
          aria-describedby={describedBy}
          disabled={busy}
        >
          <span>{busy ? strings.results.downloading : strings.results.download}</span>
          <span className="dc-download__formats">{strings.results.downloadFormats}</span>
          <PillIcon>
            <IconArrowDown size={16} strokeWidth={1.8} />
          </PillIcon>
        </button>
      </Menu.Target>
      <Menu.Dropdown aria-label={strings.results.downloadMenuLabel}>
        {ITEMS.map(({ format, label }) => (
          <Menu.Item key={format} onClick={() => void controller.download(format)}>
            {label}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}
