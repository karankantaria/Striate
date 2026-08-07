/* 3D histogram (trigram) view — the one place WebGL is genuinely
   required: 10k–1M sparse (x,y,z,count) cells as gl.POINTS at 60 fps.

   Server ships points count-descending (threshold = prefix), one
   interleaved i32 upload. Colour and size come from log-scaled count via
   the viridis polynomial evaluated in the shader; the `overlap` toggle
   switches additive blending with depth-write off (reveals interior
   density) against depth-tested opaque points. Trackball rotation plus
   idle auto-spin: a static projection of a point cloud is ambiguous and
   motion parallax resolves it. The unit cube wireframe and axis labels
   (drawn on a 2D overlay from the same MVP) give the cloud its frame of
   reference. */

import { getHist3, type BinaryModel, type Hist3Meta } from "../api.ts";
import { clearPaneError, paneError } from "../panestatus.ts";
import type { OffsetRange, SelectionStore } from "../store.ts";

const MAX_PTS = 1_000_000;        // GPU upload cap; server order keeps densest
const SPIN_RATE = 0.35;           // rad/s
const SPIN_RESUME_MS = 2500;

/* --------------------------------------------------- tiny mat4 (column-major) */

type Mat4 = Float32Array;

function mat4Mul(a: Mat4, b: Mat4): Mat4 {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] +
                     a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
  }
  return o;
}

function mat4Perspective(fovy: number, aspect: number,
                         near: number, far: number): Mat4 {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  const o = new Float32Array(16);
  o[0] = f / aspect; o[5] = f;
  o[10] = (far + near) * nf; o[11] = -1;
  o[14] = 2 * far * near * nf;
  return o;
}

function mat4Translate(z: number): Mat4 {
  const o = new Float32Array(16);
  o[0] = o[5] = o[10] = o[15] = 1;
  o[14] = z;
  return o;
}

function mat4RotX(a: number): Mat4 {
  const c = Math.cos(a), s = Math.sin(a);
  const o = new Float32Array(16);
  o[0] = 1; o[15] = 1;
  o[5] = c; o[6] = s; o[9] = -s; o[10] = c;
  return o;
}

function mat4RotY(a: number): Mat4 {
  const c = Math.cos(a), s = Math.sin(a);
  const o = new Float32Array(16);
  o[5] = 1; o[15] = 1;
  o[0] = c; o[2] = -s; o[8] = s; o[10] = c;
  return o;
}

/* ------------------------------------------------------------- shaders */

// viridis via the same CC0 polynomial fit used in colormap.ts
const VIRIDIS_GLSL = `
vec3 viridis(float t) {
  const vec3 c0 = vec3(0.2777273272234177, 0.005407344544966578, 0.3340998053353061);
  const vec3 c1 = vec3(0.1050930431085774, 1.404613529898575, 1.384590162594685);
  const vec3 c2 = vec3(-0.3308618287255563, 0.214847559468213, 0.09509516302823659);
  const vec3 c3 = vec3(-4.634230498983486, -5.799100973351585, -19.33244095627987);
  const vec3 c4 = vec3(6.228269936347081, 14.17993336680509, 56.69055260068105);
  const vec3 c5 = vec3(4.776384997670288, -13.74514537774601, -65.35303263337234);
  const vec3 c6 = vec3(-5.435455855934631, 4.645852612178535, 26.3124352495832);
  return clamp(c0 + t*(c1 + t*(c2 + t*(c3 + t*(c4 + t*(c5 + t*c6))))), 0.0, 1.0);
}`;

const POINT_VS = `#version 300 es
in vec4 a_pt;                     // x, y, z (0..255), count
uniform mat4 u_mvp;
uniform float u_logmax;           // log(1 + max count)
uniform float u_scale;
uniform float u_dpr;
out float v_t;
void main() {
  vec3 p = a_pt.xyz / 255.0 - 0.5;
  gl_Position = u_mvp * vec4(p, 1.0);
  float t = u_logmax > 0.0 ? log(1.0 + a_pt.w) / u_logmax : 0.0;
  v_t = t;
  gl_PointSize = clamp(u_scale * (0.75 + 2.5 * t) * u_dpr, 1.0, 24.0 * u_dpr);
}`;

const POINT_FS = `#version 300 es
precision mediump float;
in float v_t;
uniform float u_additive;         // 1.0 = additive/overlap mode
uniform float u_dim;              // density compensation for ONE,ONE blend
out vec4 outColor;
${VIRIDIS_GLSL}
void main() {
  vec2 d = gl_PointCoord - 0.5;
  if (dot(d, d) > 0.25) discard;  // round sprites
  vec3 c = viridis(clamp(v_t, 0.0, 1.0));
  vec3 dim = c * (0.2 + 0.8 * v_t) * u_dim;  // pre-scaled for ONE,ONE blend
  outColor = vec4(mix(c, dim, u_additive), 1.0);
}`;

