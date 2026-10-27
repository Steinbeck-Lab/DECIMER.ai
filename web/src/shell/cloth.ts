/**
 * The cloth of a ribbon bookmark: a small position-based (Verlet) simulation of a thick hanging strip, and a software
 * rasteriser that paints it smoothly (pure maths, no DOM; ClothRibbon.tsx puts the pixels on a canvas).
 *
 * The strip is a grid of particles pinned along its top row. Each fixed sub-step (1/180 s) moves every free particle by
 * gravity, by air (an aerodynamic push on every triangle along its normal, from the wind relative to the cloth, plus a
 * little skin drag) and by its own momentum; then distance links (structural and shear), "straightness" constraints
 * (stiff across the width, so the strip reads as thick; soft along the length, so slow folds can form) and long-range
 * tethers (the strip never stretches) are relaxed. The wind is a slow side breath, waves travelling down the length,
 * a twist that rolls the strip about its long axis, a flutter at the tail and occasional gusts from smooth noise; the
 * pointer adds a gust from its own side and can grab the strip and pull it.
 *
 * The look (colours, light, the fold over the top edge) is ported from the approved prototype
 * (.impeccable/mocks/decision/home-1.html); the shading is per pixel from interpolated vertex normals, so no facets show.
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
  /** Where the particle sits on the flat cloth, in px down the strip (the ribs of the weave follow it). */
  v: number;
}

/** Two particles (by index), the distance they keep, and how firmly (0 to 1, per relaxation pass). */
export type Link = readonly [a: number, b: number, rest: number, stiffness: number];

/**
 * Three particles in a row or a column (by index): the middle one is held where it sits at rest relative to the
 * midpoint of the other two (`offset` px down; the swallowtail bends the rows a little), which resists bending.
 */
export type Straight = readonly [a: number, middle: number, b: number, stiffness: number, offset: number];

export interface Grab {
  /** The particle held by the pointer. */
  index: number;
  /** Where the pointer wants it, in canvas px. */
  x: number;
  y: number;
}

