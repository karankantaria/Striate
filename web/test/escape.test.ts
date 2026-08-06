import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { esc } from "../src/escape.ts";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");

/* The payload from the S2 report: a real ELF was patched in place so a
   section name became `a"onmouseover=b`, chosen to be the same byte length
   as the original so every section-header offset stayed valid. Escaped, the
   quote must not be able to close a title="…" attribute. */
const HOSTILE_SECTION_NAME = 'a"onmouseover=b';

test("escapes the five characters that matter", () => {
  assert.equal(esc("&"), "&amp;");
  assert.equal(esc("<"), "&lt;");
  assert.equal(esc(">"), "&gt;");
  assert.equal(esc('"'), "&quot;");
  assert.equal(esc("'"), "&#39;");
});

test("ampersand is escaped first, so entities are not double-escaped", () => {
  assert.equal(esc("<"), "&lt;");
  assert.equal(esc("&lt;"), "&amp;lt;");
  assert.equal(esc("a & b < c"), "a &amp; b &lt; c");
});

test("hostile section name cannot break out of a quoted attribute", () => {
  const out = esc(HOSTILE_SECTION_NAME);
  assert.ok(!out.includes('"'), "a bare quote would close the attribute");
  assert.equal(out, "a&quot;onmouseover=b");
  // the shape the info panel actually emits
  const html = `<span class="rname" title="${out}">${out}</span>`;
  // class="rname" and title="…" — four quotes, all the author's own
  assert.equal(html.match(/"/g)!.length, 4, "no quote came from the name");
});

test("single quotes cannot break out of a single-quoted attribute", () => {
  assert.equal(esc("a'onmouseover=b"), "a&#39;onmouseover=b");
});

test("script tags survive only as text", () => {
  assert.equal(
    esc("<script>alert(1)</script>"),
    "&lt;script&gt;alert(1)&lt;/script&gt;",
  );
  assert.equal(
    esc('<img src=x onerror="alert(1)">'),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
  );
});

test("leaves ordinary section and symbol names untouched", () => {
  for (const name of [".text", ".rodata", "__libc_start_main", "_ZNSt8ios_base"]) {
    assert.equal(esc(name), name);
  }
});

/* S2's root cause was not a bad escaper — it was seven of them, five wrong.
   A local copy is how that comes back, so fail the build on one rather than
   waiting for the next audit to count them again. */
test("no module defines its own escaper", () => {
  const offenders: string[] = [];
  const walk = (dir: string): void => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name);
      if (e.isDirectory()) { walk(p); continue; }
      if (!e.name.endsWith(".ts") || p.endsWith(join("src", "escape.ts"))) continue;
      const src = readFileSync(p, "utf8");
      if (/(?:function|const)\s+esc\w*\s*[(=]/.test(src)) offenders.push(p);
      if (/replace\(\/&\/g/.test(src)) offenders.push(p + " (hand-rolled)");
    }
  };
  walk(SRC);
  assert.deepEqual(offenders, [], "import esc from src/escape.ts instead");
});

test("index.html carries a script-src 'self' CSP", () => {
  const html = readFileSync(join(SRC, "..", "index.html"), "utf8");
  const meta = /http-equiv="Content-Security-Policy"\s+content="([^"]*)"/.exec(html);
  assert.ok(meta, "CSP meta tag is missing");
  const policy = meta[1].replace(/\s+/g, " ");
  assert.match(policy, /script-src 'self'/, "inline script must stay blocked");
  assert.ok(!/script-src[^;]*unsafe-inline/.test(policy),
    "unsafe-inline in script-src would undo the CSP");
  assert.ok(!/script-src[^;]*unsafe-eval/.test(policy), "no unsafe-eval");
  assert.match(policy, /object-src 'none'/);
});
