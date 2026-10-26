import { useEffect, useState } from 'react';
import introGif from '../assets/decimer-intro.gif';
import { strings } from '../strings';
import { forgetOldFlags, isFirstVisit, markVisited } from './firstVisit';
import { Wordmark } from './Wordmark';

/** Reduced motion: no animation, and the visit is not used up. */
function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * The logo animation of a browser tab's first load of the site (PRODUCT.md, Brand Commitments; `DECIMER.gif` of the
 * legacy site, 1.9 MB, plays once for about 8 s). The visit is remembered in the tab's session storage
 * (firstVisit.ts): a new tab plays it again, a return to the home page or a reload in the same tab does not.
 *
 * - The file is only requested when it is shown: a tab that has shown it, a visitor who prefers reduced motion and
 *   every page other than the empty screen never fetch it, and nothing preloads it.
 * - The visit is remembered only once the animation has loaded and is on screen in a visible tab. One that is cut
 *   short (the page is reloaded or left before the file arrives, or the file does not load) shows it again at the next
 *   load of the tab.
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
  // The GIF is 16:9 with the logo and the tagline in the middle band; the frame shows that band, and the static
  // wordmark of a later visit is drawn to the GIF's last frame (see .dc-intro-frame and .dc-wordmark--hero in home.css).
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

/**
 * Is this load of the site, in this tab, to start with the animation? Reads the flag without using it up, and forgets
 * the flags that older versions (and the legacy site) kept in local storage.
 */
export function shouldPlayIntro(): boolean {
  forgetOldFlags();
  return isFirstVisit() && !prefersReducedMotion();
}

export function HeroLogo({ intro }: { intro: boolean }) {
  return intro ? <Intro /> : <Wordmark hero />;
}
