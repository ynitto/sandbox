import { StateEffect, StateField, Extension } from '@codemirror/state';
import { EditorView, Decoration, DecorationSet, hoverTooltip } from '@codemirror/view';

export interface LintError {
  from: number;
  to: number;
  message: string;
  ruleId: string;
}

export const setLintErrors = StateEffect.define<LintError[]>();
export const clearLintErrors = StateEffect.define<null>();

const lintErrorField = StateField.define<LintError[]>({
  create: () => [],
  update(errors, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setLintErrors)) return effect.value;
      if (effect.is(clearLintErrors)) return [];
    }
    return errors;
  },
});

const underlineMark = Decoration.mark({ class: 'textlint-underline' });

const decorationField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const effect of tr.effects) {
      if (effect.is(setLintErrors)) {
        const sorted = [...effect.value].sort((a, b) => a.from - b.from);
        const marks = sorted
          .filter((e) => e.from >= 0 && e.from < e.to)
          .map((e) => underlineMark.range(e.from, e.to));
        deco = marks.length > 0 ? Decoration.set(marks) : Decoration.none;
      }
      if (effect.is(clearLintErrors)) {
        deco = Decoration.none;
      }
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

const lintTooltip = hoverTooltip((view, pos) => {
  const errors = view.state.field(lintErrorField, false);
  if (!errors) return null;
  const error = errors.find((e) => pos >= e.from && pos <= e.to);
  if (!error) return null;
  return {
    pos: error.from,
    end: error.to,
    above: true,
    create() {
      const dom = document.createElement('div');
      dom.className = 'textlint-tooltip';
      const ruleEl = document.createElement('code');
      ruleEl.className = 'textlint-tooltip-rule';
      ruleEl.textContent = error.ruleId;
      dom.appendChild(ruleEl);
      dom.appendChild(document.createTextNode(' '));
      const msgEl = document.createElement('span');
      msgEl.textContent = error.message;
      dom.appendChild(msgEl);
      return { dom };
    },
  };
});

export function buildEditorExtension(): Extension {
  return [lintErrorField, decorationField, lintTooltip];
}
