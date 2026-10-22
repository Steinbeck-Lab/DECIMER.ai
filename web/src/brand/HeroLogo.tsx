import { useEffect, useState } from 'react';
import introGif from '../assets/decimer-intro.gif';
import { strings } from '../strings';
import { isFirstVisit, markVisited } from './firstVisit';
import { Wordmark } from './Wordmark';

/** Reduced motion: no animation, and the visit is not used up. */
function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * The logo animation of a browser's first visit (PRODUCT.md, Brand Commitments;
 * `DECIMER.gif` of the legacy site, 1.9 MB, plays once for about 8 s).
 *
 * - The file is only requested when it is shown: a returning visitor, a visitor
 *   who prefers reduced motion and every page other than the empty screen never
 *   fetch it, and nothing preloads it.
 * - The visit is remembered only once the animation has loaded and is on screen
 *   in a visible tab. A first visit that is cut short (the tab is closed before
 *   the file arrives) shows it again next time.
 */
function Intro() {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (!loaded) return;
    if (document.visibilityState === 'visible') {
      markVisited();
      return;
    }
    // Opened in a background tab: it counts once the tab is looked at.
    const onVisible = () => {
      if (document.visibilityState === 'visible') markVisited();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loaded]);
  // The GIF is 16:9 with the logo and the tagline in the middle band; the frame shows that band at the
  // size of the static wordmark (see .dc-intro-frame).
  return (
    <div className="dc-intro-frame">
      <img
        className="dc-intro"
        src={introGif}
        alt={strings.appName}
        width={1920}
        height={1080}
        onLoad={() => setLoaded(true)}
      />
    </div>
  );
}

/** Is this visit to start with the animation? Reads the flag without using it up. */
export function shouldPlayIntro(): boolean {
  return isFirstVisit() && !prefersReducedMotion();
}

export function HeroLogo({ intro }: { intro: boolean }) {
  return intro ? <Intro /> : <Wordmark hero />;
}
