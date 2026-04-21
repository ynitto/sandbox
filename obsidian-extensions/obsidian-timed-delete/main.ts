import {
  App,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TFile,
  TFolder,
} from 'obsidian';

// ============================================================
// Types
// ============================================================

type AgeBase = 'created' | 'modified';
type DeleteMode = 'trash' | 'permanent';

interface DeleteRule {
  id: string;
  name: string;
  enabled: boolean;

  // Target
  folder: string;
  recursive: boolean;

  // Conditions
  olderThanDays: number;     // 0 = disabled
  ageBase: AgeBase;          // compare creation or modification time
  filePattern: string;       // glob/substring filter on filename, '' = all files
  extensionFilter: string;   // comma-separated extensions e.g. 'md,png', '' = all
  minSizeKB: number;         // 0 = disabled

  // Action
  deleteMode: DeleteMode;

  // Schedule
  runOnStartup: boolean;
  intervalHours: number;     // 0 = disabled
  cronExpression: string;    // '' = disabled, format: 'min hour dom month dow'

  // Internal state
  lastRunTimestamp: number;
  lastRunMinute: string;
}

interface PluginSettings {
  rules: DeleteRule[];
  dryRun: boolean;
}

const DEFAULT_RULE: Omit<DeleteRule, 'id' | 'name'> = {
  enabled: true,
  folder: '',
  recursive: true,
  olderThanDays: 30,
  ageBase: 'created',
  filePattern: '',
  extensionFilter: '',
  minSizeKB: 0,
  deleteMode: 'trash',
  runOnStartup: true,
  intervalHours: 0,
  cronExpression: '',
  lastRunTimestamp: 0,
  lastRunMinute: '',
};

const DEFAULT_SETTINGS: PluginSettings = {
  rules: [],
  dryRun: false,
};

// ============================================================
// Glob / pattern matching
// ============================================================

function matchesPattern(filename: string, pattern: string): boolean {
  if (!pattern) return true;
  const regexStr = pattern
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*/g, '.*')
    .replace(/\*/g, '[^/]*')
    .replace(/\?/g, '[^/]');
  return new RegExp(`^${regexStr}$`, 'i').test(filename);
}

function matchesExtension(filename: string, extFilter: string): boolean {
  if (!extFilter) return true;
  const exts = extFilter.split(',').map((e) => e.trim().replace(/^\./, '').toLowerCase());
  const fileExt = filename.split('.').pop()?.toLowerCase() ?? '';
  return exts.includes(fileExt);
}

// ============================================================
// Cron parser (same as obsidian-file-watcher)
// ============================================================

type CronFields = {
  minute: number[];
  hour: number[];
  dom: number[];
  month: number[];
  dow: number[];
};

function parseCronField(field: string, min: number, max: number): number[] {
  if (field === '*') return Array.from({ length: max - min + 1 }, (_, i) => i + min);
  const values = new Set<number>();
  for (const part of field.split(',')) {
    if (part.includes('/')) {
      const [rangeStr, stepStr] = part.split('/');
      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step <= 0) throw new Error(`Invalid step: ${stepStr}`);
      let start = min, end = max;
      if (rangeStr !== '*') {
        if (rangeStr.includes('-')) {
          const [s, e] = rangeStr.split('-');
          start = parseInt(s, 10); end = parseInt(e, 10);
        } else {
          start = parseInt(rangeStr, 10);
        }
      }
      for (let i = start; i <= end; i += step) if (i >= min && i <= max) values.add(i);
    } else if (part.includes('-')) {
      const [s, e] = part.split('-');
      for (let i = parseInt(s, 10); i <= parseInt(e, 10); i++) if (i >= min && i <= max) values.add(i);
    } else {
      const val = parseInt(part, 10);
      if (!isNaN(val) && val >= min && val <= max) values.add(val);
    }
  }
  return Array.from(values).sort((a, b) => a - b);
}

function parseCron(expr: string): CronFields | null {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  try {
    return {
      minute: parseCronField(parts[0], 0, 59),
      hour:   parseCronField(parts[1], 0, 23),
      dom:    parseCronField(parts[2], 1, 31),
      month:  parseCronField(parts[3], 1, 12),
      dow:    parseCronField(parts[4], 0, 6),
    };
  } catch { return null; }
}

function cronMatchesNow(cron: CronFields, date: Date): boolean {
  return (
    cron.minute.includes(date.getMinutes()) &&
    cron.hour.includes(date.getHours()) &&
    cron.dom.includes(date.getDate()) &&
    cron.month.includes(date.getMonth() + 1) &&
    cron.dow.includes(date.getDay())
  );
}

function minuteKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${date.getHours()}-${date.getMinutes()}`;
}

// ============================================================
// Core: collect files matching a rule
// ============================================================

function collectFiles(app: App, rule: DeleteRule): TFile[] {
  const folder = app.vault.getAbstractFileByPath(rule.folder);
  if (!(folder instanceof TFolder)) return [];

  const now = Date.now();
  const ageMs = rule.olderThanDays > 0 ? rule.olderThanDays * 86_400_000 : 0;

  const results: TFile[] = [];

  const walk = (f: TFolder) => {
    for (const child of f.children) {
      if (child instanceof TFolder) {
        if (rule.recursive) walk(child);
      } else if (child instanceof TFile) {
        if (!matchesPattern(child.name, rule.filePattern)) continue;
        if (!matchesExtension(child.name, rule.extensionFilter)) continue;

        if (ageMs > 0) {
          const ts = rule.ageBase === 'modified' ? child.stat.mtime : child.stat.ctime;
          if (now - ts < ageMs) continue;
        }

        if (rule.minSizeKB > 0 && child.stat.size < rule.minSizeKB * 1024) continue;

        results.push(child);
      }
    }
  };

  walk(folder);
  return results;
}

// ============================================================
// Modal: rule editor
// ============================================================

class DeleteRuleModal extends Modal {
  private rule: DeleteRule;
  private readonly isNew: boolean;
  private readonly onSave: (rule: DeleteRule) => void;

  constructor(app: App, rule: DeleteRule | null, onSave: (rule: DeleteRule) => void) {
    super(app);
    this.isNew = rule === null;
    this.rule = rule
      ? { ...rule }
      : { id: crypto.randomUUID(), name: '', ...DEFAULT_RULE };
    this.onSave = onSave;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: this.isNew ? 'ルールを追加' : 'ルールを編集' });

    // Name
    new Setting(contentEl)
      .setName('ルール名')
      .setDesc('このルールの識別名')
      .addText((t) => t.setValue(this.rule.name).onChange((v) => (this.rule.name = v.trim())));

    contentEl.createEl('h4', { text: '削除対象', attr: { style: 'margin-top:16px;' } });

    // Folder
    new Setting(contentEl)
      .setName('対象フォルダ')
      .setDesc('Vault 内のフォルダパス (例: Daily Notes)')
      .addText((t) =>
        t.setPlaceholder('Daily Notes').setValue(this.rule.folder).onChange((v) => (this.rule.folder = v.trim()))
      );

    // Recursive
    new Setting(contentEl)
      .setName('サブフォルダを含む')
      .setDesc('ON にするとサブフォルダ内のファイルも対象にします')
      .addToggle((t) => t.setValue(this.rule.recursive).onChange((v) => (this.rule.recursive = v)));

    contentEl.createEl('h4', { text: '条件', attr: { style: 'margin-top:16px;' } });

    // Age
    new Setting(contentEl)
      .setName('経過日数 (日)')
      .setDesc('指定日数より古いファイルを削除します。0 で無効')
      .addText((t) =>
        t
          .setPlaceholder('30')
          .setValue(String(this.rule.olderThanDays))
          .onChange((v) => {
            const n = parseInt(v, 10);
            this.rule.olderThanDays = isNaN(n) || n < 0 ? 0 : n;
          })
      );

    // Age base
    new Setting(contentEl)
      .setName('日数の基準')
      .setDesc('作成日時と更新日時のどちらを基準にするか')
      .addDropdown((dd) =>
        dd
          .addOption('created', '作成日時')
          .addOption('modified', '更新日時')
          .setValue(this.rule.ageBase)
          .onChange((v) => (this.rule.ageBase = v as AgeBase))
      );

    // File pattern
    new Setting(contentEl)
      .setName('ファイル名パターン')
      .setDesc('glob パターン (例: *.md, report-*). 空欄ですべてのファイル')
      .addText((t) =>
        t.setPlaceholder('*.md').setValue(this.rule.filePattern).onChange((v) => (this.rule.filePattern = v.trim()))
      );

    // Extension filter
    new Setting(contentEl)
      .setName('拡張子フィルタ')
      .setDesc('カンマ区切りで拡張子を指定 (例: md,png,jpg). 空欄ですべての拡張子')
      .addText((t) =>
        t
          .setPlaceholder('md,png')
          .setValue(this.rule.extensionFilter)
          .onChange((v) => (this.rule.extensionFilter = v.trim()))
      );

    // Min size
    new Setting(contentEl)
      .setName('最小ファイルサイズ (KB)')
      .setDesc('指定サイズ以上のファイルのみ対象。0 で無効')
      .addText((t) =>
        t
          .setPlaceholder('0')
          .setValue(String(this.rule.minSizeKB))
          .onChange((v) => {
            const n = parseInt(v, 10);
            this.rule.minSizeKB = isNaN(n) || n < 0 ? 0 : n;
          })
      );

    contentEl.createEl('h4', { text: '削除方法', attr: { style: 'margin-top:16px;' } });

    // Delete mode
    new Setting(contentEl)
      .setName('削除方法')
      .setDesc('「ゴミ箱」はシステムのゴミ箱へ移動、「完全削除」は復元できません')
      .addDropdown((dd) =>
        dd
          .addOption('trash', 'ゴミ箱へ移動')
          .addOption('permanent', '完全削除')
          .setValue(this.rule.deleteMode)
          .onChange((v) => (this.rule.deleteMode = v as DeleteMode))
      );

    contentEl.createEl('h4', { text: 'スケジュール', attr: { style: 'margin-top:16px;' } });

    // Run on startup
    new Setting(contentEl)
      .setName('起動時に実行')
      .setDesc('Obsidian 起動時にこのルールを実行します')
      .addToggle((t) => t.setValue(this.rule.runOnStartup).onChange((v) => (this.rule.runOnStartup = v)));

    // Interval
    new Setting(contentEl)
      .setName('実行間隔 (時間)')
      .setDesc('指定時間ごとに定期実行します。0 で無効')
      .addText((t) =>
        t
          .setPlaceholder('0')
          .setValue(String(this.rule.intervalHours))
          .onChange((v) => {
            const n = parseFloat(v);
            this.rule.intervalHours = isNaN(n) || n < 0 ? 0 : n;
          })
      );

    // Cron
    new Setting(contentEl)
      .setName('cron 式 (任意)')
      .setDesc('分 時 日 月 曜日 の形式 (例: 0 3 * * * = 毎朝3時). 空欄で無効')
      .addText((t) =>
        t
          .setPlaceholder('0 3 * * *')
          .setValue(this.rule.cronExpression)
          .onChange((v) => (this.rule.cronExpression = v.trim()))
      );

    // Buttons
    const btnRow = contentEl.createDiv({
      attr: { style: 'display:flex; justify-content:flex-end; gap:8px; margin-top:24px;' },
    });
    btnRow.createEl('button', { text: 'キャンセル' }).addEventListener('click', () => this.close());

    const saveBtn = btnRow.createEl('button', { text: '保存', cls: 'mod-cta' });
    saveBtn.addEventListener('click', () => {
      if (!this.rule.name) { new Notice('ルール名を入力してください'); return; }
      if (!this.rule.folder) { new Notice('対象フォルダを入力してください'); return; }
      if (this.rule.olderThanDays === 0 && !this.rule.filePattern && !this.rule.extensionFilter && this.rule.minSizeKB === 0) {
        new Notice('条件を少なくとも1つ設定してください（経過日数、ファイル名パターン、拡張子、またはサイズ）'); return;
      }
      if (!this.rule.runOnStartup && this.rule.intervalHours === 0 && !this.rule.cronExpression) {
        new Notice('スケジュールを少なくとも1つ設定してください'); return;
      }
      if (this.rule.cronExpression && !parseCron(this.rule.cronExpression)) {
        new Notice('cron 式が無効です (例: 0 3 * * *)'); return;
      }
      this.onSave(this.rule);
      this.close();
    });
  }

  onClose(): void { this.contentEl.empty(); }
}

// ============================================================
// Modal: dry-run preview
// ============================================================

class PreviewModal extends Modal {
  private readonly results: Array<{ ruleName: string; files: TFile[] }>;

  constructor(app: App, results: Array<{ ruleName: string; files: TFile[] }>) {
    super(app);
    this.results = results;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: '削除プレビュー (ドライラン)' });

    const total = this.results.reduce((s, r) => s + r.files.length, 0);
    contentEl.createEl('p', {
      text: `合計 ${total} 件のファイルが削除対象です。`,
      attr: { style: 'color:var(--text-muted);' },
    });

    if (total === 0) {
      contentEl.createEl('p', { text: '削除対象のファイルはありません。' });
    }

    for (const { ruleName, files } of this.results) {
      if (files.length === 0) continue;
      contentEl.createEl('h4', { text: `${ruleName} (${files.length} 件)` });
      const ul = contentEl.createEl('ul', { attr: { style: 'font-size:0.85em; max-height:200px; overflow-y:auto;' } });
      for (const f of files) ul.createEl('li', { text: f.path });
    }

    const btnRow = contentEl.createDiv({ attr: { style: 'display:flex; justify-content:flex-end; margin-top:16px;' } });
    btnRow.createEl('button', { text: '閉じる', cls: 'mod-cta' }).addEventListener('click', () => this.close());
  }

  onClose(): void { this.contentEl.empty(); }
}

// ============================================================
// Settings tab
// ============================================================

class TimedDeleteSettingTab extends PluginSettingTab {
  plugin: TimedDeletePlugin;

  constructor(app: App, plugin: TimedDeletePlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl('h2', { text: 'Timed Delete' });
    containerEl.createEl('p', {
      text: '指定したフォルダのファイルを条件に従って自動削除します。複数のルールを設定できます。',
      attr: { style: 'color:var(--text-muted); margin-bottom:8px;' },
    });

    // Global: dry-run
    new Setting(containerEl)
      .setName('ドライラン (安全確認)')
      .setDesc('ON にすると実際には削除せず、削除対象のプレビューのみ表示します')
      .addToggle((t) =>
        t.setValue(this.plugin.settings.dryRun).onChange(async (v) => {
          this.plugin.settings.dryRun = v;
          await this.plugin.saveSettings();
        })
      );

    containerEl.createEl('hr');

    // Add rule button
    new Setting(containerEl).setName('削除ルール').addButton((btn) =>
      btn
        .setButtonText('+ ルールを追加')
        .setCta()
        .onClick(() => {
          new DeleteRuleModal(this.app, null, async (rule) => {
            this.plugin.settings.rules.push(rule);
            await this.plugin.saveSettings();
            this.display();
          }).open();
        })
    );

    if (this.plugin.settings.rules.length === 0) {
      containerEl.createEl('p', {
        text: 'ルールがありません。「+ ルールを追加」ボタンで追加してください。',
        attr: { style: 'color:var(--text-muted);' },
      });
    }

    for (const rule of this.plugin.settings.rules) {
      const desc = this.buildDesc(rule);
      const s = new Setting(containerEl)
        .setName(rule.name || '(名前なし)')
        .setDesc(desc);

      s.addToggle((t) =>
        t.setValue(rule.enabled).onChange(async (v) => {
          rule.enabled = v;
          await this.plugin.saveSettings();
        })
      );

      s.addButton((btn) =>
        btn
          .setIcon('play')
          .setTooltip('今すぐ実行')
          .onClick(async () => {
            await this.plugin.executeRule(rule);
          })
      );

      s.addButton((btn) =>
        btn
          .setIcon('pencil')
          .setTooltip('編集')
          .onClick(() => {
            new DeleteRuleModal(this.app, rule, async (updated) => {
              const idx = this.plugin.settings.rules.findIndex((r) => r.id === updated.id);
              if (idx >= 0) this.plugin.settings.rules[idx] = updated;
              await this.plugin.saveSettings();
              this.display();
            }).open();
          })
      );

      s.addButton((btn) =>
        btn
          .setIcon('trash')
          .setTooltip('ルールを削除')
          .setWarning()
          .onClick(async () => {
            this.plugin.settings.rules = this.plugin.settings.rules.filter((r) => r.id !== rule.id);
            await this.plugin.saveSettings();
            this.display();
          })
      );
    }
  }

  private buildDesc(rule: DeleteRule): string {
    const parts: string[] = [];
    parts.push(`フォルダ: ${rule.folder || '(未設定)'}${rule.recursive ? ' (再帰)' : ''}`);
    const conds: string[] = [];
    if (rule.olderThanDays > 0)
      conds.push(`${rule.olderThanDays}日以上 (${rule.ageBase === 'created' ? '作成' : '更新'})`);
    if (rule.filePattern) conds.push(`名前: ${rule.filePattern}`);
    if (rule.extensionFilter) conds.push(`拡張子: ${rule.extensionFilter}`);
    if (rule.minSizeKB > 0) conds.push(`${rule.minSizeKB}KB以上`);
    if (conds.length > 0) parts.push(`条件: ${conds.join(', ')}`);
    parts.push(`削除: ${rule.deleteMode === 'trash' ? 'ゴミ箱' : '完全削除'}`);
    const sched: string[] = [];
    if (rule.runOnStartup) sched.push('起動時');
    if (rule.intervalHours > 0) sched.push(`${rule.intervalHours}時間ごと`);
    if (rule.cronExpression) sched.push(`cron: ${rule.cronExpression}`);
    if (sched.length > 0) parts.push(`スケジュール: ${sched.join(', ')}`);
    return parts.join('  |  ');
  }
}

// ============================================================
// Plugin
// ============================================================

export default class TimedDeletePlugin extends Plugin {
  settings: PluginSettings = DEFAULT_SETTINGS;
  private statusBarEl: HTMLElement | null = null;

  async onload() {
    await this.loadSettings();

    this.statusBarEl = this.addStatusBarItem();
    this.updateStatusBar();

    // Ribbon icon: run all enabled rules now
    this.addRibbonIcon('clock', 'Timed Delete: 今すぐ実行', async () => {
      await this.runAllRules();
    });

    // Commands
    this.addCommand({
      id: 'run-all-rules',
      name: '有効なルールをすべて今すぐ実行',
      callback: async () => { await this.runAllRules(); },
    });

    this.addCommand({
      id: 'preview-deletions',
      name: '削除対象をプレビュー (ドライラン)',
      callback: () => { this.showPreview(); },
    });

    // Run on startup
    this.app.workspace.onLayoutReady(async () => {
      for (const rule of this.settings.rules) {
        if (rule.enabled && rule.runOnStartup) {
          await this.executeRule(rule);
        }
      }
      this.updateStatusBar();
    });

    // Interval / cron checker (every minute)
    this.registerInterval(window.setInterval(() => this.tickSchedules(), 60_000));

    this.addSettingTab(new TimedDeleteSettingTab(this.app, this));
  }

  // ----------------------------------------------------------

  private async tickSchedules(): Promise<void> {
    const now = new Date();
    const key = minuteKey(now);
    let dirty = false;

    for (const rule of this.settings.rules) {
      if (!rule.enabled) continue;

      // Interval-based
      if (rule.intervalHours > 0) {
        const intervalMs = rule.intervalHours * 3_600_000;
        if (rule.lastRunTimestamp > 0 && now.getTime() - rule.lastRunTimestamp >= intervalMs) {
          await this.executeRule(rule, false);
          dirty = true;
        }
      }

      // Cron-based
      if (rule.cronExpression && rule.lastRunMinute !== key) {
        const cron = parseCron(rule.cronExpression);
        if (cron && cronMatchesNow(cron, now)) {
          rule.lastRunMinute = key;
          await this.executeRule(rule, false);
          dirty = true;
        }
      }
    }

    if (dirty) await this.saveSettings();
    this.updateStatusBar();
  }

  async executeRule(rule: DeleteRule, updateState = true): Promise<void> {
    const files = collectFiles(this.app, rule);
    if (files.length === 0) {
      new Notice(`Timed Delete [${rule.name}]: 削除対象のファイルはありませんでした`);
      return;
    }

    if (this.settings.dryRun) {
      new PreviewModal(this.app, [{ ruleName: rule.name, files }]).open();
      return;
    }

    let deleted = 0;
    for (const file of files) {
      try {
        if (rule.deleteMode === 'permanent') {
          await this.app.vault.delete(file, true);
        } else {
          await this.app.vault.trash(file, true);
        }
        deleted++;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        new Notice(`Timed Delete: ${file.path} の削除に失敗しました\n${msg}`, 6000);
      }
    }

    new Notice(`Timed Delete [${rule.name}]: ${deleted} 件のファイルを削除しました`);

    if (updateState) {
      rule.lastRunTimestamp = Date.now();
      await this.saveSettings();
    }
  }

  private async runAllRules(): Promise<void> {
    const enabled = this.settings.rules.filter((r) => r.enabled);
    if (enabled.length === 0) {
      new Notice('Timed Delete: 有効なルールがありません');
      return;
    }
    for (const rule of enabled) await this.executeRule(rule);
    this.updateStatusBar();
  }

  private showPreview(): void {
    const results = this.settings.rules
      .filter((r) => r.enabled)
      .map((r) => ({ ruleName: r.name, files: collectFiles(this.app, r) }));
    new PreviewModal(this.app, results).open();
  }

  private updateStatusBar(): void {
    if (!this.statusBarEl) return;
    const total = this.settings.rules
      .filter((r) => r.enabled)
      .reduce((s, r) => s + collectFiles(this.app, r).length, 0);

    if (this.settings.dryRun) {
      this.statusBarEl.setText(`🕐 Timed Delete: ドライラン (${total} 件対象)`);
    } else if (total > 0) {
      this.statusBarEl.setText(`🕐 Timed Delete: ${total} 件対象`);
    } else {
      this.statusBarEl.setText('');
    }
  }

  async loadSettings() {
    const loaded = await this.loadData();
    this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded);
    if (!Array.isArray(this.settings.rules)) this.settings.rules = [];
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }
}
