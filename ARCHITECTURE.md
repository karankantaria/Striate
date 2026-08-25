# Striate — architecture and conventions

How Striate is put together: what ships and why, the branding every surface
inherits, the conventions any new screen must follow, and the limitations that
are deliberate rather than unfinished.

`SECURITY.md` is the security posture — what is done to protect the tool and
its users, and how to report a problem. `README.md` is the starting point.

`§` references in code comments point here; the appendix at the end decodes
the ones that outlived the work order they came from.

---

## 1. What ships, and why

**The canonical release artifact is the Python wheel + sdist**, installed
with `pipx install binviz` (or pip):

- Console entry point (`binviz = binviz.cli:main`), src layout, published
  dependency **ranges**, not pins — the reasoning is in `pyproject.toml`
  beside the ranges themselves, which is where anyone tempted to tighten
  them will be standing.
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

**Two things besides the UI are staged into the package**, both canonical
elsewhere in the repo and both copied rather than committed: the window
icon (§3) and `corpus/calibration.json`. The second is not cosmetic —
`corpus/` is not in the wheel, so without it an installed binviz falls
back to the hardcoded thresholds in `signals._FALLBACK_CAL` and classifies
windows differently from a checkout (`code_h_lo` 4.5 against the measured
5.31), which is exactly the folklore §2.1 below exists to refuse. The
release workflow asserts the wheel contains it and that it is not itself
the fallback; `/api/config` reports which source is in force; and the
thresholds feed `cache.params_fingerprint()`, because they change analysis
output and a stale verdict computed under old thresholds is the kind of
wrong answer this tool must not give quietly.

**Serving.** The packaged frontend is mounted after every `/api/*` route,
with a catch-all returning `index.html` so client-side routes survive a hard
refresh. `web/dist` lives outside the Python package, so
`python tools/build_ui.py` must stage it into `src/binviz/webui/` before a
wheel is built — a wheel without that step installs a backend and no UI, and
does so **silently**. The release workflow asserts the bundle is in the
built wheel for exactly that reason.

**Freezing.** `packaging/binviz.spec` is §1's "build it yourself" half:
`pip install pyinstaller`, `python tools/build_ui.py`, `pyinstaller
packaging/binviz.spec` → `dist/binviz/` (~100 MB, numpy and lief). The
entry point is `packaging/launcher.py`, which is the wheel's CLI with one
addition — **no argv means `app --auth local`**, because a double-clicked
executable passes none and bare `binviz` exits 2 into a console that closes
instantly.

The `--auth local` half of that default applies the desktop-packaging rule
below — keep the token, keep the bridge minimal — to the one launch path
with no terminal behind it. `binviz app` typed into a shell is a deliberate
act by whoever owns the session and keeps the parser's own `--auth none`; a
double-click establishes nothing, so the window asks for the credential
rather than signing you in invisibly. An explicit `--auth none` on the
command line still wins.

onedir, `upx=False` and `console=True` are each argued in the spec's
docstring and pinned by `tests/test_packaging.py`; the short version is that
every safe value costs something, so drift has a direction. The spec
**refuses to build** without a staged frontend: that is the same failure as
a wheel shipping no UI, and a local build has no release workflow to catch
it.

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

### 2.1 Thresholds are measured, not chosen

Every threshold that decides what a window *is* — code against data against
compressed against encrypted — comes from `corpus/calibrate.py` measuring a
ground-truth corpus, and is written to `corpus/calibration.json`. None of them
is a number somebody liked the look of.

This is a correctness rule, not a preference. A binary analyser that classifies
by folklore constants produces confident, plausible, wrong answers, and the
population that runs this tool is exactly the one that cannot afford them. Two
consequences follow and are enforced rather than trusted:

- **The measured file must ship.** `corpus/` is not in the wheel, so
  `tools/build_ui.py` stages `calibration.json` into the package and the
  release workflow fails the build if it is missing or is itself the fallback.
  `packaging/binviz.spec` does the same for the frozen desktop build.
