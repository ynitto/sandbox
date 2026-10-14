import { describe, expect, it } from 'vitest';
import { emptyDocument, History, matrixFor, validateDocument, withMatrix } from './document';
import { translate } from './geometry';

describe('edit document and history', () => {
  it('saves and restores exact image matrices while rejecting a wrong revision', () => {
    const before = emptyDocument('card', 'r1');
    const after = withMatrix(before, 'photo', translate(12, 18));
    const restored = validateDocument(JSON.parse(JSON.stringify(after)), 'card', 'r1', new Set(['photo']));
    expect(matrixFor(restored, 'photo')).toEqual(translate(12, 18));
    expect(() => validateDocument(restored, 'card', 'r2', new Set(['photo']))).toThrow();
    expect(() => validateDocument(withMatrix(before, 'photo', [0, 0, 0, 0, 0, 0]), 'card', 'r1', new Set(['photo']))).toThrow();
  });
  it('undoes and redoes a completed gesture as one entry', () => {
    const initial = emptyDocument('card', '1');
    const history = new History(initial);
    const intermediate = withMatrix(initial, 'photo', translate(3, 0));
    const final = withMatrix(initial, 'photo', translate(9, 0));
    history.preview(intermediate); history.preview(final); history.commit(initial, final);
    expect(history.undo()).toEqual(initial);
    expect(history.redo()).toEqual(final);
  });
});
