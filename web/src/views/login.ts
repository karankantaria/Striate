/* The sign-in screen (§2.5).

   Ported from `web/design/login.html`, which stays in the repo as the
   design reference — it, not this file, is the spec for how the screen
   looks. What it establishes and this must keep:

   - The boot sequence: the Hilbert mark draws itself the way Striate walks
     a file — the light cap pops at offset 0, the stroke traces, the deep
     cap lands at EOF, the wordmark rises, the splash wipes to the card. The
     offset readout ticks on the same easing curve as the stroke, so the
     number and the trace reach the end together. That semantic (--text =
     offset 0, --deep = EOF) is load-bearing per ARCHITECTURE.md §3; do not
     recolour the caps.
   - `.boot` is set only when JS runs **and** `prefers-reduced-motion` is
     not set. Everything animated is scoped to it, so without it the page
     renders in its final state rather than in a broken half-state.
   - Real labels, real tab order, Enter submits, errors in `--alert`, and
     a reserved error line so the card never jumps.

   What is *not* here: any belief that this screen is the security
   boundary. It is not (§2.1). It is a way to obtain the token that every
   `/api` route checks; anything on the machine can skip this form and talk
   to the API directly, which is exactly why the token exists. */

import { login } from "../api.ts";
import { setToken } from "../auth.ts";
import { el, replace } from "../dom.ts";

const BOOT_MS = 1480;
const TRACE_DELAY_MS = 140;
const TRACE_MS = 1060;
const EOF_OFFSET = 0x00400000;      // 4 MiB, for show
const FALLBACK = "Username or password is incorrect.";

const hex = (n: number) =>
  "0x" + n.toString(16).toUpperCase().padStart(8, "0");

/** The order-2 Hilbert curve the whole brand is built on, and the same
    curve the Hilbert surface view walks. Built as SVG DOM rather than a
    markup string: nothing here is interpolated, but keeping one way to
    build nodes is worth more than the shortcut (§3.5). */
function mark(traced: boolean): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 1024 1024");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("aria-hidden", "true");

  const bg = document.createElementNS(ns, "rect");
  bg.setAttribute("width", "1024");
  bg.setAttribute("height", "1024");
  bg.setAttribute("rx", "208");
  bg.setAttribute("fill", "#3B1C32");

  const path = document.createElementNS(ns, "path");
  if (traced) path.setAttribute("class", "trace");
  path.setAttribute("d", "M146 146 H390 V390 H146 V878 H390 V634 H634 V878 "
                       + "H878 V390 H634 V146 H878");
  path.setAttribute("pathLength", "1000");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#A64D79");
  path.setAttribute("stroke-width", "128");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");

  const cap = (cls: string, cx: string, fill: string) => {
    const c = document.createElementNS(ns, "circle");
    if (traced) c.setAttribute("class", cls);
    c.setAttribute("cx", cx);
    c.setAttribute("cy", "146");
    c.setAttribute("r", "64");
    c.setAttribute("fill", fill);
    return c;
  };

  svg.append(bg, path,
             cap("cap-start", "146", "#F7EFF4"),   // offset 0
             cap("cap-end", "878", "#6A1E55"));    // EOF
  return svg;
}

/** The section-entropy strip capping the card — the same readout Striate
    draws over a loaded binary. Bar heights are a fixed pseudo-profile, not
    a real file: this screen has no binary open, and inventing data that
    looked measured would be the tool lying decoratively. */
const STRIP = [
  10, 17, 20, 22, 27, 25, 24, 18, 14, 11, 14, 18, 16, 14, 12, 10, 15, 11,
  14, 15, 14, 14, 24, 26, 28, 22, 22, 19, 13, 17, 15, 18, 27, 24, 25, 20,
  14, 15, 12, 15, 16, 14, 14, 10, 10, 14, 13, 10, 16, 19, 20, 25, 21, 27,
  26, 20, 18, 14, 19, 22, 20, 22, 25, 21, 22, 12, 16, 14, 16, 16, 15, 16,
  9, 17, 13, 12, 15, 17, 16, 23,
];

/** Bars at or above this fraction of full height are the high-entropy
    band. The split is the encoding: two inks, not one ink at two
    opacities, so the strip says something a gradient could not. */
const STRIP_SPLIT = 0.60;

function entropyStrip(): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "entropy");
  svg.setAttribute("viewBox", "0 0 560 28");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  // two groups rather than a fill per rect: the error state re-points
  // --strip-lo/--strip-hi and both bands move together
  const lo = document.createElementNS(ns, "g");
  lo.setAttribute("fill", "var(--strip-lo)");
  const hi = document.createElementNS(ns, "g");
  hi.setAttribute("fill", "var(--strip-hi)");
  STRIP.forEach((h, i) => {
    const r = document.createElementNS(ns, "rect");
    r.setAttribute("x", String(i * 7));
    r.setAttribute("y", String(28 - h));
    r.setAttribute("width", "5.4");
    r.setAttribute("height", String(h));
    r.setAttribute("opacity", (0.55 + (h / 28) * 0.45).toFixed(2));
    (h / 28 >= STRIP_SPLIT ? hi : lo).appendChild(r);
  });
  svg.append(lo, hi);
  return svg;
}

/* -------------------------------------------------------------- screen */

