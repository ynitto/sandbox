import { describe, expect, it } from 'vitest';
import { around, compose, decompose, inverse, multiply, point, rotateBy, scale, scaleTo, setPosition, translate, zoomAt } from './geometry';

describe('geometry', () => {
  it('round trips affine points through inverse', () => {
    const matrix = compose(30, -20, 2.5, 35);
    const original = { x: 12, y: -9 };
    const result = point(inverse(matrix), point(matrix, original));
    expect(result.x).toBeCloseTo(original.x); expect(result.y).toBeCloseTo(original.y);
  });
  it('scales around fixed corner and preserves uniform aspect ratio', () => {
    const pivot = { x: 20, y: 30 };
    const matrix = multiply(around(pivot, scale(2)), translate(4, 5));
    expect(point(matrix, { x: 16, y: 25 })).toEqual(pivot);
    expect(decompose(matrix).scale).toBeCloseTo(2);
  });
  it('keeps pointer location fixed while zooming a panned view', () => {
    const old = { x: 22, y: -30, zoom: 2 };
    const pointer = { x: 140, y: 90 };
    const content = { x: (pointer.x - old.x) / old.zoom, y: (pointer.y - old.y) / old.zoom };
    const next = zoomAt(old, pointer, 1.5);
    expect(next.x + content.x * next.zoom).toBeCloseTo(pointer.x);
    expect(next.y + content.y * next.zoom).toBeCloseTo(pointer.y);
  });
  it('maps drag deltas through view zoom and a transformed parent', () => {
    const parentToScreen = multiply(translate(80, 30), multiply(scale(2), compose(25, -10, 1, 30)));
    const start = point(parentToScreen, { x: 4, y: 7 });
    const end = point(parentToScreen, { x: 14, y: 12 });
    const back = inverse(parentToScreen);
    const a = point(back, start), b = point(back, end);
    expect(b.x - a.x).toBeCloseTo(10);
    expect(b.y - a.y).toBeCloseTo(5);
  });
  it('edits position, scale and angle independently', () => {
    const start = compose(10, 20, 2, 30);
    const scaled = decompose(scaleTo(start, 3));
    expect(scaled.x).toBeCloseTo(10); expect(scaled.scale).toBeCloseTo(3); expect(scaled.angle).toBeCloseTo(30);
    const turned = decompose(rotateBy(start, 15));
    expect(turned.y).toBeCloseTo(20); expect(turned.scale).toBeCloseTo(2); expect(turned.angle).toBeCloseTo(45);
    expect(decompose(setPosition(start, 1, 2))).toMatchObject({ x: 1, y: 2 });
  });
});
