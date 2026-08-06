/* binviz web shell — Phase 7. Opens a binary (path, upload, or ?path=),
   polls analysis status, and wires the linked views around one
   SelectionStore. */

import {
  getFiles, getFunctions, getModel, getSignals, getStatus, getTriage,
  openPath, openUpload,
  type BinaryModel, type FileEntry, type Status,
} from "./api.ts";
import type { Theme } from "./colormap.ts";
import { SelectionStore, fmtRange, type ElementDtype } from "./store.ts";
import type { DisplayMode } from "./transforms.ts";
import { CfgView } from "./views/cfg.ts";
import { DotPlotView } from "./views/dotplot.ts";
import { HexView } from "./views/hexview.ts";
import { Hist2DView } from "./views/hist2d.ts";
import { Hist3DView } from "./views/hist3d.ts";
import { ImageView } from "./views/image.ts";
import { InfoPanel } from "./views/info.ts";
import { OverallView, type OverallLayout, type OverallMode } from "./views/overall.ts";
import { PlotView } from "./views/plot.ts";
import { TriagePanel } from "./views/triage.ts";

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

/* ------------------------------------------------------------- theme */

function initialTheme(): Theme {
  const saved = localStorage.getItem("binviz-theme");
  if (saved === "light" || saved === "dark") return saved;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

let theme: Theme = initialTheme();
document.documentElement.dataset.theme = theme;

/* ------------------------------------------------------------- state */

const store = new SelectionStore();
const overall = new OverallView(
  $("overall-canvas"), store, theme, "file", $("overall-legend"));
const zoomed = new OverallView($("zoom-canvas"), store, theme, "selection");
const plot = new PlotView($("plot-canvas"), store, theme, $("plot-signals"));
const hex = new HexView($("hex-scroll"), $("hex-addr"), store);
const info = new InfoPanel($("model-info"), store);
const hist2d = new Hist2DView(
  $("hist2d-canvas"), store, theme, $("hist2d-status"));
const hist3d = new Hist3DView($("hist3d-canvas"), store, theme);
hist3d.onStats = (text) => { $("hist3d-status").textContent = text; };
const image = new ImageView($("image-canvas"), {
  mode: $("img-mode"), width: $("img-width"), offset: $("img-offset"),
  invert: $("img-invert"), suggest: $("img-suggest"),
  fitSel: $("img-fitsel"), status: $("img-status"),
}, store);
const dotplot = new DotPlotView($("dotplot-canvas"), {
  ax1: $("dot-ax1"), ax2: $("dot-ax2"), window: $("dot-window"),
  samples: $("dot-samples"), run: $("dot-run"), status: $("dot-status"),
}, store, theme);
const cfg = new CfgView($("cfg-canvas"), {
  list: $("cfg-list"), banner: $("cfg-banner"), status: $("cfg-status"),
  search: $("cfg-search"), filterSel: $("cfg-filter-sel"),
}, store, theme);
const triagePanel = new TriagePanel($("triage-info"), store);

let currentId = "";
let currentPath: string | null = null;   // absolute source path, null for uploads
let pollTimer: number | undefined;
let modelLoaded = false;
let signalsLoaded = false;
let functionsLoaded = false;
let triageLoaded = false;

/* ----------------------------------------------------------- opening */

async function openBinary(kind: "path" | "upload", arg: string | ArrayBuffer) {
  try {
    setStatus("opening…");
    const { id } = kind === "path"
      ? await openPath(arg as string)
      : await openUpload(arg as ArrayBuffer);
    currentId = id;
    currentPath = kind === "path" ? (arg as string) : null;
    modelLoaded = signalsLoaded = functionsLoaded = triageLoaded = false;
    // selection resets; view configuration (dtype, layouts, modes)
    // survives the file switch — see store.setModel
    store.setModel(null);
    // setModel resets silently; broadcast the clears so views bound to
    // selection/caret/locate drop the previous file's state
    store.setSelection(null);
    store.setCaret(null);
    store.setLocate(null);
    triagePanel.clear();
    if (kind === "path") addRecent(arg as string);
    updateNav();
    poll();
  } catch (e) {
    setStatus(String((e as Error).message ?? e), true);
  }
}

async function poll(): Promise<void> {
  window.clearTimeout(pollTimer);
  if (!currentId) return;
  let st: Status;
  try {
    st = await getStatus(currentId);
  } catch (e) {
    const status = (e as { status?: number }).status;
    if (status === 404) {           // analysis thread hasn't written meta yet
      pollTimer = window.setTimeout(poll, 300);
      return;
    }
    setStatus(String((e as Error).message ?? e), true);
    return;
  }
  const arts = st.artifacts ?? {};
  const readyCount = Object.values(arts).filter((v) => v === "ready").length;
  const total = Object.keys(arts).length || 1;

  if (!modelLoaded && arts.model === "ready") {
    modelLoaded = true;
    await onModelReady(st);
  }
  if (modelLoaded && !signalsLoaded && arts.signals === "ready") {
    signalsLoaded = true;
    await onSignalsReady();
  }
  if (modelLoaded && !functionsLoaded && arts.functions === "ready") {
    functionsLoaded = true;
    cfg.setFunctions(await getFunctions(currentId));
  }
  if (modelLoaded && !triageLoaded && arts.triage === "ready") {
    triageLoaded = true;
    triagePanel.set(await getTriage(currentId));
  }

  if (st.state === "complete") {
    setStatus("ready");
    overall.refetch();   // pick up anything a mid-analysis fetch missed
    zoomed.refetch();
  } else if (st.state === "error") {
    setStatus(`analysis error: ${st.error ?? "?"}`, true);
  } else {
    setStatus(`analyzing ${readyCount}/${total}…`);
    pollTimer = window.setTimeout(poll, 500);
  }
}

async function onModelReady(st: Status): Promise<void> {
  const model: BinaryModel = await getModel(currentId);
  store.setModel(model);
  if (st.source && !st.source.stored && st.source.path) {
    currentPath = st.source.path;   // server-side abspath, matches /api/files
    updateNav();
  }
  $("layout").hidden = false;
  $("empty-state").hidden = true;
  const name = st.source?.path?.split(/[\\/]/).pop() ?? currentId.slice(0, 12);
  $("file-label").textContent = `${name} · ${model.format} · ${model.arch}`;
  document.title = `binviz — ${name}`;
  overall.setBinary(currentId, model);
  zoomed.setBinary(currentId, model);
  hex.setBinary(currentId, model);
  info.setBinary(model);
  hist2d.setBinary(currentId, model);
  hist3d.setBinary(currentId, model);
  image.setBinary(currentId, model);
  dotplot.setBinary(currentId, model);
  cfg.setBinary(currentId, model);
}

async function onSignalsReady(): Promise<void> {
  const model = await getModel(currentId);
  const signals = await getSignals(currentId);
  plot.setBinary(currentId, model, signals);
}

function setStatus(text: string, err = false): void {
  const chip = $("status-chip");
  chip.hidden = false;
  chip.textContent = text;
  chip.classList.toggle("err", err);
}

/* --------------------------------------- file navigation (Phase 11)
   Prev/next through the open file's directory with the current lens
   (dtype, layouts, modes) held fixed — flipping through a pile of
   firmware blobs looking for the odd one is the actual workflow. */

let siblings: FileEntry[] = [];
let navIndex = -1;

function dirnameOf(path: string): string {
  const i = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return i > 0 ? path.slice(0, i) : path;
}

async function updateNav(): Promise<void> {
  siblings = [];
  navIndex = -1;
  if (currentPath) {
    try {
      const { files } = await getFiles(dirnameOf(currentPath));
      siblings = files;
      const want = currentPath.toLowerCase();
      navIndex = files.findIndex((f) => f.path.toLowerCase() === want);
    } catch { /* directory gone or denied: nav stays disabled */ }
  }
  const prev = $("nav-prev") as HTMLButtonElement;
  const next = $("nav-next") as HTMLButtonElement;
  prev.disabled = navIndex <= 0;
  next.disabled = navIndex < 0 || navIndex >= siblings.length - 1;
  const pos = $("nav-pos");
  pos.hidden = navIndex < 0;
  if (navIndex >= 0) pos.textContent = `${navIndex + 1}/${siblings.length}`;
}

function navTo(delta: number): void {
  if (navIndex < 0) return;
  const target = siblings[navIndex + delta];
  if (!target) return;
  ($("path-input") as HTMLInputElement).value = target.path;
  openBinary("path", target.path);
}

$("nav-prev").addEventListener("click", () => navTo(-1));
$("nav-next").addEventListener("click", () => navTo(+1));
document.addEventListener("keydown", (e) => {
  const t = e.target as HTMLElement | null;
  const tag = t?.tagName ?? "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
      || t?.isContentEditable) return;   // typing somewhere
  if (e.key === "[") navTo(-1);
  else if (e.key === "]") navTo(+1);
});

