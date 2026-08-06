/* Singleton hover tooltip (the #tooltip div in index.html).

   Content is SafeHtml, not string: tooltips quote section and symbol names
   straight out of the binary, so the type is what stops an unescaped one
   getting here. Build it with the `html` tag from dom.ts. */

import { setHtml, type SafeHtml } from "./dom.ts";

let el: HTMLElement | null = null;

function tip(): HTMLElement {
  if (!el) el = document.getElementById("tooltip")!;
  return el;
}

export function showTooltip(clientX: number, clientY: number,
                            content: SafeHtml): void {
  const t = tip();
  setHtml(t, content);
  t.hidden = false;
  const pad = 14;
  const w = t.offsetWidth, h = t.offsetHeight;
  let x = clientX + pad, y = clientY + pad;
  if (x + w > window.innerWidth - 4) x = clientX - w - pad;
  if (y + h > window.innerHeight - 4) y = clientY - h - pad;
  t.style.left = x + "px";
  t.style.top = y + "px";
}

export function hideTooltip(): void {
  tip().hidden = true;
}
