"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// main.ts
var main_exports = {};
__export(main_exports, {
  default: () => TimedDeletePlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var DEFAULT_RULE = {
  enabled: true,
  folder: "",
  recursive: true,
  olderThanDays: 30,
  ageBase: "created",
  filePattern: "",
  extensionFilter: "",
  minSizeKB: 0,
  deleteMode: "trash",
  runOnStartup: true,
  intervalHours: 0,
  cronExpression: "",
  lastRunTimestamp: 0,
  lastRunMinute: ""
};
var DEFAULT_SETTINGS = {
  rules: [],
  dryRun: false
};
function matchesPattern(filename, pattern) {
  if (!pattern)
    return true;
  const regexStr = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*\*/g, ".*").replace(/\*/g, "[^/]*").replace(/\?/g, "[^/]");
  return new RegExp(`^${regexStr}$`, "i").test(filename);
}
function matchesExtension(filename, extFilter) {
  var _a, _b;
  if (!extFilter)
    return true;
  const exts = extFilter.split(",").map((e) => e.trim().replace(/^\./, "").toLowerCase());
  const fileExt = (_b = (_a = filename.split(".").pop()) == null ? void 0 : _a.toLowerCase()) != null ? _b : "";
  return exts.includes(fileExt);
}
function parseCronField(field, min, max) {
  if (field === "*")
    return Array.from({ length: max - min + 1 }, (_, i) => i + min);
  const values = /* @__PURE__ */ new Set();
  for (const part of field.split(",")) {
    if (part.includes("/")) {
      const [rangeStr, stepStr] = part.split("/");
      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step <= 0)
        throw new Error(`Invalid step: ${stepStr}`);
      let start = min, end = max;
      if (rangeStr !== "*") {
        if (rangeStr.includes("-")) {
          const [s, e] = rangeStr.split("-");
          start = parseInt(s, 10);
          end = parseInt(e, 10);
        } else {
          start = parseInt(rangeStr, 10);
        }
      }
      for (let i = start; i <= end; i += step)
        if (i >= min && i <= max)
          values.add(i);
    } else if (part.includes("-")) {
      const [s, e] = part.split("-");
      for (let i = parseInt(s, 10); i <= parseInt(e, 10); i++)
        if (i >= min && i <= max)
          values.add(i);
    } else {
      const val = parseInt(part, 10);
      if (!isNaN(val) && val >= min && val <= max)
        values.add(val);
    }
  }
  return Array.from(values).sort((a, b) => a - b);
}
function parseCron(expr) {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5)
    return null;
  try {
    return {
      minute: parseCronField(parts[0], 0, 59),
      hour: parseCronField(parts[1], 0, 23),
      dom: parseCronField(parts[2], 1, 31),
      month: parseCronField(parts[3], 1, 12),
      dow: parseCronField(parts[4], 0, 6)
    };
  } catch (e) {
    return null;
  }
}
function cronMatchesNow(cron, date) {
  return cron.minute.includes(date.getMinutes()) && cron.hour.includes(date.getHours()) && cron.dom.includes(date.getDate()) && cron.month.includes(date.getMonth() + 1) && cron.dow.includes(date.getDay());
}
function minuteKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${date.getHours()}-${date.getMinutes()}`;
}
function collectFiles(app, rule) {
  const folder = app.vault.getAbstractFileByPath(rule.folder);
  if (!(folder instanceof import_obsidian.TFolder))
    return [];
  const now = Date.now();
  const ageMs = rule.olderThanDays > 0 ? rule.olderThanDays * 864e5 : 0;
  const results = [];
  const walk = (f) => {
    for (const child of f.children) {
      if (child instanceof import_obsidian.TFolder) {
        if (rule.recursive)
          walk(child);
      } else if (child instanceof import_obsidian.TFile) {
        if (!matchesPattern(child.name, rule.filePattern))
          continue;
        if (!matchesExtension(child.name, rule.extensionFilter))
          continue;
        if (ageMs > 0) {
          const ts = rule.ageBase === "modified" ? child.stat.mtime : child.stat.ctime;
          if (now - ts < ageMs)
            continue;
        }
        if (rule.minSizeKB > 0 && child.stat.size < rule.minSizeKB * 1024)
          continue;
        results.push(child);
      }
    }
  };
  walk(folder);
  return results;
}
var DeleteRuleModal = class extends import_obsidian.Modal {
  constructor(app, rule, onSave) {
    super(app);
    this.isNew = rule === null;
    this.rule = rule ? { ...rule } : { id: crypto.randomUUID(), name: "", ...DEFAULT_RULE };
    this.onSave = onSave;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: this.isNew ? "\u30EB\u30FC\u30EB\u3092\u8FFD\u52A0" : "\u30EB\u30FC\u30EB\u3092\u7DE8\u96C6" });
    new import_obsidian.Setting(contentEl).setName("\u30EB\u30FC\u30EB\u540D").setDesc("\u3053\u306E\u30EB\u30FC\u30EB\u306E\u8B58\u5225\u540D").addText((t) => t.setValue(this.rule.name).onChange((v) => this.rule.name = v.trim()));
    contentEl.createEl("h4", { text: "\u524A\u9664\u5BFE\u8C61", attr: { style: "margin-top:16px;" } });
    new import_obsidian.Setting(contentEl).setName("\u5BFE\u8C61\u30D5\u30A9\u30EB\u30C0").setDesc("Vault \u5185\u306E\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9 (\u4F8B: Daily Notes)").addText(
      (t) => t.setPlaceholder("Daily Notes").setValue(this.rule.folder).onChange((v) => this.rule.folder = v.trim())
    );
    new import_obsidian.Setting(contentEl).setName("\u30B5\u30D6\u30D5\u30A9\u30EB\u30C0\u3092\u542B\u3080").setDesc("ON \u306B\u3059\u308B\u3068\u30B5\u30D6\u30D5\u30A9\u30EB\u30C0\u5185\u306E\u30D5\u30A1\u30A4\u30EB\u3082\u5BFE\u8C61\u306B\u3057\u307E\u3059").addToggle((t) => t.setValue(this.rule.recursive).onChange((v) => this.rule.recursive = v));
    contentEl.createEl("h4", { text: "\u6761\u4EF6", attr: { style: "margin-top:16px;" } });
    new import_obsidian.Setting(contentEl).setName("\u7D4C\u904E\u65E5\u6570 (\u65E5)").setDesc("\u6307\u5B9A\u65E5\u6570\u3088\u308A\u53E4\u3044\u30D5\u30A1\u30A4\u30EB\u3092\u524A\u9664\u3057\u307E\u3059\u30020 \u3067\u7121\u52B9").addText(
      (t) => t.setPlaceholder("30").setValue(String(this.rule.olderThanDays)).onChange((v) => {
        const n = parseInt(v, 10);
        this.rule.olderThanDays = isNaN(n) || n < 0 ? 0 : n;
      })
    );
    new import_obsidian.Setting(contentEl).setName("\u65E5\u6570\u306E\u57FA\u6E96").setDesc("\u4F5C\u6210\u65E5\u6642\u3068\u66F4\u65B0\u65E5\u6642\u306E\u3069\u3061\u3089\u3092\u57FA\u6E96\u306B\u3059\u308B\u304B").addDropdown(
      (dd) => dd.addOption("created", "\u4F5C\u6210\u65E5\u6642").addOption("modified", "\u66F4\u65B0\u65E5\u6642").setValue(this.rule.ageBase).onChange((v) => this.rule.ageBase = v)
    );
    new import_obsidian.Setting(contentEl).setName("\u30D5\u30A1\u30A4\u30EB\u540D\u30D1\u30BF\u30FC\u30F3").setDesc("glob \u30D1\u30BF\u30FC\u30F3 (\u4F8B: *.md, report-*). \u7A7A\u6B04\u3067\u3059\u3079\u3066\u306E\u30D5\u30A1\u30A4\u30EB").addText(
      (t) => t.setPlaceholder("*.md").setValue(this.rule.filePattern).onChange((v) => this.rule.filePattern = v.trim())
    );
    new import_obsidian.Setting(contentEl).setName("\u62E1\u5F35\u5B50\u30D5\u30A3\u30EB\u30BF").setDesc("\u30AB\u30F3\u30DE\u533A\u5207\u308A\u3067\u62E1\u5F35\u5B50\u3092\u6307\u5B9A (\u4F8B: md,png,jpg). \u7A7A\u6B04\u3067\u3059\u3079\u3066\u306E\u62E1\u5F35\u5B50").addText(
      (t) => t.setPlaceholder("md,png").setValue(this.rule.extensionFilter).onChange((v) => this.rule.extensionFilter = v.trim())
    );
    new import_obsidian.Setting(contentEl).setName("\u6700\u5C0F\u30D5\u30A1\u30A4\u30EB\u30B5\u30A4\u30BA (KB)").setDesc("\u6307\u5B9A\u30B5\u30A4\u30BA\u4EE5\u4E0A\u306E\u30D5\u30A1\u30A4\u30EB\u306E\u307F\u5BFE\u8C61\u30020 \u3067\u7121\u52B9").addText(
      (t) => t.setPlaceholder("0").setValue(String(this.rule.minSizeKB)).onChange((v) => {
        const n = parseInt(v, 10);
        this.rule.minSizeKB = isNaN(n) || n < 0 ? 0 : n;
      })
    );
    contentEl.createEl("h4", { text: "\u524A\u9664\u65B9\u6CD5", attr: { style: "margin-top:16px;" } });
    new import_obsidian.Setting(contentEl).setName("\u524A\u9664\u65B9\u6CD5").setDesc("\u300C\u30B4\u30DF\u7BB1\u300D\u306F\u30B7\u30B9\u30C6\u30E0\u306E\u30B4\u30DF\u7BB1\u3078\u79FB\u52D5\u3001\u300C\u5B8C\u5168\u524A\u9664\u300D\u306F\u5FA9\u5143\u3067\u304D\u307E\u305B\u3093").addDropdown(
      (dd) => dd.addOption("trash", "\u30B4\u30DF\u7BB1\u3078\u79FB\u52D5").addOption("permanent", "\u5B8C\u5168\u524A\u9664").setValue(this.rule.deleteMode).onChange((v) => this.rule.deleteMode = v)
    );
    contentEl.createEl("h4", { text: "\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB", attr: { style: "margin-top:16px;" } });
    new import_obsidian.Setting(contentEl).setName("\u8D77\u52D5\u6642\u306B\u5B9F\u884C").setDesc("Obsidian \u8D77\u52D5\u6642\u306B\u3053\u306E\u30EB\u30FC\u30EB\u3092\u5B9F\u884C\u3057\u307E\u3059").addToggle((t) => t.setValue(this.rule.runOnStartup).onChange((v) => this.rule.runOnStartup = v));
    new import_obsidian.Setting(contentEl).setName("\u5B9F\u884C\u9593\u9694 (\u6642\u9593)").setDesc("\u6307\u5B9A\u6642\u9593\u3054\u3068\u306B\u5B9A\u671F\u5B9F\u884C\u3057\u307E\u3059\u30020 \u3067\u7121\u52B9").addText(
      (t) => t.setPlaceholder("0").setValue(String(this.rule.intervalHours)).onChange((v) => {
        const n = parseFloat(v);
        this.rule.intervalHours = isNaN(n) || n < 0 ? 0 : n;
      })
    );
    new import_obsidian.Setting(contentEl).setName("cron \u5F0F (\u4EFB\u610F)").setDesc("\u5206 \u6642 \u65E5 \u6708 \u66DC\u65E5 \u306E\u5F62\u5F0F (\u4F8B: 0 3 * * * = \u6BCE\u671D3\u6642). \u7A7A\u6B04\u3067\u7121\u52B9").addText(
      (t) => t.setPlaceholder("0 3 * * *").setValue(this.rule.cronExpression).onChange((v) => this.rule.cronExpression = v.trim())
    );
    const btnRow = contentEl.createDiv({
      attr: { style: "display:flex; justify-content:flex-end; gap:8px; margin-top:24px;" }
    });
    btnRow.createEl("button", { text: "\u30AD\u30E3\u30F3\u30BB\u30EB" }).addEventListener("click", () => this.close());
    const saveBtn = btnRow.createEl("button", { text: "\u4FDD\u5B58", cls: "mod-cta" });
    saveBtn.addEventListener("click", () => {
      if (!this.rule.name) {
        new import_obsidian.Notice("\u30EB\u30FC\u30EB\u540D\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
        return;
      }
      if (!this.rule.folder) {
        new import_obsidian.Notice("\u5BFE\u8C61\u30D5\u30A9\u30EB\u30C0\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
        return;
      }
      if (this.rule.olderThanDays === 0 && !this.rule.filePattern && !this.rule.extensionFilter && this.rule.minSizeKB === 0) {
        new import_obsidian.Notice("\u6761\u4EF6\u3092\u5C11\u306A\u304F\u3068\u30821\u3064\u8A2D\u5B9A\u3057\u3066\u304F\u3060\u3055\u3044\uFF08\u7D4C\u904E\u65E5\u6570\u3001\u30D5\u30A1\u30A4\u30EB\u540D\u30D1\u30BF\u30FC\u30F3\u3001\u62E1\u5F35\u5B50\u3001\u307E\u305F\u306F\u30B5\u30A4\u30BA\uFF09");
        return;
      }
      if (!this.rule.runOnStartup && this.rule.intervalHours === 0 && !this.rule.cronExpression) {
        new import_obsidian.Notice("\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u3092\u5C11\u306A\u304F\u3068\u30821\u3064\u8A2D\u5B9A\u3057\u3066\u304F\u3060\u3055\u3044");
        return;
      }
      if (this.rule.cronExpression && !parseCron(this.rule.cronExpression)) {
        new import_obsidian.Notice("cron \u5F0F\u304C\u7121\u52B9\u3067\u3059 (\u4F8B: 0 3 * * *)");
        return;
      }
      this.onSave(this.rule);
      this.close();
    });
  }
  onClose() {
    this.contentEl.empty();
  }
};
var PreviewModal = class extends import_obsidian.Modal {
  constructor(app, results) {
    super(app);
    this.results = results;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: "\u524A\u9664\u30D7\u30EC\u30D3\u30E5\u30FC (\u30C9\u30E9\u30A4\u30E9\u30F3)" });
    const total = this.results.reduce((s, r) => s + r.files.length, 0);
    contentEl.createEl("p", {
      text: `\u5408\u8A08 ${total} \u4EF6\u306E\u30D5\u30A1\u30A4\u30EB\u304C\u524A\u9664\u5BFE\u8C61\u3067\u3059\u3002`,
      attr: { style: "color:var(--text-muted);" }
    });
    if (total === 0) {
      contentEl.createEl("p", { text: "\u524A\u9664\u5BFE\u8C61\u306E\u30D5\u30A1\u30A4\u30EB\u306F\u3042\u308A\u307E\u305B\u3093\u3002" });
    }
    for (const { ruleName, files } of this.results) {
      if (files.length === 0)
        continue;
      contentEl.createEl("h4", { text: `${ruleName} (${files.length} \u4EF6)` });
      const ul = contentEl.createEl("ul", { attr: { style: "font-size:0.85em; max-height:200px; overflow-y:auto;" } });
      for (const f of files)
        ul.createEl("li", { text: f.path });
    }
    const btnRow = contentEl.createDiv({ attr: { style: "display:flex; justify-content:flex-end; margin-top:16px;" } });
    btnRow.createEl("button", { text: "\u9589\u3058\u308B", cls: "mod-cta" }).addEventListener("click", () => this.close());
  }
  onClose() {
    this.contentEl.empty();
  }
};
var TimedDeleteSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Timed Delete" });
    containerEl.createEl("p", {
      text: "\u6307\u5B9A\u3057\u305F\u30D5\u30A9\u30EB\u30C0\u306E\u30D5\u30A1\u30A4\u30EB\u3092\u6761\u4EF6\u306B\u5F93\u3063\u3066\u81EA\u52D5\u524A\u9664\u3057\u307E\u3059\u3002\u8907\u6570\u306E\u30EB\u30FC\u30EB\u3092\u8A2D\u5B9A\u3067\u304D\u307E\u3059\u3002",
      attr: { style: "color:var(--text-muted); margin-bottom:8px;" }
    });
    new import_obsidian.Setting(containerEl).setName("\u30C9\u30E9\u30A4\u30E9\u30F3 (\u5B89\u5168\u78BA\u8A8D)").setDesc("ON \u306B\u3059\u308B\u3068\u5B9F\u969B\u306B\u306F\u524A\u9664\u305B\u305A\u3001\u524A\u9664\u5BFE\u8C61\u306E\u30D7\u30EC\u30D3\u30E5\u30FC\u306E\u307F\u8868\u793A\u3057\u307E\u3059").addToggle(
      (t) => t.setValue(this.plugin.settings.dryRun).onChange(async (v) => {
        this.plugin.settings.dryRun = v;
        await this.plugin.saveSettings();
      })
    );
    containerEl.createEl("hr");
    new import_obsidian.Setting(containerEl).setName("\u524A\u9664\u30EB\u30FC\u30EB").addButton(
      (btn) => btn.setButtonText("+ \u30EB\u30FC\u30EB\u3092\u8FFD\u52A0").setCta().onClick(() => {
        new DeleteRuleModal(this.app, null, async (rule) => {
          this.plugin.settings.rules.push(rule);
          await this.plugin.saveSettings();
          this.display();
        }).open();
      })
    );
    if (this.plugin.settings.rules.length === 0) {
      containerEl.createEl("p", {
        text: "\u30EB\u30FC\u30EB\u304C\u3042\u308A\u307E\u305B\u3093\u3002\u300C+ \u30EB\u30FC\u30EB\u3092\u8FFD\u52A0\u300D\u30DC\u30BF\u30F3\u3067\u8FFD\u52A0\u3057\u3066\u304F\u3060\u3055\u3044\u3002",
        attr: { style: "color:var(--text-muted);" }
      });
    }
    for (const rule of this.plugin.settings.rules) {
      const desc = this.buildDesc(rule);
      const s = new import_obsidian.Setting(containerEl).setName(rule.name || "(\u540D\u524D\u306A\u3057)").setDesc(desc);
      s.addToggle(
        (t) => t.setValue(rule.enabled).onChange(async (v) => {
          rule.enabled = v;
          await this.plugin.saveSettings();
        })
      );
      s.addButton(
        (btn) => btn.setIcon("play").setTooltip("\u4ECA\u3059\u3050\u5B9F\u884C").onClick(async () => {
          await this.plugin.executeRule(rule);
        })
      );
      s.addButton(
        (btn) => btn.setIcon("pencil").setTooltip("\u7DE8\u96C6").onClick(() => {
          new DeleteRuleModal(this.app, rule, async (updated) => {
            const idx = this.plugin.settings.rules.findIndex((r) => r.id === updated.id);
            if (idx >= 0)
              this.plugin.settings.rules[idx] = updated;
            await this.plugin.saveSettings();
            this.display();
          }).open();
        })
      );
      s.addButton(
        (btn) => btn.setIcon("trash").setTooltip("\u30EB\u30FC\u30EB\u3092\u524A\u9664").setWarning().onClick(async () => {
          this.plugin.settings.rules = this.plugin.settings.rules.filter((r) => r.id !== rule.id);
          await this.plugin.saveSettings();
          this.display();
        })
      );
    }
  }
  buildDesc(rule) {
    const parts = [];
    parts.push(`\u30D5\u30A9\u30EB\u30C0: ${rule.folder || "(\u672A\u8A2D\u5B9A)"}${rule.recursive ? " (\u518D\u5E30)" : ""}`);
    const conds = [];
    if (rule.olderThanDays > 0)
      conds.push(`${rule.olderThanDays}\u65E5\u4EE5\u4E0A (${rule.ageBase === "created" ? "\u4F5C\u6210" : "\u66F4\u65B0"})`);
    if (rule.filePattern)
      conds.push(`\u540D\u524D: ${rule.filePattern}`);
    if (rule.extensionFilter)
      conds.push(`\u62E1\u5F35\u5B50: ${rule.extensionFilter}`);
    if (rule.minSizeKB > 0)
      conds.push(`${rule.minSizeKB}KB\u4EE5\u4E0A`);
    if (conds.length > 0)
      parts.push(`\u6761\u4EF6: ${conds.join(", ")}`);
    parts.push(`\u524A\u9664: ${rule.deleteMode === "trash" ? "\u30B4\u30DF\u7BB1" : "\u5B8C\u5168\u524A\u9664"}`);
    const sched = [];
    if (rule.runOnStartup)
      sched.push("\u8D77\u52D5\u6642");
    if (rule.intervalHours > 0)
      sched.push(`${rule.intervalHours}\u6642\u9593\u3054\u3068`);
    if (rule.cronExpression)
      sched.push(`cron: ${rule.cronExpression}`);
    if (sched.length > 0)
      parts.push(`\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB: ${sched.join(", ")}`);
    return parts.join("  |  ");
  }
};
var TimedDeletePlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.settings = DEFAULT_SETTINGS;
    this.statusBarEl = null;
  }
  async onload() {
    await this.loadSettings();
    this.statusBarEl = this.addStatusBarItem();
    this.updateStatusBar();
    this.addRibbonIcon("clock", "Timed Delete: \u4ECA\u3059\u3050\u5B9F\u884C", async () => {
      await this.runAllRules();
    });
    this.addCommand({
      id: "run-all-rules",
      name: "\u6709\u52B9\u306A\u30EB\u30FC\u30EB\u3092\u3059\u3079\u3066\u4ECA\u3059\u3050\u5B9F\u884C",
      callback: async () => {
        await this.runAllRules();
      }
    });
    this.addCommand({
      id: "preview-deletions",
      name: "\u524A\u9664\u5BFE\u8C61\u3092\u30D7\u30EC\u30D3\u30E5\u30FC (\u30C9\u30E9\u30A4\u30E9\u30F3)",
      callback: () => {
        this.showPreview();
      }
    });
    this.app.workspace.onLayoutReady(async () => {
      for (const rule of this.settings.rules) {
        if (rule.enabled && rule.runOnStartup) {
          await this.executeRule(rule);
        }
      }
      this.updateStatusBar();
    });
    this.registerInterval(window.setInterval(() => this.tickSchedules(), 6e4));
    this.addSettingTab(new TimedDeleteSettingTab(this.app, this));
  }
  // ----------------------------------------------------------
  async tickSchedules() {
    const now = /* @__PURE__ */ new Date();
    const key = minuteKey(now);
    let dirty = false;
    for (const rule of this.settings.rules) {
      if (!rule.enabled)
        continue;
      if (rule.intervalHours > 0) {
        const intervalMs = rule.intervalHours * 36e5;
        if (rule.lastRunTimestamp > 0 && now.getTime() - rule.lastRunTimestamp >= intervalMs) {
          await this.executeRule(rule, false);
          dirty = true;
        }
      }
      if (rule.cronExpression && rule.lastRunMinute !== key) {
        const cron = parseCron(rule.cronExpression);
        if (cron && cronMatchesNow(cron, now)) {
          rule.lastRunMinute = key;
          await this.executeRule(rule, false);
          dirty = true;
        }
      }
    }
    if (dirty)
      await this.saveSettings();
    this.updateStatusBar();
  }
  async executeRule(rule, updateState = true) {
    const files = collectFiles(this.app, rule);
    if (files.length === 0) {
      new import_obsidian.Notice(`Timed Delete [${rule.name}]: \u524A\u9664\u5BFE\u8C61\u306E\u30D5\u30A1\u30A4\u30EB\u306F\u3042\u308A\u307E\u305B\u3093\u3067\u3057\u305F`);
      return;
    }
    if (this.settings.dryRun) {
      new PreviewModal(this.app, [{ ruleName: rule.name, files }]).open();
      return;
    }
    let deleted = 0;
    for (const file of files) {
      try {
        if (rule.deleteMode === "permanent") {
          await this.app.vault.delete(file, true);
        } else {
          await this.app.vault.trash(file, true);
        }
        deleted++;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        new import_obsidian.Notice(`Timed Delete: ${file.path} \u306E\u524A\u9664\u306B\u5931\u6557\u3057\u307E\u3057\u305F
