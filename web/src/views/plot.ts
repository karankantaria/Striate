/* Plot view — N named signals over file offset, stacked lanes. Each lane
   renders the server-side min/mean/max aggregation as a filled envelope
   plus a mean line, so a single-window spike is visually impossible to
   miss (the server bins at exactly the canvas width; the browser never
   downsamples). A region ribbon above shares the x-axis, and the whole
   plot participates in drag-select.

   Series colors are the validated categorical order, assigned per signal
   name in registry order — toggling a signal off never repaints the
   survivors. */

import { getSignal, type BinaryModel, type Region, type SignalBand, type SignalInfo } from "../api.ts";
import { LOCATE_RGB, SERIES } from "../colormap.ts";
import { html, joinHtml, replace, type SafeHtml } from "../dom.ts";
import { clearPaneError, paneError } from "../panestatus.ts";
import { fmtHex, type OffsetRange, type SelectionStore } from "../store.ts";
import { hideTooltip, showTooltip } from "../tooltip.ts";

const RIBBON_H = 18;
const AXIS_H = 20;
const LANE_GAP = 6;
const LANE_LABEL_H = 14;

interface Lane {
  info: SignalInfo;
  color: string;
  enabled: boolean;
  band: SignalBand | null;
}

export class PlotView {
  private host: HTMLElement;
  private canvas: HTMLCanvasElement;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  private lanes: Lane[] = [];
  follow = false;
  fitY = false;
  private fetchSeq = 0;
  private refetchTimer: number | undefined;
  private ro: ResizeObserver;
  private dragAnchor: number | null = null;
  private picksEl: HTMLElement | null;
  // x-range currently plotted
  private x0 = 0;
  private x1 = 0;

  constructor(
    host: HTMLElement, store: SelectionStore,
    picksEl: HTMLElement | null,
  ) {
    this.host = host;
    this.store = store;
    this.picksEl = picksEl;
    this.canvas = document.createElement("canvas");
    host.appendChild(this.canvas);
    this.ro = new ResizeObserver(() => this.debouncedRefetch());
    this.ro.observe(host);

    store.on("selection", () => {
      if (this.follow) this.debouncedRefetch();
      else this.draw();
    });
    store.on("hover", () => this.draw());
    store.on("locate", () => this.draw());

    this.canvas.addEventListener("pointerdown", (e) => {
      this.canvas.setPointerCapture(e.pointerId);
      const off = this.offsetAtClient(e.clientX);
      if (off === null) return;
      this.dragAnchor = off;
      this.store.setCaret(off);
    });
    this.canvas.addEventListener("pointermove", (e) => {
      const off = this.offsetAtClient(e.clientX);
      if (this.dragAnchor !== null && (e.buttons & 1) && off !== null) {
        hideTooltip();
        this.store.setSelection(off >= this.dragAnchor
          ? { start: this.dragAnchor, end: off + 1 }
          : { start: off, end: this.dragAnchor + 1 });
      } else if (off !== null) {
        this.store.setHover(off);
        this.tooltip(e.clientX, e.clientY, off);
      }
      this.draw();
    });
    this.canvas.addEventListener("pointerup", () => { this.dragAnchor = null; });
    this.canvas.addEventListener("pointerleave", () => {
      this.store.setHover(null);
      hideTooltip();
      this.draw();
    });
  }

  setBinary(id: string, model: BinaryModel, signals: SignalInfo[]): void {
    this.id = id;
    this.model = model;
    const defaults = new Set(["entropy_4096", "printable_ratio"]);
    this.lanes = signals.map((info) => ({
      info, color: "", enabled: defaults.has(info.name), band: null,
    }));
    this.assignColors();
    this.renderPicks();
    this.refetch();
  }

  private assignColors(): void {
    const palette = SERIES;
    this.lanes.forEach((lane, i) => {
      lane.color = palette[i % palette.length];
    });
  }

  setFollow(on: boolean): void { this.follow = on; this.refetch(); }
  setFitY(on: boolean): void { this.fitY = on; this.draw(); }

