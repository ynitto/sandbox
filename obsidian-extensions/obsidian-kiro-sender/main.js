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
  default: () => KiroSenderPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var import_child_process = require("child_process");
var DEFAULT_SETTINGS = {
  tmuxTarget: "kiro:0",
  wslDistribution: "",
  filePrefix: "@",
  defaultPremisePrompt: "",
  workspaces: []
};
var WorkspaceSelectModal = class extends import_obsidian.Modal {
  constructor(app, workspaces, fileName, onChoose, onSaveWorkspace) {
    super(app);
    this.workspaces = workspaces;
    this.fileName = fileName;
    this.onChoose = onChoose;
    this.onSaveWorkspace = onSaveWorkspace;
  }
  onOpen() {
    const { contentEl } = this;
    if (!contentEl) {
      this.fallbackDialog();
      return;
    }
    this.render();
  }
  fallbackDialog() {
    const labels = this.workspaces.map((e, i) => `${i + 1}. ${e.name || e.wslPath}`).join("\n");
    const prompt = this.workspaces.length > 0 ? `\u9001\u4FE1\u5148\u3092\u756A\u53F7\u3067\u9078\u629E\uFF080=\u30C7\u30D5\u30A9\u30EB\u30C8\uFF09:
0. \u30C7\u30D5\u30A9\u30EB\u30C8\u8A2D\u5B9A
${labels}` : "0 \u3092\u5165\u529B\u3057\u3066\u30C7\u30D5\u30A9\u30EB\u30C8\u8A2D\u5B9A\u3067\u9001\u4FE1:";
    const input = window.prompt(`Kiro \u306B\u9001\u4FE1: ${this.fileName}

${prompt}`, "0");
    if (input === null)
      return;
    const idx = parseInt(input, 10);
    if (idx === 0) {
      this.onChoose(null);
    } else if (idx >= 1 && idx <= this.workspaces.length) {
      this.onChoose(this.workspaces[idx - 1]);
    }
  }
  render() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: `Kiro \u306B\u9001\u4FE1: ${this.fileName}` });
    const addBtn = (label, onClick, isCta = false) => {
      const btn = contentEl.createEl("button", { text: label });
      btn.style.display = "block";
      btn.style.width = "100%";
      btn.style.marginBottom = "6px";
      btn.style.textAlign = "left";
      btn.style.padding = "8px 12px";
      btn.style.cursor = "pointer";
      if (isCta)
        btn.addClass("mod-cta");
      btn.addEventListener("click", () => {
        this.close();
        onClick();
      });
      return btn;
    };
    this.workspaces.forEach((entry) => {
      const label = entry.wslPath ? `${entry.name}  (${entry.wslPath})` : entry.name;
      addBtn(label, () => this.onChoose(entry), true);
    });
    contentEl.createEl("hr");
    addBtn("\u30C7\u30D5\u30A9\u30EB\u30C8\u8A2D\u5B9A\u3067\u9001\u4FE1", () => this.onChoose(null));
    contentEl.createEl("hr");
    contentEl.createEl("p", {
      text: "\u65B0\u3057\u3044\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\u3092\u8FFD\u52A0",
      attr: { style: "font-weight: bold; margin: 8px 0 4px;" }
    });
    const grid = contentEl.createDiv({ attr: { style: "display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-bottom:6px;" } });
    const nameInput = grid.createEl("input", { attr: { placeholder: "\u540D\u524D (\u4F8B: project-a)", style: "width:100%; padding:4px 8px;" } });
    const pathInput = grid.createEl("input", { attr: { placeholder: "WSL \u30D1\u30B9 (/home/...)", style: "width:100%; padding:4px 8px;" } });
    const tmuxInput = grid.createEl("input", { attr: { placeholder: "tmux \u30BF\u30FC\u30B2\u30C3\u30C8 (\u7A7A\u6B04=\u30B0\u30ED\u30FC\u30D0\u30EB)", style: "width:100%; padding:4px 8px;" } });
    const premiseInput = grid.createEl("input", { attr: { placeholder: "\u524D\u63D0\u30D7\u30ED\u30F3\u30D7\u30C8 (\u7A7A\u6B04=\u30B0\u30ED\u30FC\u30D0\u30EB)", style: "width:100%; padding:4px 8px;" } });
    const addNewBtn = contentEl.createEl("button", {
      text: "+ \u8FFD\u52A0\u3057\u3066\u9001\u4FE1",
      attr: { style: "width:100%; padding:8px 12px; cursor:pointer;" }
    });
    addNewBtn.addClass("mod-cta");
    addNewBtn.addEventListener("click", async () => {
      const name = nameInput.value.trim();
      const wslPath = pathInput.value.trim();
      if (!name) {
        nameInput.style.borderColor = "red";
        nameInput.focus();
        return;
      }
      const entry = {
        name,
        wslPath,
        tmuxTarget: tmuxInput.value.trim(),
        premisePrompt: premiseInput.value
      };
      await this.onSaveWorkspace(entry);
      this.close();
      this.onChoose(entry);
    });
  }
  onClose() {
    this.contentEl.empty();
  }
};
var KiroSenderPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.settings = DEFAULT_SETTINGS;
  }
  async onload() {
    await this.loadSettings();
    this.addCommand({
      id: "send-to-kiro",
      name: "Send current note to Kiro",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (file instanceof import_obsidian.TFile) {
          if (!checking) {
            this.triggerSend(file);
          }
          return true;
        }
        return false;
      }
    });
    this.registerEvent(
      this.app.workspace.on(
        "file-menu",
        (menu, abstractFile) => {
          if (!(abstractFile instanceof import_obsidian.TFile))
            return;
          const file = abstractFile;
          menu.addItem((item) => {
            item.setTitle("Kiro \u306B\u9001\u4FE1").setIcon("send").onClick(() => setTimeout(() => this.triggerSend(file), 50));
          });
        }
      )
    );
    this.addSettingTab(new KiroSenderSettingTab(this.app, this));
  }
  // ---------------------------------------------------------------------------
  // 送信フロー
  // ---------------------------------------------------------------------------
  /** 常にモーダルを表示してから送信 */
  triggerSend(file) {
    const { workspaces } = this.settings;
    new WorkspaceSelectModal(
      this.app,
      workspaces,
      file.name,
      (entry) => {
        this.sendToKiro(file, entry);
      },
      async (entry) => {
        this.settings.workspaces.push(entry);
        await this.saveSettings();
      }
    ).open();
  }
  /** 実際の tmux 送信処理 */
  async sendToKiro(file, workspace) {
    var _a, _b, _c, _d;
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof import_obsidian.FileSystemAdapter)) {
      new import_obsidian.Notice("Kiro Sender: FileSystemAdapter \u304C\u5229\u7528\u3067\u304D\u307E\u305B\u3093");
      return;
    }
    const isWindows = process.platform === "win32";
    const basePath = adapter.getBasePath();
    let filePath;
    if (isWindows) {
      const winAbsolute = `${basePath}\\${file.path.replace(/\//g, "\\")}`;
      filePath = toWslPath(winAbsolute);
    } else {
      filePath = `${basePath}/${file.path}`;
    }
    const tmuxTarget = ((_a = workspace == null ? void 0 : workspace.tmuxTarget) == null ? void 0 : _a.trim()) || this.settings.tmuxTarget;
    const premisePrompt = (_b = workspace == null ? void 0 : workspace.premisePrompt) != null ? _b : this.settings.defaultPremisePrompt;
    const workspacePath = (_d = (_c = workspace == null ? void 0 : workspace.wslPath) == null ? void 0 : _c.trim()) != null ? _d : "";
    const parts = [];
    if (premisePrompt.trim())
      parts.push(premisePrompt.trim());
    if (workspacePath)
      parts.push(`cd ${workspacePath} &&`);
    parts.push(`${this.settings.filePrefix}${filePath}`);
    const payload = parts.join(" ");
    let cmd;
    if (isWindows) {
      const wslBin = resolveWslBin();
      if (!wslBin) {
        new import_obsidian.Notice("Kiro Sender: wsl \u30B3\u30DE\u30F3\u30C9\u304C\u898B\u3064\u304B\u308A\u307E\u305B\u3093\u3002WSL2 \u3092\u30A4\u30F3\u30B9\u30C8\u30FC\u30EB\u3057\u3066\u304F\u3060\u3055\u3044\u3002", 8e3);
        return;
      }
      const wslPrefix = this.settings.wslDistribution ? `${wslBin} -d ${this.settings.wslDistribution}` : wslBin;
      cmd = `${wslPrefix} tmux send-keys -t "${tmuxTarget}" ${shellQuote(payload)} Enter`;
    } else {
      cmd = `tmux send-keys -t "${tmuxTarget}" ${shellQuote(payload)} Enter`;
    }
    try {
      (0, import_child_process.execSync)(cmd, { timeout: 5e3 });
      const label = workspace ? `[${workspace.name}] ` : "";
      new import_obsidian.Notice(`Kiro \u306B\u9001\u4FE1: ${label}${file.name}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`Kiro Sender \u30A8\u30E9\u30FC: ${msg}`, 6e3);
      console.error("[KiroSender] execSync error:", err);
    }
  }
  async loadSettings() {
    const loaded = await this.loadData();
    this.settings = Object.assign({}, DEFAULT_SETTINGS, loaded);
    if (!Array.isArray(this.settings.workspaces)) {
      this.settings.workspaces = [];
    }
  }
  async saveSettings() {
    await this.saveData(this.settings);
  }
};
var KiroSenderSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Kiro Sender \u8A2D\u5B9A" });
    containerEl.createEl("h3", { text: "\u30B0\u30ED\u30FC\u30D0\u30EB\u8A2D\u5B9A" });
    new import_obsidian.Setting(containerEl).setName("\u30C7\u30D5\u30A9\u30EB\u30C8 tmux \u30BF\u30FC\u30B2\u30C3\u30C8").setDesc("\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\u5074\u3067\u672A\u6307\u5B9A\u306E\u3068\u304D\u306B\u4F7F\u3046 tmux \u30BF\u30FC\u30B2\u30C3\u30C8\u3002\u4F8B: kiro:0 / multiagent:1.0").addText(
      (text) => text.setPlaceholder("kiro:0").setValue(this.plugin.settings.tmuxTarget).onChange(async (value) => {
        this.plugin.settings.tmuxTarget = value.trim();
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("WSL \u30C7\u30A3\u30B9\u30C8\u30EA\u30D3\u30E5\u30FC\u30B7\u30E7\u30F3").setDesc("wsl \u30B3\u30DE\u30F3\u30C9\u306B\u6E21\u3059\u30C7\u30A3\u30B9\u30C8\u30EA\u30D3\u30E5\u30FC\u30B7\u30E7\u30F3\u540D\uFF08\u7A7A\u6B04 = \u30C7\u30D5\u30A9\u30EB\u30C8\uFF09\u3002\u4F8B: Ubuntu-22.04").addText(
      (text) => text.setPlaceholder("Ubuntu").setValue(this.plugin.settings.wslDistribution).onChange(async (value) => {
        this.plugin.settings.wslDistribution = value.trim();
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("\u30D5\u30A1\u30A4\u30EB\u53C2\u7167\u30D7\u30EC\u30D5\u30A3\u30C3\u30AF\u30B9").setDesc('@\u30D5\u30A1\u30A4\u30EB\u53C2\u7167\u306E\u76F4\u524D\u306B\u4ED8\u3051\u308B\u6587\u5B57\u3002\u901A\u5E38\u306F "@" \u306E\u307E\u307E\u3002').addText(
      (text) => text.setPlaceholder("@").setValue(this.plugin.settings.filePrefix).onChange(async (value) => {
        this.plugin.settings.filePrefix = value;
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("\u30C7\u30D5\u30A9\u30EB\u30C8\u524D\u63D0\u30D7\u30ED\u30F3\u30D7\u30C8").setDesc(
      '\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\u5074\u3067\u672A\u6307\u5B9A\u306E\u3068\u304D @\u30D5\u30A1\u30A4\u30EB\u53C2\u7167\u306E\u524D\u306B\u4ED8\u3051\u308B\u30C6\u30AD\u30B9\u30C8\u3002\u4F8B: "\u4EE5\u4E0B\u306E\u6307\u793A\u3092\u5B9F\u884C\u3057\u3066\u304F\u3060\u3055\u3044"'
    ).addText(
      (text) => text.setPlaceholder("\u4EE5\u4E0B\u306E\u6307\u793A\u3092\u5B9F\u884C\u3057\u3066\u304F\u3060\u3055\u3044").setValue(this.plugin.settings.defaultPremisePrompt).onChange(async (value) => {
        this.plugin.settings.defaultPremisePrompt = value;
        await this.plugin.saveSettings();
      })
    );
    containerEl.createEl("h3", { text: "\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\uFF08\u4F5C\u696D\u30C7\u30A3\u30EC\u30AF\u30C8\u30EA\uFF09" });
    containerEl.createEl("p", {
      text: "\u8907\u6570\u306E\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\u3092\u767B\u9332\u3059\u308B\u3068\u3001\u9001\u4FE1\u6642\u306B\u30E2\u30FC\u30C0\u30EB\u3067\u9078\u629E\u3067\u304D\u307E\u3059\u30021\u4EF6\u306E\u307F\u306E\u5834\u5408\u306F\u81EA\u52D5\u9078\u629E\u3055\u308C\u307E\u3059\u3002",
      cls: "setting-item-description"
    });
    const workspaces = this.plugin.settings.workspaces;
    workspaces.forEach((entry, index) => {
      const entryEl = containerEl.createDiv({ cls: "kiro-workspace-entry" });
      entryEl.style.border = "1px solid var(--background-modifier-border)";
      entryEl.style.borderRadius = "6px";
      entryEl.style.padding = "12px";
      entryEl.style.marginBottom = "12px";
      const headerEl = entryEl.createDiv();
      headerEl.style.display = "flex";
      headerEl.style.justifyContent = "space-between";
      headerEl.style.alignItems = "center";
      headerEl.style.marginBottom = "8px";
      headerEl.createEl("strong", { text: `\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9 ${index + 1}` });
      const deleteBtn = headerEl.createEl("button", { text: "\u524A\u9664" });
      deleteBtn.addEventListener("click", async () => {
        this.plugin.settings.workspaces.splice(index, 1);
        await this.plugin.saveSettings();
        this.display();
      });
      new import_obsidian.Setting(entryEl).setName("\u540D\u524D").setDesc("\u9078\u629E\u30E2\u30FC\u30C0\u30EB\u306B\u8868\u793A\u3055\u308C\u308B\u77ED\u3044\u8B58\u5225\u540D").addText(
        (text) => text.setPlaceholder("project-a").setValue(entry.name).onChange(async (value) => {
          entry.name = value;
          await this.plugin.saveSettings();
        })
      );
      new import_obsidian.Setting(entryEl).setName("WSL \u30D1\u30B9").setDesc("\u4F5C\u696D\u30C7\u30A3\u30EC\u30AF\u30C8\u30EA\u306E WSL \u30D1\u30B9\u3002\u4F8B: /home/user/projects/foo").addText(
        (text) => text.setPlaceholder("/home/user/projects/foo").setValue(entry.wslPath).onChange(async (value) => {
          entry.wslPath = value.trim();
          await this.plugin.saveSettings();
        })
      );
      new import_obsidian.Setting(entryEl).setName("tmux \u30BF\u30FC\u30B2\u30C3\u30C8").setDesc("\u3053\u306E\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\u5C02\u7528\u306E\u30BF\u30FC\u30B2\u30C3\u30C8\uFF08\u7A7A\u6B04 = \u30B0\u30ED\u30FC\u30D0\u30EB\u8A2D\u5B9A\uFF09\u3002\u4F8B: kiro:0.1").addText(
        (text) => text.setPlaceholder("\uFF08\u30B0\u30ED\u30FC\u30D0\u30EB\u8A2D\u5B9A\u3092\u4F7F\u7528\uFF09").setValue(entry.tmuxTarget).onChange(async (value) => {
          entry.tmuxTarget = value.trim();
          await this.plugin.saveSettings();
        })
      );
      new import_obsidian.Setting(entryEl).setName("\u524D\u63D0\u30D7\u30ED\u30F3\u30D7\u30C8").setDesc("\u3053\u306E\u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\u5C02\u7528\u306E\u524D\u63D0\u30D7\u30ED\u30F3\u30D7\u30C8\uFF08\u7A7A\u6B04 = \u30B0\u30ED\u30FC\u30D0\u30EB\u306E\u30C7\u30D5\u30A9\u30EB\u30C8\u3092\u4F7F\u7528\uFF09").addText(
        (text) => text.setPlaceholder("\uFF08\u30B0\u30ED\u30FC\u30D0\u30EB\u306E\u30C7\u30D5\u30A9\u30EB\u30C8\u3092\u4F7F\u7528\uFF09").setValue(entry.premisePrompt).onChange(async (value) => {
          entry.premisePrompt = value;
          await this.plugin.saveSettings();
        })
      );
    });
    new import_obsidian.Setting(containerEl).addButton(
      (btn) => btn.setButtonText("+ \u30EF\u30FC\u30AF\u30B9\u30DA\u30FC\u30B9\u3092\u8FFD\u52A0").setCta().onClick(async () => {
        this.plugin.settings.workspaces.push({
          name: "",
          wslPath: "",
          tmuxTarget: "",
          premisePrompt: ""
        });
        await this.plugin.saveSettings();
        this.display();
      })
    );
  }
};
function resolveWslBin() {
  try {
    (0, import_child_process.execSync)("wsl --version", { windowsHide: true, timeout: 3e3, stdio: "ignore" });
    return "wsl";
  } catch (e) {
  }
  const fixed = "C:\\Windows\\System32\\wsl.exe";
  try {
    (0, import_child_process.execSync)(`"${fixed}" --version`, { windowsHide: true, timeout: 3e3, stdio: "ignore" });
    return `"${fixed}"`;
  } catch (e) {
    return null;
  }
}
function toWslPath(windowsPath) {
  return windowsPath.replace(/^([A-Za-z]):[\\\/]/, (_match, drive) => `/mnt/${drive.toLowerCase()}/`).replace(/\\/g, "/");
}
function shellQuote(str) {
  return `'${str.replace(/'/g, "'\\''")}'`;
}
