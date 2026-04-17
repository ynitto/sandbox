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
  VIEW_TYPE_CLIPBOARD: () => VIEW_TYPE_CLIPBOARD,
  default: () => ClipboardHistoryPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var electronClipboard = null;
try {
  electronClipboard = require("electron").clipboard;
} catch (e) {
  console.error("[ClipboardHistory] Failed to access Electron clipboard:", e);
}
var DEFAULT_SETTINGS = {
  maxHistorySize: 50,
  saveDirectory: "Clipboard History",
  pollingInterval: 1e3,
  expiryHours: 24,
  groupByDay: false,
  autoSave: false
};
function formatTimestamp(ts) {
  const d = new Date(ts);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function toSafeFileName(content) {
  const firstLine = content.split("\n")[0].trim().slice(0, 50);
  return firstLine.replace(/[\\/:*?"<>|]/g, "_") || "clipboard";
}
var VIEW_TYPE_CLIPBOARD = "clipboard-history-view";
var ClipboardHistoryView = class extends import_obsidian.ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
  }
  getViewType() {
    return VIEW_TYPE_CLIPBOARD;
  }
  getDisplayText() {
    return "Clipboard History";
  }
  getIcon() {
    return "clipboard-list";
  }
  async onOpen() {
    this.refresh();
  }
  refresh() {
    const container = this.containerEl.children[1];
    container.empty();
    container.addClass("ch-container");
    const header = container.createDiv({ cls: "ch-header" });
    header.createEl("h4", { text: "Clipboard History" });
    const clearBtn = header.createEl("button", { text: "Clear All", cls: "ch-btn mod-warning" });
    clearBtn.addEventListener("click", async () => {
      if (!confirm("Clear all clipboard history?"))
        return;
      await this.plugin.clearHistory();
      this.refresh();
    });
    const history = this.plugin.getHistory();
    if (history.length === 0) {
      container.createEl("p", {
        text: "No clipboard history yet. Start copying text!",
        cls: "ch-empty"
      });
      return;
    }
    const list = container.createDiv({ cls: "ch-list" });
    history.forEach((entry) => {
      const item = list.createDiv({ cls: "ch-item" });
      const meta = item.createDiv({ cls: "ch-meta" });
      meta.createEl("span", {
        text: formatTimestamp(entry.timestamp),
        cls: "ch-timestamp"
      });
      if (entry.savedAt) {
        meta.createEl("span", { text: "\u4FDD\u5B58\u6E08", cls: "ch-saved-badge" });
      }
      const preview = item.createDiv({ cls: "ch-preview" });
      preview.setText(
        entry.content.length > 300 ? entry.content.substring(0, 300) + "\u2026" : entry.content
      );
      const actions = item.createDiv({ cls: "ch-actions" });
      const copyBtn = actions.createEl("button", { text: "Copy", cls: "ch-btn" });
      copyBtn.addEventListener("click", async () => {
        await navigator.clipboard.writeText(entry.content);
        new import_obsidian.Notice("Copied to clipboard!");
      });
      const saveBtn = actions.createEl("button", { text: "Save to File", cls: "ch-btn mod-cta" });
      saveBtn.addEventListener("click", async () => {
        await this.plugin.saveEntryToFile(entry);
      });
      const deleteBtn = actions.createEl("button", { text: "\xD7", cls: "ch-btn ch-delete-btn" });
      deleteBtn.addEventListener("click", () => {
        this.plugin.removeEntry(entry.id);
        this.refresh();
      });
    });
  }
  async onClose() {
  }
};
var ClipboardHistoryPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.settings = { ...DEFAULT_SETTINGS };
    this.history = [];
    this.pollingTimer = null;
    this.lastContent = "";
  }
  async onload() {
    await this.loadPluginData();
    this.registerView(VIEW_TYPE_CLIPBOARD, (leaf) => new ClipboardHistoryView(leaf, this));
    this.addRibbonIcon("clipboard-list", "Clipboard History", () => {
      this.activateView();
    });
    this.addCommand({
      id: "open-clipboard-history",
      name: "Open Clipboard History",
      callback: () => this.activateView()
    });
    this.addCommand({
      id: "save-latest-to-file",
      name: "Save Latest Clipboard Entry to File",
      callback: async () => {
        const latest = this.history[0];
        if (latest) {
          await this.saveEntryToFile(latest);
        } else {
          new import_obsidian.Notice("No clipboard history yet.");
        }
      }
    });
    this.addCommand({
      id: "clear-clipboard-history",
      name: "Clear Clipboard History",
      callback: async () => {
        await this.clearHistory();
        new import_obsidian.Notice("Clipboard history cleared.");
      }
    });
    this.addSettingTab(new ClipboardHistorySettingTab(this.app, this));
    this.startPolling();
  }
  onunload() {
    this.stopPolling();
  }
  // ----------------------------------------------------------
  // Polling
  // ----------------------------------------------------------
  startPolling() {
    if (this.pollingTimer !== null)
      return;
    if (electronClipboard) {
      try {
        this.lastContent = electronClipboard.readText();
      } catch (_) {
      }
    }
    this.pollingTimer = window.setInterval(
      () => this.checkClipboard(),
      this.settings.pollingInterval
    );
  }
  stopPolling() {
    if (this.pollingTimer !== null) {
      window.clearInterval(this.pollingTimer);
      this.pollingTimer = null;
    }
  }
  checkClipboard() {
    if (!electronClipboard)
      return;
    try {
      const current = electronClipboard.readText();
      if (current && current !== this.lastContent) {
        this.lastContent = current;
        this.addToHistory(current);
      }
    } catch (_) {
    }
  }
  addToHistory(content) {
    if (!content.trim())
      return;
    const entry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      content,
      timestamp: Date.now()
    };
    this.history.unshift(entry);
    this.pruneHistory();
    this.refreshView();
    this.savePluginDataAsync();
    if (this.settings.autoSave) {
      this.saveEntryToFile(entry).catch(
        (e) => console.error("[ClipboardHistory] auto-save failed:", e)
      );
    }
  }
  // ----------------------------------------------------------
  // History management
  // ----------------------------------------------------------
  pruneHistory() {
    const cutoff = Date.now() - this.settings.expiryHours * 60 * 60 * 1e3;
    this.history = this.history.filter((e) => e.timestamp >= cutoff);
    if (this.history.length > this.settings.maxHistorySize) {
      this.history.length = this.settings.maxHistorySize;
    }
  }
  getHistory() {
    return this.history;
  }
  async clearHistory() {
    this.history = [];
    this.refreshView();
    await this.savePluginData();
  }
  removeEntry(id) {
    this.history = this.history.filter((e) => e.id !== id);
    this.savePluginDataAsync();
  }
  async saveEntryToFile(entry) {
    const dir = (0, import_obsidian.normalizePath)(this.settings.saveDirectory);
    if (!await this.app.vault.adapter.exists(dir)) {
      await this.app.vault.createFolder(dir);
    }
    let filePath;
    if (this.settings.groupByDay) {
      const d = new Date(entry.timestamp);
      const pad = (n) => String(n).padStart(2, "0");
      const dateStr = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
      const timeStr = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      filePath = (0, import_obsidian.normalizePath)(`${dir}/${dateStr}.md`);
      const appendContent = `
## ${timeStr}

${entry.content}
`;
      if (await this.app.vault.adapter.exists(filePath)) {
        const existing = await this.app.vault.adapter.read(filePath);
        await this.app.vault.adapter.write(filePath, existing + appendContent);
      } else {
        await this.app.vault.create(filePath, `# ${dateStr}
${appendContent}`);
      }
    } else {
      const datePrefix = formatTimestamp(entry.timestamp).replace(/[: ]/g, "-");
      const namePart = toSafeFileName(entry.content);
      filePath = (0, import_obsidian.normalizePath)(`${dir}/${datePrefix}_${namePart}.md`);
      let counter = 1;
      while (await this.app.vault.adapter.exists(filePath)) {
        filePath = (0, import_obsidian.normalizePath)(`${dir}/${datePrefix}_${namePart}_${counter++}.md`);
      }
      await this.app.vault.create(filePath, entry.content);
    }
    new import_obsidian.Notice(`Saved: ${filePath}`);
    entry.savedAt = Date.now();
    this.savePluginDataAsync();
    this.refreshView();
  }
  async activateView() {
    var _a;
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE_CLIPBOARD)[0];
    if (!leaf) {
      leaf = (_a = workspace.getRightLeaf(false)) != null ? _a : workspace.getLeaf(true);
      await leaf.setViewState({ type: VIEW_TYPE_CLIPBOARD, active: true });
    }
    workspace.revealLeaf(leaf);
  }
  refreshView() {
    this.app.workspace.getLeavesOfType(VIEW_TYPE_CLIPBOARD).forEach((leaf) => {
      if (leaf.view instanceof ClipboardHistoryView) {
        leaf.view.refresh();
      }
    });
  }
  // ----------------------------------------------------------
  // Persistence
  // ----------------------------------------------------------
  async saveSettings() {
    this.pruneHistory();
    await this.savePluginData();
  }
  async loadPluginData() {
    var _a, _b;
    const raw = await this.loadData();
    if (raw && typeof raw === "object" && "settings" in raw) {
      const data = raw;
      this.settings = Object.assign({}, DEFAULT_SETTINGS, (_a = data.settings) != null ? _a : {});
      this.history = (_b = data.history) != null ? _b : [];
    } else {
      this.settings = Object.assign({}, DEFAULT_SETTINGS, raw != null ? raw : {});
      this.history = [];
    }
    this.pruneHistory();
  }
  async savePluginData() {
    const data = { settings: this.settings, history: this.history };
    await this.saveData(data);
  }
  savePluginDataAsync() {
    this.savePluginData().catch(
      (e) => console.error("[ClipboardHistory] save failed:", e)
    );
  }
};
var ClipboardHistorySettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Clipboard History Settings" });
    new import_obsidian.Setting(containerEl).setName("Max history size").setDesc("Maximum number of clipboard entries to keep.").addText(
      (text) => text.setPlaceholder("50").setValue(this.plugin.settings.maxHistorySize.toString()).onChange(async (value) => {
        const num = parseInt(value, 10);
        if (!isNaN(num) && num > 0) {
          this.plugin.settings.maxHistorySize = num;
          await this.plugin.saveSettings();
        }
      })
    );
    new import_obsidian.Setting(containerEl).setName("Save directory").setDesc("Vault folder where clipboard entries are saved as Markdown notes.").addText(
      (text) => text.setPlaceholder("Clipboard History").setValue(this.plugin.settings.saveDirectory).onChange(async (value) => {
        this.plugin.settings.saveDirectory = value.trim() || "Clipboard History";
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("Polling interval (ms)").setDesc("How often the plugin checks for clipboard changes. Minimum 200 ms.").addText(
      (text) => text.setPlaceholder("1000").setValue(this.plugin.settings.pollingInterval.toString()).onChange(async (value) => {
        const num = parseInt(value, 10);
        if (!isNaN(num) && num >= 200) {
          this.plugin.settings.pollingInterval = num;
          await this.plugin.saveSettings();
          this.plugin.stopPolling();
          this.plugin.startPolling();
        }
      })
    );
    new import_obsidian.Setting(containerEl).setName("History expiry (hours)").setDesc("Entries older than this are automatically removed. Set 0 to keep forever.").addText(
      (text) => text.setPlaceholder("24").setValue(this.plugin.settings.expiryHours.toString()).onChange(async (value) => {
        const num = parseInt(value, 10);
        if (!isNaN(num) && num >= 0) {
          this.plugin.settings.expiryHours = num;
          await this.plugin.saveSettings();
        }
      })
    );
    new import_obsidian.Setting(containerEl).setName("Group entries by day").setDesc("When saving, append all entries for the same day into a single daily file (YYYY-MM-DD.md) instead of creating individual files.").addToggle(
      (toggle) => toggle.setValue(this.plugin.settings.groupByDay).onChange(async (value) => {
        this.plugin.settings.groupByDay = value;
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("Auto-save to file").setDesc("Automatically save each new clipboard entry to a file. Entries remain in history until you delete them manually.").addToggle(
      (toggle) => toggle.setValue(this.plugin.settings.autoSave).onChange(async (value) => {
        this.plugin.settings.autoSave = value;
        await this.plugin.saveSettings();
      })
    );
  }
};
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  VIEW_TYPE_CLIPBOARD
});
