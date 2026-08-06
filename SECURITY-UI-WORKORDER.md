# Striate — security, UI, and release work order

Handover for a fresh session. This is the **work to be done**; `RELEASE.md`
(see §0.1 — it is on another branch) is the **decisions already made** about
branding, packaging architecture, and screen conventions. Read that first,
then this. Where the two disagree, this document wins and says so explicitly.

Everything below was verified against the code on 2026-08-06, running the app
live. Findings marked **[verified]** were reproduced against a running server,
not inferred from reading. Line numbers are from that same revision — re-check
them before editing, they drift.

---

## 0. Read this first — repo state is not what it looks like

### 0.1 Prior work lives on a different branch

`RELEASE.md`, the canonical icons, and the login screen reference are **not on
`main`**. They are on branch `worktree-branding-release-docs`, commit `ca13e62`
("Branding assets, login screen reference, and RELEASE.md decision doc"):

```
RELEASE.md                    release + desktop-app decisions (READ THIS)
packaging/icons/              icon.svg (master), icon.ico, icon-{256,512,1024}.png, favicon.ico
web/design/login.html         login screen design reference, self-contained
web/public/favicon.ico        copy Vite serves at /
```

**First task: get that branch into `main`.** Nothing else in this document
should start until the assets are on one branch, because both the packaging
work and the login work depend on them.

### 0.2 There is an untracked duplicate of the assets

`icons temp/` in the main checkout is **untracked** and holds a near-copy of the
same assets plus `login (1).html` and `pallete.md`. The palette in `pallete.md`
matches `RELEASE.md` §3, so nothing is lost. After merging the branch, delete
`icons temp/` — do not let two copies of the branding diverge. Treat
`packaging/icons/` as canonical.

### 0.3 `.gitignore` is silently broken **[verified]**

The `.claude/` entry was written as UTF-16LE, so git reads a pattern full of
null bytes that matches nothing:

```
$ tail -c 24 .gitignore | xxd
2e00 6300 6c00 6100 7500 6400 6500 2f00   . c l a u d e /
```

`git check-ignore -v .claude/` confirms it is **not ignored**. This is the
PowerShell `Out-File` / `>>` default-encoding trap. Rewrite `.gitignore` as
UTF-8 and re-verify with `git check-ignore`.

### 0.4 A phantom submodule is committed **[verified]**

Because of §0.3, `.claude/worktrees/phase12-scale` is tracked as a **gitlink**
(mode `160000`) pointing at commit `a9327b8`, with no `.gitmodules` anywhere.
On a fresh clone this is a broken or empty submodule entry and can make
`git clone --recurse-submodules` fail. Remove it:

```sh
git rm --cached .claude/worktrees/phase12-scale
```

Do §0.3 first or it will come straight back.

### 0.5 Clean bill of health

No secrets, API keys, or personal absolute paths are committed. `git grep` for
credential patterns and for `C:\Users\...` over tracked files returns nothing.

---

## 1. Security — all of this must land before any public release

The parsing layer is genuinely well hardened against hostile **binaries**:
`parse.py:394` degrades to a raw model rather than failing, `_sanitise_mappings`
(`parse.py:78`) clamps every mapping to EOF and reports what it trimmed,
`sweep.py` caps at 1M instructions with a visited-set that makes infinite loops
structurally impossible, jump tables cap at 256 entries. Keep all of that.

What is missing is the second threat model: **the browser is hostile**. Every
critical finding below lives there.

> **Correction to `RELEASE.md` §4.** It states the localhost bind is "why that's
> been acceptable." That is not correct, and the reasoning matters — see S1.

### S1 — CRITICAL: unauthenticated arbitrary file read from any web page **[verified]**

There is no authentication on any route. A grep of `src/binviz` for `Depends`,
`HTTPBearer`, `api_key`, `Authorization` returns only the CORS line itself.
Combined with `service.py:172-174`:

```python
CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
```

`allow_origins=["*"]` is the server telling every browser "any website may read
my responses." With no auth there is no secret the attacker lacks, so the
same-origin policy is not bypassed — it is switched off by the server and
nothing replaces it.

Three reasonable endpoints then compose into an exfiltration primitive:

