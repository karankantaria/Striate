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

/* ------------------------------------------------------- byte classes */

export type Theme = "light" | "dark";

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

export const BYTE_CLASS_COLORS: Record<Theme, string[]> = {
  light: ["#eceae4", "#2a78d6", "#1baf7a", "#eb6834", "#a848b8", "#26251f"],
  dark: ["#26272b", "#3987e5", "#199e70", "#d95926", "#c559c5", "#f2f0e9"],
};

export function byteClassLut(theme: Theme): Lut {
  const lut = new Uint8Array(768);
  const colors = BYTE_CLASS_COLORS[theme];
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = hex(colors[Math.min(i, colors.length - 1)]);
    lut[i * 3] = r; lut[i * 3 + 1] = g; lut[i * 3 + 2] = b;
  }
  return lut;
}

/* --------------------------------------------------------- series ink */

// Plot-view series colors: the validated categorical order (fixed order,
// never cycled; assigned per signal name so a toggled-off series never
// repaints the survivors).
export const SERIES: Record<Theme, string[]> = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#a848b8", "#008300"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#c559c5", "#008300"],
};

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
