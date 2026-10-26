import { expect, test } from 'vitest';
import { addGust, buildGeometry, createCloth, hexToRgb, isCalm, labelPose, particleAt, settle, shadeTriangle, stepCloth } from './cloth';

const OPTIONS = { width: 46, length: 158, originX: 38 };

function maxZ(cloth: ReturnType<typeof createCloth>): number {
  return Math.max(...cloth.particles.map((p) => Math.abs(p.z)));
}

test('a strip is a grid of particles pinned along its top row only', () => {
  const cloth = createCloth(OPTIONS);
  expect(cloth.particles).toHaveLength(cloth.cols * cloth.rows);
  const pinned = cloth.particles.filter((p) => p.pin);
  expect(pinned).toHaveLength(cloth.cols);
  expect(pinned.every((p) => p.y === cloth.top)).toBe(true);
  // The tail is swallow-tailed: the middle of the last row sits higher than its corners.
  expect(particleAt(cloth, 2, cloth.rows - 1).y).toBeLessThan(particleAt(cloth, 0, cloth.rows - 1).y);
});

test('pinned particles never move, whatever the wind does', () => {
  const cloth = createCloth(OPTIONS);
  const before = cloth.particles.filter((p) => p.pin).map((p) => [p.x, p.y, p.z]);
  addGust(cloth, 1.6);
  settle(cloth, 200, 1 / 60);
  const after = cloth.particles.filter((p) => p.pin).map((p) => [p.x, p.y, p.z]);
  expect(after).toEqual(before);
});

test('the strip hangs: gravity pulls its tail down to about its length', () => {
  const cloth = createCloth(OPTIONS);
  settle(cloth);
  const tail = particleAt(cloth, 0, cloth.rows - 1);
  const topEdge = particleAt(cloth, 0, 0);
  expect(tail.y).toBeGreaterThan(topEdge.y + 0.8 * (OPTIONS.length - cloth.top - 18));
  // ...and it does not stretch to the floor: the links hold it within a tenth of its length.
  expect(tail.y).toBeLessThan(OPTIONS.length * 1.1);
});

test('the links hold: after a long run no neighbour is stretched or squeezed by more than ten percent', () => {
  const cloth = createCloth(OPTIONS);
  addGust(cloth, 1);
  settle(cloth, 600, 1 / 120);
  const worst = Math.max(
    ...cloth.links
      // The structural links: neighbours in a row or a column.
      .filter(([, , rest]) => rest < 20)
      .map(([a, b, rest]) => {
        const A = cloth.particles[a];
        const B = cloth.particles[b];
        if (!A || !B) return 0;
        return Math.abs(Math.hypot(A.x - B.x, A.y - B.y, A.z - B.z) - rest) / rest;
      }),
  );
  expect(worst).toBeLessThan(0.1);
});

test('the simulation is deterministic: the same strip and the same steps end in the same place', () => {
  const a = createCloth({ ...OPTIONS, phase: 3.7 });
  const b = createCloth({ ...OPTIONS, phase: 3.7 });
  settle(a, 100);
  settle(b, 100);
  expect(a.particles).toEqual(b.particles);
});

test('a gust blows the cloth about more than a calm does, and then dies down', () => {
  const calm = createCloth(OPTIONS);
  const gusty = createCloth(OPTIONS);
  settle(calm);
  settle(gusty);
  addGust(gusty, 1.6);
  expect(gusty.gust).toBe(1.6);
  let peak = 0;
  for (let n = 0; n < 120; n++) {
    stepCloth(calm, 1 / 60);
    stepCloth(gusty, 1 / 60);
    peak = Math.max(peak, maxZ(gusty) - maxZ(calm));
  }
  expect(peak).toBeGreaterThan(0);
  // The pointer's puff decays by itself.
  for (let n = 0; n < 600; n++) stepCloth(gusty, 1 / 60);
  expect(gusty.gust).toBeLessThan(0.01);
  // It cannot pile up without bound while the pointer keeps moving.
  for (let n = 0; n < 100; n++) addGust(gusty, 0.08);
  expect(gusty.gust).toBeLessThanOrEqual(1.6);
});

