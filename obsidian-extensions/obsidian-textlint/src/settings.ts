import { TextlintRuleSeverityLevel } from '@textlint/types';
import { App, Notice, PluginSettingTab, Setting } from 'obsidian';
import { readFileSync, writeFileSync } from 'fs';
import { join, isAbsolute } from 'path';
import TextlintPlugin from './main';
import { installTextlintPlugin, runTextlintPrintConfig, RuleConfig } from './runner';

const SEVERITY_OPTIONS: Record<string, string> = {
  '0': 'info',
  '1': 'warning',
  '2': 'error',
};

export interface TextlintPluginSettings {
  lintOnActiveFileChanged: boolean;
  lintOnSaved: boolean;
  lintOnTextChanged: boolean;
  lintDebounceMs: number;

  minimumSeverityInEditingView: TextlintRuleSeverityLevel;
  minimumSeverityInDiagnosticsView: TextlintRuleSeverityLevel;

  showGutter: boolean;
  minimumSeverityToShowGutter: TextlintRuleSeverityLevel;

  foldersToIgnore: string[];

  useGlobal: boolean;
  textlintPath: string;
  npxPath: string;
  npmPath: string;
  textlintrcPath: string;
  workingDirectory: string;
}

export const DEFAULT_SETTINGS: TextlintPluginSettings = {
  lintOnActiveFileChanged: true,
  lintOnSaved: true,
  lintOnTextChanged: false,
  lintDebounceMs: 500,

  minimumSeverityInEditingView: 1,
  minimumSeverityToShowGutter: 2,
  minimumSeverityInDiagnosticsView: 0,

  showGutter: true,

  foldersToIgnore: [],

  useGlobal: false,
  textlintPath: 'textlint',
  npxPath: 'npx',
  npmPath: 'npm',
  textlintrcPath: '',
  workingDirectory: '',
};

export class TextlintPluginSettingTab extends PluginSettingTab {
  plugin: TextlintPlugin;

  constructor(app: App, plugin: TextlintPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h1', { text: 'Obsidian textlint Settings' });

    this.addLintTriggerSection(containerEl);
    this.addDisplaySection(containerEl);
    this.addSetupSection(containerEl);
    this.addTextlintrcSection(containerEl);
    this.addInstalledRulesSection(containerEl);
    this.addPluginManagerSection(containerEl);
  }

  private addLintTriggerSection(containerEl: HTMLElement) {
    containerEl.createEl('h2', { text: 'Lint triggers' });

    new Setting(containerEl)
      .setName('Lint on save')
      .setDesc('Requires reload to take effect')
      .addToggle((toggle) => {
        toggle.setValue(this.plugin.settings.lintOnSaved).onChange(async (v) => {
          this.plugin.settings.lintOnSaved = v;
          await this.plugin.saveSettings();
        });
      });

    new Setting(containerEl)
      .setName('Lint on active file changed')
      .setDesc('Requires reload to take effect')
      .addToggle((toggle) => {
        toggle.setValue(this.plugin.settings.lintOnActiveFileChanged).onChange(async (v) => {
          this.plugin.settings.lintOnActiveFileChanged = v;
          await this.plugin.saveSettings();
        });
      });

    new Setting(containerEl)
      .setName('Lint on text changed')
      .addToggle((toggle) => {
        toggle.setValue(this.plugin.settings.lintOnTextChanged).onChange(async (v) => {
          this.plugin.settings.lintOnTextChanged = v;
          await this.plugin.saveSettings();
        });
      });

    new Setting(containerEl)
      .setName('Lint debounce time (ms)')
      .setDesc('Delay before running textlint after a trigger. Set to 0 to disable debounce.')
      .addText((text) => {
        text
          .setPlaceholder('500')
          .setValue(String(this.plugin.settings.lintDebounceMs))
          .onChange(async (v) => {
            const parsed = Number(v);
            const next = Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 500;
            this.plugin.settings.lintDebounceMs = next;
            await this.plugin.saveSettings();
          });
      });

    new Setting(containerEl)
      .setName('Folders to ignore')
      .setDesc('Folder paths to skip linting, one per line')
      .addTextArea((textArea) => {
        textArea.setValue(this.plugin.settings.foldersToIgnore.join('\n')).onChange(async (value) => {
          this.plugin.settings.foldersToIgnore = value.split('\n').filter(Boolean);
          await this.plugin.saveSettings();
        });
      });
  }

