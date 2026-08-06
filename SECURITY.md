# Security

binviz opens files chosen by an attacker. That is not an edge case in this
tool, it is the entire job — a malware triage tool where analysing malware
compromises the analyst is the worst failure mode available. This document
records the threat model, what is hardened today, and what is not yet.

## Reporting a vulnerability

Please report privately rather than opening a public issue. Use GitHub's
**Report a vulnerability** button under the repository's Security tab, which
opens a private advisory visible only to the maintainers.

You should get an acknowledgement within a few days. If a report is
confirmed, the fix and an advisory land together; you will be credited unless
you would rather not be.

Reports about the *parsers* are especially welcome: a crafted binary that
causes memory exhaustion, an unbounded loop, or a crash in the ELF/PE
handling is in scope even though it only affects the person who opened it.

### Supported versions

binviz is pre-1.0 and under active development. Only the latest commit on
`main` receives security fixes; there are no maintained release branches yet.

## Status: pre-release

binviz is pre-1.0. Authentication, file confinement and resource limits are
all in place, so a running server no longer trusts every local caller — but
treat it as a tool you run for yourself, not a service you stand up for
others. It has had one internal security review, not an external one.

Note in particular that binding to loopback is *not* a security control
against a hostile web page, and binviz does not rely on it as one. Loopback
stops other machines on the network. It does nothing about JavaScript running
in a browser tab on the same machine, which reaches `127.0.0.1` perfectly
normally. The attacker in this threat model is a web page, not a host — which
is why there is a token rather than an assumption.

## Threat model

Two distinct attackers, which need different defences:

**1. The binary is hostile.** Someone hands you a sample built to break the
tool that opens it: truncated headers, section tables pointing past EOF,
mappings that overlap or run backwards, control flow that never terminates.

**2. The browser is hostile.** The UI is a local web app, so any page in any
other tab is a potential caller of the API, and any string lifted out of the
sample — a section name, a symbol name — is potential markup on its way to
the DOM. This is the threat model that a "it only listens on localhost"
argument does not address.

## Hardened against a hostile binary

These predate the current hardening pass and are load-bearing; changes here
should be made carefully.

- **Parsing degrades rather than fails.** A binary that LIEF cannot make
  sense of falls back to a raw model instead of erroring out, so a malformed
  sample is still inspectable — which is usually exactly the sample you most
  want to look at.
- **Every mapping is clamped to EOF** on the way in, and what got trimmed is
  reported in the model's warnings rather than silently corrected. The tool
  says what it did.
- **Disassembly cannot loop forever.** The sweep caps at 1M instructions and
  carries a visited set, so a jump-to-self or a cycle terminates
  structurally, not by timeout. Jump-table recovery caps at 256 entries.
- **Cache paths cannot be traversed.** The `id` in every `/api/{id}/…` route
  is validated as exactly 64 hex characters before it is used to build a
  path, so no request can walk out of the cache directory.
- **Large files are streamed, not buffered.** Uploads hash to disk as they
  arrive and analysis works in bounded chunks, so a file larger than RAM is a
  slow operation rather than an out-of-memory crash. (Measured against a
  2 GiB sample; see `HANDOVER.md`.)

## Hardened against a hostile browser

### The API authenticates every caller

`binviz serve` mints a random token at startup (`secrets.token_urlsafe`) and
prints it once, as a URL you can click. Every `/api` route requires it. This
is the model Jupyter uses, and it is the right one here: binviz is a
single-user local tool, so the answer is a shared secret, not user accounts.

- The token may arrive as `Authorization: Bearer …`, as `X-Binviz-Token`, or
  as `?token=…`. The query form exists only so a freshly launched browser can
  bootstrap; the frontend immediately moves it into `sessionStorage` and
  strips it from the address bar, because a token left in a URL leaks through
  `Referer`, history, screenshots, and the server's own access log.
- Comparison uses `secrets.compare_digest`, never `==`. A short-circuiting
  comparison leaks the length of the matching prefix through timing, which is
  enough to reconstruct a token one character at a time.
- `--no-auth` exists for CI. It prints a banner saying what it just turned
  off. The test suite deliberately does **not** use it — the tests send a
  real token, so the auth layer is exercised by the suite meant to protect it.

### The server checks which name it was reached by

`TrustedHostMiddleware` accepts only `127.0.0.1` and `localhost`. This is
specifically the anti-DNS-rebinding control. In a rebinding attack the
attacker's own domain re-resolves to `127.0.0.1`; the browser then treats the
request as same-origin and **CORS stops applying entirely**, so the origin
list cannot save you. The `Host` header still says `evil.example`, and that is
what gets checked.

### CORS grants only the origin that needs it