/* ------------------------------------------------------ recent files */

const RECENT_KEY = "binviz-recent";
const RECENT_MAX = 10;

function recentList(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch { return []; }
}

function addRecent(path: string): void {
  const list = [path, ...recentList().filter((p) => p !== path)]
    .slice(0, RECENT_MAX);
  localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  renderRecent();
}

function renderRecent(): void {
  $("recent-files").innerHTML = recentList()
    .map((p) => `<option value="${p.replace(/&/g, "&amp;").replace(/"/g, "&quot;")}"></option>`)
    .join("");
}
renderRecent();

/* -------------------------------------- view configuration persistence
   The lens survives reloads too: layout/mode/display/dtype are restored
   from localStorage at boot and saved on every change. */

const CFG_KEY = "binviz-viewcfg";

function saveViewCfg(): void {
  localStorage.setItem(CFG_KEY, JSON.stringify({
    layout: ($("overall-layout") as HTMLSelectElement).value,
    mode: ($("overall-mode") as HTMLSelectElement).value,
    display: ($("hist2d-display") as HTMLSelectElement).value,
    dtype: ($("dtype-select") as HTMLSelectElement).value,
  }));
}

function restoreViewCfg(): void {
  let cfg: Record<string, string>;
  try {
    cfg = JSON.parse(localStorage.getItem(CFG_KEY) ?? "{}");
  } catch { return; }
  const apply = (id: string, v: string | undefined) => {
    const sel = $(id) as HTMLSelectElement;
    if (v && [...sel.options].some((o) => o.value === v) && sel.value !== v) {
      sel.value = v;
      sel.dispatchEvent(new Event("change"));
    }
  };
  apply("overall-layout", cfg.layout);
  apply("overall-mode", cfg.mode);
  apply("hist2d-display", cfg.display);
  apply("dtype-select", cfg.dtype);
}

