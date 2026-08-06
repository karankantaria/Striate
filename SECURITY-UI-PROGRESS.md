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
| §3.x | Remaining UI work (picker, first-run, nav, a11y) | ⬜ not started |
| §2.x | Optional login mode | ⬜ not started |
| §4.2–4.5 | Pins, LICENSE, Trusted Publishing | ⬜ not started (SECURITY.md ✅, that was §4.4) |

Test baseline after §3.5: **383 backend** (was 316), **62 frontend** (was 37),
`npm run build` clean. **All of §1 (S1–S7), §4.1, §3.3 and §3.5 are closed** —
i.e. every security finding and both structural UI items.

> **Building a wheel now has a required first step:**
> ```sh
> python tools/build_ui.py     # stages web/dist -> src/binviz/webui/
> pip wheel . --no-deps -w dist
> ```
> Skip it and the wheel silently ships no UI again — which is exactly the
> bug §4.1 was about. `src/binviz/webui/` is generated and gitignored.

> **Running the app changed.** The API now needs a token and confines file
> access to `--root`. `HANDOVER.md` "How to run" has the two-process recipe;
> the short version is `export BINVIZ_TOKEN=…` and pass `--token "$BINVIZ_TOKEN"`
> to `binviz serve`, because the Vite proxy reads the same variable.

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

**Both structural items are done**, so new screens can now be added without
inheriting the old habits: they get visible errors from `panestatus.ts` and
safe rendering from `dom.ts` for free.

What is left of the work order, in its own order:

- **§3.1 — file picker.** The highest-value remaining item; today the only
  way in is pasting an absolute path. **Read the platform note carefully:**
  browser and desktop must behave *differently* (`<input type="file">`
  deliberately hides the real path, so the browser must route through the
  upload endpoint, while pywebview's dialog returns real paths and should
  use the path endpoint). Getting that wrong is the likely failure mode.
- **§3.2** first-run state, **§3.4** navigation (ten panes render at once
  today), **§3.6** accessibility — the last two as part of the new screens.
- **§2.2/§2.5** optional login mode. `§4.1` unblocked the `none` mode: with
  same-origin serving the server can inject the token into the served HTML,
  making the desktop app one-click while staying authenticated on the wire.
- **§4.2/§4.3/§4.5** loosen the `==` dependency pins, add the MIT LICENSE
  text (`pyproject.toml` declares MIT but no file exists), Trusted Publishing.

Worth knowing before §3.1: the image view already clamps `width` to ≥1
client-side, which is why a bad width never reaches the server. That is fine,
but it means UI-level validation and the server-side 400s (S3) are
independent — do not assume testing one covers the other. I found it by
trying to trigger a pane error with `width=0` and getting a perfectly
successful render.

Now genuinely unblocked by §4.1: **§2.2's `none` auth mode** — with
same-origin serving, the server can inject the token into the served HTML and
the desktop app becomes one-click while staying authenticated on the wire.
That is a small change now and the natural companion to the login screen.

Still true: **do not add a pywebview `js_api` bridge** until §2.4's checklist
is satisfied. S2 and the CSP were the preconditions and both are done, but
the rest of that section (minimal bridge surface, `debug=False` in release,
same-origin serving — that last one is now satisfied) still applies.

One thing left for the owner: `README.md` says "Phase 3 complete" while
`HANDOVER.md` says Phase 12. Project status is your call, but it is the first
thing a visitor to a public repo reads.

After the security items, the work order's order puts **§4.1** (static mount
+ package data) next — it is a genuine release blocker, since the wheel
currently ships no UI at all. It also simplifies S1c: once the frontend is
served from the same origin as the API, there is no cross-origin request left
to authorise.

Still true and still worth heeding: **do not add a pywebview `js_api` bridge**
until §2.4's checklist is satisfied. S2 is fixed, which was the precondition,
but the rest of that section (minimal bridge surface, `debug=False` in
release, same-origin serving) is not.
