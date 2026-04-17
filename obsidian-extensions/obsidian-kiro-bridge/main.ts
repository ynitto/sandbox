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
import { spawn } from 'child_process';

// ---------------------------------------------------------------------------
// 設定
// ---------------------------------------------------------------------------

type KiroEnvironment = 'windows' | 'wsl';

interface KiroBridgeSettings {
  /** 実行環境 */
  environment: KiroEnvironment;
  /** WSL ディストリビューション名 */
  wslDistro: string;
  /** KiroRun.ps1 への Windows 絶対パス */
  scriptPath: string;
  /** kiro コマンド (Windows 環境) */
  kiroCmdWindows: string;
  /** kiro コマンド (WSL 環境) */
  kiroCmdWsl: string;
  /** 作業ディレクトリ (空の場合は Vault ルートを使用) */
  workingDirectory: string;
  /** kiro-cli に追加で渡すフラグ */
  extraFlags: string;
}

const DEFAULT_SETTINGS: KiroBridgeSettings = {
  environment: 'wsl',
  wslDistro: 'Ubuntu',
  scriptPath: 'C:\\tools\\kiro-bridge\\KiroRun.ps1',
  kiroCmdWindows: 'kiro-cli',
  kiroCmdWsl: 'kiro-cli',
  workingDirectory: '',
  extraFlags: '--trust-all-tools',
};

// ---------------------------------------------------------------------------
// 環境選択モーダル (コマンドパレットから環境を一時的に切り替えたい場合)
// ---------------------------------------------------------------------------

class EnvSelectModal extends Modal {
  private onChoose: (env: KiroEnvironment) => void;

  constructor(app: App, onChoose: (env: KiroEnvironment) => void) {
    super(app);
    this.onChoose = onChoose;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: 'Kiro Bridge: 実行環境を選択' });

    const makeBtn = (label: string, env: KiroEnvironment) => {
      const btn = contentEl.createEl('button');
      btn.style.cssText = 'display:block;width:100%;margin-bottom:8px;padding:10px 14px;cursor:pointer;text-align:left;font-size:1em;';
      btn.textContent = label;
      btn.addEventListener('click', () => {
        this.close();
        this.onChoose(env);
      });
    };

    makeBtn('🪟  Windows  (PowerShell)', 'windows');
    makeBtn('🐧  WSL  (Linux / Bash)', 'wsl');
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// プラグイン本体
// ---------------------------------------------------------------------------

export default class KiroBridgePlugin extends Plugin {
  settings: KiroBridgeSettings = DEFAULT_SETTINGS;

  async onload() {
    await this.loadSettings();

    // ── コマンド登録 ──

    // デフォルト環境で実行
    this.addCommand({
      id: 'run-note',
      name: 'Run current note as task',
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking) this.runNote(file, this.settings.environment);
          return true;
        }
        return false;
      },
    });

    // 環境を選んで実行
    this.addCommand({
      id: 'run-note-select-env',
      name: 'Run current note as task (select environment)',
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking) {
            new EnvSelectModal(this.app, (env) => this.runNote(file, env)).open();
          }
          return true;
        }
        return false;
      },
    });

    // Windows 固定
    this.addCommand({
      id: 'run-note-windows',
      name: 'Run current note as task (Windows)',
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking) this.runNote(file, 'windows');
          return true;
        }
        return false;
      },
    });

    // WSL 固定
    this.addCommand({
      id: 'run-note-wsl',
      name: 'Run current note as task (WSL)',
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking) this.runNote(file, 'wsl');
          return true;
        }
        return false;
      },
    });

    // ── ファイルコンテキストメニュー ──
    this.registerEvent(
      this.app.workspace.on('file-menu', (menu: Menu, abstractFile: TAbstractFile) => {
        if (!(abstractFile instanceof TFile)) return;
        const file = abstractFile;
        menu.addItem((item: MenuItem) => {
          item
            .setTitle('Kiro Bridge で実行')
            .setIcon('bot')
            .onClick(() => setTimeout(() => this.runNote(file, this.settings.environment), 50));
        });
      })
    );

    this.addSettingTab(new KiroBridgeSettingTab(this.app, this));
  }

  // ---------------------------------------------------------------------------
  // 実行ロジック
  // ---------------------------------------------------------------------------

  private runNote(file: TFile, env: KiroEnvironment): void {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) {
      new Notice('Kiro Bridge: FileSystemAdapter が利用できません');
      return;
    }

    const basePath = adapter.getBasePath(); // Windows 絶対パス
    // Obsidian の file.path はスラッシュ区切りのため変換
    const winFilePath = `${basePath}\\${file.path.replace(/\//g, '\\')}`;
    const workDir = this.settings.workingDirectory.trim() || basePath;

    const kiroCmd = env === 'wsl' ? this.settings.kiroCmdWsl : this.settings.kiroCmdWindows;

    const psArgs = [
      '-NonInteractive',
      '-WindowStyle', 'Hidden',
      '-File', this.settings.scriptPath,
      '-FilePath', winFilePath,
      '-Environment', env,
      '-KiroCmd', kiroCmd,
      '-WslDistro', this.settings.wslDistro,
      '-WorkDir', workDir,
    ];

    if (this.settings.extraFlags.trim()) {
      psArgs.push('-ExtraFlags', this.settings.extraFlags.trim());
    }

    const proc = spawn('powershell.exe', psArgs, {
      detached: true,
      stdio: 'ignore',
    });
    proc.unref();

    const envLabel = env === 'wsl' ? `WSL (${this.settings.wslDistro})` : 'Windows';
    new Notice(`Kiro Bridge: "${file.name}" を ${envLabel} で実行中…`);
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }
}

