/* binviz web shell — Phase 7. Opens a binary (path, upload, or ?path=),
   polls analysis status, and wires the linked views around one
   SelectionStore. */

import {
  getConfig, getFiles, getFunctions, getModel, getSignals, getStatus,
  getTriage,
  openPath, openUpload,
  type BinaryModel, type FileEntry, type Status,
} from "./api.ts";
import { loginContext, needsLogin } from "./auth.ts";
import { append, el, replace } from "./dom.ts";
import { initHelp, isHelpOpen } from "./help.ts";
import { Router } from "./router.ts";
import { SelectionStore, fmtRange, fmtSize, type ElementDtype } from "./store.ts";
import type { DisplayMode } from "./transforms.ts";
import {
  applyWorkspace, clearFocus, DEFAULT_ROUTE, initPaneFocus, renderTabs,
  setActiveTab, workspaceByRoute, WORKSPACE_ROUTES, WORKSPACES,
} from "./workspace.ts";
import { CfgView } from "./views/cfg.ts";
import { DotPlotView } from "./views/dotplot.ts";
import { HexView } from "./views/hexview.ts";
import { Hist2DView } from "./views/hist2d.ts";
import { Hist3DView } from "./views/hist3d.ts";
import { ImageView } from "./views/image.ts";
import { InfoPanel } from "./views/info.ts";
import { showLogin } from "./views/login.ts";
import { OverallView, type OverallLayout, type OverallMode } from "./views/overall.ts";
import { PlotView } from "./views/plot.ts";
import { TriagePanel } from "./views/triage.ts";

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

/* ------------------------------------------------------------- state */

const store = new SelectionStore();
const overall = new OverallView(
  $("overall-canvas"), store, "file", $("overall-legend"));
const zoomed = new OverallView($("zoom-canvas"), store, "selection");
const plot = new PlotView($("plot-canvas"), store, $("plot-signals"));
const hex = new HexView($("hex-scroll"), $("hex-addr"), store);
const info = new InfoPanel($("model-info"), store);
const hist2d = new Hist2DView(
  $("hist2d-canvas"), store, $("hist2d-status"));
const hist3d = new Hist3DView($("hist3d-canvas"), store);
hist3d.onStats = (text) => { $("hist3d-status").textContent = text; };
const image = new ImageView($("image-canvas"), {
  mode: $("img-mode"), width: $("img-width"), offset: $("img-offset"),
  invert: $("img-invert"), suggest: $("img-suggest"),
  fitSel: $("img-fitsel"), status: $("img-status"),
}, store);
const dotplot = new DotPlotView($("dotplot-canvas"), {
  ax1: $("dot-ax1"), ax2: $("dot-ax2"), window: $("dot-window"),
  samples: $("dot-samples"), run: $("dot-run"), status: $("dot-status"),
}, store);
const cfg = new CfgView($("cfg-canvas"), {
  list: $("cfg-list"), banner: $("cfg-banner"), status: $("cfg-status"),
  search: $("cfg-search"), filterSel: $("cfg-filter-sel"),
}, store);
const triagePanel = new TriagePanel($("triage-info"), store);

let currentId = "";
let currentPath: string | null = null;   // absolute source path, null for uploads
let uploadName: string | null = null;    // name the user picked, uploads only
let pollTimer: number | undefined;
let modelLoaded = false;
let signalsLoaded = false;
let functionsLoaded = false;
let triageLoaded = false;

/* ----------------------------------------------------------- opening */