export interface LoginOptions {
  /** Told by the server that no credential exists yet, so this sign-in
      sets one rather than checking one (§2.3). */
  claiming?: boolean;
  version?: string;
}

/** Render the sign-in screen into `host` and resolve once the token is in
    hand. The caller does not start the app until then. */
export function showLogin(host: HTMLElement,
                          opts: LoginOptions = {}): Promise<void> {
  return new Promise<void>((resolve) => {
    const animate = !matchMedia("(prefers-reduced-motion: reduce)").matches;

    const username = el("input", {
      id: "login-username", name: "username", type: "text",
      autocomplete: "username", autocapitalize: "off",
      autocorrect: "off", spellcheck: "false",
    });
    const password = el("input", {
      id: "login-password", name: "password", type: "password",
      autocomplete: opts.claiming ? "new-password" : "current-password",
    });
    // role="alert" so a failure is announced, not just coloured (§3.6)
    const error = el("p", { class: "login-error", id: "login-error",
                            role: "alert" });
    const submit = el("button", { type: "submit", id: "login-submit" },
                      opts.claiming ? "Set password" : "Sign in");

    const field = (label: string, input: HTMLInputElement) =>
      el("div", { class: "login-field" },
         el("label", { for: input.id }, label), input);

    const form = el("form", { id: "login-form", novalidate: true },
      field("Username", username),
      field("Password", password),
      error,
      submit);

    const card = el("main", { class: "login-card", id: "login-card" },
      entropyStrip(),
      el("div", { class: "login-body" },
        el("header", { class: "login-mast" },
          mark(false),
          el("div", {},
            el("h1", { class: "login-wordmark" }, "Striate"),
            el("p", { class: "login-tagline" },
               opts.claiming ? "Set this install's password" : "Binary triage"))),
        form,
        opts.claiming && el("p", { class: "login-note" },
          "No credential is set for this install. What you enter here "
          + "becomes it."),
        el("p", { class: "login-foot" },
           `binviz ${opts.version ?? ""} · local workspace`)));

    const offset = el("p", { class: "splash-offset" }, hex(0));
    const splash = el("div", { class: "splash", "aria-hidden": "true" },
      el("div", { class: "splash-inner" },
        (() => { const m = mark(animate);
                 m.classList.add("splash-mark"); return m; })(),
        el("p", { class: "splash-word" }, "Striate"),
        offset));

    host.className = "login-screen";
    host.classList.toggle("boot", animate);
    replace(host, splash, card);
    host.hidden = false;

    /* ------------------------------------------------------------ boot */

    let finished = false;
    let raf = 0;
    let timer = 0;

    const reveal = () => {
      if (finished) return;
      finished = true;
      cancelAnimationFrame(raf);
      clearTimeout(timer);
      window.removeEventListener("pointerdown", reveal);
      window.removeEventListener("keydown", reveal);
      offset.textContent = hex(EOF_OFFSET);
      offset.classList.add("is-lit");
      splash.classList.add("is-done");
      card.classList.add("is-in");
      // focus moves to the form only once the splash is gone, so a
      // keystroke during the animation is not swallowed by a hidden field
      window.setTimeout(() => { splash.remove(); username.focus(); }, 360);
    };

    if (!animate) {
      splash.remove();
      card.classList.add("is-in");
      username.focus();
    } else {
      const t0 = performance.now();
      // cubic ease-in-out — the same shape the stroke is drawn with, so
      // the number and the trace land together
      const ease = (p: number) =>
        p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      const tick = (now: number) => {
        const p = Math.min(1, Math.max(0,
          (now - t0 - TRACE_DELAY_MS) / TRACE_MS));
        offset.textContent = hex(Math.round(ease(p) * EOF_OFFSET));
        if (p < 1 && !finished) raf = requestAnimationFrame(tick);
        else offset.classList.add("is-lit");   // hold it lit at EOF
      };
      raf = requestAnimationFrame(tick);
      // any input cuts it short: this is a screen you see every day on a
      // shared machine, and an animation you cannot skip becomes a tax
      timer = window.setTimeout(reveal, BOOT_MS);
      window.addEventListener("pointerdown", reveal);
      window.addEventListener("keydown", reveal);
    }

    /* ------------------------------------------------------------ form */

    const clearError = () => {
      error.textContent = "";
      card.classList.remove("is-error");
      username.removeAttribute("aria-invalid");
      password.removeAttribute("aria-invalid");
    };
    const showError = (message: string, focus?: HTMLInputElement) => {
      error.textContent = message;
      card.classList.add("is-error");
      if (focus) {
        focus.setAttribute("aria-invalid", "true");
        focus.focus();
      }
    };
    for (const input of [username, password]) {
      input.addEventListener("input", clearError);
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();          // Enter in either field lands here too
      clearError();
      const user = username.value.trim();
      const pass = password.value;
      if (!user) return showError("Enter your username.", username);
      if (!pass) return showError("Enter your password.", password);

      const label = submit.textContent;
      submit.disabled = true;
      submit.textContent = opts.claiming ? "Setting" : "Signing in";
      try {
        const { token } = await login(user, pass);
        setToken(token);
        host.hidden = true;
        replace(host);             // the credential is not left in the DOM
        resolve();
      } catch (err) {
        showError((err as Error)?.message || FALLBACK, password);
        password.select();
      } finally {
        submit.disabled = false;
        submit.textContent = label;
      }
    });
  });
}
