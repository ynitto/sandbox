import {
  App,
  MarkdownPostProcessorContext,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  SuggestModal,
  TFile,
} from 'obsidian';
import * as fs from 'fs';
import * as nodePath from 'path';
import { execSync } from 'child_process';

// ============================================================
// Types
// ============================================================

interface GitRemote {
  name: string;
  url: string;
}

interface GitRepository {
  id: string;
  path: string;
  name: string;
  remotes: GitRemote[];
  addedAt: string;
  lastUpdated: string;
}

interface PluginData {
  repositories: GitRepository[];
  exportPath: string;
  maxScanDepth: number;
  insertTemplate: string;
}

const DEFAULT_DATA: PluginData = {
  repositories: [],
  exportPath: 'git-repositories.json',
  maxScanDepth: 5,
  insertTemplate: '{{value}}',
};

// ============================================================
// Git helpers
// ============================================================

function getGitRemotes(repoPath: string): GitRemote[] {
  try {
    const output = execSync('git remote -v', {
      cwd: repoPath,
      encoding: 'utf8',
      timeout: 5000,
    });
    const seen = new Map<string, string>();
    for (const line of output.split('\n')) {
      const m = line.match(/^(\S+)\s+(\S+)\s+\(fetch\)/);
      if (m) seen.set(m[1], m[2]);
    }
    return Array.from(seen.entries()).map(([name, url]) => ({ name, url }));
  } catch {
    return [];
  }
}

function getGitBranches(repoPath: string): { local: string[]; remote: string[] } {
  const local: string[] = [];
  const remote: string[] = [];

  try {
    const out = execSync('git branch', { cwd: repoPath, encoding: 'utf8', timeout: 5000 });
    for (const line of out.split('\n')) {
      const name = line.replace(/^\*?\s+/, '').trim();
      if (name) local.push(name);
    }
  } catch { /* ignore */ }

  try {
    const out = execSync('git branch -r', { cwd: repoPath, encoding: 'utf8', timeout: 5000 });
    for (const line of out.split('\n')) {
      if (line.includes('->')) continue;
      const name = line.trim();
      if (name) remote.push(name);
    }
  } catch { /* ignore */ }

  return { local, remote };
}

function isGitRepo(dirPath: string): boolean {
  try {
    return fs.existsSync(nodePath.join(dirPath, '.git'));
  } catch {
    return false;
  }
}

function buildRepository(repoPath: string): GitRepository {
  const now = new Date().toISOString();
  return {
    id: crypto.randomUUID(),
    path: repoPath,
    name: nodePath.basename(repoPath),
    remotes: getGitRemotes(repoPath),
    addedAt: now,
    lastUpdated: now,
  };
}

function scanForRepoPaths(rootPath: string, maxDepth: number): string[] {
  const found: string[] = [];

  function walk(dir: string, depth: number) {
    if (depth > maxDepth) return;
    if (isGitRepo(dir)) {
      found.push(dir);
      return; // don't recurse into git repos
    }
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (entry.name.startsWith('.')) continue;
      if (entry.name === 'node_modules') continue;
      walk(nodePath.join(dir, entry.name), depth + 1);
    }
  }

  walk(rootPath, 0);
  return found;
}

// ============================================================
// Markdown Code Block Renderer
// ============================================================