  private addDisplaySection(containerEl: HTMLElement) {
    containerEl.createEl('h2', { text: 'Display' });

    new Setting(containerEl).setName('Minimum severity in editing view').addDropdown((dropdown) => {
      dropdown.addOptions(SEVERITY_OPTIONS);
      dropdown.setValue(String(this.plugin.settings.minimumSeverityInEditingView));
      dropdown.onChange(async (v: string) => {
        this.plugin.settings.minimumSeverityInEditingView = Number(v) as TextlintRuleSeverityLevel;
        await this.plugin.saveSettings();
      });
    });

    new Setting(containerEl).setName('Show lint gutter').addToggle((toggle) => {
      toggle.setValue(this.plugin.settings.showGutter).onChange(async (v) => {
        this.plugin.settings.showGutter = v;
        await this.plugin.saveSettings();
        this.display();
      });
    });

    if (this.plugin.settings.showGutter) {
      new Setting(containerEl)
        .setName('Minimum severity to show lint gutter')
        .addDropdown((dropdown) => {
          dropdown.addOptions(SEVERITY_OPTIONS);
          dropdown.setValue(String(this.plugin.settings.minimumSeverityToShowGutter));
          dropdown.onChange(async (v: string) => {
            this.plugin.settings.minimumSeverityToShowGutter = Number(v) as TextlintRuleSeverityLevel;
            await this.plugin.saveSettings();
          });
        });
    }

    new Setting(containerEl).setName('Minimum severity for diagnostics view').addDropdown((dropdown) => {
      dropdown.addOptions(SEVERITY_OPTIONS);
      dropdown.setValue(String(this.plugin.settings.minimumSeverityInDiagnosticsView));
      dropdown.onChange(async (v: string) => {
        this.plugin.settings.minimumSeverityInDiagnosticsView = Number(v) as TextlintRuleSeverityLevel;
        await this.plugin.saveSettings();
      });
    });
  }

  private addSetupSection(containerEl: HTMLElement) {
    containerEl.createEl('h2', { text: 'textlint setup' });

    new Setting(containerEl)
      .setName('Use global textlint')
      .setDesc('Run the globally installed textlint instead of using npx. Plugins are also installed globally.')
      .addToggle((toggle) => {
        toggle.setValue(this.plugin.settings.useGlobal).onChange(async (v) => {
          this.plugin.settings.useGlobal = v;
          await this.plugin.saveSettings();
          this.display();
        });
      });

    if (this.plugin.settings.useGlobal) {
      new Setting(containerEl)
        .setName('textlint path')
        .setDesc('Path to the textlint executable (e.g. C:\\Users\\user\\AppData\\Roaming\\npm\\textlint.cmd)')
        .addText((text) => {
          text
            .setPlaceholder('textlint')
            .setValue(this.plugin.settings.textlintPath)
            .onChange(async (v) => {
              this.plugin.settings.textlintPath = v || 'textlint';
              await this.plugin.saveSettings();
            });
        });
    } else {
      new Setting(containerEl)
        .setName('Working directory')
        .setDesc('Directory where textlint is installed (contains node_modules). Leave empty to use vault root.')
        .addText((text) => {
          text
            .setPlaceholder('C:\\path\\to\\project')
            .setValue(this.plugin.settings.workingDirectory)
            .onChange(async (v) => {
              this.plugin.settings.workingDirectory = v;
              await this.plugin.saveSettings();
            });
        });

      new Setting(containerEl)
        .setName('npx path')
        .setDesc('Path to npx executable')
        .addText((text) => {
          text
            .setPlaceholder('npx')
            .setValue(this.plugin.settings.npxPath)
            .onChange(async (v) => {
              this.plugin.settings.npxPath = v || 'npx';
              await this.plugin.saveSettings();
            });
        });
    }

    new Setting(containerEl)
      .setName('npm path')
      .setDesc('Path to npm executable (used for plugin installation)')
      .addText((text) => {
        text
          .setPlaceholder('npm')
          .setValue(this.plugin.settings.npmPath)
          .onChange(async (v) => {
            this.plugin.settings.npmPath = v || 'npm';
            await this.plugin.saveSettings();
          });
      });
  }

