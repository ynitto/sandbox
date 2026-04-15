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
import { writeFileSync, existsSync, chmodSync } from 'fs';
import { join } from 'path';

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface SubtreeEntry {
  /** vault root からの相対パス (git subtree の --prefix 引数) */
  prefix: string;
  /** リモートリポジトリの URL */
  remote: string;
  /** git remote の短縮名 (例: "my-subtree") */
  remoteName: string;
  /** 同期対象のブランチ名 */
  branch: string;
}

interface GitSubtreeSettings {
  subtrees: SubtreeEntry[];
  /** git subtree pull/add 時に --squash を使用するか */
  useSquash: boolean;
  /** ルートの pull (post-merge) 時に subtree も自動 pull するか */
  autoPullOnMerge: boolean;
  /** ルートの push (pre-push) 時に subtree も自動 push するか */
  autoPushOnPush: boolean;
}

const DEFAULT_SETTINGS: GitSubtreeSettings = {
  subtrees: [],
  useSquash: true,
  autoPullOnMerge: true,
  autoPushOnPush: false,
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

// ---------------------------------------------------------------------------
// モーダル: subtree 追加
// ---------------------------------------------------------------------------

class AddSubtreeModal extends Modal {
  private plugin: GitSubtreePlugin;
  private initialPrefix: string;

  constructor(app: App, plugin: GitSubtreePlugin, initialPrefix = '') {
    super(app);
    this.plugin = plugin;
    this.initialPrefix = initialPrefix;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: 'Git Subtree を追加' });

    let prefix = this.initialPrefix;
    let remote = '';
    let remoteName = '';
    let branch = 'main';

    new Setting(contentEl)
      .setName('フォルダパス (prefix)')
      .setDesc('vault root からの相対パス。subtree が配置されるフォルダ')
      .addText((t) =>
        t.setValue(prefix).onChange((v) => { prefix = v.trim(); })
      );

    new Setting(contentEl)
      .setName('リモート URL')
      .setDesc('subtree として追加する git リポジトリの URL')
      .addText((t) =>
        t.setPlaceholder('https://github.com/user/repo.git').onChange((v) => { remote = v.trim(); })
      );

    new Setting(contentEl)
      .setName('リモート名')
      .setDesc('git remote の短縮名 (例: my-subtree)。スペース不可')
      .addText((t) =>
        t.setPlaceholder('my-subtree').onChange((v) => { remoteName = v.trim(); })
      );

    new Setting(contentEl)
      .setName('ブランチ')
      .setDesc('同期するリモートブランチ名')
      .addText((t) =>
        t.setValue(branch).onChange((v) => { branch = v.trim(); })
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
            await this.plugin.addSubtree({ prefix, remote, remoteName, branch });
          })
      );
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// モーダル: subtree 選択
// ---------------------------------------------------------------------------

class SelectSubtreeModal extends Modal {
  private subtrees: SubtreeEntry[];
  private title: string;
  private onSelect: (subtree: SubtreeEntry) => void;