1. `GET /api/files?dir=…` enumerates any directory (`service.py:274`)
2. `POST /api/open {"path": …}` accepts any path, only checks `os.path.isfile` (`service.py:264`)
3. `GET /api/{id}/bytes?off=…&len=…` returns contents in 1 MiB pages (`service.py:541`)

Reproduced against the running server: listed the user's home directory and read
back the full contents of `~/.gitconfig`, with no credentials.

**Loopback binding does not mitigate this.** Loopback stops *network peers* —
another machine cannot reach you. It does nothing about JavaScript in a tab the
analyst already has open, which runs on the local machine and reaches
`127.0.0.1` normally. The attacker is a web page, not a host.

**Fix — four layers, in priority order.** Do not build user accounts; this is a
single-user local tool and the correct model is Jupyter's.

**S1a — startup token (the primary control).** Generate
`secrets.token_urlsafe(32)` at startup. Require it on every `/api` route via one
FastAPI dependency. Compare with `secrets.compare_digest`, never `==`, to avoid
a timing oracle. Print it once as a clickable URL. Provide `--no-auth` for CI
and tests that prints a loud warning, so insecure mode is opt-in and visible.

**S1b — `Host` allowlist.** `TrustedHostMiddleware(allowed_hosts=["127.0.0.1",
"localhost"])` in `create_app`. This is specifically the anti-DNS-rebinding
control: in a rebinding attack the attacker's domain re-resolves to `127.0.0.1`,
the browser then treats it as same-origin, and CORS stops applying entirely.
One line.

**S1c — real CORS.** Replace `allow_origins=["*"]` with the explicit dev origin
(`http://127.0.0.1:5173`), configurable by env. Keep `expose_headers=["X-Meta"]`
— the wire format depends on it. Do **not** set `allow_credentials=True`; with a
header token it is unnecessary and would re-open things if the origin list ever
regressed.

**S1d — path confinement (defence in depth).** Add `--root`, defaulting to cwd.
Require `os.path.realpath(path)` to sit under `os.path.realpath(root)` in both
`/api/open` and `/api/files`. Call `realpath` **first** — checking the raw string
lets a symlink walk straight out. This bounds the blast radius to the sample
directory even if auth is bypassed.

> Once the frontend is served from the same origin as the API (§4.1), S1c
> largely evaporates — there is no cross-origin request left to authorize. Do it
> anyway for the dev-proxy path.

**Acceptance test:** with the server running, from a page on a different origin,
`fetch('http://127.0.0.1:8000/api/files?dir=/')` must fail. A request with no
token must 401. A path outside `--root` must 403.

### S2 — CRITICAL: XSS from a malicious binary's metadata **[verified]**

Seven view files each define their own copy-pasted `esc()`. **Five omit quote
escaping**, and two of those are used inside HTML attributes:

| File | `&` | `<` | `"` |
|---|---|---|---|
| `views/info.ts:76` | yes | yes | **no** |
| `views/cfg.ts:597` | yes | yes | **no** |
| `views/overall.ts:400` | yes | yes | **no** |
| `views/hist2d.ts:348` | yes | yes | **no** |
| `views/image.ts:297` | yes | yes | **no** |
| `views/hexview.ts:245` | yes | yes | yes |
| `views/triage.ts:81` | yes | yes | yes |

The two live sinks:

- `views/info.ts:47` — `title="${esc(r.name)}"`, where `r.name` is the raw
  ELF/PE **section name**
- `views/cfg.ts:239` — `title="${esc(f.name)}"`, where `f.name` is the raw
  **symbol name**

Both reach the DOM through `innerHTML` (`info.ts:51`, `cfg.ts:251`).

Confirmed end-to-end: patched a real ELF's `.shstrtab` to rename a section to
`a"onmouseover=b` (same byte length, so every section-header offset stays
valid), parsed it, and the hostile name survives into the model unmodified.
`info.ts` then emits:

```html
<span class="rname" title="a"onmouseover=b">a"onmouseover=b</span>
```

The `title` attribute closes early and `onmouseover` becomes a live event
handler. **This tool's entire purpose is opening files supplied by attackers.**

**Fix:** one shared escaper handling `&`, `<`, `>`, `"`, `'`; delete all seven
copies; add a CSP meta tag (`default-src 'self'; script-src 'self'`). See §3.5
for the structural version of this fix.

**This is a release blocker for the desktop app specifically.** See §2.4.

### S3 — MEDIUM: malformed query params return 500s **[verified]**

