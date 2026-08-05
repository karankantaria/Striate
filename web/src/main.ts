/* binviz web shell — Phase 7. Opens a binary (path, upload, or ?path=),
   polls analysis status, and wires the linked views around one
   SelectionStore. */

import {
  getModel, getSignals, getStatus, openPath, openUpload,
  type BinaryModel, type Status,
} from "./api.ts";
import type { Theme } from "./colormap.ts";
import { SelectionStore, fmtRange } from "./store.ts";
import { HexPeek } from "./views/hex.ts";
import { InfoPanel } from "./views/info.ts";
import { OverallView, type OverallLayout, type OverallMode } from "./views/overall.ts";
import { PlotView } from "./views/plot.ts";

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
const hex = new HexPeek($("hex-dump"), $("hex-addr"), store);
const info = new InfoPanel($("model-info"), store);

let currentId = "";
let pollTimer: number | undefined;
let modelLoaded = false;
let signalsLoaded = false;

/* ----------------------------------------------------------- opening */

async function openBinary(kind: "path" | "upload", arg: string | ArrayBuffer) {
  try {
    setStatus("opening…");
    const { id } = kind === "path"
      ? await openPath(arg as string)
      : await openUpload(arg as ArrayBuffer);
    currentId = id;
    modelLoaded = signalsLoaded = false;
    store.setModel(null);
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
  $("layout").hidden = false;
  $("empty-state").hidden = true;
  const name = st.source?.path?.split(/[\\/]/).pop() ?? currentId.slice(0, 12);
  $("file-label").textContent = `${name} · ${model.format} · ${model.arch}`;
  document.title = `binviz — ${name}`;
  overall.setBinary(currentId, model);
  zoomed.setBinary(currentId, model);
  hex.setBinary(currentId, model);
  info.setBinary(model);
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
});

$("overall-mode").addEventListener("change", () => {
  const mode = ($("overall-mode") as HTMLSelectElement).value as OverallMode;
  overall.setMode(mode);
  zoomed.setMode(mode);
});

$("zoom-clear").addEventListener("click", () => store.setSelection(null));

$("plot-follow").addEventListener("change", () => {
  plot.setFollow(($("plot-follow") as HTMLInputElement).checked);
});

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

const urlPath = new URLSearchParams(location.search).get("path");
if (urlPath) {
  ($("path-input") as HTMLInputElement).value = urlPath;
  openBinary("path", urlPath);
}
