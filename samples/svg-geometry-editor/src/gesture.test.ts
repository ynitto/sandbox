import { describe, expect, it } from 'vitest';
import { activePointerIds, distance, midpoint, remainingPointers, resolvePointerEnd } from './gesture';
import { emptyDocument } from './document';
import { IDENTITY } from './geometry';

describe('gesture bookkeeping', () => {
  it('keeps the original target and blocks the remaining finger after a pinch ends', () => {
    const pointers = new Map([[1, { x: 0, y: 0 }], [2, { x: 20, y: 0 }]]);
    expect(midpoint(pointers.get(1)!, pointers.get(2)!)).toEqual({ x: 10, y: 0 });
    expect(distance(pointers.get(1)!, pointers.get(2)!)).toBe(20);
    const blocked = { kind: 'blocked' as const, pointers: remainingPointers(pointers, 2) };
    expect(activePointerIds(blocked)).toEqual([1]);
    const gesture = { kind: 'image-pinch' as const, id: 'photo', pointers: [1, 2] as [number, number], start: [pointers.get(1)!, pointers.get(2)!] as [{x:number;y:number},{x:number;y:number}], startMatrix: IDENTITY, before: emptyDocument('a', '1'), parentInverse: {} as DOMMatrix };
    const result = resolvePointerEnd(gesture, 2, pointers, false);
    expect(result.action).toBe('commit'); expect(result.next.kind).toBe('blocked');
    expect(resolvePointerEnd(result.next, 1, new Map([[1, pointers.get(1)!]]), false).next.kind).toBe('idle');
  });
  it('restores the pre-gesture state on pointercancel', () => {
    const pointers = new Map([[7, { x: 10, y: 10 }]]);
    const gesture = { kind: 'image-drag' as const, id: 'photo', pointer: 7, start: pointers.get(7)!, startMatrix: IDENTITY, before: emptyDocument('a', '1'), parentInverse: {} as DOMMatrix };
    expect(resolvePointerEnd(gesture, 7, pointers, true).action).toBe('restore');
  });
});
