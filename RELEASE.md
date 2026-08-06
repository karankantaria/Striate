# Striate — release, branding, and what is left

Handoff document: how Striate ships, the conventions any new screen must
follow, and the work that has not been done. Written so a fresh session can
pick up without re-deriving the reasoning.

Companions: `HANDOVER.md` is the engineering history and the gotchas list;
`SECURITY.md` is the public-facing posture and disclosure route; `PLAN.md` is
the analysis design (`§5.x` references in code point there).

Last updated **2026-08-06**, after the security/UI work order was completed
and retired.

---

## 1. What ships, and why

**The canonical release artifact is the Python wheel + sdist**, installed
with `pipx install binviz` (or pip):

- Console entry point (`binviz = binviz.cli:main`), src layout, published
  dependency **ranges** (see §6 — this used to say "pinned", and pinning was
  wrong for a published distribution).
- Every runtime dependency (lief, capstone, numpy, fastapi, uvicorn, pillow)
  ships prebuilt wheels for Windows/macOS/Linux — one artifact serves all
  platforms, no code signing, no antivirus friction.
- The audience (binary triage / RE-adjacent) has Python and is comfortable
  with pipx.

**No prebuilt executables are attached to releases.** Unsigned frozen Python
exes trip Windows SmartScreen and AV heuristics — and a tool that bundles
capstone + lief and exists to dissect packed binaries is exactly the profile
scanners false-positive on. Instead the repo carries what you need to
**build the desktop app yourself**, which sidesteps signing entirely.

---

## 2. What exists now

The app is the FastAPI service plus the built frontend in one process.

| Command | What it does |
|---|---|
| `binviz serve` | The API + UI on `127.0.0.1:8000`. |
| `binviz app` | The same thing in a native window (`pip install "binviz[app]"`). Falls back to your browser without pywebview. |
| `binviz passwd` | Sets this install's sign-in credential, for `--auth local`. |

**Serving.** The packaged frontend is mounted after every `/api/*` route,
with a catch-all returning `index.html` so client-side routes survive a hard
refresh. `web/dist` lives outside the Python package, so
`python tools/build_ui.py` must stage it into `src/binviz/webui/` before a
wheel is built — a wheel without that step installs a backend and no UI, and
does so **silently**. The release workflow asserts the bundle is in the
built wheel for exactly that reason.

**Authentication.** Every `/api` route requires a token. How the browser
*gets* it is the only thing that varies:

- `none` (default) — the server injects the token into the HTML it serves.
  No login screen, nothing to copy; the desktop app is one click.
- `local` (`--auth local`) — the sign-in screen exchanges a credential
  (scrypt, per-install salt, mode 0600) for the token.
- `--no-auth` — CI only, prints a banner naming what it disabled. Not
  available on `binviz app`.

**The login screen is not the security boundary.** The token check is.
Anything on the machine can skip the form and call the API directly, which is
why the token exists. Same for the desktop window: it does not remove the
network listener, it only makes it easier to forget there is one.

**The desktop bridge is one method.** `binviz app` passes a `js_api` object
to pywebview, and *every public attribute of it is reachable from any script
running in that window* — in a tool whose purpose is opening files an
attacker chose. It therefore exposes exactly `pick_file()`, which takes no
arguments. `tests/test_app.py` fails if a second method appears; that
failure is the design working, not a test to update.

**Routing.** `web/src/router.ts` is path-based (`/bytes`, `/patterns`,
`/code`, `/all`, `/login`), which works because both the packaged mount and
Vite fall through to `index.html`. The URL is the state — no localStorage
copy — so Back and deep links behave.

---

## 3. Branding

Master mark: **an order-2 Hilbert curve traversing a 4×4 byte grid** — the
same curve the Hilbert surface view walks. The caps are semantic: **cream
cap = offset 0, sage cap = end of file.** Keep that semantic in any new use
of the mark; the login boot animation depends on it.

### Palette

| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#524646` | page background |
| `--panel` | `#453B3B` | recessed cards / panes; **the chart surface** |
| `--field` | `#372F2F` | input fields, wells |
| `--cream` | `#FCF2E5` | primary text; "offset 0" cap |
| `--sage` | `#A8A492` | labels, metadata; "EOF" cap |
| `--accent` | `#EC5B38` | buttons, focus, **every error signal**, the curve stroke |
| `--ink` | `#2B2424` | text **on** accent surfaces — not primary text |
| `--hair` | `rgba(168,164,146,0.22)` | hairline borders |

Type is **all-monospace** (system stack — no external fonts, ever; the app
must work offline). Labels tracked out in caps; wordmark letter-spacing
0.32–0.42em. **Dark theme only** — there is no light theme and no toggle, so
nothing is duplicated and nothing can drift.

Live in `web/src/theme.css`. Note `--ink` means text *on* accent; the
stylesheet's primary-text colour is `--cream`.

### Chart colours are computed, not chosen

The byte-class and signal-series palettes are painted into canvases, so they
are not CSS tokens — they live in `web/src/colormap.ts`. Every value was
generated at a target OKLCH lightness/hue and validated against `--panel`
for lightness band, chroma floor, OKLab ΔE under simulated protanopia and
deuteranopia, normal-vision separation, and WCAG contrast.

**Re-run the validator before changing any of them.** Three things that
contradict what eyeballing suggests, learned doing it:

1. Equal lightness makes colour-vision separation *worse*, not better —
   under deuteranopia hue is what collapses and lightness is what is left.
2. The byte-class raster must be validated all-pairs, not adjacent-only: any
   class can end up touching any other in a raster.
3. Red-against-green is the classic protan collision and it bit; control
   bytes are amber for that reason.

### Asset inventory

| File | Use |
|------|-----|
| `packaging/icons/icon.svg` | master, 1024×1024 viewBox — edit this, re-derive the rest |
| `packaging/icons/icon.ico` | Windows: PyInstaller `icon=`, **and the `binviz app` window icon** |
| `packaging/icons/icon-256.png` | Linux/macOS window icon |
| `packaging/icons/icon-512.png` | general purpose |
| `packaging/icons/icon-1024.png` | input for macOS `.icns` (`iconutil`, on a Mac) |
| `packaging/icons/favicon.ico` | canonical favicon (16 + 32) |
| `web/public/favicon.ico` | copy Vite serves at `/` and copies into `dist/` |

`tools/build_ui.py` stages `icon.ico` + `icon-256.png` into
`src/binviz/icons/` (gitignored) so a wheel has them. They are **copied, not
committed**, because `packaging/icons/` is canonical and two copies of the
branding in one repo is how they drift.

> **The window icon format is not cosmetic.** Handing the Windows backend a
> PNG throws `System.ArgumentException` from inside .NET — an unhandled
> exception on a foreign thread that kills the process *before any Python
> `except` sees it*, so the app just fails to open. `.ico` on Windows, `.png`
> elsewhere; `app.icon_path()` picks by platform and a test pins it.

---

## 4. Conventions every new screen must follow

- **Relative API URLs only** — every fetch is `/api/…` (`web/src/api.ts`).
  Absolute URLs break same-origin serving in the desktop app.
- **Bulk numeric data is never JSON.** Signals, histograms, rasters ship as
  raw little-endian typed arrays; metadata rides in the `X-Meta` header.
- **Views live in `web/src/views/`**; shared canvas plumbing in
  `web/src/canvas/`, workers in `web/src/workers/`. Workers stay same-origin.
- **Never build HTML by concatenation.** `web/src/dom.ts` has the element
  builder and the `html` tagged template; `innerHTML` appears in exactly one
  place in the codebase and a test enforces that. A forgotten escape used to
  be a live XSS in a tool that opens hostile files.
- **Report failures visibly.** `web/src/panestatus.ts` — `paneError` /
  `clearPaneError`, every call site passing a retry. A blank pane must mean
  "no such structure in this file", never "the request failed".
- **Lists are keyboard-navigable.** `web/src/listnav.ts` turns a row list
  into an ARIA listbox with a roving tabindex. Use it rather than adding
  click handlers to `<div>`s.
