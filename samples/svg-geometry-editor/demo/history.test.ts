import { describe, expect, it } from 'vitest';
import { commit, initHistory, redo, undo } from './history';

describe('demo history', () => {
  it('undoes and redoes committed values and drops redo after a new commit', () => {
    const one = commit(initHistory('a'), 'b');
    expect(undo(one).present).toBe('a');
    expect(redo(undo(one)).present).toBe('b');
    expect(commit(undo(one), 'c').future).toEqual([]);
    expect(undo(initHistory('a')).present).toBe('a');
  });
});