export interface Cloth {
  cols: number;
  rows: number;
  /** The strip's width in px. */
  width: number;
  /** Height of the fold over the top edge, in px. */
  top: number;
  /** The strip's height from the top of the fold to the longest point of the tail, in px. */
  length: number;
  /** x of the strip's left edge at rest. */
  originX: number;
  particles: Particle[];
  links: Link[];
  straights: Straight[];
  /** Per particle: how far it may be from the pinned particle at the top of its column (its rest distance). */
  tethers: Float64Array;
  /** Per particle: 0 for a pinned or grabbed particle, 1 for a free one. */
  inverseMass: Float64Array;
  /** Triangles, three particle indices each, two per cell, all wound the same way (normal toward the viewer at rest). */
  triangles: Uint16Array;
  /** Simulation time in seconds: it phases the wind. */
  t: number;
  /** Seed of the wind's noise, so two strips do not sway in step. */
  seed: number;
  /** Extra wind from the pointer, decaying; 0 is calm. */
  gust: number;
  /** Which way the pointer's gust blows along x: -1 (to the left) to 1 (to the right). */
  gustDir: number;
  /** Time not yet simulated, in seconds (less than one sub-step). */
  pending: number;
  /** The fastest particle of the last sub-step, in px/s. */
  speed: number;
  grab: Grab | null;
  /** Scratch space for the sub-step and the renderer (allocated once). */
  scratch: {
    ax: Float64Array;
    ay: Float64Array;
    az: Float64Array;
    nx: Float64Array;
    ny: Float64Array;
    nz: Float64Array;
  };
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
/** The simulation always advances in sub-steps of this length (s), whatever the frame rate: it feels the same at 30 and at 60 fps. */
export const SUBSTEP = 1 / 180;
/** At most this many sub-steps per call: after a stall (a busy main thread) the cloth does not race to catch up. */
const MAX_SUBSTEPS = 9;
const ITERATIONS = 4;
/** Velocity kept after one second with no air at all: the strip is heavy and settles slowly. */
const RETAINED_PER_SECOND = 0.6;
/** Air pushing on the cloth along its normal (per second): what makes a sheet catch the wind, and damps its flapping. */
const AERO = 2.6;
/** Air dragging on the cloth in every direction (per second): it sways the strip sideways. */
const SKIN = 0.55;
/** Fraction of the pointer's gust left after one second. */
const GUST_LEFT_PER_SECOND = 0.3;
/** No particle moves faster than this (px/s): a violent drag cannot make the solver blow up. */
const MAX_SPEED = 2400;
/** Each sub-step a grabbed particle covers this fraction of the way to the pointer: it follows with a little weight. */
const GRAB_EASE = 0.3;

/** The wind (px/s). */
const WIND_SIDE = 170;
const WIND_WAVE = 70;
const WIND_TWIST = 320;
const WIND_FLUTTER = 60;
const POINTER_SIDE = 420;

export function createCloth({ width, length, originX, top = 9, cols = 7, rows = 28, notch = 18, phase = 0 }: ClothOptions): Cloth {
  const dx = width / (cols - 1);
  const particles: Particle[] = [];
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      // The swallowtail: each column ends higher toward the middle, its rows evenly spaced down to that end (so the
      // notch is spread over the whole length and no cell folds over another).
      const end = length - notch * (1 - Math.abs(i - (cols - 1) / 2) / ((cols - 1) / 2));
      const y = top + (j * (end - top)) / (rows - 1);
      const x = originX + i * dx;
      particles.push({ x, y, z: 0, px: x, py: y, pz: 0, pin: j === 0, v: y - top });
    }
  }
  const id = (i: number, j: number) => j * cols + i;
  const rest = (a: number, b: number) => {
    const A = particles[a];
    const B = particles[b];
    return A && B ? Math.hypot(A.x - B.x, A.y - B.y) : 0;
  };
  const links: Link[] = [];
  const straights: Straight[] = [];
  const link = (a: number, b: number, k: number) => links.push([a, b, rest(a, b), k]);
  const straight = (a: number, m: number, b: number, k: number) =>
    straights.push([a, m, b, k, (particles[m]?.y ?? 0) - ((particles[a]?.y ?? 0) + (particles[b]?.y ?? 0)) / 2]);
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      // Structural: neighbours in a row and in a column.
      if (i < cols - 1) link(id(i, j), id(i + 1, j), 1);
      if (j < rows - 1) link(id(i, j), id(i, j + 1), 1);
      // Shear, so the strip does not fold like a fan; a little give, so it can twist.
      if (i < cols - 1 && j < rows - 1) {
        link(id(i, j), id(i + 1, j + 1), 0.7);
        link(id(i + 1, j), id(i, j + 1), 0.7);
      }
      // Across the width, once per row: the edges stay the strip's width apart.
      if (i === 0) link(id(0, j), id(cols - 1, j), 1);
      // Straight across the width: a thick cloth barely cups.
      if (i > 0 && i < cols - 1 && j > 0) straight(id(i - 1, j), id(i, j), id(i + 1, j), 0.45);
      // Gently straight along the length: slow, rounded folds instead of creases.
      if (j > 0 && j < rows - 1) straight(id(i, j - 1), id(i, j), id(i, j + 1), 0.14);
    }
  }
  const tethers = new Float64Array(particles.length);
  const inverseMass = new Float64Array(particles.length);
  particles.forEach((p, n) => {
    tethers[n] = rest(n, n % cols);
    inverseMass[n] = p.pin ? 0 : 1;
  });
  const triangles = new Uint16Array((cols - 1) * (rows - 1) * 6);
  let t = 0;
  for (let j = 0; j < rows - 1; j++) {
    for (let i = 0; i < cols - 1; i++) {
      triangles.set([id(i, j), id(i + 1, j), id(i, j + 1), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)], t);
      t += 6;
    }
  }
  const n = particles.length;
  return {
    cols,
    rows,
    width,
    top,
    length,
    originX,
    particles,
    links,
    straights,
    tethers,
    inverseMass,
    triangles,
    t: phase,
    seed: phase * 7.31 + 1.7,
    gust: 0,
    gustDir: 0,
    pending: 0,
    speed: 0,
    grab: null,
    scratch: {
      ax: new Float64Array(n),
      ay: new Float64Array(n),
      az: new Float64Array(n),
      nx: new Float64Array(n),
      ny: new Float64Array(n),
      nz: new Float64Array(n),
    },
  };
}

