import {
  App,
  ItemView,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  WorkspaceLeaf,
  normalizePath,
} from 'obsidian';

// ============================================================
// Electron clipboard (desktop only)
// ============================================================

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let electronClipboard: any = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  electronClipboard = require('electron').clipboard;
} catch (e) {
  console.error('[ClipboardHistory] Failed to access Electron clipboard:', e);
}

// ============================================================
// Types
// ============================================================

interface ClipboardEntry {
  id: string;
  content: string;
  timestamp: number;
  savedAt?: number;
  savedFilePath?: string;
  savedGroupEntry?: boolean;
}

interface PluginSettings {
  maxHistorySize: number;
  saveDirectory: string;
  pollingInterval: number; // ms
  expiryHours: number;
  groupByDay: boolean;
  autoSave: boolean;
  fileTemplate: string;
  entryTemplate: string;
  fileTemplatePath: string;
  entryTemplatePath: string;
}

interface PluginData {
  settings: PluginSettings;
  history: ClipboardEntry[];
}

const DEFAULT_FILE_TEMPLATE = '{{content}}';
const DEFAULT_ENTRY_TEMPLATE = '\n## {{time}}\n\n{{content}}\n';

const DEFAULT_SETTINGS: PluginSettings = {
  maxHistorySize: 50,
  saveDirectory: 'Clipboard History',
  pollingInterval: 1000,
  expiryHours: 24,
  groupByDay: false,
  autoSave: false,
  fileTemplate: DEFAULT_FILE_TEMPLATE,
  entryTemplate: DEFAULT_ENTRY_TEMPLATE,
  fileTemplatePath: '',
  entryTemplatePath: '',
};

// ============================================================
// Helpers
// ============================================================

