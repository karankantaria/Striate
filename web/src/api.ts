/* Typed fetch layer over the binviz HTTP service (Phase 6 wire format).
   Bulk numeric payloads are raw little-endian typed arrays; metadata rides
   in the X-Meta response header as JSON. */

import { authHeaders } from "./auth.ts";

export interface Region {
  name: string;
  kind: "section" | "segment" | "header" | "overlay" | "gap";
  file_off: number;   // -1 when not file-backed (.bss)
  file_size: number;
  vaddr: number;      // -1 when not mapped
  vsize: number;
  perms: string;
  entropy: number | null;
}

export interface Symbol_ {
  name: string; va: number; size: number; kind: string; source: string;
}

export interface BinaryModel {
  path: string; sha256: string; size: number;
  format: string; arch: string; bits: number; endian: string;
  entry_va: number | null;
  regions: Region[];
  symbols: Symbol_[];
  imports: string[];
  exports: string[];
  arch_ranges: [number, number, string][];
  warnings: string[];
  mappings: [number, number, number][]; // [file_off, size, vaddr]
}

export interface Status {
  sha256: string; size: number;
  state: "running" | "complete" | "error" | null;
  error: string | null;
  artifacts: Record<string, string> | null; // model|signals|hist|trigram|functions
  tool_version: string | null;
  source: { path: string; stored: boolean } | null;
  progress: Record<string, number> | null;  // running artifact fraction (P12)
}

export interface SignalInfo {
  name: string; unit: string; window: number; stride: number;
  lo: number; hi: number; ready: boolean; windows: number | null;
}

export interface SignalBand {
  min: Float32Array; mean: Float32Array; max: Float32Array;
  meta: {
    name: string; unit: string; n: number; window: number; stride: number;
    lo: number; hi: number; start: number; end: number; windows: number;
  };
}

export interface SurfaceMeta {
  kind: "scalar" | "rgb";
  shape: number[];          // [h, w] for scalar
  surface: string;
  meta: Record<string, unknown> & { warnings?: string[] };
}

export interface ScalarRaster {
  pixels: Uint8Array; w: number; h: number; meta: SurfaceMeta;
}

/** All API GETs bypass the browser HTTP cache: analysis state changes
    under the same URL, and a cached mid-analysis error (404/410 are
    heuristically cacheable) would wedge the view forever. Every request
    also carries the session token — see auth.ts. */
function get(url: string): Promise<Response> {
  return fetch(url, { cache: "no-store", headers: authHeaders() });
}

async function ok(r: Response): Promise<Response> {
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const doc = await r.json();
      if (doc && typeof doc.detail === "string") detail = doc.detail;
    } catch { /* body wasn't JSON */ }
    if (r.status === 401) {
      // Worth spelling out: the natural guess is "the file is bad", and
      // the actual fix is somewhere else entirely.
      detail = "not authorised — reopen the URL that `binviz serve` " +
        "printed, or set BINVIZ_TOKEN before `npm run dev`";
    }
    const err = new Error(detail) as Error & { status: number };
    err.status = r.status;
    throw err;
  }
  return r;
}

function xmeta<T>(r: Response): T {
  return JSON.parse(r.headers.get("X-Meta") ?? "{}") as T;
}

export async function openPath(path: string): Promise<{ id: string; state: string }> {
  const r = await ok(await fetch("/api/open", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ path }),
  }));
  return r.json();
}

export async function openUpload(data: ArrayBuffer): Promise<{ id: string; state: string }> {
  const r = await ok(await fetch("/api/open", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/octet-stream" }),
    body: data,
  }));
  return r.json();
}

export async function getStatus(id: string): Promise<Status> {
  return (await ok(await get(`/api/${id}/status`))).json();
}

export async function getModel(id: string): Promise<BinaryModel> {
  return (await ok(await get(`/api/${id}/model`))).json();
}

export async function getSignals(id: string): Promise<SignalInfo[]> {
  const doc = await (await ok(await get(`/api/${id}/signals`))).json();
  return doc.signals;
}

