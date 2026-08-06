import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { errorText } from "../src/panestatus.ts";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
const VIEWS = join(SRC, "views");

/* The DOM half of panestatus.ts is verified in a real browser rather than
   here: these units are deliberately DOM-free (see hexutil/cfgutil) and the
   project keeps its dependency list short, so no jsdom. What is testable
   without a DOM is the message derivation and — more valuably — the
   invariant that no view has quietly gone back to swallowing errors. */

test("errorText prefers the server's own explanation", () => {
  // api.ts unwraps the server's `detail` field into Error.message
  assert.equal(errorText(new Error("artifact 'trigram' not ready")),
    "artifact 'trigram' not ready");
});

test("errorText copes with whatever was actually thrown", () => {
  assert.equal(errorText("plain string"), "plain string");
  assert.equal(errorText(new Error("")), "unknown error");
  assert.equal(errorText(undefined), "unknown error");
  assert.equal(errorText(null), "unknown error");
  assert.equal(errorText({ nope: 1 }), "unknown error");
});

/* §3.3's actual invariant. A pane that fails silently is worse than one that
   fails loudly: for a triage tool, "blank" has to mean "no such structure in
   this file", and if it can also mean "the request failed" then the tool is
   lying by omission — in the reassuring direction. */
test("no view swallows a fetch failure into the console", () => {
  const offenders: string[] = [];
  for (const name of readdirSync(VIEWS)) {
    if (!name.endsWith(".ts")) continue;
    const src = readFileSync(join(VIEWS, name), "utf8");
    if (/console\.(warn|error|log)\s*\(/.test(src)) offenders.push(name);
  }
  assert.deepEqual(offenders, [],
    "report failures with paneError() so the user can see them");
});

test("every view that can fail a fetch imports the reporter", () => {
  // the eight sites named in the work order live in these seven files
  const fetchers = ["overall.ts", "plot.ts", "hexview.ts", "hist2d.ts",
                    "hist3d.ts", "image.ts", "dotplot.ts"];
  for (const name of fetchers) {
    const src = readFileSync(join(VIEWS, name), "utf8");
    assert.match(src, /from "\.\.\/panestatus\.ts"/, `${name} lost the import`);
    assert.match(src, /paneError\(/, `${name} never reports a failure`);
    assert.match(src, /clearPaneError\(/,
      `${name} shows errors but never clears them — a transient failure ` +
      `would leave a permanent message over working content`);
  }
});

/* Found by killing the server mid-session rather than by reading code: five
   panes recovered on the next click and the Overall pane did not, because it
   is file-bound and only refetches on a resize or a mode change. A permanent
   error banner with no way out is its own dead end, so every reporting site
   passes a retry. */
test("every failure offers a way to retry", () => {
  const fetchers = ["overall.ts", "plot.ts", "hexview.ts", "hist2d.ts",
                    "hist3d.ts", "image.ts", "dotplot.ts"];
  for (const name of fetchers) {
    const src = readFileSync(join(VIEWS, name), "utf8");
    for (const call of src.matchAll(/paneError\(([\s\S]*?)\);/g)) {
      assert.match(call[1], /\(\)\s*=>/,
        `${name}: a paneError without a retry callback strands the pane`);
    }
  }
});

test("the retry affordance is a real button, not a clickable div", () => {
  // keyboard-reachable and focusable by construction — the mistake §3.6
  // catalogues in the findings list and region list
  const src = readFileSync(join(SRC, "panestatus.ts"), "utf8");
  assert.match(src, /createElement\("button"\)/);
  assert.match(src, /button\.type = "button"/);
});

test("the reporter never routes text through innerHTML", () => {
  // error text carries a server-supplied detail, which can quote a hostile
  // file's own strings; a message about a malicious binary must not itself
  // become markup (same reasoning as escape.ts)
  const src = readFileSync(join(SRC, "panestatus.ts"), "utf8");
  // strip comments first — the module explains *why* it avoids innerHTML,
  // and a naive scan would flag that explanation as the offence
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  assert.ok(!/innerHTML/.test(code), "use textContent");
  assert.match(code, /textContent/);
});
