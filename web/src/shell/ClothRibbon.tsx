import { useEffect, useRef, type CSSProperties, type PointerEvent, type ReactNode } from 'react';
import {
  addGust,
  buildGeometry,
  createCloth,
  hexToRgb,
  isCalm,
  labelPose,
  settle,
  shadeTriangle,
  stepCloth,
  type Cloth,
  type ClothGeometry,
} from './cloth';

/** The canvas is bigger than the strip: room for the sway and for the soft cast shadow. */
const CANVAS_W = 122;
const CANVAS_H = 220;
/** The strip's width, and where its left edge sits in the canvas. */
const STRIP_W = 46;
const STRIP_X = 38;
/** The link is exactly the strip's width (blank space beside the cloth must not be a link): the canvas starts where the
 *  strip's left edge is, and the label starts at the strip's middle. */
const CANVAS_LEFT = STRIP_X;
const LABEL_LEFT = STRIP_W / 2;
/** While the wind is calm the strip only breathes: it is drawn about 30 times a second. A gust draws every frame. */
const CALM_FRAME_MS = 29;

interface Props {
  href: string;
  /** The link's accessible name, and the word printed on the cloth. */
  name: string;
  /** Cloth colour (`#rrggbb`), the colour of the label printed on it, and how dark the fold over the top edge is (0 to 1). */
  color: string;
  ink: string;
  foldShade: number;
  /** Strip height in px. */
  length: number;
  /** The icon printed under the word. */
  icon: ReactNode;
  /** Start of the wind's cycle in seconds, so two ribbons do not sway in step. */
  phase?: number;
  external?: boolean;
}

function draw(
  ctx: CanvasRenderingContext2D,
  cloth: Cloth,
  geometry: ClothGeometry,
  base: [number, number, number],
  fold: string,
  label: HTMLElement,
) {
  const { top, width } = cloth;
  ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);

  const outline = new Path2D();
  geometry.edge.forEach((p, q) => (q ? outline.lineTo(p.x, p.y) : outline.moveTo(p.x, p.y)));
  outline.closePath();

  // The soft cast shadow of the silhouette (an offset and a blur: the strip hangs off the page).
  ctx.save();
  ctx.shadowColor = 'rgba(30,50,38,.28)';
  ctx.shadowBlur = 10;
  ctx.shadowOffsetX = 3;
  ctx.shadowOffsetY = 6;
  ctx.fillStyle = `rgb(${base.join(',')})`;
  ctx.fill(outline);
  ctx.restore();

  // The cloth's thickness: the same silhouette, darker, one step down and to the right.
  ctx.save();
  ctx.translate(1.6, 1.8);
  ctx.fillStyle = `rgb(${base.map((x) => Math.round(x * 0.55)).join(',')})`;
  ctx.fill(outline);
  ctx.restore();

  ctx.lineWidth = 1.1;
  ctx.lineJoin = 'round';
  for (const [p, q, r] of geometry.triangles) {
    const colour = shadeTriangle(p, q, r, base);
    ctx.fillStyle = colour;
    ctx.strokeStyle = colour;
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(q.x, q.y);
    ctx.lineTo(r.x, r.y);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }

  // The fold over the top edge.
  ctx.fillStyle = fold;
  ctx.beginPath();
  ctx.roundRect(STRIP_X - 3, 0, width + 6, top + 1, [0, 0, 3, 3]);
  ctx.fill();

  // The label follows the centre line. Only a transform is written (it is a compositor layer, see shell.css): nothing is
  // read back, so the loop never forces a layout.
  const pose = labelPose(cloth);
  label.style.transform =
    `translate(${(pose.x - CANVAS_LEFT - LABEL_LEFT).toFixed(2)}px, ${pose.y.toFixed(2)}px) rotate(${(-pose.angle).toFixed(4)}rad) ` +
    `scaleX(${pose.scaleX.toFixed(3)}) translateX(-50%)`;
}

/**
 * A ribbon bookmark hanging from the top edge of the page: a cloth simulated and drawn on a canvas, a word and an
 * icon printed on it that follow the cloth, and a real link around the lot. The pointer (or keyboard focus) raises a
 * gust. The cloth hangs still under `prefers-reduced-motion`, and the simulation stops while the tab is hidden.
 */
export function ClothRibbon({ href, name, color, ink, foldShade, length, icon, phase = 0, external = false }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);
  const clothRef = useRef<Cloth | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const label = labelRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !label || !ctx) return; // no canvas (a test, an old browser): the label sits still on the page

    const base = hexToRgb(color);
    const fold = `rgb(${base.map((x) => Math.round(x * foldShade)).join(',')})`;
    const cloth = createCloth({ width: STRIP_W, length, originX: STRIP_X, phase });
    const geometry = buildGeometry(cloth);
    clothRef.current = cloth;
    settle(cloth);
    const paint = () => draw(ctx, cloth, geometry, base, fold, label);

    // The backing store follows the screen's pixel density, also when the window moves to another screen.
    let densityQuery: MediaQueryList | null = null;
    const onDensity = () => {
      resize();
      paint();
    };
    const resize = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(CANVAS_W * dpr);
      canvas.height = Math.round(CANVAS_H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      densityQuery?.removeEventListener('change', onDensity);
      densityQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      densityQuery.addEventListener('change', onDensity);
    };
    resize();
    paint();

    const still = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let lastStep = 0;
    let lastDraw = 0;
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const calm = isCalm(cloth);
      if (calm && now - lastDraw < CALM_FRAME_MS) return;
      const h = Math.min(0.033, (now - lastStep) / 1000);
      lastStep = lastDraw = now;
      // A calm frame is one step (it is twice as long as a gust's two): the strip only breathes.
      if (calm) stepCloth(cloth, h);
      else {
        stepCloth(cloth, h / 2);
        stepCloth(cloth, h / 2);
      }
      paint();
    };
    const stop = () => {
      cancelAnimationFrame(frame);
      frame = 0;
    };
    // Running means: motion is welcome and the tab is on screen.
    const sync = () => {
      const run = !still.matches && document.visibilityState === 'visible';
      if (run && !frame) {
        lastStep = lastDraw = performance.now();
        frame = requestAnimationFrame(tick);
      } else if (!run && frame) stop();
    };
    sync();
    document.addEventListener('visibilitychange', sync);
    still.addEventListener('change', sync);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', sync);
      still.removeEventListener('change', sync);
      densityQuery?.removeEventListener('change', onDensity);
      clothRef.current = null;
    };
  }, [color, foldShade, length, phase]);

  const gust = (event: PointerEvent) => {
    const cloth = clothRef.current;
    if (!cloth) return;
    if (event.type === 'pointerenter') cloth.gust = Math.max(cloth.gust, 1);
    else addGust(cloth, 0.08);
  };

  return (
    <a
      className="dc-ribbon"
      href={href}
      aria-label={name}
      target={external ? '_blank' : undefined}
      rel={external ? 'noreferrer' : undefined}
      onPointerEnter={gust}
      onPointerMove={gust}
      onFocus={() => {
        if (clothRef.current) clothRef.current.gust = Math.max(clothRef.current.gust, 1);
      }}
      style={{ '--ribbon-ink': ink, '--ribbon-len': `${length}px` } as CSSProperties}
    >
      <canvas ref={canvasRef} aria-hidden="true" width={CANVAS_W} height={CANVAS_H} />
      <span ref={labelRef} className="dc-ribbon__label" aria-hidden="true">
        <b>{name}</b>
        {icon}
      </span>
    </a>
  );
}
