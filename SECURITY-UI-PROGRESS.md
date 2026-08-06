# Security / UI work order — progress

Companion to `SECURITY-UI-WORKORDER.md`, which is the spec. **This file is
the bookmark: how far through it we are.** Update it as each item lands.

`SECURITY.md` is the public-facing version — posture and disclosure, no
exploit recipes. Keep the detail here, not there.

Last updated: **2026-08-06**.

## Where we are

Working the work order's §5 suggested order, but skipping the two items that
are not mine to do (see "Blocked" below). Security items first.

| Item | What | State |
|---|---|---|
| §0.1 | Merge `worktree-branding-release-docs` into `main` | ✅ already done (verified) |
| §0.2 | Delete untracked `icons temp/` | ✅ already done (absent) |
| §0.3 | `.gitignore` written as UTF-16LE, matches nothing | ✅ already done (verified) |
| §0.4 | Phantom gitlink `.claude/worktrees/phase12-scale` | ✅ already done (untracked) |
| **§4.1** | **Static mount + package data (wheel ships no UI)** | ✅ **done** |
| **S2** | **XSS from binary metadata + CSP** | ✅ **done** |
| **S1a** | **Startup auth token on every `/api` route** | ✅ **done** |
| **S1b** | **`Host` allowlist (anti-DNS-rebinding)** | ✅ **done** |
| **S1c** | **Real CORS origin list** | ✅ **done** |
| **S1d** | **`--root` path confinement** | ✅ **done** |
| **S3** | **Malformed query params return 500** | ✅ **done** |
| **S4** | **Raster dimensions unbounded** | ✅ **done** |
| **S5** | **Uploads have no size cap** | ✅ **done** |
| **S6** | **Cache grows without bound** | ✅ **done** |
| **S7** | **Unbounded analysis concurrency** | ✅ **done** |
| **§3.3** | **Eight silent failure paths** | ✅ **done** |
| **§3.5** | **One escaper + stop hand-building HTML** | ✅ **done** |
| **§3.1** | **File selector button** | ✅ **done** |
| **§3.2** | **First-run state** | ✅ **done** |
| **§3.4** | **Navigation (ten panes render at once)** | ✅ **done** |
| **§3.6** | **Accessibility baseline** | ✅ **done** |
| **§3.7** | **404-after-open race** | ✅ **done** |
| **§2.2/§2.3/§2.5** | **Optional login mode + login screen** | ✅ **done** |
| **RELEASE §3** | **Striate palette, dark-only, all-monospace** | ✅ **done** |
| **§4.2** | **Loosen the `==` dependency pins** | ✅ **done** |
| **§4.3** | **LICENSE file is missing** | ✅ **done** |
| **§4.5** | **PyPI Trusted Publishing** | ✅ **done** |

Test baseline: **405 backend** (was 316), **87 frontend** (was 37),
`npm run build` clean, and both distributions build with the UI and the
licence in them.

**The work order is complete.** Every item in §0–§4 is closed except §2.4,
which is not a fix but a checklist to satisfy *when* the pywebview bridge is
written — see "Next up".

> **Building a wheel now has a required first step:**
> ```sh
> python tools/build_ui.py     # stages web/dist -> src/binviz/webui/
> pip wheel . --no-deps -w dist
> ```
> Skip it and the wheel silently ships no UI again — which is exactly the
> bug §4.1 was about. `src/binviz/webui/` is generated and gitignored.

> **Running the app changed twice.** The API needs a token and confines
> file access to `--root` (S1). And since §2.2, the *packaged* build no
> longer needs `?token=` at all — the server injects it into the page, so
> `binviz serve` then opening `http://127.0.0.1:8000/` just works.
>
> The Vite dev server is the exception, because it serves its own
> `index.html` and knows nothing about any of this: there, still
> `export BINVIZ_TOKEN=…` and pass `--token "$BINVIZ_TOKEN"` to
> `binviz serve` so the proxy can attach it. `HANDOVER.md` has the recipe.
>
> For a shared machine: `binviz passwd`, then `binviz serve --auth local`.

## §0 is already resolved — the work order is stale there

Re-checked on 2026-08-06 and **all four §0 items were already done**, so
nothing is blocked on the repo owner any more:

- `packaging/icons/` is on `main`, so the branding branch was merged (§0.1).
- `icons temp/` is gone (§0.2).
- `.gitignore` is clean UTF-8 and `git check-ignore -v .claude/` confirms
  `.claude/` **is** ignored — the UTF-16LE null bytes are not there (§0.3).
- `git ls-files -s .claude/worktrees/phase12-scale` returns nothing, so the
  phantom gitlink is untracked, and there is no `.gitmodules` (§0.4).

Verify with the commands in the work order's appendix if you doubt it. The
point is that a fresh session should not spend time re-fixing these.

## Done

### S2 — XSS from a malicious binary's metadata ✅

**Fixed 2026-08-06.** The work order put this first because it must precede
any pywebview `js_api` bridge (§2.4): with a bridge, this stops being XSS and
becomes remote code execution on the analyst's machine.

What changed:

- **New `web/src/escape.ts`** — the one escaper, handling `&`, `<`, `>`, `"`,
  `'`. `&` is replaced first so entities are not double-escaped.
- **All seven copies deleted**, call sites now import it. Because the export
  is named `esc`, no call site changed.
- **Two sinks the work order did not list**, found while sweeping:
  - `main.ts` had an *eighth* hand-rolled escaper for the recent-files
    datalist. Replaced.
  - `plot.ts:393` interpolated a raw `region.name` into the hover tooltip
    with **no escaping at all**, reaching the DOM through `tooltip.ts`'s
    `innerHTML`. `plot.ts` was never in the list of seven because it had no
    `esc()` to audit. This one lands in element content rather than an
    attribute, so it does not even need a quote to break out — arguably
    worse than the two documented sinks.
- **CSP meta tag** in `web/index.html`. `script-src 'self'` with no
  `unsafe-inline`/`unsafe-eval`; `object-src 'none'`; `base-uri 'none'`.
- **`overall.ts` legend** rebuilt with `createElement` + CSSOM instead of a
  `style="…"` attribute, so the app's own markup does not force
  `style-src 'unsafe-inline'`.

Verified clean by audit: `hexview.ts` was already escaping correctly (and now
inherits the stronger escaper); every other interpolation of a hostile field
goes to `textContent`, a canvas `fillText`, or a URL, none of which parse
HTML.

Tests added:

- `web/test/escape.test.ts` — the five characters, no double-escaping, the
  `a"onmouseover=b` payload against the exact `title="…"` shape `info.ts`
  emits. Plus two guards: **fails if any module declares a local escaper
  again**, and fails if the CSP loses `script-src 'self'`.
- `tests/test_security.py` — builds the hostile fixture the work order's
  acceptance checklist asks for: a real ELF with `.shstrtab` patched **in
  place, same byte length**, so every section-header offset stays valid and
  LIEF still parses it as a genuine ELF. Asserts the payload reaches the
  model unmodified.

Two notes for whoever touches this next:

1. **The backend deliberately does not sanitise.** It reports the bytes that
   are actually in the file; quietly rewriting a section name would be the
   tool lying about the sample, which it may never do. The test asserts the
   hostile name survives parsing *on purpose*. The frontend escaper is the
   defence, and it is the only one.
