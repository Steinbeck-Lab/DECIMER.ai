import { useSyncExternalStore } from 'react';

export type Route = '/' | '/about' | '/privacy' | '/impressum';

const ROUTES: readonly Route[] = ['/', '/about', '/privacy', '/impressum'];

/**
 * A hash router: `#/about`. The build uses a relative base so the same files
 * serve `/beta/` and `/` (ROADMAP §3), and with hash routes a deep link needs no
 * server rule and no knowledge of the base path. It is all this app needs for
 * four routes, so there is no router dependency.
 */
export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '').replace(/\/+$/, '') || '/';
  return (ROUTES as readonly string[]).includes(path) ? (path as Route) : '/';
}

export function hrefFor(route: Route): string {
  return `#${route}`;
}

function subscribe(listener: () => void): () => void {
  window.addEventListener('hashchange', listener);
  return () => window.removeEventListener('hashchange', listener);
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  return parseHash(hash);
}

export function navigate(route: Route): void {
  window.location.hash = route;
}
