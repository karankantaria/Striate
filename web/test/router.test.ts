import assert from "node:assert/strict";
import { test } from "node:test";

import { normalizePath, resolveRoute } from "../src/router.ts";

/* The DOM half of the router (history, popstate) is exercised in a real
   browser; these are the pure parts, which are also the parts that decide
   what a stale bookmark does. */

const KNOWN = ["/", "/bytes", "/patterns", "/code", "/all"];

test("normalizePath collapses the ways of writing the same path", () => {
  assert.equal(normalizePath("/"), "/");
  assert.equal(normalizePath(""), "/");
  assert.equal(normalizePath("/bytes"), "/bytes");
  assert.equal(normalizePath("/bytes/"), "/bytes");
  assert.equal(normalizePath("/bytes///"), "/bytes");
});

test("a known path resolves to itself", () => {
  for (const route of KNOWN) {
    assert.equal(resolveRoute(route, KNOWN, "/"), route);
  }
  assert.equal(resolveRoute("/code/", KNOWN, "/"), "/code");
});

/* An unknown path must land somewhere usable rather than on an error: a
   typo, an old bookmark, or a route that existed in a previous version all
   arrive here, and none of them is the user's fault. */
test("an unknown path falls back instead of failing", () => {
  assert.equal(resolveRoute("/nope", KNOWN, "/"), "/");
  assert.equal(resolveRoute("/bytes/extra", KNOWN, "/"), "/");
  assert.equal(resolveRoute("/BYTES", KNOWN, "/"), "/");
});

/* Paths are matched exactly, not by prefix. `/login` (§2.5) will be a
   route of its own, and a prefix match would have `/` swallow it. */
test("matching is exact, so a future /login is not swallowed", () => {
  assert.equal(resolveRoute("/login", KNOWN, "/"), "/");
  assert.equal(resolveRoute("/login", [...KNOWN, "/login"], "/"), "/login");
});
