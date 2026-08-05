/* Hilbert curve d↔(x,y) — a verbatim port of surfaces/hilbert.py so a click
   on a Hilbert pixel maps to the same file offset the server used to fill
   it. Note the asymmetry: xy2d rotates with the full side n, d2xy with the
   sub-square size s — this mirrors the backend exactly. */

function rot(
  n: number, x: number, y: number, rx: number, ry: number,
): [number, number] {
  if (ry === 0) {
    if (rx === 1) {
      x = n - 1 - x;
      y = n - 1 - y;
    }
    return [y, x];
  }
  return [x, y];
}

/** Distance along the curve -> (x, y), for a 2^order x 2^order grid. */
export function d2xy(order: number, d: number): [number, number] {
  const n = 1 << order;
  let x = 0, y = 0, t = d;
  for (let s = 1; s < n; s *= 2) {
    const rx = 1 & Math.floor(t / 2);
    const ry = 1 & (t ^ rx);
    [x, y] = rot(s, x, y, rx, ry);
    x += s * rx;
    y += s * ry;
    t = Math.floor(t / 4);
  }
  return [x, y];
}

/** (x, y) -> distance along the curve. Inverse of d2xy. */
export function xy2d(order: number, x: number, y: number): number {
  const n = 1 << order;
  let d = 0;
  for (let s = n / 2; s >= 1; s = Math.floor(s / 2)) {
    const rx = (x & s) > 0 ? 1 : 0;
    const ry = (y & s) > 0 ? 1 : 0;
    d += s * s * ((3 * rx) ^ ry);
    [x, y] = rot(n, x, y, rx, ry);
  }
  return d;
}

/** File offset for a clicked Hilbert pixel — mirrors HilbertSurface.offset_at_xy. */
export function offsetAtXY(
  order: number, x: number, y: number, start: number, nbytes: number,
): number {
  const nCells = (1 << order) * (1 << order);
  return start + Math.floor((xy2d(order, x, y) * nbytes) / nCells);
}
