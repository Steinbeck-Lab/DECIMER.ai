import { expect, test } from 'vitest';
import {
  addGust,
  createCloth,
  createRaster,
  dragCloth,
  grabCloth,
  hexToRgb,
  isCalm,
  labelPose,
  outline,
  particleAt,
  releaseCloth,
  renderCloth,
  settle,
  shade,
  stepCloth,
  SUBSTEP,
  type Cloth,
} from './cloth';

const OPTIONS = { width: 46, length: 158, originX: 52 };

function tail(cloth: Cloth) {
  return particleAt(cloth, 0, cloth.rows - 1);
}

/** The worst relative stretch or squeeze of the structural links (neighbours in a row or a column). */
function worstStretch(cloth: Cloth): number {
  let worst = 0;
  for (const [a, b, rest, stiffness] of cloth.links) {
    const A = cloth.particles[a];
    const B = cloth.particles[b];
    if (!A || !B || stiffness < 1 || rest > 12) continue;
    worst = Math.max(worst, Math.abs(Math.hypot(A.x - B.x, A.y - B.y, A.z - B.z) - rest) / rest);
  }
  return worst;
}

/** The largest distance between the same particles of two strips. */
function apart(a: Cloth, b: Cloth): number {
  return Math.max(...a.particles.map((p, n) => Math.hypot(p.x - b.particles[n]!.x, p.y - b.particles[n]!.y, p.z - b.particles[n]!.z)));
}

test('a strip is a grid of particles pinned along its top row only, with a swallowtail that does not fold over itself', () => {
  const cloth = createCloth(OPTIONS);
  expect(cloth.particles).toHaveLength(cloth.cols * cloth.rows);
  const pinned = cloth.particles.filter((p) => p.pin);
  expect(pinned).toHaveLength(cloth.cols);
  expect(pinned.every((p) => p.y === cloth.top)).toBe(true);
  // The tail is swallow-tailed: the middle of the last row sits higher than its corners...
  const mid = (cloth.cols - 1) / 2;
  expect(particleAt(cloth, mid, cloth.rows - 1).y).toBeLessThan(tail(cloth).y - 15);
  // ...and every column still runs strictly downward, so no cell of the notch overlaps another.
  for (let i = 0; i < cloth.cols; i++) {
    for (let j = 1; j < cloth.rows; j++) expect(particleAt(cloth, i, j).y).toBeGreaterThan(particleAt(cloth, i, j - 1).y);
  }
});

test('pinned particles never move, whatever the wind or the pointer does', () => {
  const cloth = createCloth(OPTIONS);
  const before = cloth.particles.filter((p) => p.pin).map((p) => [p.x, p.y, p.z]);
  addGust(cloth, 1.6, -1);
  grabCloth(cloth, tail(cloth).x, tail(cloth).y);
  dragCloth(cloth, 140, 20);
  settle(cloth, 4);
  releaseCloth(cloth);
  settle(cloth, 4);
  const after = cloth.particles.filter((p) => p.pin).map((p) => [p.x, p.y, p.z]);
  expect(after).toEqual(before);
});

test('the strip hangs: its tail stays near its length, never stretched past it', () => {
  const cloth = createCloth(OPTIONS);
  for (let n = 0; n < 600; n++) {
    stepCloth(cloth, 1 / 60);
    expect(tail(cloth).y).toBeGreaterThan(OPTIONS.length * 0.85);
    expect(tail(cloth).y).toBeLessThanOrEqual(OPTIONS.length + 1e-6);
  }
});

test('it moves like cloth at rest too: the tail sways, the strip twists and the light rolls over it', () => {
  const cloth = createCloth(OPTIONS);
  settle(cloth);
  const xs: number[] = [];
  const widths: number[] = [];
  const shades = new Set<number>();
  const raster = createRaster(150, 236);
  for (let n = 0; n < 20 * 30; n++) {
    stepCloth(cloth, 1 / 30);
    xs.push(tail(cloth).x);
    widths.push(labelPose(cloth).scaleX);
    if (n % 30 === 0) {
      renderCloth(cloth, raster, 1, [194, 126, 53]);
      shades.add(raster.pixels[80 * 150 + 75]!);
    }
  }
  expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(10);
  expect(Math.min(...widths)).toBeLessThan(0.92);
  // The same spot of the cloth is painted in many different shades over twenty seconds.
  expect(shades.size).toBeGreaterThan(8);
  // ...and all of that without a pointer: the ambient sway is calm (it may be drawn at 30 fps) most of the time.
  expect(isCalm(cloth)).toBe(true);
});

