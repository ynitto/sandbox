// Undo/redo over committed values. The canvas reports commits with `{ final: true }`.
export interface HistoryState<T> { past: T[]; present: T; future: T[] }

export const initHistory = <T>(present: T): HistoryState<T> => ({ past: [], present, future: [] });
export function commit<T>(h: HistoryState<T>, next: T): HistoryState<T> {
  return { past: [...h.past, h.present].slice(-100), present: next, future: [] };
}
export function undo<T>(h: HistoryState<T>): HistoryState<T> {
  if (!h.past.length) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}
export function redo<T>(h: HistoryState<T>): HistoryState<T> {
  if (!h.future.length) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}
