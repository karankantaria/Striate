# Striate — release & desktop-app decisions

Handoff document: everything decided so far about how Striate ships, its
branding, and the conventions any new UI screen must follow. Written so a
fresh session (or contributor) can pick up UI or build work without
re-deriving the reasoning.

## 1. What ships, and why

**The canonical GitHub release artifact is the Python wheel + sdist**,
installed with `pipx install binviz` (or pip). Rationale:

- `pyproject.toml` is already release-ready: console entry point
  (`binviz = binviz.cli:main`), pinned deps, src layout.
- Every runtime dependency (lief, capstone, numpy, fastapi, uvicorn,
  pillow) ships prebuilt wheels for Windows/macOS/Linux — one artifact
  serves all platforms, no code signing, no antivirus friction.
- Striate's audience (binary triage / RE-adjacent) has Python and is
  comfortable with pipx.

**No prebuilt executables are attached to releases.** Unsigned frozen
Python exes trip Windows SmartScreen and AV heuristics — and a tool that
bundles capstone + lief and exists to dissect packed/UPX'd binaries is
exactly the profile scanners false-positive on. Instead, the repo carries
everything needed to **build the desktop app yourself** (icons, spec,
instructions), which sidesteps signing entirely: self-built binaries get
no SmartScreen reputation check.

## 2. Desktop app architecture (planned — build code not yet written)

The app is the existing FastAPI service plus the built frontend in one
process:

- `binviz serve` (exists) runs the API on `127.0.0.1:8000`. The service
  gains a static mount: `web/dist` (dev checkout) or packaged copy of it,
  served at `/`, mounted **after** the `/api/*` routes so they take
  precedence. `StaticFiles(html=True)` covers `/`; if the UI ever adds
  client-side routing with deep links, a catch-all route returning
  `index.html` is also needed.
- `binviz app` (planned subcommand): starts the server, then opens a
  pywebview native window pointed at it — falling back to
  `webbrowser.open()` when pywebview isn't installed. pywebview lives in
  an optional extra (`pip install binviz[app]`) so the base install stays
  lean. Window title "Striate", icon from `packaging/icons/`.
- Standalone build: PyInstaller **onedir** (not onefile — onefile
  self-extracts on every launch: slow, and more AV-suspicious), spec file
  to live in `packaging/`, `icon=packaging/icons/icon.ico`, bundling the
  built frontend as data. numpy/pillow have official hooks; capstone
  bundles its own DLL; lief may need `--collect-all lief`. Expect
  80–150 MB, dominated by numpy + lief.

Release pipeline (to be scripted, one command):
`npm run build` in `web/` → copy `web/dist` into the package as data
(e.g. `src/binviz/webui/`) → build wheel. UI-only changes never touch
packaging code — they are just a rebuild of `dist/`.

## 3. Branding

Master mark: **an order-2 Hilbert curve traversing a 4×4 byte grid** —
the same curve the Hilbert surface view walks. The caps are semantic:
**cream cap = offset 0, sage cap = end of file.** Keep that semantic in
any new use of the mark (the login boot animation depends on it).

### Palette

| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#524646` | page background |
| `--panel` | `#453B3B` | recessed cards / panels |
| `--field` | `#372F2F` | input fields, wells |
| `--cream` | `#FCF2E5` | primary text; "offset 0" cap |
| `--sage` | `#A8A492` | labels, metadata; "EOF" cap |
| `--accent` | `#EC5B38` | buttons, focus, **every error signal**, the curve stroke |
| `--ink` | `#2B2424` | text on accent surfaces |
| `--hair` | `rgba(168,164,146,0.22)` | hairline borders |

Type is **all-monospace** (system stack: ui-monospace, SF Mono, Menlo,
Consolas… — no external fonts, ever; the app must work offline). Labels
tracked out in caps; wordmark letter-spacing 0.32–0.42em. Dark theme
only — the analyst-tool register calls for it.

### Asset inventory

