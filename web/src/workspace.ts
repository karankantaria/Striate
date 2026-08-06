/* Workspaces — the navigation layer over the pane grid (§3.4).

   The problem this solves: `theme.css` rendered **ten panes at once** in a
   five-row grid with no way to focus, collapse or tab, and the work order is
   right that adding a sixth and seventh row makes it materially worse. The
   fix has to land *before* the new screens, because retrofitting navigation
   onto fifteen always-visible panes is a far bigger job than onto ten.

   A workspace is a named set of panes with its own grid, addressed by a
   route. Grouping is by **the question the analyst is asking**, not by
   implementation kinship:

     Overview  where is the interesting part of this file
     Bytes     read the bytes here, with the map for context
     Patterns  what is the structure/encoding of this region
     Code      what does the code do
     All       the original ten-pane grid, kept for large displays

   Three things worth knowing before changing this:

   1. **A pane belongs to as many workspaces as make sense.** `zoom` and
      `side` recur because they are context, not content — the selection
      readout and the triage findings are what make the other panes
      interpretable. One DOM element, a different `grid-area` per workspace.

   2. **Hiding is `display: none`, and that is load-bearing.** Every view
      already owns a ResizeObserver and already guards its draw path against
      a zero-sized host, so a hidden pane stops drawing and a re-shown one
      repaints itself with no help from here. Hiding by opacity or offscreen
      positioning would keep all ten drawing forever and lose the whole
      point.

   3. **Focus mode is deliberately not routed.** Which pane is maximised is
      ephemeral — it is a "look closer for a second" gesture, and putting it
      in the URL would make Back mean two different things depending on what
      you last did. Switching workspace clears it; Escape exits it. */

import { el, replace } from "./dom.ts";

export interface Workspace {
  /** Stable id — also the `data-workspace` value the CSS grids key off. */
  id: string;
  /** The route that selects it. */
  route: string;
  /** Tab text. */
  label: string;
  /** The question this workspace answers, shown as its tooltip. */
  hint: string;
  /** Grid-area names of the panes it shows; element id is `${area}-pane`. */
  panes: readonly string[];
}

/** Every pane in the grid, in DOM order. */
export const ALL_PANES = [
  "overall", "zoom", "plot", "side", "hex",
  "hist2d", "hist3d", "image", "dotplot", "cfg",
] as const;

export const WORKSPACES: readonly Workspace[] = [
  {
    id: "overview",
    route: "/",
    label: "Overview",
    hint: "where the interesting parts of this file are",
    panes: ["overall", "zoom", "plot", "side"],
  },
  {
    id: "bytes",
    route: "/bytes",
    label: "Bytes",
    hint: "read the bytes, with the map for context",
    panes: ["overall", "zoom", "hex", "side"],
  },
  {
    id: "patterns",
    route: "/patterns",
    label: "Patterns",
    hint: "structure and encoding of the selected region",
    panes: ["hist2d", "hist3d", "image", "dotplot", "zoom"],
  },
  {
    id: "code",
    route: "/code",
    label: "Code",
    hint: "disassembled functions and control flow",
    panes: ["cfg", "side"],
  },
  {
    id: "all",
    route: "/all",
    label: "All",
    hint: "every pane at once — the original layout, for large displays",
    panes: ALL_PANES,
  },
];

export const WORKSPACE_ROUTES: readonly string[] = WORKSPACES.map((w) => w.route);
export const DEFAULT_ROUTE = WORKSPACES[0].route;

export function workspaceByRoute(route: string): Workspace {
  return WORKSPACES.find((w) => w.route === route) ?? WORKSPACES[0];
}

/** Element id of a pane's grid area. */
export function paneElementId(area: string): string {
  return `${area}-pane`;
}

/* ------------------------------------------------------------------ DOM */

/** Show exactly the active workspace's panes and tag the grid so the CSS
    can pick the matching template. Clears focus mode: a maximised pane the
    new workspace does not contain would otherwise blank the screen. */
