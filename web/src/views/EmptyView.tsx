import { useState } from 'react';
import { HeroLogo, shouldPlayIntro } from '../brand/HeroLogo';
import { strings } from '../strings';
import { useAppState, useController } from '../state/react';
import { UploadZone } from './UploadZone';

export function EmptyView() {
  const { problem } = useAppState();
  const controller = useController();
  // Decided once per visit of this screen. The animation has the tagline in it, so the text is kept for
  // assistive technology only while it plays.
  const [intro] = useState(shouldPlayIntro);
  return (
    <main id="main" tabIndex={-1} className="dc-home">
      <h1 className="dc-hero">
        <HeroLogo intro={intro} />
      </h1>
      <p className={intro ? 'dc-sr' : 'dc-tagline'}>{strings.tagline}</p>
      <UploadZone onFiles={(files) => void controller.submit(files)} problem={problem} />
    </main>
  );
}