The previous `allow_origins=["*"]` was the server telling every website on the
internet that it could read these responses. It is now an explicit list (the
dev frontend's origin, overridable with `BINVIZ_ORIGINS`). `X-Meta` stays in
`expose_headers` because the wire format depends on it. `allow_credentials` is
deliberately **off**: with a header token, cookies are never needed, and
enabling it would make any future regression in the origin list much worse.

### File access is confined to a chosen root

`binviz serve --root DIR` (default: the working directory) confines
`/api/open` and `/api/files`. The check calls `os.path.realpath` **first** and
compares afterwards — checking the string the caller sent and resolving later
is the classic hole, since a symlink or Windows junction inside the root
passes a textual prefix test and then reads whatever it actually points at.
Both are covered by tests.

This is defence in depth, not the primary control. It exists so that a token
compromise bounds the damage to the directory you were analysing rather than
the whole filesystem.

### Requests are bounded and validated

- **Raster dimensions have a ceiling.** `SurfaceRequest.clamp` used to apply
  a floor and no ceiling, so `?w=20000&h=20000` asked for a 400-million-cell
  raster from a single GET and did not return. Both dimensions now cap at
  4096, well past any real display.
- **Malformed parameters return 400, not 500.** Query values used to reach a
  bare `int()`, so `width=0`, `width=abc` and `width=-5` produced unhandled
  tracebacks. Surface parameters now go through a checked reader that raises
  a dedicated `SurfaceParamError`, which the HTTP layer maps to 400. The
  exception type is deliberately specific rather than catching `ValueError`:
  a genuine bug in the render path should stay a 500, because answering 400
  would blame the caller for the server's mistake.

- **Uploads have a size cap.** 8 GiB by default (`--max-upload`), enforced by
  a byte counter as the body streams to disk, so a client that understates
  `Content-Length` is still stopped. A rejected upload leaves no partial file
  behind.
- **Analysis concurrency is bounded.** Four simultaneous analyses by default
  (`--max-analyses`); beyond that `/api/open` returns 503 with `Retry-After`.
  Deduplication only ever covered repeated opens of the *same* file, so
  without this a caller could start an unbounded number of gigabyte-scale
  analyses by opening distinct ones.
- **The cache is bounded.** 5 GiB by default (`--max-cache`), swept at
  startup and after each analysis, evicting least-recently-used entries
  first. Two things are never evicted: an analysis in flight, and anything
  touched in the last five minutes. That makes the budget a target rather
  than a hard ceiling — the alternative is deleting artifacts out from under
  a window that is displaying them, which is a worse failure than briefly
  exceeding a disk quota. The cache is safe to delete by hand at any time.

### Binary metadata cannot become script

Section names, symbol names, and parse warnings quoting either are attacker
chosen and end up on screen. They are now escaped through exactly one
function — `web/src/escape.ts` — which handles `&`, `<`, `>`, `"` and `'`,
and so is safe in element text and inside a quoted attribute alike.

The failure this replaces is worth stating, because it is a pattern rather
than a typo: there were **seven** copies of the escaper, of which five
omitted quote escaping, and two of those five were used to build
`title="…"` attributes carrying raw section and symbol names. A name
containing a double quote closed the attribute early and turned the rest of
the name into live event-handler markup. One correct implementation is not
merely tidier than seven; it is the only arrangement in which "is this
escaped correctly?" has a single answer.

Supporting changes:

- A **Content Security Policy** ships in `web/index.html`. `script-src
  'self'` is the load-bearing directive: it blocks inline event handlers and
  injected `<script>` alike, so a hole in the escaper is not immediately a
  code-execution bug. `object-src 'none'` and `base-uri 'none'` close the
  usual side doors.
- The legend swatches set their colour through the CSSOM rather than a
  `style="…"` attribute, so the policy does not need `unsafe-inline` for
  styles on account of the app's own markup. It currently still allows it,
  because Vite's dev server injects stylesheets that way; the packaged build
  should send a stricter policy as a real HTTP header, which is also the only
  place `frame-ancestors` can be set.
- Two regression tests guard this. `web/test/escape.test.ts` fails the build
  if any module declares a local escaper again, or if the CSP loses
  `script-src 'self'`. `tests/test_security.py` builds a genuinely hostile
  fixture — a real ELF whose `.shstrtab` is patched in place, same byte
  length so every section-header offset stays valid, renaming a section to
  an XSS payload — and asserts the payload survives parsing intact. That last
  assertion is deliberate: the backend must report what is actually in the
  file, so the frontend escaper is the only thing between that name and the
  DOM, and it should be tested as such.

## The desktop window

`binviz app` runs the same server in a native window. Two things about that
are easy to get wrong, and both are handled deliberately:

**A window does not remove the network listener.** It is still an ordinary
TCP listener that any process or page on the machine can reach — and the user
is now *less* likely to realise it, because there is no terminal and no tab.
So the desktop build always authenticates: there is no `--no-auth` on
`binviz app`, and it prints the URL it is serving on. The ephemeral default
port is politeness about port clashes, not a control; a malicious page can
scan localhost with timed `fetch`.

**A `js_api` bridge would turn any surviving XSS into code execution.** Every
public attribute of the object passed to pywebview is callable from any
script running in that window, in a tool whose purpose is opening files an
attacker chose. binviz exposes exactly one method, `pick_file()`, which takes
no arguments, spawns nothing, and returns a path that goes back through the
same `--root` confinement as a typed one. `tests/test_app.py` fails if a
second method appears.

If you extend the bridge, that test failing is the design working. The
question to answer is not "is this useful" but "what does this let a hostile
section name do".

## Not yet done

Every finding from the review that produced this document is closed. What
remains is hardening that has not been reviewed rather than known holes; the
known limitations are listed in `RELEASE.md` §8.