| File | Use |
|------|-----|
| `packaging/icons/icon.svg` | master, 1024×1024 viewBox — edit this, re-derive the rest |
| `packaging/icons/icon.ico` | Windows: PyInstaller spec `icon=`, Explorer/taskbar (7 sizes, 16–256) |
| `packaging/icons/icon-256.png` | Linux window icon / pywebview |
| `packaging/icons/icon-512.png` | general purpose |
| `packaging/icons/icon-1024.png` | input for macOS `.icns` (generate with `iconutil` on a Mac when needed) |
| `packaging/icons/favicon.ico` | canonical favicon (16 + 32) |
| `web/public/favicon.ico` | copy consumed by Vite — anything in `web/public/` is served at `/` and copied into `dist/` verbatim |

Browsers request `/favicon.ico` by convention so it works untagged, but
the new UI's `index.html` should still carry
`<link rel="icon" href="/favicon.ico">`.

## 4. Login screen

Reference implementation: **`web/design/login.html`** — self-contained
(inline CSS, inline SVG mark, no frameworks, no external assets). It is a
design reference to be integrated into the new UI, not wired to anything.

What it establishes:

- **Boot sequence**: splash where the Hilbert mark draws itself the way
  Striate walks a file — cream cap pops at offset 0, the stroke traces
  (`stroke-dasharray` animation, ~1.1 s), sage cap lands at EOF, wordmark
  rises, splash wipes to the card. All animation is scoped to a `.boot`
  class set only when JS runs **and** `prefers-reduced-motion` is not
  set — without it the page renders in its final state. Preserve this
  pattern for any future animation.
- Centered recessed card, username + password, accent "Sign in" button,
  reserved error-message area (errors always in `--accent`).
- Keyboard-complete: real labels, tab order, Enter submits.
- Backend contact point is a single `onSubmit(creds)` stub.

**Auth does not exist in the backend yet.** The service currently binds
to localhost with no authentication (why that's been acceptable). Wiring
the login screen for real requires an auth layer on `/api/*` (session
token or similar) — otherwise the login is cosmetic, since anything on
localhost can hit the API directly. Planned as part of the build-tool
work; the frontend should treat "POST credentials → receive token → send
it on subsequent requests" as the working assumption.

## 5. Conventions every new screen must follow

- **Relative API URLs only** — every fetch is `/api/...` (see
  `web/src/api.ts`). Absolute URLs would break same-origin serving in
  the desktop app. The Vite dev proxy (`web/vite.config.ts`) forwards
  `/api` to `127.0.0.1:8000` during development, so the workflow is
  unchanged: `binviz serve` in one terminal, `npm run dev` in another.
- **Bulk numeric data is never JSON.** Signals, histograms, rasters ship
  as raw little-endian typed arrays (`new Float32Array(await
  r.arrayBuffer())`); metadata rides in the `X-Meta` response header.
  Keep this for any new data-heavy endpoint/view.
- **Views live in `web/src/views/`** (one module per view: hexview,
  dotplot, cfg, triage…); shared canvas plumbing in `web/src/canvas/`,
  workers in `web/src/workers/`. Workers must stay same-origin (they're
  bundled by Vite; nothing loads cross-origin).
- The frontend is currently a single page with no router. Adding routed
  screens (e.g. `/login`) is fine but requires the backend catch-all
  described in §2.
- Respect `prefers-reduced-motion` everywhere, as login.html does.
- No external assets of any kind — fonts, CDNs, remote images. The app
  must be fully functional offline.

## 6. Repo map for this work

```
RELEASE.md              this file
packaging/
  icons/                canonical branding assets (see §3)
  <binviz.spec>         PyInstaller spec — to be written
web/
  public/favicon.ico    served at / by Vite, copied into dist/
  design/login.html     login screen reference (see §4)
  src/                  frontend source (views/, canvas/, workers/, api.ts)
  dist/                 build output — gitignored; rebuild with npm run build
src/binviz/             backend package; service.py = FastAPI app
```

Still to build, in rough order: static mount in `service.py` →
`binviz app` subcommand + `[app]` extra → auth layer for the login
screen → PyInstaller spec + release script → README instructions for
"build the desktop app yourself".
