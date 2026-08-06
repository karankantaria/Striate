import assert from "node:assert/strict";
import { test } from "node:test";

import { SafeHtml, html, joinHtml, rawHtml } from "../src/dom.ts";

/* The element half of dom.ts needs a DOM and is exercised in the browser;
   the markup half is pure and is the half that carries the security weight,
   so it gets pinned here. The payload is S2's, so a regression shows up as
   the original bug rather than an abstract one. */

const HOSTILE = 'a"onmouseover=b';

test("interpolated values are escaped", () => {
  assert.equal(html`<span>${HOSTILE}</span>`.value,
    "<span>a&quot;onmouseover=b</span>");
});

test("a hostile name cannot close a quoted attribute", () => {
  const out = html`<span title="${HOSTILE}">${HOSTILE}</span>`.value;
  // exactly the two quotes the author wrote around the title value; the
  // one inside the name became an entity, so the attribute cannot end early
  assert.equal(out.match(/"/g)!.length, 2, "a quote escaped from the value");
  assert.equal(out,
    '<span title="a&quot;onmouseover=b">a&quot;onmouseover=b</span>');
});

test("markup in the literal is preserved, markup in a value is not", () => {
  const evil = "<img src=x onerror=alert(1)>";
  const out = html`<b>${evil}</b>`.value;
  assert.ok(out.startsWith("<b>") && out.endsWith("</b>"), "literal kept");
  assert.ok(!out.includes("<img"), "value neutered");
});

test("nested SafeHtml is spliced verbatim, not double-escaped", () => {
  // the reason SafeHtml is a wrapper object rather than a branded string:
  // a branded string is still a string at runtime and would be re-escaped
  const inner = html`<i>${HOSTILE}</i>`;
  const outer = html`<b>${inner}</b>`;
  assert.equal(outer.value, "<b><i>a&quot;onmouseover=b</i></b>");
  assert.ok(!outer.value.includes("&amp;quot;"), "double-escaped");
});

test("joinHtml keeps fragments safe and the separator literal", () => {
  const joined = joinHtml([html`${HOSTILE}`, html`${"<b>"}`], "<br>");
  assert.equal(joined.value, "a&quot;onmouseover=b<br>&lt;b&gt;");
  assert.ok(joined instanceof SafeHtml);
});

test("numbers, null and undefined interpolate without throwing", () => {
  assert.equal(html`${1}${null}${undefined}`.value, "1nullundefined");
});

test("rawHtml is the deliberate escape hatch", () => {
  assert.equal(rawHtml("<br>").value, "<br>");
  // and it composes without being re-escaped
  assert.equal(html`a${rawHtml("<br>")}b`.value, "a<br>b");
});

test("an empty template is still SafeHtml", () => {
  assert.ok(html`` instanceof SafeHtml);
  assert.equal(html``.value, "");
});