- **New pane?** Give it a `grid-area`, add it to a workspace's `panes` and to
  that workspace's `grid-template-areas` in `theme.css`. The tests fail if
  you forget one — including if the pane ends up in no workspace and is
  therefore unreachable.
- **Respect `prefers-reduced-motion`** — scope animation to a class set only
  when JS runs *and* reduced motion is not requested, as the login screen
  does.
- **No external assets of any kind.** Fonts, CDNs, remote images. The app
  must be fully functional offline.
- **Read the size when you use it.** A pane can be hidden now, so
  `clientWidth` at load time may be 0. Views that read it at *draw* time are
  fine; one that cached it at load time was silently broken.

---

## 5. What is left to build

Nothing here is a bug. In rough order:

1. **PyInstaller spec** — `packaging/binviz.spec`, the one piece of §1's
   "build it yourself" promise that does not exist yet.
   - **onedir, not onefile**: onefile self-extracts on every launch — slow,
     and more AV-suspicious for exactly the reasons in §1.
   - `icon=packaging/icons/icon.ico`; bundle the staged frontend
     (`src/binviz/webui/`) and icons as data.
   - numpy/pillow have official hooks; capstone bundles its own DLL; lief
     may need `--collect-all lief`. Expect 80–150 MB, dominated by numpy and
     lief.
   - Add README instructions for building it yourself.
2. **macOS `.icns`** from `icon-1024.png` with `iconutil`, on a Mac.
3. **First release** — see §7, which is owner-only.

---

## 6. Open questions — decisions for the owner

- **`lief>=1.0,<1.1` is deliberately tight.** lief's API moves fast and it is
  the component whose failure mode is a *plausible but wrong parse* rather
  than an exception, and nothing else in a normal environment depends on it,
  so the resolution cost of a narrow range is near zero. The price is that
  lief 1.1 will require a binviz release even if it is compatible. Keep, or
  widen to `<2` and rely on the parser tests?
- **`--auth local` has a claim window.** If no credential exists, the first
  sign-in becomes the account — the startup banner warns in four lines of
  `!!`, and `binviz passwd` closes it ahead of time. The alternative was
  refusing to start without a credential, which locks out a desktop user who
  only ever double-clicks an icon. Current behaviour was chosen
  deliberately; revisit if the shared-machine case becomes the common one.
- **Two version numbers, on purpose.** `binviz.__version__` is the
  distribution version (pyproject reads it). `cache.TOOL_VERSION` is the
  *analysis* version and feeds the cache fingerprint — bumping it discards
  every cached analysis on every install, so a UI-only release must not
  touch it. They read the same today. Keep them separate.
- **`README.md` says "Phase 3 complete"** while `HANDOVER.md` says Phase 12.
  Project status is your call, but it is the first thing a visitor to a
  public repo reads.

---

## 7. Publishing (owner-only)

`.github/workflows/publish.yml` publishes to PyPI with **Trusted
Publishing** — OIDC, no API token anywhere in the repo or its secrets. For a
tool in this space a stolen publish token is the worst available outcome:
the next `pip install binviz` would hand an attacker's code to the exact
population that opens hostile files for a living.

**One-time setup, before the workflow can run:**

```
PyPI -> project -> Publishing -> Add a new pending publisher
  Owner: karankantaria    Repository: Striate
  Workflow: publish.yml   Environment: pypi
```

The environment name is half of the identity PyPI checks, and it is where a
required reviewer can be attached so a publish cannot happen without a human.

Three jobs: `build` (stage the UI, run the suite, build both distributions,
assert the wheel contains the UI + licence), `floors` (run the suite against
the *oldest* version every dependency range allows), and `publish`, which
needs both and only fires on a published release.

> **The workflow has never actually run.** It is written and locally
> rehearsed — the wheel check and the floor resolution were both executed by
> hand — but no CI run has happened, so treat the first `workflow_dispatch`
> as part of the release, not as a formality.

Exact versions the suite is green against live in `constraints-dev.txt`:

