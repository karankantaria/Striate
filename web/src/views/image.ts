/* Image view — raw bytes as pixels (Phase 9). All the P3 image surface
   modes: grey/rgb/bgr/rgba/bgra at 8/12/16 bit plus the 24 Bayer CFA
   modes, each at 8/12/16 bit. The server renders to PNG; we draw the
   decoded bitmap pixelated and map pointer positions back to file
   offsets, so the picture stays a linked view, not a picture.

   The one control that matters is row stride (§5.7) — hence the
   autocorrelation suggester's top candidates rendered as one-click
   buttons next to the width input. */

import {
  getStrideSuggestions, getSurfaceRgb,
  type BinaryModel, type StrideCandidate, type SurfaceMeta,
} from "../api.ts";
import {
  fmtHex, offToVa, regionAtOff, type SelectionStore,
} from "../store.ts";
import { hideTooltip, showTooltip } from "../tooltip.ts";

const MAX_ROWS_REQ = 2048;         // rows requested per fetch (server caps)
const MAX_WIDTH = 16384;

export interface ImageControls {
  mode: HTMLSelectElement;
  width: HTMLInputElement;
  offset: HTMLInputElement;
  invert: HTMLInputElement;        // checkbox
  suggest: HTMLElement;            // container for suggester buttons
  fitSel: HTMLButtonElement;       // width-from-selection helper
  status: HTMLElement;
}

/** Bytes per pixel for the mode strings this view generates. Mirrors
    surfaces/image.py bytes_per_pixel for exactly those strings. */
export function bytesPerPixel(mode: string): number {
  const depth = mode.endsWith("16") ? 16 : mode.endsWith("12") ? 12 : 8;
  let channels = 1;
  if (!mode.startsWith("bayer")) {
    if (mode.startsWith("rgba") || mode.startsWith("bgra")) channels = 4;
    else if (mode.startsWith("rgb") || mode.startsWith("bgr")) channels = 3;
  }
  return channels * depth / 8;
}

const CFA_PHASES = ["RGGB", "BGGR", "GRBG", "GBRG"];
const CHANNEL_PERMS = ["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"];

function populateModes(sel: HTMLSelectElement): void {
  const add = (group: HTMLOptGroupElement, value: string) => {
    const o = document.createElement("option");
    o.value = o.textContent = value;
    group.appendChild(o);
  };
  const packed = document.createElement("optgroup");
  packed.label = "Packed";
  for (const fmt of ["grey", "rgb", "bgr", "rgba", "bgra"]) {
    for (const d of [8, 12, 16]) add(packed, `${fmt}${d}`);
  }
  sel.appendChild(packed);
  for (const d of [8, 12, 16]) {
    const g = document.createElement("optgroup");
    g.label = `Bayer ${d}-bit`;
    for (const phase of CFA_PHASES) {
      for (const perm of CHANNEL_PERMS) {
        add(g, d === 8 ? `bayer_${phase}_${perm}`
                       : `bayer_${phase}_${perm}_${d}`);
      }
    }
    sel.appendChild(g);
  }
  sel.value = "grey8";
}

export class ImageView {
  private host: HTMLElement;
  private canvas: HTMLCanvasElement;
  private c: ImageControls;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  private bitmap: ImageBitmap | null = null;
  private meta: SurfaceMeta | null = null;
  private start = 0;                 // file offset of the bitmap's first byte
  private manualOffset = false;      // user typed an offset; stop following
  private fetchSeq = 0;
  private suggestSeq = 0;
  private refetchTimer: number | undefined;
  private ro: ResizeObserver;
  // bitmap placement in CSS px, set by draw()
  private dx = 0; private dy = 0; private dw = 0; private dh = 0;

