import { useState } from 'react';
import { strings } from '../strings';

interface Props {
  src: string;
  alt: string;
  className?: string;
}

/**
 * The original crop of a structure. A result can have none: an image of a several-image upload that the worker could
 * not read has no crop file, and the media endpoint answers 404. The browser's broken-image icon is never shown; a
 * quiet "No picture" stands in its place. A new `src` is tried again.
 */
export function CropImage({ src, alt, className }: Props) {
  const [failed, setFailed] = useState<string | null>(null);
  if (failed === src) {
    return (
      <span className={[className, 'dc-crop--none'].filter(Boolean).join(' ')} data-crop-missing="true">
        {strings.tile.noPicture}
      </span>
    );
  }
  return <img className={className} src={src} alt={alt} onError={() => setFailed(src)} />;
}
