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
| §0.1 | Merge `worktree-branding-release-docs` into `main` | ⛔ blocked — needs a human commit |
| §0.2 | Delete untracked `icons temp/` | ⛔ blocked — depends on §0.1 |
| §0.3 | `.gitignore` written as UTF-16LE, matches nothing | ⬜ not started |
| §0.4 | Phantom gitlink `.claude/worktrees/phase12-scale` | ⛔ blocked — needs `git rm --cached` |
| §4.1 | Static mount + package data (wheel ships no UI) | ⬜ not started |
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
| §3.x | UI work (error helper, picker, nav, a11y) | ⬜ not started |
| §2.x | Optional login mode | ⬜ not started |
| §4.2–4.5 | Pins, LICENSE, Trusted Publishing | ⬜ not started (SECURITY.md ✅, that was §4.4) |

Test baseline after S6: **371 backend** (was 316), **45 frontend** (was 37),
`npm run build` clean. **All of §1 (S1–S7) is now closed.**

> **Running the app changed.** The API now needs a token and confines file
> access to `--root`. `HANDOVER.md` "How to run" has the two-process recipe;
> the short version is `export BINVIZ_TOKEN=…` and pass `--token "$BINVIZ_TOKEN"`
> to `binviz serve`, because the Vite proxy reads the same variable.

## Blocked on the repo owner

The user commits manually and does not want git history touched, so §0.1,
§0.2 and §0.4 need them. They are worth doing early — the work order is right
that everything else assumes one branch. The exact commands:

```sh
git merge origin/worktree-branding-release-docs   # §0.1
git rm --cached .claude/worktrees/phase12-scale   # §0.4 — do §0.3 first
rm -rf "icons temp"                               # §0.2, after §0.1
```

§0.3 (`.gitignore` encoding) is a file edit, not a git operation, so it is
not blocked — it is just not done yet. Do it *before* §0.4 or the phantom
gitlink comes straight back.

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

**§1 is done.** Every numbered security finding (S1–S7) is closed, tested,
and — for S1 through S5 — verified against a running server rather than only
in tests.

The work order's §5 order puts **§4.1** next: static mount + package data.
It is a genuine release blocker (`pip install binviz` currently gets a
backend with no frontend at all) and it simplifies S1c, since a frontend
served from the API's own origin makes the cross-origin case disappear. It is
also the prerequisite for injecting the token into the served HTML, which is
what `RELEASE.md` §2's `none` auth mode needs.

Then the UI work in §3, where §3.3 (one shared `paneError` helper) and §3.5
(stop hand-building HTML) should land **before** new screens, not after.

Still blocked on the repo owner: the three §0 git items at the top of this
file.

Two things worth deciding before a release:

- `SECURITY-UI-WORKORDER.md` contains verified reproduction detail and sits
  in a repo with a public remote. All the findings are fixed now, so this is
  much less pressing than it was, but it is still a deliberate choice rather
  than an accident.
- `README.md` still says "Phase 3 complete" while `HANDOVER.md` says Phase 12.
  Left alone because project status is the owner's call, but it is the first
  thing a visitor reads.

After the security items, the work order's order puts **§4.1** (static mount
+ package data) next — it is a genuine release blocker, since the wheel
currently ships no UI at all. It also simplifies S1c: once the frontend is
served from the same origin as the API, there is no cross-origin request left
to authorise.

Still true and still worth heeding: **do not add a pywebview `js_api` bridge**
until §2.4's checklist is satisfied. S2 is fixed, which was the precondition,
but the rest of that section (minimal bridge surface, `debug=False` in
release, same-origin serving) is not.
