import { Diagnostic } from '@codemirror/lint';
import { EditorView } from '@codemirror/view';
import { TextlintRuleSeverityLevel } from '@textlint/types';
import { Editor, MarkdownView, Plugin, TFile } from 'obsidian';

const getActiveView = (plugin: Plugin) => {
  return plugin.app.workspace.getActiveViewOfType(MarkdownView);
};

export const getActiveFile = (plugin: Plugin) => {
  return plugin.app.workspace.getActiveFile();
};

export const getActiveEditor = (plugin: Plugin) => {
  const view = getActiveView(plugin);
  return view?.editor;
};

export const getEditorView = (editor: Editor) => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (editor as any).cm as EditorView;
};

export const getActiveEditorView = (plugin: Plugin) => {
  const editor = getActiveEditor(plugin);
  if (!editor) return;
  return getEditorView(editor);
};

export const isIgnoredFile = (file: TFile, folders: string[]) => {
  for (const folder of folders) {
    if (folder.length > 0 && file.path.startsWith(folder)) {
      return true;
    }
  }
  return false;
};

export const textlintSeverityToDiagnosticSeverity = (severity: TextlintRuleSeverityLevel) => {
  return ['info', 'warning', 'error'][severity] as Diagnostic['severity'];
};

export const diagnosticSeverityToTextlintSeverity = (severity: Diagnostic['severity']) => {
  return { hint: 0, info: 0, warning: 1, error: 2 }[severity] as TextlintRuleSeverityLevel;
};