test('stable over a long, rough run: no NaN, links within bounds, nothing flies off the canvas', () => {
  const cloth = createCloth({ ...OPTIONS, phase: 3.7 });
  const bad: string[] = [];
  // Two minutes of uneven frames (from a fast screen to a stalling one), gusts and pulls from both sides.
  for (let n = 0; n < 4000; n++) {
    const dt = [1 / 144, 1 / 60, 1 / 30, 0.1, 1 / 60][n % 5]!;
    if (n % 400 === 0) addGust(cloth, 1.6, n % 800 === 0 ? 1 : -1);
    if (n % 700 === 100) grabCloth(cloth, 60, 120);
    if (cloth.grab) dragCloth(cloth, 4 + ((n * 7) % 140), 10 + ((n * 13) % 220));
    if (n % 700 === 300) releaseCloth(cloth);
    stepCloth(cloth, dt);
    for (const p of cloth.particles) {
      if (!Number.isFinite(p.x + p.y + p.z)) bad.push(`NaN at frame ${n}`);
      if (p.x < -60 || p.x > 210 || p.y > OPTIONS.length + 1e-6) bad.push(`(${p.x}, ${p.y}) at frame ${n}`);
    }
  }
  expect(bad).toEqual([]);
  releaseCloth(cloth);
  settle(cloth, 3);
  expect(worstStretch(cloth)).toBeLessThan(0.1);
});

test('the simulation is deterministic: the same strip and the same steps end in the same place', () => {
  const a = createCloth({ ...OPTIONS, phase: 3.7 });
  const b = createCloth({ ...OPTIONS, phase: 3.7 });
  settle(a, 2);
  settle(b, 2);
  expect(a.particles).toEqual(b.particles);
  // ...and two ribbons with different phases do not sway in step.
  const c = createCloth(OPTIONS);
  settle(c, 2);
  expect(apart(a, c)).toBeGreaterThan(1);
});

test('the frame rate does not change the motion: the cloth moves in fixed sub-steps', () => {
  const at30 = createCloth(OPTIONS);
  const at60 = createCloth(OPTIONS);
  let steps = 0;
  for (let n = 0; n < 90; n++) steps += stepCloth(at30, 1 / 30);
  for (let n = 0; n < 180; n++) stepCloth(at60, 1 / 60);
  expect(steps).toBe(Math.round(3 / SUBSTEP));
  expect(apart(at30, at60)).toBeLessThan(1e-6);
  // A long stall (a busy page) is not caught up in one go.
  expect(stepCloth(at30, 5)).toBeLessThan(12);
});

test('the pointer gust blows the strip away from the pointer, and then dies down', () => {
  const calm = createCloth(OPTIONS);
  const fromLeft = createCloth(OPTIONS);
  const fromRight = createCloth(OPTIONS);
  for (const c of [calm, fromLeft, fromRight]) settle(c, 3);
  addGust(fromLeft, 1, 1);
  addGust(fromRight, 1, -1);
  expect(isCalm(fromLeft)).toBe(false);
  let right = 0;
  let left = 0;
  for (let n = 0; n < 60; n++) {
    for (const c of [calm, fromLeft, fromRight]) stepCloth(c, 1 / 60);
    right = Math.max(right, tail(fromLeft).x - tail(calm).x);
    left = Math.max(left, tail(calm).x - tail(fromRight).x);
  }
  expect(right).toBeGreaterThan(12);
  expect(left).toBeGreaterThan(12);
  // The puff decays by itself...
  for (let n = 0; n < 600; n++) stepCloth(fromLeft, 1 / 60);
  expect(fromLeft.gust).toBeLessThan(0.02);
  // ...and cannot pile up without bound while the pointer keeps moving.
  for (let n = 0; n < 100; n++) addGust(fromLeft, 0.08, 1);
  expect(fromLeft.gust).toBeLessThanOrEqual(1.6);
});

test('a pull moves the cloth toward the pointer, within its reach; let go, it swings back to where it would hang', () => {
  const pulled = createCloth(OPTIONS);
  const twin = createCloth(OPTIONS);
  settle(pulled);
  settle(twin);
  const end = tail(pulled);
  // Nothing to grab far from the cloth.
  expect(grabCloth(pulled, 5, 230)).toBe(false);
  expect(grabCloth(pulled, end.x + 2, end.y - 2)).toBe(true);
  expect(isCalm(pulled)).toBe(false);
  dragCloth(pulled, 130, 120);
  for (let n = 0; n < 60; n++) {
    stepCloth(pulled, 1 / 60);
    stepCloth(twin, 1 / 60);
  }
  expect(end.x).toBeGreaterThan(110);
  // Out of reach, it stops where the strip ends: the cloth never stretches.
  dragCloth(pulled, 140, 400);
  for (let n = 0; n < 60; n++) {
    stepCloth(pulled, 1 / 60);
    stepCloth(twin, 1 / 60);
  }
  expect(Math.hypot(end.x - particleAt(pulled, 0, 0).x, end.y - particleAt(pulled, 0, 0).y)).toBeLessThanOrEqual(pulled.tethers[(pulled.rows - 1) * pulled.cols]! + 1e-6);
  releaseCloth(pulled);
  expect(pulled.grab).toBeNull();
  // It swings back: right after the release it is far from its twin, a while later it moves with it again.
  expect(apart(pulled, twin)).toBeGreaterThan(40);
  for (let n = 0; n < 60 * 10; n++) {
    stepCloth(pulled, 1 / 60);
    stepCloth(twin, 1 / 60);
  }
  expect(apart(pulled, twin)).toBeLessThan(3);
});

