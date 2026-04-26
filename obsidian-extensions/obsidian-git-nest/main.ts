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
  TFolder,
} from 'obsidian';
import { spawn } from 'child_process';
import { writeFileSync, existsSync, readFileSync } from 'fs';
import { join } from 'path';

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface RepoEntry {
  /** vault root からの相対パス (子リポジトリのディレクトリ) */
  prefix: string;
  /** リモートリポジトリの URL */
  remote: string;
  /** git remote の短縮名 (例: "origin") */
  remoteName: string;
  /** 同期対象のブランチ名 */
  branch: string;
  /** 子リポジトリ内で使用するサブフォルダ (省略時はルート全体) */
  subdir?: string;
}

interface GitNestSettings {
  repos: RepoEntry[];
}

const DEFAULT_SETTINGS: GitNestSettings = {
  repos: [],
};

// ---------------------------------------------------------------------------
// Git ヘルパー (shell を介さず spawn で安全に実行)
// ---------------------------------------------------------------------------

function runGit(cwd: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn('git', args, { cwd });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on('data', (d: Buffer) => { stderr += d.toString(); });
    proc.on('close', (code: number) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error(stderr.trim() || stdout.trim() || `git exited with code ${code}`));
      }
    });
    proc.on('error', reject);
  });
}

// vault の .gitignore に prefix を追記する (重複は追加しない)
function addToGitIgnore(vaultPath: string, prefix: string): void {
  const gitignorePath = join(vaultPath, '.gitignore');
  let content = existsSync(gitignorePath) ? readFileSync(gitignorePath, 'utf-8') : '';
  const entry = prefix.endsWith('/') ? prefix : `${prefix}/`;
  const lines = content.split('\n').map((l) => l.trim());
  if (lines.includes(entry) || lines.includes(prefix)) return;
  if (content && !content.endsWith('\n')) content += '\n';
  content += `${entry}\n`;
  writeFileSync(gitignorePath, content, 'utf-8');
}

// ---------------------------------------------------------------------------
// モーダル: リポジトリ追加
// ---------------------------------------------------------------------------

class AddRepoModal extends Modal {
  private plugin: GitNestPlugin;
  private initialPrefix: string;

  constructor(app: App, plugin: GitNestPlugin, initialPrefix = '') {
    super(app);
    this.plugin = plugin;
    this.initialPrefix = initialPrefix;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: 'リポジトリを追加' });

    let prefix = this.initialPrefix;
    let remote = '';
    let remoteName = 'origin';
    let branch = 'main';
    let subdir = '';

    new Setting(contentEl)
      .setName('フォルダパス')
      .setDesc('vault root からの相対パス。リポジトリがクローンされるフォルダ')
      .addText((t) =>
        t.setValue(prefix).onChange((v) => { prefix = v.trim(); })
      );

    new Setting(contentEl)
      .setName('リモート URL')
      .setDesc('クローンする git リポジトリの URL')
      .addText((t) =>
        t.setPlaceholder('https://github.com/user/repo.git').onChange((v) => { remote = v.trim(); })
      );

    new Setting(contentEl)
      .setName('リモート名')
      .setDesc('git remote の短縮名 (デフォルト: origin)')
      .addText((t) =>
        t.setValue(remoteName).onChange((v) => { remoteName = v.trim(); })
      );

    new Setting(contentEl)
      .setName('ブランチ')
      .setDesc('同期するリモートブランチ名')
      .addText((t) =>
        t.setValue(branch).onChange((v) => { branch = v.trim(); })
      );

    new Setting(contentEl)
      .setName('サブフォルダ (省略可)')
      .setDesc('リポジトリ内で使用するフォルダパス。省略時はリポジトリ全体を使用')
      .addText((t) =>
        t.setPlaceholder('docs').onChange((v) => { subdir = v.trim(); })
      );

    new Setting(contentEl)
      .addButton((btn) =>
        btn
          .setButtonText('追加')
          .setCta()
          .onClick(async () => {
            if (!prefix || !remote || !remoteName || !branch) {
              new Notice('すべての項目を入力してください');
              return;
            }
            if (/\s/.test(remoteName)) {
              new Notice('リモート名にスペースは使用できません');
              return;
            }
            this.close();
            await this.plugin.addRepo({ prefix, remote, remoteName, branch, subdir: subdir || undefined });
          })
      );
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// モーダル: リポジトリ選択
// ---------------------------------------------------------------------------

