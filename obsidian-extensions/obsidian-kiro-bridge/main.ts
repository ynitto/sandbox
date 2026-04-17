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
import { ChildProcess, spawn } from 'child_process';
import { mkdirSync, writeFileSync } from 'fs';
import { basename, extname, join } from 'path';
import { tmpdir } from 'os';

// ---------------------------------------------------------------------------
// 型定義・設定
// ---------------------------------------------------------------------------

type KiroMode = 'wt-wsl' | 'wt-windows' | 'direct';

interface KiroBridgeSettings {
  mode: KiroMode;
  wslDistro: string;
  kiroPath: string;
  kiroFlags: string;
  /** プレースホルダー: {file} {filename} {title} */
  promptTemplate: string;
  workingDirectory: string;
}

const DEFAULT_SETTINGS: KiroBridgeSettings = {
  mode: 'wt-wsl',
  wslDistro: 'Ubuntu',
  kiroPath: 'kiro-cli',
  kiroFlags: '--trust-all-tools',
  promptTemplate: '以下のタスクを実行してください:\n\n{file}',
  workingDirectory: '',
};

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

function winToWslPath(winPath: string): string {
  return winPath
    .replace(/^([A-Za-z]):[\\\/]/, (_, d) => `/mnt/${d.toLowerCase()}/`)
    .replace(/\\/g, '/');
}

function expandPrompt(template: string, filePath: string): string {
  const name = basename(filePath);
  const title = basename(filePath, extname(filePath));
  return template
    .replace(/\{file\}/g, filePath)
    .replace(/\{filename\}/g, name)
    .replace(/\{title\}/g, title);
}