- **Moving a threshold invalidates the analyses computed under the old one.**
  The calibration feeds `cache.params_fingerprint()`, so re-running
  `calibrate.py` re-analyses what it needs to and nothing else. A cached
  verdict computed under thresholds that have since moved is precisely the
  quiet wrong answer the rule exists to prevent.

`signals._FALLBACK_CAL` exists only so the library imports in a checkout with
no corpus built yet. It is **not a shipping mode**; `/api/config` reports which
source is in force so that "am I running on the fallback?" is a question with
an answer.

---

## 3. Branding

Master mark: **an order-2 Hilbert curve traversing a 4×4 byte grid** — the
same curve the Hilbert surface view walks. The caps are semantic: **light
cap = offset 0, deep cap = end of file.** Keep that semantic in any new use
of the mark; the login boot animation depends on it.

### Palette

The four brand values are a single-hue ramp — four dark plums, no light
value — so **nothing in the ramp can carry text.** The brightest, `--rose`,
reaches 3.27:1 on `--ink`, below the 4.5:1 body-text threshold. `--text` and
`--muted` are two neutrals tinted off the same hue so they read as family;
they are the only two values text is ever set in. There is no third, dimmer
step: at 0.66 opacity `--muted` falls to 3.3:1, so hierarchy below `--muted`
is carried by size and tracking, never by fading the ink.

| Token | Hex | Role |
|-------|-----|------|
| `--void` | `#121215` | the page, one step below `--ink`; wells and inputs |
| `--ink` | `#1A1A1D` | panes, toolbar, dialogs, tooltips; **the chart surface** |
| `--plum` | `#3B1C32` | raised: hovered rows, chips, badges, the sign-in card, the icon tile |
| `--deep` | `#6A1E55` | low-entropy band, pressed button, "EOF" cap |
| `--rose` | `#A64D79` | buttons, focus, high-entropy band, the curve stroke |
| `--text` | `#F7EFF4` | primary text (15.4:1 on ink, 13.3:1 on plum); "offset 0" cap |
| `--muted` | `#C98CA8` | labels, metadata (6.5:1 on ink, 5.6:1 on plum) |
| `--alert` | `#FF7A8A` | **every error signal** (6.0:1 on plum) |
| `--hair` | `rgba(166,77,121,0.30)` | hairline borders |

**`--plum` is not the pane surface, and that is the point.** The ramp's
lightest structural value is also its most saturated. On one 380px sign-in
card it reads as the brand; behind nine panes it reads as a lit screen. So
the surfaces run *down* from `--ink` — `--void` page, `--ink` panes, wells
back to `--void` — and `--plum` is spent on small raised things where the
saturation is a signal. `--void` exists because a pane cannot be `--ink`
and sit on `--ink`. The sign-in screen is the documented exception: it
keeps the design reference's own `--ink` page and `--plum` card, so the app
shifts a step darker when the card dismisses.

**Errors are `--alert`, not `--rose`.** `--rose` is the primary action, and
an error must not be the colour of the button that just failed. `--alert` is
the one value outside the four; set it to `var(--rose)` to stay strictly
inside the ramp — every error-related rule routes through that one token.

Two consequences worth keeping: the button does **not** brighten on hover
(lightening `--rose` pushes its label to 3.89:1), so hover is a soft outer
glow and the press darkens to `--deep` (9.6:1); and the login entropy strip
is genuinely two-tone — low bars `--deep`, high bars `--rose`, split at 0.60
— rather than one ink at varying opacity.

Type is **all-monospace** (system stack — no external fonts, ever; the app
must work offline). Labels tracked out in caps; wordmark letter-spacing
0.32–0.42em. **Dark theme only** — there is no light theme and no toggle, so
nothing is duplicated and nothing can drift.

Live in `web/src/theme.css`. Note `--ink` is the *page*, not text on accent;
text on `--rose` is `--text`, and text on `--alert` is `--ink`.

### Chart colours are computed, not chosen

