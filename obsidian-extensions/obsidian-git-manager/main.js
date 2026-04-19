"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
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
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// main.ts
var main_exports = {};
__export(main_exports, {
  default: () => GitManagerPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var fs = __toESM(require("fs"));
var nodePath = __toESM(require("path"));
var import_child_process = require("child_process");
var DEFAULT_DATA = {
  repositories: [],
  exportPath: "git-repositories.json",
  maxScanDepth: 5,
  insertTemplate: "{{value}}"
};
function getGitRemotes(repoPath) {
  try {
    const output = (0, import_child_process.execSync)("git remote -v", {
      cwd: repoPath,
      encoding: "utf8",
      timeout: 5e3
    });
    const seen = /* @__PURE__ */ new Map();
    for (const line of output.split("\n")) {
      const m = line.match(/^(\S+)\s+(\S+)\s+\(fetch\)/);
      if (m)
        seen.set(m[1], m[2]);
    }
    return Array.from(seen.entries()).map(([name, url]) => ({ name, url }));
  } catch (e) {
    return [];
  }
}
function getGitBranches(repoPath) {
  const local = [];
  const remote = [];
  try {
    const out = (0, import_child_process.execSync)("git branch", { cwd: repoPath, encoding: "utf8", timeout: 5e3 });
    for (const line of out.split("\n")) {
      const name = line.replace(/^\*?\s+/, "").trim();
      if (name)
        local.push(name);
    }
  } catch (e) {
  }
  try {
    const out = (0, import_child_process.execSync)("git branch -r", { cwd: repoPath, encoding: "utf8", timeout: 5e3 });
    for (const line of out.split("\n")) {
      if (line.includes("->"))
        continue;
      const name = line.trim();
      if (name)
        remote.push(name);
    }
  } catch (e) {
  }
  return { local, remote };
}
function isGitRepo(dirPath) {
  try {
    return fs.existsSync(nodePath.join(dirPath, ".git"));
  } catch (e) {
    return false;
  }
}
function buildRepository(repoPath) {
  const now = (/* @__PURE__ */ new Date()).toISOString();
  return {
    id: crypto.randomUUID(),
    path: repoPath,
    name: nodePath.basename(repoPath),
    remotes: getGitRemotes(repoPath),
    addedAt: now,
    lastUpdated: now
  };
}
function scanForRepoPaths(rootPath, maxDepth) {
  const found = [];
  function walk(dir, depth) {
    if (depth > maxDepth)
      return;
    if (isGitRepo(dir)) {
      found.push(dir);
      return;
    }
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (e) {
      return;
    }
    for (const entry of entries) {
      if (!entry.isDirectory())
        continue;
      if (entry.name.startsWith("."))
        continue;
      if (entry.name === "node_modules")
        continue;
      walk(nodePath.join(dir, entry.name), depth + 1);
    }
  }
  walk(rootPath, 0);
  return found;
}
function renderGitManagerBlock(source, el, plugin, ctx) {
  var _a;
  const config = {};
  for (const line of source.split("\n")) {
    const m = line.match(/^(\w+)\s*:\s*(.+)/);
    if (m)
      config[m[1].trim()] = m[2].trim();
  }
  const show = ((_a = config["show"]) != null ? _a : "all").toLowerCase();
  const repos = plugin.data.repositories;
  const container = el.createDiv({ cls: "git-manager-block" });
  container.style.cssText = "border:1px solid var(--background-modifier-border); border-radius:6px; padding:12px; font-size:0.9em;";
  if (repos.length === 0) {
    container.createEl("p", {
      text: "\u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u767B\u9332\u3055\u308C\u3066\u3044\u307E\u305B\u3093\u3002\u30D7\u30E9\u30B0\u30A4\u30F3\u8A2D\u5B9A\u304B\u3089\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u8FFD\u52A0\u3057\u3066\u304F\u3060\u3055\u3044\u3002",
      attr: { style: "color:var(--text-muted); margin:0;" }
    });
    return;
  }
  container.createEl("div", {
    text: "Git \u30EA\u30DD\u30B8\u30C8\u30EA",
    attr: { style: "font-weight:600; margin-bottom:8px; color:var(--text-normal);" }
  });
  const selectEl = container.createEl("select");
  selectEl.style.cssText = "width:100%; margin-bottom:10px; padding:4px 8px; background:var(--background-secondary); color:var(--text-normal); border:1px solid var(--background-modifier-border); border-radius:4px;";
  for (const repo of repos) {
    selectEl.createEl("option", { text: repo.name, value: repo.id });
  }
  const infoPanel = container.createDiv();
  async function insertBelowBlock(name, value) {
    const info = ctx.getSectionInfo(el);
    const file = plugin.app.vault.getAbstractFileByPath(ctx.sourcePath);
    if (!info || !(file instanceof import_obsidian.TFile))
      return;
    const text = plugin.data.insertTemplate.replace(/\{\{name\}\}/g, name).replace(/\{\{value\}\}/g, value);
    const content = await plugin.app.vault.read(file);
    const lines = content.split("\n");
    lines.splice(info.lineEnd + 1, 0, text);
    await plugin.app.vault.modify(file, lines.join("\n"));
  }
  function renderInfo(repoId) {
    infoPanel.empty();
    const repo = repos.find((r) => r.id === repoId);
    if (!repo)
      return;
    function makeInsertRow(name, value) {
      const row = infoPanel.createDiv({
        attr: { style: "display:flex; align-items:center; gap:8px; margin-bottom:6px;" }
      });
      row.createEl("span", {
        text: `${name}:`,
        attr: { style: "color:var(--text-muted); min-width:70px; flex-shrink:0;" }
      });
      row.createEl("code", {
        text: value,
        attr: { style: "flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" }
      });
      const btn = row.createEl("button", {
        text: "\u633F\u5165",
        attr: { style: "padding:2px 8px; font-size:0.85em; flex-shrink:0;" }
      });
      btn.addEventListener("click", async () => {
        await insertBelowBlock(name, value);
        btn.textContent = "\u2713";
        setTimeout(() => {
          btn.textContent = "\u633F\u5165";
        }, 1500);
      });
    }
    if (show === "all" || show === "folder") {
      makeInsertRow("\u30D5\u30A9\u30EB\u30C0", repo.path);
    }
    if (show === "all" || show === "remote") {
      if (repo.remotes.length === 0) {
        infoPanel.createEl("p", {
          text: "\u30EA\u30E2\u30FC\u30C8\u306A\u3057",
          attr: { style: "color:var(--text-muted); margin:4px 0;" }
        });
      } else {
        for (const remote of repo.remotes) {
          makeInsertRow(remote.name, remote.url);
        }
      }
    }
    if (show === "all" || show === "branch") {
      const branchRow = infoPanel.createDiv({
        attr: { style: "display:flex; align-items:center; gap:8px; margin-top:6px;" }
      });
      branchRow.createEl("span", {
        text: "\u30D6\u30E9\u30F3\u30C1:",
        attr: { style: "color:var(--text-muted); min-width:70px; flex-shrink:0;" }
      });
      const branchBtn = branchRow.createEl("button", {
        text: "\u30D6\u30E9\u30F3\u30C1\u3092\u9078\u629E",
        attr: { style: "padding:2px 8px; font-size:0.85em;" }
      });
      branchBtn.addEventListener("click", () => {
        const { local, remote } = getGitBranches(repo.path);
        const all = [...local, ...remote];
        if (all.length === 0) {
          new import_obsidian.Notice("\u30D6\u30E9\u30F3\u30C1\u304C\u898B\u3064\u304B\u308A\u307E\u305B\u3093\u3067\u3057\u305F");
          return;
        }
        new BranchSelectModal(plugin.app, all, async (branch) => {
          await insertBelowBlock("\u30D6\u30E9\u30F3\u30C1", branch);
        }).open();
      });
    }
  }
  renderInfo(repos[0].id);
  selectEl.addEventListener("change", (e) => {
    renderInfo(e.target.value);
  });
}
var BranchSelectModal = class extends import_obsidian.SuggestModal {
  constructor(app, branches, onSelect) {
    super(app);
    this.branches = branches;
    this.onSelect = onSelect;
    this.setPlaceholder("\u30D6\u30E9\u30F3\u30C1\u540D\u3092\u5165\u529B\uFF08\u524D\u65B9\u4E00\u81F4\u3067\u7D5E\u308A\u8FBC\u307F\uFF09");
  }
  getSuggestions(query) {
    if (!query)
      return this.branches;
    const q = query.toLowerCase();
    return this.branches.filter((b) => b.toLowerCase().startsWith(q));
  }
  renderSuggestion(branch, el) {
    el.setText(branch);
  }
  onChooseSuggestion(branch, _evt) {
    this.onSelect(branch);
  }
};
var ScanModal = class extends import_obsidian.Modal {
  constructor(app, plugin) {
    super(app);
    this.plugin = plugin;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.createEl("h3", { text: "\u30D5\u30A9\u30EB\u30C0\u3092\u30B9\u30AD\u30E3\u30F3" });
    new import_obsidian.Setting(contentEl).setName("\u30B9\u30AD\u30E3\u30F3\u3059\u308B\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9").setDesc("\u3053\u306E\u30D5\u30A9\u30EB\u30C0\u4EE5\u4E0B\u3092\u518D\u5E30\u7684\u306B\u8D70\u67FB\u3057\u3066 Git \u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u691C\u51FA\u3057\u307E\u3059").addText((t) => {
      t.setPlaceholder("/home/user/projects");
      this.inputEl = t.inputEl;
      this.inputEl.style.width = "100%";
    });
    new import_obsidian.Setting(contentEl).setName("\u6700\u5927\u63A2\u7D22\u6DF1\u5EA6").addText((t) => {
      t.setValue(String(this.plugin.data.maxScanDepth));
      t.inputEl.type = "number";
      t.inputEl.min = "1";
      t.inputEl.max = "10";
      this.depthEl = t.inputEl;
    });
    this.resultEl = contentEl.createEl("p", {
      attr: { style: "color:var(--text-muted); min-height:1.5em; margin:8px 0;" }
    });
    const btnRow = contentEl.createDiv({
      attr: { style: "display:flex; justify-content:flex-end; gap:8px; margin-top:16px;" }
    });
    btnRow.createEl("button", { text: "\u30AD\u30E3\u30F3\u30BB\u30EB" }).addEventListener("click", () => this.close());
    const scanBtn = btnRow.createEl("button", { text: "\u30B9\u30AD\u30E3\u30F3\u958B\u59CB", cls: "mod-cta" });
    scanBtn.addEventListener("click", () => {
      const rootPath = this.inputEl.value.trim();
      if (!rootPath) {
        this.resultEl.textContent = "\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044";
        return;
      }
      if (!fs.existsSync(rootPath)) {
        this.resultEl.textContent = `\u30D5\u30A9\u30EB\u30C0\u304C\u898B\u3064\u304B\u308A\u307E\u305B\u3093: ${rootPath}`;
        return;
      }
      const depth = parseInt(this.depthEl.value, 10) || this.plugin.data.maxScanDepth;
      this.resultEl.textContent = "\u30B9\u30AD\u30E3\u30F3\u4E2D...";
      scanBtn.disabled = true;
      setTimeout(async () => {
        const { added, found } = await this.plugin.scanAndRegister(rootPath, depth);
        this.resultEl.textContent = `${found} \u4EF6\u767A\u898B / ${added} \u4EF6\u3092\u65B0\u898F\u8FFD\u52A0\u3057\u307E\u3057\u305F`;
        scanBtn.disabled = false;
        if (added > 0)
          setTimeout(() => this.close(), 1500);
      }, 50);
    });
  }
  onClose() {
    this.contentEl.empty();
  }
};
var GitManagerSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Git Repository Manager" });
    containerEl.createEl("h3", { text: "\u633F\u5165\u30C6\u30F3\u30D7\u30EC\u30FC\u30C8" });
    containerEl.createEl("p", {
      text: "{{name}} = \u9805\u76EE\u540D\uFF08\u30D5\u30A9\u30EB\u30C0 / \u30EA\u30E2\u30FC\u30C8\u540D\uFF09\u3001{{value}} = \u9805\u76EE\u5024\uFF08\u30D1\u30B9 / URL\uFF09",
      attr: { style: "color:var(--text-muted); margin-bottom:8px;" }
    });
    new import_obsidian.Setting(containerEl).setName("\u30C6\u30F3\u30D7\u30EC\u30FC\u30C8").setDesc("\u30B3\u30FC\u30C9\u30D6\u30ED\u30C3\u30AF\u4E0B\u306B\u633F\u5165\u3059\u308B\u30C6\u30AD\u30B9\u30C8\u306E\u30C6\u30F3\u30D7\u30EC\u30FC\u30C8").addText((t) => {
      t.setPlaceholder("{{value}}").setValue(this.plugin.data.insertTemplate).onChange(async (v) => {
        this.plugin.data.insertTemplate = v || "{{value}}";
        await this.plugin.savePluginData();
      });
      t.inputEl.style.width = "300px";
    });
    containerEl.createEl("h3", { text: "\u30A8\u30AF\u30B9\u30DD\u30FC\u30C8\u8A2D\u5B9A", attr: { style: "margin-top:24px;" } });
    new import_obsidian.Setting(containerEl).setName("\u30A8\u30AF\u30B9\u30DD\u30FC\u30C8\u30D5\u30A1\u30A4\u30EB\u30D1\u30B9").setDesc("Vault \u5185\u306E\u30A8\u30AF\u30B9\u30DD\u30FC\u30C8\u5148 JSON \u30D5\u30A1\u30A4\u30EB\u30D1\u30B9\uFF08\u76F8\u5BFE\u30D1\u30B9\uFF09").addText(
      (t) => t.setPlaceholder("git-repositories.json").setValue(this.plugin.data.exportPath).onChange(async (v) => {
        this.plugin.data.exportPath = v.trim() || "git-repositories.json";
        await this.plugin.savePluginData();
      })
    ).addButton(
      (btn) => btn.setButtonText("\u30A8\u30AF\u30B9\u30DD\u30FC\u30C8").setCta().onClick(async () => {
        await this.plugin.exportToJson();
      })
    );
    containerEl.createEl("h3", { text: "\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u624B\u52D5\u8FFD\u52A0", attr: { style: "margin-top:24px;" } });
    containerEl.createEl("p", {
      text: "Git \u30EA\u30DD\u30B8\u30C8\u30EA\u306E\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9\uFF08\u7D76\u5BFE\u30D1\u30B9\uFF09\u3092\u5165\u529B\u3057\u3066\u8FFD\u52A0\u3057\u307E\u3059\u3002",
      attr: { style: "color:var(--text-muted); margin-bottom:8px;" }
    });
    let manualPath = "";
    new import_obsidian.Setting(containerEl).setName("\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9").addText((t) => {
      t.setPlaceholder("/path/to/repo").onChange((v) => {
        manualPath = v.trim();
      });
      t.inputEl.style.width = "300px";
    }).addButton(
      (btn) => btn.setButtonText("\u8FFD\u52A0").setCta().onClick(async () => {
        await this.plugin.addRepository(manualPath);
        this.display();
      })
    );
    containerEl.createEl("h3", { text: "\u30D5\u30A9\u30EB\u30C0\u3092\u81EA\u52D5\u30B9\u30AD\u30E3\u30F3", attr: { style: "margin-top:24px;" } });
    containerEl.createEl("p", {
      text: "\u6307\u5B9A\u30D5\u30A9\u30EB\u30C0\u4EE5\u4E0B\u3092\u518D\u5E30\u7684\u306B\u8D70\u67FB\u3057\u3066 Git \u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u81EA\u52D5\u691C\u51FA\u30FB\u767B\u9332\u3057\u307E\u3059\u3002",
      attr: { style: "color:var(--text-muted); margin-bottom:8px;" }
    });
    let scanPath = "";
    new import_obsidian.Setting(containerEl).setName("\u30B9\u30AD\u30E3\u30F3\u3059\u308B\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9").addText((t) => {
      t.setPlaceholder("/path/to/scan").onChange((v) => {
        scanPath = v.trim();
      });
      t.inputEl.style.width = "300px";
    }).addButton(
      (btn) => btn.setButtonText("\u30B9\u30AD\u30E3\u30F3").onClick(async () => {
        if (!scanPath) {
          new import_obsidian.Notice("\u30B9\u30AD\u30E3\u30F3\u3059\u308B\u30D5\u30A9\u30EB\u30C0\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
          return;
        }
        const { added, found } = await this.plugin.scanAndRegister(
          scanPath,
          this.plugin.data.maxScanDepth
        );
        new import_obsidian.Notice(`${found} \u4EF6\u767A\u898B / ${added} \u4EF6\u3092\u65B0\u898F\u8FFD\u52A0\u3057\u307E\u3057\u305F`);
        this.display();
      })
    );
    new import_obsidian.Setting(containerEl).setName("\u6700\u5927\u63A2\u7D22\u6DF1\u5EA6").setDesc("\u30B9\u30AD\u30E3\u30F3\u6642\u306B\u63A2\u7D22\u3059\u308B\u30D5\u30A9\u30EB\u30C0\u306E\u6700\u5927\u6DF1\u5EA6\uFF081\u301C10\uFF09").addSlider(
      (sl) => sl.setLimits(1, 10, 1).setValue(this.plugin.data.maxScanDepth).setDynamicTooltip().onChange(async (v) => {
        this.plugin.data.maxScanDepth = v;
        await this.plugin.savePluginData();
      })
    );
    const count = this.plugin.data.repositories.length;
    containerEl.createEl("h3", {
      text: `\u767B\u9332\u6E08\u307F\u30EA\u30DD\u30B8\u30C8\u30EA\uFF08${count} \u4EF6\uFF09`,
      attr: { style: "margin-top:24px;" }
    });
    if (count === 0) {
      containerEl.createEl("p", {
        text: "\u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u767B\u9332\u3055\u308C\u3066\u3044\u307E\u305B\u3093\u3002\u4E0A\u306E\u6A5F\u80FD\u3067\u8FFD\u52A0\u30FB\u30B9\u30AD\u30E3\u30F3\u3057\u3066\u304F\u3060\u3055\u3044\u3002",
        attr: { style: "color:var(--text-muted);" }
      });
      return;
    }
    new import_obsidian.Setting(containerEl).addButton(
      (btn) => btn.setButtonText("\u5168\u3066\u66F4\u65B0").setTooltip("\u5168\u30EA\u30DD\u30B8\u30C8\u30EA\u306E\u30EA\u30E2\u30FC\u30C8\u60C5\u5831\u3092\u518D\u53D6\u5F97").onClick(async () => {
        await this.plugin.refreshAllRepositories();
        this.display();
      })
    ).addButton(
      (btn) => btn.setButtonText("\u5168\u3066\u524A\u9664").setWarning().onClick(async () => {
        this.plugin.data.repositories = [];
        await this.plugin.savePluginData();
        this.display();
      })
    );
    for (const repo of this.plugin.data.repositories) {
      const remoteLines = repo.remotes.length > 0 ? repo.remotes.map((r) => `${r.name}: ${r.url}`).join("  |  ") : "\u30EA\u30E2\u30FC\u30C8\u306A\u3057";
      const desc = `${repo.path}
${remoteLines}`;
      new import_obsidian.Setting(containerEl).setName(repo.name).setDesc(desc).addButton(
        (btn) => btn.setIcon("refresh-cw").setTooltip("\u30EA\u30E2\u30FC\u30C8\u3092\u518D\u53D6\u5F97").onClick(async () => {
          await this.plugin.refreshRepository(repo.id);
          this.display();
        })
      ).addButton(
        (btn) => btn.setIcon("trash").setTooltip("\u524A\u9664").setWarning().onClick(async () => {
          this.plugin.data.repositories = this.plugin.data.repositories.filter(
            (r) => r.id !== repo.id
          );
          await this.plugin.savePluginData();
          this.display();
        })
      );
    }
  }
};
var GitManagerPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.data = { ...DEFAULT_DATA };
  }
  async onload() {
    const saved = await this.loadData();
    this.data = Object.assign({}, DEFAULT_DATA, saved);
    this.addSettingTab(new GitManagerSettingTab(this.app, this));
    this.registerMarkdownCodeBlockProcessor("git-manager", (source, el, ctx) => {
      renderGitManagerBlock(source, el, this, ctx);
    });
    this.addCommand({
      id: "export-to-json",
      name: "\u30EA\u30DD\u30B8\u30C8\u30EA\u60C5\u5831\u3092 JSON \u306B\u30A8\u30AF\u30B9\u30DD\u30FC\u30C8",
      callback: () => this.exportToJson()
    });
    this.addCommand({
      id: "scan-folder",
      name: "\u30D5\u30A9\u30EB\u30C0\u3092\u30B9\u30AD\u30E3\u30F3\u3057\u3066\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u81EA\u52D5\u767B\u9332",
      callback: () => new ScanModal(this.app, this).open()
    });
    this.addCommand({
      id: "refresh-all",
      name: "\u5168\u30EA\u30DD\u30B8\u30C8\u30EA\u306E\u30EA\u30E2\u30FC\u30C8\u60C5\u5831\u3092\u66F4\u65B0",
      callback: () => this.refreshAllRepositories()
    });
  }
  onunload() {
  }
  async savePluginData() {
    await this.saveData(this.data);
  }
  async addRepository(repoPath) {
    if (!repoPath) {
      new import_obsidian.Notice("\u30D1\u30B9\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
      return false;
    }
    if (!isGitRepo(repoPath)) {
      new import_obsidian.Notice(`Git \u30EA\u30DD\u30B8\u30C8\u30EA\u3067\u306F\u3042\u308A\u307E\u305B\u3093: ${repoPath}`);
      return false;
    }
    if (this.data.repositories.find((r) => r.path === repoPath)) {
      new import_obsidian.Notice(`\u65E2\u306B\u767B\u9332\u6E08\u307F\u3067\u3059: ${nodePath.basename(repoPath)}`);
      return false;
    }
    const repo = buildRepository(repoPath);
    this.data.repositories.push(repo);
    await this.savePluginData();
    new import_obsidian.Notice(`\u8FFD\u52A0\u3057\u307E\u3057\u305F: ${repo.name}\uFF08\u30EA\u30E2\u30FC\u30C8 ${repo.remotes.length} \u4EF6\uFF09`);
    return true;
  }
  async scanAndRegister(rootPath, maxDepth) {
    if (!fs.existsSync(rootPath)) {
      new import_obsidian.Notice(`\u30D5\u30A9\u30EB\u30C0\u304C\u898B\u3064\u304B\u308A\u307E\u305B\u3093: ${rootPath}`);
      return { found: 0, added: 0 };
    }
    const paths = scanForRepoPaths(rootPath, maxDepth);
    let added = 0;
    for (const p of paths) {
      if (!this.data.repositories.find((r) => r.path === p)) {
        this.data.repositories.push(buildRepository(p));
        added++;
      }
    }
    if (added > 0)
      await this.savePluginData();
    return { found: paths.length, added };
  }
  async refreshRepository(id) {
    const repo = this.data.repositories.find((r) => r.id === id);
    if (!repo)
      return;
    repo.remotes = getGitRemotes(repo.path);
    repo.lastUpdated = (/* @__PURE__ */ new Date()).toISOString();
    await this.savePluginData();
    new import_obsidian.Notice(`\u66F4\u65B0\u3057\u307E\u3057\u305F: ${repo.name}\uFF08\u30EA\u30E2\u30FC\u30C8 ${repo.remotes.length} \u4EF6\uFF09`);
  }
  async refreshAllRepositories() {
    const now = (/* @__PURE__ */ new Date()).toISOString();
    for (const repo of this.data.repositories) {
      repo.remotes = getGitRemotes(repo.path);
      repo.lastUpdated = now;
    }
    await this.savePluginData();
    new import_obsidian.Notice(`${this.data.repositories.length} \u4EF6\u306E\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u66F4\u65B0\u3057\u307E\u3057\u305F`);
  }
  async exportToJson() {
    const payload = {
      exportedAt: (/* @__PURE__ */ new Date()).toISOString(),
      count: this.data.repositories.length,
      repositories: this.data.repositories
    };
    const content = JSON.stringify(payload, null, 2);
    const filePath = this.data.exportPath;
    const existing = this.app.vault.getAbstractFileByPath(filePath);
    if (existing instanceof import_obsidian.TFile) {
      await this.app.vault.modify(existing, content);
    } else {
      await this.app.vault.create(filePath, content);
    }
    new import_obsidian.Notice(`\u30A8\u30AF\u30B9\u30DD\u30FC\u30C8\u3057\u307E\u3057\u305F: ${filePath}\uFF08${payload.count} \u4EF6\uFF09`);
  }
};
