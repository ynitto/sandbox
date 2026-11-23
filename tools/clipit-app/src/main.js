"use strict";

const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const {
  app, BrowserWindow, clipboard, dialog, globalShortcut, ipcMain, Menu, nativeImage, screen, Tray,
} = require("electron");
const { Store } = require("./store");
const { evaluate } = require("./calc");
const { translate } = require("./translate");
const { resolveHelperCommand, createHelperClient } = require("./helper");
const { loadDevkit } = require("./devkitClient");

app.setName("ClipIt");

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  start();
}

function start() {
  const page = path.join(__dirname, "renderer", "index.html");
  let panel;
  let tray;
  let store;
  let helper;
  let devkit;
  let devkitError;
  let timer;
  let quitting = false;
  let pasting = false;
  let foregroundPending = false;
  let visibilityVersion = 0;
  let target = null;
  let panelHandle;
  let clipboardKey;
  let lastError = null;
  let notice = null;
  let pendingShow = false;
  let suppressBlurUntil = 0;

  function notify() {
    if (panel && !panel.isDestroyed()) panel.webContents.send("clips:changed");
  }

  function showPanel() {
    if (!panel || panel.isDestroyed()) {
      pendingShow = true;
      return;
    }
    if (pasting) return;
    visibilityVersion++;
    const { x, y, width, height } = screen.getPrimaryDisplay().workArea;
    const panelHeight = Math.min(440, Math.floor(height / 2));
    panel.setBounds({ x, y: y + height - panelHeight, width, height: panelHeight });
    panel.show();
    panel.focus();
    notify();
  }

  function togglePanel() {
    if (!panel || panel.isDestroyed() || pasting) return;
    if (panel.isVisible()) panel.hide();
    else showPanel();
  }

  // 画像の変化検知用の指紋。全体の PNG 化＋ハッシュは重いので、
  // ビットマップの寸法と長さ＋等間隔に抜いた断片のハッシュで代用する。
  // 保存時の重複判定は store 側で PNG 全体の sha256 を見るので、ここは粗くてよい。
  function imageFingerprint(image) {
    const { width, height } = image.getSize();
    const bitmap = image.toBitmap();
    const hash = createHash("sha256");
    hash.update(`image:${width}x${height}:${bitmap.length}:`);
    const SAMPLE = 32 * 1024;
    const PARTS = 8;
    if (bitmap.length <= SAMPLE * PARTS) {
      hash.update(bitmap);
    } else {
      for (let i = 0; i < PARTS; i++) {
        const start = Math.floor(((bitmap.length - SAMPLE) * i) / (PARTS - 1));
        hash.update(bitmap.subarray(start, start + SAMPLE));
      }
    }
    return "image:" + hash.digest("hex");
  }

  function writeClipboard(text, image) {
    if (image) {
      clipboard.writeImage(image);
      const written = clipboard.readImage();
      clipboardKey = imageFingerprint(written.isEmpty() ? image : written);
    } else {
      clipboard.writeText(text);
      clipboardKey = text ? "text:" + text : null;
    }
  }

  function captureClipboard() {
    try {
      const text = clipboard.readText();
      const image = clipboard.readImage();
      const hasImage = !image.isEmpty();
      const key = hasImage ? imageFingerprint(image) : text ? "text:" + text : null;
      if (key === clipboardKey) return;
      if (hasImage) store.addImage(image.toPNG());
      else if (text) store.addText(text);
      clipboardKey = key;
      if (key !== null) notify();
    } catch (error) {
      const message = `クリップボード履歴を保存できませんでした（${error.message}）`;
      if (lastError !== message) {
        lastError = message;
        notify();
      }
    }
  }

  async function rememberTarget() {
    if (process.platform !== "win32" || !helper || panel.isVisible() || pasting || foregroundPending) return;
    foregroundPending = true;
    const version = visibilityVersion;
    try {
      // HWND は10進文字列で受け取る（数値では桁落ちしうるため）。
      const { window } = await helper.run("get-foreground", {}, { timeoutMs: 3000 });
      if (version === visibilityVersion && !pasting && typeof window === "string" &&
          /^[1-9][0-9]*$/.test(window) && window !== panelHandle) target = window;
    } catch {
      if (version === visibilityVersion) target = null;
    } finally {
      foregroundPending = false;
    }
  }

  function clipById(id) {
    const clip = store.clips.find((item) => item.id === id);
    if (!clip) throw new Error("クリップが見つかりません。");
    return clip;
  }

  function imagePath(id) {
    const clip = clipById(id);
    if (clip.kind !== "image" || !/^[a-f0-9]{32}$/i.test(clip.id)) {
      throw new Error("画像クリップが見つかりません。");
    }
    return store.imagePath(clip);
  }

  function addText(text) {
    if (typeof text !== "string" || !text) throw new Error("テキストを入力してください。");
    const clip = store.addText(text);
    notify();
    return clip;
  }

  function handle(channel, action) {
    ipcMain.handle(channel, async (event, input) => {
      // 呼び出し元は自前のパネルのメインフレームだけに限る。
      // URL 比較は Windows のパス正規化で一致しないことがあるため使わない。
      if (event.sender !== panel.webContents || event.senderFrame !== panel.webContents.mainFrame) {
        return { ok: false, error: "IPC の呼び出し元が無効です。" };
      }
      try {
        return await action(input);
      } catch (error) {
        return { ok: false, error: error.message };
      }
    });
  }

  function registerHandlers() {
    handle("state:get", () => {
      const state = { clips: store.clips, notes: store.notes, lastError, notice };
      if (panel.isVisible()) notice = null;
      return state;
    });
    handle("clips:add-text", ({ text }) => addText(text));
    handle("clips:add-image", ({ pngBase64 }) => {
      const png = Buffer.from(pngBase64, "base64");
      const pngSignature = Buffer.from("89504e470d0a1a0a", "hex");
      if (!png.subarray(0, 8).equals(pngSignature) || nativeImage.createFromBuffer(png).isEmpty()) {
        throw new Error("PNG 画像を読み込めません。");
      }
      const clip = store.addImage(png);
      notify();
      return clip;
    });
    handle("clips:image", ({ id }) => fs.readFileSync(imagePath(id)).toString("base64"));
    handle("clips:update", ({ id, changes }) => {
      clipById(id);
      const update = {};
      if (typeof changes.pinned === "boolean") update.pinned = changes.pinned;
      if (Array.isArray(changes.tags) && changes.tags.every((tag) => typeof tag === "string")) {
        update.tags = [...new Set(changes.tags.map((tag) => tag.trim()).filter(Boolean))];
      }
      store.updateClip(id, update);
      notify();
    });
    handle("clips:delete", ({ id }) => {
      store.deleteClip(id);
      notify();
    });
    handle("notes:save", ({ notes }) => {
      if (!Array.isArray(notes) || !notes.every((note) =>
        typeof note?.text === "string" && typeof note.checked === "boolean")) {
        throw new Error("メモの形式が無効です。");
      }
      store.saveNotes(notes.map(({ text, checked }) => ({ text, checked })));
      notify();
    });
    handle("notes:copy", ({ text }) => {
      if (typeof text !== "string" || !text.trim()) throw new Error("メモが空です。");
      writeClipboard(text);
      return addText(text);
    });
    handle("clips:paste", async ({ id }) => {
      if (pasting || !panel.isVisible()) return;
      const clip = clipById(id);
      pasting = true;
      try {
        if (clip.kind === "image") {
          const image = nativeImage.createFromPath(imagePath(id));
          if (image.isEmpty()) throw new Error("画像ファイルを読み込めません。");
          writeClipboard(null, image);
        } else {
          writeClipboard(clip.text);
        }
        panel.hide();
        if (helper && target !== null) {
          try {
            await helper.run("paste", { window: target });
            return;
          } catch (error) {
            notice = `クリップボードにコピーしました。Ctrl+V で貼り付けてください。（${error.message}）`;
            return;
          }
        }
        notice = "クリップボードにコピーしました。Ctrl+V で貼り付けてください。";
      } finally {
        pasting = false;
        // 貼り戻し直後に遅れて届く blur で、開き直したパネルが消えるのを防ぐ。
        suppressBlurUntil = Date.now() + 500;
      }
    });
    handle("text:translate", ({ text }) => translate(text));
    handle("clips:ocr", async ({ id }) => {
      if (!helper) throw new Error("ClipIt ヘルパーが見つかりません。");
      const result = await helper.run("ocr", { path: imagePath(id) });
      return result.text;
    });
    handle("calc:evaluate", ({ expression }) => evaluate(expression));
    handle("devkit:list", () => ({ commands: devkit?.listCommands() ?? [], reason: devkitError }));
    handle("devkit:run", async ({ name, argv, stdin }) => {
      if (!devkit) throw new Error(devkitError);
      const { code, stdout, stderr } = await devkit.runCommand(name, argv, { stdin });
      return { code, stdout, stderr };
    });
    handle("panel:hide", () => panel.hide());
    handle("app:quit", () => { setImmediate(() => app.quit()); });
  }

  app.on("window-all-closed", () => {});
  app.on("second-instance", showPanel);
  app.on("activate", showPanel);
  app.on("before-quit", () => {
    quitting = true;
    clearInterval(timer);
    globalShortcut.unregisterAll();
    if (tray && !tray.isDestroyed()) tray.destroy();
  });

  app.whenReady().then(async () => {
    store = new Store(path.join(app.getPath("userData"), "data"));
    const locations = { resourcesPath: app.isPackaged ? process.resourcesPath : undefined, appDir: app.getAppPath() };
    const resolved = resolveHelperCommand(locations);
    if (resolved) helper = createHelperClient(resolved);
    try {
      devkit = loadDevkit(locations);
    } catch (error) {
      devkitError = error.message;
    }

    panel = new BrowserWindow({
      title: "ClipIt",
      show: false,
      frame: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    if (process.platform === "win32") {
      const handle = panel.getNativeWindowHandle();
      panelHandle = handle.length === 8
        ? handle.readBigUInt64LE().toString()
        : String(handle.readUInt32LE());
    }
    panel.on("blur", () => {
      if (!pasting && Date.now() >= suppressBlurUntil) panel.hide();
    });
    panel.on("close", (event) => {
      if (quitting) return;
      event.preventDefault();
      panel.hide();
    });
    panel.webContents.on("will-navigate", (event) => event.preventDefault());
    panel.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    registerHandlers();
    await panel.loadFile(page);
    if (pendingShow) {
      pendingShow = false;
      showPanel();
    }

    let trayError;
    try {
      // A small BGRA clipboard icon keeps the tray visible without a separate asset.
      const bitmap = Buffer.alloc(16 * 16 * 4);
      for (let y = 1; y < 15; y++) {
        for (let x = 2; x < 14; x++) {
          const border = x < 4 || x > 11 || y < 4 || y > 12 || (y % 3 === 0 && x < 10);
          bitmap.set(border ? [145, 87, 35, 255] : [255, 249, 236, 255], (y * 16 + x) * 4);
        }
      }
      tray = new Tray(nativeImage.createFromBitmap(bitmap, { width: 16, height: 16 }));
      tray.setToolTip("ClipIt — Ctrl+Shift+V");
      tray.setContextMenu(Menu.buildFromTemplate([
        { label: "表示", click: showPanel },
        { label: "終了", click: () => app.quit() },
      ]));
      tray.on("click", togglePanel);
    } catch (error) {
      trayError = `トレイを初期化できませんでした（${error.message}）`;
    }

    let registered = false;
    try {
      registered = globalShortcut.register("Control+Shift+V", togglePanel);
    } catch (error) {
      lastError = error.message;
    }
    if (!registered) {
      lastError = ["Ctrl+Shift+V を登録できませんでした。", lastError].filter(Boolean).join(" ");
    }
    if (trayError) lastError = [lastError, trayError].filter(Boolean).join(" ");
    if (!registered || trayError) showPanel();

    const poll = () => {
      captureClipboard();
      void rememberTarget();
    };
    poll();
    timer = setInterval(poll, 500);
  }).catch((error) => {
    dialog.showErrorBox("ClipIt", `ClipIt を起動できませんでした。\n${error.message}`);
    app.quit();
  });
}
