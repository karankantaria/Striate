import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import {
  ALL_PANES, DEFAULT_ROUTE, paneElementId, workspaceByRoute,
  WORKSPACE_ROUTES, WORKSPACES,
} from "../src/workspace.ts";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const INDEX = readFileSync(join(WEB, "index.html"), "utf8");
const THEME = readFileSync(join(WEB, "src", "theme.css"), "utf8");
const MAIN = readFileSync(join(WEB, "src", "main.ts"), "utf8");

/* §3.4's failure mode is not a crash, it is a pane that quietly cannot be
   reached: the table, the markup and the CSS grids have to agree, and
   nothing in the type system makes them. These tests are that agreement. */

test("routes and ids are unique", () => {
  assert.equal(new Set(WORKSPACE_ROUTES).size, WORKSPACES.length);
  assert.equal(new Set(WORKSPACES.map((w) => w.id)).size, WORKSPACES.length);
});

test("the default route is the first workspace", () => {
  assert.equal(DEFAULT_ROUTE, WORKSPACES[0].route);
  assert.equal(workspaceByRoute(DEFAULT_ROUTE).id, WORKSPACES[0].id);
});

test("an unknown route still yields a workspace", () => {
  // the router resolves before calling this, but a lookup that can return
  // undefined would put `hidden` on every pane and show a blank grid
  assert.equal(workspaceByRoute("/nope").id, WORKSPACES[0].id);
});

test("every workspace names only real panes", () => {
  for (const ws of WORKSPACES) {
    assert.ok(ws.panes.length, `${ws.id} shows nothing`);
    for (const pane of ws.panes) {
      assert.ok((ALL_PANES as readonly string[]).includes(pane),
        `${ws.id} names an unknown pane: ${pane}`);
    }
    assert.equal(new Set(ws.panes).size, ws.panes.length,
      `${ws.id} lists a pane twice`);
  }
});

/* The regression this exists to catch: add a pane to the grid, forget to
   put it in a workspace, and it is invisible forever with no error. */
test("every pane is reachable from some workspace", () => {
  for (const pane of ALL_PANES) {
    const homes = WORKSPACES.filter((w) => w.panes.includes(pane));
    assert.ok(homes.length, `${pane} is in no workspace — unreachable`);
  }
});

test("every pane exists in the markup", () => {
  for (const pane of ALL_PANES) {
    assert.match(INDEX, new RegExp(`id="${paneElementId(pane)}"`),
      `${pane} has no element`);
  }
});

test("every pane has a grid area of its name", () => {
  for (const pane of ALL_PANES) {
    assert.match(THEME,
      new RegExp(`#${paneElementId(pane)}\\s*\\{\\s*grid-area:\\s*${pane};`),
      `${pane} is not placed in the grid`);
  }
});

/* A workspace with no grid template would inherit whatever the previous
   one set, which reads as "the tab did nothing". */
test("every workspace has its own grid template", () => {
  for (const ws of WORKSPACES) {
    const block = new RegExp(
      `#layout\\[data-workspace="${ws.id}"\\]\\s*\\{[^}]*grid-template-areas:([^;]*);`);
    const m = THEME.match(block);
    assert.ok(m, `${ws.id} has no grid template`);
    const areas = m[1];
    for (const pane of ws.panes) {
      assert.match(areas, new RegExp(`\\b${pane}\\b`),
        `${ws.id}'s grid has no slot for ${pane}`);
    }
  }
});

/* `.pane` sets `display: flex`, which beats the user-agent's `[hidden] {
   display: none }` on specificity — so without an explicit author rule,
   hiding a pane silently does nothing at all and every workspace shows
   ten panes again. Exactly the bug §3.4 is about, reintroduced quietly. */
test("hidden panes are actually hidden", () => {
  assert.match(THEME, /\.pane\[hidden\]\s*\{\s*display:\s*none;/);
});

/* Hiding is `display: none` because that zeroes the host, and a zero-sized
   host is what stops each view drawing and fetching (every view already
   guards on it). Hiding by opacity or offscreen positioning would keep all
   ten views live and lose the point. */
test("the layout hides panes by removing them from layout", () => {
  const focus = THEME.match(/#layout\[data-focus\][^}]*\.pane\s*\{([^}]*)\}/);
  assert.ok(focus, "focus mode does not hide the other panes");
  assert.match(focus[1], /display:\s*none/);
});

test("the tab bar is wired to the router, not to a stored preference", () => {
  // the URL is the state: back/forward and deep links have to work, and a
  // localStorage copy would silently disagree with the address bar
  assert.match(MAIN, /router\.go\(ws\.route\)/);
  assert.doesNotMatch(MAIN, /localStorage[^\n]*workspace/i);
});