/* ---------------------------------------------------------- controls */

$("open-btn").addEventListener("click", () => {
  const path = ($("path-input") as HTMLInputElement).value.trim();
  if (path) openBinary("path", path);
});
$("path-input").addEventListener("keydown", (e) => {
  if ((e as KeyboardEvent).key === "Enter") {
    const path = ($("path-input") as HTMLInputElement).value.trim();
    if (path) openBinary("path", path);
  }
});

$("theme-btn").addEventListener("click", () => {
  theme = theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("binviz-theme", theme);
  store.setTheme(theme);
});

$("overall-layout").addEventListener("change", () => {
  const sel = $("overall-layout") as HTMLSelectElement;
  const modeSel = $("overall-mode") as HTMLSelectElement;
  const layout = sel.value as OverallLayout;
  // the backend's hilbert surface has no signal mode — restrict the picker
  const entropyOpt = modeSel.querySelector<HTMLOptionElement>('option[value="signal"]')!;
  entropyOpt.disabled = layout === "hilbert";
  if (layout === "hilbert" && modeSel.value === "signal") {
    modeSel.value = "byteclass";
  }
  overall.setLayout(layout);
  zoomed.setLayout(layout);
  saveViewCfg();
});

$("overall-mode").addEventListener("change", () => {
  const mode = ($("overall-mode") as HTMLSelectElement).value as OverallMode;
  overall.setMode(mode);
  zoomed.setMode(mode);
  saveViewCfg();
});

$("zoom-clear").addEventListener("click", () => store.setSelection(null));

$("plot-follow").addEventListener("change", () => {
  plot.setFollow(($("plot-follow") as HTMLInputElement).checked);
});

$("hist2d-display").addEventListener("change", () => {
  hist2d.setDisplay(($("hist2d-display") as HTMLSelectElement).value as DisplayMode);
  saveViewCfg();
});
$("dtype-select").addEventListener("change", () => {
  store.setDtype(($("dtype-select") as HTMLSelectElement).value as ElementDtype);
  saveViewCfg();
});
$("locate-clear").addEventListener("click", () => hist2d.clearBrush());

$("hist3d-threshold").addEventListener("change", () => {
  const v = parseInt(($("hist3d-threshold") as HTMLInputElement).value, 10);
  hist3d.setThreshold(Number.isFinite(v) ? v : 1);
});
$("hist3d-scale").addEventListener("input", () => {
  hist3d.setScale(parseFloat(($("hist3d-scale") as HTMLInputElement).value));
});
$("hist3d-overlap").addEventListener("change", () => {
  hist3d.setOverlap(($("hist3d-overlap") as HTMLInputElement).checked);
});
$("hist3d-spin").addEventListener("change", () => {
  hist3d.setSpin(($("hist3d-spin") as HTMLInputElement).checked);
});

$("cfg-fit").addEventListener("click", () => cfg.fit());

store.on("selection", (sel) => {
  $("zoom-range").textContent = sel ? fmtRange(sel) : "no selection";
});

/* --------------------------------------------------------- drag-drop */

document.body.addEventListener("dragover", (e) => {
  e.preventDefault();
  document.body.classList.add("dragover");
});
document.body.addEventListener("dragleave", (e) => {
  if (e.target === document.body) document.body.classList.remove("dragover");
});
document.body.addEventListener("drop", async (e) => {
  e.preventDefault();
  document.body.classList.remove("dragover");
  const file = e.dataTransfer?.files?.[0];
  if (file) openBinary("upload", await file.arrayBuffer());
});

/* ------------------------------------------------------------- boot */

restoreViewCfg();   // after all control listeners are wired

const urlPath = new URLSearchParams(location.search).get("path");
if (urlPath) {
  ($("path-input") as HTMLInputElement).value = urlPath;
  openBinary("path", urlPath);
}
