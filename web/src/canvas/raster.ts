/* The one raster canvas component every pixel view reuses — the frontend
   counterpart of the Surface protocol. It owns a raster canvas plus an
   overlay canvas (markers/hover draw without re-painting the raster),
   applies a colormap LUT client-side, reports pointer positions in raster
   cell coordinates, and notifies on resize so the owner can refetch at the
   new size. */

import { applyLut, type Lut } from "../colormap.ts";

export interface CellPointerEvent {
  type: "down" | "move" | "up" | "leave";
  cellX: number; cellY: number;    // raster-space, clamped to bounds
  inside: boolean;
  buttons: number;
  clientX: number; clientY: number;
}

export interface RasterCanvasOptions {
  /** "stretch": raster fills the host 1:1-ish (linear surfaces are
      requested at host size). "square": raster is a square scaled to fit,
      centered, pixelated (hilbert). */
  fit: "stretch" | "square";
  onPointer?: (ev: CellPointerEvent) => void;
  onResize?: (cssW: number, cssH: number) => void;
  drawOverlay?: (ctx: CanvasRenderingContext2D, view: RasterCanvas) => void;
}

export class RasterCanvas {
  readonly host: HTMLElement;
  private raster: HTMLCanvasElement;
  private overlay: HTMLCanvasElement;
  private opts: RasterCanvasOptions;
  private pixels: Uint8Array | null = null;
  private lut: Lut | null = null;
  w = 0; h = 0;                     // raster dimensions
  cssW = 0; cssH = 0;               // host size
  // placement of the raster inside the host (CSS px)
  drawX = 0; drawY = 0; drawW = 0; drawH = 0;
  private ro: ResizeObserver;
  private resizeTimer: number | undefined;

  constructor(host: HTMLElement, opts: RasterCanvasOptions) {
    this.host = host;
    this.opts = opts;
    this.raster = document.createElement("canvas");
    this.overlay = document.createElement("canvas");
    this.overlay.style.zIndex = "1";
    host.appendChild(this.raster);
    host.appendChild(this.overlay);

    this.ro = new ResizeObserver(() => {
      window.clearTimeout(this.resizeTimer);
      this.resizeTimer = window.setTimeout(() => this.handleResize(), 120);
    });
    this.ro.observe(host);
    this.handleResize(false);

    const fwd = (type: CellPointerEvent["type"]) => (e: PointerEvent) => {
      if (!this.opts.onPointer) return;
      const r = this.overlay.getBoundingClientRect();
      const px = e.clientX - r.left, py = e.clientY - r.top;
      const inside = px >= this.drawX && px < this.drawX + this.drawW &&
                     py >= this.drawY && py < this.drawY + this.drawH;
      const cellX = this.w
        ? Math.max(0, Math.min(this.w - 1,
            Math.floor((px - this.drawX) / this.drawW * this.w)))
        : 0;
      const cellY = this.h
        ? Math.max(0, Math.min(this.h - 1,
            Math.floor((py - this.drawY) / this.drawH * this.h)))
        : 0;
      this.opts.onPointer({
        type, cellX, cellY, inside, buttons: e.buttons,
        clientX: e.clientX, clientY: e.clientY,
      });
    };
    this.overlay.addEventListener("pointerdown", (e) => {
      this.overlay.setPointerCapture(e.pointerId);
      fwd("down")(e);
    });
    this.overlay.addEventListener("pointermove", fwd("move"));
    this.overlay.addEventListener("pointerup", fwd("up"));
    this.overlay.addEventListener("pointerleave", fwd("leave"));
  }

  destroy(): void {
    this.ro.disconnect();
    this.raster.remove();
    this.overlay.remove();
  }

  /** Pull the current host size from the DOM. RO delivery after an
      unhide is not guaranteed in order with our fetch path, so owners
      call this before deciding whether a size is usable. */
  sync(): void {
    const { clientWidth: w, clientHeight: h } = this.host;
    if (w !== this.cssW || h !== this.cssH) this.handleResize(false);
  }

  private handleResize(notify = true): void {
    const { clientWidth: w, clientHeight: h } = this.host;
    if (w === 0 || h === 0) return;
    const changed = w !== this.cssW || h !== this.cssH;
    this.cssW = w; this.cssH = h;
    for (const c of [this.raster, this.overlay]) {
      c.width = w * devicePixelRatio;
      c.height = h * devicePixelRatio;
      c.style.width = w + "px";
      c.style.height = h + "px";
    }
    this.redraw();
    if (changed && notify) this.opts.onResize?.(w, h);
  }

  setRaster(pixels: Uint8Array, w: number, h: number): void {
    this.pixels = pixels; this.w = w; this.h = h;
    this.redraw();
  }

  setLut(lut: Lut): void {
    this.lut = lut;
    this.redraw();
  }

  /** Raster cell -> CSS px (top-left corner of the cell). */
  cellToCss(cellX: number, cellY: number): [number, number] {
    return [
      this.drawX + (cellX / this.w) * this.drawW,
      this.drawY + (cellY / this.h) * this.drawH,
    ];
  }

  get cellCssW(): number { return this.drawW / this.w; }
  get cellCssH(): number { return this.drawH / this.h; }

  redraw(): void {
    if (!this.pixels || !this.lut || this.cssW === 0) return;
    const ctx = this.raster.getContext("2d")!;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, this.cssW, this.cssH);

    if (this.opts.fit === "square") {
      const side = Math.min(this.cssW, this.cssH);
      this.drawW = this.drawH = side;
      this.drawX = Math.floor((this.cssW - side) / 2);
      this.drawY = Math.floor((this.cssH - side) / 2);
    } else {
      this.drawX = this.drawY = 0;
      this.drawW = this.cssW; this.drawH = this.cssH;
    }

    const rgba = applyLut(this.pixels, this.lut);
    const img = new ImageData(rgba, this.w, this.h);
    // draw via a scratch canvas so we can scale with the proper smoothing
    const scratch = document.createElement("canvas");
    scratch.width = this.w; scratch.height = this.h;
    scratch.getContext("2d")!.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;   // pixels are data; never blur them
    ctx.drawImage(scratch, this.drawX, this.drawY, this.drawW, this.drawH);
    this.redrawOverlay();
  }

  redrawOverlay(): void {
    const ctx = this.overlay.getContext("2d")!;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, this.cssW, this.cssH);
    this.opts.drawOverlay?.(ctx, this);
  }
}
