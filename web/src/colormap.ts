/* Colour mapping — scalar rasters ship raw from the server and are coloured
   client-side through a 256-entry RGB LUT, so changing colormap or theme
   never refetches.

   Sequential maps are perceptually uniform (viridis/magma/inferno via the
   CC0 polynomial fits by Matt Zucker); never rainbow/jet. The byte-class
   palette is categorical, from a distinct hue family, validated with the
   dataviz palette checker in both modes (all-pairs): blue/orange/aqua/
   fuchsia for the four mid classes, with 0x00 and 0xFF on deliberate
   lightness anchors (padding recedes toward the surface, erased flash pops
   as ink). The legend chips + hover tooltip are the required secondary
   encoding for the warn-band CVD pair. */

export type Lut = Uint8Array; // 256 * 3, [r,g,b] per value

function polyLut(c: number[][]): Lut {
  const lut = new Uint8Array(768);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    for (let ch = 0; ch < 3; ch++) {
      // c0 + t*(c1 + t*(c2 + t*(c3 + t*(c4 + t*(c5 + t*c6)))))
      let v = c[6][ch];
      for (let k = 5; k >= 0; k--) v = c[k][ch] + t * v;
      lut[i * 3 + ch] = Math.max(0, Math.min(255, Math.round(v * 255)));
    }
  }
  return lut;
}

const VIRIDIS_C = [
  [0.2777273272234177, 0.005407344544966578, 0.3340998053353061],
  [0.1050930431085774, 1.404613529898575, 1.384590162594685],
  [-0.3308618287255563, 0.214847559468213, 0.09509516302823659],
  [-4.634230498983486, -5.799100973351585, -19.33244095627987],
  [6.228269936347081, 14.17993336680509, 56.69055260068105],
  [4.776384997670288, -13.74514537774601, -65.35303263337234],
  [-5.435455855934631, 4.645852612178535, 26.3124352495832],
];
const MAGMA_C = [
  [-0.002136485053939582, -0.000749655052795221, -0.005386127855323933],
  [0.2516605407371642, 0.6775232436837668, 2.494026599312351],
  [8.353717279216625, -3.577719514958484, 0.3144679030132573],
  [-27.66873308576866, 14.26473078096533, -13.64921318813922],
  [52.17613981234068, -27.94360607168351, 12.94416944238394],
  [-50.76852536473588, 29.04658282127291, 4.23415299384598],
  [18.65570506591883, -11.48977351997711, -5.601961508734096],
];
const INFERNO_C = [
  [0.0002189403691192265, 0.001651004631001012, -0.01948089843709184],
  [0.1065134194856116, 0.5639564367884091, 3.932712388889277],
  [11.60249308247187, -3.972853965665698, -15.9423941062914],
  [-41.70399613139459, 17.43639888205313, 44.35414519872687],
  [77.162935699427, -33.40235894210092, -81.80730925738993],
  [-71.31942824499214, 32.62606426397723, 73.20951985803202],
  [25.13112622477341, -12.24266895238567, -23.07032500287172],
];

export const VIRIDIS: Lut = polyLut(VIRIDIS_C);
export const MAGMA: Lut = polyLut(MAGMA_C);
export const INFERNO: Lut = polyLut(INFERNO_C);

export const GRAY: Lut = (() => {
  const lut = new Uint8Array(768);
  for (let i = 0; i < 256; i++) lut[i * 3] = lut[i * 3 + 1] = lut[i * 3 + 2] = i;
  return lut;
})();

/* ------------------------------------------------------- byte classes

   One palette, not two. RELEASE.md §3 fixes Striate as dark-only, so the
   light theme and its second set of chart colours are gone; what is left
   is stepped against the one surface it will ever be drawn on
   (`--panel` #453B3B).

   These were not chosen by eye. Every value below was generated at a
   target OKLCH lightness and hue, then run through the dataviz validator
   against that surface, which measures the lightness band, the chroma
   floor, CVD separation (OKLab ΔE under simulated protanopia and
   deuteranopia), the normal-vision floor, and WCAG contrast. Re-run it
   before changing any of them — "it looks fine to me" is exactly the
   judgement colour-vision deficiency defeats. */