Query params flow unvalidated from `service.py:485` into `surfaces/image.py:247`.
Verified live — each produced HTTP 500 with an unhandled server traceback:

```
/surface/image?mode=rgb8&width=0     -> 500   (ZeroDivisionError)
/surface/image?mode=rgb8&width=abc   -> 500   (ValueError)
/surface/image?mode=rgb8&width=-5    -> 500   (reshape error)
```

**Fix:** validate and clamp in `render`, raise 400 on bad input. Never let a
query string reach `int()` unguarded.

### S4 — MEDIUM: raster dimensions are unbounded **[verified]**

`SurfaceRequest.clamp` (`surfaces/base.py:31`) uses `max(1, …)` — a floor with
no ceiling. `GET /surface/linear?w=20000&h=20000` did not return within 40
seconds; it allocates a 400-million-cell raster from one GET. The dot-plot path
then persists such matrices to disk via `np.savez` (`service.py:148`), making
the same knob a disk-write amplifier.

**Fix:** clamp `w`/`h` to a maximum (4096 is generous) in `SurfaceRequest.clamp`.

### S5 — HIGH: uploads have no size cap

`service.py:237-251` streams to disk counting bytes but only ever checks
`n == 0` (`service.py:244`). **Fix:** abort with 413 past a configured maximum.

### S6 — MEDIUM: the cache grows without bound

`cache.py` has no eviction, no TTL, no size accounting. `wipe()` is only called
on a params-fingerprint mismatch. Every opened binary is retained forever.
**Fix:** LRU sweep against a configured byte budget on startup and after each
`analyze()`.

### S7 — MEDIUM: unbounded analysis concurrency

`_Jobs.ensure` (`service.py:80`) spawns an unbounded daemon thread per distinct
sha256. Dedup only covers the *same* hash. **Fix:** bounded
`ThreadPoolExecutor`, reject when the queue is full.

### Not a bug — do not "fix" these

- `overall.ts:257-258` tags are correct (`</b>`, `</span>`). An earlier audit
  pass flagged them as malformed; that was a false positive, verified by grep.
- `id` is strictly validated as 64 hex chars (`service.py:193`), which correctly
  prevents traversal into the cache directory. Leave it.
- Python runtime deps are fully `==`-pinned in `pyproject.toml`. Good for a
  lockfile — but see §4.2, it is wrong for a *published* package.

---

## 2. The auth layer and the optional login screen

### 2.1 The login screen is not the security boundary

`RELEASE.md` §4 already gets this right and it bears repeating in the strongest
terms: **a login screen that only gates the UI is cosmetic.** Anything on the
machine can talk to `/api/*` directly and skip the form entirely.

The security boundary is the **token check on every `/api` route** from S1a. The
login screen is a way to *obtain* that token, not a substitute for checking it.
Build S1a first; wire the login screen to it second. If the schedule slips, ship
S1a without the login screen — never the reverse.

### 2.2 Make it optional and off by default

The user's requirement is an **optional** login on the standalone build. Model
it as three modes:

| Mode | When | Behaviour |
|---|---|---|
| `none` (default) | dev, single-user desktop | Server mints a token, injects it into the served HTML. No login screen. Invisible to the user. |
| `local` | opt-in, shared machine | Login screen shown. Credentials verified locally, exchanged for the session token. |
| `--no-auth` | CI, tests only | No token required. Prints a loud warning banner on startup. |

Default `none` means the desktop app stays one-click for the ordinary case while
still being authenticated on the wire. That is the whole point: the token
protects the API whether or not a human ever sees a form.

### 2.3 If you implement `local` mode, do it properly

- **Never store a plaintext password, and never a bare hash.** Use a memory-hard
  KDF. `hashlib.scrypt` is in the standard library and adds no dependency —
  prefer it unless you already want `argon2-cffi` (argon2id is stronger; either
  is acceptable, a bare SHA-256 is not).
- Store the KDF output plus a per-install random salt under the cache root with
  restrictive permissions (`0o600`). Never in the repo, never in the wheel.
- On success, return a session token; the frontend sends it on every subsequent
  request. Do not keep re-sending the password.
- Rate-limit attempts and back off after repeated failures, so the local form is
  not a free offline oracle.
- First run in `local` mode must **set** a password rather than compare against
  a default. Never ship a default credential.