export function applyWorkspace(layout: HTMLElement, ws: Workspace): void {
  const shown = new Set(ws.panes);
  for (const area of ALL_PANES) {
    const pane = document.getElementById(paneElementId(area));
    if (pane) pane.hidden = !shown.has(area);
  }
  layout.dataset.workspace = ws.id;
  clearFocus(layout);
}

/** Build the tab bar, once. A real ARIA tablist with roving tabindex: one
    tab stop for the whole bar, arrows move between tabs, Home/End jump to
    the ends. §3.6 says to set the accessibility baseline in the new
    navigation rather than backfill it, and this is the app's primary
    navigation, so it starts correct.

    Built once and then only re-marked by `setActiveTab` — rebuilding the
    buttons on every route change would destroy the element the user is
    keyboard-navigating and drop focus back to the body mid-arrow-press. */
export function renderTabs(
  host: HTMLElement, onSelect: (ws: Workspace) => void,
): void {
  replace(host, ...WORKSPACES.map((ws) => {
    const tab = el("button", {
      type: "button",
      class: "wtab",
      id: `wtab-${ws.id}`,
      role: "tab",
      title: ws.hint,
      "aria-selected": "false",
      "aria-controls": "layout",
      tabindex: -1,
    }, ws.label);
    tab.addEventListener("click", () => onSelect(ws));
    return tab;
  }));

  host.addEventListener("keydown", (e) => {
    const tabs = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    const at = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    const to =
      e.key === "ArrowRight" ? (at + 1) % tabs.length
      : e.key === "ArrowLeft" ? (at - 1 + tabs.length) % tabs.length
      : e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1
      : -1;
    if (to < 0) return;
    e.preventDefault();
    tabs[to].focus();
    tabs[to].click();
  });
}

/** Mark which tab is current. Separate from `renderTabs` so the DOM the
    user is interacting with survives a route change. */
export function setActiveTab(host: HTMLElement, activeRoute: string): void {
  const active = workspaceByRoute(activeRoute);
  for (const ws of WORKSPACES) {
    const tab = host.querySelector<HTMLButtonElement>(`#wtab-${ws.id}`);
    if (!tab) continue;
    const selected = ws.id === active.id;
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
  }
  // the panel announces which tab describes it
  document.getElementById("layout")
    ?.setAttribute("aria-labelledby", `wtab-${active.id}`);
}

/* ----------------------------------------------------------- focus mode */

/** Add a maximise toggle to every pane header.

    Done from script rather than in `index.html` so the ten headers cannot
    drift apart, and so a new pane gets the affordance by existing. */
export function initPaneFocus(layout: HTMLElement): void {
  for (const area of ALL_PANES) {
    const pane = document.getElementById(paneElementId(area));
    const head = pane?.querySelector(".pane-head");
    if (!pane || !head) continue;
    const button = el("button", {
      type: "button",
      class: "pane-focus",
      title: "maximise this pane (Esc to exit)",
      "aria-label": "maximise this pane",
      "aria-pressed": "false",
    }, "⤢");
    button.addEventListener("click", () => toggleFocus(layout, area));
    head.appendChild(button);
  }
}

export function toggleFocus(layout: HTMLElement, area: string): void {
  if (layout.dataset.focus === area) clearFocus(layout);
  else setFocus(layout, area);
}

function setFocus(layout: HTMLElement, area: string): void {
  clearFocus(layout);
  const pane = document.getElementById(paneElementId(area));
  if (!pane || pane.hidden) return;
  pane.classList.add("focused");
  pane.querySelector(".pane-focus")?.setAttribute("aria-pressed", "true");
  layout.dataset.focus = area;
}

export function clearFocus(layout: HTMLElement): void {
  const area = layout.dataset.focus;
  if (!area) return;
  const pane = document.getElementById(paneElementId(area));
  pane?.classList.remove("focused");
  pane?.querySelector(".pane-focus")?.setAttribute("aria-pressed", "false");
  delete layout.dataset.focus;
}
