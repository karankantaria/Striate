/* DOM-free math and lookups behind the virtualised hex viewer (Phase 9).
   Kept separate so node --test can exercise the scroll mapping and the
   symbol/annotation lookups without a browser. */

import type { Symbol_ } from "../api.ts";

export const ROW_BYTES = 16;
export const PAGE_BYTES = 16384;

/** Browsers cap element heights (Chrome ≈ 33.5M px). A 1 GiB file is 67M
    rows ≈ 1.2G px of spacer, so past this cap the spacer is compressed
    and scroll positions are scaled back up to virtual rows. */
export const MAX_SPACER_PX = 24_000_000;

export interface ScrollMap {
  spacerH: number;   // actual spacer element height in px
  scale: number;     // virtual px per spacer px (1 = uncompressed)
}

export function scrollMapFor(totalRows: number, rowH: number): ScrollMap {
  const totalPx = totalRows * rowH;
  if (totalPx <= MAX_SPACER_PX) return { spacerH: totalPx, scale: 1 };
  return { spacerH: MAX_SPACER_PX, scale: totalPx / MAX_SPACER_PX };
}

export function firstRowAt(map: ScrollMap, scrollTop: number,
                           rowH: number): number {
  return Math.max(0, Math.floor((scrollTop * map.scale) / rowH));
}

export function scrollTopForRow(map: ScrollMap, row: number,
                                rowH: number): number {
  return (row * rowH) / map.scale;
}

/** Inclusive page index range covering [byteStart, byteEnd). */
export function pageSpan(byteStart: number, byteEnd: number,
                         pageBytes = PAGE_BYTES): [number, number] {
  const p0 = Math.floor(byteStart / pageBytes);
  const p1 = Math.floor(Math.max(byteStart, byteEnd - 1) / pageBytes);
  return [p0, p1];
}

/* -------------------------------------------------------------- symbols */

export interface SymLite {
  name: string; va: number; size: number; kind: string;
}

/** Sorted-by-VA symbol table for bisecting. Zero-size symbols still get
    one addressable byte so they can be found at all. */
export function sortSymbols(symbols: Symbol_[]): SymLite[] {
  return symbols
    .filter((s) => s.va >= 0 && s.name)
    .map((s) => ({ name: s.name, va: s.va,
                   size: Math.max(1, s.size), kind: s.kind }))
    .sort((a, b) => a.va - b.va);
}

/** The symbol containing `va`, or null. Prefers the latest-starting one. */
export function symbolAt(sorted: SymLite[], va: number): SymLite | null {
  let lo = 0, hi = sorted.length;
  while (lo < hi) {                  // bisect_right on symbol va
    const mid = (lo + hi) >> 1;
    if (sorted[mid].va <= va) lo = mid + 1; else hi = mid;
  }
  for (let i = lo - 1; i >= 0; i--) {
    const s = sorted[i];
    if (va < s.va + s.size) return s;
    // symbols are sorted by start; once one starts too early to reach va
    // AND everything before starts even earlier, only overlapping ones
    // could still match — scan a few, then give up (tables are shallow)
    if (lo - i > 8) break;
  }
  return null;
}