/** The particle in column `i`, row `j`. */
export function particleAt(cloth: Cloth, i: number, j: number): Particle {
  const p = cloth.particles[j * cloth.cols + i];
  if (!p) throw new RangeError(`no particle at column ${i}, row ${j}`);
  return p;
}

/** The outline of the strip, as its own particles (they move in place): down the left side, along the tail, up the right side. */
export function outline(cloth: Cloth): Particle[] {
  const { cols, rows } = cloth;
  const edge: Particle[] = [];
  for (let j = 0; j < rows; j++) edge.push(particleAt(cloth, 0, j));
  for (let i = 1; i < cols - 1; i++) edge.push(particleAt(cloth, i, rows - 1));
  for (let j = rows - 1; j >= 0; j--) edge.push(particleAt(cloth, cols - 1, j));
  return edge;
}

/**
 * The pointer is over the strip: a puff of wind that fades (`amount` adds to it, up to `max`). `dir` is the way it
 * blows along x (away from the pointer's side: 1 pushes the strip to the right).
 */
export function addGust(cloth: Cloth, amount: number, dir = cloth.gustDir, max = 1.6): void {
  cloth.gust = Math.min(max, cloth.gust + amount);
  cloth.gustDir = Math.max(-1, Math.min(1, cloth.gustDir * 0.5 + dir * 0.5));
}

/** Smooth 1D value noise in [0, 1]. */
function noise(x: number, seed: number): number {
  const i = Math.floor(x);
  const f = x - i;
  const hash = (n: number) => {
    const s = Math.sin(n * 127.1 + seed * 311.7) * 43758.5453;
    return s - Math.floor(s);
  };
  const a = hash(i);
  const b = hash(i + 1);
  return a + (b - a) * f * f * (3 - 2 * f);
}

/** How gusty the ambient air is at time `t`: mostly 0, now and then a soft swell toward 1. */
export function ambientGust(t: number, seed: number): number {
  const g = noise(t * 0.16, seed) * 0.7 + noise(t * 0.41, seed + 5) * 0.3;
  const s = Math.max(0, Math.min(1, (g - 0.5) / 0.35));
  return s * s * (3 - 2 * s);
}

