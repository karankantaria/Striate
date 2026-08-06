/* Session token for the API (SECURITY-UI-WORKORDER S1a).

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
    return sessionStorage.getItem(KEY);
  } catch {
    return null;   // storage disabled, or a non-browser context (tests)
  }
}

let token: string | null = bootstrap();

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