2. **The fixture needs a wide enough name slot.** A payload of N characters
   needs an existing section name of at least N characters, since it is
   written in place plus its NUL. In this corpus only the debug builds
   qualify (`.debug_pubnames`, exactly 15 — same width as the payload). The
   fixture tries `hello_O0`, `hello_static`, `hello_arm64`, `hello_O2` in
   order and skips if none fits, rather than pinning one sample. It also
   refuses to overwrite a name that another section's name offset points
   into, because ELF toolchains share string suffixes (`.rela.text` ends
   with `.text`) and clobbering the wrong entry corrupts a second section's
   name silently.

### S1a–S1d — unauthenticated arbitrary file read ✅

**Fixed 2026-08-06.** All four layers, in the work order's priority order.
Done as one unit because §5 lists them as one step and they share
`create_app`.

**S1a, the token** (`service.py`). `secrets.token_urlsafe(32)` minted in
`create_app`, checked by one middleware rather than a per-route dependency —
middleware cannot be forgotten when someone adds a route later, which is the
failure mode a dependency has. Accepts `Authorization: Bearer`,
`X-Binviz-Token`, or `?token=`; `secrets.compare_digest`, never `==`.
`--no-auth` prints a banner naming what it disabled.

**S1b, the Host allowlist.** `TrustedHostMiddleware` with `127.0.0.1` and
`localhost`. Starlette strips the port before matching, so `127.0.0.1:8000`
is fine.

**S1c, CORS.** Explicit origin list (`BINVIZ_ORIGINS` to override), methods
narrowed to GET/POST/OPTIONS, headers to the three we use. `expose_headers`
keeps `X-Meta` — the wire format depends on it. `allow_credentials` stays off
on purpose.

**S1d, confinement.** `--root`, defaulting to cwd in the CLI. `realpath`
first, then `commonpath` under `normcase` (Windows is case-insensitive and
raises `ValueError` across drives, which is treated as "outside").

Middleware **order is load-bearing** and is the thing to be careful about if
you touch this: the last one added is the outermost. Current stack, outermost
first, is TrustedHost → CORS → no-store → token. CORS must sit outside the
token check or a 401 arrives without the headers a browser needs to read it,
and the failure looks like a network error instead of an auth error.

**The test suite now authenticates.** `conftest.make_app` / `authed_client`
replaced eight direct `create_app` + `TestClient` pairs. Two things that bite:
`TestClient`'s default Host is `testserver`, which TrustedHost correctly
rejects, so `authed_client` sets `base_url="http://127.0.0.1"`; and
`make_app` passes `file_root=None`, because the tests open both corpus files
and pytest temp dirs, which on Windows are not even on the same volume.
Confinement is covered separately with a purpose-built root.

> Deliberate call, flagged because it is the tempting shortcut: the tests
> send a **real token** rather than using `--no-auth`. A flag that is
> convenient for tests is exactly how insecure defaults escape into
> production, and using it here would mean the suite guarding the auth layer
> never exercises it.

**Frontend.** New `web/src/auth.ts` takes the token from `?token=` once,
moves it to `sessionStorage`, and strips it from the address bar via
`history.replaceState` — a token in a URL leaks through `Referer`, history,
and access logs. `api.ts` attaches it to every request and gives 401 a
human-readable message instead of "Unauthorized". `vite.config.ts` injects
`BINVIZ_TOKEN` into proxied requests so dev works without the browser ever
seeing it.

Verified live against a running server, not just in tests — the work order's
acceptance list, reproduced: no token → 401; wrong token → 401;
`Host: evil.com` → 400; foreign `Origin` → no CORS grant; and the original
finding's own attack (list the home directory, read `~/.gitconfig`) → 403 at
the confinement layer.

Two small things found while doing this, both fixed: the startup banner used
`print()` without `flush`, so the token did not appear at all when stdout was
piped — the one message that must never be swallowed; and it contained an
em-dash, which renders as a replacement glyph on a cp1252 Windows console
because `cli.py` sets `errors="replace"`.

### S3 + S4 — parameter validation and raster clamping ✅

**Fixed 2026-08-06.**

**S4** is two lines: `SurfaceRequest.clamp` gained a `max_dim` ceiling
(`MAX_RASTER_DIM = 4096`) alongside its existing floor. Verified live —
`?w=20000&h=20000` went from *not returning within 40 s* to **707 ms**,
clamped to 4096×4096.

**S3** turned out to be broader than the report. The work order found it in
the image path, but the same two patterns — an unguarded `int()` and an
unknown-mode `ValueError` — ran through **every** surface. Rather than patch
three call sites, `surfaces/base.py` now exports `int_param` / `choice_param`
and a dedicated `SurfaceParamError`, and every surface plus the dot-plot
parameter block in `service.py` goes through them.

`SurfaceParamError` subclasses `ValueError`, so existing `except ValueError`
handlers (notably `/image/stride`) keep working unchanged. The service
catches **only** the subclass: a genuine bug in the render path stays a 500,
because answering 400 would blame the caller for our mistake.

Additional cases found and fixed while doing this, none of them in the
report: `signal=nonsense` (KeyError out of `compute_signals`), `bayerXX_1`
and `bayer_RGGB_RGB_zzz` (unguarded `int()` in the bayer mode parser), and
`width=1e999` — the service coerces that to float `inf` before it is seen,
and `int(inf)` raises **OverflowError**, not ValueError, so the first version
of the guard missed it.

> **The trap in this one, worth reading before touching the surface
> endpoint.** Raising `HTTPException` from inside
> `with MappedFile.open(path) as mf:` broke 14 tests with
> `BufferError: cannot close exported pointers exist`. The exception's
> traceback keeps the `render` frame alive, that frame still holds numpy
> views of the mmap, and `close()` then fails on Windows — burying the real
> error behind an unrelated one. This is `HANDOVER.md` gotcha 7 arriving by a
> new route: previously it was about local variables, here it is the
> traceback. The fix is to catch inside the `with`, keep only the *message*,
> and raise after the block, so the traceback is dropped before the file is
> unmapped. `test_a_param_error_does_not_wedge_the_mmap` pins it.

One deliberate non-change: `linear?mode=byteclass&reduce=nonsense` still
returns 200. `reduce` is genuinely unused in byteclass mode, and rejecting it
would break a UI that sets the control once and switches modes. My first test
asserted 400 here and was wrong; it is now pinned as 200 with a comment, so
tightening it later is a decision rather than a side effect.

### S5 + S7 — upload cap and bounded analysis concurrency ✅

**Fixed 2026-08-06.**