${msg}`, 6e3);
      }
    }
    new import_obsidian.Notice(`Timed Delete [${rule.name}]: ${deleted} \u4EF6\u306E\u30D5\u30A1\u30A4\u30EB\u3092\u524A\u9664\u3057\u307E\u3057\u305F`);
    if (updateState) {
      rule.lastRunTimestamp = Date.now();
      await this.saveSettings();
    }
  }
  async runAllRules() {
    const enabled = this.settings.rules.filter((r) => r.enabled);
    if (enabled.length === 0) {
      new import_obsidian.Notice("Timed Delete: \u6709\u52B9\u306A\u30EB\u30FC\u30EB\u304C\u3042\u308A\u307E\u305B\u3093");
      return;
    }
    for (const rule of enabled)
      await this.executeRule(rule);
    this.updateStatusBar();
  }
  showPreview() {
    const results = this.settings.rules.filter((r) => r.enabled).map((r) => ({ ruleName: r.name, files: collectFiles(this.app, r) }));
    new PreviewModal(this.app, results).open();
  }
  updateStatusBar() {
    if (!this.statusBarEl)
      return;
    const total = this.settings.rules.filter((r) => r.enabled).reduce((s, r) => s + collectFiles(this.app, r).length, 0);
    if (this.settings.dryRun) {
      this.statusBarEl.setText(`\u{1F550} Timed Delete: \u30C9\u30E9\u30A4\u30E9\u30F3 (${total} \u4EF6\u5BFE\u8C61)`);
    } else if (total > 0) {
      this.statusBarEl.setText(`\u{1F550} Timed Delete: ${total} \u4EF6\u5BFE\u8C61`);
    } else {
      this.statusBarEl.setText("");
    }
  }
  async loadSettings() {
    const loaded = await this.loadData();
    this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded);
    if (!Array.isArray(this.settings.rules))
      this.settings.rules = [];
  }
  async saveSettings() {
    await this.saveData(this.settings);
  }
};
