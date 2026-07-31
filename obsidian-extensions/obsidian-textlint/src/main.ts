import { Notice, Plugin, TFile } from 'obsidian';
import { join, isAbsolute } from 'path';
import { existsSync } from 'fs';
import { debounce } from 'lodash-es';
import { Diagnostic, lintGutter, setDiagnostics } from '@codemirror/lint';
import { EditorView, tooltips, ViewUpdate } from '@codemirror/view';
import { Extension } from '@codemirror/state';
import { runTextlint, runTextlintFix } from './runner';
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
  private debouncedRunLint = debounce(() => {
    void this.executeLint();
  }, DEFAULT_SETTINGS.lintDebounceMs);

  async onload() {
    console.log('[textlint] loading...');
    this.isEnabled = true;

    this.app.workspace.onLayoutReady(async () => {
      await this.loadSettings();
      this.updateDebouncedRunLint();

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
    this.debouncedRunLint.cancel();
    console.log('[textlint] unloaded');
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
    this.updateDebouncedRunLint();
    this.runLint();
  }

  updateDebouncedRunLint() {
    this.debouncedRunLint.cancel();
    this.debouncedRunLint = debounce(
      () => {
        void this.executeLint();
      },
      this.settings.lintDebounceMs,
    );
  }

  getVaultBasePath(): string {
    // @ts-expect-error
    return this.app.vault.adapter.basePath as string;
  }

  getPluginTextlintrcPath(): string {
    return join(this.getVaultBasePath(), this.manifest.dir ?? '', '.textlintrc');
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

  async getOrOpenDiagnosticView(): Promise<TextlintDiagnosticView | undefined> {
    const existing = this.getDiagnosticView();
    if (existing) return existing;

    const leaf = this.app.workspace.getRightLeaf(false);
    if (!leaf) return;
    await leaf.setViewState({ type: VIEW_TYPE_TEXTLINT_DIAGNOSTICS, active: false });
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

  runLint = () => {
    this.debouncedRunLint();
  };

  private async executeLint() {
    if (!this.isEnabled) return;
    const cm = getActiveEditorView(this);
    if (!cm) return;
    const file = getActiveFile(this);
    if (!file) return;
    if (isIgnoredFile(file, this.settings.foldersToIgnore)) return;

    const basePath = this.getVaultBasePath();
    const filePath = join(basePath, file.path);

    const { textlintrcPath, workingDirectory, npxPath, useGlobal, textlintPath } = this.settings;
    let resolvedRcPath: string | undefined;
    if (textlintrcPath) {
      resolvedRcPath = isAbsolute(textlintrcPath) ? textlintrcPath : join(basePath, textlintrcPath);
    } else {
      const pluginRcPath = this.getPluginTextlintrcPath();
      if (existsSync(pluginRcPath)) {
        resolvedRcPath = pluginRcPath;
      }
    }
    const resolvedWorkDir = workingDirectory || basePath;

    try {
      const { results, rawOutput } = await runTextlint(filePath, {
        npxPath,
        textlintrcPath: resolvedRcPath,
        workingDirectory: resolvedWorkDir,
        useGlobal,
        textlintPath,
      });

      const messages = results[0]?.messages ?? [];

      const view = await this.getOrOpenDiagnosticView();
      if (view) {
        view.setFixCallback(() => this.runFix());
        view.setCommandOutput(rawOutput);
        view.setTextlintDiagnostics(this, messages);
      }

      const diagnostics = getDiagnostics(this, messages);
      cm.dispatch(setDiagnostics(cm.state, diagnostics));
    } catch (e) {
      const err = e as Error;
      console.error('[textlint] error:', e);
      new Notice('[textlint] Error: ' + err.message);
      const view = this.getDiagnosticView();
      if (view) view.setCommandOutput('Error: ' + err.message);
    }
  }

  async runFix() {
    const file = getActiveFile(this);
    if (!file) return;

    const basePath = this.getVaultBasePath();
    const filePath = join(basePath, file.path);

    const { textlintrcPath, workingDirectory, npxPath, useGlobal, textlintPath } = this.settings;
    let resolvedRcPath: string | undefined;
    if (textlintrcPath) {
      resolvedRcPath = isAbsolute(textlintrcPath) ? textlintrcPath : join(basePath, textlintrcPath);
    } else {
      const pluginRcPath = this.getPluginTextlintrcPath();
      if (existsSync(pluginRcPath)) resolvedRcPath = pluginRcPath;
    }
    const resolvedWorkDir = workingDirectory || basePath;

    try {
      const { rawOutput } = await runTextlintFix(filePath, {
        npxPath,
        textlintrcPath: resolvedRcPath,
        workingDirectory: resolvedWorkDir,
        useGlobal,
        textlintPath,
      });

      // Reload the file in Obsidian to reflect on-disk changes made by textlint
      const tfile = this.app.vault.getAbstractFileByPath(file.path);
      if (tfile instanceof TFile) {
        const content = await this.app.vault.adapter.read(file.path);
        await this.app.vault.modify(tfile, content);
      }

      const view = this.getDiagnosticView();
      if (view) view.setCommandOutput(rawOutput);

      new Notice('[textlint] Fix applied');
      this.runLint();
    } catch (e) {
      console.error('[textlint] fix error:', e);
      new Notice('[textlint] Fix error: ' + (e as Error).message);
    }
  }
}
