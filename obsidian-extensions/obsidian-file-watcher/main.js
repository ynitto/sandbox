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
  default: () => FileWatcherPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var DEFAULT_SETTINGS = {
  fileWatchRules: [],
  scheduleRules: []
};
function matchesGlob(filePath, pattern) {
  if (pattern.endsWith("/")) {
    return filePath.startsWith(pattern);
  }
  const regexStr = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*\*\//g, "(.*\\/)?").replace(/\*\*/g, ".*").replace(/\*/g, "[^/]*").replace(/\?/g, "[^/]");
  return new RegExp(`^${regexStr}$`).test(filePath);
}
function parseCronField(field, min, max) {
  if (field === "*") {
    return Array.from({ length: max - min + 1 }, (_, i) => i + min);
  }
  const values = /* @__PURE__ */ new Set();
  for (const part of field.split(",")) {
    if (part.includes("/")) {
      const [rangeStr, stepStr] = part.split("/");
      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step <= 0)
        throw new Error(`Invalid step: ${stepStr}`);
      let start = min;
      let end = max;
      if (rangeStr !== "*") {
        if (rangeStr.includes("-")) {
          const [s, e] = rangeStr.split("-");
          start = parseInt(s, 10);
          end = parseInt(e, 10);
        } else {
          start = parseInt(rangeStr, 10);
        }
      }
      for (let i = start; i <= end; i += step) {
        if (i >= min && i <= max)
          values.add(i);
      }
    } else if (part.includes("-")) {
      const [s, e] = part.split("-");
      const start = parseInt(s, 10);
      const end = parseInt(e, 10);
      for (let i = start; i <= end; i++) {
        if (i >= min && i <= max)
          values.add(i);
      }
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
function getCommands(app) {
  var _a, _b;
  const cmds = (_b = (_a = app.commands) == null ? void 0 : _a.commands) != null ? _b : {};
  return Object.values(cmds).map((c) => ({ id: c.id, name: c.name })).sort((a, b) => a.name.localeCompare(b.name));
}
function minuteKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${date.getHours()}-${date.getMinutes()}`;
}
var FileWatchRuleModal = class extends import_obsidian.Modal {
  constructor(app, rule, onSave) {
    super(app);
    this.isNew = rule === null;
    this.rule = rule ? { ...rule, events: [...rule.events] } : {
      id: crypto.randomUUID(),
      name: "",
      pathPattern: "",
      events: ["create", "modify"],
      commandId: "",
      enabled: true,
      activateFile: false
    };
    this.onSave = onSave;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", {
      text: this.isNew ? "\u30D5\u30A1\u30A4\u30EB\u76E3\u8996\u30EB\u30FC\u30EB\u3092\u8FFD\u52A0" : "\u30D5\u30A1\u30A4\u30EB\u76E3\u8996\u30EB\u30FC\u30EB\u3092\u7DE8\u96C6"
    });
    new import_obsidian.Setting(contentEl).setName("\u540D\u524D").setDesc("\u3053\u306E\u30EB\u30FC\u30EB\u306E\u8B58\u5225\u540D").addText((t) => t.setValue(this.rule.name).onChange((v) => this.rule.name = v.trim()));
    new import_obsidian.Setting(contentEl).setName("\u30D1\u30B9\u30D1\u30BF\u30FC\u30F3").setDesc("glob \u30D1\u30BF\u30FC\u30F3 (\u4F8B: notes/**/*.md, daily/*.md) \u307E\u305F\u306F\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9 (\u4F8B: attachments/)").addText(
      (t) => t.setPlaceholder("notes/**/*.md").setValue(this.rule.pathPattern).onChange((v) => this.rule.pathPattern = v.trim())
    );
    const evtSetting = new import_obsidian.Setting(contentEl).setName("\u76E3\u8996\u30A4\u30D9\u30F3\u30C8");
    const cbWrap = evtSetting.controlEl.createDiv({ attr: { style: "display:flex; gap:16px;" } });
    for (const evt of ["create", "modify"]) {
      const label = cbWrap.createEl("label", {
        attr: { style: "display:flex; align-items:center; gap:4px; cursor:pointer;" }
      });
      const cb = label.createEl("input", { type: "checkbox" });
      cb.checked = this.rule.events.includes(evt);
      cb.addEventListener("change", () => {
        if (cb.checked) {
          if (!this.rule.events.includes(evt))
            this.rule.events.push(evt);
        } else {
          this.rule.events = this.rule.events.filter((e) => e !== evt);
        }
      });
      label.createSpan({ text: evt === "create" ? "\u4F5C\u6210" : "\u5909\u66F4" });
    }
    const commands = getCommands(this.app);
    new import_obsidian.Setting(contentEl).setName("\u5B9F\u884C\u30B3\u30DE\u30F3\u30C9").setDesc("\u30D5\u30A1\u30A4\u30EB\u30A4\u30D9\u30F3\u30C8\u767A\u751F\u6642\u306B\u5B9F\u884C\u3059\u308B Obsidian \u30B3\u30DE\u30F3\u30C9").addDropdown((dd) => {
      dd.addOption("", "-- \u30B3\u30DE\u30F3\u30C9\u3092\u9078\u629E --");
      for (const cmd of commands)
        dd.addOption(cmd.id, cmd.name);
      dd.setValue(this.rule.commandId).onChange((v) => this.rule.commandId = v);
    });
    new import_obsidian.Setting(contentEl).setName("\u30D5\u30A1\u30A4\u30EB\u3092\u30A2\u30AF\u30C6\u30A3\u30D6\u306B\u3057\u3066\u5B9F\u884C").setDesc("ON \u306B\u3059\u308B\u3068\u30B3\u30DE\u30F3\u30C9\u5B9F\u884C\u524D\u306B\u30C8\u30EA\u30AC\u30FC\u3068\u306A\u3063\u305F\u30D5\u30A1\u30A4\u30EB\u3092\u958B\u3044\u3066\u30A2\u30AF\u30C6\u30A3\u30D6\u306B\u3057\u307E\u3059").addToggle(
      (t) => t.setValue(this.rule.activateFile).onChange((v) => this.rule.activateFile = v)
    );
    const btnRow = contentEl.createDiv({
      attr: { style: "display:flex; justify-content:flex-end; gap:8px; margin-top:16px;" }
    });
    btnRow.createEl("button", { text: "\u30AD\u30E3\u30F3\u30BB\u30EB" }).addEventListener("click", () => this.close());
    const saveBtn = btnRow.createEl("button", { text: "\u4FDD\u5B58", cls: "mod-cta" });
    saveBtn.addEventListener("click", () => {
      if (!this.rule.name)
        return new import_obsidian.Notice("\u540D\u524D\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
      if (!this.rule.pathPattern)
        return new import_obsidian.Notice("\u30D1\u30B9\u30D1\u30BF\u30FC\u30F3\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
      if (!this.rule.commandId)
        return new import_obsidian.Notice("\u30B3\u30DE\u30F3\u30C9\u3092\u9078\u629E\u3057\u3066\u304F\u3060\u3055\u3044");
      if (this.rule.events.length === 0)
        return new import_obsidian.Notice("\u30A4\u30D9\u30F3\u30C8\u30921\u3064\u4EE5\u4E0A\u9078\u629E\u3057\u3066\u304F\u3060\u3055\u3044");
      this.onSave(this.rule);
      this.close();
    });
  }
  onClose() {
    this.contentEl.empty();
  }
};
var ScheduleRuleModal = class extends import_obsidian.Modal {
  constructor(app, rule, onSave) {
    super(app);
    this.isNew = rule === null;
    this.rule = rule ? { ...rule } : {
      id: crypto.randomUUID(),
      name: "",
      schedule: "0 9 * * *",
      commandId: "",
      enabled: true
    };
    this.onSave = onSave;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", {
      text: this.isNew ? "\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u30EB\u30FC\u30EB\u3092\u8FFD\u52A0" : "\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u30EB\u30FC\u30EB\u3092\u7DE8\u96C6"
    });
    new import_obsidian.Setting(contentEl).setName("\u540D\u524D").setDesc("\u3053\u306E\u30EB\u30FC\u30EB\u306E\u8B58\u5225\u540D").addText((t) => t.setValue(this.rule.name).onChange((v) => this.rule.name = v.trim()));
    new import_obsidian.Setting(contentEl).setName("\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB (cron \u5F0F)").setDesc("\u5F62\u5F0F: \u5206 \u6642 \u65E5 \u6708 \u66DC\u65E5 (0=\u65E5\u66DC)  \u4F8B: 0 9 * * * = \u6BCE\u671D9\u6642, */5 * * * * = 5\u5206\u3054\u3068").addText(
      (t) => t.setPlaceholder("0 9 * * *").setValue(this.rule.schedule).onChange((v) => this.rule.schedule = v.trim())
    );
    new import_obsidian.Setting(contentEl).setName("\u5BFE\u8C61\u30D5\u30A1\u30A4\u30EB (\u7701\u7565\u53EF)").setDesc("\u6307\u5B9A\u3059\u308B\u3068\u30B3\u30DE\u30F3\u30C9\u5B9F\u884C\u524D\u306B\u305D\u306E\u30D5\u30A1\u30A4\u30EB\u3092\u958B\u304D\u30A2\u30AF\u30C6\u30A3\u30D6\u306B\u3057\u307E\u3059 (Vault \u5185\u306E\u76F8\u5BFE\u30D1\u30B9)").addText(
      (t) => {
        var _a;
        return t.setPlaceholder("notes/target.md").setValue((_a = this.rule.filePath) != null ? _a : "").onChange((v) => {
          const trimmed = v.trim();
          this.rule.filePath = trimmed || void 0;
        });
      }
    );
    const commands = getCommands(this.app);
    new import_obsidian.Setting(contentEl).setName("\u5B9F\u884C\u30B3\u30DE\u30F3\u30C9").setDesc("\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u5B9F\u884C\u6642\u306B\u547C\u3073\u51FA\u3059 Obsidian \u30B3\u30DE\u30F3\u30C9").addDropdown((dd) => {
      dd.addOption("", "-- \u30B3\u30DE\u30F3\u30C9\u3092\u9078\u629E --");
      for (const cmd of commands)
        dd.addOption(cmd.id, cmd.name);
      dd.setValue(this.rule.commandId).onChange((v) => this.rule.commandId = v);
    });
    const btnRow = contentEl.createDiv({
      attr: { style: "display:flex; justify-content:flex-end; gap:8px; margin-top:16px;" }
    });
    btnRow.createEl("button", { text: "\u30AD\u30E3\u30F3\u30BB\u30EB" }).addEventListener("click", () => this.close());
    const saveBtn = btnRow.createEl("button", { text: "\u4FDD\u5B58", cls: "mod-cta" });
    saveBtn.addEventListener("click", () => {
      if (!this.rule.name)
        return new import_obsidian.Notice("\u540D\u524D\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
      if (!parseCron(this.rule.schedule))
        return new import_obsidian.Notice("\u6709\u52B9\u306A cron \u5F0F\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044 (\u4F8B: 0 9 * * *)");
      if (!this.rule.commandId)
        return new import_obsidian.Notice("\u30B3\u30DE\u30F3\u30C9\u3092\u9078\u629E\u3057\u3066\u304F\u3060\u3055\u3044");
      this.onSave(this.rule);
      this.close();
    });
  }
  onClose() {
    this.contentEl.empty();
  }
};
var FileWatcherSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "\u30D5\u30A1\u30A4\u30EB\u76E3\u8996\u30EB\u30FC\u30EB" });
    containerEl.createEl("p", {
      text: "\u30D5\u30A1\u30A4\u30EB\u304C\u4F5C\u6210\u30FB\u5909\u66F4\u3055\u308C\u305F\u6642\u306B Obsidian \u30B3\u30DE\u30F3\u30C9\u3092\u81EA\u52D5\u5B9F\u884C\u3057\u307E\u3059\u3002",
      attr: { style: "color:var(--text-muted); margin-bottom:8px;" }
    });
    new import_obsidian.Setting(containerEl).setName("\u30EB\u30FC\u30EB\u3092\u8FFD\u52A0").addButton(
      (btn) => btn.setButtonText("+ \u8FFD\u52A0").setCta().onClick(() => {
        new FileWatchRuleModal(this.app, null, async (rule) => {
          this.plugin.settings.fileWatchRules.push(rule);
          await this.plugin.saveSettings();
          this.display();
        }).open();
      })
    );
    if (this.plugin.settings.fileWatchRules.length === 0) {
      containerEl.createEl("p", {
        text: "\u30EB\u30FC\u30EB\u304C\u3042\u308A\u307E\u305B\u3093\u3002\u300C+ \u8FFD\u52A0\u300D\u30DC\u30BF\u30F3\u3067\u8FFD\u52A0\u3057\u3066\u304F\u3060\u3055\u3044\u3002",
        attr: { style: "color:var(--text-muted); padding:4px 0;" }
      });
    }
    for (const rule of this.plugin.settings.fileWatchRules) {
      const cmdName = this.commandName(rule.commandId);
      const activateLabel = rule.activateFile ? "  |  \u30D5\u30A1\u30A4\u30EB\u3092\u30A2\u30AF\u30C6\u30A3\u30D6\u5316" : "";
      new import_obsidian.Setting(containerEl).setName(rule.name || "(\u540D\u524D\u306A\u3057)").setDesc(
        `\u30D1\u30BF\u30FC\u30F3: ${rule.pathPattern}  |  \u30A4\u30D9\u30F3\u30C8: ${rule.events.join(", ")}  |  \u30B3\u30DE\u30F3\u30C9: ${cmdName}${activateLabel}`
      ).addToggle(
        (tog) => tog.setValue(rule.enabled).onChange(async (v) => {
          rule.enabled = v;
          await this.plugin.saveSettings();
        })
      ).addButton(
        (btn) => btn.setIcon("pencil").setTooltip("\u7DE8\u96C6").onClick(() => {
          new FileWatchRuleModal(this.app, rule, async (updated) => {
            const idx = this.plugin.settings.fileWatchRules.findIndex(
              (r) => r.id === updated.id
            );
            if (idx >= 0)
              this.plugin.settings.fileWatchRules[idx] = updated;
            await this.plugin.saveSettings();
            this.display();
          }).open();
        })
      ).addButton(
        (btn) => btn.setIcon("trash").setTooltip("\u524A\u9664").setWarning().onClick(async () => {
          this.plugin.settings.fileWatchRules = this.plugin.settings.fileWatchRules.filter(
            (r) => r.id !== rule.id
          );
          await this.plugin.saveSettings();
          this.display();
        })
      );
    }
    containerEl.createEl("h2", {
      text: "\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u30EB\u30FC\u30EB",
      attr: { style: "margin-top:32px;" }
    });
    containerEl.createEl("p", {
      text: "cron \u5F0F\u3067\u6307\u5B9A\u3057\u305F\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u306B\u5F93\u3044 Obsidian \u30B3\u30DE\u30F3\u30C9\u3092\u5B9F\u884C\u3057\u307E\u3059\u3002",
      attr: { style: "color:var(--text-muted); margin-bottom:8px;" }
    });
    new import_obsidian.Setting(containerEl).setName("\u30EB\u30FC\u30EB\u3092\u8FFD\u52A0").addButton(
      (btn) => btn.setButtonText("+ \u8FFD\u52A0").setCta().onClick(() => {
        new ScheduleRuleModal(this.app, null, async (rule) => {
          this.plugin.settings.scheduleRules.push(rule);
          await this.plugin.saveSettings();
          this.display();
        }).open();
      })
    );
    if (this.plugin.settings.scheduleRules.length === 0) {
      containerEl.createEl("p", {
        text: "\u30EB\u30FC\u30EB\u304C\u3042\u308A\u307E\u305B\u3093\u3002\u300C+ \u8FFD\u52A0\u300D\u30DC\u30BF\u30F3\u3067\u8FFD\u52A0\u3057\u3066\u304F\u3060\u3055\u3044\u3002",
        attr: { style: "color:var(--text-muted); padding:4px 0;" }
      });
    }
    for (const rule of this.plugin.settings.scheduleRules) {
      const cmdName = this.commandName(rule.commandId);
      const fileLabel = rule.filePath ? `  |  \u30D5\u30A1\u30A4\u30EB: ${rule.filePath}` : "";
      new import_obsidian.Setting(containerEl).setName(rule.name || "(\u540D\u524D\u306A\u3057)").setDesc(`\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB: ${rule.schedule}  |  \u30B3\u30DE\u30F3\u30C9: ${cmdName}${fileLabel}`).addToggle(
        (tog) => tog.setValue(rule.enabled).onChange(async (v) => {
          rule.enabled = v;
          await this.plugin.saveSettings();
        })
      ).addButton(
        (btn) => btn.setIcon("pencil").setTooltip("\u7DE8\u96C6").onClick(() => {
          new ScheduleRuleModal(this.app, rule, async (updated) => {
            const idx = this.plugin.settings.scheduleRules.findIndex(
              (r) => r.id === updated.id
            );
            if (idx >= 0)
              this.plugin.settings.scheduleRules[idx] = updated;
            await this.plugin.saveSettings();
            this.display();
          }).open();
        })
      ).addButton(
        (btn) => btn.setIcon("trash").setTooltip("\u524A\u9664").setWarning().onClick(async () => {
          this.plugin.settings.scheduleRules = this.plugin.settings.scheduleRules.filter(
            (r) => r.id !== rule.id
          );
          await this.plugin.saveSettings();
          this.display();
        })
      );
    }
  }
  commandName(commandId) {
    var _a, _b;
    const cmd = (_b = (_a = this.app.commands) == null ? void 0 : _a.commands) == null ? void 0 : _b[commandId];
    return cmd ? cmd.name : commandId || "(\u672A\u8A2D\u5B9A)";
  }
};
var FileWatcherPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.settings = DEFAULT_SETTINGS;
  }
  async onload() {
    await this.loadSettings();
    this.registerEvent(
      this.app.vault.on("create", (file) => {
        if (file instanceof import_obsidian.TFile)
          this.handleFileEvent("create", file);
      })
    );
    this.registerEvent(
      this.app.vault.on("modify", (file) => {
        if (file instanceof import_obsidian.TFile)
          this.handleFileEvent("modify", file);
      })
    );
    this.registerInterval(window.setInterval(() => this.checkSchedules(), 6e4));
    this.addSettingTab(new FileWatcherSettingTab(this.app, this));
  }
  // ----------------------------------------------------------
  async handleFileEvent(event, file) {
    for (const rule of this.settings.fileWatchRules) {
      if (!rule.enabled)
        continue;
      if (!rule.events.includes(event))
        continue;
      if (!matchesGlob(file.path, rule.pathPattern))
        continue;
      if (rule.activateFile) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(file);
      }
      this.executeCommand(rule.commandId, `\u30D5\u30A1\u30A4\u30EB\u76E3\u8996\u30EB\u30FC\u30EB "${rule.name}"`);
    }
  }
  async checkSchedules() {
    const now = /* @__PURE__ */ new Date();
    const key = minuteKey(now);
    let dirty = false;
    for (const rule of this.settings.scheduleRules) {
      if (!rule.enabled)
        continue;
      if (rule.lastRunMinute === key)
        continue;
      const cron = parseCron(rule.schedule);
      if (!cron || !cronMatchesNow(cron, now))
        continue;
      rule.lastRunMinute = key;
      dirty = true;
      if (rule.filePath) {
        const target = this.app.vault.getAbstractFileByPath(rule.filePath);
        if (target instanceof import_obsidian.TFile) {
          const leaf = this.app.workspace.getLeaf(false);
          await leaf.openFile(target);
        } else {
          new import_obsidian.Notice(
            `File Watcher: \u30D5\u30A1\u30A4\u30EB\u304C\u898B\u3064\u304B\u308A\u307E\u305B\u3093 "${rule.filePath}"
(\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u30EB\u30FC\u30EB "${rule.name}")`,
            6e3
          );
        }
      }
      this.executeCommand(rule.commandId, `\u30B9\u30B1\u30B8\u30E5\u30FC\u30EB\u30EB\u30FC\u30EB "${rule.name}"`);
    }
    if (dirty)
      this.saveSettings();
  }
  executeCommand(commandId, source) {
    var _a;
    try {
      const ok = (_a = this.app.commands) == null ? void 0 : _a.executeCommandById(commandId);
      if (!ok) {
        new import_obsidian.Notice(`File Watcher: \u30B3\u30DE\u30F3\u30C9 "${commandId}" \u306E\u5B9F\u884C\u306B\u5931\u6557\u3057\u307E\u3057\u305F
(${source})`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`File Watcher: \u30A8\u30E9\u30FC (${source})
${msg}`, 8e3);
    }
  }
  async loadSettings() {
    const loaded = await this.loadData();
    this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded);
  }
  async saveSettings() {
    await this.saveData(this.settings);
  }
};
