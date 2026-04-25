import { FileSystemAdapter, MarkdownView, Notice, Plugin, TFile, normalizePath } from 'obsidian';
import { Extension } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import { TextlintSettings, DEFAULT_SETTINGS } from './settings';
import { TextlintSettingTab } from './settingTab';
import { TextlintWorker } from './worker';
import { buildEditorExtension, setLintErrors, clearLintErrors, LintError } from './editor/underline';

export default class TextlintPlugin extends Plugin {
  settings: TextlintSettings;
  private worker: TextlintWorker | null = null;
  private editorExtension: Extension[] = [];

  async onload() {
    await this.loadSettings();

    this.editorExtension = [buildEditorExtension()];
    this.registerEditorExtension(this.editorExtension);

    const workerUrl = this.resolveWorkerUrl();
    if (workerUrl) {
      this.worker = new TextlintWorker(workerUrl);
      this.worker.setTextlintrc(JSON.parse(this.settings.textlintrc));
    }

    this.addCommand({
      id: 'run-textlint',
      name: 'Run textlint on current file',
      editorCallback: async (editor) => {
        await this.runTextlint(editor.getValue());
      },
    });

    this.addCommand({
      id: 'clear-textlint',
      name: 'Clear textlint errors',
      editorCallback: () => {
        this.dispatchToEditor((view) => view.dispatch({ effects: clearLintErrors.of(null) }));
      },
    });

    this.registerEvent(
      this.app.workspace.on('file-open', async (file) => {
        if (file && this.settings.lintOnOpen) {
          const view = this.app.workspace.getActiveViewOfType(MarkdownView);
          if (view) {
            await this.runTextlint(view.editor.getValue());
          }
        }
      })
    );

    this.addSettingTab(new TextlintSettingTab(this.app, this));
  }

  onunload() {
    this.worker?.terminate();
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
    try {
      this.worker?.setTextlintrc(JSON.parse(this.settings.textlintrc));
    } catch {
      // invalid JSON - keep previous config
    }
  }

  private resolveWorkerUrl(): string | null {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) return null;
    const rel = normalizePath(`${this.manifest.dir}/textlint-worker.js`);
    return adapter.getResourcePath(rel);
  }

  async runTextlint(text: string) {
    if (!this.worker) {
      new Notice('textlint: worker not initialized. Run "generate-worker" and rebuild the plugin.');
      return;
    }
    const activeFile = this.app.workspace.getActiveFile();
    if (!activeFile || this.shouldIgnoreFile(activeFile)) return;

    try {
      const result = await this.worker.lint(text);
      const docLength = this.getEditorDocLength();

      const errors: LintError[] = result.messages
        .map((msg) => ({
          from: msg.range[0],
          to: msg.range[1],
          ruleId: msg.ruleId,
          message: msg.message,
        }))
        .filter((e) => e.from >= 0 && e.to > e.from && e.to <= docLength);

      this.dispatchToEditor((view) =>
        view.dispatch({ effects: setLintErrors.of(errors) })
      );

      if (result.messages.length === 0) {
        new Notice('textlint: No issues found');
      } else {
        new Notice(`textlint: ${result.messages.length} issue(s) found`);
      }
    } catch (err) {
      console.error('[obsidian-textlint]', err);
      new Notice('textlint: An error occurred during linting.');
    }
  }

  private shouldIgnoreFile(file: TFile): boolean {
    return this.settings.foldersToIgnore.some((folder) =>
      file.path.startsWith(folder.endsWith('/') ? folder : folder + '/')
    );
  }

  private dispatchToEditor(fn: (view: EditorView) => void) {
    const markdownView = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!markdownView) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cm = (markdownView.editor as any).cm as EditorView | undefined;
    if (cm) fn(cm);
  }

  private getEditorDocLength(): number {
    const markdownView = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (!markdownView) return 0;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cm = (markdownView.editor as any).cm as EditorView | undefined;
    return cm?.state.doc.length ?? 0;
  }
}