  toggleSignal(name: string, on: boolean): void {
    const lane = this.lanes.find((l) => l.info.name === name);
    if (lane) { lane.enabled = on; this.refetch(); }
  }

  private renderPicks(): void {
    if (!this.picksEl) return;
    replace(this.picksEl);
    for (const lane of this.lanes) {
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = lane.enabled;
      cb.addEventListener("change", () => this.toggleSignal(lane.info.name, cb.checked));
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.cssText =
        `display:inline-block;width:9px;height:9px;border-radius:3px;background:${lane.color}`;
      label.append(cb, swatch, lane.info.name);
      this.picksEl.appendChild(label);
    }
  }

  private range(): OffsetRange {
    const sel = this.store.state.offsetRange;
    if (this.follow && sel) return sel;
    return { start: 0, end: this.model?.size ?? 0 };
  }

  private debouncedRefetch(): void {
    window.clearTimeout(this.refetchTimer);
    this.refetchTimer = window.setTimeout(() => this.refetch(), 90);
  }

  async refetch(): Promise<void> {
    if (!this.id || !this.model) return;
    const w = this.host.clientWidth;
    if (w < 40) return;
    const seq = ++this.fetchSeq;
    const r = this.range();
    const n = Math.max(64, Math.min(w, 8192));
    const enabled = this.lanes.filter((l) => l.enabled);
    try {
      const bands = await Promise.all(enabled.map((l) =>
        getSignal(this.id, l.info.name, n, r.start, r.end)));
      if (seq !== this.fetchSeq) return;
      enabled.forEach((l, i) => { l.band = bands[i]; });
      this.x0 = r.start; this.x1 = r.end;
      clearPaneError(this.host);
    } catch (e) {
      paneError(this.host, "could not load signals", e,
                () => this.refetch());
      return;
    }
    this.draw();
  }

  /* ---------------------------------------------------- geometry */

  private plotTop(): number { return RIBBON_H + 4; }

  private laneRects(): { lane: Lane; y: number; h: number }[] {
    const enabled = this.lanes.filter((l) => l.enabled);
    const availH = this.host.clientHeight - this.plotTop() - AXIS_H;
    if (!enabled.length || availH < 20) return [];
    const laneH = (availH - LANE_GAP * (enabled.length - 1)) / enabled.length;
    return enabled.map((lane, i) => ({
      lane, y: this.plotTop() + i * (laneH + LANE_GAP), h: laneH,
    }));
  }

  private offsetAtClient(clientX: number): number | null {
    if (this.x1 <= this.x0) return null;
    const rect = this.canvas.getBoundingClientRect();
    const t = (clientX - rect.left) / rect.width;
    if (t < 0 || t > 1) return null;
    return Math.min(this.x1 - 1,
      Math.floor(this.x0 + t * (this.x1 - this.x0)));
  }

  private xOfOffset(off: number, w: number): number {
    return ((off - this.x0) / (this.x1 - this.x0)) * w;
  }

  /* ------------------------------------------------------ drawing */

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
    if (!this.model || this.x1 <= this.x0) return;

    const css = getComputedStyle(document.documentElement);
    const ink2 = css.getPropertyValue("--ink-2").trim();
    const muted = css.getPropertyValue("--muted").trim();
    const grid = css.getPropertyValue("--grid").trim();
    const selFill = css.getPropertyValue("--select-fill").trim();
    const selEdge = css.getPropertyValue("--select-edge").trim();

    this.drawRibbon(ctx, w);
    this.drawLocate(ctx, w, h);