  private addTextlintrcSection(containerEl: HTMLElement) {
    containerEl.createEl('h2', { text: 'textlintrc' });

    // --- Plugin .textlintrc ---
    containerEl.createEl('h3', { text: 'Plugin .textlintrc' });

    const pluginRcPath = this.getPluginTextlintrcPath();
    const pathDesc = containerEl.createEl('p');
    pathDesc.textContent = `Path: ${pluginRcPath}`;
    pathDesc.style.color = 'var(--text-muted)';
    pathDesc.style.fontSize = 'smaller';

    const autoDesc = containerEl.createEl('p');
    autoDesc.textContent = 'Used automatically when "Custom textlintrc path" below is empty.';
    autoDesc.style.color = 'var(--text-muted)';
    autoDesc.style.fontSize = 'smaller';

    let pluginRcContent = '';
    try {
      pluginRcContent = readFileSync(pluginRcPath, 'utf-8');
    } catch (_e) {
      pluginRcContent = '{\n  "rules": {}\n}';
    }

    const pluginRcSetting = new Setting(containerEl).setName('Edit plugin .textlintrc');
    let pluginRcTextareaEl: HTMLTextAreaElement;
    pluginRcSetting.addTextArea((ta) => {
      ta.inputEl.style.width = '100%';
      ta.inputEl.style.height = '200px';
      ta.inputEl.style.fontFamily = 'monospace';
      ta.setValue(pluginRcContent);
      pluginRcTextareaEl = ta.inputEl;
    });
    pluginRcSetting.addButton((btn) => {
      btn.setButtonText('Save').onClick(() => {
        try {
          writeFileSync(pluginRcPath, pluginRcTextareaEl.value, 'utf-8');
          new Notice('[textlint] Saved: ' + pluginRcPath);
        } catch (e) {
          new Notice('[textlint] Failed to save: ' + e.message);
        }
      });
    });

    // --- Custom textlintrc path ---
    containerEl.createEl('h3', { text: 'Custom textlintrc path' });

    new Setting(containerEl)
      .setName('textlintrc path')
      .setDesc('Override: path to .textlintrc file (absolute, or relative to vault root). Leave empty to use plugin .textlintrc.')
      .addText((text) => {
        text
          .setPlaceholder('.textlintrc or /absolute/path/.textlintrc')
          .setValue(this.plugin.settings.textlintrcPath)
          .onChange(async (v) => {
            this.plugin.settings.textlintrcPath = v;
            await this.plugin.saveSettings();
            this.display();
          });
      });

    const fullPath = this.resolveAbsolutePath(this.plugin.settings.textlintrcPath);
    if (!fullPath) return;

    let content = '';
    try {
      content = readFileSync(fullPath, 'utf-8');
    } catch (_e) {
      content = '';
    }

    const editorSetting = new Setting(containerEl)
      .setName('Edit custom textlintrc')
      .setDesc(fullPath);

    let textareaEl: HTMLTextAreaElement;
    editorSetting.addTextArea((ta) => {
      ta.inputEl.style.width = '100%';
      ta.inputEl.style.height = '200px';
      ta.inputEl.style.fontFamily = 'monospace';
      ta.setValue(content);
      textareaEl = ta.inputEl;
    });

    editorSetting.addButton((btn) => {
      btn.setButtonText('Save').onClick(() => {
        try {
          writeFileSync(fullPath, textareaEl.value, 'utf-8');
          new Notice('[textlint] Saved: ' + fullPath);
        } catch (e) {
          new Notice('[textlint] Failed to save: ' + e.message);
        }
      });
    });
  }

  private getPluginTextlintrcPath(): string {
    return join(this.plugin.getVaultBasePath(), this.plugin.manifest.dir ?? '', '.textlintrc');
  }

