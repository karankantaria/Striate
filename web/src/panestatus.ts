/* The one place a view reports that it could not load something.

   Every view used to swallow fetch failures into `console.warn`, leaving the
   pane blank or, worse, showing the *previous* file's data. For a triage tool
   that is a correctness problem rather than a polish one: a blank Bigram pane
   should mean "this range has no bigram structure", and if it can also mean
   "the request failed" then the tool is lying by omission — and lying in the
   direction of "nothing to see here", which is the dangerous direction.

   So: `paneError` on failure, `clearPaneError` on success. Views that retry
   (the 409/410 "analysis still settling" path) should NOT call this — that
   is an expected transient, not a failure. */

const CLASS = "pane-error";

/** A one-line, human-readable description of a thrown value.

   `api.ts` already unwraps the server's `detail` field into `Error.message`,
   so in practice this is the server's own explanation. */
export function errorText(e: unknown): string {
  if (e instanceof Error && e.message) return e.message;
  if (typeof e === "string" && e) return e;
  return "unknown error";
}

/** Show a failure message over a pane's content area.

    `host` should be the element the view draws into (`.canvas-host` and the
    hex scroller are both `position: relative`, which the overlay needs).

    Pass `retry` wherever the view can re-run the request. Without it a pane
    whose refetch is not triggered by anything the user is likely to do — the
    file-bound Overall view only refetches on resize or a mode change — keeps
    a failure message forever with no way out. Found exactly that by killing
    the server mid-session: five panes recovered on the next click and the
    sixth stayed broken. */
export function paneError(host: HTMLElement | null | undefined,
                          what: string, e?: unknown,
                          retry?: () => void): void {
  if (!host) return;
  let el = host.querySelector<HTMLElement>(`:scope > .${CLASS}`);
  if (!el) {
    el = document.createElement("div");
    el.className = CLASS;
    // announced to screen readers without stealing focus (§3.6 baseline)
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    host.appendChild(el);
  }
  // textContent, never innerHTML: this string carries a server-supplied
  // detail, and a message about a hostile file must not itself be markup.
  el.textContent = e === undefined ? what : `${what} — ${errorText(e)}`;
  if (retry) {
    // a real <button>, so it is focusable and keyboard-reachable rather
    // than a click-only <div> (the mistake §3.6 catalogues elsewhere)
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pane-error-retry";
    button.textContent = "retry";
    button.addEventListener("click", () => {
      clearPaneError(host);
      retry();
    });
    el.appendChild(button);
  }
  el.hidden = false;
}

/** Hide any error currently shown over `host`. Safe to call unconditionally. */
export function clearPaneError(host: HTMLElement | null | undefined): void {
  if (!host) return;
  const el = host.querySelector<HTMLElement>(`:scope > .${CLASS}`);
  if (el) el.hidden = true;
}

/** Is an error currently shown? Exposed for tests. */
export function paneErrorText(host: HTMLElement): string | null {
  const el = host.querySelector<HTMLElement>(`:scope > .${CLASS}`);
  return el && !el.hidden ? el.textContent : null;
}