```sh
pip install -e ".[dev,app]" -c constraints-dev.txt
```

---

## 8. Known limitations

Deliberate, documented, and worth knowing before someone "fixes" them:

- **Three views still fetch while hidden.** Bigram, Image and Trigram issue
  their one bounded request on a selection change even when their workspace
  is not showing. That is what they cost when all ten panes were always
  visible, so it is not a regression. The expensive ones — the dot plot's
  sampling loop and the trigram's GL loop — *are* gated. Gating the rest
  needs a refetch-on-show each, and getting that wrong shows the previous
  selection's data, which is worse than the waste.
- **The hex dump and the image view are still mouse-only** for placing the
  caret. The findings list, region list and function list are all
  keyboard-navigable; those two are dense canvas surfaces and giving them
  keyboard equivalents means a caret model — a feature, not a baseline fix.
- **Some chart colours WARN on contrast** (three series slots, two byte
  classes) and are kept: the plot titles every lane and names every series
  in its legend, and a filled raster is read against itself rather than
  against a background you can see. Identity never rests on colour alone.
- **`--auth local` does nothing under `npm run dev`.** The Vite dev server
  serves its own `index.html` with no bootstrap meta, so the frontend sees
  no auth mode and skips the login screen; dev authenticates through the
  proxy instead (`BINVIZ_TOKEN`). `local` mode is a packaged-build feature.
- **Everything was verified on Windows.** The Linux/macOS pywebview
  backends, and the `0o600` credential mode (skipped as advisory on
  Windows), have not been exercised on their own platforms.
- **The desktop file dialog's last inch is unverified.** Its code path is
  tested with a fake window — opens at `--root`, single selection,
  re-entrancy blocked — but a dialog actually returning a path needs a human
  in front of it.

---

## 9. Repo map

```
RELEASE.md              this file — shipping, branding, what is left
HANDOVER.md             engineering history + the gotchas list
SECURITY.md             public posture and disclosure route
PLAN.md                 analysis design (code's §5.x references)
LICENSE                 MIT
constraints-dev.txt     exact versions the suite is green against
.github/workflows/      publish.yml — Trusted Publishing
packaging/
  icons/                canonical branding (§3)
  <binviz.spec>         PyInstaller spec — to be written (§5)
tools/build_ui.py       stages web/dist + icons into the package
web/
  design/login.html     login screen design reference
  src/                  frontend (views/, canvas/, workers/, api.ts, …)
src/binviz/
  service.py            the FastAPI app
  auth.py               local sign-in credentials
  app.py                desktop window + the js_api bridge
  cache.py              artifact cache, TOOL_VERSION, params fingerprint
```

---

## Appendix — decoding `§` references in code comments

Comments and test docstrings cite a security/UI work order that has been
retired now that every item in it is closed. `§5.x` references point at
`PLAN.md` and still resolve; the rest decode as:

| Ref | What it was |
|---|---|
| S1a–S1d | Unauthenticated file read: startup token, `Host` allowlist, real CORS, `--root` confinement |
| S2 | XSS from a malicious binary's metadata, plus the CSP |
| S3 / S4 | Malformed query params returning 500; unbounded raster dimensions |
| S5 / S6 / S7 | Upload size cap; unbounded cache growth; unbounded analysis concurrency |
| §2.1 | "A login screen that only gates the UI is cosmetic" |
| §2.2 / §2.3 | The three auth modes; how a local credential must be stored |
| §2.4 | Desktop packaging: keep the token, keep the `js_api` surface minimal, no devtools in release |
| §2.5 | Wiring the login screen design |
| §3.1 / §3.2 | File picker button; first-run empty state |
| §3.3 / §3.5 | Silent fetch failures; one escaper, stop hand-building HTML |
| §3.4 / §3.6 / §3.7 | Workspace navigation; accessibility baseline; the 404-after-open race |
| §4.1 | The wheel shipped no UI |
| §4.2 / §4.3 / §4.5 | Dependency ranges; the missing LICENSE; Trusted Publishing |
| §4.6 | Why open-sourcing raises the stakes: the audience is malware analysts |
