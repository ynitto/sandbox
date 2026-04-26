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
  default: () => GitNestPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var import_child_process = require("child_process");
var import_fs = require("fs");
var import_path = require("path");
var DEFAULT_SETTINGS = {
  repos: []
};
function runGit(cwd, args) {
  return new Promise((resolve, reject) => {
    const proc = (0, import_child_process.spawn)("git", args, { cwd });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => {
      stdout += d.toString();
    });
    proc.stderr.on("data", (d) => {
      stderr += d.toString();
    });
    proc.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error(stderr.trim() || stdout.trim() || `git exited with code ${code}`));
      }
    });
    proc.on("error", reject);
  });
}
function addToGitIgnore(vaultPath, prefix) {
  const gitignorePath = (0, import_path.join)(vaultPath, ".gitignore");
  let content = (0, import_fs.existsSync)(gitignorePath) ? (0, import_fs.readFileSync)(gitignorePath, "utf-8") : "";
  const entry = prefix.endsWith("/") ? prefix : `${prefix}/`;
  const lines = content.split("\n").map((l) => l.trim());
  if (lines.includes(entry) || lines.includes(prefix))
    return;
  if (content && !content.endsWith("\n"))
    content += "\n";
  content += `${entry}
`;
  (0, import_fs.writeFileSync)(gitignorePath, content, "utf-8");
}
var AddRepoModal = class extends import_obsidian.Modal {
  constructor(app, plugin, initialPrefix = "") {
    super(app);
    this.plugin = plugin;
    this.initialPrefix = initialPrefix;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: "\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u8FFD\u52A0" });
    let prefix = this.initialPrefix;
    let remote = "";
    let remoteName = "origin";
    let branch = "main";
    let subdir = "";
    new import_obsidian.Setting(contentEl).setName("\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9").setDesc("vault root \u304B\u3089\u306E\u76F8\u5BFE\u30D1\u30B9\u3002\u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u30AF\u30ED\u30FC\u30F3\u3055\u308C\u308B\u30D5\u30A9\u30EB\u30C0").addText(
      (t) => t.setValue(prefix).onChange((v) => {
        prefix = v.trim();
      })
    );
    new import_obsidian.Setting(contentEl).setName("\u30EA\u30E2\u30FC\u30C8 URL").setDesc("\u30AF\u30ED\u30FC\u30F3\u3059\u308B git \u30EA\u30DD\u30B8\u30C8\u30EA\u306E URL").addText(
      (t) => t.setPlaceholder("https://github.com/user/repo.git").onChange((v) => {
        remote = v.trim();
      })
    );
    new import_obsidian.Setting(contentEl).setName("\u30EA\u30E2\u30FC\u30C8\u540D").setDesc("git remote \u306E\u77ED\u7E2E\u540D (\u30C7\u30D5\u30A9\u30EB\u30C8: origin)").addText(
      (t) => t.setValue(remoteName).onChange((v) => {
        remoteName = v.trim();
      })
    );
    new import_obsidian.Setting(contentEl).setName("\u30D6\u30E9\u30F3\u30C1").setDesc("\u540C\u671F\u3059\u308B\u30EA\u30E2\u30FC\u30C8\u30D6\u30E9\u30F3\u30C1\u540D").addText(
      (t) => t.setValue(branch).onChange((v) => {
        branch = v.trim();
      })
    );
    new import_obsidian.Setting(contentEl).setName("\u30B5\u30D6\u30D5\u30A9\u30EB\u30C0 (\u7701\u7565\u53EF)").setDesc("\u30EA\u30DD\u30B8\u30C8\u30EA\u5185\u3067\u4F7F\u7528\u3059\u308B\u30D5\u30A9\u30EB\u30C0\u30D1\u30B9\u3002\u7701\u7565\u6642\u306F\u30EA\u30DD\u30B8\u30C8\u30EA\u5168\u4F53\u3092\u4F7F\u7528").addText(
      (t) => t.setPlaceholder("docs").onChange((v) => {
        subdir = v.trim();
      })
    );
    new import_obsidian.Setting(contentEl).addButton(
      (btn) => btn.setButtonText("\u8FFD\u52A0").setCta().onClick(async () => {
        if (!prefix || !remote || !remoteName || !branch) {
          new import_obsidian.Notice("\u3059\u3079\u3066\u306E\u9805\u76EE\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
          return;
        }
        if (/\s/.test(remoteName)) {
          new import_obsidian.Notice("\u30EA\u30E2\u30FC\u30C8\u540D\u306B\u30B9\u30DA\u30FC\u30B9\u306F\u4F7F\u7528\u3067\u304D\u307E\u305B\u3093");
          return;
        }
        this.close();
        await this.plugin.addRepo({ prefix, remote, remoteName, branch, subdir: subdir || void 0 });
      })
    );
  }
  onClose() {
    this.contentEl.empty();
  }
};
var SelectRepoModal = class extends import_obsidian.Modal {
  constructor(app, repos, title, onSelect) {
    super(app);
    this.repos = repos;
    this.title = title;
    this.onSelect = onSelect;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: this.title });
    if (this.repos.length === 0) {
      contentEl.createEl("p", {
        text: "\u767B\u9332\u6E08\u307F\u306E\u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u3042\u308A\u307E\u305B\u3093\u3002\u5148\u306B\u8FFD\u52A0\u3057\u3066\u304F\u3060\u3055\u3044\u3002"
      });
      return;
    }
    this.repos.forEach((repo) => {
      const btn = contentEl.createEl("button");
      btn.style.cssText = "display:block;width:100%;margin-bottom:6px;text-align:left;padding:8px 12px;cursor:pointer;border-radius:4px;";
      const titleSpan = btn.createEl("span", { text: repo.prefix });
      titleSpan.style.fontWeight = "bold";
      btn.createEl("span", {
        text: `  ${repo.remoteName}/${repo.branch}`,
        attr: { style: "opacity:0.6;font-size:0.85em;margin-left:8px;" }
      });
      btn.addEventListener("click", () => {
        this.close();
        this.onSelect(repo);
      });
    });
  }
  onClose() {
    this.contentEl.empty();
  }
};
var SwitchBranchModal = class extends import_obsidian.Modal {
  constructor(app, plugin, repo) {
    super(app);
    this.plugin = plugin;
    this.repo = repo;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: `\u30D6\u30E9\u30F3\u30C1\u3092\u5207\u308A\u66FF\u3048: ${this.repo.prefix}` });
    contentEl.createEl("p", {
      text: `\u73FE\u5728\u306E\u30D6\u30E9\u30F3\u30C1: ${this.repo.branch}`,
      attr: { style: "opacity:0.7;margin-bottom:12px;" }
    });
    contentEl.createEl("p", {
      text: "\u4EE5\u964D\u306E pull/push \u3067\u540C\u671F\u3059\u308B\u30EA\u30E2\u30FC\u30C8\u30D6\u30E9\u30F3\u30C1\u3092\u5909\u66F4\u3057\u307E\u3059\u3002",
      attr: { style: "opacity:0.7;font-size:0.9em;margin-bottom:12px;" }
    });
    let branchName = "";
    new import_obsidian.Setting(contentEl).setName("\u30D6\u30E9\u30F3\u30C1\u540D").addText(
      (t) => t.setPlaceholder("develop").onChange((v) => {
        branchName = v.trim();
      })
    );
    new import_obsidian.Setting(contentEl).addButton(
      (btn) => btn.setButtonText("\u5207\u308A\u66FF\u3048").setCta().onClick(async () => {
        if (!branchName) {
          new import_obsidian.Notice("\u30D6\u30E9\u30F3\u30C1\u540D\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
          return;
        }
        this.close();
        await this.plugin.switchBranch(this.repo, branchName);
      })
    );
  }
  onClose() {
    this.contentEl.empty();
  }
};
var GitRepoModal = class extends import_obsidian.Modal {
  constructor(app, plugin, repo) {
    super(app);
    this.plugin = plugin;
    this.repo = repo;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    const titleRow = contentEl.createDiv({ attr: { style: "display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;" } });
    titleRow.createEl("h3", { text: `Git \u64CD\u4F5C: ${this.repo.prefix}`, attr: { style: "margin:0;" } });
    const vscodeBtn = titleRow.createEl("button", { text: "VSCode \u3067\u958B\u304F" });
    vscodeBtn.style.cssText = "font-size:0.85em;cursor:pointer;";
    vscodeBtn.addEventListener("click", () => this.plugin.openInVSCode(this.repo));
    const infoLine = contentEl.createEl("p", {
      attr: { style: "opacity:0.6;font-size:0.85em;margin-bottom:12px;" }
    });
    infoLine.createEl("span", { text: "\u73FE\u5728\u306E\u30D6\u30E9\u30F3\u30C1: " });
    const branchCode = infoLine.createEl("code", { text: "\u8AAD\u307F\u8FBC\u307F\u4E2D..." });
    infoLine.createEl("span", { text: `  |  \u30EA\u30E2\u30FC\u30C8: ${this.repo.remoteName}` });
    contentEl.createEl("h4", { text: "\u30B3\u30DF\u30C3\u30C8\u5C65\u6B74", attr: { style: "margin:12px 0 4px;" } });
    const commitLogEl = contentEl.createEl("pre", {
      text: "\u8AAD\u307F\u8FBC\u307F\u4E2D...",
      attr: {
        style: "max-height:120px;overflow-y:auto;font-size:0.78em;background:var(--background-secondary);padding:8px;border-radius:4px;margin:0;white-space:pre-wrap;"
      }
    });
    contentEl.createEl("h4", { text: "Git \u30B3\u30DE\u30F3\u30C9\u30ED\u30B0", attr: { style: "margin:12px 0 4px;" } });
    const cmdLogEl = contentEl.createEl("pre", {
      attr: {
        style: "max-height:100px;overflow-y:auto;font-size:0.78em;background:var(--background-secondary);padding:8px;border-radius:4px;margin:0;white-space:pre-wrap;"
      }
    });
    const logLines = this.plugin.commandLog;
    cmdLogEl.setText(logLines.length > 0 ? [...logLines].reverse().join("\n") : "(\u307E\u3060\u5B9F\u884C\u3055\u308C\u305F\u30B3\u30DE\u30F3\u30C9\u306F\u3042\u308A\u307E\u305B\u3093)");
    contentEl.createEl("h4", { text: "\u540C\u671F", attr: { style: "margin-top:16px;" } });
    new import_obsidian.Setting(contentEl).setName("Pull").setDesc(`${this.repo.remoteName}/${this.repo.branch} \u304B\u3089\u6700\u65B0\u3092\u53D6\u5F97`).addButton(
      (btn) => btn.setButtonText("Pull").setIcon("download").onClick(async () => {
        this.close();
        await this.plugin.pullRepo(this.repo);
      })
    );
    new import_obsidian.Setting(contentEl).setName("Push").setDesc(`${this.repo.remoteName}/${this.repo.branch} \u3078\u5909\u66F4\u3092\u9001\u4FE1`).addButton(
      (btn) => btn.setButtonText("Push").setIcon("upload").onClick(async () => {
        this.close();
        await this.plugin.pushRepo(this.repo);
      })
    );
    new import_obsidian.Setting(contentEl).setName("\u30B9\u30BF\u30C3\u30B7\u30E5\u3057\u3066 Pull").setDesc("\u73FE\u5728\u306E\u5909\u66F4\u3092\u30B9\u30BF\u30C3\u30B7\u30E5\u3057\u3001\u6700\u65B0\u3092 pull \u3057\u3066\u30B9\u30BF\u30C3\u30B7\u30E5\u3092\u623B\u3057\u307E\u3059").addButton(
      (btn) => btn.setButtonText("Stash & Pull").onClick(async () => {
        this.close();
        await this.plugin.pullWithStash(this.repo);
      })
    );
    contentEl.createEl("h4", { text: "\u30B3\u30DF\u30C3\u30C8 & Push", attr: { style: "margin-top:16px;" } });
    let commitMessage = "";
    const commitSetting = new import_obsidian.Setting(contentEl).setName("\u30B3\u30DF\u30C3\u30C8\u30E1\u30C3\u30BB\u30FC\u30B8").addTextArea((t) => {
      t.setPlaceholder("\u30B3\u30DF\u30C3\u30C8\u30E1\u30C3\u30BB\u30FC\u30B8\u3092\u5165\u529B...").onChange((v) => {
        commitMessage = v;
      });
      t.inputEl.style.cssText = "width:100%;min-height:60px;";
      return t;
    });
    commitSetting.settingEl.style.flexDirection = "column";
    commitSetting.settingEl.style.alignItems = "flex-start";
    new import_obsidian.Setting(contentEl).addButton(
      (btn) => btn.setButtonText("Commit & Push").setCta().onClick(async () => {
        if (!commitMessage.trim()) {
          new import_obsidian.Notice("\u30B3\u30DF\u30C3\u30C8\u30E1\u30C3\u30BB\u30FC\u30B8\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
          return;
        }
        this.close();
        await this.plugin.commitAndPush(this.repo, commitMessage.trim());
      })
    );
    contentEl.createEl("h4", { text: "\u30D6\u30E9\u30F3\u30C1\u64CD\u4F5C", attr: { style: "margin-top:16px;" } });
    let branchName = "";
    const branchSetting = new import_obsidian.Setting(contentEl).setName("\u30D6\u30E9\u30F3\u30C1\u540D").addText(
      (t) => t.setPlaceholder("branch-name").onChange((v) => {
        branchName = v.trim();
      })
    );
    branchSetting.settingEl.style.marginBottom = "0";
    new import_obsidian.Setting(contentEl).setName("\u65E2\u5B58\u30D6\u30E9\u30F3\u30C1\u3078\u5207\u308A\u66FF\u3048").setDesc("git checkout \u3067\u30D6\u30E9\u30F3\u30C1\u3092\u5207\u308A\u66FF\u3048\u307E\u3059").addButton(
      (btn) => btn.setButtonText("\u30C1\u30A7\u30C3\u30AF\u30A2\u30A6\u30C8").onClick(async () => {
        if (!branchName) {
          new import_obsidian.Notice("\u30D6\u30E9\u30F3\u30C1\u540D\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
          return;
        }
        this.close();
        await this.plugin.checkoutBranch(this.repo, branchName);
      })
    );
    new import_obsidian.Setting(contentEl).setName("\u65B0\u898F\u30D6\u30E9\u30F3\u30C1\u3092\u4F5C\u6210\u3057\u3066 Push").setDesc("\u65B0\u3057\u3044\u30D6\u30E9\u30F3\u30C1\u3092\u4F5C\u6210\u3057\u3066\u30EA\u30E2\u30FC\u30C8\u306B push \u3057\u307E\u3059").addButton(
      (btn) => btn.setButtonText("\u4F5C\u6210 & Push").setCta().onClick(async () => {
        if (!branchName) {
          new import_obsidian.Notice("\u30D6\u30E9\u30F3\u30C1\u540D\u3092\u5165\u529B\u3057\u3066\u304F\u3060\u3055\u3044");
          return;
        }
        this.close();
        await this.plugin.createAndPushBranch(this.repo, branchName);
      })
    );
    this.loadGitInfo(branchCode, commitLogEl);
  }
  async loadGitInfo(branchEl, logEl) {
    const destPath = (0, import_path.join)(this.plugin.getVaultPath(), this.repo.prefix);
    try {
      const branch = await runGit(destPath, ["branch", "--show-current"]);
      branchEl.setText(branch || this.repo.branch);
    } catch (e) {
      branchEl.setText(`${this.repo.branch} (config)`);
    }
    try {
      const log = await runGit(destPath, ["log", "--oneline", "-15"]);
      logEl.setText(log || "(\u30B3\u30DF\u30C3\u30C8\u306A\u3057)");
    } catch (e) {
      logEl.setText("(\u53D6\u5F97\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F)");
    }
  }
  onClose() {
    this.contentEl.empty();
  }
};
var GitNestPlugin = class extends import_obsidian.Plugin {
  constructor() {
    super(...arguments);
    this.settings = DEFAULT_SETTINGS;
    this.commandLog = [];
  }
  logCmd(cmd, result) {
    const t = (/* @__PURE__ */ new Date()).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    this.commandLog.push(`[${t}] ${cmd}  \u2192  ${result}`);
    if (this.commandLog.length > 100)
      this.commandLog.shift();
  }
  async runGitAndLog(cwd, args) {
    const cmd = `git ${args.join(" ")}`;
    try {
      const out = await runGit(cwd, args);
      this.logCmd(cmd, out || "OK");
      return out;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.logCmd(cmd, `Error: ${msg}`);
      throw err;
    }
  }
  async onload() {
    await this.loadSettings();
    this.addCommand({
      id: "add-repo",
      name: "\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u8FFD\u52A0",
      callback: () => new AddRepoModal(this.app, this).open()
    });
    this.addCommand({
      id: "pull-repo",
      name: "\u30EA\u30DD\u30B8\u30C8\u30EA\u3092 pull (\u9078\u629E)",
      callback: () => new SelectRepoModal(
        this.app,
        this.settings.repos,
        "Pull \u3059\u308B\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u9078\u629E",
        (repo) => this.pullRepo(repo)
      ).open()
    });
    this.addCommand({
      id: "pull-all-repos",
      name: "\u3059\u3079\u3066\u306E\u30EA\u30DD\u30B8\u30C8\u30EA\u3092 pull",
      callback: () => this.pullAllRepos()
    });
    this.addCommand({
      id: "push-repo",
      name: "\u30EA\u30DD\u30B8\u30C8\u30EA\u3092 push (\u9078\u629E)",
      callback: () => new SelectRepoModal(
        this.app,
        this.settings.repos,
        "Push \u3059\u308B\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u9078\u629E",
        (repo) => this.pushRepo(repo)
      ).open()
    });
    this.addCommand({
      id: "push-all-repos",
      name: "\u3059\u3079\u3066\u306E\u30EA\u30DD\u30B8\u30C8\u30EA\u3092 push",
      callback: () => this.pushAllRepos()
    });
    this.addCommand({
      id: "switch-repo-branch",
      name: "\u30EA\u30DD\u30B8\u30C8\u30EA\u306E\u30D6\u30E9\u30F3\u30C1\u3092\u5207\u308A\u66FF\u3048",
      callback: () => new SelectRepoModal(
        this.app,
        this.settings.repos,
        "\u30D6\u30E9\u30F3\u30C1\u3092\u5207\u308A\u66FF\u3048\u308B\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u9078\u629E",
        (repo) => new SwitchBranchModal(this.app, this, repo).open()
      ).open()
    });
    this.registerEvent(
      this.app.workspace.on(
        "file-menu",
        (menu, abstractFile) => {
          if (!(abstractFile instanceof import_obsidian.TFolder))
            return;
          const folderPath = abstractFile.path;
          const existing = this.settings.repos.find(
            (repo) => repo.prefix === folderPath || repo.subdir && `${repo.prefix}/${repo.subdir}` === folderPath
          );
          if (existing) {
            menu.addItem(
              (item) => item.setTitle("Git: \u7BA1\u7406").setIcon("git-branch").onClick(() => new GitRepoModal(this.app, this, existing).open())
            );
          } else {
            menu.addItem(
              (item) => item.setTitle("Git Nest: \u30EA\u30DD\u30B8\u30C8\u30EA\u3068\u3057\u3066\u8FFD\u52A0").setIcon("git-pull-request").onClick(() => new AddRepoModal(this.app, this, folderPath).open())
            );
          }
        }
      )
    );
    this.addSettingTab(new GitNestSettingTab(this.app, this));
  }
  // ---------------------------------------------------------------------------
  // vault パス取得
  // ---------------------------------------------------------------------------
  getVaultPath() {
    const adapter = this.app.vault.adapter;
    if (adapter instanceof import_obsidian.FileSystemAdapter) {
      return adapter.getBasePath();
    }
    throw new Error("FileSystemAdapter \u304C\u5229\u7528\u3067\u304D\u307E\u305B\u3093");
  }
  // ---------------------------------------------------------------------------
  // リポジトリ操作
  // ---------------------------------------------------------------------------
  async addRepo(entry) {
    const vaultPath = this.getVaultPath();
    const destPath = (0, import_path.join)(vaultPath, entry.prefix);
    new import_obsidian.Notice(`"${entry.prefix}" \u3092\u30AF\u30ED\u30FC\u30F3\u3057\u3066\u3044\u307E\u3059...`);
    try {
      if ((0, import_fs.existsSync)((0, import_path.join)(destPath, ".git"))) {
        new import_obsidian.Notice(`"${entry.prefix}" \u306B\u306F\u3059\u3067\u306B git \u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u5B58\u5728\u3057\u307E\u3059`);
      } else {
        await this.runGitAndLog(vaultPath, ["clone", "--origin", entry.remoteName, entry.remote, entry.prefix]);
      }
      addToGitIgnore(vaultPath, entry.prefix);
      this.settings.repos.push(entry);
      await this.saveSettings();
      new import_obsidian.Notice(`"${entry.prefix}" \u3092\u8FFD\u52A0\u3057\u307E\u3057\u305F`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`\u30EA\u30DD\u30B8\u30C8\u30EA\u306E\u8FFD\u52A0\u306B\u5931\u6557\u3057\u307E\u3057\u305F:
${msg}`, 8e3);
    }
  }
  async pullRepo(entry) {
    const destPath = (0, import_path.join)(this.getVaultPath(), entry.prefix);
    new import_obsidian.Notice(`"${entry.prefix}" \u3092 pull \u3057\u3066\u3044\u307E\u3059...`);
    try {
      await this.runGitAndLog(destPath, ["pull", entry.remoteName, entry.branch]);
      new import_obsidian.Notice(`"${entry.prefix}" \u306E pull \u304C\u5B8C\u4E86\u3057\u307E\u3057\u305F`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`pull \u306B\u5931\u6557\u3057\u307E\u3057\u305F:
${msg}`, 8e3);
    }
  }
  async pullAllRepos() {
    if (this.settings.repos.length === 0) {
      new import_obsidian.Notice("\u767B\u9332\u6E08\u307F\u306E\u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u3042\u308A\u307E\u305B\u3093");
      return;
    }
    for (const repo of this.settings.repos) {
      await this.pullRepo(repo);
    }
  }
  async pushRepo(entry) {
    const destPath = (0, import_path.join)(this.getVaultPath(), entry.prefix);
    new import_obsidian.Notice(`"${entry.prefix}" \u3092 push \u3057\u3066\u3044\u307E\u3059...`);
    try {
      await this.runGitAndLog(destPath, ["push", entry.remoteName, entry.branch]);
      new import_obsidian.Notice(`"${entry.prefix}" \u306E push \u304C\u5B8C\u4E86\u3057\u307E\u3057\u305F`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`push \u306B\u5931\u6557\u3057\u307E\u3057\u305F:
${msg}`, 8e3);
    }
  }
  async pushAllRepos() {
    if (this.settings.repos.length === 0) {
      new import_obsidian.Notice("\u767B\u9332\u6E08\u307F\u306E\u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u3042\u308A\u307E\u305B\u3093");
      return;
    }
    for (const repo of this.settings.repos) {
      await this.pushRepo(repo);
    }
  }
  async switchBranch(entry, newBranch) {
    const idx = this.settings.repos.findIndex((repo) => repo.prefix === entry.prefix);
    if (idx === -1)
      return;
    const oldBranch = this.settings.repos[idx].branch;
    this.settings.repos[idx].branch = newBranch;
    await this.saveSettings();
    new import_obsidian.Notice(`"${entry.prefix}" \u306E\u30D6\u30E9\u30F3\u30C1\u3092 "${oldBranch}" \u304B\u3089 "${newBranch}" \u306B\u5909\u66F4\u3057\u307E\u3057\u305F`);
  }
  async pullWithStash(entry) {
    const destPath = (0, import_path.join)(this.getVaultPath(), entry.prefix);
    new import_obsidian.Notice(`"${entry.prefix}" \u306E\u5909\u66F4\u3092\u30B9\u30BF\u30C3\u30B7\u30E5\u3057\u3066\u3044\u307E\u3059...`);
    try {
      const stashOut = await this.runGitAndLog(destPath, ["stash"]);
      const hasStash = !stashOut.includes("No local changes");
      new import_obsidian.Notice(`"${entry.prefix}" \u3092 pull \u3057\u3066\u3044\u307E\u3059...`);
      await this.runGitAndLog(destPath, ["pull", entry.remoteName, entry.branch]);
      if (hasStash) {
        new import_obsidian.Notice(`"${entry.prefix}" \u306E\u30B9\u30BF\u30C3\u30B7\u30E5\u3092\u623B\u3057\u3066\u3044\u307E\u3059...`);
        await this.runGitAndLog(destPath, ["stash", "pop"]);
      }
      new import_obsidian.Notice(`"${entry.prefix}" \u306E pull \u304C\u5B8C\u4E86\u3057\u307E\u3057\u305F`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`\u30B9\u30BF\u30C3\u30B7\u30E5 pull \u306B\u5931\u6557\u3057\u307E\u3057\u305F:
${msg}`, 8e3);
    }
  }
  async checkoutBranch(entry, branchName) {
    const destPath = (0, import_path.join)(this.getVaultPath(), entry.prefix);
    try {
      await this.runGitAndLog(destPath, ["checkout", branchName]);
      const idx = this.settings.repos.findIndex((r) => r.prefix === entry.prefix);
      if (idx !== -1) {
        this.settings.repos[idx].branch = branchName;
        await this.saveSettings();
      }
      new import_obsidian.Notice(`"${entry.prefix}" \u3092 "${branchName}" \u306B\u5207\u308A\u66FF\u3048\u307E\u3057\u305F`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`\u30D6\u30E9\u30F3\u30C1\u306E\u5207\u308A\u66FF\u3048\u306B\u5931\u6557\u3057\u307E\u3057\u305F:
${msg}`, 8e3);
    }
  }
  async createAndPushBranch(entry, branchName) {
    const destPath = (0, import_path.join)(this.getVaultPath(), entry.prefix);
    try {
      await this.runGitAndLog(destPath, ["checkout", "-b", branchName]);
      new import_obsidian.Notice(`"${branchName}" \u30D6\u30E9\u30F3\u30C1\u3092\u4F5C\u6210\u3057\u307E\u3057\u305F\u3002push \u3057\u3066\u3044\u307E\u3059...`);
      await this.runGitAndLog(destPath, ["push", "-u", entry.remoteName, branchName]);
      const idx = this.settings.repos.findIndex((r) => r.prefix === entry.prefix);
      if (idx !== -1) {
        this.settings.repos[idx].branch = branchName;
        await this.saveSettings();
      }
      new import_obsidian.Notice(`"${entry.prefix}" \u306E\u65B0\u30D6\u30E9\u30F3\u30C1 "${branchName}" \u3092 push \u3057\u307E\u3057\u305F`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`\u30D6\u30E9\u30F3\u30C1\u306E\u4F5C\u6210\u30FBpush \u306B\u5931\u6557\u3057\u307E\u3057\u305F:
${msg}`, 8e3);
    }
  }
  openInVSCode(entry) {
    const destPath = (0, import_path.join)(this.getVaultPath(), entry.prefix);
    const proc = (0, import_child_process.spawn)("code", [destPath], { detached: true, stdio: "ignore" });
    proc.unref();
  }
  async commitAndPush(entry, message) {
    const destPath = (0, import_path.join)(this.getVaultPath(), entry.prefix);
    new import_obsidian.Notice(`"${entry.prefix}" \u3092\u30B3\u30DF\u30C3\u30C8\u3057\u3066\u3044\u307E\u3059...`);
    try {
      await this.runGitAndLog(destPath, ["add", "-A"]);
      await this.runGitAndLog(destPath, ["commit", "-m", message]);
      new import_obsidian.Notice(`"${entry.prefix}" \u3092 push \u3057\u3066\u3044\u307E\u3059...`);
      await this.runGitAndLog(destPath, ["push", entry.remoteName, entry.branch]);
      new import_obsidian.Notice(`"${entry.prefix}" \u306E\u30B3\u30DF\u30C3\u30C8\u30FBpush \u304C\u5B8C\u4E86\u3057\u307E\u3057\u305F`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      new import_obsidian.Notice(`\u30B3\u30DF\u30C3\u30C8\u30FBpush \u306B\u5931\u6557\u3057\u307E\u3057\u305F:
${msg}`, 8e3);
    }
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
};
var GitNestSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Git Nest \u8A2D\u5B9A" });
    containerEl.createEl("h3", { text: "\u767B\u9332\u6E08\u307F\u30EA\u30DD\u30B8\u30C8\u30EA" });
    if (this.plugin.settings.repos.length === 0) {
      containerEl.createEl("p", {
        text: "\u767B\u9332\u6E08\u307F\u306E\u30EA\u30DD\u30B8\u30C8\u30EA\u304C\u3042\u308A\u307E\u305B\u3093\u3002\u30B3\u30DE\u30F3\u30C9\u30D1\u30EC\u30C3\u30C8\u304B\u3001\u30D5\u30A9\u30EB\u30C0\u306E\u53F3\u30AF\u30EA\u30C3\u30AF\u30E1\u30CB\u30E5\u30FC\u304B\u3089\u8FFD\u52A0\u3057\u3066\u304F\u3060\u3055\u3044\u3002",
        attr: { style: "opacity:0.7;" }
      });
    }
    this.plugin.settings.repos.forEach((repo, idx) => {
      const card = containerEl.createDiv();
      card.style.cssText = "border:1px solid var(--background-modifier-border);border-radius:6px;padding:12px;margin-bottom:12px;";
      card.createEl("strong", { text: repo.prefix });
      const rows = [
        ["Remote URL", repo.remote],
        ["Remote \u540D", repo.remoteName],
        ["Branch", repo.branch],
        ...repo.subdir ? [["\u30B5\u30D6\u30D5\u30A9\u30EB\u30C0", repo.subdir]] : []
      ];
      rows.forEach(([label, value]) => {
        const p = card.createEl("p");
        p.style.margin = "4px 0";
        p.createEl("span", {
          text: `${label}: `,
          attr: { style: "opacity:0.6;font-size:0.85em;" }
        });
        p.createEl("code", { text: value });
      });
      const actions = card.createDiv({ attr: { style: "margin-top:10px;display:flex;gap:8px;" } });
      const pullBtn = actions.createEl("button", { text: "Pull" });
      pullBtn.addEventListener("click", () => this.plugin.pullRepo(repo));
      const pushBtn = actions.createEl("button", { text: "Push" });
      pushBtn.addEventListener("click", () => this.plugin.pushRepo(repo));
      const switchBtn = actions.createEl("button", { text: "\u30D6\u30E9\u30F3\u30C1\u5207\u308A\u66FF\u3048" });
      switchBtn.addEventListener(
        "click",
        () => new SwitchBranchModal(this.plugin.app, this.plugin, repo).open()
      );
      const removeBtn = actions.createEl("button", { text: "\u524A\u9664" });
      removeBtn.style.color = "var(--text-error)";
      removeBtn.style.marginLeft = "auto";
      removeBtn.addEventListener("click", async () => {
        this.plugin.settings.repos.splice(idx, 1);
        await this.plugin.saveSettings();
        this.display();
      });
    });
    new import_obsidian.Setting(containerEl).addButton(
      (btn) => btn.setButtonText("\u30EA\u30DD\u30B8\u30C8\u30EA\u3092\u8FFD\u52A0").setCta().onClick(() => new AddRepoModal(this.plugin.app, this.plugin).open())
    );
  }
};
