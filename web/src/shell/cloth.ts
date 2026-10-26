/**
 * The cloth of a ribbon bookmark: a small Verlet simulation of a hanging strip (pure maths, no DOM; ClothRibbon.tsx
 * draws it on a canvas). A grid of particles is pinned along the top row, pulled down by gravity, pushed sideways
 * and toward the viewer by a slow breath of wind (and by a gust while the pointer is over it), and held together by
 * distance links that are relaxed a few times per step. The look is ported from the approved prototype
 * (.impeccable/mocks/decision/home-1.html).
 */

export interface Particle {
  x: number;
  y: number;
  z: number;
  /** The previous position: Verlet integration keeps no velocity. */
  px: number;
  py: number;
  pz: number;
  /** Pinned particles (the top row, under the fold) never move. */
  pin: boolean;
}

/** Two particles (by index) and the distance they keep. */
export type Link = readonly [a: number, b: number, rest: number];

export interface Cloth {
  cols: number;
  rows: number;
  /** The strip's width in px. */
  width: number;
  /** Height of the fold over the top edge, in px. */
  top: number;
  particles: Particle[];
  links: Link[];
  /** Simulation time in seconds: it phases the wind. */
  t: number;
  /** Extra wind from the pointer, decaying; 0 is calm. */
  gust: number;
}

export interface ClothOptions {
  /** The strip's width in px. */
  width: number;
  /** The strip's height in px, from the top of the fold to the longest point of the tail. */
  length: number;
  /** x of the strip's left edge in the canvas. */
  originX: number;
  /** Height of the fold in px. */
  top?: number;
  cols?: number;
  rows?: number;
  /** How far the swallowtail notch rises at the centre, in px. */
  notch?: number;
  /** Where in the wind's cycle this strip starts, in seconds: two ribbons must not sway in step. */
  phase?: number;
}

export const GRAVITY = 900;
const DAMPING = 0.97;
const GUST_DECAY = 0.985;
/** Steps per second the damping and decay constants are tuned for (the prototype: two steps per frame at 60 fps). */
const REFERENCE_RATE = 120;
const RELAX_PASSES = 14;

export function createCloth({ width, length, originX, top = 9, cols = 5, rows = 20, notch = 18, phase = 0 }: ClothOptions): Cloth {
  const dx = width / (cols - 1);
  const dy = (length - top) / (rows - 1);
  const particles: Particle[] = [];
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      let y = top + j * dy;
      // The swallowtail: the last row is notched up toward its middle.
      if (j === rows - 1) y -= notch * (1 - Math.abs(i - (cols - 1) / 2) / ((cols - 1) / 2));
      const x = originX + i * dx;
      particles.push({ x, y, z: 0, px: x, py: y, pz: 0, pin: j === 0 });
    }
  }
  const links: Link[] = [];
  const id = (i: number, j: number) => j * cols + i;
  const link = (a: number, b: number) => {
    const A = particles[a];
    const B = particles[b];
    if (A && B) links.push([a, b, Math.hypot(A.x - B.x, A.y - B.y)]);
  };
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      if (i < cols - 1) link(id(i, j), id(i + 1, j));
      if (j < rows - 1) link(id(i, j), id(i, j + 1));
      // Shear, so the strip does not fold like a fan.
      if (i < cols - 1 && j < rows - 1) {
        link(id(i, j), id(i + 1, j + 1));
        link(id(i + 1, j), id(i, j + 1));
      }
      // Bend: a strip of cloth resists a sharp crease.
      if (i < cols - 2) link(id(i, j), id(i + 2, j));
      if (j < rows - 2) link(id(i, j), id(i, j + 2));
      if (j < rows - 4) link(id(i, j), id(i, j + 4));
      // Across the width, once per row: the edges stay the strip's width apart.
      if (i === 0) link(id(0, j), id(cols - 1, j));
    }
  }
  return { cols, rows, width, top, particles, links, t: phase, gust: 0 };
}

/** What is drawn from a strip: the outline and the triangles of the cloth, as the strip's own particles (they move in place). */
export interface ClothGeometry {
  /** Down the left side, along the tail, up the right side. */
  edge: Particle[];
  /** Two per cell, in the order they are painted. */
  triangles: [Particle, Particle, Particle][];
}

/** Built once per strip: the draw loop must not allocate it again every frame. */
export function buildGeometry(cloth: Cloth): ClothGeometry {
  const { cols, rows } = cloth;
  const at = (i: number, j: number) => particleAt(cloth, i, j);
  const edge: Particle[] = [];
  for (let j = 0; j < rows; j++) edge.push(at(0, j));
  for (let i = 0; i < cols; i++) edge.push(at(i, rows - 1));
  for (let j = rows - 1; j >= 0; j--) edge.push(at(cols - 1, j));
  const triangles: [Particle, Particle, Particle][] = [];
  for (let j = 0; j < rows - 1; j++) {
    for (let i = 0; i < cols - 1; i++) {
      triangles.push([at(i, j), at(i + 1, j), at(i, j + 1)], [at(i + 1, j), at(i + 1, j + 1), at(i, j + 1)]);
    }
  }
  return { edge, triangles };
}

/** The particle in column `i`, row `j`. */
export function particleAt(cloth: Cloth, i: number, j: number): Particle {
  const p = cloth.particles[j * cloth.cols + i];
  if (!p) throw new RangeError(`no particle at column ${i}, row ${j}`);
  return p;
}