// ---------------------------------------------------------------------------
// 設定タブ
// ---------------------------------------------------------------------------

class KiroBridgeSettingTab extends PluginSettingTab {
  plugin: KiroBridgePlugin;

  constructor(app: App, plugin: KiroBridgePlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Kiro Bridge 設定' });

    new Setting(containerEl)
      .setName('デフォルト実行環境')
      .setDesc('コマンド "Run current note as task" で使用する環境')
      .addDropdown((dd) =>
        dd
          .addOption('wsl', '🐧 WSL (Linux)')
          .addOption('windows', '🪟 Windows (PowerShell)')
          .setValue(this.plugin.settings.environment)
          .onChange(async (v) => {
            this.plugin.settings.environment = v as KiroEnvironment;
            await this.plugin.saveSettings();
            this.display();
          })
      );

    if (this.plugin.settings.environment === 'wsl') {
      new Setting(containerEl)
        .setName('WSL ディストリビューション')
        .setDesc('使用する WSL ディストリビューション名 (例: Ubuntu, Debian)')
        .addText((t) =>
          t
            .setPlaceholder('Ubuntu')
            .setValue(this.plugin.settings.wslDistro)
            .onChange(async (v) => {
              this.plugin.settings.wslDistro = v.trim();
              await this.plugin.saveSettings();
            })
        );
    }

    containerEl.createEl('h3', { text: 'スクリプト設定' });

    new Setting(containerEl)
      .setName('KiroRun.ps1 のパス')
      .setDesc('KiroRun.ps1 への Windows 絶対パス')
      .addText((t) =>
        t
          .setPlaceholder('C:\\tools\\kiro-bridge\\KiroRun.ps1')
          .setValue(this.plugin.settings.scriptPath)
          .onChange(async (v) => {
            this.plugin.settings.scriptPath = v.trim();
            await this.plugin.saveSettings();
          })
      );

    containerEl.createEl('h3', { text: 'kiro-cli 設定' });

    new Setting(containerEl)
      .setName('kiro コマンド (Windows)')
      .setDesc('Windows 環境で実行する kiro-cli コマンドまたは絶対パス')
      .addText((t) =>
        t
          .setPlaceholder('kiro-cli')
          .setValue(this.plugin.settings.kiroCmdWindows)
          .onChange(async (v) => {
            this.plugin.settings.kiroCmdWindows = v.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('kiro コマンド (WSL)')
      .setDesc('WSL 環境で実行する kiro-cli コマンドまたは絶対パス')
      .addText((t) =>
        t
          .setPlaceholder('kiro-cli')
          .setValue(this.plugin.settings.kiroCmdWsl)
          .onChange(async (v) => {
            this.plugin.settings.kiroCmdWsl = v.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('追加フラグ')
      .setDesc('kiro-cli に追加で渡すフラグ (例: --trust-all-tools)')
      .addText((t) =>
        t
          .setPlaceholder('--trust-all-tools')
          .setValue(this.plugin.settings.extraFlags)
          .onChange(async (v) => {
            this.plugin.settings.extraFlags = v.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('作業ディレクトリ')
      .setDesc('kiro-cli を起動するディレクトリ (空の場合は Vault のルートを使用)')
      .addText((t) =>
        t
          .setPlaceholder('C:\\Users\\...\\my-project')
          .setValue(this.plugin.settings.workingDirectory)
          .onChange(async (v) => {
            this.plugin.settings.workingDirectory = v.trim();
            await this.plugin.saveSettings();
          })
      );
  }
}
