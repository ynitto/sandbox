import {
  App,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TAbstractFile,
  TFile,
} from 'obsidian';
import * as fs from 'fs';
import * as nodePath from 'path';

// ============================================================
// Types
// ============================================================

interface FileWatchRule {
  id: string;
  name: string;
  pathPattern: string;
  events: ('create' | 'modify')[];
  commandId: string;
  enabled: boolean;
  activateFile: boolean; // コマンド実行前にトリガーファイルをアクティブにする
}

interface ScheduleRule {
  id: string;
  name: string;
  schedule: string; // cron expression: "分 時 日 月 曜日"
  commandId: string;
  enabled: boolean;
  filePath?: string;    // 実行前に開くファイル (省略可)
  lastRunMinute?: string; // "YYYY-M-D-H-MM" で同分内の二重実行を防ぐ
}

interface AbsolutePathCopyRule {
  id: string;
  name: string;
  enabled: boolean;
  sourcePath: string;   // Obsidian外のファイル絶対パス
  destFolder: string;   // Vault内のコピー先フォルダ（相対パス、空欄でルート）
  triggerType: 'event' | 'schedule';
  watchEvents: ('create' | 'modify')[]; // triggerType === 'event' の場合
  schedule: string;     // triggerType === 'schedule' の場合 (cron式)
  lastRunMinute?: string; // スケジュール実行の同分内二重実行防止
}

interface PluginSettings {
  fileWatchRules: FileWatchRule[];
  scheduleRules: ScheduleRule[];
  absolutePathCopyRules: AbsolutePathCopyRule[];
}

const DEFAULT_SETTINGS: PluginSettings = {
  fileWatchRules: [],
  scheduleRules: [],
  absolutePathCopyRules: [],
};

// ============================================================
// Glob Matcher
// ============================================================