/** The pointer is over the strip: a puff of wind that fades (`amount` adds to it, up to `max`). */
export function addGust(cloth: Cloth, amount: number, max = 1.6): void {
  cloth.gust = Math.min(max, cloth.gust + amount);
}

/** One step of `h` seconds (about 1/60 or less): wind and gravity move every free particle, then the links are relaxed. */
export function stepCloth(cloth: Cloth, h: number): void {
  cloth.t += h;
  // Damping and the gust's decay are per step of the reference rate (1/120 s), so a slower frame rate feels the same.
  const rate = h * REFERENCE_RATE;
  cloth.gust *= Math.pow(GUST_DECAY, rate);
  const damping = Math.pow(DAMPING, rate);
  const { t, gust, particles } = cloth;
  for (const p of particles) {
    if (p.pin) continue;
    const w = Math.sin(t * 0.8 + p.y * 0.022) * 0.75 + Math.sin(t * 1.3 + p.y * 0.04 + p.x * 0.05) * 0.25;
    const ax = Math.sin(t * 0.55 + p.y * 0.015) * 7 * (1 + gust);
    const az = w * (26 + 120 * gust);
    const vx = (p.x - p.px) * damping;
    const vy = (p.y - p.py) * damping;
    const vz = (p.z - p.pz) * damping;
    p.px = p.x;
    p.py = p.y;
    p.pz = p.z;
    p.x += vx + ax * h * h;
    p.y += vy + GRAVITY * h * h;
    p.z += vz + az * h * h;
  }
  for (let n = 0; n < RELAX_PASSES; n++) {
    for (const [a, b, rest] of cloth.links) {
      const A = particles[a];
      const B = particles[b];
      if (!A || !B) continue;
      const ex = B.x - A.x;
      const ey = B.y - A.y;
      const ez = B.z - A.z;
      const d = Math.sqrt(ex * ex + ey * ey + ez * ez) || 1; // the hot loop: sqrt, not hypot
      const f = ((d - rest) / d) * 0.5;
      // A pinned end takes none of the correction; its partner takes all of it.
      const wa = A.pin ? 0 : B.pin ? 2 : 1;
      const wb = B.pin ? 0 : A.pin ? 2 : 1;
      A.x += ex * f * wa;
      A.y += ey * f * wa;
      A.z += ez * f * wa;
      B.x -= ex * f * wb;
      B.y -= ey * f * wb;
      B.z -= ez * f * wb;
    }
  }
}

/** Wind is calm when no gust is left: the strip only breathes, and a lower frame rate shows no difference. */
export const CALM_GUST = 0.02;

export function isCalm(cloth: Cloth): boolean {
  return cloth.gust < CALM_GUST;
}

/** Runs the simulation forward without drawing, so a strip starts at rest and a still one (reduced motion) hangs naturally. */
export function settle(cloth: Cloth, steps = 240, h = 1 / 120): void {
  for (let n = 0; n < steps; n++) stepCloth(cloth, h);
}

type Vec3 = [number, number, number];

function normalise(v: Vec3): Vec3 {
  const l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

/** The light comes from the upper left, in front. */
const LIGHT = normalise([-0.45, -0.55, 0.7]);
const HALF = normalise([LIGHT[0], LIGHT[1], LIGHT[2] + 1]);

/** `#rrggbb` as [r, g, b]. */
export function hexToRgb(hex: string): Vec3 {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/**
 * The colour of one triangle of the cloth: its base colour, lit by the angle between the triangle's normal and the
 * light (a broad shade, 70 to 106 percent) plus a tight highlight where the cloth faces the half-vector.
 */
export function shadeTriangle(a: Particle, b: Particle, c: Particle, base: Vec3): string {
  const u: Vec3 = [b.x - a.x, b.y - a.y, b.z - a.z];
  const v: Vec3 = [c.x - a.x, c.y - a.y, c.z - a.z];
  let n = normalise([u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]);
  // Cloth has two sides, both lit as the one facing the viewer.
  if (n[2] < 0) n = [-n[0], -n[1], -n[2]];
  const diffuse = Math.max(0, n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]);
  const specular = Math.pow(Math.max(0, n[0] * HALF[0] + n[1] * HALF[1] + n[2] * HALF[2]), 12) * 30;
  const m = 0.7 + 0.36 * diffuse;
  const [r, g, bl] = base.map((x) => Math.min(255, Math.round(x * m + specular))) as Vec3;
  return `rgb(${r},${g},${bl})`;
}

export interface LabelPose {
  /** Where the label's top centre sits, in the canvas's coordinates. */
  x: number;
  y: number;
  /** Radians: the label turns with the strip's centre line. */
  angle: number;
  /** The strip narrows when it turns edge-on, and the label narrows with it. */
  scaleX: number;
}

/** Where the label (a word and an icon printed on the cloth) goes, so that it rides the cloth instead of floating over it. */
export function labelPose(cloth: Cloth): LabelPose {
  const { cols, rows, width } = cloth;
  const r0 = Math.round(rows * 0.35);
  const mid = Math.floor((cols - 1) / 2);
  const p0 = particleAt(cloth, mid, r0 - 2);
  const p1 = particleAt(cloth, mid, r0 + 6);
  const left = particleAt(cloth, 0, r0 + 2);
  const right = particleAt(cloth, cols - 1, r0 + 2);
  return {
    x: p0.x,
    y: p0.y,
    angle: Math.atan2(p1.x - p0.x, p1.y - p0.y),
    scaleX: Math.max(0.35, Math.sqrt((right.x - left.x) ** 2 + (right.y - left.y) ** 2) / width),
  };
}