class SelectRepoModal extends Modal {
  private repos: RepoEntry[];
  private title: string;
  private onSelect: (repo: RepoEntry) => void;

  constructor(
    app: App,
    repos: RepoEntry[],
    title: string,
    onSelect: (r: RepoEntry) => void,
  ) {
    super(app);
    this.repos = repos;
    this.title = title;
    this.onSelect = onSelect;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: this.title });

    if (this.repos.length === 0) {
      contentEl.createEl('p', {
        text: '登録済みのリポジトリがありません。先に追加してください。',
      });
      return;
    }

    this.repos.forEach((repo) => {
      const btn = contentEl.createEl('button');
      btn.style.cssText =
        'display:block;width:100%;margin-bottom:6px;text-align:left;padding:8px 12px;cursor:pointer;border-radius:4px;';

      const titleSpan = btn.createEl('span', { text: repo.prefix });
      titleSpan.style.fontWeight = 'bold';

      btn.createEl('span', {
        text: `  ${repo.remoteName}/${repo.branch}`,
        attr: { style: 'opacity:0.6;font-size:0.85em;margin-left:8px;' },
      });

      btn.addEventListener('click', () => {
        this.close();
        this.onSelect(repo);
      });
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// モーダル: ブランチ切り替え (設定タブ・コマンドパレット用)
// ---------------------------------------------------------------------------

class SwitchBranchModal extends Modal {
  private plugin: GitNestPlugin;
  private repo: RepoEntry;

  constructor(app: App, plugin: GitNestPlugin, repo: RepoEntry) {
    super(app);
    this.plugin = plugin;
    this.repo = repo;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: `ブランチを切り替え: ${this.repo.prefix}` });

    contentEl.createEl('p', {
      text: `現在のブランチ: ${this.repo.branch}`,
      attr: { style: 'opacity:0.7;margin-bottom:12px;' },
    });

    contentEl.createEl('p', {
      text: '以降の pull/push で同期するリモートブランチを変更します。',
      attr: { style: 'opacity:0.7;font-size:0.9em;margin-bottom:12px;' },
    });

    let branchName = '';

    new Setting(contentEl)
      .setName('ブランチ名')
      .addText((t) =>
        t
          .setPlaceholder('develop')
          .onChange((v) => { branchName = v.trim(); })
      );

    new Setting(contentEl)
      .addButton((btn) =>
        btn
          .setButtonText('切り替え')
          .setCta()
          .onClick(async () => {
            if (!branchName) {
              new Notice('ブランチ名を入力してください');
              return;
            }
            this.close();
            await this.plugin.switchBranch(this.repo, branchName);
          })
      );
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// モーダル: Git 操作 (フォルダ右クリックメニューから表示)
// ---------------------------------------------------------------------------

class GitRepoModal extends Modal {
  private plugin: GitNestPlugin;
  private repo: RepoEntry;

  constructor(app: App, plugin: GitNestPlugin, repo: RepoEntry) {
    super(app);
    this.plugin = plugin;
    this.repo = repo;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: `Git 操作: ${this.repo.prefix}` });

    contentEl.createEl('p', {
      text: `ブランチ: ${this.repo.branch}  |  リモート: ${this.repo.remoteName}`,
      attr: { style: 'opacity:0.6;font-size:0.85em;margin-bottom:16px;' },
    });

    // ---- 同期 ----

    contentEl.createEl('h4', { text: '同期' });

    new Setting(contentEl)
      .setName('Pull')
      .setDesc(`${this.repo.remoteName}/${this.repo.branch} から最新を取得`)
      .addButton((btn) =>
        btn.setButtonText('Pull').setIcon('download').onClick(async () => {
          this.close();
          await this.plugin.pullRepo(this.repo);
        })
      );

    new Setting(contentEl)
      .setName('Push')
      .setDesc(`${this.repo.remoteName}/${this.repo.branch} へ変更を送信`)
      .addButton((btn) =>
        btn.setButtonText('Push').setIcon('upload').onClick(async () => {
          this.close();
          await this.plugin.pushRepo(this.repo);
        })
      );

    new Setting(contentEl)
      .setName('スタッシュして Pull')
      .setDesc('現在の変更をスタッシュし、最新を pull してスタッシュを戻します')
      .addButton((btn) =>
        btn.setButtonText('Stash & Pull').onClick(async () => {
          this.close();
          await this.plugin.pullWithStash(this.repo);
        })
      );

    // ---- ブランチ操作 ----

    contentEl.createEl('h4', { text: 'ブランチ操作', attr: { style: 'margin-top:16px;' } });

    let branchName = '';
    const branchSetting = new Setting(contentEl)
      .setName('ブランチ名')
      .addText((t) =>
        t.setPlaceholder('branch-name').onChange((v) => { branchName = v.trim(); })
      );
    branchSetting.settingEl.style.marginBottom = '0';

    new Setting(contentEl)
      .setName('既存ブランチへ切り替え')
      .setDesc('git checkout でブランチを切り替えます')
      .addButton((btn) =>
        btn.setButtonText('チェックアウト').onClick(async () => {
          if (!branchName) {
            new Notice('ブランチ名を入力してください');
            return;
          }
          this.close();
          await this.plugin.checkoutBranch(this.repo, branchName);
        })
      );

    new Setting(contentEl)
      .setName('新規ブランチを作成して Push')
      .setDesc('新しいブランチを作成してリモートに push します')
      .addButton((btn) =>
        btn.setButtonText('作成 & Push').setCta().onClick(async () => {
          if (!branchName) {
            new Notice('ブランチ名を入力してください');
            return;
          }
          this.close();
          await this.plugin.createAndPushBranch(this.repo, branchName);
        })
      );
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// プラグイン本体
// ---------------------------------------------------------------------------

export default class GitNestPlugin extends Plugin {
  settings: GitNestSettings = DEFAULT_SETTINGS;

  async onload() {
    await this.loadSettings();

    // ---- コマンド登録 ----

    this.addCommand({
      id: 'add-repo',
      name: 'リポジトリを追加',
      callback: () => new AddRepoModal(this.app, this).open(),
    });

    this.addCommand({
      id: 'pull-repo',
      name: 'リポジトリを pull (選択)',
      callback: () =>
        new SelectRepoModal(
          this.app,
          this.settings.repos,
          'Pull するリポジトリを選択',
          (repo) => this.pullRepo(repo),
        ).open(),
    });

    this.addCommand({
      id: 'pull-all-repos',
      name: 'すべてのリポジトリを pull',
      callback: () => this.pullAllRepos(),
    });

    this.addCommand({
      id: 'push-repo',
      name: 'リポジトリを push (選択)',
      callback: () =>
        new SelectRepoModal(
          this.app,
          this.settings.repos,
          'Push するリポジトリを選択',
          (repo) => this.pushRepo(repo),
        ).open(),
    });

    this.addCommand({
      id: 'push-all-repos',
      name: 'すべてのリポジトリを push',
      callback: () => this.pushAllRepos(),
    });

    this.addCommand({
      id: 'switch-repo-branch',
      name: 'リポジトリのブランチを切り替え',
      callback: () =>
        new SelectRepoModal(
          this.app,
          this.settings.repos,
          'ブランチを切り替えるリポジトリを選択',
          (repo) => new SwitchBranchModal(this.app, this, repo).open(),
        ).open(),
    });

    // ---- フォルダ右クリックメニュー ----

    this.registerEvent(
      this.app.workspace.on(
        'file-menu',
        (menu: Menu, abstractFile: TAbstractFile) => {
          if (!(abstractFile instanceof TFolder)) return;
          const folderPath = abstractFile.path;
          const existing = this.settings.repos.find(
            (repo) =>
              repo.prefix === folderPath ||
              (repo.subdir && `${repo.prefix}/${repo.subdir}` === folderPath),
          );

          if (existing) {
            menu.addItem((item: MenuItem) =>
              item
                .setTitle('Git: 管理')
                .setIcon('git-branch')
                .onClick(() => new GitRepoModal(this.app, this, existing).open())
            );
          } else {
            menu.addItem((item: MenuItem) =>
              item
                .setTitle('Git Nest: リポジトリとして追加')
                .setIcon('git-pull-request')
                .onClick(() => new AddRepoModal(this.app, this, folderPath).open())
            );
          }
        },
      )
    );

    this.addSettingTab(new GitNestSettingTab(this.app, this));
  }

  // ---------------------------------------------------------------------------
  // vault パス取得
  // ---------------------------------------------------------------------------

  getVaultPath(): string {
    const adapter = this.app.vault.adapter;
    if (adapter instanceof FileSystemAdapter) {
      return adapter.getBasePath();
    }
    throw new Error('FileSystemAdapter が利用できません');
  }

  // ---------------------------------------------------------------------------
  // リポジトリ操作
  // ---------------------------------------------------------------------------

  async addRepo(entry: RepoEntry): Promise<void> {
    const vaultPath = this.getVaultPath();
    const destPath = join(vaultPath, entry.prefix);

    new Notice(`"${entry.prefix}" をクローンしています...`);
    try {
      if (existsSync(join(destPath, '.git'))) {
        new Notice(`"${entry.prefix}" にはすでに git リポジトリが存在します`);
      } else {
        await runGit(vaultPath, ['clone', '--origin', entry.remoteName, entry.remote, entry.prefix]);
      }

      addToGitIgnore(vaultPath, entry.prefix);
      this.settings.repos.push(entry);
      await this.saveSettings();
      new Notice(`"${entry.prefix}" を追加しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`リポジトリの追加に失敗しました:\n${msg}`, 8000);
    }
  }

  async pullRepo(entry: RepoEntry): Promise<void> {
    const destPath = join(this.getVaultPath(), entry.prefix);
    new Notice(`"${entry.prefix}" を pull しています...`);
    try {
      await runGit(destPath, ['pull', entry.remoteName, entry.branch]);
      new Notice(`"${entry.prefix}" の pull が完了しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`pull に失敗しました:\n${msg}`, 8000);
    }
  }

  async pullAllRepos(): Promise<void> {
    if (this.settings.repos.length === 0) {
      new Notice('登録済みのリポジトリがありません');
      return;
    }
    for (const repo of this.settings.repos) {
      await this.pullRepo(repo);
    }
  }

  async pushRepo(entry: RepoEntry): Promise<void> {
    const destPath = join(this.getVaultPath(), entry.prefix);
    new Notice(`"${entry.prefix}" を push しています...`);
    try {
      await runGit(destPath, ['push', entry.remoteName, entry.branch]);
      new Notice(`"${entry.prefix}" の push が完了しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`push に失敗しました:\n${msg}`, 8000);
    }
  }

  async pushAllRepos(): Promise<void> {
    if (this.settings.repos.length === 0) {
      new Notice('登録済みのリポジトリがありません');
      return;
    }
    for (const repo of this.settings.repos) {
      await this.pushRepo(repo);
    }
  }

  async switchBranch(entry: RepoEntry, newBranch: string): Promise<void> {
    const idx = this.settings.repos.findIndex((repo) => repo.prefix === entry.prefix);
    if (idx === -1) return;

    const oldBranch = this.settings.repos[idx].branch;
    this.settings.repos[idx].branch = newBranch;
    await this.saveSettings();
    new Notice(`"${entry.prefix}" のブランチを "${oldBranch}" から "${newBranch}" に変更しました`);
  }

  async pullWithStash(entry: RepoEntry): Promise<void> {
    const destPath = join(this.getVaultPath(), entry.prefix);
    new Notice(`"${entry.prefix}" の変更をスタッシュしています...`);
    try {
      const stashOut = await runGit(destPath, ['stash']);
      const hasStash = !stashOut.includes('No local changes');
      new Notice(`"${entry.prefix}" を pull しています...`);
      await runGit(destPath, ['pull', entry.remoteName, entry.branch]);
      if (hasStash) {
        new Notice(`"${entry.prefix}" のスタッシュを戻しています...`);
        await runGit(destPath, ['stash', 'pop']);
      }
      new Notice(`"${entry.prefix}" の pull が完了しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`スタッシュ pull に失敗しました:\n${msg}`, 8000);
    }
  }

  async checkoutBranch(entry: RepoEntry, branchName: string): Promise<void> {
    const destPath = join(this.getVaultPath(), entry.prefix);
    try {
      await runGit(destPath, ['checkout', branchName]);
      const idx = this.settings.repos.findIndex((r) => r.prefix === entry.prefix);
      if (idx !== -1) {
        this.settings.repos[idx].branch = branchName;
        await this.saveSettings();
      }
      new Notice(`"${entry.prefix}" を "${branchName}" に切り替えました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`ブランチの切り替えに失敗しました:\n${msg}`, 8000);
    }
  }

  async createAndPushBranch(entry: RepoEntry, branchName: string): Promise<void> {
    const destPath = join(this.getVaultPath(), entry.prefix);
    try {
      await runGit(destPath, ['checkout', '-b', branchName]);
      new Notice(`"${branchName}" ブランチを作成しました。push しています...`);
      await runGit(destPath, ['push', '-u', entry.remoteName, branchName]);
      const idx = this.settings.repos.findIndex((r) => r.prefix === entry.prefix);
      if (idx !== -1) {
        this.settings.repos[idx].branch = branchName;
        await this.saveSettings();
      }
      new Notice(`"${entry.prefix}" の新ブランチ "${branchName}" を push しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`ブランチの作成・push に失敗しました:\n${msg}`, 8000);
    }
  }

  // ---------------------------------------------------------------------------
  // 設定の保存 / 読み込み
  // ---------------------------------------------------------------------------

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

class GitNestSettingTab extends PluginSettingTab {
  plugin: GitNestPlugin;

  constructor(app: App, plugin: GitNestPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Git Nest 設定' });

    // ---- 登録済みリポジトリ一覧 ----

    containerEl.createEl('h3', { text: '登録済みリポジトリ' });

    if (this.plugin.settings.repos.length === 0) {
      containerEl.createEl('p', {
        text: '登録済みのリポジトリがありません。コマンドパレットか、フォルダの右クリックメニューから追加してください。',
        attr: { style: 'opacity:0.7;' },
      });
    }

    this.plugin.settings.repos.forEach((repo, idx) => {
      const card = containerEl.createDiv();
      card.style.cssText =
        'border:1px solid var(--background-modifier-border);border-radius:6px;padding:12px;margin-bottom:12px;';

      card.createEl('strong', { text: repo.prefix });

      const rows: Array<[string, string]> = [
        ['Remote URL', repo.remote],
        ['Remote 名', repo.remoteName],
        ['Branch', repo.branch],
        ...(repo.subdir ? [['サブフォルダ', repo.subdir] as [string, string]] : []),
      ];
      rows.forEach(([label, value]) => {
        const p = card.createEl('p');
        p.style.margin = '4px 0';
        p.createEl('span', {
          text: `${label}: `,
          attr: { style: 'opacity:0.6;font-size:0.85em;' },
        });
        p.createEl('code', { text: value });
      });

      const actions = card.createDiv({ attr: { style: 'margin-top:10px;display:flex;gap:8px;' } });

      const pullBtn = actions.createEl('button', { text: 'Pull' });
      pullBtn.addEventListener('click', () => this.plugin.pullRepo(repo));

      const pushBtn = actions.createEl('button', { text: 'Push' });
      pushBtn.addEventListener('click', () => this.plugin.pushRepo(repo));

      const switchBtn = actions.createEl('button', { text: 'ブランチ切り替え' });
      switchBtn.addEventListener('click', () =>
        new SwitchBranchModal(this.plugin.app, this.plugin, repo).open()
      );

      const removeBtn = actions.createEl('button', { text: '削除' });
      removeBtn.style.color = 'var(--text-error)';
      removeBtn.style.marginLeft = 'auto';
      removeBtn.addEventListener('click', async () => {
        this.plugin.settings.repos.splice(idx, 1);
        await this.plugin.saveSettings();
        this.display();
      });
    });

    // ---- リポジトリ追加ボタン ----

    new Setting(containerEl)
      .addButton((btn) =>
        btn
          .setButtonText('リポジトリを追加')
          .setCta()
          .onClick(() => new AddRepoModal(this.plugin.app, this.plugin).open())
      );
  }
}