async function openBinary(kind: "path" | "upload", arg: string | ArrayBuffer,
                          name?: string) {
  try {
    setStatus("opening…");
    const { id } = kind === "path"
      ? await openPath(arg as string)
      : await openUpload(arg as ArrayBuffer);
    currentId = id;
    currentPath = kind === "path" ? (arg as string) : null;
    uploadName = kind === "upload" ? (name ?? null) : null;
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
    // No 404 special case any more: /status answers from the moment `open`
    // returns an id, so a 404 here now means what it says (§3.7).
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
    // name the running step, with its fractional progress on large files
    const running = Object.entries(arts).find(([, v]) => v === "running")?.[0];
    const frac = running ? st.progress?.[running] : undefined;
    const detail = running
      ? ` · ${running}${frac !== undefined ? ` ${Math.floor(frac * 100)}%` : ""}`
      : "";
    setStatus(`analyzing ${readyCount}/${total}${detail}…`);
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
  $("workspace-tabs").hidden = false;
  $("empty-state").hidden = true;
  // For an upload, source.path is the cache's own `file.bin`, which is not
  // what the user picked — show the name they chose instead.
  const name = uploadName
    ?? st.source?.path?.split(/[\\/]/).pop()
    ?? currentId.slice(0, 12);
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

/* ------------------------------------------------- workspaces (§3.4)

   The grid used to render all ten panes at once, with no way to focus,
   collapse or tab — and the work order is right that a sixth and seventh
   screen would make that materially worse, so this lands before them.

   The router owns the state: the URL names the workspace, Back works, and
   a deep link survives a hard refresh because both the packaged mount
   (§4.1) and Vite fall through to index.html. `/login` (§2.5) slots into
   the same router as a non-workspace route when it is built. */

const router = new Router(WORKSPACE_ROUTES, DEFAULT_ROUTE);

initHelp($("help-btn"));
initPaneFocus($("layout"));
renderTabs($("workspace-tabs"), (ws) => router.go(ws.route));

router.start((route) => {
  applyWorkspace($("layout"), workspaceByRoute(route));
  setActiveTab($("workspace-tabs"), route);
});

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
  // Escape is checked before the typing guard on purpose: it is not a text
  // character, and a maximised CFG pane has a search box in it — leaving the
  // only way out unreachable from the field you are typing in is a trap.
  // The help dialog closes itself on Escape (that is what <dialog> does), so
  // step aside rather than also dropping the user out of focus mode.
  if (e.key === "Escape") {
    if (!isHelpOpen()) clearFocus($("layout"));
    return;
  }
  const t = e.target as HTMLElement | null;
  const tag = t?.tagName ?? "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
      || t?.isContentEditable) return;   // typing somewhere
  if (e.key === "[") navTo(-1);
  else if (e.key === "]") navTo(+1);
  else if (e.key >= "1" && e.key <= "9") {
    const ws = WORKSPACES[Number(e.key) - 1];
    if (ws) router.go(ws.route);
  }
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
  replace($("recent-files"),
    ...recentList().map((p) => el("option", { value: p })));
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

/* ------------------------------------------------------- file picker

   The single worst first-run friction point was that the only way in was
   typing an absolute path (§3.1). The button has to behave *differently* in
   the two runtimes, and that difference is the whole subtlety:

   - Browser: `<input type="file">` deliberately never reveals an absolute
     path — you get a File and nothing else — so the bytes go up through the
     existing upload endpoint. The consequence is real and correct: an
     upload has no source path, so directory navigation ([ / ]) stays
     disabled for it.
   - Desktop: pywebview's native dialog returns a real absolute path, so it
     uses the path endpoint. Strictly better — no copy, no upload, and file
     navigation keeps working.

   Feature detection is `window.pywebview`, not a build flag, so one bundle
   serves both. Note this only *detects* a bridge; it does not create one.
   Adding a `js_api` bridge is gated on §2.4's checklist, because a bridge
   turns any surviving XSS into code execution. */

interface PywebviewBridge {
  api?: { pick_file?: () => Promise<string | string[] | null> };
}

function desktopBridge(): PywebviewBridge | undefined {
  return (window as unknown as { pywebview?: PywebviewBridge }).pywebview;
}

async function chooseFile(): Promise<void> {
  const bridge = desktopBridge();
  if (bridge?.api?.pick_file) {
    try {
      const picked = await bridge.api.pick_file();
      const path = Array.isArray(picked) ? picked[0] : picked;
      if (!path) return;                       // dialog cancelled
      ($("path-input") as HTMLInputElement).value = path;
      void openBinary("path", path);
    } catch (e) {
      setStatus(String((e as Error).message ?? e), true);
    }
    return;
  }
  ($("file-input") as HTMLInputElement).click();
}

$("choose-btn").addEventListener("click", () => void chooseFile());
$("file-input").addEventListener("change", async (e) => {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";        // so re-picking the same file fires change again
  if (!file) return;
  ($("path-input") as HTMLInputElement).value = "";
  await openBinary("upload", await file.arrayBuffer(), file.name);
});

/* ------------------------------------------------------ first run (§3.2)

   The empty state used to say only "Open a binary to begin", which is a dead
   end: nothing on screen said what the tool did, and the only documented way
   in was a path you had to already know. The sample list is whatever the
   served root actually contains, so it works for a corpus checkout and a
   `pip install` alike rather than hardcoding paths that exist on one
   machine. */

const SAMPLES_SHOWN = 6;

async function renderEmptyState(): Promise<void> {
  const host = $("empty-state");
  const intro = el("div", { class: "empty-intro" },
    el("h2", {}, "binviz"),
    el("p", {},
       "Linked views over one binary — entropy, byte structure, images, "
       + "self-similarity and control flow, all sharing a selection."),
    el("p", { class: "muted" },
       "Choose a file, drop one onto the window, or paste an absolute path."),
    el("button", { id: "empty-choose", class: "primary" }, "Choose file…"));
  replace(host, intro);
  host.querySelector("#empty-choose")!
      .addEventListener("click", () => void chooseFile());

  // ask the server where its root is rather than assuming "." — that
  // resolves against the server's cwd, which need not be --root, and the
  // mismatch 403s (found exactly that way)
  try {
    const cfg = await getConfig();
    const { dir, files } = await getFiles(cfg.root ?? ".");
    const pick = files.filter((f) => f.size > 0).slice(0, SAMPLES_SHOWN);
    if (!pick.length) return;
    append(intro,
      el("p", { class: "muted empty-samples-label" }, `or try one from ${dir}:`),
      el("div", { class: "empty-samples" },
         ...pick.map((f) => el("button", { class: "sample", title: f.path },
           `${f.name} · ${fmtSize(f.size)}`))));
    intro.querySelectorAll<HTMLElement>(".sample").forEach((b, i) => {
      b.addEventListener("click", () => {
        ($("path-input") as HTMLInputElement).value = pick[i].path;
        void openBinary("path", pick[i].path);
      });
    });
  } catch { /* root unreadable: the blurb and the button are enough */ }
}

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
  if (file) openBinary("upload", await file.arrayBuffer(), file.name);
});

/* ------------------------------------------------------------- boot

   In `local` auth mode (§2.2) the sign-in screen comes first and nothing
   that touches the API runs until it resolves. In every other mode this
   is a straight line: the token was injected into the page, or supplied
   by the dev proxy, or there is none because the server was started with
   --no-auth.

   The views above are constructed either way — they only build canvases —
   but they are given no binary and issue no request until here. */

async function boot(): Promise<void> {
  restoreViewCfg();   // after all control listeners are wired

  if (needsLogin()) {
    // Whether this sign-in sets the credential or checks it comes from the
    // same bootstrap meta as the mode itself — no extra round trip, and no
    // second unauthenticated endpoint to keep correct.
    document.title = "Sign in — Striate";
    await showLogin($("login-screen"), loginContext());
    document.title = "binviz";
  }

  void renderEmptyState();

  const urlPath = new URLSearchParams(location.search).get("path");
  if (urlPath) {
    ($("path-input") as HTMLInputElement).value = urlPath;
    void openBinary("path", urlPath);
  }
}

void boot();