export async function getSignal(
  id: string, name: string, n: number, start = 0, end = -1,
): Promise<SignalBand> {
  const r = await ok(await get(
    `/api/${id}/signal/${name}?n=${n}&start=${start}&end=${end}`));
  const meta = xmeta<SignalBand["meta"]>(r);
  const buf = await r.arrayBuffer();
  const all = new Float32Array(buf);
  const nn = meta.n;
  return {
    min: all.subarray(0, nn),
    mean: all.subarray(nn, 2 * nn),
    max: all.subarray(2 * nn, 3 * nn),
    meta,
  };
}

export async function getSurface(
  id: string, name: string,
  opts: { start?: number; end?: number; w?: number; h?: number; dtype?: string;
          [param: string]: string | number | boolean | undefined },
): Promise<ScalarRaster> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) {
    if (v !== undefined) q.set(k, String(v));
  }
  const r = await ok(await get(`/api/${id}/surface/${name}?${q}`));
  const meta = xmeta<SurfaceMeta>(r);
  if (meta.kind !== "scalar") {
    throw new Error(`surface ${name} returned ${meta.kind}; expected scalar`);
  }
  const pixels = new Uint8Array(await r.arrayBuffer());
  const [h, w] = meta.shape;
  return { pixels, w, h, meta };
}

/** RGB surfaces (image view) ship as PNG; decode to an ImageBitmap. */
export async function getSurfaceRgb(
  id: string, name: string,
  opts: { start?: number; end?: number; w?: number; h?: number; dtype?: string;
          [param: string]: string | number | boolean | undefined },
): Promise<{ bitmap: ImageBitmap; meta: SurfaceMeta }> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) {
    if (v !== undefined) q.set(k, String(v));
  }
  const r = await ok(await get(`/api/${id}/surface/${name}?${q}`));
  const meta = xmeta<SurfaceMeta>(r);
  if (meta.kind !== "rgb") {
    throw new Error(`surface ${name} returned ${meta.kind}; expected rgb`);
  }
  const bitmap = await createImageBitmap(await r.blob());
  return { bitmap, meta };
}

export interface StrideCandidate {
  bytes: number; pixels: number; exact: boolean;
  score: number; origin: string;
}

/** Autocorrelation stride suggester for the image view (§5.7). */
export async function getStrideSuggestions(
  id: string, mode: string, start = 0, end = -1, top = 3,
): Promise<StrideCandidate[]> {
  const r = await ok(await get(
    `/api/${id}/image/stride?mode=${encodeURIComponent(mode)}` +
    `&start=${start}&end=${end}&top=${top}`));
  const doc = await r.json();
  return doc.candidates as StrideCandidate[];
}

export interface QuantiseMeta {
  dtype: string; n: number; lo: number; hi: number; method: string;
  n_nonfinite?: number;
}

export interface Hist2Meta {
  n: number; dtype: string; start: number; end: number;
  quantise: QuantiseMeta | { method: string };
}

/** 256×256 bigram counts, flat C-order [first*256 + second]. */
export async function getHist2(
  id: string, dtype = "u8", start = 0, end = -1,
): Promise<{ counts: Uint32Array; meta: Hist2Meta }> {
  const r = await ok(await get(
    `/api/${id}/hist?n=2&dtype=${dtype}&start=${start}&end=${end}`));
  const meta = xmeta<Hist2Meta>(r);
  return { counts: new Uint32Array(await r.arrayBuffer()), meta };
}

export interface Hist3Meta {
  points: number; total_points: number; threshold: number; dtype: string;
  capped: boolean; layout: string; start?: number; end?: number;
}

/** Sparse trigram points, interleaved [x,y,z,count] i32. With `limit`,
    the server keeps the densest points (whole-file u8 responses are
    count-descending; capped computed responses are sorted to match). */
export async function getHist3(
  id: string, threshold = 1, dtype = "u8", start = 0, end = -1, limit = 0,
): Promise<{ pts: Int32Array; meta: Hist3Meta }> {
  const r = await ok(await get(
    `/api/${id}/hist3?threshold=${threshold}&dtype=${dtype}` +
    `&start=${start}&end=${end}&limit=${limit}`));
  const meta = xmeta<Hist3Meta>(r);
  return { pts: new Int32Array(await r.arrayBuffer()), meta };
}

export interface LocateRect {
  first0: number; first1: number; second0: number; second1: number;
}

export interface LocateMeta {
  n: number; dtype: string; start: number; end: number;
  rect: LocateRect; matches: number; pairs: number;
}

