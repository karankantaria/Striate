/* SelectionStore — the linkage core. One selection in absolute file
   offsets, plus VA and hovered offset, broadcast to every view. This is
   the generalisation of binary_viewer's rangeSelected(float,float): views
   are linked, not adjacent, because they all read and write this store.

   DOM-free on purpose: node --test exercises the off<->va conversion. */

import type { BinaryModel, Region } from "./api.ts";

export interface OffsetRange { start: number; end: number } // half-open [start, end)

export interface Selection {
  offsetRange: OffsetRange | null;
  vaRange: OffsetRange | null;      // derived; null when unmapped
  hoveredOffset: number | null;
  caret: number | null;             // hex-peek anchor
}

type Events = {
  selection: OffsetRange | null;
  hover: number | null;
  caret: number | null;
  theme: "light" | "dark";
};

type Handler<K extends keyof Events> = (v: Events[K]) => void;

export class SelectionStore {
  state: Selection = {
    offsetRange: null, vaRange: null, hoveredOffset: null, caret: null,
  };
  private handlers: { [K in keyof Events]: Handler<K>[] } = {
    selection: [], hover: [], caret: [], theme: [],
  };
  private model: BinaryModel | null = null;

  setModel(model: BinaryModel | null): void {
    this.model = model;
    this.state = {
      offsetRange: null, vaRange: null, hoveredOffset: null, caret: null,
    };
  }

  on<K extends keyof Events>(ev: K, fn: Handler<K>): void {
    this.handlers[ev].push(fn);
  }

  private emit<K extends keyof Events>(ev: K, v: Events[K]): void {
    for (const fn of this.handlers[ev]) fn(v);
  }

  setSelection(range: OffsetRange | null): void {
    if (range) {
      const size = this.model?.size ?? Infinity;
      let start = Math.max(0, Math.min(range.start, range.end));
      let end = Math.min(size, Math.max(range.start, range.end));
      if (end - start < 1) { start = Math.max(0, end - 1); }
      range = { start, end };
    }
    this.state.offsetRange = range;
    this.state.vaRange = range ? this.vaRangeOf(range) : null;
    this.emit("selection", range);
  }

  setHover(off: number | null): void {
    this.state.hoveredOffset = off;
    this.emit("hover", off);
  }

  setCaret(off: number | null): void {
    this.state.caret = off;
    this.emit("caret", off);
  }

  setTheme(theme: "light" | "dark"): void {
    this.emit("theme", theme);
  }

  private vaRangeOf(r: OffsetRange): OffsetRange | null {
    if (!this.model) return null;
    const a = offToVa(this.model.mappings, r.start);
    const b = offToVa(this.model.mappings, Math.max(r.start, r.end - 1));
    return a !== null && b !== null ? { start: a, end: b + 1 } : null;
  }
}

/* ------------------------------------------------ off <-> va conversion
   `mappings` is the model's compact interval table: sorted, non-overlapping
   [file_off, size, vaddr] triples. Mirrors BinaryModel.off_to_va/va_to_off. */

export function offToVa(
  mappings: [number, number, number][], off: number,
): number | null {
  let lo = 0, hi = mappings.length;
  while (lo < hi) {                    // bisect_right on file_off
    const mid = (lo + hi) >> 1;
    if (mappings[mid][0] <= off) lo = mid + 1; else hi = mid;
  }
  const i = lo - 1;
  if (i < 0) return null;
  const [fo, size, va] = mappings[i];
  return off < fo + size ? va + (off - fo) : null;
}

export function vaToOff(
  mappings: [number, number, number][], va: number,
): number | null {
  // mappings are sorted by file_off, not vaddr — scan (tables are tiny)
  for (const [fo, size, v0] of mappings) {
    if (va >= v0 && va < v0 + size) return fo + (va - v0);
  }
  return null;
}

export function regionAtOff(
  regions: Region[], off: number,
): Region | null {
  for (const r of regions) {
    if (r.file_off >= 0 && off >= r.file_off && off < r.file_off + r.file_size) {
      return r;
    }
  }
  return null;
}

/* ------------------------------------------------------- formatting */

export function fmtHex(n: number): string {
  return "0x" + n.toString(16);
}

export function fmtSize(n: number): string {
  if (n >= 1 << 30) return (n / (1 << 30)).toFixed(2) + " GiB";
  if (n >= 1 << 20) return (n / (1 << 20)).toFixed(2) + " MiB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KiB";
  return n + " B";
}

export function fmtRange(r: OffsetRange): string {
  return `${fmtHex(r.start)}–${fmtHex(r.end)} (${fmtSize(r.end - r.start)})`;
}
