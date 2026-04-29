import { Notice, Plugin } from 'obsidian';
import { join, isAbsolute } from 'path';
import { debounce } from 'lodash-es';
import { Diagnostic, lintGutter, setDiagnostics } from '@codemirror/lint';
import { EditorView, tooltips, ViewUpdate } from '@codemirror/view';
import { Extension } from '@codemirror/state';
import { runTextlint } from './textlint/runner';
import { getTheme } from './theme';
import { DEFAULT_SETTINGS, TextlintPluginSettingTab, TextlintPluginSettings } from './settings';
import {
  diagnosticSeverityToTextlintSeverity,
  getActiveEditorView,
  getActiveFile,
  isIgnoredFile,
} from './util';
import {
  TextlintDiagnosticView,
  TEXTLINT_DIAGNOSTICS_EXTENSION,
  VIEW_TYPE_TEXTLINT_DIAGNOSTICS,
} from './diagnosticsView';
import { getDiagnostics } from './cm/diagnostics';

export default class TextlintPlugin extends Plugin {
  settings: TextlintPluginSettings;
  private isEnabled = true;

  async onload() {
    console.log('[textlint] loading...');
    this.isEnabled = true;

    this.app.workspace.onLayoutReady(async () => {
      await this.loadSettings();

      this.registerEditorExtensions();
      this.registerEvents();
      this.registerDiagnosticsViewExtension();
      this.addCommands();
      this.runLint();

      this.addSettingTab(new TextlintPluginSettingTab(this.app, this));
    });

    console.log('[textlint] loaded');
  }

  async onunload() {
    console.log('[textlint] unloading...');
    this.isEnabled = false;
    console.log('[textlint] unloaded');
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
    this.runLint();
  }

  getVaultBasePath(): string {
    // @ts-expect-error
    return this.app.vault.adapter.basePath as string;
  }

  addCommands() {
    this.addCommand({
      id: 'run-lint',
      name: 'Run textlint lint',
      editorCallback: () => {
        this.runLint();
      },
    });
  }

  registerDiagnosticsViewExtension() {
    this.registerView(VIEW_TYPE_TEXTLINT_DIAGNOSTICS, (leaf) => {
      return new TextlintDiagnosticView(leaf);
    });
    this.registerExtensions([TEXTLINT_DIAGNOSTICS_EXTENSION], VIEW_TYPE_TEXTLINT_DIAGNOSTICS);
    this.addCommand({
      id: 'show-diagnostics-view',
      name: 'Show textlint diagnostics',
      callback: async () => {
        const activeViewLeaf = this.getDiagnosticViewLeaf();
        if (activeViewLeaf) {
          this.app.workspace.revealLeaf(activeViewLeaf);
          return;
        }
        const leaf = this.app.workspace.getRightLeaf(false);
        if (leaf) {
          await leaf.setViewState({
            type: VIEW_TYPE_TEXTLINT_DIAGNOSTICS,
            active: true,
          });
        }
        const activeLeaf = this.app.workspace.getMostRecentLeaf();
        if (activeLeaf) {
          this.app.workspace.setActiveLeaf(activeLeaf);
        }
      },
    });
  }

  registerEvents() {
    if (this.settings.lintOnSaved) {
      // @ts-expect-error
      const editorSaveCommand = this.app.commands?.commands?.['editor:save-file'];
      if (editorSaveCommand?.callback) {
        const originalCallback = editorSaveCommand.callback.bind({});
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const saveCallback = (...args: any[]) => {
          originalCallback(...args);
          this.runLint();
        };
        editorSaveCommand.callback = saveCallback;
      }
    }

    if (this.settings.lintOnActiveFileChanged) {
      this.registerEvent(
        this.app.workspace.on('active-leaf-change', (leaf) => {
          if (!leaf) return;
          const state = leaf.getViewState();
          if (state.type === VIEW_TYPE_TEXTLINT_DIAGNOSTICS) return;
          if (state.type !== 'markdown') {
            return this.clear();
          }
          this.runLint();
        }),
      );
    }
  }

  registerEditorExtensions() {
    const extensions = [
      tooltips({ parent: document.body, position: 'fixed' }),
      this.getLintOnTextChangedExtension(),
      this.getLintGutterExtension(),
      getTheme(),
    ].filter((v) => v) as Extension[];

    this.registerEditorExtension(extensions);
  }

  getDiagnosticViewLeaf() {
    const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE_TEXTLINT_DIAGNOSTICS);
    if (!leaves[0]) return;
    return leaves[0];
  }

  getDiagnosticView() {
    const leaf = this.getDiagnosticViewLeaf();
    if (!leaf) return;
    if (leaf.isDeferred) leaf.loadIfDeferred();
    return leaf.view as TextlintDiagnosticView;
  }

  getLintOnTextChangedExtension() {
    if (!this.settings.lintOnTextChanged) return;

    this.registerEvent(
      this.app.workspace.on('editor-paste', () => {
        this.runLint();
      }),
    );

    return EditorView.updateListener.of((update: ViewUpdate) => {
      if (this.isEnabled && update.docChanged) {
        this.runLint();
      }
    });
  }

  getLintGutterExtension() {
    const filter = (diagnostics: Diagnostic[]) => {
      if (!this.settings.showGutter) {
        return [];
      }
      return diagnostics.filter(
        (d) => diagnosticSeverityToTextlintSeverity(d.severity) >= this.settings.minimumSeverityToShowGutter,
      );
    };
    return lintGutter({ hoverTime: 100, tooltipFilter: filter, markerFilter: filter });
  }

  clear() {
    const cm = getActiveEditorView(this);
    if (cm) {
      cm.dispatch(setDiagnostics(cm.state, []));
    }
    const view = this.getDiagnosticView();
    if (view) view.clear();
  }

  runLint = debounce(async () => {
    if (!this.isEnabled) return;
    const cm = getActiveEditorView(this);
    if (!cm) return;
    const file = getActiveFile(this);
    if (!file) return;
    if (isIgnoredFile(file, this.settings.foldersToIgnore)) return;

    const basePath = this.getVaultBasePath();
    const filePath = join(basePath, file.path);

    const { textlintrcPath, workingDirectory, npxPath, useGlobal, textlintPath } = this.settings;
    const resolvedRcPath = textlintrcPath
      ? isAbsolute(textlintrcPath) ? textlintrcPath : join(basePath, textlintrcPath)
      : undefined;
    const resolvedWorkDir = workingDirectory || basePath;

    try {
      const results = await runTextlint(filePath, {
        npxPath,
        textlintrcPath: resolvedRcPath,
        workingDirectory: resolvedWorkDir,
        useGlobal,
        textlintPath,
      });

      const messages = results[0]?.messages ?? [];

      const view = this.getDiagnosticView();
      if (view) view.setTextlintDiagnostics(this, messages);

      const diagnostics = getDiagnostics(this, messages);
      cm.dispatch(setDiagnostics(cm.state, diagnostics));
    } catch (e) {
      console.error('[textlint] error:', e);
      new Notice('[textlint] Error: ' + e.message);
    }
  }, 500);
}
