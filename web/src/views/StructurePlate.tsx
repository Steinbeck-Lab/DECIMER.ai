import type { CSSProperties } from 'react';
import { STAGE_ORDER, type StageName, type StageState } from '../api/contract';
import { FRAGMENT_PARTS, FRAGMENTS, VIEW_BOX } from './caffeine';

/** A fragment waits for its stage, is being drawn (copper) while its stage is the one the page speaks of, or is drawn (Velvet Forest). */
export type FragmentState = 'pending' | 'active' | 'done';

/**
 * The state of each fragment of the drawing, one per stage in pipeline order. A queued job has done what it has done
 * and nothing is being drawn; a running one draws the fragment of the stage the heading names (`current`) and has
 * drawn the ones before it.
 */
export function fragmentStates(
  states: Record<StageName, StageState>,
  current: number,
  queued: boolean,
): FragmentState[] {
  return STAGE_ORDER.map((name, i) => {
    if (queued) return states[name] === 'done' ? 'done' : 'pending';
    return i < current ? 'done' : i === current ? 'active' : 'pending';
  });
}

interface Props {
  states: readonly FragmentState[];
}

/**
 * Caffeine drawing itself on a porcelain plate beside the uploaded file: one fragment of the skeletal structure per
 * stage (caffeine.ts), each line drawn with stroke-dashoffset while its stage runs, copper and breathing, then settled
 * in Velvet Forest. By the last stage the molecule is whole. It only dresses the wait: hidden from assistive
 * technology (the heading says the stage in words) and the rules in run.css (`.dc-mol`) draw it, stop it with
 * motion reduced (each fragment fades in instead) and pause it while the tab is hidden.
 */
export function StructurePlate({ states }: Props) {
  return (
    <div className="dc-plate" aria-hidden="true">
      <svg className="dc-mol" viewBox={VIEW_BOX} focusable="false">
        {FRAGMENTS.map((fragment) => {
          const parts = FRAGMENT_PARTS[fragment];
          return (
            <g key={fragment} className="dc-mol__frag" data-state={states[fragment] ?? 'pending'}>
              {parts?.strokes.map((stroke) => (
                <path key={stroke.d} d={stroke.d} pathLength={1} style={{ '--i': stroke.order } as CSSProperties} />
              ))}
              {parts?.letters.map((letter) => (
                <text key={`${letter.x},${letter.y}`} x={letter.x} y={letter.y} dy="0.36em" style={{ '--i': letter.order } as CSSProperties}>
                  {letter.text}
                </text>
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
