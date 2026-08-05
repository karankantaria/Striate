/* Bigram display transforms — the client-side mirror of
   surfaces/ngram.py:to_display(). Raw counts span 6+ orders of magnitude,
   so the transform determines what the plot *means*; it is applied
   client-side over the raw /hist counts so switching modes never
   refetches. DOM-free on purpose: node --test exercises it against the
   Python semantics.

   log1p  general purpose default
   rank   percentile-flattened; best for faint structure
   sqrt   gentler than log, keeps some magnitude sense
   linear raw; almost always shows one bright cell and nothing else */

export type DisplayMode = "log1p" | "rank" | "sqrt" | "linear";

export const DISPLAY_MODES: readonly DisplayMode[] =
  ["log1p", "rank", "sqrt", "linear"];

/** Counts -> uint8 display values (0..255), peak-normalised. */
export function toDisplay(counts: Uint32Array, mode: DisplayMode): Uint8Array {
  const n = counts.length;
  const out = new Uint8Array(n);

  if (mode === "rank") {
    // nonzero cells get 1..nnz by ascending count (ties broken by index,
    // matching numpy's stable argsort().argsort()); zeros stay 0
    const nz: number[] = [];
    for (let i = 0; i < n; i++) if (counts[i] > 0) nz.push(i);
    if (nz.length === 0) return out;
    nz.sort((a, b) => counts[a] - counts[b] || a - b);
    const peak = nz.length;   // top rank
    for (let r = 0; r < nz.length; r++) {
      out[nz[r]] = Math.min(255, ((r + 1) * 255) / peak);
    }
    return out;
  }

  const f = mode === "linear" ? (c: number) => c
    : mode === "sqrt" ? Math.sqrt
    : Math.log1p;
  let peak = 0;
  const v = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    v[i] = f(counts[i]);
    if (v[i] > peak) peak = v[i];
  }
  if (peak <= 0) return out;
  const s = 255 / peak;
  for (let i = 0; i < n; i++) out[i] = Math.min(255, v[i] * s);
  return out;
}
