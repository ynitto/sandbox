import { describe, expect, it } from 'vitest';
import { validateEdits } from './edits';
import { translate } from './geometry';

describe('edits', () => {
  it('restores saved matrices and drops unknown ids or singular matrices', () => {
    const saved = JSON.parse(JSON.stringify({ photo: translate(12, 18), gone: translate(1, 1), flat: [0, 0, 0, 0, 0, 0] }));
    const { edits, dropped } = validateEdits(saved, new Set(['photo', 'flat']));
    expect(edits).toEqual({ photo: translate(12, 18) });
    expect(dropped).toEqual(['gone', 'flat']);
  });
  it('rejects non-object input', () => {
    expect(validateEdits([1, 2], new Set()).edits).toEqual({});
  });
});