/** One sub-step of SUBSTEP seconds. */
function substep(cloth: Cloth): void {
  const h = SUBSTEP;
  const t = (cloth.t += h);
  const { particles, inverseMass, triangles, top, length, cols, width, seed } = cloth;
  const { ax, ay, az } = cloth.scratch;
  cloth.gust *= Math.pow(GUST_LEFT_PER_SECOND, h);

  // The wind's shape at this instant: a global side breath, and what scales the local waves.
  const ambient = ambientGust(t, seed);
  const strength = 1 + 1.4 * ambient + 0.9 * cloth.gust;
  const side =
    WIND_SIDE * (0.6 * Math.sin(0.83 * t + seed) + 0.4 * Math.sin(1.37 * t + 2 * seed)) * (1 + 0.8 * ambient) +
    POINTER_SIDE * cloth.gust * cloth.gustDir;
  const span = 1 / (length - top);
  const centre = cloth.originX + width / 2;
  const halfWidth = 2 / width;
  const twistPhase = 1.3 * t + seed * 3;
  const cellArea = (width / (cols - 1)) * ((length - top) / (cloth.rows - 1)); // near enough for the narrower middle
  const aero = AERO / (3 * cellArea);

  for (let n = 0; n < particles.length; n++) {
    ax[n] = 0;
    ay[n] = GRAVITY;
    az[n] = 0;
  }

  // Air on every triangle: along its normal, in proportion to its area and to the wind relative to it.
  for (let k = 0; k < triangles.length; k += 3) {
    const ia = triangles[k]!;
    const ib = triangles[k + 1]!;
    const ic = triangles[k + 2]!;
    const A = particles[ia]!;
    const B = particles[ib]!;
    const C = particles[ic]!;
    const ux = B.x - A.x;
    const uy = B.y - A.y;
    const uz = B.z - A.z;
    const vx = C.x - A.x;
    const vy = C.y - A.y;
    const vz = C.z - A.z;
    // Twice the area along the normal.
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    const area2 = Math.sqrt(nx * nx + ny * ny + nz * nz);
    if (area2 < 1e-9) continue;
    const cx = (A.x + B.x + C.x) / 3;
    const cy = (A.y + B.y + C.y) / 3;
    const along = Math.max(0, (cy - top) * span); // 0 at the top, 1 at the tail
    const across = (cx - centre) * halfWidth; // -1 at the left edge, 1 at the right
    // Waves that travel down the strip, a twist that rolls it, and a flutter that grows toward the tail.
    const wz =
      strength *
      (WIND_WAVE * Math.sin(0.045 * cy - 1.9 * t + seed) +
        WIND_TWIST * across * Math.sin(twistPhase + 0.018 * cy) +
        WIND_FLUTTER * along * along * Math.sin(3.4 * t - 0.11 * cy + seed));
    const wx = side + strength * 40 * along * Math.sin(1.6 * t - 0.05 * cy);
    // The wind relative to the triangle's velocity.
    const rx = wx - (A.x - A.px + B.x - B.px + C.x - C.px) / (3 * h);
    const ry = -(A.y - A.py + B.y - B.py + C.y - C.py) / (3 * h);
    const rz = wz - (A.z - A.pz + B.z - B.pz + C.z - C.pz) / (3 * h);
    // (n . r) n / |n|^2 * |n|/2 = the push along the unit normal times the area.
    const f = (aero * (nx * rx + ny * ry + nz * rz)) / (2 * area2);
    const fx = f * nx;
    const fy = f * ny;
    const fz = f * nz;
    ax[ia] = ax[ia]! + fx;
    ay[ia] = ay[ia]! + fy;
    az[ia] = az[ia]! + fz;
    ax[ib] = ax[ib]! + fx;
    ay[ib] = ay[ib]! + fy;
    az[ib] = az[ib]! + fz;
    ax[ic] = ax[ic]! + fx;
    ay[ic] = ay[ic]! + fy;
    az[ic] = az[ic]! + fz;
  }

  // Move every free particle: momentum (damped), gravity and the air.
  const damping = Math.pow(RETAINED_PER_SECOND, h);
  const limit = MAX_SPEED * h;
  for (let n = 0; n < particles.length; n++) {
    const p = particles[n]!;
    if (inverseMass[n] === 0) continue;
    let vx = (p.x - p.px) * damping;
    let vy = (p.y - p.py) * damping;
    let vz = (p.z - p.pz) * damping;
    const speed = Math.sqrt(vx * vx + vy * vy + vz * vz);
    if (speed > limit) {
      vx *= limit / speed;
      vy *= limit / speed;
      vz *= limit / speed;
    }
    p.px = p.x;
    p.py = p.y;
    p.pz = p.z;
    p.x += vx + (ax[n]! + SKIN * (side - vx / h)) * h * h;
    p.y += vy + (ay[n]! - SKIN * (vy / h)) * h * h;
    p.z += vz + (az[n]! - SKIN * (vz / h)) * h * h;
  }

  // A grabbed particle follows the pointer (within the strip's reach) and drifts back toward the page.
  const grab = cloth.grab;
  if (grab) {
    const p = particles[grab.index]!;
    const anchor = particles[grab.index % cols]!;
    let tx = grab.x - anchor.x;
    let ty = grab.y - anchor.y;
    const reach = (cloth.tethers[grab.index] ?? 0) * 0.98;
    const d = Math.sqrt(tx * tx + ty * ty);
    if (d > reach && d > 0) {
      tx *= reach / d;
      ty *= reach / d;
    }
    p.px = p.x;
    p.py = p.y;
    p.pz = p.z;
    p.x += (anchor.x + tx - p.x) * GRAB_EASE;
    p.y += (anchor.y + ty - p.y) * GRAB_EASE;
    p.z *= 0.97;
  }

  relax(cloth);

  let fastest = 0;
  for (let n = 0; n < particles.length; n++) {
    const p = particles[n]!;
    const dx = p.x - p.px;
    const dy = p.y - p.py;
    const dz = p.z - p.pz;
    fastest = Math.max(fastest, dx * dx + dy * dy + dz * dz);
  }
  cloth.speed = Math.sqrt(fastest) / h;
}

