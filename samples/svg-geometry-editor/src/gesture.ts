import type { EditorDocument, Matrix } from './types';
import type { Point, View } from './geometry';

export type Gesture =
  | { kind: 'idle' }
  | { kind: 'blocked'; pointers: Set<number> }
  | { kind: 'image-drag'; id: string; pointer: number; start: Point; startMatrix: Matrix; before: EditorDocument; parentInverse: DOMMatrix }
  | { kind: 'image-resize'; id: string; pointer: number; start: Point; startMatrix: Matrix; before: EditorDocument; pivot: Point; pivotScreen: Point; parentInverse: DOMMatrix }
  | { kind: 'image-rotate'; id: string; pointer: number; start: Point; startMatrix: Matrix; before: EditorDocument; pivot: Point; pivotScreen: Point }
  | { kind: 'image-pinch'; id: string; pointers: [number, number]; start: [Point, Point]; startMatrix: Matrix; before: EditorDocument; parentInverse: DOMMatrix }
  | { kind: 'view-pan'; pointer: number; start: Point; startView: View }
  | { kind: 'view-pinch'; pointers: [number, number]; start: [Point, Point]; startView: View };

export const midpoint = (a: Point, b: Point): Point => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
export const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
export const angle = (a: Point, b: Point) => Math.atan2(b.y - a.y, b.x - a.x);
export function remainingPointers(pointers: ReadonlyMap<number, Point>, removed: number) {
  return new Set(Array.from(pointers.keys()).filter(id => id !== removed));
}
export function activePointerIds(gesture: Gesture): number[] {
  if (gesture.kind === 'idle') return [];
  if (gesture.kind === 'blocked') return [...gesture.pointers];
  if ('pointers' in gesture) return gesture.pointers;
  return [gesture.pointer];
}

export function resolvePointerEnd(gesture: Gesture, pointerId: number, pointers: ReadonlyMap<number, Point>, canceled: boolean) {
  const remaining = remainingPointers(pointers, pointerId);
  const active = activePointerIds(gesture).includes(pointerId);
  const action = active && 'before' in gesture ? (canceled ? 'restore' : 'commit') : 'none';
  const next: Gesture = remaining.size ? { kind: 'blocked', pointers: remaining } : { kind: 'idle' };
  return { action, next } as const;
}
