import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(WEB, "src");
const VIEWS = join(SRC, "views");
const INDEX = readFileSync(join(WEB, "index.html"), "utf8");
const LISTNAV = readFileSync(join(SRC, "listnav.ts"), "utf8");
const THEME = readFileSync(join(SRC, "theme.css"), "utf8");

const view = (name: string) => readFileSync(join(VIEWS, name), "utf8");
const code = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

/* §3.6. The app had zero `aria-*` and zero `role=` attributes, and its
   primary navigation flow — click a triage finding, land on the bytes — was
   a mouse-only `<div>`. These pin the parts of the fix that a refactor could
   silently undo; the interaction itself is verified in a real browser. */

/* The three lists the work order names, plus the CFG list which has the
   same shape. A view that builds clickable rows without going through
   listnav has, by construction, built something a keyboard cannot reach. */
test("every clickable list goes through the shared listbox helper", () => {
  for (const name of ["triage.ts", "info.ts", "cfg.ts"]) {
    const src = view(name);
    assert.match(src, /from "\.\.\/listnav\.ts"/, `${name} lost the import`);
    assert.match(src, /optionList\(/, `${name} builds rows without listnav`);
  }
});

/* The failure this prevents: a view calls `classList.toggle("active", …)`
   directly, so the list looks right and announces wrong — the highlighted
   row and the one a screen reader calls selected drift apart. */
test("no view sets the active class behind listnav's back", () => {
  const offenders: string[] = [];
  for (const name of readdirSync(VIEWS)) {
    if (!name.endsWith(".ts")) continue;
    if (/classList\.toggle\(\s*["']active["']/.test(code(view(name)))) {
      offenders.push(name);
    }
  }
  assert.deepEqual(offenders, [],
    "use setOptionSelected() so aria-selected stays in step");
});

test("the listbox is one tab stop, not one per row", () => {
  // a binary with 200 functions would otherwise put 200 stops in the tab
  // order and make Tab useless for reaching anything past the CFG pane
  assert.match(LISTNAV, /tabIndex = -1/);
  assert.match(LISTNAV, /tabIndex = row === stop \? 0 : -1/);
});

test("rows are reachable and activatable by keyboard", () => {
  for (const key of ["ArrowDown", "ArrowUp", "Home", "End", "Enter"]) {
    assert.match(LISTNAV, new RegExp(`"${key}"`), `no handling for ${key}`);
  }
});

/* Focus must be visible or arrowing through a dense list is blind. */
test("focused rows have a visible focus ring", () => {
  for (const cls of ["finding-row", "region-row", "fn-row"]) {
    assert.match(THEME, new RegExp(`\\.${cls}:focus-visible`),
      `${cls} has no focus style`);
  }
});

/* Icon-only controls: `title` is a tooltip a keyboard user never hovers and
   a screen reader is not obliged to read. */
test("icon-only buttons carry a real accessible name", () => {
  for (const id of ["nav-prev", "nav-next", "theme-btn", "help-btn",
                    "zoom-clear", "locate-clear"]) {
    const tag = INDEX.match(new RegExp(`<button id="${id}"[^>]*>`, "s"));
    assert.ok(tag, `${id} is gone`);
    assert.match(tag[0], /aria-label="/, `${id} has no accessible name`);
  }
});

test("text inputs without a visible label carry one", () => {
  for (const id of ["path-input", "cfg-search"]) {
    const tag = INDEX.match(new RegExp(`<input id="${id}"[^>]*>`, "s"));
    assert.ok(tag, `${id} is gone`);
    assert.match(tag[0], /aria-label="/,
      `${id} relies on a placeholder, which disappears once it has text`);
  }
});

/* Analysis progress was visual-only; it is the app's main feedback channel
   while a large file is being processed. */
test("analysis status is announced", () => {
  const tag = INDEX.match(/<span id="status-chip"[^>]*>/);
  assert.ok(tag);
  assert.match(tag[0], /role="status"/);
  assert.match(tag[0], /aria-live="polite"/);
});

/* The work order's specific complaint: the only key bindings were
   documented in a `title` tooltip. */
test("the shortcut list is a real dialog, not a tooltip", () => {
  const help = readFileSync(join(SRC, "help.ts"), "utf8");
  assert.match(help, /showModal\(\)/,
    "showModal gives the focus trap and Escape-to-close; show() does not");
  for (const key of ["[", "]", "Enter", "Esc"]) {
    assert.ok(help.includes(`"${key}"`), `${key} is undocumented`);
  }
});
