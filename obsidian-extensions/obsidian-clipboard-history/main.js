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
var DEFAULT_SETTINGS = {
  maxHistorySize: 50,
  saveDirectory: "Clipboard History",
  pollingInterval: 1e3
};
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
    clearBtn.addEventListener("click", () => {
      this.plugin.clearHistory();
      this.refresh();
    });
    const history = this.plugin.getHistory();
    if (history.length === 0) {
      container.createEl("p", { text: "No clipboard history yet. Start copying text!", cls: "ch-empty" });
      return;
    }
    const list = container.createDiv({ cls: "ch-list" });
    history.forEach((entry) => {
      const item = list.createDiv({ cls: "ch-item" });
      const meta = item.createDiv({ cls: "ch-meta" });
      meta.createEl("span", {
        text: new Date(entry.timestamp).toLocaleString(),
        cls: "ch-timestamp"
      });
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
    await this.loadSettings();
    this.initElectronClipboard();
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
      callback: () => {
        this.clearHistory();
        new import_obsidian.Notice("Clipboard history cleared.");
      }
    });
    this.addSettingTab(new ClipboardHistorySettingTab(this.app, this));
    this.startPolling();
  }
  onunload() {
    this.stopPolling();
  }
  initElectronClipboard() {
    try {
      const electron = require("electron");
      this.electronClipboard = electron.clipboard;
    } catch (e) {
      console.error("[ClipboardHistory] Failed to access Electron clipboard:", e);
    }
  }
  startPolling() {
    if (this.pollingTimer !== null)
      return;
    if (this.electronClipboard) {
      try {
        this.lastContent = this.electronClipboard.readText();
      } catch (_) {
      }
    }
    this.pollingTimer = window.setInterval(() => this.checkClipboard(), this.settings.pollingInterval);
  }
  stopPolling() {
    if (this.pollingTimer !== null) {
      window.clearInterval(this.pollingTimer);
      this.pollingTimer = null;
    }
  }
  checkClipboard() {
    if (!this.electronClipboard)
      return;
    try {
      const current = this.electronClipboard.readText();
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
    if (this.history.length > this.settings.maxHistorySize) {
      this.history.length = this.settings.maxHistorySize;
    }
    this.refreshView();
  }
  getHistory() {
    return this.history;
  }
  clearHistory() {
    this.history = [];
    this.refreshView();
  }
  removeEntry(id) {
    this.history = this.history.filter((e) => e.id !== id);
  }
  async saveEntryToFile(entry) {
    const dir = this.settings.saveDirectory;
    const ts = new Date(entry.timestamp).toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const path = (0, import_obsidian.normalizePath)(`${dir}/clipboard-${ts}.md`);
    if (!await this.app.vault.adapter.exists(dir)) {
      await this.app.vault.createFolder(dir);
    }
    let finalPath = path;
    let counter = 1;
    while (await this.app.vault.adapter.exists(finalPath)) {
      finalPath = (0, import_obsidian.normalizePath)(`${dir}/clipboard-${ts}-${counter++}.md`);
    }
    await this.app.vault.create(finalPath, entry.content);
    new import_obsidian.Notice(`Saved: ${finalPath}`);
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
  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }
  async saveSettings() {
    await this.saveData(this.settings);
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
    new import_obsidian.Setting(containerEl).setName("Max history size").setDesc("Maximum number of clipboard entries to keep in memory.").addText(
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
        this.plugin.settings.saveDirectory = value;
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
  }
};
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  VIEW_TYPE_CLIPBOARD
});
