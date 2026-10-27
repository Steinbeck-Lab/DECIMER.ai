import { useEffect, useRef, type CSSProperties, type MouseEvent, type PointerEvent, type ReactNode } from 'react';
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
  releaseCloth,
  renderCloth,
  settle,
  stepCloth,
  type Cloth,
  type Particle,
  type Raster,
} from './cloth';

/** The canvas is bigger than the strip: room for the sway, for a pull of the pointer and for the soft cast shadow. */
const CANVAS_W = 150;
const CANVAS_H = 236;
/** The strip's width, and where its left edge sits in the canvas. */
const STRIP_W = 46;
const STRIP_X = 52;
/** The link is exactly the strip's width (blank space beside the cloth must not be a link): the canvas starts where the
 *  strip's left edge is, and the label starts at the strip's middle. */
const CANVAS_LEFT = STRIP_X;
const LABEL_LEFT = STRIP_W / 2;
/** While the wind is calm the strip only sways: it is drawn about 30 times a second. A gust or a pull draws every frame. */
const CALM_FRAME_MS = 29;
/** A press that moves farther than this (px) is a pull of the cloth, not a click. */
const DRAG_THRESHOLD = 6;

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

interface Painter {
  cloth: Cloth;
  edge: Particle[];
  base: [number, number, number];
  fold: string;
  thickness: string;
  /** The cloth's pixels, at the screen's density, and the canvas they are uploaded to before they are drawn. */
  raster: Raster | null;
  image: ImageData | null;
  buffer: HTMLCanvasElement;
  bufferCtx: CanvasRenderingContext2D | null;
  /** Device pixels per px, across and down (they differ a little when the density is fractional). */
  dpr: number;
  dprY: number;
}

function paint(ctx: CanvasRenderingContext2D, painter: Painter, label: HTMLElement) {
  const { cloth, edge, base, fold, thickness, raster, image, buffer, bufferCtx } = painter;
  const { top, width } = cloth;
  ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);

  const silhouette = new Path2D();
  edge.forEach((p, q) => (q ? silhouette.lineTo(p.x, p.y) : silhouette.moveTo(p.x, p.y)));
  silhouette.closePath();

  // The soft cast shadow of the silhouette (an offset and a blur: the strip hangs off the page).
  ctx.save();
  ctx.shadowColor = 'rgba(30,50,38,.28)';
  ctx.shadowBlur = 10;
  ctx.shadowOffsetX = 3;
  ctx.shadowOffsetY = 6;
  ctx.fillStyle = `rgb(${base.join(',')})`;
  ctx.fill(silhouette);
  ctx.restore();

  // The cloth's thickness: the same silhouette, darker, one step down and to the right.
  ctx.save();
  ctx.translate(1.6, 1.8);
  ctx.fillStyle = thickness;
  ctx.fill(silhouette);
  ctx.restore();

  // The cloth itself, shaded per pixel; the silhouette (drawn with anti-aliasing) clips it, so its edges are smooth.
  if (raster && image && bufferCtx) {
    const box = renderCloth(cloth, raster, painter.dpr, base, painter.dprY);
    if (box) bufferCtx.putImageData(image, 0, 0, box[0], box[1], box[2] - box[0], box[3] - box[1]);
    ctx.save();
    ctx.clip(silhouette);
    ctx.drawImage(buffer, 0, 0, CANVAS_W, CANVAS_H);
    ctx.restore();
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
    `scale(${pose.scaleX.toFixed(3)}, ${pose.scaleY.toFixed(3)}) translateX(-50%)`;
}

/** A press on the ribbon: where it started, and whether it has become a pull of the cloth. */
interface Press {
  pointerId: number;
  x: number;
  y: number;
  /** The link's box when the press started: the scale of the ribbons (smaller on a phone) and the canvas's origin. */
  left: number;
  top: number;
  scale: number;
  dragging: boolean;
}

/**
 * A ribbon bookmark hanging from the top edge of the page: a cloth simulated and drawn on a canvas, a word and an
 * icon printed on it that follow the cloth, and a real link around the lot. The pointer raises a gust from its side
 * (keyboard focus a gust too), and a mouse (or a pen on a desktop) can take hold of the cloth and pull it; a press that
 * hardly moves is still a click. On a touch screen a finger, or a pen, scrolls the page as usual and a tap follows the
 * link. The cloth hangs still under `prefers-reduced-motion`, and the simulation stops while the tab is
 * hidden or the ribbon is scrolled out of view.
 */
