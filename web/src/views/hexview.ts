/* Full virtualised hex viewer (Phase 9) — replaces the Phase 7 peek.

   Only the visible rows are ever rendered, and bytes arrive through an
   LRU page cache over /bytes, so a 1 GiB file scrolls without being
   loaded (the criterion is < 10 MB fetched after scrolling to the end).
   Left of the dump sits the annotation gutter: the containing region and
   any symbol at that address — free once P1 exists, and the reason this
   is more than a plain dump. Selection ranges highlight, and a selection
   made anywhere scrolls the viewer to it. Clicking a byte sets the
   caret. */

import { getBytes, type BinaryModel, type Region } from "../api.ts";
import {
  fmtHex, offToVa, regionAtOff, type SelectionStore,
} from "../store.ts";
import {
  PAGE_BYTES, ROW_BYTES,
  firstRowAt, pageSpan, scrollMapFor, scrollTopForRow, sortSymbols,
  symbolAt, type ScrollMap, type SymLite,
} from "./hexutil.ts";

const ROW_H = 18;                  // must match .hexrow line-height in CSS
const MAX_PAGES = 64;              // 64 × 16 KiB = 1 MiB resident
const OVERDRAW_ROWS = 4;

export class HexView {
  private scroller: HTMLElement;   // the scrollable container
  private spacer: HTMLElement;
  private content: HTMLElement;
  private addrEl: HTMLElement | null;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  private symbols: SymLite[] = [];
  private map: ScrollMap = { spacerH: 0, scale: 1 };
  private totalRows = 0;
  private pages = new Map<number, Uint8Array>();   // LRU: insertion order
  private pending = new Set<number>();
  private bytesFetched = 0;        // observability for the fetch budget
  private renderQueued = false;