**S5.** The stream loop already counted bytes and only ever compared them to
zero, so this is a comparison rather than a rewrite. Two layers: a cheap
`Content-Length` rejection (a courtesy — it is the client's claim) and the
authoritative byte counter in the loop, which stops reading the moment the
cap is passed. Default 8 GiB, `--max-upload` / `BINVIZ_MAX_UPLOAD` to change
it. Generous on purpose: P12 measured analysis against a 2 GiB file and
uploads stream to disk, so the cap exists to stop a runaway filling the disk,
not to define "a big file". A test asserts a rejected upload leaves no
`.upload` temp file behind — otherwise the cap bounds one request but not the
disk.

**S7.** `_Jobs` now refuses to start more than `max_concurrent` (default 4)
analyses and returns **503 with `Retry-After`**, not 500. `--max-analyses`
to change it. The bound is on *live* work — finished threads are reaped, so
it is a concurrency limit and not a high-water mark.

> **Deviation from the work order, deliberate.** S7 says "bounded
> `ThreadPoolExecutor`". I kept counted daemon threads instead. Since Python
> 3.9 the executor's workers are **non-daemon** and are joined by an atexit
> hook, so Ctrl+C on `binviz serve` would block until the running analysis
> finished — up to ~90 s on a 2 GiB file, per the P12 numbers. The bound is
> what S7 actually asks for; the executor was the suggested mechanism, and
> this one delivers the bound without regressing shutdown. Change it if you
> disagree, but measure the Ctrl+C path first.

Verified live as well as in tests: a 1 MiB upload against a 4096-byte cap
returns 413, a small one still returns 200, and no temp files are left.

Also fixed: `--no-auth`'s help text contained an em-dash, which printed as a
replacement glyph in `--help` on this cp1252 console — the same trap as the
startup banner in S1.

### S6 — unbounded cache growth ✅

**Fixed 2026-08-06.** The two open questions were settled by the repo owner:
**5 GiB default budget** (laptop-sized; `--max-cache` / `BINVIZ_MAX_CACHE`
to raise it, documented in the README) and **LRU that protects active
entries**.

`cache.py` gained `entry_size`, `cache_usage`, `sweep` and `BinaryCache.touch`.
The sweep runs at startup (a cache that grew while the server was down is
exactly the case for it) and after each analysis, evicting least-recently-used
first.

Recency comes from the entry directory's mtime, bumped by `touch()` in
`get_cache` — i.e. on every API request that names a binary. A directory mtime
rather than a `last_access` field in meta.json: this runs on every request,
and rewriting meta.json that often is both wasteful and a good way to hit the
read-during-replace race below.

Two things are never evicted: an analysis in flight (`_Jobs.active()`), and
anything touched within `PROTECT_RECENT_SECONDS` (300). **That makes the
budget a target, not a hard ceiling** — `sweep` returns `over_budget: True`
when it could not reach it. That is the intended trade and it is tested: the
alternative is deleting artifacts out from under a window displaying them.

`sweep` only ever deletes sha256-shaped directories, so stray `.upload` temp
files and anything else in the cache root are left alone — tested, because a
sweep that decides the cache root is entirely its own property is a very bad
bug to write.

Checked against the real cache on this machine: 17 entries, 534 MB, i.e.
0.50 GiB against a 5 GiB budget — comfortably under, so nothing evicts today.

> **A pre-existing bug this surfaced.** The new tests hammer surface
> endpoints right after opening a file, and started failing intermittently
> with **409** — a different test each run. Cause: `require()` read meta.json
> while `analyze()` was rewriting it via `os.replace`, got `None`, and
> reported a ready artifact as not ready. This is `HANDOVER.md` gotcha 2, and
> `source_path()` already retried for exactly this reason — `require()` never
> did. Both now share `_meta_or_retry`. This was **not** caused by the S6
> work; the fixture just made it reproducible. A real user would have seen an
> occasional spurious 409, self-healing only because the frontend retries.

Also switched the startup hook from `@app.on_event` to the lifespan API — the
former is deprecated and adding it took the suite from 1 warning to 105.

### §4.1 — the wheel now ships a UI ✅

**Fixed 2026-08-06.** This was the release blocker: `pip install binviz`
produced a backend with no frontend, because `web/dist` sits outside
`src/binviz/` where setuptools cannot see it.

- **`tools/build_ui.py`** runs the Vite build and stages `web/dist` into
  `src/binviz/webui/`, dropping `.map` files (~600 KB of dev-only weight;
  `--with-sourcemaps` keeps them). `--skip-build` stages an existing dist.
- **`pyproject.toml`** declares `binviz = ["webui/*", "webui/assets/*"]` as
  package data.
- **`service.py`** mounts the bundle **after** every `/api` route, so the API
  always wins, with an SPA fallback to `index.html` for unknown paths (which
  `/login` will need on a hard refresh, per `RELEASE.md` §2).
- `src/binviz/webui/` is generated output and is **gitignored**.

Three decisions in the mount worth keeping:

1. **The UI is not behind the token.** You have to load the page before you
   can supply a token, and nothing in the bundle is secret — it is the same
   static output every install ships. The API behind it is still gated; a
   test asserts exactly that pair (`/` is 200 anonymously, `/api/files` is
   401).
2. **An unmatched `/api/*` path stays a JSON 404** rather than falling
   through to `index.html`. Serving HTML there would hand a JSON client a
   page of markup and turn a clear 404 into a confusing parse error
   somewhere else entirely.
3. **The catch-all takes a raw path from the URL**, so it gets the same
   realpath-first confinement as `/api/open`. Four traversal shapes are
   tested (`..`, `%2e%2e`, `..%2f`, `....//`); all fall back to index.html
   and none leak.

The packaged build also now sends a **real CSP header**, which is stricter
than the meta tag it intersects with: no websocket in `connect-src` (there is
no Vite here) and `frame-ancestors 'none'`, which a meta tag silently
ignores. Plus `X-Content-Type-Options: nosniff`.

**Verified the acceptance criterion properly** — built a wheel, installed it
into a clean venv, and ran it from there: the wheel contains all six UI files,
`/` serves the real page, every referenced asset resolves, and a full
open → analyse → `state: complete` (all six artifacts ready) → surface render
round-trip works. Offline holds too: the bundle references `eclipse.org` and
`w3.org` only as XML namespace identifiers, and contains **zero** occurrences
of `fetch(`, `XMLHttpRequest`, `importScripts`, `WebSocket` or `sendBeacon`.

### §3.3 — eight silent failure paths ✅

**Fixed 2026-08-06.** Every view swallowed fetch errors into `console.warn`,
so a failed pane sat blank or, worse, kept showing the *previous* file's
data. New `web/src/panestatus.ts` exports `paneError` / `clearPaneError` /
`errorText`; all eight sites report, and each success path clears.

The 409/410 "analysis still settling" retry paths were left alone on purpose
— that is an expected transient, not a failure, and shouting about it would
train people to ignore the banner.

Styling is deliberate: the message is **overlaid at the bottom of the pane
rather than replacing the content**, so whatever was drawn before stays
visible behind it. "This is stale and here is why" beats a blank box.

> **The bit that only showed up by running it.** After wiring all eight, I
> killed the server mid-session and clicked: five panes recovered on the next
> interaction and **Overall did not** — it is file-bound, so it only refetches
> on a resize or a mode change, and nothing the user was likely to do would
> ever retry it. A permanent error banner with no way out is its own dead
> end. So `paneError` now takes an optional `retry` callback and every site
> passes one; the affordance is a real `<button>` (focusable, keyboard
> reachable — the mistake §3.6 catalogues elsewhere), not a clickable `<div>`.
> A test asserts no `paneError` call is missing its retry.

**Verified in a real browser**, not just in tests: served the packaged UI,
opened `hello_O2`, killed the server, dragged a selection → **seven** panes
(Zoomed, Signals, Hex, Bigram, Trigram, Image, Dot plot) each showed its
message plus a retry button. Restarted the server, clicked all seven retries
→ every pane recovered and repopulated for the selection. That round trip
also incidentally confirmed S1a end to end: the page was opened with
`?token=…` and the address bar afterwards read only `?path=…`, i.e. `auth.ts`
captured the token and stripped it.

Tests are the DOM-free kind this project uses (no jsdom — the dependency list
is deliberately short). What is pinned: `errorText` behaviour, that **no view
contains `console.warn/error/log`** at all, that every fetching view imports
and uses both `paneError` and `clearPaneError`, that no `paneError` call
lacks a retry, and that the module never touches `innerHTML` (the message
carries a server-supplied detail, which can quote a hostile file's own
strings — same reasoning as `escape.ts`).

### §3.5 — stop hand-building HTML ✅

**Fixed 2026-08-06.** S2 gave every view one correct escaper; this makes the
*safe* path the default, so a forgotten `esc()` stops being expressible.

New `web/src/dom.ts`, deliberately tiny — not a framework:

- `el()` / `replace()` / `span()` build nodes. Text goes through
  `textContent`, so an interpolated value cannot be markup no matter what
  the binary contained.
- ``html`…` `` is a tagged template that escapes every `${…}` and returns
  `SafeHtml`. `setHtml` and `showTooltip` accept **only** `SafeHtml`, so a
  plain string will not type-check where markup is expected.

Migrated: the three list builders that carry attacker-controlled strings
(`info.ts` regions, `triage.ts` findings, `cfg.ts` function list) to nodes;
all seven tooltips and the hex viewer to ``html`…` ``; the legend, signal
picks and recent-files datalist to nodes.

**`innerHTML` now appears exactly once in the codebase** — inside `setHtml` —
and a test enforces that. `esc` likewise has exactly one caller, the `html`
tag, which is also tested: a second entity table is precisely how S2
happened.

Three things worth keeping:

1. **`SafeHtml` is a wrapper object, not a branded string.** The brand would
   be compile-time only, and `html` has to decide *at runtime* whether a
   value is an already-escaped fragment (splice verbatim) or ordinary text
   (escape). A branded string is still a string at runtime, so nested
   fragments would silently double-escape. There is a test for that.
2. **The hex viewer deliberately keeps markup.** A screenful is ~50 rows ×
   34 spans rebuilt every scroll frame; parsing one string beats ~1,700
   `createElement` calls. It uses ``html`…` ``, so it keeps the fast path
   without keeping the unsafe one.
3. **No parameter properties in `src/`.** `constructor(readonly value: string)`
   compiles fine under Vite but `node --test` runs in strip-only mode and
   rejects it outright — the app built cleanly and only the tests caught it.
   Write the field out.

I also dropped an over-clever guard that tried to regex for "template literal
containing a tag": it flagged `querySelectorAll<HTMLElement>` and every other
generic. A guard with false positives gets deleted by whoever hits it next,
so the tests pin the two facts that are actually true and checkable
(`innerHTML` in one place, `esc` with one caller) and leave the rest to the
compiler.

**Verified in a real browser**, since this touched every rendering path:
region list, triage findings, CFG function list, hex rows and gutter,
tooltips, legend chips all render; clicking a triage finding still drives the
selection (Zoomed followed to `0x22fd–0x2b00`); zero console output.

A nice accident of the corpus: `hello_O2`'s first region is named
**`<header>`** — a name that *is* an HTML tag. It renders as visible text in
both the node-built region list and the ``html``-tagged hex gutter, which is
the escaping proving itself on real data rather than on a synthetic payload.

### §3.1 + §3.2 — file picker and first-run state ✅

**Fixed 2026-08-06.** The work order called the missing picker "the single
worst friction point in the app"; until now the only way in was typing an
absolute path.

**§3.1.** A "Choose file…" button in the toolbar, behaving differently in the
two runtimes exactly as the platform note requires:

- **Browser:** `<input type="file">` never reveals an absolute path, so the
  bytes go up through the existing upload endpoint. The consequence is real
  and correct — an upload has no source path, so `[` / `]` directory
  navigation stays disabled.
- **Desktop:** a `window.pywebview.api.pick_file()` bridge returns a real
  absolute path and uses the path endpoint, so navigation keeps working.

Detection is `window.pywebview`, not a build flag, so one bundle serves both.
**This only detects a bridge; it does not create one** — adding a `js_api`
bridge is still gated on §2.4, since a bridge turns any surviving XSS into
code execution.

**§3.2.** The empty state now says what the tool is, offers the picker, and
lists real files from the served root as one-click samples — so it works for
a corpus checkout and a `pip install` alike instead of hardcoding paths that
exist on one machine.

> **New endpoint, and the bug that forced it.** The sample list first used
> `GET /api/files?dir=.` — which **403s**, because "." resolves against the
> *server process* cwd and that need not be `--root`. With
> `--root corpus/out` the two differ and the list silently vanished. So
> there is now `GET /api/config` returning `{root, max_upload, tool_version}`;
> the UI asks where the root is rather than guessing. It also gives the
> desktop dialog somewhere sensible to open, which §3.1 asks for. Behind the
> token like everything else, since it names a filesystem path.

Also fixed while testing: after picking a file in the browser, the label read
**`file.bin`** — the cache's own internal name — rather than what the user
chose. `source.path` for a stored upload is the cache copy, so the picked
name is now carried separately and shown.

**Verified in a real browser**, all three routes:

- sample button → opens by path, nav shows `6/21`
- browser upload (a real `File` handed to the input) → analyses to ready,
  label shows `my-chosen-name.bin`, nav correctly **disabled**
- desktop route (bridge stubbed, since there is no real one yet) → takes the
  **path** route, nav shows `4/21` — i.e. the browser/desktop difference
  genuinely works rather than just compiling
- a path outside `--root` → `"path is outside the served root"`, flagged as
  an error rather than failing silently

### §3.4 — navigation ✅

**Fixed 2026-08-06.** `theme.css` was a five-row grid rendering **ten panes
at once**, with no way to focus, collapse or tab. The work order is right
that a sixth and seventh row makes it materially worse, so this had to land
before the new screens rather than after.

**Routing, built once.** New `web/src/router.ts`: path-based (`/bytes`,
`/patterns`, …), not hash-based, because both servers already fall through
to `index.html` for unknown paths — §4.1 added that for the packaged mount
and Vite does it by default — so a deep link survives a hard refresh. That
fallback is the *precondition*; without it a hash router would be the right
answer. `/login` (§2.5) slots in as a non-workspace route with no second
mechanism. Verified by hard-refreshing `/patterns?path=…`.

**The URL is the state**, deliberately, with no localStorage copy: the
address bar is already shareable, survives reload, and makes Back mean
something. A stored preference does none of that and would silently
disagree with the URL. A test pins that.

**Workspaces** (`web/src/workspace.ts`) group panes by *the question the
analyst is asking*, not by implementation kinship:

| Route | Tab | Panes |
|---|---|---|
| `/` | Overview | Overall, Zoomed, Signals, Binary |
| `/bytes` | Bytes | Overall, Zoomed, Hex, Binary |
| `/patterns` | Patterns | Bigram, Trigram, Image, Dot plot, Zoomed |
| `/code` | Code | CFG, Binary |
| `/all` | All | all ten — the original grid, kept for large displays |

Zoomed and Binary recur because they are *context*, not content: the
selection readout and the triage findings are what make the other panes
interpretable. One DOM element, a different `grid-area` per workspace.

Keyboard and ARIA are right from birth rather than backfilled (§3.6 says to
set the baseline in the new navigation): a real `role="tablist"` with roving
tabindex, arrows/Home/End, `1`–`5` to switch from anywhere, and a per-pane
maximise button with Escape to exit. Escape is checked **before** the
"are they typing?" guard on purpose — a maximised CFG pane contains a search
box, and leaving the only way out unreachable from the field you are typing
in is a trap.

> **Focus mode is deliberately not routed.** Which pane is maximised is a
> "look closer for a second" gesture; putting it in the URL would make Back
> mean two different things depending on what you last did.

**Hiding is `display: none`, and that is load-bearing.** Every view already
owns a ResizeObserver and already guards its draw path against a zero-sized
host, so a hidden pane stops drawing and a re-shown one repaints itself with
no new plumbing. Hiding by opacity or offscreen positioning would keep all
ten live and lose the point. Two consequences to know about:

1. `.pane` sets `display: flex`, which **beats** the user-agent's
   `[hidden] { display: none }` on specificity. Without the explicit
   `.pane[hidden]` rule, hiding does nothing at all and every workspace
   silently shows ten panes again — §3.4's own bug, reintroduced quietly.
   There is a test for that rule.
2. `RasterCanvas` used to *ignore* a zero size; it now records it, so the
   surface views' existing `cssW === 0` guards stop them fetching a picture
   nobody is looking at. The canvas bitmaps are left untouched (resizing one
   clears it), so coming back is instant.

**Two things that genuinely burn resources while hidden are gated; the rest
are not, and that is a deliberate line.** The dot plot skips a restart at
zero size — a restart runs a sampling pass server-side and the refine loop
keeps asking for more — and the trigram view's rAF loop stops doing GL work
and stops advancing the spin. Bigram, Image and Trigram still *fetch* on a
selection change while hidden: that is one bounded request each, exactly
what they cost today when all ten panes are always visible, so it is not a
regression. Gating them properly needs a refetch-on-show for each, and
getting that wrong means a pane that quietly shows the previous selection's
data — the §3.3 failure mode. Worth doing, worth doing carefully.

Measured in the browser rather than assumed: with a selection dragged on the
Overview workspace, `surface/dotplot` requests were **zero** and the two
visible surfaces fetched normally.

> **A pre-existing bug this surfaced.** `CfgView.fit()` read
> `host.clientWidth` at layout-completion time. That was always latent
> (`HANDOVER.md` gotcha 3) and became reachable the moment a pane could be
> hidden: land on `/patterns`, let the CFG layout finish, switch to Code,
> and the graph was a one-pixel sliver at the left edge — fitted to a 0×0
> viewport, and nothing ever recomputes the fit, so it stayed broken
> forever. `fit()` now defers when the host has no size and the resize
> observer completes it. Found by looking at the screen, not at the code.

Also fixed while testing: `.pane-head` clipped and shredded its controls in
the narrower workspace columns — the dot plot's title broke across two lines
and its status label became a one-word-per-line column that ate the canvas.
Heads now wrap to a second row instead. Costs a row of height in the narrow
case and nothing in the wide one.

Tests are the project's DOM-free kind. `router.test.ts` pins path
normalisation and, more usefully, what a stale bookmark does (falls back
rather than failing) and that matching is exact so a future `/login` is not
swallowed by `/`. `workspace.test.ts` pins the agreement between three
things nothing in the type system connects — the table, the markup and the
CSS grids — including **every pane being reachable from some workspace**,
which is the real regression: add a pane, forget to route it, and it is
invisible forever with no error.

**Verified in a real browser**, packaged build: all five tabs render;
`/patterns?path=…` survives a hard refresh; back/forward move between
workspaces with panes and tab state following; `2` switches from the
keyboard; arrows/Home/End walk the tablist with wrap-around and focus
surviving the route change; maximise fills the grid and refetches Overall at
the new size; Escape exits; the triage-finding → selection path still works
(`0x22fd–0x2b00`, the overlay); zero console output throughout.

### §3.6 — accessibility baseline ✅

**Fixed 2026-08-06.** The app had **zero** `aria-*` and **zero** `role=`
attributes, and the thing the work order calls the best workflow path in the
whole tool — click a triage finding, land on the bytes — was a mouse-only
`<div>`. It now has 30 `aria-*` and 11 `role=`, but the count is not the
point; three specific things were unreachable and now are not.

**The three lists** (triage findings, regions, CFG functions) are ARIA
listboxes via one shared `web/src/listnav.ts`, for the reason `escape.ts`
exists: three hand-rolled keyboard handlers would drift and two would end up
subtly wrong.

Two design calls in there worth not undoing:

1. **Listbox, not a list of `<button>`s.** Buttons would be less code, but a
   binary with 200 functions would then put 200 stops in the tab order and
   make Tab useless for reaching anything past the CFG pane. A roving
   tabindex makes each list one stop, with arrows inside it. The stop sits
   on the *selected* row, so Tab lands where the user's attention already is
   rather than at the top of a long list.
2. **Focus does not select.** In a plain listbox, arrows conventionally move
   the selection. Here "selecting" drives the SelectionStore, so arrowing
   through twenty findings would fire twenty rounds of refetch across every
   linked view. Arrows move focus; Enter/Space commits.

`setOptionSelected` sets the `active` class and `aria-selected` together,
because they are the same fact rendered two ways — and a test fails if any
view goes back to `classList.toggle("active", …)` directly, which is exactly
how a list ends up looking right and announcing wrong.

> **Found by using it, not by reading it.** The CFG list rebuilds its rows
> when a function is opened — which happens *because* the user pressed Enter
> on a row. The row they were on is destroyed mid-keystroke, so focus fell
> back to `document.body` and every Enter cost them their place. `renderList`
> now captures whether focus was inside the list and `focusTabStop` puts it
> back. Verified: Enter on `compare_ints` leaves focus on `compare_ints`.

**Shortcut help.** The work order's specific complaint was that the only key
bindings were documented in a `title` tooltip — which a keyboard user never
hovers and a screen reader is not obliged to read. `web/src/help.ts` is a
native `<dialog>` opened with `?` or the toolbar button; `showModal()` gives
the focus trap, the inert background and Escape-to-close from the platform,
and each of those is something a hand-rolled overlay gets wrong. There are
more bindings to list now than the two the work order counted: §3.4 added
`1`–`5` and Escape, and the lists added arrows/Home/End/Enter.

Escape is now overloaded (close the dialog, leave a maximised pane), so
`main.ts` checks `isHelpOpen()` first and steps aside — otherwise one press
would do both.

**Names, not tooltips.** Every icon-only control (`‹`, `›`, `✕`, `◐`, `?`)
gained an `aria-label`; `title` is a mouse affordance. The path input and the
CFG filter had only placeholders — which vanish the moment there is text, so
they are hints, not names. The status chip is `role="status"`
`aria-live="polite"`, so analysis progress on a large file is not information
only sighted users get.

Focus rings are explicit on all three row types with a negative
`outline-offset`, so the ring sits inside the row instead of overlapping its
neighbours in a dense list — an invisible focus ring in a 200-row function
list means arrowing blind.

**Still mouse-only, deliberately:** clicking the hex dump to place the caret
(`hexview.ts`) and clicking the image view to pick an offset. Those are dense
canvas/virtualised surfaces, and giving them keyboard equivalents is a caret
model — a feature, not a baseline fix. The work order's acceptance item is
"findings list and region list keyboard-reachable", and that is met.

**Verified in a real browser** with real keypresses, not just synthetic
events: 41 region options with exactly one tab stop; click `.dynsym`, ↓↓,
Enter → selection `0x332–0x334` with `aria-selected` on the activated row;
Enter on a triage finding → `0x22fd–0x2b00`, the overlay; the CFG list
arrow/Enter round trip above; the help dialog opening from the button and
from `?`, toggling off, closing on Escape, and **not** hijacking `?` typed
into the path field. Zero console output.

### §3.7 — the 404-after-open race ✅

**Fixed 2026-08-06.** `POST /api/open` returned 200 and an id that pointed
at nothing until the analysis thread got round to writing its first
`meta.json`, so the very next `GET /api/{id}/status` 404'd for a few hundred
milliseconds. Every client had to know that: the frontend special-cased it,
and the work order records two throwaway review scripts learning it the hard
way.

`get_cache` gained `pending_ok`, used **only** by `/status`: an id with a
live job but nothing on disk yet resolves instead of 404ing. Everything else
genuinely needs the artifacts and still says 404 — the test asserts
`/api/{sha}/model` during the same window to pin that the pass does not leak.

Two decisions in the shape of the fix:

1. **The synthesised document speaks the same vocabulary as a real one** —
   `state: "running"`, every artifact `"pending"`, which is exactly what
   `analyze()` writes a moment later. My first attempt used
   `state: "analyzing"` (the word `open`'s *own* response uses) and an empty
   artifacts map; the test caught it. Inventing a fourth state would have
   moved the special case from the status code into the body rather than
   removing it.
2. **200, not 202.** The work order offers 202 as an option. RFC 9110 puts
   202 on the response that *accepts* the work — that is `POST /api/open` —
   and using it on the poll would split "how far along is this" across the
   status code and the body when `state` and `artifacts` already say it
   precisely. It would also have made progressive polling awkward: a client
   would have to accept 202 to see partial readiness at all. A 404 still
   means what it should.

> Note for whoever touches `open`: its response says `state: "analyzing"`
> while meta.json says `"running"` for the same condition. Two vocabularies
> for one fact. Pre-existing and left alone as out of scope, but it is a
> wart, and it is what my first version of this fix tripped over.

**The frontend workaround is gone**, which is the actual point — `main.ts`
no longer has a 404 branch in `poll()`, so a 404 there now means what it
says. Four test polling loops that *tolerated* the 404 (`if
resp.status_code == 200:`, one with a comment explaining the race) became
assertions, turning the workaround into four free regression tests.

The new test holds the window open with a blocked `analyze` rather than
racing it — the real window is a few hundred ms, so a polling test would
pass on a slow machine and prove nothing on a fast one. Confirmed it fails
without the fix before keeping it.

**Verified against a genuinely cold cache** (a fresh `--cache` dir, so the
race is real rather than short-circuited by a warm entry): opening a 3.6 MiB
`hello_O0` produced **6 status polls, all 200**, through to `ready`. Zero
console output.

### §2.2 / §2.3 / §2.5 — optional login mode ✅

**Fixed 2026-08-06.** The thing to hold on to, because it is the whole
point of §2.1: **the login screen is not the security boundary.** The
boundary is the token check on every `/api` route (S1a). Signing in is a
way to *obtain* that token, not a substitute for checking it — anything on
the machine can skip the form and talk to the API directly. Two tests exist
purely to keep that honest: with `local` mode on and nobody signed in,
`/api/open`, `/api/config` and `/api/{id}/status` all still 401; and having
signed in does not make later requests authenticated by itself.

**Three modes, exactly as §2.2 lays them out.**

| Mode | Flag | Behaviour |
|---|---|---|
| `none` | default | The server injects the token into the HTML it serves. No login screen, no `?token=` URL to copy. |
| `local` | `--auth local` | Login screen; a credential is exchanged for the token. |
| `off` | `--no-auth` | No token. CI only, and the banner says so. |

`none` is what makes the desktop app one click while still authenticating
every call. Injecting a token into a page looks alarming written down, so
the reasoning is in the code: the page is same-origin with the API and CORS
names an explicit origin list, so a hostile page in another tab can *issue*
the request but cannot read the response — and any local process that could
fetch it could equally read the cache directory. This is Jupyter's model.

**The bootstrap is a `<meta>`, not an inline script.** The CSP is
`script-src 'self'` with no `unsafe-inline` (S2), and weakening that to
pass one string across would trade the XSS defence for a convenience. The
server rewrites `<meta name="binviz-boot" content="">` per request with the
mode, the version, and — in `none` mode only — the token. If the
placeholder is missing it raises 500 rather than serving a page that
silently cannot authenticate: a stale staged bundle should look broken, not
mysterious. (It did exactly that once during this work, which is how I know
the check earns its place.)

**Credentials (§2.3).** `hashlib.scrypt`, standard library, no new
dependency: n=2^15, r=8, ~80 ms a guess. A per-install random salt, so one
rainbow table cannot cover every install of the tool. Written to
`<cache>/auth.json` created at mode 0600 — created, not chmodded
afterwards, because between the two there is a window where the digest is
world-readable. Tests assert the password does not appear in the file and
that two installs of the *same* password produce different digests.

Wrong username and wrong password return byte-identical responses, tested,
because differing messages turn the form into a username oracle. Failures
back off exponentially after three attempts (429 with `Retry-After`) — not
a defence against a determined attacker, which is scrypt's job, but enough
that the form is not a free fast oracle.

**Setting the credential**, per the decision taken: `binviz passwd` prompts
with `getpass` and writes it (never an argument — a password on the command
line lands in the shell history and the process list, which is exactly the
person this mode defends against). If none exists when `--auth local`
starts, the first sign-in claims the install, and the startup banner says
so in four lines of `!!` because that window is the one thing this mode is
supposed to close. There is no default credential anywhere.

> **A real bug found by measuring rather than reading.** The handler is
> `async def`, and `hashlib.scrypt` is CPU-bound, so the KDF was running on
> the event loop — 80 ms during which the server answers nothing at all,
> including a running analysis's status polling. Now offloaded with
> `run_in_threadpool`, with a test that fails if it goes back. The KDF being
> slow must cost the attacker, not the rest of the server.

**The screen (§2.5)** is ported from `web/design/login.html`, which stays in
the repo as the reference. Everything RELEASE.md §4 asks to preserve is
preserved: the boot sequence where the Hilbert mark draws itself the way
Striate walks a file (cream cap at offset 0, stroke traces, sage cap lands
at EOF, wordmark rises, splash wipes to the card), with the offset readout
ticking on the same easing curve as the stroke so the number and the trace
land together; the section-entropy strip capping the card, which shifts to
`--accent` on a failed sign-in; real labels, real tab order, Enter submits,
errors in `--accent` on a reserved line so the card never jumps.

Two things kept deliberately:

1. **`.boot` is set only when JS runs *and* `prefers-reduced-motion` is not
   set.** Every animated rule is scoped to it, so without it the screen
   renders in its final state rather than a broken half-state. Any click or
   keypress also cuts the splash short — this is a screen you see every day
   on a shared machine, and an animation you cannot skip becomes a tax.
2. **The credential is not left in the DOM.** On success the screen is
   emptied, not just hidden.

The strip's bar heights are a fixed pseudo-profile and say so in a comment:
this screen has no binary open, and inventing data that *looked* measured
would be the tool lying decoratively.

**Verified in a real browser**, both modes, against the packaged build:

- `none`: opened `http://127.0.0.1:8020/` with **no `?token=` at all** and
  the empty state listed six corpus samples — i.e. `/api/config` and
  `/api/files` both authenticated off the injected token.
- `local`, unclaimed install: the card came up in "Set password" mode with
  the claiming note; a 5-character password surfaced the server's own
  "Password must be at least 8 characters." verbatim; a valid one set the
  credential, stored the token, emptied the screen and loaded the app.
- `local`, returning: reloaded with `sessionStorage` cleared → "Sign in"
  mode, claiming note gone; a wrong password showed the neutral message,
  turned the entropy strip and the field border accent, and left the app
  gated; typing again cleared the error; the right one signed in.
- Both banners checked, with and without a credential set.

Zero console output throughout.

### RELEASE.md §3 — the Striate palette ✅

**Done 2026-08-06**, on the decision to follow RELEASE.md rather than hedge:
**dark only**. The light theme, the `◐` toggle, the `binviz-theme`
localStorage key, the `store.setTheme` event and every `Record<Theme, …>`
colour table are gone. One palette cannot drift out of step with itself.

Tokens are RELEASE.md §3 verbatim (`--bg` `--panel` `--field` `--cream`
`--sage` `--accent` `--ink` `--hair`), with the sheet's existing names
mapped onto them rather than renamed across 400 lines. Type is the
all-monospace system stack — no external fonts, ever, because the tool must
work offline and a font request is a network request.

> **One collision worth knowing about.** RELEASE.md defines `--ink` as
> *text on accent surfaces* (#2B2424). The stylesheet already used
> `var(--ink)` to mean *primary text*. Sixteen uses were remapped to
> `--cream`, and `--ink` now means what the document says. If you see
> dark-on-dark text somewhere I missed, that is the cause.

**The chart colours were computed, not chosen.** The byte-class and series
palettes are painted into canvases, so they are not CSS tokens and they had
to be re-stepped against the new surface (`--panel` #453B3B, considerably
lighter than the old #1a1a19). Every value was generated at a target OKLCH
lightness and hue and then run through the dataviz validator, which measures
the lightness band, the chroma floor, OKLab ΔE under simulated protanopia
and deuteranopia, the normal-vision floor, and WCAG contrast.

Three findings from that, all of which contradict what eyeballing suggests:

1. **Equal lightness made CVD *worse*.** My first series palette was six
   hues at one lightness; it failed at ΔE 2.8 because under deuteranopia
   the hue difference is most of what collapses and lightness is what is
   left. Alternating lightness across the slots took it to 9.1.
2. **The byte-class raster needs `--pairs all`, not adjacent-only.** Any
   class can end up touching any other in a raster, so there is no such
   thing as a non-adjacent pair there. Worst all-pairs is 9.3 under
   protanopia, above the target of 8.
3. **Red-on-green is the classic protan collision and it bit.** Control
   bytes moved from red-orange to amber, which took that pair from ΔE 1.2
   to 9.3.

Contrast WARNs remain on three series slots and two byte classes, kept
deliberately and for different reasons: the plot titles every lane and
names every series in the legend, so identity never rests on colour there;
and a filled raster tiles the whole canvas, so those marks are read against
each *other* rather than against a background you can see — which is what
the CVD checks measure, and they pass.

Re-run `scripts/validate_palette.js` before changing any of them. "It looks
fine to me" is exactly the judgement colour-vision deficiency defeats.

Status colours (`--status-critical` / `-warning` / `-good`) are reserved for
state and never reused as a series colour, and each ships with its own
words — the verdict is spelled out, findings carry a code and a description
— so severity is never carried by hue alone. Critical is the accent, which
RELEASE.md assigns to every error signal.

### §4.2 — dependency pins loosened to ranges ✅

**Done 2026-08-06.** `pyproject.toml` pinned every runtime dependency with
`==`. That is right for a lockfile and wrong for a published distribution:
`numpy==2.5.1` beside anything else that wants numpy produces an unsolvable
resolution, and the user cannot fix it from their side.

| Was | Now | Why |
|---|---|---|
| `lief==1.0.0` | `>=1.0,<1.1` | The tight one, on purpose |
| `capstone==5.0.9` | `>=5.0.1,<6` | 5.0.0 is yanked — see below |
| `numpy==2.5.1` | `>=2.1,<3` | |
| `fastapi==0.141.1` | `>=0.115,<1` | 0.x: minor bumps can break |
| `uvicorn==0.52.1` | `>=0.30,<1` | |
| `pillow==12.3.0` | `>=10.1,<14` | Tiny API surface, ubiquitous package |

Upper bounds sit at the next major. binviz is an *application*, not a
library — nothing depends on *us*, so a cap here strands nobody, which is
the usual argument against caps and does not apply.

**lief stays tight** because the work order says so and the reason holds up:
it is the component whose failure mode is a *plausible but wrong parse*
rather than an exception, and nothing else in a normal environment depends
on lief, so a narrow range costs almost no resolution pain. The cost is that
lief 1.1 will require a binviz release even if it is compatible. Re-run the
parser tests before widening it.

**`constraints-dev.txt`** is the other half of the trade: the exact versions
the suite is green against, including transitives.

```sh
pip install -e ".[dev]" -c constraints-dev.txt
```

A constraints file rather than a requirements file, so it pins versions
without *adding* dependencies and stays correct if the extras change. The
README's quickstart now uses it.

> **Two things found by actually resolving the ranges rather than writing
> them down.**
>
> 1. **`capstone==5.0.0` is yanked on PyPI** ("Reason for being yanked:
>    wrong"). A floor of `>=5.0` resolves straight onto it — pip warns and
>    installs it anyway. Floor raised to `5.0.1`.
> 2. **A floor is a promise, and it was an untested one.** Testing only the
>    pinned set proves the ceiling. So the release workflow now has an
>    `oldest supported dependencies` job that installs at the minimum of
>    every range on Python 3.11 (`requires-python`'s own floor) and runs the
>    suite. If it fails, raise the floor — do not delete the job. Publishing
>    is gated on it, because shipping metadata that claims compatibility we
>    know is broken is the §4.2 problem again, just moved from pins to a lie
>    about ranges.
>
> The floor set was checked locally with a pip dry-run for Python 3.12 and
> resolves consistently: lief 1.0.0, capstone 5.0.1, numpy 2.1.0,
> fastapi 0.115.0 with starlette 0.38.6, uvicorn 0.30.0, Pillow 10.1.0.

**A version bug this surfaced.** `pyproject.toml` said `0.0.1`,
`__init__.py` said `0.0.1`, and `cache.TOOL_VERSION` said `0.0.3` — and it
is the last of those the tool actually reports, in `/api/config`, in
`meta.json`, and in the login screen footer. So `pip install binviz` would
have shipped metadata claiming a version the tool disagreed with.

Fixed by making `binviz.__version__` the one place the distribution version
is written, read by `pyproject.toml` through
`[tool.setuptools.dynamic]`, and set to `0.0.3` to match what the tool has
been reporting all along.

> **`TOOL_VERSION` is deliberately *not* unified with it.** It feeds
> `params_fingerprint`, so changing it invalidates every cached analysis on
> every install — a release that only touches CSS would silently throw away
> a 5 GiB cache and re-analyse everything. They are two different facts that
> happen to read the same today. Both now say so in a comment.

### §4.3 — LICENSE ✅

**Done 2026-08-06.** `pyproject.toml` declared `license = "MIT"` with no MIT
text anywhere, which is legally ambiguous and means GitHub's detection shows
nothing — so the repo read as "all rights reserved" to anyone evaluating it.

`LICENSE` added with the MIT text and a copyright line (2026 Karan
Kantaria), plus `license-files = ["LICENSE"]` so it travels in the
distributions rather than only in the repo. `build-system.requires` bumped to
`setuptools>=77`, which is what PEP 639's `license`/`license-files` need.

Verified in the built artifacts, not assumed: the wheel carries
`binviz-0.0.3.dist-info/licenses/LICENSE` and the metadata reads
`License-Expression: MIT` + `License-File: LICENSE`; the sdist carries the
file too. The release workflow asserts the licence is present as part of its
wheel check, so a future packaging change cannot quietly drop it.

Also added `[project.urls]` — Homepage, Source, Issues, and Security
pointing at `SECURITY.md` — so the PyPI page has a disclosure route on it
rather than only in the repo.

### §4.5 — PyPI Trusted Publishing ✅

**Done 2026-08-06.** `.github/workflows/publish.yml`, publishing via OIDC
with **no API token anywhere in the repository or its secrets**. PyPI
verifies a short-lived identity minted by GitHub for this workflow in this
repository; there is nothing long-lived to steal, leak in a log, or forget
to rotate. For a tool in the security space that matters more than usual —
§4.6's point is that the audience is malware analysts, so a stolen publish
token would hand an attacker's code to exactly the population that opens
hostile files for a living.

Three jobs: `build` (stage the UI, run the suite, build both distributions,
verify the wheel), `floors` (§4.2's minimum-version run), and `publish`,
which needs both and only fires on a published release.

**The wheel check is the §4.1 trap, closed properly.** `tools/build_ui.py`
must run before `python -m build` or the wheel ships a backend with no
frontend — and it fails *silently*, which is what made §4.1 a release
blocker in the first place. The workflow now asserts the built wheel
contains `binviz/webui/index.html`, a non-empty `webui/assets/`, and the
licence, and fails the release if not. A documented step relies on someone
remembering; this does not.

**One-time setup on PyPI before this can run** — the workflow's header
repeats it, but for the record:

```
PyPI -> project -> Publishing -> Add a new pending publisher
  Owner: karankantaria   Repository: Striate
  Workflow: publish.yml  Environment: pypi
```

The environment name is half of the identity PyPI checks, and it is also
where a required reviewer can be attached so a publish cannot happen without
a human approving it.

Verified locally as far as it can be without a release: both distributions
build clean, the wheel contains the UI and the licence, and a **clean venv
install of the built wheel serves a working authenticated app** — `/` 200
with the token injected into the page, `/api/config` answering with that
token, and the CSS asset resolving. The OIDC exchange itself can only be
verified by publishing.

### §4.4 — `SECURITY.md` ✅

Written as a real public-facing document, since the repo has a public remote
(`github.com/karankantaria/Striate`). It states the disclosure route, the
threat model, and what is hardened — and warns plainly that the port must not
be exposed while S1 is outstanding, **without** publishing reproduction
steps for the open findings.

> ⚠️ Related decision for the repo owner: `SECURITY-UI-WORKORDER.md` itself
> contains verified reproduction detail for S1 and S2 and is committed to a
> repo with a public remote. Worth deciding deliberately whether it should be
> public before release, or kept local until the findings are closed.

## Next up

**§0, §1 and §4.1 are all done.** Every numbered security finding is closed
and tested, and most were re-verified against a running server rather than
only in the suite.

**All three structural items are done**, so new screens can now be added
without inheriting the old habits: they get visible errors from
`panestatus.ts`, safe rendering from `dom.ts`, and a routed home in a
workspace from `workspace.ts` for free. Adding a screen is now: build the
view, give it a `grid-area`, add it to a workspace's `panes`, add its slot
to that workspace's `grid-template-areas`. The tests will tell you if you
forgot one of those.

**The work order is done.** §0 through §4 are all closed. What is left is
not a fix list any more — it is the two things the work order deliberately
scoped *out*, plus a release decision that is the repo owner's:

- **Publish.** The pipeline is built and verified as far as it can be
  without publishing: `binviz passwd` / `binviz serve` both work from a
  clean-venv wheel install, and the release workflow gates on the suite,
  the floors, and the wheel actually containing the UI. What it needs is the
  one-time PyPI pending-publisher setup (in the workflow header) and a
  GitHub release. **Nothing is committed or pushed** — that is yours.
- **`SECURITY-UI-WORKORDER.md` is still committed to a repo with a public
  remote and still contains verified reproduction detail for S1 and S2.**
  Every finding it describes is now fixed, so the argument for keeping it
  private is much weaker than it was — but it is a deliberate call, not an
  oversight, and it is yours to make.

**§2.4 is now the only thing between here and the desktop app.** Its
preconditions are all satisfied — S2 is fixed, the CSP is in place, and the
UI is served same-origin — so what is left of that section is the bridge
itself: keep the `js_api` surface minimal (nothing that takes a path or
spawns a subprocess), and make sure `debug=True` is off in release builds.
`main.ts` already *detects* `window.pywebview.api.pick_file` for the native
file dialog (§3.1); it does not create it.

Things learned by testing that are easy to trip over again:

- The image view clamps `width` to ≥1 **client-side**, so a bad width never
  reaches the server. UI validation and the server-side 400s (S3) are
  therefore independent — testing one does not cover the other.
- Anything that needs to know where the server can read from must ask
  `GET /api/config`. Do not reach for `"."`.
- **A pane can now be hidden, so "the host has a size" is no longer a safe
  assumption anywhere.** Views that read `clientWidth` at *draw* time were
  all fine; the one that read it once at load time (`CfgView.fit`) was
  silently broken. If you add a view, read the size when you use it.
- **A list that rebuilds when you activate it destroys the row you are
  standing on.** Harmless with a mouse, and it costs a keyboard user their
  place on every Enter. Capture `contains(document.activeElement)` before
  the rebuild.

Now genuinely unblocked by §4.1: **§2.2's `none` auth mode** — with
same-origin serving, the server can inject the token into the served HTML and
the desktop app becomes one-click while staying authenticated on the wire.
That is a small change now and the natural companion to the login screen,
and §3.4's router is where `/login` hangs.

Still true: **do not add a pywebview `js_api` bridge** until §2.4's checklist
is satisfied. S2 and the CSP were the preconditions and both are done, but
the rest of that section (minimal bridge surface, `debug=False` in release,
same-origin serving — that last one is now satisfied) still applies.

One thing left for the owner: `README.md` says "Phase 3 complete" while
`HANDOVER.md` says Phase 12. Project status is your call, but it is the first
thing a visitor to a public repo reads.