- The credential exists only to unlock this local install. It is not an account,
  there is no server to register with, and nothing should be transmitted
  anywhere.

### 2.4 Desktop packaging changes the severity of S2 — read before writing the bridge

`RELEASE.md` §2 plans a pywebview window. Two things a fresh session must not
get wrong:

**Wrapping the UI in a desktop window does not remove the network listener.**
The standard pattern starts uvicorn on `127.0.0.1:8000` and points the webview
at it. That is still an ordinary TCP listener that every browser and process on
the machine can reach, so S1 is completely unchanged — and the user is now *less*
likely to realise a server is running, because there is no terminal and no tab.
Binding an ephemeral port raises the bar but is obscurity, not a control: a
malicious page can scan localhost with timed `fetch`. **Keep the token in the
desktop build.** With same-origin serving it costs the user nothing.

**A `js_api` bridge turns S2 from XSS into remote code execution.** If you pass
`js_api=` to `webview.create_window`, any JavaScript in that window can call
`pywebview.api.<method>()`. Combine that with the section-name XSS in S2 and
opening a hostile sample executes attacker-controlled Python on the analyst's
machine. A malware triage tool where analysing malware runs the malware is the
worst failure mode available.

Therefore, before any bridge exists:

- Fix S2.
- Add the CSP.
- Keep the `js_api` surface minimal; expose nothing that takes a path or spawns
  a subprocess.
- Ensure `debug=True` is off in release builds — it enables devtools in the
  shipped app.
- Serve the UI over the same local HTTP origin as the API. Loading from `file://`
  and calling `http://127.0.0.1` is cross-origin with a `null` origin and drags
  CORS back in.

### 2.5 Wiring the reference design

`web/design/login.html` is self-contained (inline CSS, inline SVG mark, no
external assets) and exposes a single `onSubmit(creds)` stub. Integrate it as a
routed screen; that needs the backend catch-all described in `RELEASE.md` §2.

Preserve from the reference: the `.boot` class pattern gated on JS **and**
`prefers-reduced-motion`, real labels and tab order, Enter-submits, and errors
rendered in `--accent`. Palette and type rules are in `RELEASE.md` §3 — dark
theme only, all-monospace system stack, **no external fonts or assets ever**.

---

## 3. UI and UX changes

Three of these are structural habits rather than individual bugs. Since new
screens are planned, fix the habits **before** adding screens — at three views
this is cheap, at fifteen it is a migration.

### 3.1 File selector button — read the platform note, it is not obvious

Today the only way in is pasting an **absolute path** into a text box
(`index.html:13`) or drag-drop. There is no picker. This is the single worst
friction point in the app and the most common first-run dead end.

Add a "Choose file…" button next to the path input. **It must behave differently
in the two runtimes, and getting this wrong is the likely failure mode:**

- **Browser / dev mode.** `<input type="file">` deliberately does **not** expose
  the absolute path — you get a `File` object only. So the button must route
  through the existing **upload** path: read the `File` and `POST /api/open` as
  `application/octet-stream`. That endpoint already exists and already streams
  to disk while hashing (`service.py:237`), so no backend work is needed. Note
  the consequence: uploads have no source path, so directory navigation
  (`[` / `]`) is correctly disabled for them — `main.ts:208` already handles this.
- **Desktop / pywebview.** Use `webview.create_file_dialog(webview.OPEN_DIALOG)`,
  which returns **real absolute paths**. Send that to the path-based
  `POST /api/open {"path": …}`. This is strictly better: no copy, no upload, and
  file navigation keeps working.

Feature-detect rather than guessing — have the server inject a capability flag
into the served HTML, or check for `window.pywebview`. Fall back to the
`<input type="file">` path when absent.

If S1d (`--root`) is implemented, default the native dialog to that root, and
surface the 403 clearly when a user picks something outside it.

### 3.2 First-run state

The empty state currently says only "Open a binary to begin." Add a "try a
sample" affordance — the repo ships a corpus, so this costs almost nothing and
converts a dead end into a working first session. One line of what the tool
does would help too; nothing on screen currently says.

### 3.3 Eight silent failure paths — fix before adding screens

Every view swallows fetch errors into `console.warn`:

```
overall.ts:133   plot.ts:173      hexview.ts:156   hist2d.ts:150
hist2d.ts:187    hist3d.ts:320    image.ts:194     dotplot.ts:179
```

