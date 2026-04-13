import {
  App,
  FileSystemAdapter,
  Menu,
  MenuItem,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TAbstractFile,
  TFile,
} from 'obsidian';
import { readFileSync } from 'fs';
import { spawn } from 'child_process';

// ---------------------------------------------------------------------------
// terminal-launcher config.json の型定義
// ---------------------------------------------------------------------------

interface TerminalEntry {
  title: string;
  type: 'wsl' | 'cmd' | 'powershell';
  mode: 'wt' | 'direct';
  launch?: 'auto' | 'manual';
  distro?: string;
  user?: string;
  dir?: string;
  cmd?: string;
}

interface TerminalLauncherConfig {
  entries: TerminalEntry[];
}

// ---------------------------------------------------------------------------
// プラグイン設定
// ---------------------------------------------------------------------------

interface TerminalLauncherSettings {
  /** terminal-launcher の config.json の Windows パス */
  configPath: string;
  /** Send.ps1 の Windows パス */
  scriptPath: string;
}

const DEFAULT_SETTINGS: TerminalLauncherSettings = {
  configPath: 'C:\\tools\\terminal-launcher\\config.json',
  scriptPath: 'C:\\tools\\terminal-launcher\\Send.ps1',
};

// ---------------------------------------------------------------------------
// エントリ選択モーダル
// ---------------------------------------------------------------------------

class EntrySelectModal extends Modal {
  private entries: TerminalEntry[];
  private fileName: string;
  private onChoose: (entry: TerminalEntry) => void;

  constructor(
    app: App,
    entries: TerminalEntry[],
    fileName: string,
    onChoose: (entry: TerminalEntry) => void,
  ) {
    super(app);
    this.entries = entries;
    this.fileName = fileName;
    this.onChoose = onChoose;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: `Terminal Launcher: ${this.fileName}` });

    if (this.entries.length === 0) {
      contentEl.createEl('p', { text: 'config.json にエントリが見つかりませんでした。' });
      return;
    }

    this.entries.forEach((entry) => {
      const btn = contentEl.createEl('button');
      btn.style.display = 'block';
      btn.style.width = '100%';
      btn.style.marginBottom = '6px';
      btn.style.textAlign = 'left';
      btn.style.padding = '8px 12px';
      btn.style.cursor = 'pointer';

      // タイトル
      const titleEl = btn.createEl('span', { text: entry.title });
      titleEl.style.fontWeight = 'bold';

      // cmd を補足表示
      if (entry.cmd) {
        btn.createEl('span', {
          text: `  ${entry.cmd}`,
          attr: { style: 'opacity: 0.6; font-size: 0.85em; margin-left: 8px;' },
        });
      }

      // manual エントリはバッジ表示
      if (entry.launch === 'manual') {
        btn.createEl('span', {
          text: ' [manual]',
          attr: { style: 'opacity: 0.5; font-size: 0.8em; margin-left: 4px;' },
        });
      }

      btn.addEventListener('click', () => {
        this.close();
        this.onChoose(entry);
      });
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// プラグイン本体
// ---------------------------------------------------------------------------

export default class TerminalLauncherPlugin extends Plugin {
  settings: TerminalLauncherSettings = DEFAULT_SETTINGS;

  async onload() {
    await this.loadSettings();

    // コマンドパレット: アクティブノートをコンテキストにエントリ選択
    this.addCommand({
      id: 'launch-terminal-entry',
      name: 'Launch terminal entry',
      checkCallback: (checking: boolean) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking) {
            this.triggerLaunch(file);
          }
          return true;
        }
        return false;
      },
    });

    // 右クリックメニュー: ファイルエクスプローラーのコンテキストメニュー
    this.registerEvent(
      this.app.workspace.on(
        'file-menu',
        (menu: Menu, abstractFile: TAbstractFile) => {
          if (!(abstractFile instanceof TFile)) return;
          const file = abstractFile;
          menu.addItem((item: MenuItem) => {
            item
              .setTitle('Terminal Launcher で起動')
              .setIcon('terminal')
              .onClick(() => setTimeout(() => this.triggerLaunch(file), 50));
          });
        }
      )
    );

    this.addSettingTab(new TerminalLauncherSettingTab(this.app, this));
  }

  // ---------------------------------------------------------------------------
  // 起動フロー
  // ---------------------------------------------------------------------------

  private triggerLaunch(file: TFile): void {
    const entries = this.loadConfig();
    if (!entries) return;

    new EntrySelectModal(
      this.app,
      entries,
      file.name,
      (entry) => this.launchEntry(entry, file),
    ).open();
  }

  private loadConfig(): TerminalEntry[] | null {
    try {
      const raw = readFileSync(this.settings.configPath, 'utf-8');
      const config = JSON.parse(raw) as TerminalLauncherConfig;
      return config.entries ?? [];
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`Terminal Launcher: config.json の読み込みに失敗しました\n${msg}`, 8000);
      return null;
    }
  }

  private launchEntry(entry: TerminalEntry, file: TFile): void {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) {
      new Notice('Terminal Launcher: FileSystemAdapter が利用できません');
      return;
    }

    const basePath = adapter.getBasePath();
    // Obsidian は Windows 上でも file.path はスラッシュ区切りのため変換
    const contextFile = `${basePath}\\${file.path.replace(/\//g, '\\')}`;

    const args = [
      '-NonInteractive',
      '-WindowStyle', 'Hidden',
      '-File', this.settings.scriptPath,
      '-Name', entry.title,
      '-ContextFile', contextFile,
    ];

    const proc = spawn('powershell.exe', args, {
      detached: true,
      stdio: 'ignore',
    });
    proc.unref();

    new Notice(`Terminal Launcher: "${entry.title}" を起動しました`);
  }

  async loadSettings() {
    const loaded = await this.loadData();
    this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded);
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }
}

// ---------------------------------------------------------------------------
// 設定タブ
// ---------------------------------------------------------------------------

class TerminalLauncherSettingTab extends PluginSettingTab {
  plugin: TerminalLauncherPlugin;

  constructor(app: App, plugin: TerminalLauncherPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Terminal Launcher 設定' });

    new Setting(containerEl)
      .setName('config.json のパス')
      .setDesc('terminal-launcher の config.json への Windows 絶対パス')
      .addText((text) =>
        text
          .setPlaceholder('C:\\tools\\terminal-launcher\\config.json')
          .setValue(this.plugin.settings.configPath)
          .onChange(async (value) => {
            this.plugin.settings.configPath = value.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Send.ps1 のパス')
      .setDesc('terminal-launcher の Send.ps1 への Windows 絶対パス')
      .addText((text) =>
        text
          .setPlaceholder('C:\\tools\\terminal-launcher\\Send.ps1')
          .setValue(this.plugin.settings.scriptPath)
          .onChange(async (value) => {
            this.plugin.settings.scriptPath = value.trim();
            await this.plugin.saveSettings();
          })
      );
  }
}
