import { App, PluginSettingTab, Setting } from 'obsidian';
import TextlintPlugin from './main';

export class TextlintSettingTab extends PluginSettingTab {
  plugin: TextlintPlugin;

  constructor(app: App, plugin: TextlintPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Textlint Settings' });

    new Setting(containerEl)
      .setName('Lint on file open')
      .setDesc('Automatically run textlint when a file is opened.')
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.lintOnOpen)
          .onChange(async (value) => {
            this.plugin.settings.lintOnOpen = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Folders to ignore')
      .setDesc('Comma-separated list of folder paths to skip (e.g. templates, attachments).')
      .addText((text) =>
        text
          .setPlaceholder('templates, attachments')
          .setValue(this.plugin.settings.foldersToIgnore.join(', '))
          .onChange(async (value) => {
            this.plugin.settings.foldersToIgnore = value
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean);
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('textlint configuration (JSON)')
      .setDesc('Custom textlintrc rules. Changes apply after saving.')
      .addTextArea((textarea) => {
        textarea.inputEl.rows = 10;
        textarea.inputEl.style.width = '100%';
        textarea.inputEl.style.fontFamily = 'monospace';
        textarea
          .setValue(this.plugin.settings.textlintrc)
          .onChange(async (value) => {
            try {
              JSON.parse(value);
              this.plugin.settings.textlintrc = value;
              await this.plugin.saveSettings();
            } catch {
              // ignore invalid JSON until user corrects it
            }
          });
      });
  }
}
