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
import { execSync } from 'child_process';

// ---------------------------------------------------------------------------
// 設定
// ---------------------------------------------------------------------------

/** ワークスペース（作業ディレクトリ）エントリ */
interface WorkspaceEntry {
  /** 表示名 (例: "project-a") */
  name: string;
  /** WSL パス (例: /home/user/projects/foo) — 送信時のコンテキストとして使用 */
  wslPath: string;
  /** このワークスペース専用の tmux ターゲット（空欄 = グローバル設定を使用） */
  tmuxTarget: string;
  /** このワークスペース専用の前提プロンプト（空欄 = グローバルのデフォルトを使用） */
  premisePrompt: string;
}

interface KiroSenderSettings {
  /** デフォルト tmux ターゲット (例: "kiro:0", "multiagent:1.0") */
  tmuxTarget: string;
  /** WSL ディストリビューション名 (空欄 = デフォルト) */
  wslDistribution: string;
  /** ファイル参照プレフィックス (例: "@") */
  filePrefix: string;
  /** デフォルト前提プロンプト（ワークスペース側で上書き可能） */
  defaultPremisePrompt: string;
  /** ワークスペース一覧 */
  workspaces: WorkspaceEntry[];
}

const DEFAULT_SETTINGS: KiroSenderSettings = {
  tmuxTarget: 'kiro:0',
  wslDistribution: '',
  filePrefix: '@',
  defaultPremisePrompt: '',
  workspaces: [],
};

// ---------------------------------------------------------------------------
// ワークスペース選択モーダル
// ---------------------------------------------------------------------------

class WorkspaceSelectModal extends Modal {
  private workspaces: WorkspaceEntry[];
  private fileName: string;
  // null = グローバル設定で送信
  private onChoose: (entry: WorkspaceEntry | null) => void;
  private onSaveWorkspace: (entry: WorkspaceEntry) => Promise<void>;

  constructor(
    app: App,
    workspaces: WorkspaceEntry[],
    fileName: string,
    onChoose: (entry: WorkspaceEntry | null) => void,
    onSaveWorkspace: (entry: WorkspaceEntry) => Promise<void>,
  ) {
    super(app);
    this.workspaces = workspaces;
    this.fileName = fileName;
    this.onChoose = onChoose;
    this.onSaveWorkspace = onSaveWorkspace;
  }

  onOpen(): void {
    // Obsidian の Modal が .modal-content を生成するまで待つ
    // contentEl は super.onOpen() 後に確実に存在する
    const { contentEl } = this;
    if (!contentEl) {
      // フォールバック: ブラウザネイティブのダイアログ
      this.fallbackDialog();
      return;
    }
    this.render();
  }

  private fallbackDialog(): void {
    const labels = this.workspaces.map((e, i) => `${i + 1}. ${e.name || e.wslPath}`).join('\n');
    const prompt = this.workspaces.length > 0
      ? `送信先を番号で選択（0=デフォルト）:\n0. デフォルト設定\n${labels}`
      : '0 を入力してデフォルト設定で送信:';
    const input = window.prompt(`Kiro に送信: ${this.fileName}\n\n${prompt}`, '0');
    if (input === null) return; // キャンセル
    const idx = parseInt(input, 10);
    if (idx === 0) {
      this.onChoose(null);
    } else if (idx >= 1 && idx <= this.workspaces.length) {
      this.onChoose(this.workspaces[idx - 1]);
    }
  }

