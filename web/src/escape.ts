/* The one HTML escaper. Import it; do not write another.

   Every string this app renders can come from a file an attacker chose —
   ELF section names, symbol names, import names, parse warnings quoting
   any of them. Escaping only `&` and `<` is enough for text nodes but not
   for attribute values, and `title="${...}"` is all over the views: a name
   containing a double quote closes the attribute early and everything
   after it is parsed as more attributes, which is a live event handler.

   So this escapes the full set — `&` `<` `>` `"` `'` — and is safe in both
   positions. `&` must be replaced first or it would double-escape the
   entities the later rules introduce. */

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Escape a string for interpolation into HTML text or a quoted attribute. */
export function esc(s: string): string {
  return String(s).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}