export function ClothRibbon({ href, name, color, ink, foldShade, length, icon, phase = 0, external = false }: Props) {
  const linkRef = useRef<HTMLAnchorElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);
  const clothRef = useRef<Cloth | null>(null);
  /** Motion is running (not reduced, on screen): the cloth can be pulled. */
  const liveRef = useRef(false);
  const pressRef = useRef<Press | null>(null);
  /** A pull just ended: the click that follows it must not follow the link. */
  const pulledRef = useRef(false);

  useEffect(() => {
    const link = linkRef.current;
    const canvas = canvasRef.current;
    const label = labelRef.current;
    const ctx = canvas?.getContext('2d');
    if (!link || !canvas || !label || !ctx) return; // no canvas (a test, an old browser): the label sits still on the page

    const base = hexToRgb(color);
    const cloth = createCloth({ width: STRIP_W, length, originX: STRIP_X, phase });
    clothRef.current = cloth;
    settle(cloth);
    const buffer = document.createElement('canvas');
    const painter: Painter = {
      cloth,
      edge: outline(cloth),
      base,
      fold: `rgb(${base.map((x) => Math.round(x * foldShade)).join(',')})`,
      thickness: `rgb(${base.map((x) => Math.round(x * 0.55)).join(',')})`,
      raster: null,
      image: null,
      buffer,
      bufferCtx: buffer.getContext('2d'),
      dpr: 1,
      dprY: 1,
    };
    const draw = () => paint(ctx, painter, label);

    // The backing store follows the screen's pixel density, also when the window moves to another screen.
    let densityQuery: MediaQueryList | null = null;
    const onDensity = () => {
      resize();
      draw();
    };
    const resize = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = Math.round(CANVAS_W * dpr);
      const h = Math.round(CANVAS_H * dpr);
      canvas.width = buffer.width = w;
      canvas.height = buffer.height = h;
      // The backing store is whole pixels: its true scale, across and down, is what the cloth is painted at.
      painter.dpr = w / CANVAS_W;
      painter.dprY = h / CANVAS_H;
      ctx.setTransform(painter.dpr, 0, 0, painter.dprY, 0, 0);
      painter.raster = createRaster(w, h);
      painter.image = typeof ImageData === 'undefined' ? null : new ImageData(painter.raster.bytes, w, h);
      densityQuery?.removeEventListener('change', onDensity);
      densityQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      densityQuery.addEventListener('change', onDensity);
    };
    resize();
    draw();

    const still = window.matchMedia('(prefers-reduced-motion: reduce)');
    let onScreen = true;
    let frame = 0;
    let lastStep = 0;
    let lastDraw = 0;
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      if (isCalm(cloth) && now - lastDraw < CALM_FRAME_MS) return;
      stepCloth(cloth, (now - lastStep) / 1000);
      lastStep = lastDraw = now;
      draw();
    };
    const stop = () => {
      cancelAnimationFrame(frame);
      frame = 0;
    };
    // Running means: motion is welcome, the tab is on screen and so is the ribbon.
    const sync = () => {
      const run = !still.matches && document.visibilityState === 'visible' && onScreen;
      liveRef.current = !still.matches;
      if (run && !frame) {
        lastStep = lastDraw = performance.now();
        frame = requestAnimationFrame(tick);
      } else if (!run && frame) stop();
    };
    const observer =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver((entries) => {
            onScreen = entries.some((entry) => entry.isIntersecting);
            sync();
          });
    observer?.observe(link);
    sync();
    document.addEventListener('visibilitychange', sync);
    still.addEventListener('change', sync);
    return () => {
      stop();
      observer?.disconnect();
      document.removeEventListener('visibilitychange', sync);
      still.removeEventListener('change', sync);
      densityQuery?.removeEventListener('change', onDensity);
      clothRef.current = null;
      liveRef.current = false;
    };
  }, [color, foldShade, length, phase]);

  /** The pointer's position in the canvas's px, as of the press (or of `rect` for a hover). */
  const toCanvas = (clientX: number, clientY: number, left: number, top: number, scale: number) => ({
    x: (clientX - left) / scale + CANVAS_LEFT,
    y: (clientY - top) / scale,
  });

  const onPointerEnter = (event: PointerEvent<HTMLAnchorElement>) => {
    const cloth = clothRef.current;
    if (!cloth) return;
    // The wind comes from the pointer's side and blows away from it.
    const rect = event.currentTarget.getBoundingClientRect();
    addGust(cloth, Math.max(0, 1 - cloth.gust), event.clientX < rect.left + rect.width / 2 ? 1 : -1);
  };

  /** Ends a press, whatever state it reached: lets go of the cloth and of the pointer. Returns whether it was a pull. */
  const dropPress = (link: HTMLAnchorElement): boolean => {
    const press = pressRef.current;
    pressRef.current = null;
    const cloth = clothRef.current;
    if (cloth?.grab) releaseCloth(cloth);
    delete link.dataset.pulling;
    if (press && link.hasPointerCapture?.(press.pointerId)) link.releasePointerCapture(press.pointerId);
    return press?.dragging ?? false;
  };

  const onPointerDown = (event: PointerEvent<HTMLAnchorElement>) => {
    // A mouse (or a desktop pen) can pull the cloth; a finger scrolls the page as usual (and a tap still follows the link).
    if (event.button !== 0 || event.pointerType === 'touch' || !liveRef.current) return;
    // A second button or pointer while the cloth is held does not start another press.
    if (pressRef.current?.dragging) return;
    const link = event.currentTarget;
    if (pressRef.current) dropPress(link);
    const rect = link.getBoundingClientRect();
    pulledRef.current = false;
    pressRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      left: rect.left,
      top: rect.top,
      scale: rect.width / STRIP_W || 1,
      dragging: false,
    };
    // Captured from the start, so the release comes back here even off the link (a plain click still lands on it).
    link.setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event: PointerEvent<HTMLAnchorElement>) => {
    const cloth = clothRef.current;
    if (!cloth) return;
    let press = pressRef.current;
    // The button was let go where this link never heard of it: the press is over.
    if (press && press.pointerId === event.pointerId && !(event.buttons & 1)) {
      dropPress(event.currentTarget);
      press = null;
    }
    if (!press || press.pointerId !== event.pointerId) {
      // Hovering: the pointer's movement stirs the air the way it moves.
      addGust(cloth, 0.06, Math.sign(event.movementX) || cloth.gustDir);
      return;
    }
    if (!press.dragging) {
      if (Math.hypot(event.clientX - press.x, event.clientY - press.y) < DRAG_THRESHOLD) return;
      const at = toCanvas(press.x, press.y, press.left, press.top, press.scale);
      if (!grabCloth(cloth, at.x, at.y)) return;
      press.dragging = true;
      event.currentTarget.dataset.pulling = 'true';
    }
    const at = toCanvas(event.clientX, event.clientY, press.left, press.top, press.scale);
    // Held inside the canvas by half a strip's width, so the cloth around the held point is not cut off by its edge.
    const margin = STRIP_W / 2 + 2;
    dragCloth(cloth, Math.max(margin, Math.min(CANVAS_W - margin, at.x)), Math.max(cloth.top, Math.min(CANVAS_H - margin / 2, at.y)));
  };

  const endPress = (event: PointerEvent<HTMLAnchorElement>) => {
    const press = pressRef.current;
    if (!press || press.pointerId !== event.pointerId) return;
    // The cloth swings back from where it was let go; the click that ends a pull is not a click on the link.
    if (!dropPress(event.currentTarget)) return;
    pulledRef.current = true;
    window.setTimeout(() => (pulledRef.current = false), 0);
  };

  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (pulledRef.current) {
      event.preventDefault();
      pulledRef.current = false;
      return;
    }
    // The link holds pointer capture from pointerdown, so a release off the ribbon still clicks it. Cancel that, as a
    // native link would; a keyboard click (detail 0) has no pointer position and always navigates.
    const under = typeof document.elementFromPoint === 'function' ? document.elementFromPoint(event.clientX, event.clientY) : null;
    if (event.detail !== 0 && under && !event.currentTarget.contains(under)) {
      event.preventDefault();
    }
  };

  return (
    <a
      ref={linkRef}
      className="dc-ribbon"
      href={href}
      aria-label={name}
      target={external ? '_blank' : undefined}
      rel={external ? 'noreferrer' : undefined}
      // The browser's own drag of a link would take the pointer away from the cloth.
      draggable={false}
      onDragStart={(event) => event.preventDefault()}
      onPointerEnter={onPointerEnter}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endPress}
      onPointerCancel={endPress}
      onLostPointerCapture={endPress}
      onClick={onClick}
      onFocus={() => {
        if (clothRef.current) addGust(clothRef.current, Math.max(0, 1 - clothRef.current.gust));
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