const LINE_VS = `#version 300 es
in vec3 a_pos;
uniform mat4 u_mvp;
void main() { gl_Position = u_mvp * vec4(a_pos, 1.0); }`;

const LINE_FS = `#version 300 es
precision mediump float;
uniform vec4 u_color;
out vec4 outColor;
void main() { outColor = u_color; }`;

function compile(gl: WebGL2RenderingContext, type: number, src: string) {
  const sh = gl.createShader(type)!;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    throw new Error("shader: " + gl.getShaderInfoLog(sh));
  }
  return sh;
}

function program(gl: WebGL2RenderingContext, vs: string, fs: string) {
  const p = gl.createProgram()!;
  gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    throw new Error("link: " + gl.getProgramInfoLog(p));
  }
  return p;
}

function cssColor(v: string): [number, number, number, number] {
  const s = v.trim();
  if (s.startsWith("#")) {
    return [parseInt(s.slice(1, 3), 16) / 255, parseInt(s.slice(3, 5), 16) / 255,
            parseInt(s.slice(5, 7), 16) / 255, 1];
  }
  const m = s.match(/rgba?\(([^)]+)\)/);
  if (m) {
    const p = m[1].split(",").map(Number);
    return [p[0] / 255, p[1] / 255, p[2] / 255, p[3] ?? 1];
  }
  return [0.5, 0.5, 0.5, 1];
}

/* ---------------------------------------------------------------- view */

const CUBE_CORNERS: [number, number, number][] = [];
for (let i = 0; i < 8; i++) {
  CUBE_CORNERS.push([(i & 1) - 0.5, ((i >> 1) & 1) - 0.5, ((i >> 2) & 1) - 0.5]);
}
const CUBE_EDGES = [0, 1, 0, 2, 0, 4, 1, 3, 1, 5, 2, 3, 2, 6, 3, 7,
                    4, 5, 4, 6, 5, 7, 6, 7];

export class Hist3DView {
  private host: HTMLElement;
  private glCanvas: HTMLCanvasElement;
  private overlay: HTMLCanvasElement;
  private gl: WebGL2RenderingContext | null = null;
  private ptProg!: WebGLProgram;
  private lnProg!: WebGLProgram;
  private ptVao!: WebGLVertexArrayObject;
  private ptBuf!: WebGLBuffer;
  private lnVao!: WebGLVertexArrayObject;
  private store: SelectionStore;
  private id = "";
  private model: BinaryModel | null = null;
  private nPts = 0;
  private maxCount = 0;
  threshold = 1;
  scale = 2;
  overlap = true;
  spin = true;
  onStats: ((text: string) => void) | null = null;
  // camera
  private yaw = 0.7;
  private pitch = 0.45;
  private dist = 2.3;
  private lastInteract = 0;
  private dragging = false;
  private lastPX = 0;
  private lastPY = 0;
  private dirty = true;
  private lastFrame = 0;
  private fetchSeq = 0;
  private refetchTimer: number | undefined;
  private ro: ResizeObserver;
  /** Pane not on screen (§3.4). Cached from the resize observer rather than
      read per frame: `clientWidth` forces a layout, and this is checked 60
      times a second. */
  private offscreen = true;