function formatTimestamp(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function toSafeFileName(content: string): string {
  const firstLine = content.split('\n')[0].trim().slice(0, 50);
  return firstLine.replace(/[\\/:*?"<>|]/g, '_') || 'clipboard';
}

function applyTemplate(template: string, entry: ClipboardEntry): string {
  const d = new Date(entry.timestamp);
  const pad = (n: number) => String(n).padStart(2, '0');
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return template
    .replace(/\{\{content\}\}/g, entry.content)
    .replace(/\{\{date\}\}/g, date)
    .replace(/\{\{time\}\}/g, time)
    .replace(/\{\{datetime\}\}/g, `${date} ${time}`)
    .replace(/\{\{title\}\}/g, toSafeFileName(entry.content));
}

// ============================================================
// View
// ============================================================

export const VIEW_TYPE_CLIPBOARD = 'clipboard-history-view';

class ClipboardHistoryView extends ItemView {
  plugin: ClipboardHistoryPlugin;

  constructor(leaf: WorkspaceLeaf, plugin: ClipboardHistoryPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string {
    return VIEW_TYPE_CLIPBOARD;
  }

  getDisplayText(): string {
    return 'Clipboard History';
  }

  getIcon(): string {
    return 'clipboard-list';
  }

  async onOpen(): Promise<void> {
    this.refresh();
  }

  refresh(): void {
    const container = this.containerEl.children[1] as HTMLElement;
    container.empty();
    container.addClass('ch-container');

    const header = container.createDiv({ cls: 'ch-header' });
    header.createEl('h4', { text: 'Clipboard History' });

    const clearBtn = header.createEl('button', { text: 'Clear All', cls: 'ch-btn mod-warning' });
    clearBtn.addEventListener('click', async () => {
      if (!confirm('Clear all clipboard history?')) return;
      await this.plugin.clearHistory();
      this.refresh();
    });

    const history = this.plugin.getHistory();

    if (history.length === 0) {
      container.createEl('p', {
        text: 'No clipboard history yet. Start copying text!',
        cls: 'ch-empty',
      });
      return;
    }

    const list = container.createDiv({ cls: 'ch-list' });

    history.forEach((entry) => {
      const item = list.createDiv({ cls: 'ch-item' });

      const meta = item.createDiv({ cls: 'ch-meta' });
      meta.createEl('span', {
        text: formatTimestamp(entry.timestamp),
        cls: 'ch-timestamp',
      });
      if (entry.savedAt) {
        meta.createEl('span', { text: '保存済', cls: 'ch-saved-badge' });
      }

      const preview = item.createDiv({ cls: 'ch-preview' });
      preview.setText(
        entry.content.length > 300 ? entry.content.substring(0, 300) + '…' : entry.content
      );

      const actions = item.createDiv({ cls: 'ch-actions' });

      const copyBtn = actions.createEl('button', { text: 'Copy', cls: 'ch-btn' });
      copyBtn.addEventListener('click', async () => {
        await navigator.clipboard.writeText(entry.content);
        new Notice('Copied to clipboard!');
      });

      const saveBtn = actions.createEl('button', { text: 'Save to File', cls: 'ch-btn mod-cta' });
      saveBtn.addEventListener('click', async () => {
        await this.plugin.saveEntryToFile(entry);
      });

      if (entry.savedFilePath && !entry.savedGroupEntry) {
        const deleteFileBtn = actions.createEl('button', { text: 'Remove Saved File', cls: 'ch-btn ch-delete-file-btn' });
        deleteFileBtn.addEventListener('click', async () => {
          if (!confirm(`Remove saved file?\n${entry.savedFilePath}`)) return;
          await this.plugin.deleteSavedFile(entry);
        });
      }

      const deleteBtn = actions.createEl('button', { text: '×', cls: 'ch-btn ch-delete-btn' });
      deleteBtn.addEventListener('click', () => {
        this.plugin.removeEntry(entry.id);
        this.refresh();
      });
    });
  }

  async onClose(): Promise<void> {}
}

// ============================================================
// Plugin
// ============================================================

export default class ClipboardHistoryPlugin extends Plugin {
  settings: PluginSettings = { ...DEFAULT_SETTINGS };
  private history: ClipboardEntry[] = [];
  private pollingTimer: number | null = null;
  private lastContent = '';

  async onload(): Promise<void> {
    await this.loadPluginData();

    this.registerView(VIEW_TYPE_CLIPBOARD, (leaf) => new ClipboardHistoryView(leaf, this));

    this.addRibbonIcon('clipboard-list', 'Clipboard History', () => {
      this.activateView();
    });

    this.addCommand({
      id: 'open-clipboard-history',
      name: 'Open Clipboard History',
      callback: () => this.activateView(),
    });

    this.addCommand({
      id: 'save-latest-to-file',
      name: 'Save Latest Clipboard Entry to File',
      callback: async () => {
        const latest = this.history[0];
        if (latest) {
          await this.saveEntryToFile(latest);
        } else {
          new Notice('No clipboard history yet.');
        }
      },
    });

    this.addCommand({
      id: 'clear-clipboard-history',
      name: 'Clear Clipboard History',
      callback: async () => {
        await this.clearHistory();
        new Notice('Clipboard history cleared.');
      },
    });

    this.addSettingTab(new ClipboardHistorySettingTab(this.app, this));
    this.startPolling();
  }

  onunload(): void {
    this.stopPolling();
  }

  // ----------------------------------------------------------
  // Polling
  // ----------------------------------------------------------

  startPolling(): void {
    if (this.pollingTimer !== null) return;

    if (electronClipboard) {
      try {
        this.lastContent = electronClipboard.readText();
      } catch (_) {}
    }

    this.pollingTimer = window.setInterval(
      () => this.checkClipboard(),
      this.settings.pollingInterval
    );
  }

  stopPolling(): void {
    if (this.pollingTimer !== null) {
      window.clearInterval(this.pollingTimer);
      this.pollingTimer = null;
    }
  }

  private checkClipboard(): void {
    if (!electronClipboard) return;
    try {
      const current = electronClipboard.readText();
      if (current && current !== this.lastContent) {
        this.lastContent = current;
        this.addToHistory(current);
      }
    } catch (_) {}
  }

  private addToHistory(content: string): void {
    if (!content.trim()) return;

    const entry: ClipboardEntry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      content,
      timestamp: Date.now(),
    };

    this.history.unshift(entry);
    this.pruneHistory();
    this.refreshView();
    this.savePluginDataAsync();

    if (this.settings.autoSave) {
      this.saveEntryToFile(entry).catch((e) =>
        console.error('[ClipboardHistory] auto-save failed:', e)
      );
    }
  }

  // ----------------------------------------------------------
  // History management
  // ----------------------------------------------------------

  private pruneHistory(): void {
    const cutoff = Date.now() - this.settings.expiryHours * 60 * 60 * 1000;
    this.history = this.history.filter((e) => e.timestamp >= cutoff);
    if (this.history.length > this.settings.maxHistorySize) {
      this.history.length = this.settings.maxHistorySize;
    }
  }

  getHistory(): ClipboardEntry[] {
    return this.history;
  }

  async clearHistory(): Promise<void> {
    this.history = [];
    this.refreshView();
    await this.savePluginData();
  }

  removeEntry(id: string): void {
    this.history = this.history.filter((e) => e.id !== id);
    this.savePluginDataAsync();
  }

  private async getEffectiveTemplate(templatePath: string, fallback: string): Promise<string> {
    if (templatePath) {
      const path = normalizePath(templatePath);
      if (await this.app.vault.adapter.exists(path)) {
        return await this.app.vault.adapter.read(path);
      }
      new Notice(`Template file not found: ${templatePath}`);
    }
    return fallback;
  }

  async saveEntryToFile(entry: ClipboardEntry): Promise<void> {
    const dir = normalizePath(this.settings.saveDirectory);

    if (!(await this.app.vault.adapter.exists(dir))) {
      await this.app.vault.createFolder(dir);
    }

    let filePath: string;

    if (this.settings.groupByDay) {
      const d = new Date(entry.timestamp);
      const pad = (n: number) => String(n).padStart(2, '0');
      const dateStr = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
      filePath = normalizePath(`${dir}/${dateStr}.md`);
      const tpl = await this.getEffectiveTemplate(this.settings.entryTemplatePath, this.settings.entryTemplate);
      const entryContent = applyTemplate(tpl, entry);
      if (await this.app.vault.adapter.exists(filePath)) {
        const existing = await this.app.vault.adapter.read(filePath);
        await this.app.vault.adapter.write(filePath, existing + entryContent);
      } else {
        await this.app.vault.create(filePath, `# ${dateStr}\n${entryContent}`);
      }
    } else {
      const datePrefix = formatTimestamp(entry.timestamp).replace(/[: ]/g, '-');
      const namePart = toSafeFileName(entry.content);
      filePath = normalizePath(`${dir}/${datePrefix}_${namePart}.md`);
      let counter = 1;
      while (await this.app.vault.adapter.exists(filePath)) {
        filePath = normalizePath(`${dir}/${datePrefix}_${namePart}_${counter++}.md`);
      }
      const tpl = await this.getEffectiveTemplate(this.settings.fileTemplatePath, this.settings.fileTemplate);
      await this.app.vault.create(filePath, applyTemplate(tpl, entry));
    }

    new Notice(`Saved: ${filePath}`);
    entry.savedAt = Date.now();
    entry.savedFilePath = filePath;
    entry.savedGroupEntry = this.settings.groupByDay;
    this.savePluginDataAsync();
    this.refreshView();
  }

  async deleteSavedFile(entry: ClipboardEntry): Promise<void> {
    if (!entry.savedFilePath) return;
    const path = normalizePath(entry.savedFilePath);
    if (await this.app.vault.adapter.exists(path)) {
      await this.app.vault.adapter.remove(path);
      new Notice(`Deleted: ${path}`);
    } else {
      new Notice(`File not found: ${path}`);
    }
    entry.savedAt = undefined;
    entry.savedFilePath = undefined;
    this.savePluginDataAsync();
    this.refreshView();
  }

  async activateView(): Promise<void> {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE_CLIPBOARD)[0];
    if (!leaf) {
      leaf = workspace.getRightLeaf(false) ?? workspace.getLeaf(true);
      await leaf.setViewState({ type: VIEW_TYPE_CLIPBOARD, active: true });
    }
    workspace.revealLeaf(leaf);
  }

  private refreshView(): void {
    this.app.workspace.getLeavesOfType(VIEW_TYPE_CLIPBOARD).forEach((leaf) => {
      if (leaf.view instanceof ClipboardHistoryView) {
        leaf.view.refresh();
      }
    });
  }

  // ----------------------------------------------------------
  // Persistence
  // ----------------------------------------------------------

  async saveSettings(): Promise<void> {
    this.pruneHistory();
    await this.savePluginData();
  }

  private async loadPluginData(): Promise<void> {
    const raw = await this.loadData();
    if (raw && typeof raw === 'object' && 'settings' in raw) {
      const data = raw as Partial<PluginData>;
      this.settings = Object.assign({}, DEFAULT_SETTINGS, data.settings ?? {});
      this.history = data.history ?? [];
    } else {
      // legacy: only settings were saved
      this.settings = Object.assign({}, DEFAULT_SETTINGS, raw ?? {});
      this.history = [];
    }
    this.pruneHistory();
  }

  private async savePluginData(): Promise<void> {
    const data: PluginData = { settings: this.settings, history: this.history };
    await this.saveData(data);
  }

  private savePluginDataAsync(): void {
    this.savePluginData().catch((e) =>
      console.error('[ClipboardHistory] save failed:', e)
    );
  }
}

// ============================================================
// Settings Tab
// ============================================================

class ClipboardHistorySettingTab extends PluginSettingTab {
  plugin: ClipboardHistoryPlugin;

  constructor(app: App, plugin: ClipboardHistoryPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Clipboard History Settings' });

    new Setting(containerEl)
      .setName('Max history size')
      .setDesc('Maximum number of clipboard entries to keep.')
      .addText((text) =>
        text
          .setPlaceholder('50')
          .setValue(this.plugin.settings.maxHistorySize.toString())
          .onChange(async (value) => {
            const num = parseInt(value, 10);
            if (!isNaN(num) && num > 0) {
              this.plugin.settings.maxHistorySize = num;
              await this.plugin.saveSettings();
            }
          })
      );

    new Setting(containerEl)
      .setName('Save directory')
      .setDesc('Vault folder where clipboard entries are saved as Markdown notes.')
      .addText((text) =>
        text
          .setPlaceholder('Clipboard History')
          .setValue(this.plugin.settings.saveDirectory)
          .onChange(async (value) => {
            this.plugin.settings.saveDirectory = value.trim() || 'Clipboard History';
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Polling interval (ms)')
      .setDesc('How often the plugin checks for clipboard changes. Minimum 200 ms.')
      .addText((text) =>
        text
          .setPlaceholder('1000')
          .setValue(this.plugin.settings.pollingInterval.toString())
          .onChange(async (value) => {
            const num = parseInt(value, 10);
            if (!isNaN(num) && num >= 200) {
              this.plugin.settings.pollingInterval = num;
              await this.plugin.saveSettings();
              this.plugin.stopPolling();
              this.plugin.startPolling();
            }
          })
      );

    new Setting(containerEl)
      .setName('History expiry (hours)')
      .setDesc('Entries older than this are automatically removed. Set 0 to keep forever.')
      .addText((text) =>
        text
          .setPlaceholder('24')
          .setValue(this.plugin.settings.expiryHours.toString())
          .onChange(async (value) => {
            const num = parseInt(value, 10);
            if (!isNaN(num) && num >= 0) {
              this.plugin.settings.expiryHours = num;
              await this.plugin.saveSettings();
            }
          })
      );

    new Setting(containerEl)
      .setName('Group entries by day')
      .setDesc('When saving, append all entries for the same day into a single daily file (YYYY-MM-DD.md) instead of creating individual files.')
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.groupByDay)
          .onChange(async (value) => {
            this.plugin.settings.groupByDay = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Auto-save to file')
      .setDesc('Automatically save each new clipboard entry to a file. Entries remain in history until you delete them manually.')
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.autoSave)
          .onChange(async (value) => {
            this.plugin.settings.autoSave = value;
            await this.plugin.saveSettings();
          })
      );

    containerEl.createEl('h3', { text: 'Templates' });
    containerEl.createEl('p', {
      text: 'Available variables: {{content}}, {{date}}, {{time}}, {{datetime}}, {{title}}',
      cls: 'ch-setting-desc',
    });
    containerEl.createEl('p', {
      text: 'Specify a vault file path to load the template from a file. If the path is empty or the file does not exist, the text template below is used.',
      cls: 'ch-setting-desc',
    });

    new Setting(containerEl)
      .setName('File template path')
      .setDesc('Vault path to a template file for individual files (e.g. Templates/clipboard-file.md). Leave blank to use the text template below.')
      .addText((text) =>
        text
          .setPlaceholder('Templates/clipboard-file.md')
          .setValue(this.plugin.settings.fileTemplatePath)
          .onChange(async (value) => {
            this.plugin.settings.fileTemplatePath = value.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('File template')
      .setDesc('Fallback template for individual files (used when "Group entries by day" is off and no template path is set).')
      .addTextArea((ta) =>
        ta
          .setPlaceholder(DEFAULT_FILE_TEMPLATE)
          .setValue(this.plugin.settings.fileTemplate)
          .onChange(async (value) => {
            this.plugin.settings.fileTemplate = value || DEFAULT_FILE_TEMPLATE;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Entry template path')
      .setDesc('Vault path to a template file for daily entries (e.g. Templates/clipboard-entry.md). Leave blank to use the text template below.')
      .addText((text) =>
        text
          .setPlaceholder('Templates/clipboard-entry.md')
          .setValue(this.plugin.settings.entryTemplatePath)
          .onChange(async (value) => {
            this.plugin.settings.entryTemplatePath = value.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Entry template')
      .setDesc('Fallback template for each entry in a daily file (used when "Group entries by day" is on and no template path is set).')
      .addTextArea((ta) =>
        ta
          .setPlaceholder(DEFAULT_ENTRY_TEMPLATE)
          .setValue(this.plugin.settings.entryTemplate)
          .onChange(async (value) => {
            this.plugin.settings.entryTemplate = value || DEFAULT_ENTRY_TEMPLATE;
            await this.plugin.saveSettings();
          })
      );
  }
}
