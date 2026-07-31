import { Diagnostic } from '@codemirror/lint';
import { TextlintMessage } from '@textlint/types';
import TextlintPlugin from '../main';
import { getActiveEditor, getEditorView, textlintSeverityToDiagnosticSeverity } from '../util';

export const getDiagnostics = (plugin: TextlintPlugin, messages: TextlintMessage[]): Diagnostic[] => {
  const editor = getActiveEditor(plugin);
  if (!editor) return [];

  const cm = getEditorView(editor);

  const diagnostics: Diagnostic[] = [];

  messages.forEach((message) => {
    if (message.severity < plugin.settings.minimumSeverityInEditingView) return;

    const from = message.range ? message.range[0] : message.index;
    const to = message.range ? message.range[1] : message.index + 1;

    const diagnostic: Diagnostic = {
      from,
      to,
      severity: textlintSeverityToDiagnosticSeverity(message.severity),
      source: '[textlint] ' + message.ruleId,
      message: `${message.message} [${message.loc.start.line}, ${message.loc.start.column}]`,
      renderMessage: () => {
        const item = document.createElement('span');
        item.setText(message.message);
        item.style.display = 'flex';
        item.style.flexDirection = 'row';

        const fix = message.fix;
        if (fix) {
          const fixBtn = document.createElement('button');
          fixBtn.style.marginLeft = '0.5em';
          fixBtn.setText('Fix');
          const [fixFrom, fixTo] = fix.range;

          const oldText = cm.state.sliceDoc(fixFrom, fixTo);
          fixBtn.onClickEvent(() => {
            fixBtn.setText('Fixing...');
            fixBtn.setAttribute('disabled', '');
            const changes = { changes: { from: fixFrom, to: fixTo, insert: fix.text } };
            cm.dispatch(changes);

            setTimeout(() => {
              if (cm.state.sliceDoc(fixFrom, fixTo) !== oldText) {
                fixBtn.setText('Fixed!');
                item.parentElement?.parentElement?.remove();
              } else {
                fixBtn.setText('Fix');
                fixBtn.removeAttribute('disabled');
              }
            }, 1000);
          });
          item.appendChild(fixBtn);
        }
        return item;
      },
    };
    diagnostics.push(diagnostic);
  });
  return diagnostics;
};