  constructor(host: HTMLElement, store: SelectionStore) {
    this.host = host;
    this.store = store;
    this.glCanvas = document.createElement("canvas");
    this.overlay = document.createElement("canvas");
    this.overlay.style.zIndex = "1";
    host.appendChild(this.glCanvas);
    host.appendChild(this.overlay);

    const gl = this.glCanvas.getContext("webgl2", { antialias: true });
    if (gl) {
      this.gl = gl;
      this.ptProg = program(gl, POINT_VS, POINT_FS);
      this.lnProg = program(gl, LINE_VS, LINE_FS);
      this.ptBuf = gl.createBuffer()!;
      this.ptVao = gl.createVertexArray()!;
      gl.bindVertexArray(this.ptVao);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.ptBuf);
      const loc = gl.getAttribLocation(this.ptProg, "a_pt");
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 4, gl.FLOAT, false, 16, 0);
      // cube wireframe
      const lines = new Float32Array(CUBE_EDGES.length * 3);
      CUBE_EDGES.forEach((corner, i) => {
        lines.set(CUBE_CORNERS[corner], i * 3);
      });
      this.lnVao = gl.createVertexArray()!;
      gl.bindVertexArray(this.lnVao);
      const lbuf = gl.createBuffer()!;
      gl.bindBuffer(gl.ARRAY_BUFFER, lbuf);
      gl.bufferData(gl.ARRAY_BUFFER, lines, gl.STATIC_DRAW);
      const lloc = gl.getAttribLocation(this.lnProg, "a_pos");
      gl.enableVertexAttribArray(lloc);
      gl.vertexAttribPointer(lloc, 3, gl.FLOAT, false, 12, 0);
      gl.bindVertexArray(null);
    } else {
      host.textContent = "WebGL2 unavailable — 3D histogram disabled";
    }

    this.ro = new ResizeObserver(() => { this.resize(); });
    this.ro.observe(host);
    this.resize();

    store.on("selection", () => this.debouncedRefetch());
    store.on("dtype", () => this.refetch());

    this.overlay.addEventListener("pointerdown", (e) => {
      this.overlay.setPointerCapture(e.pointerId);
      this.dragging = true;
      this.lastPX = e.clientX; this.lastPY = e.clientY;
      this.lastInteract = performance.now();
    });
    this.overlay.addEventListener("pointermove", (e) => {
      if (!this.dragging || !(e.buttons & 1)) return;
      this.yaw += (e.clientX - this.lastPX) * 0.008;
      this.pitch += (e.clientY - this.lastPY) * 0.008;
      this.pitch = Math.max(-1.55, Math.min(1.55, this.pitch));
      this.lastPX = e.clientX; this.lastPY = e.clientY;
      this.lastInteract = performance.now();
      this.dirty = true;
    });
    this.overlay.addEventListener("pointerup", () => {
      this.dragging = false;
      this.lastInteract = performance.now();
    });
    this.overlay.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.dist = Math.max(1.2, Math.min(6, this.dist * Math.exp(e.deltaY * 0.001)));
      this.lastInteract = performance.now();
      this.dirty = true;
    }, { passive: false });

    requestAnimationFrame((t) => this.frame(t));
  }

  setBinary(id: string, model: BinaryModel): void {
    this.id = id;
    this.model = model;
    this.refetch();
  }

  setThreshold(t: number): void {
    this.threshold = Math.max(1, Math.floor(t));
    this.refetch();
  }

  setScale(s: number): void { this.scale = s; this.dirty = true; }
  setOverlap(on: boolean): void { this.overlap = on; this.dirty = true; }
  setSpin(on: boolean): void { this.spin = on; }

  private range(): OffsetRange {
    return this.store.state.offsetRange
      ?? { start: 0, end: this.model?.size ?? 0 };
  }

  private debouncedRefetch(): void {
    window.clearTimeout(this.refetchTimer);
    this.refetchTimer = window.setTimeout(() => this.refetch(), 300);
  }

  async refetch(): Promise<void> {
    if (!this.id || !this.model || !this.gl) return;
    const seq = ++this.fetchSeq;
    const r = this.range();
    let pts: Int32Array, meta: Hist3Meta;
    try {
      ({ pts, meta } = await getHist3(
        this.id, this.threshold, this.store.state.dtype,
        r.start, r.end, MAX_PTS));
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 409 || status === 410) {
        window.clearTimeout(this.refetchTimer);
        this.refetchTimer = window.setTimeout(() => this.refetch(), 700);
      } else {
        paneError(this.host, "could not load the trigram", e,
                  () => this.refetch());
      }
      return;
    }
    if (seq !== this.fetchSeq) return;
    clearPaneError(this.host);
    this.nPts = pts.length / 4;
    // computed subrange responses are key-ordered unless capped — scan
    let max = 0;
    for (let i = 3; i < pts.length; i += 4) if (pts[i] > max) max = pts[i];
    this.maxCount = max;
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ptBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pts), gl.STATIC_DRAW);
    this.dirty = true;
    if (this.onStats) {
      let txt = `${this.nPts.toLocaleString()} pts`;
      if (meta.capped) txt += " (densest shown)";
      txt += ` · ${meta.total_points.toLocaleString()} cells`;
      this.onStats(txt);
    }
  }

  /* ------------------------------------------------------------ render */

  private resize(): void {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    this.offscreen = w === 0 || h === 0;
    if (this.offscreen) return;
    for (const c of [this.glCanvas, this.overlay]) {
      c.width = w * devicePixelRatio;
      c.height = h * devicePixelRatio;
      c.style.width = w + "px";
      c.style.height = h + "px";
    }
    this.dirty = true;
  }

  private frame(t: number): void {
    requestAnimationFrame((tt) => this.frame(tt));
    // A hidden pane keeps its rAF loop (so it resumes instantly) but does no
    // GL work and does not advance the spin — otherwise the Patterns
    // workspace would cost a GPU frame every 16 ms from whichever tab the
    // user is actually on. `dirty` is set by resize() on the way back, so
    // the first visible frame repaints.
    if (this.offscreen) { this.lastFrame = t; return; }
    const dt = this.lastFrame ? Math.min((t - this.lastFrame) / 1000, 0.1) : 0;
    this.lastFrame = t;
    const idle = !this.dragging &&
      t - this.lastInteract > SPIN_RESUME_MS;
    if (this.spin && idle && this.nPts > 0) {
      this.yaw += SPIN_RATE * dt;
      this.dirty = true;
    }
    if (this.dirty) this.draw();
  }

  private mvp(): Mat4 {
    const w = this.glCanvas.width, h = Math.max(1, this.glCanvas.height);
    const proj = mat4Perspective(Math.PI / 4, w / h, 0.1, 20);
    let mv = mat4Mul(mat4RotX(this.pitch), mat4RotY(this.yaw));
    mv = mat4Mul(mat4Translate(-this.dist), mv);
    return mat4Mul(proj, mv);
  }

  private draw(): void {
    const gl = this.gl;
    if (!gl) return;
    this.dirty = false;
    const mvp = this.mvp();
    const css = getComputedStyle(document.documentElement);
    const gridCol = cssColor(css.getPropertyValue("--baseline"));

    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    // cube wireframe first (points may blend over it)
    gl.useProgram(this.lnProg);
    gl.uniformMatrix4fv(gl.getUniformLocation(this.lnProg, "u_mvp"), false, mvp);
    gl.uniform4fv(gl.getUniformLocation(this.lnProg, "u_color"), gridCol);
    gl.bindVertexArray(this.lnVao);
    gl.disable(gl.BLEND);
    gl.disable(gl.DEPTH_TEST);
    gl.drawArrays(gl.LINES, 0, CUBE_EDGES.length);

    if (this.nPts > 0) {
      gl.useProgram(this.ptProg);
      gl.uniformMatrix4fv(gl.getUniformLocation(this.ptProg, "u_mvp"), false, mvp);
      gl.uniform1f(gl.getUniformLocation(this.ptProg, "u_logmax"),
                   Math.log(1 + this.maxCount));
      gl.uniform1f(gl.getUniformLocation(this.ptProg, "u_scale"), this.scale);
      gl.uniform1f(gl.getUniformLocation(this.ptProg, "u_dpr"), devicePixelRatio);
      gl.uniform1f(gl.getUniformLocation(this.ptProg, "u_additive"),
                   this.overlap ? 1 : 0);
      // more points -> more overdraw: dim so density stays a gradient
      // instead of saturating a large uniform cloud to a white cube
      gl.uniform1f(gl.getUniformLocation(this.ptProg, "u_dim"),
                   Math.min(0.55, Math.max(0.1, 110_000 / this.nPts)));
      gl.bindVertexArray(this.ptVao);
      if (this.overlap) {
        // additive, depth-write off: interior density shows through
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE);
        gl.disable(gl.DEPTH_TEST);
      } else {
        gl.disable(gl.BLEND);
        gl.enable(gl.DEPTH_TEST);
      }
      gl.drawArrays(gl.POINTS, 0, this.nPts);
    }
    gl.bindVertexArray(null);

    this.drawLabels(mvp, css);
  }

  /** Axis labels on the 2D overlay, projected through the same MVP. */
  private drawLabels(mvp: Mat4, css: CSSStyleDeclaration): void {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    const ctx = this.overlay.getContext("2d")!;
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "10px ui-monospace, monospace";
    ctx.textAlign = "center";
    const ink2 = css.getPropertyValue("--text").trim();
    const muted = css.getPropertyValue("--muted").trim();

    const project = (p: [number, number, number]): [number, number] | null => {
      const x = mvp[0] * p[0] + mvp[4] * p[1] + mvp[8] * p[2] + mvp[12];
      const y = mvp[1] * p[0] + mvp[5] * p[1] + mvp[9] * p[2] + mvp[13];
      const cw = mvp[3] * p[0] + mvp[7] * p[1] + mvp[11] * p[2] + mvp[15];
      if (cw <= 0.01) return null;
      return [(x / cw * 0.5 + 0.5) * w, (0.5 - y / cw * 0.5) * h];
    };

    const labels: [string, [number, number, number], string][] = [
      ["b[i]", [0.62, -0.5, -0.5], ink2],
      ["b[i+1]", [-0.5, 0.62, -0.5], ink2],
      ["b[i+2]", [-0.5, -0.5, 0.62], ink2],
      ["00", [-0.56, -0.56, -0.56], muted],
    ];
    for (const [text, pos, color] of labels) {
      const pt = project(pos);
      if (!pt) continue;
      ctx.fillStyle = color;
      ctx.fillText(text, pt[0], pt[1]);
    }
  }
}