  private render(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: `Kiro に送信: ${this.fileName}` });

    const addBtn = (label: string, onClick: () => void, isCta = false) => {
      const btn = contentEl.createEl('button', { text: label });
      btn.style.display = 'block';
      btn.style.width = '100%';
      btn.style.marginBottom = '6px';
      btn.style.textAlign = 'left';
      btn.style.padding = '8px 12px';
      btn.style.cursor = 'pointer';
      if (isCta) btn.addClass('mod-cta');
      btn.addEventListener('click', () => { this.close(); onClick(); });
      return btn;
    };

    this.workspaces.forEach((entry) => {
      const label = entry.wslPath ? `${entry.name}  (${entry.wslPath})` : entry.name;
      addBtn(label, () => this.onChoose(entry), true);
    });

    contentEl.createEl('hr');

    // デフォルト設定で送信
    addBtn('デフォルト設定で送信', () => this.onChoose(null));

    // ── 新しいワークスペースを追加 ──────────────────────────
    contentEl.createEl('hr');
    contentEl.createEl('p', {
      text: '新しいワークスペースを追加',
      attr: { style: 'font-weight: bold; margin: 8px 0 4px;' },
    });

    const grid = contentEl.createDiv({ attr: { style: 'display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-bottom:6px;' } });

    const nameInput = grid.createEl('input', { attr: { placeholder: '名前 (例: project-a)', style: 'width:100%; padding:4px 8px;' } });
    const pathInput = grid.createEl('input', { attr: { placeholder: 'WSL パス (/home/...)', style: 'width:100%; padding:4px 8px;' } });
    const tmuxInput = grid.createEl('input', { attr: { placeholder: 'tmux ターゲット (空欄=グローバル)', style: 'width:100%; padding:4px 8px;' } });
    const premiseInput = grid.createEl('input', { attr: { placeholder: '前提プロンプト (空欄=グローバル)', style: 'width:100%; padding:4px 8px;' } });

    const addNewBtn = contentEl.createEl('button', {
      text: '+ 追加して送信',
      attr: { style: 'width:100%; padding:8px 12px; cursor:pointer;' },
    });
    addNewBtn.addClass('mod-cta');
    addNewBtn.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      const wslPath = pathInput.value.trim();
      if (!name) {
        nameInput.style.borderColor = 'red';
        nameInput.focus();
        return;
      }
      const entry: WorkspaceEntry = {
        name,
        wslPath,
        tmuxTarget: tmuxInput.value.trim(),
        premisePrompt: premiseInput.value,
      };
      await this.onSaveWorkspace(entry);
      this.close();
      this.onChoose(entry);
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// プラグイン本体
// ---------------------------------------------------------------------------

export default class KiroSenderPlugin extends Plugin {
  settings: KiroSenderSettings = DEFAULT_SETTINGS;