/** Gauss-Seidel relaxation of the links, the straightness constraints and the tethers. */
function relax(cloth: Cloth): void {
  const { particles, links, straights, inverseMass, tethers, cols } = cloth;
  for (let pass = 0; pass < ITERATIONS; pass++) {
    for (let k = 0; k < links.length; k++) {
      const [a, b, rest, stiffness] = links[k]!;
      const wa = inverseMass[a]!;
      const wb = inverseMass[b]!;
      const w = wa + wb;
      if (w === 0) continue;
      const A = particles[a]!;
      const B = particles[b]!;
      const ex = B.x - A.x;
      const ey = B.y - A.y;
      const ez = B.z - A.z;
      const d = Math.sqrt(ex * ex + ey * ey + ez * ez) || 1; // the hot loop: sqrt, not hypot
      const f = ((d - rest) / (d * w)) * stiffness;
      A.x += ex * f * wa;
      A.y += ey * f * wa;
      A.z += ez * f * wa;
      B.x -= ex * f * wb;
      B.y -= ey * f * wb;
      B.z -= ez * f * wb;
    }
    for (let k = 0; k < straights.length; k++) {
      const [a, m, b, stiffness, offset] = straights[k]!;
      const wa = inverseMass[a]!;
      const wm = inverseMass[m]!;
      const wb = inverseMass[b]!;
      const w = wm + 0.25 * (wa + wb);
      if (w === 0) continue;
      const A = particles[a]!;
      const M = particles[m]!;
      const B = particles[b]!;
      const s = stiffness / w;
      const cx = (M.x - (A.x + B.x) * 0.5) * s;
      const cy = (M.y - (A.y + B.y) * 0.5 - offset) * s;
      const cz = (M.z - (A.z + B.z) * 0.5) * s;
      M.x -= cx * wm;
      M.y -= cy * wm;
      M.z -= cz * wm;
      A.x += cx * 0.5 * wa;
      A.y += cy * 0.5 * wa;
      A.z += cz * 0.5 * wa;
      B.x += cx * 0.5 * wb;
      B.y += cy * 0.5 * wb;
      B.z += cz * 0.5 * wb;
    }
  }
  // Long-range tethers: no particle gets farther from the top of its column than it is at rest, so the strip never
  // stretches however few passes the links get.
  for (let n = cols; n < particles.length; n++) {
    if (inverseMass[n] === 0) continue;
    const p = particles[n]!;
    const anchor = particles[n % cols]!;
    const ex = p.x - anchor.x;
    const ey = p.y - anchor.y;
    const ez = p.z - anchor.z;
    const d = Math.sqrt(ex * ex + ey * ey + ez * ez);
    const max = tethers[n]!;
    if (d > max) {
      const s = max / d;
      p.x = anchor.x + ex * s;
      p.y = anchor.y + ey * s;
      p.z = anchor.z + ez * s;
    }
  }
}

/** Puts every particle back where it hangs at rest (a safety net: the simulation produced a non-finite number). */
function reset(cloth: Cloth): void {
  const fresh = createCloth({ width: cloth.width, length: cloth.length, originX: cloth.originX, top: cloth.top, cols: cloth.cols, rows: cloth.rows });
  cloth.particles.forEach((p, n) => Object.assign(p, fresh.particles[n]));
  cloth.grab = null;
  cloth.inverseMass.set(fresh.inverseMass);
  cloth.speed = 0;
}

/**
 * Advances the cloth by `dt` seconds, in fixed sub-steps (time left over is kept for the next call). Returns the number
 * of sub-steps taken.
 */
export function stepCloth(cloth: Cloth, dt: number): number {
  if (!Number.isFinite(dt)) dt = 0;
  cloth.pending = Math.min(cloth.pending + Math.max(0, dt), MAX_SUBSTEPS * SUBSTEP);
  let steps = 0;
  while (cloth.pending >= SUBSTEP - 1e-9) {
    substep(cloth);
    cloth.pending -= SUBSTEP;
    steps++;
  }
  if (!Number.isFinite(cloth.speed)) reset(cloth);
  return steps;
}