When any fail, the pane sits blank or stale with no explanation, and the user
cannot tell "this file has no such structure" from "the request failed." For a
triage tool that is a correctness problem, not just polish — the app can lie by
omission.

**Fix:** one shared `paneError(el, msg)` helper that every view calls. Do this
first and new screens inherit user-visible errors for free.

### 3.4 Navigation — required before more screens land

`theme.css:96-124` is a five-row CSS grid rendering **ten panes simultaneously**
(Overall, Zoomed, Signals, Info, Hex, Bigram, Trigram, Image, Dot plot, CFG),
scrolling vertically on smaller screens. There is no way to focus, collapse, or
tab. Adding a sixth and seventh grid row will make it materially worse.

Introduce tabs, routing, or collapsible sections **as part of** the new-screens
work. Retrofitting navigation onto fifteen always-visible panes later is a far
bigger job. Routing is needed for `/login` anyway (`RELEASE.md` §2), so do it
once.

### 3.5 One escaper, and stop hand-building HTML

The root cause of S2 is that seven copies of `esc()` exist and the unsafe path
is the convenient one. Export a single correct escaper from a shared module and
delete the copies. Better: build binary-derived strings with `textContent` or a
small element helper so `innerHTML` stops being the default. Add a CI grep that
fails on a bare `innerHTML` in `web/src/views/`.

### 3.6 Accessibility baseline

There are currently **zero** `aria-*` and **zero** `role=` attributes in the
frontend. The primary navigation flow — clicking a triage finding
(`views/triage.ts:64`) — is mouse-only `<div>`s: not focusable, not
keyboard-reachable, invisible to screen readers. Same for the region list
(`views/info.ts:53`).

Set the baseline in the new screens and backfill the old ones. Only two key
bindings exist app-wide (`[` / `]`, `main.ts:235`) and they are documented only
in a `title` tooltip; a help overlay listing shortcuts would help.

### 3.7 The 404-after-open race

`POST /api/open` returns success with an `id`, but `GET /api/{id}/status`
404s until the analysis thread writes `meta.json`. The frontend special-cases
this (`main.ts:110`); every other client must too. It bit two throwaway scripts
during review before they replicated the workaround.

**Fix:** return the initial status in the `open` response, or have `status`
return `202` while pending. A success response should not point at a resource
that does not exist yet.

### 3.8 Keep these — they are the good parts

- The `SelectionStore` linked-view model (`web/src/store.ts`). Everything in
  absolute file offsets, every view reads and writes it. This is the product.
- Triage findings as navigation (`views/triage.ts:64`) — click a finding, land
  on the bytes. Best workflow path in the app; make sure new screens join it.
- Progressive view enablement during analysis (`main.ts:121-136`).
- The lens persisting across files (`main.ts:225`), which supports the real
  "flip through a pile of blobs" workflow.
- Honest uncertainty: the `?` sentinel for unresolved jumps, the packed-binary
  banner, dot-plot progress. Do not smooth these over in a redesign.

---

## 4. Packaging and release

### 4.1 BLOCKER: the wheel would ship no UI **[verified]**

`pyproject.toml` has no `package-data` and no `MANIFEST.in`, and `web/dist` sits
**outside** `src/binviz/`. With `[tool.setuptools.packages.find] where = ["src"]`
the wheel gets the Python package and nothing else. `pip install binviz` would
install a backend with no frontend, and `binviz serve` would serve a JSON API to
a user with no way to look at it.

This makes the static-mount work in `RELEASE.md` §2 a **packaging
prerequisite**, not just an architecture preference. Build the frontend, copy
`dist` into `src/binviz/webui/`, declare it as package data, mount it after the
`/api/*` routes. Same-origin serving also deletes most of S1c and makes token
injection trivial.

### 4.2 Loosen the dependency pins before publishing

`RELEASE.md` §1 cites the pinned deps as release-ready. For a *published*
distribution they are a problem: `numpy==2.5.1`, `fastapi==0.141.1` and friends
will collide with almost anything else in a user's environment and produce
unsolvable resolutions. Publish compatible ranges (`numpy>=2.1,<3`), keep exact
pins in a dev lockfile. Keep `lief` tight — its API moves fast.

### 4.3 LICENSE file is missing **[verified]**