function hex(s: string): [number, number, number] {
  return [
    parseInt(s.slice(1, 3), 16),
    parseInt(s.slice(3, 5), 16),
    parseInt(s.slice(5, 7), 16),
  ];
}

// index == server byte-class id; names pinned by the backend:
// 0 null · 1 printable · 2 whitespace · 3 control · 4 high · 5 0xff
export const BYTE_CLASS_NAMES = [
  "null", "printable", "whitespace", "control", "high", "0xff",
] as const;

/* Slots 1–4 are the categorical set and are validated with `--pairs all`,
   not the default adjacent-only: this is a raster, so any class can end up
   touching any other and there is no such thing as a non-adjacent pair.
   Worst pair is control↔whitespace at ΔE 9.3 under protanopia, above the
   target of 8.

   Slots 0 and 5 are deliberate extremes rather than categories: null
   recedes toward the panel, 0xff is the brand cream. They fail the
   lightness band and the chroma floor on purpose — that is the encoding.

   The validator WARNs that whitespace and high sit under 3:1 against the
   surface. Kept: a filled raster tiles the whole canvas, so these marks
   are read against each *other*, not against a background you can see,
   and mark-to-mark separation is what the CVD checks above measure. The
   legend carries the names either way. */
export const BYTE_CLASS_COLORS: string[] = [
  "#3a3130",   // null       recedes into the panel
  "#1e8fee",   // printable
  "#017634",   // whitespace
  "#d77800",   // control
  "#a4429e",   // high
  "#fcf2e5",   // 0xff       the brand cream: the top of the range
];

export function byteClassLut(): Lut {
  const lut = new Uint8Array(768);
  const colors = BYTE_CLASS_COLORS;
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = hex(colors[Math.min(i, colors.length - 1)]);
    lut[i * 3] = r; lut[i * 3 + 1] = g; lut[i * 3 + 2] = b;
  }
  return lut;
}

/* --------------------------------------------------------- series ink */

/* Plot-view series ink: fixed order, never cycled, assigned per signal
   name so toggling one off never repaints the survivors.

   Lightness alternates deliberately across the slots. An equal-lightness
   set validated worse, not better: under deuteranopia the hue difference
   is most of what collapses, and lightness is what is left to tell two
   series apart. Worst adjacent pair here is ΔE 9.1 under deuteranopia.

   Three slots WARN under 3:1 against the surface. That is relieved rather
   than ignored: every lane is titled on the plot and every series has a
   named checkbox in the legend, so identity never rests on colour alone. */
export const SERIES: string[] = [
  "#2a97f7",   // blue
  "#a26c00",   // amber
  "#25ae56",   // green
  "#a644a0",   // magenta
  "#00a6ac",   // cyan
  "#cf4946",   // red
];

// Brush-to-locate highlight ink as "r,g,b" for rgba() templating with a
// density-driven alpha. The brand accent, which nothing else in a raster
// uses, so it cannot be confused with the selection band it draws over.
export const LOCATE_RGB = "236,91,56";      // --accent #EC5B38

/* ---------------------------------------------------------- rendering */

/** Paint a scalar raster through a LUT into RGBA pixels. */
export function applyLut(
  pixels: Uint8Array, lut: Lut, out?: Uint8ClampedArray,
): Uint8ClampedArray {
  const rgba = out ?? new Uint8ClampedArray(pixels.length * 4);
  for (let i = 0; i < pixels.length; i++) {
    const v = pixels[i] * 3;
    rgba[i * 4] = lut[v];
    rgba[i * 4 + 1] = lut[v + 1];
    rgba[i * 4 + 2] = lut[v + 2];
    rgba[i * 4 + 3] = 255;
  }
  return rgba;
}
