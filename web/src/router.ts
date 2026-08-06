/* Client-side routing (SECURITY-UI-WORKORDER §3.4).

   Deliberately built once, generally, rather than as a workspace switcher:
   §2.5 needs `/login` as a screen of its own, and retrofitting a second
   navigation mechanism later is how an app ends up with two.

   Path-based, not hash-based, because both servers already fall through to
   `index.html` for unknown paths — the packaged mount added that in §4.1 and
   Vite's dev server does it by default — so a deep link survives a hard
   refresh. That fallback is the *precondition* for this file: without it
   `/bytes` would 404 on reload, and a hash router would be the right answer
   instead.

   The URL is the state. Nothing here is persisted to localStorage: the
   address bar already remembers, is shareable, and works with the back
   button, which a stored preference does none of.

   `location.search` is preserved across navigations on purpose — `?path=`
   names the open file, and losing it on a tab click would close the binary
   the user was looking at. (`?token=` is stripped by auth.ts long before
   this ever runs; see S1a.) */

/** Trim a pathname to its canonical form: no trailing slash, never empty. */
export function normalizePath(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

/** Resolve a pathname to one of `known`, or `fallback` if it names nothing.

    Unknown paths deliberately resolve rather than throw: a stale bookmark or
    a typo should land somewhere usable, not on an error screen. */
export function resolveRoute(
  pathname: string, known: readonly string[], fallback: string,
): string {
  const path = normalizePath(pathname);
  return known.includes(path) ? path : fallback;
}

export type RouteHandler = (route: string) => void;

export class Router {
  private known: readonly string[];
  private fallback: string;
  private handler: RouteHandler | null = null;

  constructor(known: readonly string[], fallback: string) {
    this.known = known;
    this.fallback = fallback;
  }

  /** The route the address bar currently names. */
  get route(): string {
    return resolveRoute(location.pathname, this.known, this.fallback);
  }

  /** Begin routing: fires `handler` once for the initial URL, then on every
      back/forward. If the initial path named nothing, the address bar is
      corrected with `replaceState` so it stops claiming a route that does
      not exist — silently rendering something else under the wrong URL
      would break sharing and the back button both. */
  start(handler: RouteHandler): void {
    this.handler = handler;
    window.addEventListener("popstate", () => this.handler?.(this.route));
    const route = this.route;
    if (normalizePath(location.pathname) !== route) {
      history.replaceState({}, "", route + location.search);
    }
    handler(route);
  }

  /** Navigate. A no-op when already there, so clicking the active tab does
      not pile duplicate entries onto the back stack. */
  go(route: string, { replace = false } = {}): void {
    const target = resolveRoute(route, this.known, this.fallback);
    if (target === this.route) return;
    const url = target + location.search;
    if (replace) history.replaceState({}, "", url);
    else history.pushState({}, "", url);
    this.handler?.(target);
  }
}
