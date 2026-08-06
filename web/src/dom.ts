/* A very small element builder, so `innerHTML` stops being the convenient
   path (SECURITY-UI-WORKORDER §3.5).

   S2's root cause was not that someone wrote a bad escaper — it was that
   building HTML by string concatenation made escaping a thing you had to
   remember, seven times, and five of those went wrong. `escape.ts` fixes the
   escaper. This fixes the default: text set through `textContent` cannot be
   markup no matter what the binary contained, so a forgotten `esc()` stops
   being a vulnerability and becomes impossible to write.

   Deliberately tiny — this is not a framework. No reactivity, no diffing, no
   templates: just enough that constructing a row of elements is shorter than
   concatenating a string, because whichever is shorter is what gets used. */

import { esc } from "./escape.ts";

type Attrs = Record<string, string | number | boolean | null | undefined>;
export type Child = Node | string | number | null | undefined | false;

/** Create an element. Attributes are set as attributes; `class` and `text`
    are the two shorthands worth having.

    Every string child becomes a text node, so interpolated values are inert
    by construction:

        el("span", { class: "rname", title: region.name }, region.name)

    is safe for any bytes the file happened to contain. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, attrs: Attrs = {}, ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") { node.textContent = String(value); continue; }
    node.setAttribute(key, value === true ? "" : String(value));
  }
  append(node, ...children);
  return node;
}

/** Append children, skipping the falsy ones so `cond && el(…)` reads well. */
export function append(parent: Node, ...children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(
      typeof child === "string" || typeof child === "number"
        ? document.createTextNode(String(child))
        : child);
  }
}

/** Replace an element's contents. The counterpart to `innerHTML = …`, and
    the reason a view never needs to reach for it. */
export function replace(parent: Element, ...children: Child[]): void {
  parent.replaceChildren();
  append(parent, ...children);
}

/** A `<span class="…">text</span>`, which is most of what these views build. */
export function span(cls: string, text: Child): HTMLSpanElement {
  return el("span", { class: cls }, text);
}

/* ------------------------------------------------ markup, when it must be

   Building nodes is the default, but a couple of places genuinely want a
   string of markup: the hex viewer renders ~1,700 spans per scroll frame and
   `innerHTML` is measurably the right tool there, and the tooltip is a single
   shared element whose content is one small fragment.

   For those, `html` is a tagged template that escapes every interpolated
   value, and `SafeHtml` is a type only `html` (or an explicit `rawHtml`)
   can produce. The two together mean the unsafe path is not merely
   discouraged — a plain string will not type-check where markup is
   expected, so "I forgot to escape this one" stops being expressible. That
   was S2's actual failure mode: five of seven escapers were wrong and
   nothing complained. */

/** Markup whose interpolations have been escaped.

    A real wrapper object rather than a branded string: the brand would be
    compile-time only, and `html` needs to tell at *runtime* whether a value
    is an already-escaped fragment (splice it verbatim) or ordinary text
    (escape it). A branded string is still a string at runtime, so nested
    fragments would silently double-escape. */
export class SafeHtml {
  // written out rather than a `constructor(readonly value: string)`
  // parameter property: Vite compiles those, but the test runner is
  // `node --test` in strip-only mode, which rejects them outright
  readonly value: string;
  constructor(value: string) { this.value = value; }
  toString(): string { return this.value; }
}

/** Tagged template producing SafeHtml. Every `${…}` is escaped; nested
    SafeHtml is spliced verbatim so fragments compose:

        html`<span title="${name}">${name}</span>`

    is safe for any bytes the binary contained. */
export function html(strings: TemplateStringsArray,
                     ...values: unknown[]): SafeHtml {
  let out = strings[0];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    // escape.ts stays the single escaper — a second entity table here is
    // exactly how S2 happened, and two of them would drift.
    out += (v instanceof SafeHtml ? v.value : esc(String(v))) + strings[i + 1];
  }
  return new SafeHtml(out);
}

/** Join SafeHtml fragments, staying SafeHtml. */
export function joinHtml(parts: SafeHtml[], sep = ""): SafeHtml {
  return new SafeHtml(parts.map((p) => p.value).join(sep));
}

/** Mark a literal as safe. Only for markup with **no interpolation at all**
    — if it has a `${…}`, use `html` so the value gets escaped. */
export function rawHtml(literal: string): SafeHtml {
  return new SafeHtml(literal);
}

/** Set an element's markup. The one place `innerHTML` is written, and it
    cannot be reached with an unescaped string. */
export function setHtml(node: Element, markup: SafeHtml): void {
  node.innerHTML = markup.value;
}