  private addInstalledRulesSection(containerEl: HTMLElement) {
    containerEl.createEl('h2', { text: 'Installed rules' });

    let rulesContainer: HTMLElement | undefined;

    new Setting(containerEl)
      .setName('Effective config')
      .setDesc('Run textlint --print-config to display configured rules')
      .addButton((btn) => {
        btn.setButtonText('Refresh').onClick(async () => {
          if (!rulesContainer) return;
          btn.setButtonText('Loading...');
          btn.setDisabled(true);
          try {
            await this.loadInstalledRules(rulesContainer);
          } finally {
            btn.setButtonText('Refresh');
            btn.setDisabled(false);
          }
        });
      });

    rulesContainer = containerEl.createDiv();
  }

  private async loadInstalledRules(container: HTMLElement) {
    container.empty();
    const loading = container.createEl('p', { text: 'Loading...' });
    loading.style.color = 'var(--text-muted)';

    const basePath = this.plugin.getVaultBasePath();
    const { textlintrcPath, workingDirectory, npxPath, useGlobal, textlintPath } = this.plugin.settings;

    let resolvedRcPath: string | undefined;
    if (textlintrcPath) {
      resolvedRcPath = isAbsolute(textlintrcPath) ? textlintrcPath : join(basePath, textlintrcPath);
    } else {
      const pluginRcPath = this.getPluginTextlintrcPath();
      try {
        readFileSync(pluginRcPath);
        resolvedRcPath = pluginRcPath;
      } catch {
        // no plugin rc file
      }
    }
    const resolvedWorkDir = workingDirectory || basePath;

    try {
      let { rules, rawOutput } = await runTextlintPrintConfig({
        npxPath,
        textlintrcPath: resolvedRcPath,
        workingDirectory: resolvedWorkDir,
        useGlobal,
        textlintPath,
      });

      // Fallback: rawOutput may contain valid JSON with rules if initial extraction missed them
      if (Object.keys(rules).length === 0 && rawOutput) {
        try {
          const jsonStart = rawOutput.indexOf('{');
          const jsonEnd = rawOutput.lastIndexOf('}');
          if (jsonStart !== -1 && jsonEnd > jsonStart) {
            const fallback = JSON.parse(rawOutput.slice(jsonStart, jsonEnd + 1)) as { rules?: Record<string, RuleConfig> };
            if (fallback.rules && Object.keys(fallback.rules).length > 0) {
              rules = fallback.rules;
            }
          }
        } catch {
          // ignore
        }
      }

      container.empty();

      const ruleEntries = Object.entries(rules);
      if (ruleEntries.length === 0) {
        if (rawOutput) {
          const pre = container.createEl('pre');
          pre.style.cssText = 'font-size:var(--font-ui-smaller);overflow-x:auto;white-space:pre-wrap;word-break:break-all;color:var(--text-muted);background:var(--background-secondary);padding:8px;border-radius:4px;margin-top:4px';
          try {
            const jsonStart = rawOutput.indexOf('{');
            const jsonEnd = rawOutput.lastIndexOf('}');
            pre.textContent = jsonStart !== -1 && jsonEnd > jsonStart
              ? JSON.stringify(JSON.parse(rawOutput.slice(jsonStart, jsonEnd + 1)), null, 2)
              : rawOutput;
          } catch {
            pre.textContent = rawOutput;
          }
        } else {
          const empty = container.createEl('p', { text: 'No rules configured.' });
          empty.style.color = 'var(--text-muted)';
        }
        return;
      }

      const SEVERITY_LABEL: Record<number, string> = { 0: 'info', 1: 'warning', 2: 'error' };
      const SEVERITY_CLASS: Record<number, string> = {
        0: 'textlint-plugin-severity-info',
        1: 'textlint-plugin-severity-warning',
        2: 'textlint-plugin-severity-error',
      };

      const table = container.createEl('table');
      table.style.cssText = 'width:100%;border-collapse:collapse;font-size:var(--font-ui-small);margin-top:0.5em';

      for (const [name, config] of ruleEntries) {
        const row = table.createEl('tr');
        row.style.borderBottom = '1px solid var(--background-modifier-border)';

        const nameCell = row.createEl('td');
        nameCell.style.cssText = 'padding:3px 4px;font-family:var(--font-monospace)';
        nameCell.textContent = name;

        const sevCell = row.createEl('td');
        sevCell.style.cssText = 'padding:3px 4px;text-align:right;white-space:nowrap';

        const sev = resolveSeverity(config);
        const badge = sevCell.createEl('span', { text: SEVERITY_LABEL[sev] ?? 'warning' });
        badge.addClass(SEVERITY_CLASS[sev] ?? 'textlint-plugin-severity-warning');
      }
    } catch (e) {
      container.empty();
      const errEl = container.createEl('p', { text: 'Error: ' + (e as Error).message });
      errEl.style.color = 'var(--color-red)';
    }
  }