  constructor(scroller: HTMLElement, addrEl: HTMLElement | null,
              store: SelectionStore) {
    this.scroller = scroller;
    this.addrEl = addrEl;
    this.store = store;
    this.spacer = document.createElement("div");
    this.spacer.className = "hex-spacer";
    this.content = document.createElement("div");
    this.content.className = "hex-content";
    scroller.appendChild(this.spacer);
    scroller.appendChild(this.content);

    scroller.addEventListener("scroll", () => this.queueRender());
    new ResizeObserver(() => this.queueRender()).observe(scroller);

    this.content.addEventListener("click", (e) => {
      const t = (e.target as HTMLElement).closest<HTMLElement>("[data-o]");
      if (t) this.store.setCaret(Number(t.dataset.o));
    });

    store.on("selection", (sel) => {
      if (sel) this.scrollToOffset(sel.start);
      this.queueRender();
    });
    store.on("caret", () => this.queueRender());
  }

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.symbols = sortSymbols(model.symbols);
    this.pages.clear();
    this.pending.clear();
    this.bytesFetched = 0;
    this.totalRows = Math.max(1, Math.ceil(model.size / ROW_BYTES));
    this.map = scrollMapFor(this.totalRows, ROW_H);
    this.spacer.style.height = this.map.spacerH + "px";
    this.scroller.scrollTop = 0;
    this.store.setCaret(0);
    this.queueRender();
  }

  /* -------------------------------------------------------- scrolling */

  scrollToOffset(off: number): void {
    if (!this.model) return;
    const row = Math.floor(off / ROW_BYTES);
    const first = firstRowAt(this.map, this.scroller.scrollTop, ROW_H);
    const vis = Math.floor(this.scroller.clientHeight / ROW_H);
    if (row >= first + 1 && row < first + vis - 1) return;  // already shown
    const target = Math.max(0, row - Math.floor(vis / 3));
    this.scroller.scrollTop = scrollTopForRow(this.map, target, ROW_H);
  }

  private queueRender(): void {
    if (this.renderQueued) return;
    this.renderQueued = true;
    requestAnimationFrame(() => {
      this.renderQueued = false;
      this.render();
    });
  }

  /* ------------------------------------------------------- page cache */

  private pageFor(off: number): Uint8Array | null {
    const p = Math.floor(off / PAGE_BYTES);
    const page = this.pages.get(p);
    if (page) {
      this.pages.delete(p);         // refresh LRU position
      this.pages.set(p, page);
      return page;
    }
    return null;
  }

  private byteAt(off: number): number | null {
    const page = this.pageFor(off);
    return page ? page[off % PAGE_BYTES] ?? null : null;
  }

  private ensurePages(byteStart: number, byteEnd: number): void {
    if (!this.id || byteEnd <= byteStart) return;
    const [p0, p1] = pageSpan(byteStart, byteEnd);
    for (let p = p0; p <= p1; p++) {
      if (this.pages.has(p) || this.pending.has(p)) continue;
      this.pending.add(p);
      void this.fetchPage(p);
    }
  }

  private async fetchPage(p: number): Promise<void> {
    const id = this.id;
    try {
      const { data } = await getBytes(id, p * PAGE_BYTES, PAGE_BYTES);
      if (id !== this.id) return;   // binary changed mid-flight
      this.bytesFetched += data.length;
      this.pages.set(p, data);
      while (this.pages.size > MAX_PAGES) {
        const oldest = this.pages.keys().next().value as number;
        this.pages.delete(oldest);
      }
      this.queueRender();
    } catch (e) {
      const status = (e as { status?: number }).status;
      if ((status === 409 || status === 410) && id === this.id) {
        window.setTimeout(() => {
          if (id === this.id && this.pending.has(p)) {
            this.pending.delete(p);
            this.ensurePages(p * PAGE_BYTES, p * PAGE_BYTES + 1);
          }
        }, 700);
        return;                     // keep p in pending until the retry
      }
      console.warn("hex page fetch failed:", e);
    } finally {
      if (id === this.id) this.pending.delete(p);
    }
  }

  /** Total bytes fetched so far — exposed for the scroll-budget check. */
  get fetched(): number { return this.bytesFetched; }

  /* --------------------------------------------------------- painting */

  render(): void {
    if (!this.model) return;
    const vpH = this.scroller.clientHeight;
    if (vpH === 0) return;
    const visRows = Math.ceil(vpH / ROW_H) + OVERDRAW_ROWS;
    const first = Math.min(
      firstRowAt(this.map, this.scroller.scrollTop, ROW_H),
      Math.max(0, this.totalRows - visRows));
    const last = Math.min(this.totalRows, first + visRows);
    const byteStart = first * ROW_BYTES;
    const byteEnd = Math.min(this.model.size, last * ROW_BYTES);
    this.ensurePages(byteStart, byteEnd);

    this.content.style.top = scrollTopForRow(this.map, first, ROW_H) + "px";

    const sel = this.store.state.offsetRange;
    const caret = this.store.state.caret;
    const rows: string[] = [];
    let prevRegion: Region | null | undefined;
    let prevSym: SymLite | null | undefined;
    for (let row = first; row < last; row++) {
      const off = row * ROW_BYTES;
      const region = regionAtOff(this.model.regions, off);
      const va = offToVa(this.model.mappings, off);
      const symb = va !== null ? symbolAt(this.symbols, va) : null;
      rows.push(this.renderRow(off, region, symb,
        region !== prevRegion, symb !== prevSym, sel, caret));
      prevRegion = region;
      prevSym = symb;
    }
    this.content.innerHTML = rows.join("");
    this.renderAddr();
  }

  private renderRow(
    off: number, region: Region | null, symb: SymLite | null,
    regionChanged: boolean, symChanged: boolean,
    sel: { start: number; end: number } | null, caret: number | null,
  ): string {
    const n = Math.min(ROW_BYTES, (this.model?.size ?? 0) - off);
    const hexParts: string[] = [];
    const asciiParts: string[] = [];
    for (let i = 0; i < ROW_BYTES; i++) {
      if (i >= n) { hexParts.push("  "); asciiParts.push(" "); continue; }
      const o = off + i;
      const b = this.byteAt(o);
      const hx = b === null ? "··" : b.toString(16).padStart(2, "0");
      const ch = b === null ? "·"
        : b >= 0x20 && b < 0x7f ? esc(String.fromCharCode(b)) : "·";
      let cls = "";
      if (sel && o >= sel.start && o < sel.end) cls = "hl";
      if (o === caret) cls += (cls ? " " : "") + "caret";
      const attr = cls ? ` class="${cls}"` : "";
      hexParts.push(`<span data-o="${o}"${attr}>${hx}</span>`);
      asciiParts.push(`<span data-o="${o}"${attr}>${ch}</span>`);
    }
    const g1 = hexParts.slice(0, 8).join(" ");
    const g2 = hexParts.slice(8).join(" ");
    const rlabel = regionChanged && region ? esc(region.name) : "";
    const slabel = symChanged && symb ? esc(symb.name) : "";
    return `<div class="hexrow">` +
      `<span class="gr" title="${region ? esc(region.name) : ""}">${rlabel}</span>` +
      `<span class="gs" title="${symb ? esc(symb.name) : ""}">${slabel}</span>` +
      `<span class="addr">${off.toString(16).padStart(8, "0")}</span>` +
      `  ${g1}  ${g2}  ` +
      `<span class="ascii">${asciiParts.join("")}</span></div>`;
  }

  private renderAddr(): void {
    if (!this.addrEl || !this.model) return;
    const a = this.store.state.caret
      ?? this.store.state.offsetRange?.start ?? 0;
    const va = offToVa(this.model.mappings, a);
    this.addrEl.textContent =
      `${fmtHex(a)}${va !== null ? " · VA " + fmtHex(va) : ""}`;
  }
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}