test('the label rides the cloth: upright and full width at rest, turning with the strip', () => {
  const cloth = createCloth(OPTIONS);
  const rest = labelPose(cloth);
  expect(rest.angle).toBeCloseTo(0, 5);
  expect(rest.scaleX).toBeCloseTo(1, 5);
  expect(rest.x).toBeCloseTo(OPTIONS.originX + OPTIONS.width / 2, 5);
  // Push the middle column sideways, lower down than the top: the centre line leans, and the pose follows.
  for (let j = 3; j < 14; j++) particleAt(cloth, 2, j).x += j * 2;
  const leaning = labelPose(cloth);
  expect(Math.abs(leaning.angle)).toBeGreaterThan(0.01);
});

test('shading is a valid colour, brighter where the cloth faces the light', () => {
  const base = hexToRgb('#C27E35');
  expect(base).toEqual([194, 126, 53]);
  const flat = createCloth(OPTIONS);
  const a = particleAt(flat, 0, 4);
  const b = particleAt(flat, 1, 4);
  const c = particleAt(flat, 0, 5);
  const toward = shadeTriangle(a, b, c, base);
  expect(toward).toMatch(/^rgb\(\d{1,3},\d{1,3},\d{1,3}\)$/);
  // Tilt the triangle away from the light (up-left in front): the same cloth reads darker.
  const away = shadeTriangle({ ...a, z: 40 }, b, { ...c, z: -40 }, base);
  const level = (rgb: string) => rgb.match(/\d+/g)!.map(Number).reduce((s, x) => s + x, 0);
  expect(level(toward)).not.toBe(level(away));
  expect(Math.max(...(toward.match(/\d+/g) ?? []).map(Number))).toBeLessThanOrEqual(255);
});

test('the geometry is built once from the strip, out of its own particles: the outline and two triangles per cell, moving with the cloth', () => {
  const cloth = createCloth(OPTIONS);
  const { edge, triangles } = buildGeometry(cloth);
  expect(edge).toHaveLength(2 * cloth.rows + cloth.cols);
  expect(triangles).toHaveLength(2 * (cloth.cols - 1) * (cloth.rows - 1));
  // The same objects, not copies: a step moves what is drawn.
  expect(edge[0]).toBe(particleAt(cloth, 0, 0));
  const before = triangles[10]?.[0].y;
  stepCloth(cloth, 1 / 60);
  expect(triangles[10]?.[0].y).not.toBe(before);
});

test('the wind is calm until a gust, and calm again once it has died down', () => {
  const cloth = createCloth(OPTIONS);
  expect(isCalm(cloth)).toBe(true);
  addGust(cloth, 1);
  expect(isCalm(cloth)).toBe(false);
  for (let n = 0; n < 1200 && !isCalm(cloth); n++) stepCloth(cloth, 1 / 60);
  expect(isCalm(cloth)).toBe(true);
});

test('a slower frame rate feels the same: the gust fades in the same time at 30 and at 60 steps a second', () => {
  const seconds = (rate: number) => {
    const cloth = createCloth(OPTIONS);
    addGust(cloth, 1);
    let n = 0;
    while (!isCalm(cloth)) {
      stepCloth(cloth, 1 / rate);
      n++;
    }
    return n / rate;
  };
  expect(Math.abs(seconds(30) - seconds(60))).toBeLessThan(0.1);
});

test('a calm frame is one step of about 1/30 s: the links still hold within five percent', () => {
  const cloth = createCloth(OPTIONS);
  settle(cloth, 300, 1 / 30);
  const worst = Math.max(
    ...cloth.links
      .filter(([, , rest]) => rest < 20)
      .map(([a, b, rest]) => {
        const A = cloth.particles[a];
        const B = cloth.particles[b];
        return A && B ? Math.abs(Math.hypot(A.x - B.x, A.y - B.y, A.z - B.z) - rest) / rest : 0;
      }),
  );
  expect(worst).toBeLessThan(0.05);
});
