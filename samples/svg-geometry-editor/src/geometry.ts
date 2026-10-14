import type { Matrix } from './types';

export const IDENTITY: Matrix = [1, 0, 0, 1, 0, 0];
export type Point = { x: number; y: number };

export function multiply(a: Matrix, b: Matrix): Matrix {
  return [
    a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
    a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
    a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]
  ];
}
export function inverse(m: Matrix): Matrix {
  const d = m[0] * m[3] - m[1] * m[2];
  if (!Number.isFinite(d) || Math.abs(d) < 1e-12) throw new Error('Singular transform');
  return [m[3] / d, -m[1] / d, -m[2] / d, m[0] / d,
    (m[2] * m[5] - m[3] * m[4]) / d, (m[1] * m[4] - m[0] * m[5]) / d];
}
export function point(m: Matrix, p: Point): Point {
  return { x: m[0] * p.x + m[2] * p.y + m[4], y: m[1] * p.x + m[3] * p.y + m[5] };
}
export const translate = (x: number, y: number): Matrix => [1, 0, 0, 1, x, y];
export const scale = (s: number): Matrix => [s, 0, 0, s, 0, 0];
export const rotate = (radians: number): Matrix => [Math.cos(radians), Math.sin(radians), -Math.sin(radians), Math.cos(radians), 0, 0];
export const around = (pivot: Point, transform: Matrix): Matrix => multiply(multiply(translate(pivot.x, pivot.y), transform), translate(-pivot.x, -pivot.y));
export const matrixString = (m: Matrix) => `matrix(${m.map(n => Number(n.toFixed(8))).join(' ')})`;
export const fromDOM = (m: DOMMatrixReadOnly): Matrix => [m.a, m.b, m.c, m.d, m.e, m.f];
export const toDOM = (m: Matrix): DOMMatrix => new DOMMatrix(m);
export function decompose(m: Matrix) {
  return { x: m[4], y: m[5], scale: Math.hypot(m[0], m[1]), angle: Math.atan2(m[1], m[0]) * 180 / Math.PI };
}
export function compose(x: number, y: number, s: number, angle: number): Matrix {
  return multiply(translate(x, y), multiply(rotate(angle * Math.PI / 180), scale(s)));
}
export function clamp(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)); }

export interface View { x: number; y: number; zoom: number }
export const DEFAULT_VIEW: View = { x: 0, y: 0, zoom: 1 };
export function zoomAt(view: View, client: Point, factor: number, min = 0.25, max = 8): View {
  const zoom = clamp(view.zoom * factor, min, max);
  const ratio = zoom / view.zoom;
  return { x: client.x - (client.x - view.x) * ratio, y: client.y - (client.y - view.y) * ratio, zoom };
}