  async onload() {
    await this.loadSettings();

    // コマンドパレット: アクティブノートを送信
    this.addCommand({
      id: 'send-to-kiro',
      name: 'Send current note to Kiro',
      checkCallback: (checking: boolean) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof TFile) {
          if (!checking) {
            this.triggerSend(file);
          }
          return true;
        }
        return false;
      },
    });

    // 右クリックメニュー: ファイル・エクスプローラーのコンテキストメニュー
    this.registerEvent(
      this.app.workspace.on(
        'file-menu',
        (menu: Menu, abstractFile: TAbstractFile) => {
          if (!(abstractFile instanceof TFile)) return;
          const file = abstractFile;
          menu.addItem((item: MenuItem) => {
            item
              .setTitle('Kiro に送信')
              .setIcon('send')
              .onClick(() => setTimeout(() => this.triggerSend(file), 50));
          });
        }
      )
    );

    // 設定タブ
    this.addSettingTab(new KiroSenderSettingTab(this.app, this));
  }

  // ---------------------------------------------------------------------------
  // 送信フロー
  // ---------------------------------------------------------------------------

  /** 常にモーダルを表示してから送信 */
  private triggerSend(file: TFile): void {
    const { workspaces } = this.settings;
    new WorkspaceSelectModal(
      this.app,
      workspaces,
      file.name,
      (entry) => { this.sendToKiro(file, entry); },
      async (entry) => {
        this.settings.workspaces.push(entry);
        await this.saveSettings();
      },
    ).open();
  }

  /** 実際の tmux 送信処理 */
  async sendToKiro(file: TFile, workspace: WorkspaceEntry | null): Promise<void> {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) {
      new Notice('Kiro Sender: FileSystemAdapter が利用できません');
      return;
    }

    const isWindows = process.platform === 'win32';
    const basePath = adapter.getBasePath();

    // ファイルの絶対パスを tmux に渡す形式で取得
    let filePath: string;
    if (isWindows) {
      // Windows: C:\... → /mnt/c/... に変換して WSL 内パスとして渡す
      const winAbsolute = `${basePath}\\${file.path.replace(/\//g, '\\')}`;
      filePath = toWslPath(winAbsolute);
    } else {
      // macOS / Linux: Vault のベースパスはそのまま使える
      filePath = `${basePath}/${file.path}`;
    }

    // 有効なターゲット・前提プロンプトを決定
    const tmuxTarget = workspace?.tmuxTarget?.trim() || this.settings.tmuxTarget;
    const premisePrompt = (workspace?.premisePrompt ?? this.settings.defaultPremisePrompt);
    const workspacePath = workspace?.wslPath?.trim() ?? '';

    // ペイロード: [前提プロンプト ][cd {path} && ]@{filePath}
    const parts: string[] = [];
    if (premisePrompt.trim()) parts.push(premisePrompt.trim());
    if (workspacePath) parts.push(`cd ${workspacePath} &&`);
    parts.push(`${this.settings.filePrefix}${filePath}`);
    const payload = parts.join(' ');

    let cmd: string;
    if (isWindows) {
      // Windows: wsl 経由で tmux を叩く
      const wslBin = resolveWslBin();
      if (!wslBin) {
        new Notice('Kiro Sender: wsl コマンドが見つかりません。WSL2 をインストールしてください。', 8000);
        return;
      }
      const wslPrefix = this.settings.wslDistribution
        ? `${wslBin} -d ${this.settings.wslDistribution}`
        : wslBin;
      cmd = `${wslPrefix} tmux send-keys -t "${tmuxTarget}" ${shellQuote(payload)} Enter`;
    } else {
      // macOS / Linux: tmux に直接送る
      cmd = `tmux send-keys -t "${tmuxTarget}" ${shellQuote(payload)} Enter`;
    }

    try {
      execSync(cmd, { timeout: 5000 });
      const label = workspace ? `[${workspace.name}] ` : '';
      new Notice(`Kiro に送信: ${label}${file.name}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`Kiro Sender エラー: ${msg}`, 6000);
      console.error('[KiroSender] execSync error:', err);
    }
  }

  async loadSettings() {
    const loaded = await this.loadData();
    this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded);
    // workspaces が旧データで undefined の場合の後方互換
    if (!Array.isArray(this.settings.workspaces)) {
      this.settings.workspaces = [];
    }
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }
}

// ---------------------------------------------------------------------------
// 設定タブ
// ---------------------------------------------------------------------------

class KiroSenderSettingTab extends PluginSettingTab {
  plugin: KiroSenderPlugin;

  constructor(app: App, plugin: KiroSenderPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Kiro Sender 設定' });

    // ── グローバル設定 ─────────────────────────────────────────────────────

    containerEl.createEl('h3', { text: 'グローバル設定' });

    new Setting(containerEl)
      .setName('デフォルト tmux ターゲット')
      .setDesc('ワークスペース側で未指定のときに使う tmux ターゲット。例: kiro:0 / multiagent:1.0')
      .addText((text) =>
        text
          .setPlaceholder('kiro:0')
          .setValue(this.plugin.settings.tmuxTarget)
          .onChange(async (value) => {
            this.plugin.settings.tmuxTarget = value.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('WSL ディストリビューション')
      .setDesc('wsl コマンドに渡すディストリビューション名（空欄 = デフォルト）。例: Ubuntu-22.04')
      .addText((text) =>
        text
          .setPlaceholder('Ubuntu')
          .setValue(this.plugin.settings.wslDistribution)
          .onChange(async (value) => {
            this.plugin.settings.wslDistribution = value.trim();
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('ファイル参照プレフィックス')
      .setDesc('@ファイル参照の直前に付ける文字。通常は "@" のまま。')
      .addText((text) =>
        text
          .setPlaceholder('@')
          .setValue(this.plugin.settings.filePrefix)
          .onChange(async (value) => {
            this.plugin.settings.filePrefix = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('デフォルト前提プロンプト')
      .setDesc(
        'ワークスペース側で未指定のとき @ファイル参照の前に付けるテキスト。' +
          '例: "以下の指示を実行してください"'
      )
      .addText((text) =>
        text
          .setPlaceholder('以下の指示を実行してください')
          .setValue(this.plugin.settings.defaultPremisePrompt)
          .onChange(async (value) => {
            this.plugin.settings.defaultPremisePrompt = value;
            await this.plugin.saveSettings();
          })
      );

    // ── ワークスペース一覧 ────────────────────────────────────────────────

    containerEl.createEl('h3', { text: 'ワークスペース（作業ディレクトリ）' });
    containerEl.createEl('p', {
      text:
        '複数のワークスペースを登録すると、送信時にモーダルで選択できます。' +
        '1件のみの場合は自動選択されます。',
      cls: 'setting-item-description',
    });

    const workspaces = this.plugin.settings.workspaces;

    workspaces.forEach((entry, index) => {
      const entryEl = containerEl.createDiv({ cls: 'kiro-workspace-entry' });
      entryEl.style.border = '1px solid var(--background-modifier-border)';
      entryEl.style.borderRadius = '6px';
      entryEl.style.padding = '12px';
      entryEl.style.marginBottom = '12px';

      // ヘッダー行（番号 + 削除ボタン）
      const headerEl = entryEl.createDiv();
      headerEl.style.display = 'flex';
      headerEl.style.justifyContent = 'space-between';
      headerEl.style.alignItems = 'center';
      headerEl.style.marginBottom = '8px';
      headerEl.createEl('strong', { text: `ワークスペース ${index + 1}` });
      const deleteBtn = headerEl.createEl('button', { text: '削除' });
      deleteBtn.addEventListener('click', async () => {
        this.plugin.settings.workspaces.splice(index, 1);
        await this.plugin.saveSettings();
        this.display();
      });

      new Setting(entryEl)
        .setName('名前')
        .setDesc('選択モーダルに表示される短い識別名')
        .addText((text) =>
          text
            .setPlaceholder('project-a')
            .setValue(entry.name)
            .onChange(async (value) => {
              entry.name = value;
              await this.plugin.saveSettings();
            })
        );

      new Setting(entryEl)
        .setName('WSL パス')
        .setDesc('作業ディレクトリの WSL パス。例: /home/user/projects/foo')
        .addText((text) =>
          text
            .setPlaceholder('/home/user/projects/foo')
            .setValue(entry.wslPath)
            .onChange(async (value) => {
              entry.wslPath = value.trim();
              await this.plugin.saveSettings();
            })
        );

      new Setting(entryEl)
        .setName('tmux ターゲット')
        .setDesc('このワークスペース専用のターゲット（空欄 = グローバル設定）。例: kiro:0.1')
        .addText((text) =>
          text
            .setPlaceholder('（グローバル設定を使用）')
            .setValue(entry.tmuxTarget)
            .onChange(async (value) => {
              entry.tmuxTarget = value.trim();
              await this.plugin.saveSettings();
            })
        );

      new Setting(entryEl)
        .setName('前提プロンプト')
        .setDesc('このワークスペース専用の前提プロンプト（空欄 = グローバルのデフォルトを使用）')
        .addText((text) =>
          text
            .setPlaceholder('（グローバルのデフォルトを使用）')
            .setValue(entry.premisePrompt)
            .onChange(async (value) => {
              entry.premisePrompt = value;
              await this.plugin.saveSettings();
            })
        );
    });

    new Setting(containerEl)
      .addButton((btn) =>
        btn
          .setButtonText('+ ワークスペースを追加')
          .setCta()
          .onClick(async () => {
            this.plugin.settings.workspaces.push({
              name: '',
              wslPath: '',
              tmuxTarget: '',
              premisePrompt: '',
            });
            await this.plugin.saveSettings();
            this.display();
          })
      );
  }
}

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

/**
/**
 * wsl.exe のフルパスを返す。見つからなければ null。
 * Windows では C:\Windows\System32\wsl.exe が標準的な場所。
 * PATH に wsl があればそれを使い、なければ固定パスで試みる。
 */
function resolveWslBin(): string | null {
  // 1. PATH 上に wsl があれば採用
  try {
    execSync('wsl --version', { windowsHide: true, timeout: 3000, stdio: 'ignore' });
    return 'wsl';
  } catch {
    // fall through
  }
  // 2. Windows 標準インストールパス
  const fixed = 'C:\\Windows\\System32\\wsl.exe';
  try {
    execSync(`"${fixed}" --version`, { windowsHide: true, timeout: 3000, stdio: 'ignore' });
    return `"${fixed}"`;
  } catch {
    return null;
  }
}

/**
 * Windows パスを WSL パスに変換する。
 * 例: C:\Users\foo\bar.md → /mnt/c/Users/foo/bar.md
 */
function toWslPath(windowsPath: string): string {
  return windowsPath
    .replace(/^([A-Za-z]):[\\\/]/, (_match, drive: string) => `/mnt/${drive.toLowerCase()}/`)
    .replace(/\\/g, '/');
}

/**
 * 文字列を tmux send-keys に渡せるよう引用符で囲む。
 * シングルクォート内のシングルクォートをエスケープする。
 */
function shellQuote(str: string): string {
  return `'${str.replace(/'/g, "'\\''")}'`;
}
