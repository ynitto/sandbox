import type { Edits, Matrix } from './types';

function isMatrix(value: unknown): value is Matrix {
  return Array.isArray(value) && value.length === 6 && value.every(n => typeof n === 'number' && Number.isFinite(n))
    && Math.abs(value[0] * value[3] - value[1] * value[2]) >= 1e-12;
}

/** Keeps entries for known image ids with finite, invertible matrices and lists the keys it dropped. */
export function validateEdits(input: unknown, ids: ReadonlySet<string>): { edits: Edits; dropped: string[] } {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return { edits: {}, dropped: ['(not an object)'] };
  const edits: Edits = {};
  const dropped: string[] = [];
  for (const [id, matrix] of Object.entries(input)) {
    if (ids.has(id) && isMatrix(matrix)) edits[id] = matrix;
    else dropped.push(id);
  }
  return { edits, dropped };
}

export const sameEdits = (a: Edits, b: Edits) => JSON.stringify(a) === JSON.stringify(b);