function renderGitManagerBlock(
  source: string,
  el: HTMLElement,
  plugin: GitManagerPlugin,
  ctx: MarkdownPostProcessorContext,
): void {
  const config: Record<string, string> = {};
  for (const line of source.split('\n')) {
    const m = line.match(/^(\w+)\s*:\s*(.+)/);
    if (m) config[m[1].trim()] = m[2].trim();
  }
  const show = (config['show'] ?? 'all').toLowerCase();

  const repos = plugin.data.repositories;
  const container = el.createDiv({ cls: 'git-manager-block' });
  container.style.cssText =
    'border:1px solid var(--background-modifier-border); border-radius:6px; padding:12px; font-size:0.9em;';

  if (repos.length === 0) {
    container.createEl('p', {
      text: 'リポジトリが登録されていません。プラグイン設定からリポジトリを追加してください。',
      attr: { style: 'color:var(--text-muted); margin:0;' },
    });
    return;
  }

  container.createEl('div', {
    text: 'Git リポジトリ',
    attr: { style: 'font-weight:600; margin-bottom:8px; color:var(--text-normal);' },
  });

  const selectEl = container.createEl('select');
  selectEl.style.cssText =
    'width:100%; margin-bottom:10px; padding:4px 8px;' +
    ' background:var(--background-secondary); color:var(--text-normal);' +
    ' border:1px solid var(--background-modifier-border); border-radius:4px;';
  for (const repo of repos) {
    selectEl.createEl('option', { text: repo.name, value: repo.id });
  }

  const infoPanel = container.createDiv();

  async function insertBelowBlock(name: string, value: string) {
    const info = ctx.getSectionInfo(el);
    const file = plugin.app.vault.getAbstractFileByPath(ctx.sourcePath);
    if (!info || !(file instanceof TFile)) return;
    const text = plugin.data.insertTemplate
      .replace(/\{\{name\}\}/g, name)
      .replace(/\{\{value\}\}/g, value);
    const content = await plugin.app.vault.read(file);
    const lines = content.split('\n');
    lines.splice(info.lineEnd + 1, 0, text);
    await plugin.app.vault.modify(file, lines.join('\n'));
  }

  function renderInfo(repoId: string) {
    infoPanel.empty();
    const repo = repos.find(r => r.id === repoId);
    if (!repo) return;

    function makeInsertRow(name: string, value: string) {
      const row = infoPanel.createDiv({
        attr: { style: 'display:flex; align-items:center; gap:8px; margin-bottom:6px;' },
      });
      row.createEl('span', {
        text: `${name}:`,
        attr: { style: 'color:var(--text-muted); min-width:70px; flex-shrink:0;' },
      });
      row.createEl('code', {
        text: value,
        attr: { style: 'flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;' },
      });
      const btn = row.createEl('button', {
        text: '挿入',
        attr: { style: 'padding:2px 8px; font-size:0.85em; flex-shrink:0;' },
      });
      btn.addEventListener('click', async () => {
        await insertBelowBlock(name, value);
        btn.textContent = '✓';
        setTimeout(() => { btn.textContent = '挿入'; }, 1500);
      });
    }

    if (show === 'all' || show === 'folder') {
      makeInsertRow('フォルダ', repo.path);
    }

    if (show === 'all' || show === 'remote') {
      if (repo.remotes.length === 0) {
        infoPanel.createEl('p', {
          text: 'リモートなし',
          attr: { style: 'color:var(--text-muted); margin:4px 0;' },
        });
      } else {
        for (const remote of repo.remotes) {
          makeInsertRow(remote.name, remote.url);
        }
      }
    }

    if (show === 'all' || show === 'branch') {
      const branchRow = infoPanel.createDiv({
        attr: { style: 'display:flex; align-items:center; gap:8px; margin-top:6px;' },
      });
      branchRow.createEl('span', {
        text: 'ブランチ:',
        attr: { style: 'color:var(--text-muted); min-width:70px; flex-shrink:0;' },
      });
      const branchBtn = branchRow.createEl('button', {
        text: 'ブランチを選択',
        attr: { style: 'padding:2px 8px; font-size:0.85em;' },
      });
      branchBtn.addEventListener('click', () => {
        const { local, remote } = getGitBranches(repo.path);
        const all = [...local, ...remote];
        if (all.length === 0) {
          new Notice('ブランチが見つかりませんでした');
          return;
        }
        new BranchSelectModal(plugin.app, all, async (branch) => {
          await insertBelowBlock('ブランチ', branch);
        }).open();
      });
    }
  }

  renderInfo(repos[0].id);
  selectEl.addEventListener('change', e => {
    renderInfo((e.target as HTMLSelectElement).value);
  });
}

// ============================================================
// Branch Select Modal
// ============================================================

class BranchSelectModal extends SuggestModal<string> {
  private branches: string[];
  private onSelect: (branch: string) => void;

  constructor(app: App, branches: string[], onSelect: (branch: string) => void) {
    super(app);
    this.branches = branches;
    this.onSelect = onSelect;
    this.setPlaceholder('ブランチ名を入力（前方一致で絞り込み）');
  }