  private addPluginManagerSection(containerEl: HTMLElement) {
    containerEl.createEl('h2', { text: 'Plugin manager' });
    const { useGlobal } = this.plugin.settings;
    containerEl.createEl('p', {
      text: useGlobal
        ? 'Install textlint plugins globally (npm install -g).'
        : 'Install textlint plugins into the working directory.',
    }).style.color = 'var(--text-muted)';

    let outputEl: HTMLTextAreaElement;

    new Setting(containerEl)
      .setName('Install textlint')
      .setDesc('Run: npm install -g textlint')
      .addButton((btn) => {
        btn.setButtonText('Install').onClick(async () => {
          btn.setButtonText('Installing...');
          btn.setDisabled(true);
          try {
            const output = await installTextlintPlugin('textlint', this.plugin.getVaultBasePath(), this.plugin.settings.npmPath, true);
            if (outputEl) outputEl.value = output;
            new Notice('[textlint] Installed textlint globally');
          } catch (e) {
            if (outputEl) outputEl.value = 'Error: ' + e.message;
            new Notice('[textlint] Install failed: ' + e.message);
          } finally {
            btn.setButtonText('Install');
            btn.setDisabled(false);
          }
        });
      });

    let packageInput = '';

    const installSetting = new Setting(containerEl)
      .setName('Install plugin')
      .setDesc('Package name, e.g. textlint-rule-spellcheck-tech-word');

    installSetting.addText((text) => {
      text.setPlaceholder('textlint-rule-...').onChange((v) => {
        packageInput = v;
      });
    });

    installSetting.addButton((btn) => {
      btn.setButtonText('Install').onClick(async () => {
        if (!packageInput.trim()) {
          new Notice('[textlint] Enter a package name');
          return;
        }
        const workingDir = this.plugin.settings.workingDirectory || this.plugin.getVaultBasePath();
        btn.setButtonText('Installing...');
        btn.setDisabled(true);
        try {
          const output = await installTextlintPlugin(
            packageInput.trim(),
            workingDir,
            this.plugin.settings.npmPath,
            useGlobal,
          );
          if (outputEl) outputEl.value = output;
          new Notice('[textlint] Installed: ' + packageInput.trim());
        } catch (e) {
          if (outputEl) outputEl.value = 'Error: ' + e.message;
          new Notice('[textlint] Install failed: ' + e.message);
        } finally {
          btn.setButtonText('Install');
          btn.setDisabled(false);
        }
      });
    });

    const outputSetting = new Setting(containerEl).setName('Output');
    outputSetting.addTextArea((ta) => {
      ta.inputEl.style.width = '100%';
      ta.inputEl.style.height = '100px';
      ta.inputEl.style.fontFamily = 'monospace';
      ta.setDisabled(true);
      outputEl = ta.inputEl;
    });
  }

  private resolveAbsolutePath(p: string): string | null {
    if (!p) return null;
    if (isAbsolute(p)) return p;
    return join(this.plugin.getVaultBasePath(), p);
  }
}

function resolveSeverity(config: RuleConfig): number {
  if (typeof config === 'number') return config;
  if (typeof config === 'object' && config !== null && 'severity' in config) {
    return (config as { severity: number }).severity;
  }
  return 1; // default: warning
}