/** Runs the simulation forward without drawing, so a strip starts in motion and a still one (reduced motion) hangs naturally. */
export function settle(cloth: Cloth, seconds = 1): void {
  for (let n = Math.round(seconds * 60); n > 0; n--) stepCloth(cloth, 1 / 60);
}

/** The pointer takes hold of the free particle nearest to (x, y) in canvas px, if one is within `radius`. */
export function grabCloth(cloth: Cloth, x: number, y: number, radius = 28): boolean {
  let best = -1;
  let bestDistance = radius * radius;
  cloth.particles.forEach((p, n) => {
    if (p.pin) return;
    const d = (p.x - x) ** 2 + (p.y - y) ** 2;
    if (d < bestDistance) {
      best = n;
      bestDistance = d;
    }
  });
  if (best < 0) return false;
  releaseCloth(cloth);
  const p = cloth.particles[best]!;
  cloth.grab = { index: best, x: p.x, y: p.y };
  cloth.inverseMass[best] = 0;
  return true;
}

/** The pointer moves while it holds the strip: (x, y) in canvas px. */
export function dragCloth(cloth: Cloth, x: number, y: number): void {
  if (cloth.grab) {
    cloth.grab.x = x;
    cloth.grab.y = y;
  }
}

/** The pointer lets go: the strip keeps the momentum it has and swings back. */
export function releaseCloth(cloth: Cloth): void {
  if (!cloth.grab) return;
  cloth.inverseMass[cloth.grab.index] = 1;
  cloth.grab = null;
}

/** Above this speed (px/s) a strip is drawn every frame; below it, the ambient sway looks the same at 30 fps. */
export const CALM_SPEED = 110;
/** Wind is calm when no pointer gust is left. */
export const CALM_GUST = 0.02;

/** Calm: no pointer gust, nothing held, nothing moving fast. The strip still sways, at a lower frame rate. */
export function isCalm(cloth: Cloth): boolean {
  return cloth.gust < CALM_GUST && !cloth.grab && cloth.speed < CALM_SPEED;
}

type Vec3 = [number, number, number];

function normalise(v: Vec3): Vec3 {
  const l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
}

/** The light comes from the upper left, in front. */
const LIGHT = normalise([-0.45, -0.55, 0.7]);
const HALF = normalise([LIGHT[0], LIGHT[1], LIGHT[2] + 1]);
/** The highlight: (n . half)^12 * 30, looked up instead of computed per pixel. */
const SPECULAR = Float32Array.from({ length: 257 }, (_, k) => Math.pow(k / 256, 12) * 30);
/** The ribs of the weave (grosgrain): a faint stripe across the strip every RIB_PERIOD px down it. */
const RIB_PERIOD = 3;
const RIB_DEPTH = 0.035;
const RIBS = Float32Array.from({ length: 64 }, (_, k) => 1 + RIB_DEPTH * Math.sin((k / 64) * 2 * Math.PI));

