/* Minimal hex peek panel (Phase 7) — the full virtualised hex viewer with
   its annotation gutter lands in Phase 9. Follows the caret (click) or the
   selection start, fetching one small window via /bytes. */

import { getBytes, type BinaryModel } from "../api.ts";
import { fmtHex, offToVa, type SelectionStore } from "../store.ts";

const PEEK_BYTES = 512;
const ROW = 16;

export class HexPeek {
  private dumpEl: HTMLElement;
  private addrEl: HTMLElement | null;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  private fetchSeq = 0;
  private timer: number | undefined;

  constructor(
    dumpEl: HTMLElement, addrEl: HTMLElement | null, store: SelectionStore,
  ) {
    this.dumpEl = dumpEl;
    this.addrEl = addrEl;
    this.store = store;
    store.on("caret", () => this.schedule());
    store.on("selection", () => this.schedule());
  }

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.store.setCaret(0);
  }

  private schedule(): void {
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.refetch(), 60);
  }

  private anchor(): number {
    const { caret, offsetRange } = this.store.state;
    return caret ?? offsetRange?.start ?? 0;
  }

  async refetch(): Promise<void> {
    if (!this.id || !this.model) return;
    const seq = ++this.fetchSeq;
    const base = Math.max(0, Math.min(
      this.anchor() - (this.anchor() % ROW),
      Math.max(0, this.model.size - PEEK_BYTES)));
    let data: Uint8Array, off: number;
    try {
      ({ data, off } = await getBytes(this.id, base, PEEK_BYTES));
    } catch (e) {
      console.warn("bytes fetch failed:", e);
      return;
    }
    if (seq !== this.fetchSeq) return;
    this.render(data, off);
  }

  private render(data: Uint8Array, base: number): void {
    const sel = this.store.state.offsetRange;
    const rows: string[] = [];
    for (let r = 0; r < data.length; r += ROW) {
      const addr = base + r;
      const hexParts: string[] = [];
      const asciiParts: string[] = [];
      for (let i = 0; i < ROW; i++) {
        if (r + i >= data.length) { hexParts.push("  "); asciiParts.push(" "); continue; }
        const off = addr + i;
        const b = data[r + i];
        const hx = b.toString(16).padStart(2, "0");
        const ch = b >= 0x20 && b < 0x7f
          ? esc(String.fromCharCode(b))
          : "·";
        const inSel = sel && off >= sel.start && off < sel.end;
        hexParts.push(inSel ? `<span class="hl">${hx}</span>` : hx);
        asciiParts.push(inSel ? `<span class="hl">${ch}</span>` : ch);
      }
      const g1 = hexParts.slice(0, 8).join(" ");
      const g2 = hexParts.slice(8).join(" ");
      rows.push(
        `<span class="addr">${addr.toString(16).padStart(8, "0")}</span>  ` +
        `${g1}  ${g2}  <span class="ascii">${asciiParts.join("")}</span>`);
    }
    this.dumpEl.innerHTML = rows.join("\n");
    if (this.addrEl && this.model) {
      const a = this.anchor();
      const va = offToVa(this.model.mappings, a);
      this.addrEl.textContent =
        `${fmtHex(a)}${va !== null ? " · VA " + fmtHex(va) : ""}`;
    }
  }
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
}