  getSuggestions(query: string): string[] {
    if (!query) return this.branches;
    const q = query.toLowerCase();
    return this.branches.filter(b => b.toLowerCase().startsWith(q));
  }

  renderSuggestion(branch: string, el: HTMLElement): void {
    el.setText(branch);
  }

  onChooseSuggestion(branch: string, _evt: MouseEvent | KeyboardEvent): void {
    this.onSelect(branch);
  }
}

// ============================================================
// Scan Modal
// ============================================================

class ScanModal extends Modal {
  private plugin: GitManagerPlugin;
  private inputEl!: HTMLInputElement;
  private depthEl!: HTMLInputElement;
  private resultEl!: HTMLElement;

  constructor(app: App, plugin: GitManagerPlugin) {
    super(app);
    this.plugin = plugin;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.createEl('h3', { text: 'フォルダをスキャン' });

    new Setting(contentEl)
      .setName('スキャンするフォルダパス')
      .setDesc('このフォルダ以下を再帰的に走査して Git リポジトリを検出します')
      .addText(t => {
        t.setPlaceholder('/home/user/projects');
        this.inputEl = t.inputEl;
        this.inputEl.style.width = '100%';
      });

    new Setting(contentEl)
      .setName('最大探索深度')
      .addText(t => {
        t.setValue(String(this.plugin.data.maxScanDepth));
        t.inputEl.type = 'number';
        t.inputEl.min = '1';
        t.inputEl.max = '10';
        this.depthEl = t.inputEl;
      });

    this.resultEl = contentEl.createEl('p', {
      attr: { style: 'color:var(--text-muted); min-height:1.5em; margin:8px 0;' },
    });

    const btnRow = contentEl.createDiv({
      attr: { style: 'display:flex; justify-content:flex-end; gap:8px; margin-top:16px;' },
    });
    btnRow.createEl('button', { text: 'キャンセル' }).addEventListener('click', () => this.close());

    const scanBtn = btnRow.createEl('button', { text: 'スキャン開始', cls: 'mod-cta' });
    scanBtn.addEventListener('click', () => {
      const rootPath = this.inputEl.value.trim();
      if (!rootPath) {
        this.resultEl.textContent = 'フォルダパスを入力してください';
        return;
      }
      if (!fs.existsSync(rootPath)) {
        this.resultEl.textContent = `フォルダが見つかりません: ${rootPath}`;
        return;
      }
      const depth = parseInt(this.depthEl.value, 10) || this.plugin.data.maxScanDepth;
      this.resultEl.textContent = 'スキャン中...';
      scanBtn.disabled = true;

      // Defer to allow UI repaint
      setTimeout(async () => {
        const { added, found } = await this.plugin.scanAndRegister(rootPath, depth);
        this.resultEl.textContent = `${found} 件発見 / ${added} 件を新規追加しました`;
        scanBtn.disabled = false;
        if (added > 0) setTimeout(() => this.close(), 1500);
      }, 50);
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ============================================================
// Settings Tab
// ============================================================

class GitManagerSettingTab extends PluginSettingTab {
  plugin: GitManagerPlugin;

  constructor(app: App, plugin: GitManagerPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl('h2', { text: 'Git Repository Manager' });

    // ---- 挿入テンプレート ----
    containerEl.createEl('h3', { text: '挿入テンプレート' });
    containerEl.createEl('p', {
      text: '{{name}} = 項目名（フォルダ / リモート名）、{{value}} = 項目値（パス / URL）',
      attr: { style: 'color:var(--text-muted); margin-bottom:8px;' },
    });

    new Setting(containerEl)
      .setName('テンプレート')
      .setDesc('コードブロック下に挿入するテキストのテンプレート')
      .addText(t => {
        t
          .setPlaceholder('{{value}}')
          .setValue(this.plugin.data.insertTemplate)
          .onChange(async v => {
            this.plugin.data.insertTemplate = v || '{{value}}';
            await this.plugin.savePluginData();
          });
        t.inputEl.style.width = '300px';
      });

    // ---- エクスポート設定 ----
    containerEl.createEl('h3', { text: 'エクスポート設定', attr: { style: 'margin-top:24px;' } });

    new Setting(containerEl)
      .setName('エクスポートファイルパス')
      .setDesc('Vault 内のエクスポート先 JSON ファイルパス（相対パス）')
      .addText(t =>
        t
          .setPlaceholder('git-repositories.json')
          .setValue(this.plugin.data.exportPath)
          .onChange(async v => {
            this.plugin.data.exportPath = v.trim() || 'git-repositories.json';
            await this.plugin.savePluginData();
          })
      )
      .addButton(btn =>
        btn
          .setButtonText('エクスポート')
          .setCta()
          .onClick(async () => {
            await this.plugin.exportToJson();
          })
      );

    // ---- 手動追加 ----
    containerEl.createEl('h3', { text: 'リポジトリを手動追加', attr: { style: 'margin-top:24px;' } });
    containerEl.createEl('p', {
      text: 'Git リポジトリのフォルダパス（絶対パス）を入力して追加します。',
      attr: { style: 'color:var(--text-muted); margin-bottom:8px;' },
    });

    let manualPath = '';
    new Setting(containerEl)
      .setName('フォルダパス')
      .addText(t => {
        t.setPlaceholder('/path/to/repo').onChange(v => { manualPath = v.trim(); });
        t.inputEl.style.width = '300px';
      })
      .addButton(btn =>
        btn
          .setButtonText('追加')
          .setCta()
          .onClick(async () => {
            await this.plugin.addRepository(manualPath);
            this.display();
          })
      );

    // ---- 自動スキャン ----
    containerEl.createEl('h3', { text: 'フォルダを自動スキャン', attr: { style: 'margin-top:24px;' } });
    containerEl.createEl('p', {
      text: '指定フォルダ以下を再帰的に走査して Git リポジトリを自動検出・登録します。',
      attr: { style: 'color:var(--text-muted); margin-bottom:8px;' },
    });

    let scanPath = '';
    new Setting(containerEl)
      .setName('スキャンするフォルダパス')
      .addText(t => {
        t.setPlaceholder('/path/to/scan').onChange(v => { scanPath = v.trim(); });
        t.inputEl.style.width = '300px';
      })
      .addButton(btn =>
        btn.setButtonText('スキャン').onClick(async () => {
          if (!scanPath) {
            new Notice('スキャンするフォルダを入力してください');
            return;
          }
          const { added, found } = await this.plugin.scanAndRegister(
            scanPath,
            this.plugin.data.maxScanDepth,
          );
          new Notice(`${found} 件発見 / ${added} 件を新規追加しました`);
          this.display();
        })
      );

    new Setting(containerEl)
      .setName('最大探索深度')
      .setDesc('スキャン時に探索するフォルダの最大深度（1〜10）')
      .addSlider(sl =>
        sl
          .setLimits(1, 10, 1)
          .setValue(this.plugin.data.maxScanDepth)
          .setDynamicTooltip()
          .onChange(async v => {
            this.plugin.data.maxScanDepth = v;
            await this.plugin.savePluginData();
          })
      );

    // ---- 登録済みリポジトリ ----
    const count = this.plugin.data.repositories.length;
    containerEl.createEl('h3', {
      text: `登録済みリポジトリ（${count} 件）`,
      attr: { style: 'margin-top:24px;' },
    });

    if (count === 0) {
      containerEl.createEl('p', {
        text: 'リポジトリが登録されていません。上の機能で追加・スキャンしてください。',
        attr: { style: 'color:var(--text-muted);' },
      });
      return;
    }

    new Setting(containerEl)
      .addButton(btn =>
        btn.setButtonText('全て更新').setTooltip('全リポジトリのリモート情報を再取得').onClick(async () => {
          await this.plugin.refreshAllRepositories();
          this.display();
        })
      )
      .addButton(btn =>
        btn.setButtonText('全て削除').setWarning().onClick(async () => {
          this.plugin.data.repositories = [];
          await this.plugin.savePluginData();
          this.display();
        })
      );

    for (const repo of this.plugin.data.repositories) {
      const remoteLines =
        repo.remotes.length > 0
          ? repo.remotes.map(r => `${r.name}: ${r.url}`).join('  |  ')
          : 'リモートなし';
      const desc = `${repo.path}\n${remoteLines}`;

      new Setting(containerEl)
        .setName(repo.name)
        .setDesc(desc)
        .addButton(btn =>
          btn
            .setIcon('refresh-cw')
            .setTooltip('リモートを再取得')
            .onClick(async () => {
              await this.plugin.refreshRepository(repo.id);
              this.display();
            })
        )
        .addButton(btn =>
          btn
            .setIcon('trash')
            .setTooltip('削除')
            .setWarning()
            .onClick(async () => {
              this.plugin.data.repositories = this.plugin.data.repositories.filter(
                r => r.id !== repo.id,
              );
              await this.plugin.savePluginData();
              this.display();
            })
        );
    }
  }
}

// ============================================================
// Plugin
// ============================================================

export default class GitManagerPlugin extends Plugin {
  data: PluginData = { ...DEFAULT_DATA };

  async onload() {
    const saved = await this.loadData();
    this.data = Object.assign({}, DEFAULT_DATA, saved);

    this.addSettingTab(new GitManagerSettingTab(this.app, this));

    this.registerMarkdownCodeBlockProcessor('git-manager', (source, el, ctx) => {
      renderGitManagerBlock(source, el, this, ctx);
    });

    this.addCommand({
      id: 'export-to-json',
      name: 'リポジトリ情報を JSON にエクスポート',
      callback: () => this.exportToJson(),
    });

    this.addCommand({
      id: 'scan-folder',
      name: 'フォルダをスキャンしてリポジトリを自動登録',
      callback: () => new ScanModal(this.app, this).open(),
    });

    this.addCommand({
      id: 'refresh-all',
      name: '全リポジトリのリモート情報を更新',
      callback: () => this.refreshAllRepositories(),
    });
  }

  onunload() {}

  async savePluginData() {
    await this.saveData(this.data);
  }

  async addRepository(repoPath: string): Promise<boolean> {
    if (!repoPath) {
      new Notice('パスを入力してください');
      return false;
    }
    if (!isGitRepo(repoPath)) {
      new Notice(`Git リポジトリではありません: ${repoPath}`);
      return false;
    }
    if (this.data.repositories.find(r => r.path === repoPath)) {
      new Notice(`既に登録済みです: ${nodePath.basename(repoPath)}`);
      return false;
    }
    const repo = buildRepository(repoPath);
    this.data.repositories.push(repo);
    await this.savePluginData();
    new Notice(`追加しました: ${repo.name}（リモート ${repo.remotes.length} 件）`);
    return true;
  }

  async scanAndRegister(
    rootPath: string,
    maxDepth: number,
  ): Promise<{ found: number; added: number }> {
    if (!fs.existsSync(rootPath)) {
      new Notice(`フォルダが見つかりません: ${rootPath}`);
      return { found: 0, added: 0 };
    }
    const paths = scanForRepoPaths(rootPath, maxDepth);
    let added = 0;
    for (const p of paths) {
      if (!this.data.repositories.find(r => r.path === p)) {
        this.data.repositories.push(buildRepository(p));
        added++;
      }
    }
    if (added > 0) await this.savePluginData();
    return { found: paths.length, added };
  }

  async refreshRepository(id: string): Promise<void> {
    const repo = this.data.repositories.find(r => r.id === id);
    if (!repo) return;
    repo.remotes = getGitRemotes(repo.path);
    repo.lastUpdated = new Date().toISOString();
    await this.savePluginData();
    new Notice(`更新しました: ${repo.name}（リモート ${repo.remotes.length} 件）`);
  }

  async refreshAllRepositories(): Promise<void> {
    const now = new Date().toISOString();
    for (const repo of this.data.repositories) {
      repo.remotes = getGitRemotes(repo.path);
      repo.lastUpdated = now;
    }
    await this.savePluginData();
    new Notice(`${this.data.repositories.length} 件のリポジトリを更新しました`);
  }

  async exportToJson(): Promise<void> {
    const payload = {
      exportedAt: new Date().toISOString(),
      count: this.data.repositories.length,
      repositories: this.data.repositories,
    };
    const content = JSON.stringify(payload, null, 2);
    const filePath = this.data.exportPath;

    const existing = this.app.vault.getAbstractFileByPath(filePath);
    if (existing instanceof TFile) {
      await this.app.vault.modify(existing, content);
    } else {
      await this.app.vault.create(filePath, content);
    }
    new Notice(`エクスポートしました: ${filePath}（${payload.count} 件）`);
  }
}