    for (const { lane, y, h: lh } of this.laneRects()) {
      const band = lane.band;
      const plotY = y + LANE_LABEL_H;
      const plotH = lh - LANE_LABEL_H;
      // recessive frame
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.strokeRect(0.5, plotY + 0.5, w - 1, plotH - 1);
      // direct label: name + unit, in ink (text never wears series color)
      ctx.fillStyle = ink2;
      ctx.font = "11px system-ui, sans-serif";
      ctx.fillText(`${lane.info.name} (${lane.info.unit})`, 4, y + 10);
      if (!band) continue;

      let lo = band.meta.lo, hi = band.meta.hi;
      if (this.fitY) {
        lo = Infinity; hi = -Infinity;
        for (let i = 0; i < band.min.length; i++) {
          if (band.min[i] < lo) lo = band.min[i];
          if (band.max[i] > hi) hi = band.max[i];
        }
        if (!(hi > lo)) { lo = band.meta.lo; hi = band.meta.hi; }
      }
      const n = band.mean.length;
      const yOf = (v: number) =>
        plotY + plotH - ((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * plotH;
      const xOf = (i: number) => ((i + 0.5) / n) * w;

      // min/max envelope — the spike-preserving band
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const x = xOf(i), yv = yOf(band.max[i]);
        i ? ctx.lineTo(x, yv) : ctx.moveTo(x, yv);
      }
      for (let i = n - 1; i >= 0; i--) ctx.lineTo(xOf(i), yOf(band.min[i]));
      ctx.closePath();
      ctx.fillStyle = lane.color + "3d";   // ~24% alpha envelope
      ctx.fill();

      // mean line, 2px
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const x = xOf(i), yv = yOf(band.mean[i]);
        i ? ctx.lineTo(x, yv) : ctx.moveTo(x, yv);
      }
      ctx.strokeStyle = lane.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.stroke();

      // y extremes, tabular, muted
      ctx.fillStyle = muted;
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(fmtSig(hi), w - 3, plotY + 9);
      ctx.fillText(fmtSig(lo), w - 3, plotY + plotH - 3);
      ctx.textAlign = "left";
    }

    this.drawAxis(ctx, w, h, muted);

    // selection band (full plot height) + crosshair
    const sel = this.store.state.offsetRange;
    if (sel && sel.end > this.x0 && sel.start < this.x1) {
      const xa = this.xOfOffset(Math.max(sel.start, this.x0), w);
      const xb = this.xOfOffset(Math.min(sel.end, this.x1), w);
      ctx.fillStyle = selFill;
      ctx.fillRect(xa, this.plotTop(), Math.max(xb - xa, 1), h - this.plotTop() - AXIS_H);
      ctx.fillStyle = selEdge;
      ctx.fillRect(xa - 0.5, this.plotTop(), 1, h - this.plotTop() - AXIS_H);
      ctx.fillRect(xb - 0.5, this.plotTop(), 1, h - this.plotTop() - AXIS_H);
    }
    const hov = this.store.state.hoveredOffset;
    if (hov !== null && hov >= this.x0 && hov < this.x1) {
      const x = this.xOfOffset(hov, w);
      ctx.strokeStyle = muted;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x + 0.5, this.plotTop());
      ctx.lineTo(x + 0.5, h - AXIS_H);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  /** Brush-to-locate density from the bigram view: vertical tint columns
      wherever the brushed byte pairs occur, alpha driven by density. */
  private drawLocate(
    ctx: CanvasRenderingContext2D, w: number, h: number,
  ): void {
    const loc = this.store.state.locate;
    if (!loc || loc.max === 0 || this.x1 <= this.x0) return;
    if (loc.end <= this.x0 || loc.start >= this.x1) return;
    const rgb = LOCATE_RGB;
    const n = loc.density.length;
    const span = loc.end - loc.start;
    const top = this.plotTop(), bot = h - AXIS_H;
    let i = 0;
    while (i < n) {                       // runs of adjacent non-empty bins
      if (loc.density[i] === 0) { i++; continue; }
      let j = i, dmax = 0;
      while (j < n && loc.density[j] > 0) {
        if (loc.density[j] > dmax) dmax = loc.density[j];
        j++;
      }
      const o0 = Math.max(loc.start + Math.floor((i * span) / n), this.x0);
      const o1 = Math.min(loc.start + Math.ceil((j * span) / n), this.x1);
      if (o1 > o0) {
        const xa = this.xOfOffset(o0, w);
        const xb = this.xOfOffset(o1, w);
        const alpha = 0.15 + 0.4 * (dmax / loc.max);
        ctx.fillStyle = `rgba(${rgb},${alpha.toFixed(3)})`;
        ctx.fillRect(xa, top, Math.max(xb - xa, 1), bot - top);
      }
      i = j;
    }
  }