/** `#rrggbb` as [r, g, b]. */
export function hexToRgb(hex: string): Vec3 {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/**
 * The colour of the cloth where its (unit) normal is n: the base colour lit by the angle to the light (a broad shade,
 * 70 to 106 percent), a tight highlight where it faces the half-vector, and a soft sheen where it turns edge-on (the
 * pile of a velvet catches the light at a grazing angle). Both sides of the cloth are lit as the one facing the
 * viewer. Packed as 0xAABBGGRR, the byte order of a canvas's pixels on a little-endian machine.
 */
export function shade(nx: number, ny: number, nz: number, base: Vec3, rib = 1): number {
  if (nz < 0) {
    nx = -nx;
    ny = -ny;
    nz = -nz;
  }
  const diffuse = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
  const h = nx * HALF[0] + ny * HALF[1] + nz * HALF[2];
  const specular = h > 0 ? SPECULAR[(h * 256) | 0]! : 0;
  const sheen = (1 - nz) * (1 - nz) * 26;
  const m = (0.7 + 0.36 * diffuse) * rib;
  const r = Math.min(255, base[0] * m + specular + sheen) | 0;
  const g = Math.min(255, base[1] * m + specular + sheen) | 0;
  const b = Math.min(255, base[2] * m + specular + sheen) | 0;
  return (0xff000000 | (b << 16) | (g << 8) | r) >>> 0;
}

/** An RGBA pixel buffer the cloth is painted into, with a depth buffer (folds that cross hide each other correctly). */
export interface Raster {
  width: number;
  height: number;
  /** One pixel per element, 0xAABBGGRR; a view on `bytes`. */
  pixels: Uint32Array;
  bytes: Uint8ClampedArray<ArrayBuffer>;
  depth: Float32Array;
  /** The pixels the last frame painted: [x0, y0, x1, y1), or null. */
  box: [number, number, number, number] | null;
}

export function createRaster(width: number, height: number): Raster {
  const bytes = new Uint8ClampedArray(width * height * 4);
  return { width, height, bytes, pixels: new Uint32Array(bytes.buffer), depth: new Float32Array(width * height).fill(-Infinity), box: null };
}

/**
 * Paints the cloth into the raster, `scale` (and `scaleY` down) device pixels per px (the screen's pixel density). Every pixel is shaded
 * from the vertex normals interpolated across its triangle, so the folds are smooth and the light rolls over them as
 * they move. Returns the rectangle that changed since the last frame (the old box and the new one), to upload only
 * that, or null when nothing did.
 */
export function renderCloth(
  cloth: Cloth,
  raster: Raster,
  scale: number,
  base: Vec3,
  scaleY = scale,
): [number, number, number, number] | null {
  const { particles, triangles } = cloth;
  const { nx, ny, nz } = cloth.scratch;
  const { width: W, height: H, pixels, depth } = raster;
  const old = raster.box;
  if (old) {
    for (let y = old[1]; y < old[3]; y++) {
      pixels.fill(0, y * W + old[0], y * W + old[2]);
      depth.fill(-Infinity, y * W + old[0], y * W + old[2]);
    }
  }

  // Vertex normals: the sum of the (area-weighted) normals of the triangles around each particle.
  nx.fill(0);
  ny.fill(0);
  nz.fill(0);
  for (let k = 0; k < triangles.length; k += 3) {
    const ia = triangles[k]!;
    const ib = triangles[k + 1]!;
    const ic = triangles[k + 2]!;
    const A = particles[ia]!;
    const B = particles[ib]!;
    const C = particles[ic]!;
    const ux = B.x - A.x;
    const uy = B.y - A.y;
    const uz = B.z - A.z;
    const vx = C.x - A.x;
    const vy = C.y - A.y;
    const vz = C.z - A.z;
    const tx = uy * vz - uz * vy;
    const ty = uz * vx - ux * vz;
    const tz = ux * vy - uy * vx;
    nx[ia] = nx[ia]! + tx;
    ny[ia] = ny[ia]! + ty;
    nz[ia] = nz[ia]! + tz;
    nx[ib] = nx[ib]! + tx;
    ny[ib] = ny[ib]! + ty;
    nz[ib] = nz[ib]! + tz;
    nx[ic] = nx[ic]! + tx;
    ny[ic] = ny[ic]! + ty;
    nz[ic] = nz[ic]! + tz;
  }

  let bx0 = W;
  let by0 = H;
  let bx1 = 0;
  let by1 = 0;
  const ribScale = 64 / RIB_PERIOD;
  for (let k = 0; k < triangles.length; k += 3) {
    const ia = triangles[k]!;
    const ib = triangles[k + 1]!;
    const ic = triangles[k + 2]!;
    const A = particles[ia]!;
    const B = particles[ib]!;
    const C = particles[ic]!;
    const x0 = A.x * scale;
    const y0 = A.y * scaleY;
    const x1 = B.x * scale;
    const y1 = B.y * scaleY;
    const x2 = C.x * scale;
    const y2 = C.y * scaleY;
    const area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
    if (Math.abs(area) < 1e-6) continue;
    const inv = 1 / area;
    const minX = Math.max(0, Math.floor(Math.min(x0, x1, x2)));
    const maxX = Math.min(W - 1, Math.ceil(Math.max(x0, x1, x2)));
    const minY = Math.max(0, Math.floor(Math.min(y0, y1, y2)));
    const maxY = Math.min(H - 1, Math.ceil(Math.max(y0, y1, y2)));
    if (minX > maxX || minY > maxY) continue;
    // Barycentric weights of A and B are linear in the pixel's position: start them at the row's first pixel, step them.
    const da = (y1 - y2) * inv;
    const db = (y2 - y0) * inv;
    const nxa = nx[ia]!;
    const nya = ny[ia]!;
    const nza = nz[ia]!;
    const nxb = nx[ib]!;
    const nyb = ny[ib]!;
    const nzb = nz[ib]!;
    const nxc = nx[ic]!;
    const nyc = ny[ic]!;
    const nzc = nz[ic]!;
    for (let py = minY; py <= maxY; py++) {
      const cy = py + 0.5;
      const cx = minX + 0.5;
      let wa = ((x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)) * inv;
      let wb = ((x2 - cx) * (y0 - cy) - (x0 - cx) * (y2 - cy)) * inv;
      let index = py * W + minX;
      for (let px = minX; px <= maxX; px++, index++, wa += da, wb += db) {
        const wc = 1 - wa - wb;
        if (wa < -1e-4 || wb < -1e-4 || wc < -1e-4) continue;
        const z = wa * A.z + wb * B.z + wc * C.z;
        if (z < depth[index]!) continue;
        depth[index] = z;
        let mx = wa * nxa + wb * nxb + wc * nxc;
        let my = wa * nya + wb * nyb + wc * nyc;
        let mz = wa * nza + wb * nzb + wc * nzc;
        const l = 1 / (Math.sqrt(mx * mx + my * my + mz * mz) || 1);
        mx *= l;
        my *= l;
        mz *= l;
        // The ribs fade as the cloth turns away: seen at a slant they would be finer than a pixel.
        const v = wa * A.v + wb * B.v + wc * C.v;
        const rib = 1 + (RIBS[((v * ribScale) | 0) & 63]! - 1) * mz * mz;
        pixels[index] = shade(mx, my, mz, base, rib);
      }
    }
    bx0 = Math.min(bx0, minX);
    by0 = Math.min(by0, minY);
    bx1 = Math.max(bx1, maxX + 1);
    by1 = Math.max(by1, maxY + 1);
  }
  raster.box = bx1 > bx0 ? [bx0, by0, bx1, by1] : null;
  const now = raster.box;
  if (!old) return now;
  if (!now) return old;
  return [Math.min(old[0], now[0]), Math.min(old[1], now[1]), Math.max(old[2], now[2]), Math.max(old[3], now[3])];
}

export interface LabelPose {
  /** Where the label's top centre sits, in the canvas's coordinates. */
  x: number;
  y: number;
  /** Radians: the label turns with the strip's centre line. */
  angle: number;
  /** The strip narrows when it turns edge-on, and the label narrows with it. */
  scaleX: number;
  /** The strip looks shorter when it swings toward or away from the viewer, and the label with it. */
  scaleY: number;
}

/** Where the label (a word and an icon printed on the cloth) goes, so that it rides the cloth instead of floating over it. */
export function labelPose(cloth: Cloth): LabelPose {
  const { cols, rows, width } = cloth;
  // Rows by their place down the strip, so the label sits in the same place whatever the grid.
  const row = (f: number) => Math.round(f * (rows - 1));
  const lo = Math.floor((cols - 1) / 2);
  const hi = Math.ceil((cols - 1) / 2);
  const centre = (j: number) => {
    const a = particleAt(cloth, lo, j);
    const b = particleAt(cloth, hi, j);
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  };
  const j0 = row(0.26);
  const j1 = row(0.69);
  const p0 = centre(j0);
  const p1 = centre(j1);
  const jw = row(0.47);
  const left = particleAt(cloth, 0, jw);
  const right = particleAt(cloth, cols - 1, jw);
  const restSpan = particleAt(cloth, lo, j1).v - particleAt(cloth, lo, j0).v;
  return {
    x: p0.x,
    y: p0.y,
    angle: Math.atan2(p1.x - p0.x, p1.y - p0.y),
    scaleX: Math.max(0.35, Math.min(1, Math.hypot(right.x - left.x, right.y - left.y) / width)),
    scaleY: Math.max(0.6, Math.min(1, Math.hypot(p1.x - p0.x, p1.y - p0.y) / restSpan)),
  };
}