/** Brush-to-locate: bigram cell rect -> binned occurrence density. */
export async function postLocate(
  id: string, rect: LocateRect,
  opts: { dtype?: string; start?: number; end?: number; n?: number } = {},
): Promise<{ density: Uint32Array; meta: LocateMeta }> {
  const r = await ok(await fetch(`/api/${id}/hist/locate`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ ...rect, ...opts }),
    cache: "no-store",
  }));
  const meta = xmeta<LocateMeta>(r);
  return { density: new Uint32Array(await r.arrayBuffer()), meta };
}

/* ------------------------------------------------------ CFG (Phase 10) */

export interface FunctionIndexEntry {
  va: number; name: string; size: number;
  discovery: string;        // symbol|entry|call_target|ptr_target|prologue|gap_sweep
  confidence: number;
  mode: string;
  complete: boolean;
  blocks: number; edges: number; insns: number; unresolved: number;
}

export interface FunctionsDoc {
  functions: FunctionIndexEntry[];
  call_graph: { from: number; to: number }[];
  unclaimed_blocks: {
    va: number; end_va: number; file_off: number;
    insns: number; confidence: string;
  }[];
  packed: boolean;
  warnings: string[];
  stats: Record<string, number>;
}

export interface CfgInsn {
  va: number; size: number; bytes: string; mnemonic: string; op: string;
}

export interface CfgBlock {
  id: number; va: number; end_va: number; file_off: number;
  confidence: "high" | "low";
  insns: CfgInsn[];
  terminator: string;       // jcc|jmp|ret|call_noreturn|fallthrough|indirect|invalid|halt
}

export interface CfgEdge {
  src: number; dst: number;
  kind: "true" | "false" | "uncond" | "fallthrough" | "indirect_unresolved";
}

export interface CfgDoc {
  function: {
    va: number; name: string; size: number; discovery: string;
    confidence: number; mode: string; complete: boolean;
  };
  blocks: CfgBlock[];
  edges: CfgEdge[];
  unresolved: { va: number; reason: string; hint: string | null }[];
  calls_out: {
    from_va: number; target_va: number | null;
    name: string | null; kind: string;
  }[];
}

export async function getFunctions(id: string): Promise<FunctionsDoc> {
  return (await ok(await get(`/api/${id}/functions`))).json();
}

export async function getCfg(id: string, va: number): Promise<CfgDoc> {
  return (await ok(await get(`/api/${id}/cfg/0x${va.toString(16)}`))).json();
}

/* ---------------------------------------------------- triage (Phase 11) */

export type Verdict =
  | "likely_packed" | "likely_benign_binary" | "non_executable"
  | "corrupt" | "inconclusive";

export interface TriageFinding {
  severity: "high" | "medium" | "low";
  code: string;
  detail: string;
  offsets: [number, number] | null;   // half-open file range when navigable
  stride_bytes?: number;
}

export interface TriageDoc {
  verdict: Verdict;
  confidence: number;
  findings: TriageFinding[];
  format: string;
  size: number;
}

export async function getTriage(id: string): Promise<TriageDoc> {
  return (await ok(await get(`/api/${id}/triage`))).json();
}

/* ---------------------------------------- file navigation (Phase 11) */

export interface ServerConfig {
  /** Directory the server will read files from; null when unconfined. */
  root: string | null;
  max_upload: number;
  tool_version: string;
}

/** What this server allows. `root` is not guessable client-side — "."
    resolves against the server's cwd, which need not be --root. */
export async function getConfig(): Promise<ServerConfig> {
  return (await ok(await get("/api/config"))).json();
}

export interface FileEntry { name: string; path: string; size: number }

export async function getFiles(
  dir: string,
): Promise<{ dir: string; files: FileEntry[] }> {
  return (await ok(await get(
    `/api/files?dir=${encodeURIComponent(dir)}`))).json();
}

export async function getBytes(
  id: string, off: number, len: number,
): Promise<{ data: Uint8Array; off: number }> {
  const r = await ok(await get(`/api/${id}/bytes?off=${off}&len=${len}`));
  const meta = xmeta<{ off: number }>(r);
  return { data: new Uint8Array(await r.arrayBuffer()), off: meta.off };
}