  constructor(host: HTMLElement, controls: ImageControls,
              store: SelectionStore) {
    this.host = host;
    this.c = controls;
    this.store = store;
    this.canvas = document.createElement("canvas");
    host.appendChild(this.canvas);
    populateModes(controls.mode);

    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(host);

    store.on("selection", () => {
      this.manualOffset = false;     // a new selection re-anchors the view
      this.debouncedRefetch();
      this.suggest();
    });

    controls.mode.addEventListener("change", () => {
      this.refetch();
      this.suggest();
    });
    controls.width.addEventListener("change", () => this.refetch());
    controls.invert.addEventListener("change", () => this.refetch());
    controls.offset.addEventListener("change", () => {
      this.manualOffset = true;
      this.refetch();
    });
    controls.fitSel.addEventListener("click", () => {
      const sel = this.store.state.offsetRange;
      if (!sel) return;
      const bpp = bytesPerPixel(this.c.mode.value);
      const w = Math.round((sel.end - sel.start) / bpp);
      this.c.width.value = String(Math.max(1, Math.min(MAX_WIDTH, w)));
      this.refetch();
    });

    this.canvas.addEventListener("pointermove", (e) => this.hover(e));
    this.canvas.addEventListener("pointerleave", () => hideTooltip());
    this.canvas.addEventListener("click", (e) => {
      const off = this.offsetAt(e.clientX, e.clientY);
      if (off !== null) this.store.setCaret(off);
    });
  }

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.bitmap = null;
    this.manualOffset = false;
    this.c.offset.value = "0x0";
    this.refetch();
    this.suggest();
  }

  /* ------------------------------------------------------------ range */

  private range(): { start: number; end: number } {
    const size = this.model?.size ?? 0;
    const sel = this.store.state.offsetRange;
    if (!this.manualOffset && sel) return { start: sel.start, end: sel.end };
    const start = Math.max(0, Math.min(parseOffset(this.c.offset.value), size));
    return { start, end: size };
  }

  private widthPx(): number {
    const w = parseInt(this.c.width.value, 10);
    return Number.isFinite(w) ? Math.max(1, Math.min(MAX_WIDTH, w)) : 256;
  }

  private debouncedRefetch(): void {
    window.clearTimeout(this.refetchTimer);
    this.refetchTimer = window.setTimeout(() => this.refetch(), 150);
  }

  /* ------------------------------------------------------------ fetch */

  async refetch(): Promise<void> {
    if (!this.id || !this.model) return;
    const seq = ++this.fetchSeq;
    const mode = this.c.mode.value;
    const width = this.widthPx();
    const { start, end } = this.range();
    if (!this.manualOffset) this.c.offset.value = fmtHex(start);
    const rowBytes = width * bytesPerPixel(mode);
    const capped = Math.min(end, start + Math.ceil(rowBytes * MAX_ROWS_REQ));
    try {
      const { bitmap, meta } = await getSurfaceRgb(this.id, "image", {
        start, end: capped, mode, width,
        invert: this.c.invert.checked, max_rows: MAX_ROWS_REQ,
      });
      if (seq !== this.fetchSeq) return;
      this.bitmap?.close();
      this.bitmap = bitmap;
      this.meta = meta;
      this.start = start;
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 409 || status === 410) {
        window.clearTimeout(this.refetchTimer);
        this.refetchTimer = window.setTimeout(() => this.refetch(), 700);
      } else {
        console.warn("image fetch failed:", e);
      }
      return;
    }
    this.draw();
    this.renderStatus();
  }

  private async suggest(): Promise<void> {
    if (!this.id) return;
    const seq = ++this.suggestSeq;
    const { start, end } = this.range();
    let cands: StrideCandidate[];
    try {
      cands = await getStrideSuggestions(this.id, this.c.mode.value, start, end);
    } catch {
      return;                        // suggester is best-effort
    }
    if (seq !== this.suggestSeq) return;
    this.c.suggest.replaceChildren();
    for (const cand of cands) {
      const b = document.createElement("button");
      b.textContent = `${cand.pixels}px`;
      b.title = `autocorrelation lag ${cand.bytes} B ` +
        `(score ${cand.score.toFixed(2)}, ${cand.origin})` +
        (cand.exact ? "" : " — not a whole pixel multiple");
      b.classList.toggle("inexact", !cand.exact);
      b.addEventListener("click", () => {
        this.c.width.value = String(cand.pixels);
        this.refetch();
      });
      this.c.suggest.appendChild(b);
    }
  }

  /* ------------------------------------------------------------- draw */

  draw(): void {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (w === 0 || h === 0) return;
    this.canvas.width = w * devicePixelRatio;
    this.canvas.height = h * devicePixelRatio;
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
    const ctx = this.canvas.getContext("2d")!;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!this.bitmap) return;

    const bw = this.bitmap.width, bh = this.bitmap.height;
    const scale = Math.min(w / bw, h / bh);
    // integer upscale when we can afford it: pixels are data
    const s = scale >= 1 ? Math.max(1, Math.floor(scale)) : scale;
    this.dw = bw * s; this.dh = bh * s;
    this.dx = Math.floor((w - this.dw) / 2);
    this.dy = Math.floor((h - this.dh) / 2);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.bitmap, this.dx, this.dy, this.dw, this.dh);
  }

  private renderStatus(): void {
    if (!this.meta) return;
    const [h, w] = this.meta.shape;
    const parts = [`${w}×${h}`];
    const warns = (this.meta.meta.warnings as string[] | undefined) ?? [];
    for (const wtext of warns) parts.push(wtext);
    this.c.status.textContent = parts.join(" · ");
    this.c.status.title = parts.join("\n");
  }

  /* ------------------------------------------------------- interaction */

  private offsetAt(clientX: number, clientY: number): number | null {
    if (!this.bitmap || !this.meta || !this.model) return null;
    const r = this.canvas.getBoundingClientRect();
    const x = clientX - r.left - this.dx;
    const y = clientY - r.top - this.dy;
    if (x < 0 || y < 0 || x >= this.dw || y >= this.dh) return null;
    const px = Math.floor((x / this.dw) * this.bitmap.width);
    const py = Math.floor((y / this.dh) * this.bitmap.height);
    const bpp = (this.meta.meta.bytes_per_pixel as number)
      ?? bytesPerPixel(this.c.mode.value);
    const off = this.start + Math.floor((py * this.bitmap.width + px) * bpp);
    return Math.min(off, this.model.size - 1);
  }

  private hover(e: PointerEvent): void {
    const off = this.offsetAt(e.clientX, e.clientY);
    if (off === null || !this.model) { hideTooltip(); return; }
    const rows = [`<b>${fmtHex(off)}</b>`];
    const va = offToVa(this.model.mappings, off);
    if (va !== null) rows.push(`<span class="t2">VA</span> ${fmtHex(va)}`);
    const region = regionAtOff(this.model.regions, off);
    if (region) rows.push(`<span class="t2">${esc(region.name)}</span>`);
    showTooltip(e.clientX, e.clientY, rows.join("<br>"));
  }
}

function parseOffset(s: string): number {
  const n = Number(s.trim() || "0");   // Number() handles 0x… prefixes
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
}