`pyproject.toml:10` declares `license = "MIT"` but there is no MIT text in the
repo. Metadata alone is legally ambiguous and GitHub's detection will not pick
it up, so the repo reads as "all rights reserved" to anyone evaluating it. Add
the MIT text with a copyright line.

### 4.4 Add `SECURITY.md`

A disclosure contact and a supported-versions note. This project will attract
security researchers by its nature; give them somewhere to go.

### 4.5 Use PyPI Trusted Publishing

OIDC rather than a long-lived API token in CI. For a tool in the security space
a stolen publish token is the worst available outcome, and it costs nothing.

### 4.6 Why open-sourcing raises the stakes on §1

Worth stating for whoever weighs the schedule:

- Published code makes the attack free to find. `allow_origins=["*"]` beside a
  `path`-accepting endpoint is greppable, and this exact pattern — local
  analysis server, wildcard CORS, file-read endpoint — is a known CVE class.
- Users will deploy it in ways nobody designed for: Docker with `-p`, SSH
  tunnels, devcontainers, WSL. VS Code and Codespaces auto-forward listening
  ports. Each turns a loopback assumption into a network-reachable service.
- The audience is malware analysts, whose machines hold samples, client data,
  and credentials. Most worth attacking, most likely to be targeted.
- `pip install binviz` reaches users who never read the source and do not know a
  server is now listening. The implicit "only developers run this" filter is
  gone.

---

## 5. Suggested order

1. **§0** — merge the branding branch, fix `.gitignore`, drop the phantom
   submodule, delete `icons temp/`. Everything else assumes one branch.
2. **§4.1** — static mount + package data. Unblocks the wheel *and* collapses
   the CORS problem.
3. **S2 + CSP** — two `esc()` functions. Small, and it must precede any
   pywebview bridge (§2.4).
4. **S1a–S1d** — token, Host allowlist, CORS, path confinement.
5. **§3.3 + §3.5** — shared error helper and shared escaper, *before* new
   screens.
6. **§3.1, §3.2** — file selector button and first-run state.
7. **§3.4, §3.6** — navigation and accessibility, as part of the new screens.
8. **S3, S4, S5, S6, S7** — parameter clamping and resource limits.
9. **§2.2, §2.3, §2.5** — optional login mode and screen integration.
10. **§4.2–4.5** — pins, LICENSE, SECURITY.md, Trusted Publishing.

## 6. Acceptance checklist

- [ ] Cross-origin `fetch` to `/api/*` fails; no-token request 401s
- [ ] Path outside `--root` 403s; symlink escape 403s (`realpath` first)
- [ ] `Host: evil.com` rejected
- [ ] ELF with section name `a"onmouseover=b` renders inert (regression test —
      build the fixture by patching `.shstrtab` in place, same byte length)
- [ ] `?width=0|abc|-5` return 400, not 500
- [ ] `?w=20000&h=20000` clamped, returns promptly
- [ ] Oversize upload 413s
- [ ] `pip install` from the built wheel serves a working UI, offline
- [ ] File picker works in both browser (upload) and desktop (native path) modes
- [ ] Every view surfaces fetch failures visibly
- [ ] Findings list and region list keyboard-reachable
- [ ] `git clone` of a fresh copy has no submodule error
- [ ] 316 backend tests, 37 frontend tests, `npm run build` clean

---

## Appendix — how to verify the current state yourself

```sh
# tests (all passing as of writing: 316 backend, 37 frontend, clean strict build)
.venv/Scripts/python -m pytest -q
cd web && npm test && npm run build

# run it
.venv/Scripts/python -m binviz.cli serve      # 127.0.0.1:8000
cd web && npm run dev                          # 127.0.0.1:5173

# the repo-state problems in §0
tail -c 24 .gitignore | xxd                    # UTF-16 null bytes
git check-ignore -v .claude/                   # NOT ignored
git ls-files -s .claude/worktrees/phase12-scale # mode 160000 gitlink
ls .gitmodules                                 # absent
```

Sample corpus: `make -C corpus` or `python corpus/build.py` → `corpus/out/`
(gitignored). Useful cases: `hello_upx` (likely_packed 0.81), `hello_O2`
(likely_benign 0.80), `sample.zip` (non_executable 0.90 — the false-positive
guard working), `switchy` (resolved jump tables), `hello_stripped`.