The byte-class and signal-series palettes are painted into canvases, so they
are not CSS tokens — they live in `web/src/colormap.ts`. Every value was
generated at a target OKLCH lightness/hue and validated against `--ink`
for lightness band, chroma floor, OKLab ΔE under simulated protanopia and
deuteranopia, normal-vision separation, and WCAG contrast.

The categorical slots are used exactly as the validator produced them. Only
the brand-tied values are chosen by hand: byte class `null` tracks the panel,
`0xff` is `--text`, and the brush-to-locate ink is `--alert` — `--rose` sits
too close to the `high` class to survive a raster full of it. Note that the
chart surface is `--ink` rather than a lighter panel, which *raises* every
contrast figure above, so darkening a surface is the safe direction and
lightening one means re-running the validator.

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
| `packaging/icons/icon-1024.png` | the raster master — every size below is derived from it |
| `packaging/icons/icon.icns` | macOS `.app` bundle icon; `python tools/make_icns.py` re-derives it |
| `packaging/icons/favicon.ico` | canonical favicon (16 + 32) |
| `web/public/favicon.ico` | copy Vite serves at `/` and copies into `dist/` |

`tools/build_ui.py` stages `icon.ico` + `icon-256.png` into
`src/binviz/icons/` (gitignored) so a wheel has them. They are **copied, not
committed**, because `packaging/icons/` is canonical and two copies of the
branding in one repo is how they drift.

`icon.icns` is **not** staged into the wheel: it is the `.app` bundle's
icon and nothing else reads it. The wheel's non-Windows window icon is the
PNG, because that is what GTK and Qt take.

> **Generating `.icns` does not require a Mac.** `iconutil` is a container
> tool — the file is a header, a length, and a run of PNGs under
> four-character type codes, and the scaling on the way in is an ordinary
> Lanczos downsample. `tools/make_icns.py` writes the same ten entries
> `iconutil -c icns` emits from a standard `.iconset`, downsampled from
> the 1024px master rather than redrawn (a second copy of the mark at
> 16px is exactly the drift this section exists to prevent). It can also
> emit the `.iconset` itself, so a Mac owner can regenerate the file the
> canonical way and compare. `tests/test_packaging.py` walks the
> container and checks every entry is a PNG of the size its type code
> promises, because the platform that would notice a malformed one is the
> platform this file is generated without.

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

## 5. Known limitations

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
- **The desktop layer is verified on Windows only.** The test suite runs on
  Linux in CI as well, but `binviz app` does not: the Linux and macOS
  pywebview backends, the macOS `.app` bundle, and the `0o600` credential
  mode (skipped as advisory on Windows) have not been exercised on their own
  platforms. The wheel and the CLI are not affected — those are what CI
  covers.
- **The desktop file dialog's last inch is unverified.** Its code path is
  tested with a fake window — opens at `--root`, single selection,
  re-entrancy blocked — but a dialog actually returning a path needs a human
  in front of it.

---

## 6. Repo map

```
ARCHITECTURE.md         this file — what ships, branding, conventions
README.md               what the tool is and how to run it
SECURITY.md             security posture and disclosure route
LICENSE                 MIT
pyproject.toml          packaging metadata; dependency ranges and why
constraints-dev.txt     exact versions the suite is green against
.github/workflows/      publish.yml — Trusted Publishing
corpus/                 ground-truth samples: build.py compiles them with
                        zig cc, calibrate.py measures the thresholds (§2.1)
tests/                  the Python suite; conftest.py gates on the corpus
docs/plates/            rendered examples used by README.md
docs/screenshots/       UI screenshots (shot list; images not committed yet)
packaging/
  icons/                canonical branding (§3)
  binviz.spec           PyInstaller spec: onedir desktop build (§2)
  launcher.py           its entry point — the CLI, defaulting to
                        `app --auth local`
tools/build_ui.py       stages web/dist + icons + calibration into the package
tools/make_icns.py      derives packaging/icons/icon.icns from the master
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
retired now that every item in it is closed. The design document those
comments once cited as `plan §5.x` has been retired too — its one
load-bearing argument, that thresholds are measured rather than chosen, is
§2.1 above. The rest decode as:

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