  private drawRibbon(ctx: CanvasRenderingContext2D, w: number): void {
    if (!this.model) return;
    const css = getComputedStyle(document.documentElement);
    const baseline = css.getPropertyValue("--baseline").trim();
    for (const r of this.model.regions) {
      if (r.file_off < 0 || r.file_size <= 0) continue;
      if (r.file_off + r.file_size <= this.x0 || r.file_off >= this.x1) continue;
      const xa = this.xOfOffset(Math.max(r.file_off, this.x0), w);
      const xb = this.xOfOffset(Math.min(r.file_off + r.file_size, this.x1), w);
      ctx.fillStyle = regionColor(r);
      ctx.fillRect(xa, 2, Math.max(xb - xa - 1, 1), RIBBON_H - 4);
      if (xb - xa > 46) {
        ctx.fillStyle = css.getPropertyValue("--cream").trim();
        ctx.font = "10px system-ui, sans-serif";
        ctx.fillText(r.name, xa + 3, RIBBON_H - 6, xb - xa - 6);
      }
    }
    ctx.fillStyle = baseline;
    ctx.fillRect(0, RIBBON_H - 1, w, 1);
  }

  private drawAxis(
    ctx: CanvasRenderingContext2D, w: number, h: number, muted: string,
  ): void {
    ctx.fillStyle = muted;
    ctx.font = "10px ui-monospace, monospace";
    const nTicks = Math.max(2, Math.floor(w / 110));
    for (let i = 0; i <= nTicks; i++) {
      const off = Math.floor(this.x0 + (i / nTicks) * (this.x1 - this.x0));
      const x = this.xOfOffset(off, w);
      const label = fmtHex(off);
      const align = i === 0 ? "left" : i === nTicks ? "right" : "center";
      ctx.textAlign = align as CanvasTextAlign;
      ctx.fillText(label, x + (align === "left" ? 2 : align === "right" ? -2 : 0),
                   h - 6);
    }
    ctx.textAlign = "left";
  }

  private tooltip(clientX: number, clientY: number, off: number): void {
    const rows: SafeHtml[] = [html`<b>${fmtHex(off)}</b>`];
    const region = this.model?.regions.find((r) =>
      r.file_off >= 0 && off >= r.file_off && off < r.file_off + r.file_size);
    if (region) {
      rows.push(html`<span class="t2">${region.name} (${region.kind})</span>`);
    }
    for (const lane of this.lanes) {
      if (!lane.enabled || !lane.band) continue;
      const b = lane.band;
      const n = b.mean.length;
      const i = Math.max(0, Math.min(n - 1,
        Math.floor(((off - this.x0) / (this.x1 - this.x0)) * n)));
      rows.push(html`<span class="t2">${lane.info.name}:</span> ${fmtSig(b.mean[i])} <span class="t2">(${fmtSig(b.min[i])}–${fmtSig(b.max[i])})</span>`);
    }
    showTooltip(clientX, clientY, joinHtml(rows, "<br>"));
  }
}

/* Region ribbon fills, stepped against --panel like everything else.
   Alpha carries a second signal on purpose: executable reads strongest,
   writable next, read-only faintest, so the ribbon is legible as a shape
   before any colour is resolved. Overlay is the accent because an overlay
   is the finding an analyst is looking for. */
function regionColor(r: Region): string {
  if (r.kind === "overlay") return "#ec5b3880";     // attention: the accent
  if (r.kind === "gap") return "#a8a49240";         // sage, barely there
  if (r.kind === "header") return "#a8a49266";
  if (r.perms.includes("x")) return "#2a97f780";    // exec
  if (r.perms.includes("w")) return "#25ae5660";    // data
  return "#2a97f740";                               // ro
}

function fmtSig(v: number): string {
  if (!isFinite(v)) return "–";
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(3);
}