function ensureTmpDir(): string {
  const dir = join(tmpdir(), 'kiro-bridge');
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** bash シングルクォート内のエスケープ */
function shEsc(s: string): string {
  return s.replace(/'/g, "'\\''");
}

/** PowerShell シングルクォート内のエスケープ */
function psEsc(s: string): string {
  return s.replace(/'/g, "''");
}

// ---------------------------------------------------------------------------
// 出力モーダル (direct モード用)
// ---------------------------------------------------------------------------

class KiroOutputModal extends Modal {
  private outputEl!: HTMLPreElement;

  constructor(
    app: App,
    private readonly noteTitle: string,
    private readonly proc: ChildProcess,
  ) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.addClass('kiro-output-modal');

    const header = contentEl.createDiv('kiro-output-header');
    header.createEl('strong', { text: `Kiro: ${this.noteTitle}` });
    const statusEl = header.createEl('span', { text: ' 実行中…', cls: 'kiro-status-running' });

    this.outputEl = contentEl.createEl('pre', { cls: 'kiro-output-pre' });

    const append = (text: string) => {
      this.outputEl.textContent = (this.outputEl.textContent ?? '') + text;
      this.outputEl.scrollTop = this.outputEl.scrollHeight;
    };

    this.proc.stdout?.on('data', (d: Buffer) => append(d.toString()));
    this.proc.stderr?.on('data', (d: Buffer) => append(d.toString()));
    this.proc.on('exit', (code) => {
      const ok = code === 0;
      statusEl.textContent = ` 完了 (code: ${code ?? '?'})`;
      statusEl.className = ok ? 'kiro-status-ok' : 'kiro-status-err';
      append(`\n[終了: code ${code ?? '?'}]`);
    });
    this.proc.on('error', (err) => {
      statusEl.textContent = ' エラー';
      statusEl.className = 'kiro-status-err';
      append(`\n[エラー: ${err.message}]`);
    });
  }

  onClose(): void {
    if (this.proc.exitCode === null) this.proc.kill();
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// モード選択モーダル
// ---------------------------------------------------------------------------

class ModeSelectModal extends Modal {
  constructor(
    app: App,
    private readonly onChoose: (mode: KiroMode) => void,
  ) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: 'Kiro Bridge: 実行モードを選択' });

    const modes: [KiroMode, string][] = [
      ['wt-wsl',     '🐧  WSL  (Windows Terminal + Bash)'],
      ['wt-windows', '🪟  Windows  (Windows Terminal + PowerShell)'],
      ['direct',     '⚡  Direct  (直接実行 / Mac・Linux対応)'],
    ];

    modes.forEach(([mode, label]) => {
      const btn = contentEl.createEl('button', { cls: 'kiro-mode-btn' });
      btn.textContent = label;
      btn.addEventListener('click', () => { this.close(); this.onChoose(mode); });
    });
  }

  onClose(): void { this.contentEl.empty(); }
}

// ---------------------------------------------------------------------------
// プラグイン本体
// ---------------------------------------------------------------------------

export default class KiroBridgePlugin extends Plugin {
  settings: KiroBridgeSettings = DEFAULT_SETTINGS;

  async onload() {
    await this.loadSettings();

    this.addCommand({
      id: 'run-note',
      name: 'Run current note as task',
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking) this.runNote(file, this.settings.mode);
          return true;
        }
        return false;
      },
    });

    this.addCommand({
      id: 'run-note-select-mode',
      name: 'Run current note as task (select mode)',
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking)
            new ModeSelectModal(this.app, (mode) => this.runNote(file, mode)).open();
          return true;
        }
        return false;
      },
    });

    this.registerEvent(
      this.app.workspace.on('file-menu', (menu: Menu, abstractFile: TAbstractFile) => {
        if (!(abstractFile instanceof TFile)) return;
        const f = abstractFile;
        menu.addItem((item: MenuItem) =>
          item
            .setTitle('Kiro Bridge で実行')
            .setIcon('bot')
            .onClick(() => setTimeout(() => this.runNote(f, this.settings.mode), 50)),
        );
      }),
    );

    this.addSettingTab(new KiroBridgeSettingTab(this.app, this));
  }

  // ---------------------------------------------------------------------------
  // 実行ロジック
  // ---------------------------------------------------------------------------

  private runNote(file: TFile, mode: KiroMode): void {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) {
      new Notice('Kiro Bridge: FileSystemAdapter が利用できません');
      return;
    }

    const basePath = adapter.getBasePath();
    const sep = basePath.includes('\\') ? '\\' : '/';
    const nativeFilePath = basePath + sep + file.path.replace(/\//g, sep);
    const workDir = this.settings.workingDirectory.trim() || basePath;

    try {
      if (mode === 'wt-wsl')     this.runWtWsl(file, nativeFilePath, workDir);
      else if (mode === 'wt-windows') this.runWtWindows(file, nativeFilePath, workDir);
      else                            this.runDirect(file, nativeFilePath, workDir);
    } catch (err: unknown) {
      new Notice(`Kiro Bridge: エラー\n${err instanceof Error ? err.message : String(err)}`, 10000);
    }
  }

  /** Windows Terminal + WSL */
  private runWtWsl(file: TFile, winFilePath: string, winWorkDir: string): void {
    const wslFile    = winToWslPath(winFilePath);
    const wslWorkDir = winToWslPath(winWorkDir);
    const prompt     = expandPrompt(this.settings.promptTemplate, wslFile);
    const flags      = this.settings.kiroFlags.trim();

    const tmpDir     = ensureTmpDir();
    const scriptPath = join(tmpDir, `kiro-${Date.now()}.sh`);
    const wslScript  = winToWslPath(scriptPath);

    const bashScript = [
      '#!/usr/bin/env bash',
      'set -euo pipefail',
      `export PATH="$HOME/.local/bin:$HOME/.kiro/bin:/usr/local/bin:/usr/bin:$PATH"`,
      `cd '${shEsc(wslWorkDir)}'`,
      `'${shEsc(this.settings.kiroPath)}' ${flags} '${shEsc(prompt)}'`,
    ].join('\n') + '\n';

    writeFileSync(scriptPath, bashScript, { encoding: 'utf-8' });

    // Windows Terminal は Store アプリのため Start-Process 経由で起動
    const psCmd = `Start-Process wt -ArgumentList @('new-tab','--title','Kiro: ${psEsc(file.basename)}','wsl','-d','${psEsc(this.settings.wslDistro)}','--','bash','${psEsc(wslScript)}')`;
    spawn('powershell.exe', ['-NonInteractive', '-WindowStyle', 'Hidden', '-Command', psCmd], {
      detached: true,
      stdio: 'ignore',
    }).unref();

    new Notice(`Kiro Bridge: WSL (${this.settings.wslDistro}) で起動しました`);
  }

  /** Windows Terminal + PowerShell */
  private runWtWindows(file: TFile, winFilePath: string, winWorkDir: string): void {
    const prompt     = expandPrompt(this.settings.promptTemplate, winFilePath);
    const flags      = this.settings.kiroFlags.trim();
    const tmpDir     = ensureTmpDir();
    const scriptPath = join(tmpDir, `kiro-${Date.now()}.ps1`);

    const ps1 = [
      `Set-Location '${psEsc(winWorkDir)}'`,
      `& '${psEsc(this.settings.kiroPath)}' ${flags} '${psEsc(prompt)}'`,
    ].join('\n');

    writeFileSync(scriptPath, ps1, { encoding: 'utf-8' });

    const psCmd = `Start-Process wt -ArgumentList @('new-tab','--title','Kiro: ${psEsc(file.basename)}','powershell','-NoExit','-File','${psEsc(scriptPath)}')`;
    spawn('powershell.exe', ['-NonInteractive', '-WindowStyle', 'Hidden', '-Command', psCmd], {
      detached: true,
      stdio: 'ignore',
    }).unref();

    new Notice(`Kiro Bridge: Windows Terminal で起動しました`);
  }

  /** 直接実行 (Mac / Linux / Windows 共通) */
  private runDirect(file: TFile, nativeFilePath: string, workDir: string): void {
    const prompt = expandPrompt(this.settings.promptTemplate, nativeFilePath);
    const flags  = this.settings.kiroFlags.trim().split(/\s+/).filter(Boolean);

    const env = { ...process.env };
    if (process.platform !== 'win32') {
      const home = process.env.HOME ?? '';
      env.PATH = [
        home && `${home}/.local/bin`,
        home && `${home}/.kiro/bin`,
        '/opt/homebrew/bin',
        '/usr/local/bin',
        process.env.PATH,
      ].filter(Boolean).join(':');
    }

    const proc = spawn(this.settings.kiroPath, [...flags, prompt], {
      cwd: workDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      env,
    });

    new KiroOutputModal(this.app, file.name, proc).open();
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
  constructor(app: App, private readonly plugin: KiroBridgePlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Kiro Bridge 設定' });

    new Setting(containerEl)
      .setName('実行モード')
      .setDesc('kiro-cli をどの環境で起動するか')
      .addDropdown((dd) =>
        dd
          .addOption('wt-wsl',     '🐧 WSL (Windows Terminal + Bash)')
          .addOption('wt-windows', '🪟 Windows (Windows Terminal + PowerShell)')
          .addOption('direct',     '⚡ Direct (直接実行 / Mac・Linux対応)')
          .setValue(this.plugin.settings.mode)
          .onChange(async (v) => {
            this.plugin.settings.mode = v as KiroMode;
            await this.plugin.saveSettings();
            this.display();
          }),
      );

    if (this.plugin.settings.mode === 'wt-wsl') {
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
            }),
        );
    }

    containerEl.createEl('h3', { text: 'kiro-cli 設定' });

    new Setting(containerEl)
      .setName('kiro-cli のパス')
      .setDesc('コマンド名または絶対パス (wt-wsl の場合は WSL 内のパス)')
      .addText((t) =>
        t
          .setPlaceholder('kiro-cli')
          .setValue(this.plugin.settings.kiroPath)
          .onChange(async (v) => {
            this.plugin.settings.kiroPath = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName('追加フラグ')
      .setDesc('kiro-cli に常に渡すフラグ (スペース区切り)')
      .addText((t) =>
        t
          .setPlaceholder('--trust-all-tools')
          .setValue(this.plugin.settings.kiroFlags)
          .onChange(async (v) => {
            this.plugin.settings.kiroFlags = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName('作業ディレクトリ')
      .setDesc('kiro-cli を起動するディレクトリ (空の場合は Vault のルートを使用)')
      .addText((t) =>
        t
          .setPlaceholder('/path/to/project  または  C:\\projects\\myapp')
          .setValue(this.plugin.settings.workingDirectory)
          .onChange(async (v) => {
            this.plugin.settings.workingDirectory = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    containerEl.createEl('h3', { text: 'プロンプト設定' });

    const descFrag = document.createDocumentFragment();
    descFrag.append(
      'kiro-cli に渡すプロンプトのテンプレート。プレースホルダー: ',
      Object.assign(document.createElement('code'), { textContent: '{file}' }),
      ' (ファイルパス)、',
      Object.assign(document.createElement('code'), { textContent: '{filename}' }),
      ' (ファイル名)、',
      Object.assign(document.createElement('code'), { textContent: '{title}' }),
      ' (タイトル・拡張子なし)',
    );

    new Setting(containerEl)
      .setName('プロンプトテンプレート')
      .setDesc(descFrag)
      .addTextArea((ta) => {
        ta
          .setPlaceholder('以下のタスクを実行してください:\n\n{file}')
          .setValue(this.plugin.settings.promptTemplate)
          .onChange(async (v) => {
            this.plugin.settings.promptTemplate = v;
            await this.plugin.saveSettings();
          });
        ta.inputEl.rows = 5;
        ta.inputEl.style.width = '100%';
      });
  }
}