function matchesGlob(filePath: string, pattern: string): boolean {
  // パターンがフォルダパス（末尾 /）の場合はプレフィックスマッチ
  if (pattern.endsWith('/')) {
    return filePath.startsWith(pattern);
  }

  const regexStr = pattern
    // 正規表現の特殊文字をエスケープ（*、?、/ は除く）
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    // ** は任意の深さのパスにマッチ
    .replace(/\*\*\//g, '(.*\\/)?')
    .replace(/\*\*/g, '.*')
    // * はスラッシュ以外の任意文字列
    .replace(/\*/g, '[^/]*')
    // ? は1文字
    .replace(/\?/g, '[^/]');

  return new RegExp(`^${regexStr}$`).test(filePath);
}

// ============================================================
// Cron Parser
// ============================================================

type CronFields = {
  minute: number[];
  hour: number[];
  dom: number[];   // day of month
  month: number[];
  dow: number[];   // day of week
};

function parseCronField(field: string, min: number, max: number): number[] {
  if (field === '*') {
    return Array.from({ length: max - min + 1 }, (_, i) => i + min);
  }

  const values = new Set<number>();

  for (const part of field.split(',')) {
    if (part.includes('/')) {
      const [rangeStr, stepStr] = part.split('/');
      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step <= 0) throw new Error(`Invalid step: ${stepStr}`);

      let start = min;
      let end = max;

      if (rangeStr !== '*') {
        if (rangeStr.includes('-')) {
          const [s, e] = rangeStr.split('-');
          start = parseInt(s, 10);
          end = parseInt(e, 10);
        } else {
          start = parseInt(rangeStr, 10);
        }
      }

      for (let i = start; i <= end; i += step) {
        if (i >= min && i <= max) values.add(i);
      }
    } else if (part.includes('-')) {
      const [s, e] = part.split('-');
      const start = parseInt(s, 10);
      const end = parseInt(e, 10);
      for (let i = start; i <= end; i++) {
        if (i >= min && i <= max) values.add(i);
      }
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
  } catch {
    return null;
  }
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

// ============================================================
// Helper
// ============================================================

function getCommands(app: App): Array<{ id: string; name: string }> {
  const cmds = (app as any).commands?.commands ?? {};
  return (Object.values(cmds) as any[])
    .map((c) => ({ id: c.id as string, name: c.name as string }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function minuteKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${date.getHours()}-${date.getMinutes()}`;
}

// ============================================================
// Modal: FileWatchRule
// ============================================================

class FileWatchRuleModal extends Modal {
  private rule: FileWatchRule;
  private readonly isNew: boolean;
  private readonly onSave: (rule: FileWatchRule) => void;

  constructor(app: App, rule: FileWatchRule | null, onSave: (rule: FileWatchRule) => void) {
    super(app);
    this.isNew = rule === null;
    this.rule = rule
      ? { ...rule, events: [...rule.events] }
      : {
          id: crypto.randomUUID(),
          name: '',
          pathPattern: '',
          events: ['create', 'modify'],
          commandId: '',
          enabled: true,
          activateFile: false,
        };
    this.onSave = onSave;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', {
      text: this.isNew ? 'ファイル監視ルールを追加' : 'ファイル監視ルールを編集',
    });

    new Setting(contentEl)
      .setName('名前')
      .setDesc('このルールの識別名')
      .addText((t) => t.setValue(this.rule.name).onChange((v) => (this.rule.name = v.trim())));

    new Setting(contentEl)
      .setName('パスパターン')
      .setDesc('glob パターン (例: notes/**/*.md, daily/*.md) またはフォルダパス (例: attachments/)')
      .addText((t) =>
        t
          .setPlaceholder('notes/**/*.md')
          .setValue(this.rule.pathPattern)
          .onChange((v) => (this.rule.pathPattern = v.trim()))
      );

    // イベント チェックボックス
    const evtSetting = new Setting(contentEl).setName('監視イベント');
    const cbWrap = evtSetting.controlEl.createDiv({ attr: { style: 'display:flex; gap:16px;' } });

    for (const evt of ['create', 'modify'] as const) {
      const label = cbWrap.createEl('label', {
        attr: { style: 'display:flex; align-items:center; gap:4px; cursor:pointer;' },
      });
      const cb = label.createEl('input', { type: 'checkbox' });
      cb.checked = this.rule.events.includes(evt);
      cb.addEventListener('change', () => {
        if (cb.checked) {
          if (!this.rule.events.includes(evt)) this.rule.events.push(evt);
        } else {
          this.rule.events = this.rule.events.filter((e) => e !== evt);
        }
      });
      label.createSpan({ text: evt === 'create' ? '作成' : '変更' });
    }

    // コマンド
    const commands = getCommands(this.app);
    new Setting(contentEl)
      .setName('実行コマンド')
      .setDesc('ファイルイベント発生時に実行する Obsidian コマンド')
      .addDropdown((dd) => {
        dd.addOption('', '-- コマンドを選択 --');
        for (const cmd of commands) dd.addOption(cmd.id, cmd.name);
        dd.setValue(this.rule.commandId).onChange((v) => (this.rule.commandId = v));
      });

    // ファイルをアクティブにする
    new Setting(contentEl)
      .setName('ファイルをアクティブにして実行')
      .setDesc('ON にするとコマンド実行前にトリガーとなったファイルを開いてアクティブにします')
      .addToggle((t) =>
        t.setValue(this.rule.activateFile).onChange((v) => (this.rule.activateFile = v))
      );

    // ボタン
    const btnRow = contentEl.createDiv({
      attr: { style: 'display:flex; justify-content:flex-end; gap:8px; margin-top:16px;' },
    });
    btnRow.createEl('button', { text: 'キャンセル' }).addEventListener('click', () => this.close());

    const saveBtn = btnRow.createEl('button', { text: '保存', cls: 'mod-cta' });
    saveBtn.addEventListener('click', () => {
      if (!this.rule.name) return new Notice('名前を入力してください');
      if (!this.rule.pathPattern) return new Notice('パスパターンを入力してください');
      if (!this.rule.commandId) return new Notice('コマンドを選択してください');
      if (this.rule.events.length === 0) return new Notice('イベントを1つ以上選択してください');
      this.onSave(this.rule);
      this.close();
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ============================================================
// Modal: ScheduleRule
// ============================================================

class ScheduleRuleModal extends Modal {
  private rule: ScheduleRule;
  private readonly isNew: boolean;
  private readonly onSave: (rule: ScheduleRule) => void;

  constructor(app: App, rule: ScheduleRule | null, onSave: (rule: ScheduleRule) => void) {
    super(app);
    this.isNew = rule === null;
    this.rule = rule
      ? { ...rule }
      : {
          id: crypto.randomUUID(),
          name: '',
          schedule: '0 9 * * *',
          commandId: '',
          enabled: true,
        };
    this.onSave = onSave;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', {
      text: this.isNew ? 'スケジュールルールを追加' : 'スケジュールルールを編集',
    });

    new Setting(contentEl)
      .setName('名前')
      .setDesc('このルールの識別名')
      .addText((t) => t.setValue(this.rule.name).onChange((v) => (this.rule.name = v.trim())));

    new Setting(contentEl)
      .setName('スケジュール (cron 式)')
      .setDesc('形式: 分 時 日 月 曜日 (0=日曜)  例: 0 9 * * * = 毎朝9時, */5 * * * * = 5分ごと')
      .addText((t) =>
        t
          .setPlaceholder('0 9 * * *')
          .setValue(this.rule.schedule)
          .onChange((v) => (this.rule.schedule = v.trim()))
      );

    new Setting(contentEl)
      .setName('対象ファイル (省略可)')
      .setDesc('指定するとコマンド実行前にそのファイルを開きアクティブにします (Vault 内の相対パス)')
      .addText((t) =>
        t
          .setPlaceholder('notes/target.md')
          .setValue(this.rule.filePath ?? '')
          .onChange((v) => {
            const trimmed = v.trim();
            this.rule.filePath = trimmed || undefined;
          })
      );

    const commands = getCommands(this.app);
    new Setting(contentEl)
      .setName('実行コマンド')
      .setDesc('スケジュール実行時に呼び出す Obsidian コマンド')
      .addDropdown((dd) => {
        dd.addOption('', '-- コマンドを選択 --');
        for (const cmd of commands) dd.addOption(cmd.id, cmd.name);
        dd.setValue(this.rule.commandId).onChange((v) => (this.rule.commandId = v));
      });

    const btnRow = contentEl.createDiv({
      attr: { style: 'display:flex; justify-content:flex-end; gap:8px; margin-top:16px;' },
    });
    btnRow.createEl('button', { text: 'キャンセル' }).addEventListener('click', () => this.close());

    const saveBtn = btnRow.createEl('button', { text: '保存', cls: 'mod-cta' });
    saveBtn.addEventListener('click', () => {
      if (!this.rule.name) return new Notice('名前を入力してください');
      if (!parseCron(this.rule.schedule))
        return new Notice('有効な cron 式を入力してください (例: 0 9 * * *)');
      if (!this.rule.commandId) return new Notice('コマンドを選択してください');
      this.onSave(this.rule);
      this.close();
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ============================================================
// Modal: AbsolutePathCopyRule
// ============================================================

class AbsolutePathCopyRuleModal extends Modal {
  private rule: AbsolutePathCopyRule;
  private readonly isNew: boolean;
  private readonly onSave: (rule: AbsolutePathCopyRule) => void;

  constructor(
    app: App,
    rule: AbsolutePathCopyRule | null,
    onSave: (rule: AbsolutePathCopyRule) => void
  ) {
    super(app);
    this.isNew = rule === null;
    this.rule = rule
      ? { ...rule, watchEvents: [...rule.watchEvents] }
      : {
          id: crypto.randomUUID(),
          name: '',
          enabled: true,
          sourcePath: '',
          destFolder: '',
          triggerType: 'event',
          watchEvents: ['create', 'modify'],
          schedule: '0 9 * * *',
        };
    this.onSave = onSave;
  }

  onOpen(): void {
    this.render();
  }

  private render(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', {
      text: this.isNew ? '絶対パスコピールールを追加' : '絶対パスコピールールを編集',
    });

    new Setting(contentEl)
      .setName('名前')
      .setDesc('このルールの識別名')
      .addText((t) => t.setValue(this.rule.name).onChange((v) => (this.rule.name = v.trim())));

    new Setting(contentEl)
      .setName('コピー元 (絶対パス)')
      .setDesc('Obsidian 外のファイルの絶対パス (例: /home/user/docs/note.md, C:\\Users\\user\\docs\\note.md)')
      .addText((t) =>
        t
          .setPlaceholder('/home/user/docs/note.md')
          .setValue(this.rule.sourcePath)
          .onChange((v) => (this.rule.sourcePath = v.trim()))
      );

    new Setting(contentEl)
      .setName('コピー先フォルダ (Vault 内)')
      .setDesc('Vault 内のフォルダパス (例: inbox)。空欄の場合は Vault ルートにコピーします。')
      .addText((t) =>
        t
          .setPlaceholder('inbox')
          .setValue(this.rule.destFolder)
          .onChange((v) => (this.rule.destFolder = v.trim()))
      );

    new Setting(contentEl)
      .setName('トリガー種別')
      .setDesc('ファイルイベント: ファイル変更時にコピー / cronスケジュール: 指定時刻にコピー')
      .addDropdown((dd) => {
        dd.addOption('event', 'ファイルイベント');
        dd.addOption('schedule', 'cronスケジュール');
        dd.setValue(this.rule.triggerType);
        dd.onChange((v) => {
          this.rule.triggerType = v as 'event' | 'schedule';
          this.render();
        });
      });

    if (this.rule.triggerType === 'event') {
      const evtSetting = new Setting(contentEl)
        .setName('監視イベント')
        .setDesc('コピーをトリガーするファイルイベントを選択してください');
      const cbWrap = evtSetting.controlEl.createDiv({ attr: { style: 'display:flex; gap:16px;' } });

      for (const evt of ['create', 'modify'] as const) {
        const label = cbWrap.createEl('label', {
          attr: { style: 'display:flex; align-items:center; gap:4px; cursor:pointer;' },
        });
        const cb = label.createEl('input', { type: 'checkbox' });
        cb.checked = this.rule.watchEvents.includes(evt);
        cb.addEventListener('change', () => {
          if (cb.checked) {
            if (!this.rule.watchEvents.includes(evt)) this.rule.watchEvents.push(evt);
          } else {
            this.rule.watchEvents = this.rule.watchEvents.filter((e) => e !== evt);
          }
        });
        label.createSpan({ text: evt === 'create' ? '作成' : '変更' });
      }
    } else {
      new Setting(contentEl)
        .setName('スケジュール (cron 式)')
        .setDesc('形式: 分 時 日 月 曜日 (0=日曜)  例: 0 9 * * * = 毎朝9時, */5 * * * * = 5分ごと')
        .addText((t) =>
          t
            .setPlaceholder('0 9 * * *')
            .setValue(this.rule.schedule)
            .onChange((v) => (this.rule.schedule = v.trim()))
        );
    }

    const btnRow = contentEl.createDiv({
      attr: { style: 'display:flex; justify-content:flex-end; gap:8px; margin-top:16px;' },
    });
    btnRow.createEl('button', { text: 'キャンセル' }).addEventListener('click', () => this.close());

    const saveBtn = btnRow.createEl('button', { text: '保存', cls: 'mod-cta' });
    saveBtn.addEventListener('click', () => {
      if (!this.rule.name) return new Notice('名前を入力してください');
      if (!this.rule.sourcePath) return new Notice('コピー元パスを入力してください');
      if (this.rule.triggerType === 'event' && this.rule.watchEvents.length === 0)
        return new Notice('イベントを1つ以上選択してください');
      if (this.rule.triggerType === 'schedule' && !parseCron(this.rule.schedule))
        return new Notice('有効な cron 式を入力してください (例: 0 9 * * *)');
      this.onSave(this.rule);
      this.close();
    });
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

// ============================================================
// Settings Tab
// ============================================================

class FileWatcherSettingTab extends PluginSettingTab {
  plugin: FileWatcherPlugin;

  constructor(app: App, plugin: FileWatcherPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    // ---- ファイル監視ルール ----
    containerEl.createEl('h2', { text: 'ファイル監視ルール' });
    containerEl.createEl('p', {
      text: 'ファイルが作成・変更された時に Obsidian コマンドを自動実行します。',
      attr: { style: 'color:var(--text-muted); margin-bottom:8px;' },
    });

    new Setting(containerEl).setName('ルールを追加').addButton((btn) =>
      btn
        .setButtonText('+ 追加')
        .setCta()
        .onClick(() => {
          new FileWatchRuleModal(this.app, null, async (rule) => {
            this.plugin.settings.fileWatchRules.push(rule);
            await this.plugin.saveSettings();
            this.display();
          }).open();
        })
    );

    if (this.plugin.settings.fileWatchRules.length === 0) {
      containerEl.createEl('p', {
        text: 'ルールがありません。「+ 追加」ボタンで追加してください。',
        attr: { style: 'color:var(--text-muted); padding:4px 0;' },
      });
    }

    for (const rule of this.plugin.settings.fileWatchRules) {
      const cmdName = this.commandName(rule.commandId);
      const activateLabel = rule.activateFile ? '  |  ファイルをアクティブ化' : '';
      new Setting(containerEl)
        .setName(rule.name || '(名前なし)')
        .setDesc(
          `パターン: ${rule.pathPattern}  |  イベント: ${rule.events.join(', ')}  |  コマンド: ${cmdName}${activateLabel}`
        )
        .addToggle((tog) =>
          tog.setValue(rule.enabled).onChange(async (v) => {
            rule.enabled = v;
            await this.plugin.saveSettings();
          })
        )
        .addButton((btn) =>
          btn
            .setIcon('pencil')
            .setTooltip('編集')
            .onClick(() => {
              new FileWatchRuleModal(this.app, rule, async (updated) => {
                const idx = this.plugin.settings.fileWatchRules.findIndex(
                  (r) => r.id === updated.id
                );
                if (idx >= 0) this.plugin.settings.fileWatchRules[idx] = updated;
                await this.plugin.saveSettings();
                this.display();
              }).open();
            })
        )
        .addButton((btn) =>
          btn
            .setIcon('trash')
            .setTooltip('削除')
            .setWarning()
            .onClick(async () => {
              this.plugin.settings.fileWatchRules = this.plugin.settings.fileWatchRules.filter(
                (r) => r.id !== rule.id
              );
              await this.plugin.saveSettings();
              this.display();
            })
        );
    }

    // ---- スケジュールルール ----
    containerEl.createEl('h2', {
      text: 'スケジュールルール',
      attr: { style: 'margin-top:32px;' },
    });
    containerEl.createEl('p', {
      text: 'cron 式で指定したスケジュールに従い Obsidian コマンドを実行します。',
      attr: { style: 'color:var(--text-muted); margin-bottom:8px;' },
    });

    new Setting(containerEl).setName('ルールを追加').addButton((btn) =>
      btn
        .setButtonText('+ 追加')
        .setCta()
        .onClick(() => {
          new ScheduleRuleModal(this.app, null, async (rule) => {
            this.plugin.settings.scheduleRules.push(rule);
            await this.plugin.saveSettings();
            this.display();
          }).open();
        })
    );

    if (this.plugin.settings.scheduleRules.length === 0) {
      containerEl.createEl('p', {
        text: 'ルールがありません。「+ 追加」ボタンで追加してください。',
        attr: { style: 'color:var(--text-muted); padding:4px 0;' },
      });
    }

    for (const rule of this.plugin.settings.scheduleRules) {
      const cmdName = this.commandName(rule.commandId);
      const fileLabel = rule.filePath ? `  |  ファイル: ${rule.filePath}` : '';
      new Setting(containerEl)
        .setName(rule.name || '(名前なし)')
        .setDesc(`スケジュール: ${rule.schedule}  |  コマンド: ${cmdName}${fileLabel}`)
        .addToggle((tog) =>
          tog.setValue(rule.enabled).onChange(async (v) => {
            rule.enabled = v;
            await this.plugin.saveSettings();
          })
        )
        .addButton((btn) =>
          btn
            .setIcon('pencil')
            .setTooltip('編集')
            .onClick(() => {
              new ScheduleRuleModal(this.app, rule, async (updated) => {
                const idx = this.plugin.settings.scheduleRules.findIndex(
                  (r) => r.id === updated.id
                );
                if (idx >= 0) this.plugin.settings.scheduleRules[idx] = updated;
                await this.plugin.saveSettings();
                this.display();
              }).open();
            })
        )
        .addButton((btn) =>
          btn
            .setIcon('trash')
            .setTooltip('削除')
            .setWarning()
            .onClick(async () => {
              this.plugin.settings.scheduleRules = this.plugin.settings.scheduleRules.filter(
                (r) => r.id !== rule.id
              );
              await this.plugin.saveSettings();
              this.display();
            })
        );
    }

    // ---- 絶対パスコピールール ----
    containerEl.createEl('h2', {
      text: '絶対パスコピールール',
      attr: { style: 'margin-top:32px;' },
    });
    containerEl.createEl('p', {
      text: 'Obsidian 外の絶対パスで指定したファイルを Vault 内フォルダにコピーします。ファイルイベントまたは cron スケジュールでトリガーできます。',
      attr: { style: 'color:var(--text-muted); margin-bottom:8px;' },
    });

    new Setting(containerEl).setName('ルールを追加').addButton((btn) =>
      btn
        .setButtonText('+ 追加')
        .setCta()
        .onClick(() => {
          new AbsolutePathCopyRuleModal(this.app, null, async (rule) => {
            this.plugin.settings.absolutePathCopyRules.push(rule);
            await this.plugin.saveSettings();
            this.plugin.setupFsWatchers();
            this.display();
          }).open();
        })
    );

    if (this.plugin.settings.absolutePathCopyRules.length === 0) {
      containerEl.createEl('p', {
        text: 'ルールがありません。「+ 追加」ボタンで追加してください。',
        attr: { style: 'color:var(--text-muted); padding:4px 0;' },
      });
    }

    for (const rule of this.plugin.settings.absolutePathCopyRules) {
      const triggerLabel =
        rule.triggerType === 'event'
          ? `イベント: ${rule.watchEvents.join(', ')}`
          : `スケジュール: ${rule.schedule}`;
      const destLabel = rule.destFolder || '(Vault ルート)';
      new Setting(containerEl)
        .setName(rule.name || '(名前なし)')
        .setDesc(
          `コピー元: ${rule.sourcePath}  |  コピー先: ${destLabel}  |  ${triggerLabel}`
        )
        .addToggle((tog) =>
          tog.setValue(rule.enabled).onChange(async (v) => {
            rule.enabled = v;
            await this.plugin.saveSettings();
            this.plugin.setupFsWatchers();
          })
        )
        .addButton((btn) =>
          btn
            .setIcon('pencil')
            .setTooltip('編集')
            .onClick(() => {
              new AbsolutePathCopyRuleModal(this.app, rule, async (updated) => {
                const idx = this.plugin.settings.absolutePathCopyRules.findIndex(
                  (r) => r.id === updated.id
                );
                if (idx >= 0) this.plugin.settings.absolutePathCopyRules[idx] = updated;
                await this.plugin.saveSettings();
                this.plugin.setupFsWatchers();
                this.display();
              }).open();
            })
        )
        .addButton((btn) =>
          btn
            .setIcon('trash')
            .setTooltip('削除')
            .setWarning()
            .onClick(async () => {
              this.plugin.settings.absolutePathCopyRules =
                this.plugin.settings.absolutePathCopyRules.filter((r) => r.id !== rule.id);
              await this.plugin.saveSettings();
              this.plugin.setupFsWatchers();
              this.display();
            })
        );
    }
  }

  private commandName(commandId: string): string {
    const cmd = (this.app as any).commands?.commands?.[commandId];
    return cmd ? (cmd.name as string) : commandId || '(未設定)';
  }
}

// ============================================================
// Plugin
// ============================================================

export default class FileWatcherPlugin extends Plugin {
  settings: PluginSettings = DEFAULT_SETTINGS;
  private fsWatchers: Map<string, fs.FSWatcher> = new Map();

  async onload() {
    await this.loadSettings();

    // ファイル作成イベント
    this.registerEvent(
      this.app.vault.on('create', (file: TAbstractFile) => {
        if (file instanceof TFile) this.handleFileEvent('create', file);
      })
    );

    // ファイル変更イベント
    this.registerEvent(
      this.app.vault.on('modify', (file: TAbstractFile) => {
        if (file instanceof TFile) this.handleFileEvent('modify', file);
      })
    );

    // スケジュールチェッカー（1分ごと）
    this.registerInterval(window.setInterval(() => this.checkSchedules(), 60_000));

    this.addSettingTab(new FileWatcherSettingTab(this.app, this));

    // 絶対パスコピーの fs ウォッチャーを起動
    this.setupFsWatchers();
  }

  onunload(): void {
    this.teardownFsWatchers();
  }

  // ----------------------------------------------------------

  setupFsWatchers(): void {
    this.teardownFsWatchers();

    for (const rule of this.settings.absolutePathCopyRules) {
      if (!rule.enabled || rule.triggerType !== 'event') continue;
      this.startFsWatcher(rule);
    }
  }

  private teardownFsWatchers(): void {
    for (const watcher of this.fsWatchers.values()) {
      try { watcher.close(); } catch { /* ignore */ }
    }
    this.fsWatchers.clear();
  }

  private startFsWatcher(rule: AbsolutePathCopyRule): void {
    try {
      if (!fs.existsSync(rule.sourcePath)) {
        new Notice(
          `File Watcher: コピー元が見つかりません "${rule.name}"\n${rule.sourcePath}`,
          6000
        );
        return;
      }

      const watcher = fs.watch(rule.sourcePath, (eventType) => {
        if (eventType === 'change' && rule.watchEvents.includes('modify')) {
          this.copyFileToVault(rule);
        } else if (eventType === 'rename' && rule.watchEvents.includes('create')) {
          // 'rename' はファイル作成・削除・リネームで発火。存在確認してコピー
          if (fs.existsSync(rule.sourcePath)) {
            this.copyFileToVault(rule);
          }
        }
      });

      watcher.on('error', (err) => {
        new Notice(`File Watcher: 監視エラー "${rule.name}"\n${err.message}`, 8000);
        this.fsWatchers.delete(rule.id);
      });

      this.fsWatchers.set(rule.id, watcher);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`File Watcher: 監視開始エラー "${rule.name}"\n${msg}`, 8000);
    }
  }

  private async copyFileToVault(rule: AbsolutePathCopyRule): Promise<void> {
    try {
      const srcBuf = fs.readFileSync(rule.sourcePath);
      const arrayBuffer = srcBuf.buffer.slice(
        srcBuf.byteOffset,
        srcBuf.byteOffset + srcBuf.byteLength
      ) as ArrayBuffer;

      const fileName = nodePath.basename(rule.sourcePath);
      const destPath = rule.destFolder ? `${rule.destFolder}/${fileName}` : fileName;

      if (rule.destFolder) {
        const folder = this.app.vault.getAbstractFileByPath(rule.destFolder);
        if (!folder) {
          await this.app.vault.createFolder(rule.destFolder);
        }
      }

      const existing = this.app.vault.getAbstractFileByPath(destPath);
      if (existing instanceof TFile) {
        await this.app.vault.modifyBinary(existing, arrayBuffer);
      } else {
        await this.app.vault.createBinary(destPath, arrayBuffer);
      }

      new Notice(`File Watcher: コピー完了 "${rule.name}"\n→ ${destPath}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`File Watcher: コピーエラー "${rule.name}"\n${msg}`, 8000);
    }
  }

  // ----------------------------------------------------------

  private async handleFileEvent(event: 'create' | 'modify', file: TFile): Promise<void> {
    for (const rule of this.settings.fileWatchRules) {
      if (!rule.enabled) continue;
      if (!rule.events.includes(event)) continue;
      if (!matchesGlob(file.path, rule.pathPattern)) continue;

      if (rule.activateFile) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(file);
      }

      this.executeCommand(rule.commandId, `ファイル監視ルール "${rule.name}"`);
    }
  }

  private async checkSchedules(): Promise<void> {
    const now = new Date();
    const key = minuteKey(now);
    let dirty = false;

    for (const rule of this.settings.scheduleRules) {
      if (!rule.enabled) continue;
      if (rule.lastRunMinute === key) continue;

      const cron = parseCron(rule.schedule);
      if (!cron || !cronMatchesNow(cron, now)) continue;

      rule.lastRunMinute = key;
      dirty = true;

      if (rule.filePath) {
        const target = this.app.vault.getAbstractFileByPath(rule.filePath);
        if (target instanceof TFile) {
          const leaf = this.app.workspace.getLeaf(false);
          await leaf.openFile(target);
        } else {
          new Notice(
            `File Watcher: ファイルが見つかりません "${rule.filePath}"\n(スケジュールルール "${rule.name}")`,
            6000
          );
        }
      }

      this.executeCommand(rule.commandId, `スケジュールルール "${rule.name}"`);
    }

    for (const rule of this.settings.absolutePathCopyRules) {
      if (!rule.enabled || rule.triggerType !== 'schedule') continue;
      if (rule.lastRunMinute === key) continue;

      const cron = parseCron(rule.schedule);
      if (!cron || !cronMatchesNow(cron, now)) continue;

      rule.lastRunMinute = key;
      dirty = true;

      await this.copyFileToVault(rule);
    }

    if (dirty) this.saveSettings();
  }

  private executeCommand(commandId: string, source: string): void {
    try {
      const ok = (this.app as any).commands?.executeCommandById(commandId);
      if (!ok) {
        new Notice(`File Watcher: コマンド "${commandId}" の実行に失敗しました\n(${source})`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new Notice(`File Watcher: エラー (${source})\n${msg}`, 8000);
    }
  }

  async loadSettings() {
    const loaded = await this.loadData();
    this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded);
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }
}