  constructor(
    app: App,
    subtrees: SubtreeEntry[],
    title: string,
    onSelect: (s: SubtreeEntry) => void,
  ) {
    super(app);
    this.subtrees = subtrees;
    this.title = title;
    this.onSelect = onSelect;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: this.title });

    if (this.subtrees.length === 0) {
      contentEl.createEl('p', {
        text: '登録済みの subtree がありません。先に subtree を追加してください。',
      });
      return;
    }

    this.subtrees.forEach((st) => {
      const btn = contentEl.createEl('button');
      btn.style.cssText =
        'display:block;width:100%;margin-bottom:6px;text-align:left;padding:8px 12px;cursor:pointer;border-radius:4px;';

      const titleSpan = btn.createEl('span', { text: st.prefix });
      titleSpan.style.fontWeight = 'bold';

      btn.createEl('span', {
        text: `  ${st.remoteName}/${st.branch}`,
        attr: { style: 'opacity:0.6;font-size:0.85em;margin-left:8px;' },
      });

      btn.addEventListener('click', () => {
        this.close();
        this.onSelect(st);
      });
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// モーダル: ブランチ作成 / 切り替え
// ---------------------------------------------------------------------------

class BranchModal extends Modal {
  private plugin: GitSubtreePlugin;
  private subtree: SubtreeEntry;
  private mode: 'create' | 'switch';

  constructor(
    app: App,
    plugin: GitSubtreePlugin,
    subtree: SubtreeEntry,
    mode: 'create' | 'switch',
  ) {
    super(app);
    this.plugin = plugin;
    this.subtree = subtree;
    this.mode = mode;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();

    const isCreate = this.mode === 'create';
    const title = isCreate ? 'ブランチを作成' : 'ブランチを切り替え';
    contentEl.createEl('h3', { text: `${title}: ${this.subtree.prefix}` });

    if (!isCreate) {
      contentEl.createEl('p', {
        text: `現在のブランチ: ${this.subtree.branch}`,
        attr: { style: 'opacity:0.7;margin-bottom:12px;' },
      });
    }

    if (isCreate) {
      contentEl.createEl('p', {
        text: 'git subtree split で subtree の履歴のみを持つローカルブランチを作成します。',
        attr: { style: 'opacity:0.7;font-size:0.9em;margin-bottom:12px;' },
      });
    } else {
      contentEl.createEl('p', {
        text: '以降の pull/push で同期するリモートブランチを変更します。',
        attr: { style: 'opacity:0.7;font-size:0.9em;margin-bottom:12px;' },
      });
    }

    let branchName = '';

    new Setting(contentEl)
      .setName('ブランチ名')
      .addText((t) =>
        t
          .setPlaceholder(isCreate ? 'feature/my-feature' : 'develop')
          .onChange((v) => { branchName = v.trim(); })
      );

    new Setting(contentEl)
      .addButton((btn) =>
        btn
          .setButtonText(title)
          .setCta()
          .onClick(async () => {
            if (!branchName) {
              new Notice('ブランチ名を入力してください');
              return;
            }
            this.close();
            if (isCreate) {
              await this.plugin.createSubtreeBranch(this.subtree, branchName);
            } else {
              await this.plugin.switchSubtreeBranch(this.subtree, branchName);
            }
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

export default class GitSubtreePlugin extends Plugin {
  settings: GitSubtreeSettings = DEFAULT_SETTINGS;

  async onload() {
    await this.loadSettings();

    // ---- コマンド登録 ----

    this.addCommand({
      id: 'add-subtree',
      name: 'Subtree を追加',
      callback: () => new AddSubtreeModal(this.app, this).open(),
    });

    this.addCommand({
      id: 'pull-subtree',
      name: 'Subtree を pull (選択)',
      callback: () =>
        new SelectSubtreeModal(
          this.app,
          this.settings.subtrees,
          'Pull する Subtree を選択',
          (st) => this.pullSubtree(st),
        ).open(),
    });

    this.addCommand({
      id: 'pull-all-subtrees',
      name: 'すべての Subtree を pull',
      callback: () => this.pullAllSubtrees(),
    });

    this.addCommand({
      id: 'push-subtree',
      name: 'Subtree を push (選択)',
      callback: () =>
        new SelectSubtreeModal(
          this.app,
          this.settings.subtrees,
          'Push する Subtree を選択',
          (st) => this.pushSubtree(st),
        ).open(),
    });

    this.addCommand({
      id: 'push-all-subtrees',
      name: 'すべての Subtree を push',
      callback: () => this.pushAllSubtrees(),
    });

    this.addCommand({
      id: 'create-subtree-branch',
      name: 'Subtree のブランチを作成',
      callback: () =>
        new SelectSubtreeModal(
          this.app,
          this.settings.subtrees,
          'ブランチを作成する Subtree を選択',
          (st) => new BranchModal(this.app, this, st, 'create').open(),
        ).open(),
    });

    this.addCommand({
      id: 'switch-subtree-branch',
      name: 'Subtree のブランチを切り替え',
      callback: () =>
        new SelectSubtreeModal(
          this.app,
          this.settings.subtrees,
          'ブランチを切り替える Subtree を選択',
          (st) => new BranchModal(this.app, this, st, 'switch').open(),
        ).open(),
    });

    this.addCommand({
      id: 'install-git-hooks',
      name: 'Git hooks をインストール',
      callback: () => this.installGitHooks(),
    });

    // ---- フォルダ右クリックメニュー ----

    this.registerEvent(
      this.app.workspace.on(
        'file-menu',
        (menu: Menu, abstractFile: TAbstractFile) => {
          if (!(abstractFile instanceof TFolder)) return;
          const prefix = abstractFile.path;
          const existing = this.settings.subtrees.find((st) => st.prefix === prefix);

          if (existing) {
            menu.addItem((item: MenuItem) =>
              item
                .setTitle('Git Subtree: Pull')
                .setIcon('download')
                .onClick(() => this.pullSubtree(existing))
            );
            menu.addItem((item: MenuItem) =>
              item
                .setTitle('Git Subtree: Push')
                .setIcon('upload')
                .onClick(() => this.pushSubtree(existing))
            );
            menu.addItem((item: MenuItem) =>
              item
                .setTitle('Git Subtree: ブランチを切り替え')
                .setIcon('git-branch')
                .onClick(() => new BranchModal(this.app, this, existing, 'switch').open())
            );
            menu.addItem((item: MenuItem) =>
              item
                .setTitle('Git Subtree: ブランチを作成')
                .setIcon('git-branch')
                .onClick(() => new BranchModal(this.app, this, existing, 'create').open())
            );
          } else {
            menu.addItem((item: MenuItem) =>
              item
                .setTitle('Git Subtree として追加')
                .setIcon('git-pull-request')
                .onClick(() => new AddSubtreeModal(this.app, this, prefix).open())
            );
          }
        },
      )
    );

    this.addSettingTab(new GitSubtreeSettingTab(this.app, this));
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
  // Subtree 操作
  // ---------------------------------------------------------------------------

  async addSubtree(entry: SubtreeEntry): Promise<void> {
    const vaultPath = this.getVaultPath();
    new Notice(`Subtree "${entry.prefix}" を追加しています...`);
    try {
      // リモートが未登録の場合は追加
      const remoteList = await runGit(vaultPath, ['remote']);
      if (!remoteList.split('\n').includes(entry.remoteName)) {
        await runGit(vaultPath, ['remote', 'add', entry.remoteName, entry.remote]);
      }

      const args = [
        'subtree', 'add',
        `--prefix=${entry.prefix}`,
        entry.remoteName,
        entry.branch,
      ];
      if (this.settings.useSquash) args.push('--squash');

      await runGit(vaultPath, args);

      this.settings.subtrees.push(entry);
      await this.saveSettings();
      await this.installGitHooks();
      new Notice(`Subtree "${entry.prefix}" を追加しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`Subtree の追加に失敗しました:\n${msg}`, 8000);
    }
  }

  async pullSubtree(entry: SubtreeEntry): Promise<void> {
    const vaultPath = this.getVaultPath();
    new Notice(`Subtree "${entry.prefix}" を pull しています...`);
    try {
      const args = [
        'subtree', 'pull',
        `--prefix=${entry.prefix}`,
        entry.remoteName,
        entry.branch,
      ];
      if (this.settings.useSquash) args.push('--squash');

      await runGit(vaultPath, args);
      new Notice(`Subtree "${entry.prefix}" の pull が完了しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`Subtree の pull に失敗しました:\n${msg}`, 8000);
    }
  }

  async pullAllSubtrees(): Promise<void> {
    if (this.settings.subtrees.length === 0) {
      new Notice('登録済みの subtree がありません');
      return;
    }
    for (const st of this.settings.subtrees) {
      await this.pullSubtree(st);
    }
  }

  async pushSubtree(entry: SubtreeEntry): Promise<void> {
    const vaultPath = this.getVaultPath();
    new Notice(`Subtree "${entry.prefix}" を push しています...`);
    try {
      await runGit(vaultPath, [
        'subtree', 'push',
        `--prefix=${entry.prefix}`,
        entry.remoteName,
        entry.branch,
      ]);
      new Notice(`Subtree "${entry.prefix}" の push が完了しました`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`Subtree の push に失敗しました:\n${msg}`, 8000);
    }
  }

  async pushAllSubtrees(): Promise<void> {
    if (this.settings.subtrees.length === 0) {
      new Notice('登録済みの subtree がありません');
      return;
    }
    for (const st of this.settings.subtrees) {
      await this.pushSubtree(st);
    }
  }

  /**
   * git subtree split でローカルブランチを作成する。
   * subtree の履歴のみを抽出したブランチが作られ、
   * その後 git push <remoteName> <localBranch>:<remoteBranch> でリモートに反映できる。
   */
  async createSubtreeBranch(entry: SubtreeEntry, branchName: string): Promise<void> {
    const vaultPath = this.getVaultPath();
    new Notice(`ブランチ "${branchName}" を作成しています...`);
    try {
      await runGit(vaultPath, [
        'subtree', 'split',
        `--prefix=${entry.prefix}`,
        '-b', branchName,
      ]);
      new Notice(
        `ブランチ "${branchName}" を作成しました。\n` +
        `リモートに push するには:\n` +
        `git push ${entry.remoteName} ${branchName}:<リモートブランチ名>`,
        8000,
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`ブランチの作成に失敗しました:\n${msg}`, 8000);
    }
  }

  /**
   * 以降の pull/push で同期するリモートブランチを変更する。
   * 設定を更新して git hooks を再生成する。
   */
  async switchSubtreeBranch(entry: SubtreeEntry, newBranch: string): Promise<void> {
    const idx = this.settings.subtrees.findIndex((st) => st.prefix === entry.prefix);
    if (idx === -1) return;

    const oldBranch = this.settings.subtrees[idx].branch;
    this.settings.subtrees[idx].branch = newBranch;
    await this.saveSettings();
    await this.installGitHooks();
    new Notice(
      `Subtree "${entry.prefix}" のブランチを "${oldBranch}" から "${newBranch}" に変更しました`,
    );
  }

  // ---------------------------------------------------------------------------
  // Git hooks インストール
  // ---------------------------------------------------------------------------

  async installGitHooks(): Promise<void> {
    let vaultPath: string;
    try {
      vaultPath = this.getVaultPath();
    } catch {
      new Notice('FileSystemAdapter が利用できません');
      return;
    }

    const hooksDir = join(vaultPath, '.git', 'hooks');
    if (!existsSync(hooksDir)) {
      new Notice('Git hooks ディレクトリが見つかりません。git リポジトリか確認してください。', 8000);
      return;
    }

    const subtrees = this.settings.subtrees;
    const squash = this.settings.useSquash ? ' --squash' : '';

    // ---- post-merge (pull 後に実行) ----
    const pullLines = subtrees
      .map((st) =>
        `git subtree pull --prefix=${st.prefix} ${st.remoteName} ${st.branch}${squash} || echo "WARN: subtree pull failed for ${st.prefix}"`
      )
      .join('\n');

    const postMergeContent = [
      '#!/bin/bash',
      '# Generated by obsidian-git-subtree plugin',
      '# DO NOT EDIT MANUALLY - このファイルはプラグインにより自動生成されます',
      '',
      subtrees.length > 0
        ? `# Subtree auto-pull (${subtrees.length} subtree(s))\n${pullLines}`
        : '# subtree が登録されていません',
      '',
    ].join('\n');

    // ---- pre-push (push 前に実行) ----
    const pushLines = subtrees
      .map((st) =>
        `git subtree push --prefix=${st.prefix} ${st.remoteName} ${st.branch} || echo "WARN: subtree push failed for ${st.prefix}"`
      )
      .join('\n');

    const prePushContent = [
      '#!/bin/bash',
      '# Generated by obsidian-git-subtree plugin',
      '# DO NOT EDIT MANUALLY - このファイルはプラグインにより自動生成されます',
      '',
      subtrees.length > 0
        ? `# Subtree auto-push (${subtrees.length} subtree(s))\n${pushLines}`
        : '# subtree が登録されていません',
      '',
    ].join('\n');

    const postMergePath = join(hooksDir, 'post-merge');
    const prePushPath = join(hooksDir, 'pre-push');

    if (this.settings.autoPullOnMerge) {
      writeFileSync(postMergePath, postMergeContent, { encoding: 'utf-8' });
      chmodSync(postMergePath, 0o755);
    }

    if (this.settings.autoPushOnPush) {
      writeFileSync(prePushPath, prePushContent, { encoding: 'utf-8' });
      chmodSync(prePushPath, 0o755);
    }

    new Notice('Git hooks をインストールしました');
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

class GitSubtreeSettingTab extends PluginSettingTab {
  plugin: GitSubtreePlugin;

  constructor(app: App, plugin: GitSubtreePlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'Git Subtree 設定' });

    // ---- 全般設定 ----

    new Setting(containerEl)
      .setName('--squash を使用')
      .setDesc('pull/add 時に --squash フラグを使用して履歴をまとめる (推奨)')
      .addToggle((t) =>
        t.setValue(this.plugin.settings.useSquash).onChange(async (v) => {
          this.plugin.settings.useSquash = v;
          await this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName('pull 時に subtree を自動 pull')
      .setDesc('ルートの git pull 後に post-merge hook で subtree を自動 pull する')
      .addToggle((t) =>
        t.setValue(this.plugin.settings.autoPullOnMerge).onChange(async (v) => {
          this.plugin.settings.autoPullOnMerge = v;
          await this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName('push 時に subtree を自動 push')
      .setDesc('ルートの git push 前に pre-push hook で subtree を自動 push する')
      .addToggle((t) =>
        t.setValue(this.plugin.settings.autoPushOnPush).onChange(async (v) => {
          this.plugin.settings.autoPushOnPush = v;
          await this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName('Git Hooks')
      .setDesc('現在の設定で git hooks を再生成してインストールする')
      .addButton((btn) =>
        btn.setButtonText('hooks をインストール').onClick(() => this.plugin.installGitHooks())
      );

    // ---- 登録済み subtree 一覧 ----

    containerEl.createEl('h3', { text: '登録済み Subtree' });

    if (this.plugin.settings.subtrees.length === 0) {
      containerEl.createEl('p', {
        text: '登録済みの subtree がありません。コマンドパレットか、フォルダの右クリックメニューから追加してください。',
        attr: { style: 'opacity:0.7;' },
      });
    }

    this.plugin.settings.subtrees.forEach((st, idx) => {
      const card = containerEl.createDiv();
      card.style.cssText =
        'border:1px solid var(--background-modifier-border);border-radius:6px;padding:12px;margin-bottom:12px;';

      card.createEl('strong', { text: st.prefix });

      const rows: Array<[string, string]> = [
        ['Remote URL', st.remote],
        ['Remote 名', st.remoteName],
        ['Branch', st.branch],
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
      pullBtn.addEventListener('click', () => this.plugin.pullSubtree(st));

      const pushBtn = actions.createEl('button', { text: 'Push' });
      pushBtn.addEventListener('click', () => this.plugin.pushSubtree(st));

      const switchBtn = actions.createEl('button', { text: 'ブランチ切り替え' });
      switchBtn.addEventListener('click', () =>
        new BranchModal(this.plugin.app, this.plugin, st, 'switch').open()
      );

      const removeBtn = actions.createEl('button', { text: '削除' });
      removeBtn.style.color = 'var(--text-error)';
      removeBtn.style.marginLeft = 'auto';
      removeBtn.addEventListener('click', async () => {
        this.plugin.settings.subtrees.splice(idx, 1);
        await this.plugin.saveSettings();
        await this.plugin.installGitHooks();
        this.display();
      });
    });

    // ---- Subtree 追加ボタン ----

    new Setting(containerEl)
      .addButton((btn) =>
        btn
          .setButtonText('Subtree を追加')
          .setCta()
          .onClick(() => new AddSubtreeModal(this.plugin.app, this.plugin).open())
      );
  }
}
