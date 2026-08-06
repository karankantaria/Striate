/* Session token for the API (S1a).

   The server mints a token at startup and prints it as a clickable URL.
   This module accepts it once from `?token=…`, moves it into sessionStorage,
   and strips it from the address bar — after which every request carries it
   as a header instead.

   Why bother moving it: a token left in the URL leaks through `Referer` on
   any outbound link, through the browser history, through anything that
   screenshots or shares the address bar, and through the server's own access
   log. A header goes to exactly one place. The query parameter exists only
   because a freshly launched browser has nowhere else to get the token from.

   sessionStorage rather than localStorage: the token dies with the tab,
   which matches a token that dies with the server process. */

const KEY = "binviz-token";

/** How this page is expected to authenticate, as the server told it.

    - `none`  the server injected a token into the HTML; nothing to do.
    - `local` a credential must be exchanged for one at /login (§2.2).
    - `off`   `--no-auth`; there is no token to have.
    - `unknown` the page was not served by binviz — the Vite dev server has
      no idea about any of this, and the proxy attaches the token itself. */
export type AuthMode = "none" | "local" | "off" | "unknown";

interface Boot {
  auth_mode?: string;
  token?: string;
  tool_version?: string;
  /** `local` mode only: no credential is set yet, so the next sign-in
      sets one rather than checking one (§2.3). */
  claiming?: boolean;
}

/** The bootstrap the server stamped into the page it served.

    A `<meta>` rather than an inline `<script>`: the CSP is `script-src
    'self'` with no `unsafe-inline` (S2), and loosening that to pass one
    string across would trade the XSS defence for a convenience. */
function readBoot(): Boot {
  try {
    const meta = document.querySelector<HTMLMetaElement>(
      'meta[name="binviz-boot"]');
    const raw = meta?.content?.trim();
    if (!raw) return {};
    const doc: unknown = JSON.parse(raw);
    return typeof doc === "object" && doc !== null ? doc as Boot : {};
  } catch {
    return {};      // a malformed bootstrap must not blank the whole app
  }
}

const boot = readBoot();

export const authMode: AuthMode =
  boot.auth_mode === "none" || boot.auth_mode === "local"
    || boot.auth_mode === "off" ? boot.auth_mode : "unknown";

function bootstrap(): string | null {
  try {
    const url = new URL(window.location.href);
    const fromUrl = url.searchParams.get("token");
    if (fromUrl) {
      sessionStorage.setItem(KEY, fromUrl);
      url.searchParams.delete("token");
      history.replaceState(null, "", url.toString());
      return fromUrl;
    }
    // The injected token wins over a stored one: a restarted server mints a
    // fresh token, and a stale sessionStorage entry from the previous run
    // would 401 every request until the tab was closed.
    if (boot.token) {
      sessionStorage.setItem(KEY, boot.token);
      return boot.token;
    }
    return sessionStorage.getItem(KEY);
  } catch {
    return null;   // storage disabled, or a non-browser context (tests)
  }
}

let token: string | null = bootstrap();

/** Does the user have to sign in before the app can do anything? */
export function needsLogin(): boolean {
  return authMode === "local" && !token;
}

/** What the sign-in screen needs to know, straight from the bootstrap. */
export function loginContext(): { claiming: boolean; version: string } {
  return { claiming: !!boot.claiming, version: boot.tool_version ?? "" };
}

/** The token, or null when the server is running with --no-auth. */
export function getToken(): string | null {
  return token;
}

export function setToken(value: string | null): void {
  token = value;
  try {
    if (value) sessionStorage.setItem(KEY, value);
    else sessionStorage.removeItem(KEY);
  } catch { /* storage disabled */ }
}

/** Request headers with the token attached, if we have one.

    In dev the Vite proxy can inject the header instead (set BINVIZ_TOKEN
    before `npm run dev`), so a missing token here is not necessarily an
    error — the request may still be authenticated by the time it lands. */
export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}