test('the label rides the cloth: upright and full size at rest, turning and narrowing with the strip', () => {
  const cloth = createCloth(OPTIONS);
  const rest = labelPose(cloth);
  expect(rest.angle).toBeCloseTo(0, 5);
  expect(rest.scaleX).toBeCloseTo(1, 5);
  expect(rest.scaleY).toBeCloseTo(1, 5);
  expect(rest.x).toBeCloseTo(OPTIONS.originX + OPTIONS.width / 2, 5);
  // Lean the lower half of the strip sideways: the centre line leans, and the pose follows.
  for (let j = 10; j < cloth.rows; j++) for (let i = 0; i < cloth.cols; i++) particleAt(cloth, i, j).x += (j - 9) * 2;
  expect(Math.abs(labelPose(cloth).angle)).toBeGreaterThan(0.1);
  // Turn the middle of the strip edge-on (about its long axis): the label narrows.
  const twisted = createCloth(OPTIONS);
  for (let j = 0; j < twisted.rows; j++) {
    for (let i = 0; i < twisted.cols; i++) {
      const p = particleAt(twisted, i, j);
      const r = p.x - (OPTIONS.originX + OPTIONS.width / 2);
      p.x = OPTIONS.originX + OPTIONS.width / 2 + r * Math.cos(1);
      p.z = r * Math.sin(1);
    }
  }
  expect(labelPose(twisted).scaleX).toBeLessThan(0.6);
});

test('shading: an opaque colour within range, brighter where the cloth faces the light, the same on both sides', () => {
  const base = hexToRgb('#C27E35');
  expect(base).toEqual([194, 126, 53]);
  const channels = (c: number) => [c & 255, (c >> 8) & 255, (c >> 16) & 255, c >>> 24];
  const level = (c: number) => channels(c).slice(0, 3).reduce((s, x) => s + x, 0);
  const facing = shade(0, 0, 1, base);
  expect(channels(facing)[3]).toBe(255);
  // Toward the light (up and left, in front) is brighter than away from it.
  const toward = shade(-0.45, -0.55, 0.7, base);
  const away = shade(0.6, 0.6, 0.53, base);
  expect(level(toward)).toBeGreaterThan(level(away));
  expect(shade(0, 0, -1, base)).toBe(facing);
  for (const c of [facing, toward, away]) for (const x of channels(c)) expect(x).toBeLessThanOrEqual(255);
});

test('the renderer paints the strip and nothing else, smoothly, and reports only what changed', () => {
  const cloth = createCloth(OPTIONS);
  const raster = createRaster(150, 236);
  const box = renderCloth(cloth, raster, 1, [194, 126, 53]);
  expect(box).not.toBeNull();
  const at = (x: number, y: number) => raster.pixels[y * 150 + x]!;
  // Inside the strip: opaque; beside it and below the notch: empty.
  expect(at(75, 80) >>> 24).toBe(255);
  expect(at(20, 80)).toBe(0);
  expect(at(75, 150)).toBe(0);
  // A flat strip at rest is one smooth colour (no facets) apart from the faint ribs of the weave.
  const row = Array.from({ length: 40 }, (_, k) => at(55 + k, 80) & 255);
  expect(Math.max(...row) - Math.min(...row)).toBe(0);
  const column = Array.from({ length: 100 }, (_, k) => at(75, 20 + k) & 255);
  expect(Math.max(...column) - Math.min(...column)).toBeLessThan(12);
  // The changed box covers the strip; a second frame of the same cloth is the same box.
  const [x0, y0, x1, y1] = box!;
  expect(x0).toBeLessThanOrEqual(52);
  expect(x1).toBeGreaterThanOrEqual(98);
  expect(y0).toBeLessThanOrEqual(cloth.top);
  expect(y1).toBeGreaterThanOrEqual(OPTIONS.length);
  expect(renderCloth(cloth, raster, 1, [194, 126, 53])).toEqual(box);
  // At twice the density, twice the pixels.
  const sharp = createRaster(300, 472);
  expect(renderCloth(cloth, sharp, 2, [194, 126, 53])?.[2]).toBeGreaterThanOrEqual(196);
});

test('the outline runs down the left side, along the swallowtail and up the right side, out of the strip\'s own particles', () => {
  const cloth = createCloth(OPTIONS);
  const edge = outline(cloth);
  expect(edge).toHaveLength(2 * cloth.rows + cloth.cols - 2);
  expect(edge[0]).toBe(particleAt(cloth, 0, 0));
  expect(edge.at(-1)).toBe(particleAt(cloth, cloth.cols - 1, 0));
  const before = edge[10]!.y;
  stepCloth(cloth, 1 / 60);
  expect(edge[10]!.y).not.toBe(before);
});
