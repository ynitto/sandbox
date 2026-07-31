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
  default: () => KiroChatPlugin
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var import_child_process = require("child_process");
var VIEW_TYPE_KIRO = "kiro-chat-view";
var DEFAULT_SETTINGS = {
  kiroPath: "kiro-cli",
  workingDirectory: ""
};
var KiroAcpClient = class {
  constructor(kiroPath, cwd) {
    this.kiroPath = kiroPath;
    this.cwd = cwd;
    this.proc = null;
    this.nextId = 0;
    this.pending = /* @__PURE__ */ new Map();
    this.buf = "";
    this.sessionId = null;
    // session/prompt は JSON-RPC レスポンスが来ないため turn_end で解決する
    this.turnPending = null;
    this.onText = null;
    this.onTurnEnd = null;
    this.onDisconnect = null;
    this.onStderr = null;
  }
  /** kiro-cli acp を起動し ACP ハンドシェイクを完了する */
  async connect() {
    var _a;
    let proc;
    if (import_obsidian.Platform.isWin) {
      proc = (0, import_child_process.spawn)(this.kiroPath, ["acp"], {
        cwd: this.cwd || void 0,
        stdio: ["pipe", "pipe", "pipe"],
        shell: true,
        windowsHide: true
      });
    } else {
      const home = (_a = process.env.HOME) != null ? _a : "";
      const extraPaths = [
        home && `${home}/.local/bin`,
        home && `${home}/.kiro/bin`,
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin"
      ].filter(Boolean);
      const env = {
        ...process.env,
        PATH: [...extraPaths, process.env.PATH].filter(Boolean).join(":")
      };
      proc = (0, import_child_process.spawn)(this.kiroPath, ["acp"], {
        cwd: this.cwd || void 0,
        stdio: ["pipe", "pipe", "pipe"],
        env
      });
    }
    this.proc = proc;
    this.proc.stdout.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk) => {
      this.buf += chunk;
      this.flush();
    });
    this.proc.stderr.setEncoding("utf8");
    this.proc.stderr.on("data", (data) => {
      var _a2;
      console.warn("[kiro-acp] stderr:", data);
      for (const line of data.split("\n")) {
        const t = line.trim();
        if (t)
          (_a2 = this.onStderr) == null ? void 0 : _a2.call(this, t);
      }
    });
    this.proc.on("error", (err) => {
      var _a2;
      this.rejectAllPending(err);
      this.proc = null;
      this.sessionId = null;
      (_a2 = this.onDisconnect) == null ? void 0 : _a2.call(this);
    });
    this.proc.on("exit", (code) => {
      var _a2;
      const hasWork = this.pending.size > 0 || this.turnPending !== null;
      if (hasWork) {
        const findCmd = import_obsidian.Platform.isWin ? "where.exe kiro-cli" : "which kiro-cli";
        const msg = code === 127 ? `kiro-cli \u304C\u898B\u3064\u304B\u308A\u307E\u305B\u3093 (code 127)\u3002
\u8A2D\u5B9A\u3067\u30D5\u30EB\u30D1\u30B9\u3092\u6307\u5B9A\u3057\u3066\u304F\u3060\u3055\u3044\u3002
\u300C${findCmd}\u300D\u3092\u5B9F\u884C\u3059\u308B\u3068\u30D1\u30B9\u3092\u78BA\u8A8D\u3067\u304D\u307E\u3059\u3002` : code === 0 ? "kiro-cli \u304C\u7D42\u4E86\u3057\u307E\u3057\u305F\u3002\u518D\u63A5\u7D9A\u3057\u3066\u304F\u3060\u3055\u3044\u3002" : `kiro-cli \u304C\u4E88\u671F\u305B\u305A\u7D42\u4E86\u3057\u307E\u3057\u305F (code: ${code})`;
        this.rejectAllPending(new Error(msg));
      }
      this.proc = null;
      this.sessionId = null;
      (_a2 = this.onDisconnect) == null ? void 0 : _a2.call(this);
    });
    await this.request("initialize", {
      protocolVersion: 1,
      clientCapabilities: {},
      clientInfo: { name: "obsidian-kiro-chat", version: "1.0.0" }
    });
    const res = await this.request("session/new", {
      cwd: this.cwd || ".",
      mcpServers: []
    });
    this.sessionId = res.sessionId;
  }
  /** プロンプトを送信する（応答は session/notification 経由、turn_end で完了） */
  prompt(text) {
    var _a;
    if (!this.sessionId)
      throw new Error("\u30BB\u30C3\u30B7\u30E7\u30F3\u304C\u521D\u671F\u5316\u3055\u308C\u3066\u3044\u307E\u305B\u3093");
    if (!((_a = this.proc) == null ? void 0 : _a.stdin))
      throw new Error("\u30D7\u30ED\u30BB\u30B9\u304C\u8D77\u52D5\u3057\u3066\u3044\u307E\u305B\u3093");
    return new Promise((resolve, reject) => {
      this.turnPending = { resolve, reject };
      const id = this.nextId++;
      this.pending.set(id, {
        resolve: () => {
        },
        reject: (e) => {
          var _a2;
          (_a2 = this.turnPending) == null ? void 0 : _a2.reject(e);
          this.turnPending = null;
        }
      });
      const line = JSON.stringify({
        jsonrpc: "2.0",
        id,
        method: "session/prompt",
        params: {
          sessionId: this.sessionId,
          content: [{ type: "text", text }]
        }
      }) + "\n";
      this.proc.stdin.write(line);
    });
  }
  disconnect() {
    if (this.proc) {
      this.proc.kill();
      this.proc = null;
    }
    this.sessionId = null;
  }
  get connected() {
    return this.proc !== null && this.sessionId !== null;
  }
  // ── プライベート ──────────────────────────────────────────────────────
  flush() {
    var _a;
    const lines = this.buf.split("\n");
    this.buf = (_a = lines.pop()) != null ? _a : "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed)
        continue;
      try {
        this.handle(JSON.parse(trimmed));
      } catch (e) {
        console.error("[kiro-acp] JSON parse error:", trimmed);
      }
    }
  }
  writeMsg(obj) {
    var _a, _b;
    const line = JSON.stringify(obj) + "\n";
    (_b = (_a = this.proc) == null ? void 0 : _a.stdin) == null ? void 0 : _b.write(line);
  }
  handle(msg) {
    var _a, _b, _c, _d, _e, _f;
    const id = msg.id;
    const method = msg.method;
    const hasId = id !== void 0 && id !== null;
    if (hasId && method) {
      if (method === "permission/request") {
        this.writeMsg({ jsonrpc: "2.0", id, result: { granted: true } });
      } else {
        this.writeMsg({
          jsonrpc: "2.0",
          id,
          error: { code: -32601, message: `Method not found: ${method}` }
        });
      }
      (_a = this.onStderr) == null ? void 0 : _a.call(this, `[req\u2192] ${method}`);
      return;
    }
    if (hasId && !method && this.pending.has(id)) {
      const p = this.pending.get(id);
      this.pending.delete(id);
      if (msg.error) {
        p.reject(
          new Error(
            (_b = msg.error.message) != null ? _b : "Unknown error"
          )
        );
      } else {
        p.resolve(msg.result);
      }
      return;
    }
    if (!hasId && method === "session/notification") {
      const params = msg.params;
      const data = params == null ? void 0 : params.data;
      if (!data)
        return;
      if (data.type === "agent_message_chunk") {
        const content = data.content;
        for (const part of content != null ? content : []) {
          if (part.type === "text" && part.text)
            (_c = this.onText) == null ? void 0 : _c.call(this, part.text);
        }
      } else if (data.type === "turn_end") {
        (_d = this.onTurnEnd) == null ? void 0 : _d.call(this);
        (_e = this.turnPending) == null ? void 0 : _e.resolve();
        this.turnPending = null;
      }
      return;
    }
    if (method) {
      (_f = this.onStderr) == null ? void 0 : _f.call(this, `[notif] ${method}`);
    }
  }
  request(method, params) {
    return new Promise((resolve, reject) => {
      var _a;
      if (!((_a = this.proc) == null ? void 0 : _a.stdin)) {
        reject(new Error("\u30D7\u30ED\u30BB\u30B9\u304C\u8D77\u52D5\u3057\u3066\u3044\u307E\u305B\u3093"));
        return;
      }
      const id = this.nextId++;
      this.pending.set(id, { resolve, reject });
      const line = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
      this.proc.stdin.write(line);
    });
  }
  rejectAllPending(err) {
    var _a;
    for (const [, p] of this.pending)
      p.reject(err);
    this.pending.clear();
    (_a = this.turnPending) == null ? void 0 : _a.reject(err);
    this.turnPending = null;
  }
};
var KiroChatView = class extends import_obsidian.ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.client = null;
    this.currentBubble = null;
  }
  getViewType() {
    return VIEW_TYPE_KIRO;
  }
  getDisplayText() {
    return "Kiro Chat";
  }
  getIcon() {
    return "bot";
  }
  async onOpen() {
    const root = this.containerEl;
    root.empty();
    root.addClass("kiro-view");
    const header = root.createDiv("kiro-header");
    this.connectBtn = header.createEl("button", {
      text: "\u63A5\u7D9A",
      cls: "kiro-btn"
    });
    this.connectBtn.addEventListener("click", () => this.toggleConnection());
    this.statusEl = header.createEl("span", {
      text: "\u672A\u63A5\u7D9A",
      cls: "kiro-status off"
    });
    this.messagesEl = root.createDiv("kiro-messages");
    const footer = root.createDiv("kiro-footer");
    this.inputEl = footer.createEl("textarea", {
      cls: "kiro-input",
      attr: {
        placeholder: "\u30E1\u30C3\u30BB\u30FC\u30B8\u3092\u5165\u529B\u2026 (Ctrl+Enter \u3067\u9001\u4FE1)",
        rows: "3"
      }
    });
    this.inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.ctrlKey) {
        e.preventDefault();
        this.send();
      }
    });
    this.sendBtn = footer.createDiv("kiro-send-row").createEl("button", {
      text: "\u9001\u4FE1",
      cls: "kiro-btn primary"
    });
    this.sendBtn.disabled = true;
    this.sendBtn.addEventListener("click", () => this.send());
  }
  async onClose() {
    var _a;
    (_a = this.client) == null ? void 0 : _a.disconnect();
  }
  // ── 接続 / 切断 ──────────────────────────────────────────────────────
  async toggleConnection() {
    var _a;
    if ((_a = this.client) == null ? void 0 : _a.connected) {
      this.client.disconnect();
      this.client = null;
      this.setStatus(false);
      this.addSys("\u5207\u65AD\u3057\u307E\u3057\u305F");
    } else {
      await this.connect();
    }
  }
  async connect() {
    const { kiroPath, workingDirectory } = this.plugin.settings;
    const vaultPath = this.app.vault.adapter.basePath;
    const cwd = workingDirectory || vaultPath || ".";
    const cli = new KiroAcpClient(kiroPath, cwd);
    cli.onText = (t) => this.appendText(t);
    cli.onTurnEnd = () => this.onTurnEnd();
    cli.onStderr = (line) => this.addSys(`\u25B6 ${line}`, false, true);
    cli.onDisconnect = () => {
      this.client = null;
      this.setStatus(false);
      this.addSys("\u5207\u65AD\u3055\u308C\u307E\u3057\u305F");
    };
    this.setStatus(false, true);
    this.addSys("\u63A5\u7D9A\u4E2D\u2026");
    try {
      await cli.connect();
      this.client = cli;
      this.setStatus(true);
      this.addSys("\u63A5\u7D9A\u3057\u307E\u3057\u305F\uFF08kiro-cli acp\uFF09");
    } catch (e) {
      this.addSys(`\u63A5\u7D9A\u5931\u6557: ${e.message}`, true);
      this.setStatus(false);
    }
  }
  // ── 送受信 ───────────────────────────────────────────────────────────
  async send() {
    var _a;
    const text = this.inputEl.value.trim();
    if (!text || !((_a = this.client) == null ? void 0 : _a.connected))
      return;
    this.inputEl.value = "";
    this.addBubble("user", text);
    this.setSending(true);
    this.currentBubble = this.messagesEl.createDiv("kiro-bubble assistant");
    this.currentBubble.createEl("span", { text: "Kiro", cls: "kiro-role" });
    this.currentBubble.createDiv("kiro-body");
    this.scroll();
    try {
      await this.client.prompt(text);
    } catch (e) {
      this.addSys(`\u9001\u4FE1\u5931\u6557: ${e.message}`, true);
      this.currentBubble = null;
      this.setSending(false);
    }
  }
  appendText(text) {
    var _a, _b;
    const body = (_a = this.currentBubble) == null ? void 0 : _a.querySelector(
      ".kiro-body"
    );
    if (body)
      body.textContent = ((_b = body.textContent) != null ? _b : "") + text;
    this.scroll();
  }
  onTurnEnd() {
    this.currentBubble = null;
    this.setSending(false);
  }
  // ── UI ヘルパー ──────────────────────────────────────────────────────
  addBubble(role, text) {
    const el = this.messagesEl.createDiv(`kiro-bubble ${role}`);
    el.createEl("span", { text: "You", cls: "kiro-role" });
    el.createDiv("kiro-body").textContent = text;
    this.scroll();
  }
  addSys(text, isError = false, isStderr = false) {
    const el = this.messagesEl.createDiv("kiro-sys");
    if (isError)
      el.addClass("error");
    if (isStderr)
      el.addClass("stderr");
    el.textContent = text;
    this.scroll();
  }
  setStatus(on, connecting = false) {
    if (connecting) {
      this.statusEl.textContent = "\u63A5\u7D9A\u4E2D\u2026";
      this.statusEl.className = "kiro-status off";
      this.connectBtn.textContent = "\u63A5\u7D9A\u4E2D";
      this.connectBtn.disabled = true;
      this.sendBtn.disabled = true;
    } else {
      this.statusEl.textContent = on ? "\u63A5\u7D9A\u4E2D" : "\u672A\u63A5\u7D9A";
      this.statusEl.className = `kiro-status ${on ? "on" : "off"}`;
      this.connectBtn.textContent = on ? "\u5207\u65AD" : "\u63A5\u7D9A";
      this.connectBtn.disabled = false;
      this.sendBtn.disabled = !on;
    }
  }
  setSending(sending) {
    var _a;
    this.sendBtn.disabled = sending || !((_a = this.client) == null ? void 0 : _a.connected);
    this.inputEl.disabled = sending;
  }
  scroll() {
    this.messagesEl.scrollTo({
      top: this.messagesEl.scrollHeight,
      behavior: "smooth"
    });
  }
};
var KiroChatPlugin = class extends import_obsidian.Plugin {
  async onload() {
    await this.loadSettings();
    this.registerView(
      VIEW_TYPE_KIRO,
      (leaf) => new KiroChatView(leaf, this)
    );
    this.addRibbonIcon("bot", "Kiro Chat", () => this.activate());
    this.addCommand({
      id: "open-kiro-chat",
      name: "Kiro Chat \u3092\u958B\u304F",
      callback: () => this.activate()
    });
    this.addSettingTab(new KiroSettingTab(this.app, this));
  }
  async onunload() {
    this.app.workspace.detachLeavesOfType(VIEW_TYPE_KIRO);
  }
  async loadSettings() {
    this.settings = Object.assign(
      {},
      DEFAULT_SETTINGS,
      await this.loadData()
    );
  }
  async saveSettings() {
    await this.saveData(this.settings);
  }
  async activate() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE_KIRO)[0];
    if (!leaf) {
      leaf = workspace.getRightLeaf(false);
      await leaf.setViewState({ type: VIEW_TYPE_KIRO, active: true });
    }
    workspace.revealLeaf(leaf);
  }
};
var KiroSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Kiro Chat \u8A2D\u5B9A" });
    const isWin = import_obsidian.Platform.isWin;
    const findCmd = isWin ? "where.exe kiro-cli" : "which kiro-cli";
    const placeholder = isWin ? "\u4F8B: C:\\Users\\...\\AppData\\Local\\...\\kiro-cli.exe" : "\u4F8B: /home/user/.local/bin/kiro-cli";
    const pathDesc = document.createDocumentFragment();
    pathDesc.append(
      "kiro-cli \u3078\u306E\u30D5\u30EB\u30D1\u30B9\u3002\u63A5\u7D9A\u5931\u6557 (code 127) \u306E\u5834\u5408\u306F\u30D5\u30EB\u30D1\u30B9\u3092\u8A2D\u5B9A\u3057\u3066\u304F\u3060\u3055\u3044\u3002",
      document.createTextNode(" "),
      Object.assign(document.createElement("code"), {
        textContent: findCmd
      }),
      ` \u3067\u30D1\u30B9\u3092\u78BA\u8A8D\u3067\u304D\u307E\u3059\u3002`
    );
    new import_obsidian.Setting(containerEl).setName("kiro-cli \u306E\u30D1\u30B9").setDesc(pathDesc).addText(
      (t) => t.setPlaceholder(placeholder).setValue(this.plugin.settings.kiroPath).onChange(async (v) => {
        this.plugin.settings.kiroPath = v.trim();
        await this.plugin.saveSettings();
      })
    );
    new import_obsidian.Setting(containerEl).setName("\u4F5C\u696D\u30C7\u30A3\u30EC\u30AF\u30C8\u30EA").setDesc(
      "kiro-cli \u3092\u8D77\u52D5\u3059\u308B\u30C7\u30A3\u30EC\u30AF\u30C8\u30EA\uFF08\u7A7A\u306E\u5834\u5408\u306F Vault \u306E\u30EB\u30FC\u30C8\u30C7\u30A3\u30EC\u30AF\u30C8\u30EA\u3092\u4F7F\u7528\uFF09"
    ).addText(
      (t) => t.setPlaceholder("\u4F8B: C:\\Users\\...\\my-project").setValue(this.plugin.settings.workingDirectory).onChange(async (v) => {
        this.plugin.settings.workingDirectory = v.trim();
        await this.plugin.saveSettings();
      })
    );
  }
};
