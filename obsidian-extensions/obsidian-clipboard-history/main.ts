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

interface ClipboardEntry {
  id: string;
  content: string;
  timestamp: number;
}

interface ClipboardHistorySettings {
  maxHistorySize: number;
  saveDirectory: string;
  pollingInterval: number;
}

const DEFAULT_SETTINGS: ClipboardHistorySettings = {
  maxHistorySize: 50,
  saveDirectory: 'Clipboard History',
  pollingInterval: 1000,
};

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
    clearBtn.addEventListener('click', () => {
      this.plugin.clearHistory();
      this.refresh();
    });

    const history = this.plugin.getHistory();

    if (history.length === 0) {
      container.createEl('p', { text: 'No clipboard history yet. Start copying text!', cls: 'ch-empty' });
      return;
    }

    const list = container.createDiv({ cls: 'ch-list' });

    history.forEach((entry) => {
      const item = list.createDiv({ cls: 'ch-item' });

      const meta = item.createDiv({ cls: 'ch-meta' });
      meta.createEl('span', {
        text: new Date(entry.timestamp).toLocaleString(),
        cls: 'ch-timestamp',
      });

      const preview = item.createDiv({ cls: 'ch-preview' });
      preview.setText(
        entry.content.length > 300
          ? entry.content.substring(0, 300) + '…'
          : entry.content
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

      const deleteBtn = actions.createEl('button', { text: '×', cls: 'ch-btn ch-delete-btn' });
      deleteBtn.addEventListener('click', () => {
        this.plugin.removeEntry(entry.id);
        this.refresh();
      });
    });
  }

  async onClose(): Promise<void> {}
}

export default class ClipboardHistoryPlugin extends Plugin {
  settings: ClipboardHistorySettings = { ...DEFAULT_SETTINGS };
  private history: ClipboardEntry[] = [];
  private pollingTimer: number | null = null;
  private lastContent = '';
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private electronClipboard: any;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.initElectronClipboard();

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
      callback: () => {
        this.clearHistory();
        new Notice('Clipboard history cleared.');
      },
    });

    this.addSettingTab(new ClipboardHistorySettingTab(this.app, this));
    this.startPolling();
  }

  onunload(): void {
    this.stopPolling();
  }

  private initElectronClipboard(): void {
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const electron = require('electron');
      this.electronClipboard = electron.clipboard;
    } catch (e) {
      console.error('[ClipboardHistory] Failed to access Electron clipboard:', e);
    }
  }

  startPolling(): void {
    if (this.pollingTimer !== null) return;

    // Snapshot the current clipboard so we don't treat it as a new entry on load
    if (this.electronClipboard) {
      try {
        this.lastContent = this.electronClipboard.readText();
      } catch (_) {}
    }

    this.pollingTimer = window.setInterval(() => this.checkClipboard(), this.settings.pollingInterval);
  }

  stopPolling(): void {
    if (this.pollingTimer !== null) {
      window.clearInterval(this.pollingTimer);
      this.pollingTimer = null;
    }
  }

  private checkClipboard(): void {
    if (!this.electronClipboard) return;
    try {
      const current = this.electronClipboard.readText();
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

    if (this.history.length > this.settings.maxHistorySize) {
      this.history.length = this.settings.maxHistorySize;
    }

    this.refreshView();
  }

  getHistory(): ClipboardEntry[] {
    return this.history;
  }

  clearHistory(): void {
    this.history = [];
    this.refreshView();
  }

  removeEntry(id: string): void {
    this.history = this.history.filter((e) => e.id !== id);
  }

  async saveEntryToFile(entry: ClipboardEntry): Promise<void> {
    const dir = this.settings.saveDirectory;
    const ts = new Date(entry.timestamp)
      .toISOString()
      .replace(/[:.]/g, '-')
      .slice(0, 19);
    const path = normalizePath(`${dir}/clipboard-${ts}.md`);

    if (!(await this.app.vault.adapter.exists(dir))) {
      await this.app.vault.createFolder(dir);
    }

    // Avoid duplicate file names by appending a counter
    let finalPath = path;
    let counter = 1;
    while (await this.app.vault.adapter.exists(finalPath)) {
      finalPath = normalizePath(`${dir}/clipboard-${ts}-${counter++}.md`);
    }

    await this.app.vault.create(finalPath, entry.content);
    new Notice(`Saved: ${finalPath}`);
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

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}

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
      .setDesc('Maximum number of clipboard entries to keep in memory.')
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
            this.plugin.settings.saveDirectory = value;
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
  }
}
