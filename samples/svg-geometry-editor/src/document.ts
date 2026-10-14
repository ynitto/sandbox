import { IDENTITY } from './geometry';
import type { EditorDocument, Matrix } from './types';

export function emptyDocument(templateId: string, templateRevision: string): EditorDocument {
  return { schemaVersion: 1, templateId, templateRevision, images: [] };
}
export function matrixFor(doc: EditorDocument, id: string): Matrix {
  return doc.images.find(edit => edit.elementId === id)?.matrix ?? IDENTITY;
}
export function withMatrix(doc: EditorDocument, id: string, matrix: Matrix): EditorDocument {
  return { ...doc, images: [...doc.images.filter(edit => edit.elementId !== id), { elementId: id, matrix }] };
}
export function withoutMatrix(doc: EditorDocument, id: string): EditorDocument {
  return { ...doc, images: doc.images.filter(edit => edit.elementId !== id) };
}
export function validateDocument(input: unknown, templateId: string, revision: string, ids: ReadonlySet<string>): EditorDocument {
  if (!input || typeof input !== 'object') throw new Error('Invalid edit document');
  const doc = input as EditorDocument;
  if (doc.schemaVersion !== 1 || doc.templateId !== templateId || doc.templateRevision !== revision || !Array.isArray(doc.images)) throw new Error('Template or schema mismatch');
  const seen = new Set<string>();
  for (const edit of doc.images) {
    if (!edit || !ids.has(edit.elementId) || seen.has(edit.elementId) || !Array.isArray(edit.matrix) || edit.matrix.length !== 6 || !edit.matrix.every(n => typeof n === 'number' && Number.isFinite(n)) || Math.abs(edit.matrix[0] * edit.matrix[3] - edit.matrix[1] * edit.matrix[2]) < 1e-12) throw new Error('Invalid image edit');
    seen.add(edit.elementId);
  }
  return doc;
}

export class History {
  private past: EditorDocument[] = [];
  private future: EditorDocument[] = [];
  constructor(private current: EditorDocument) {}
  get value() { return this.current; }
  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
  preview(value: EditorDocument) { this.current = value; }
  commit(before: EditorDocument, after: EditorDocument) {
    this.current = after;
    if (JSON.stringify(before.images) === JSON.stringify(after.images)) return false;
    this.past.push(before);
    if (this.past.length > 100) this.past.shift();
    this.future = [];
    return true;
  }
  undo() { const previous = this.past.pop(); if (!previous) return null; this.future.push(this.current); this.current = previous; return previous; }
  redo() { const next = this.future.pop(); if (!next) return null; this.past.push(this.current); this.current = next; return next; }
  replace(value: EditorDocument) { this.current = value; this.past = []; this.future = []; }
}
