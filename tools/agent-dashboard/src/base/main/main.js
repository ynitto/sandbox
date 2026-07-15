'use strict';

const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const { registerIpcHandlers } = require('./ipc');

// 環境変数のプロキシ設定を Chromium に引き継ぐ（gitlab-review-viewer と同じ）。
// GitLab API 呼び出し（net.fetch）がこの設定を経由する。
// app.ready より前に設定する必要がある。
function applyProxyFromEnv() {
  const env = process.env;
  const httpProxy = env.HTTP_PROXY || env.http_proxy || '';
  const httpsProxy = env.HTTPS_PROXY || env.https_proxy || '';
  const allProxy = env.ALL_PROXY || env.all_proxy || '';
  const noProxy = env.NO_PROXY || env.no_proxy || '';

  let server = '';
  if (httpProxy && httpsProxy && httpProxy !== httpsProxy) {
    server = `http=${httpProxy};https=${httpsProxy}`;
  } else {
    server = httpsProxy || httpProxy || allProxy;
  }
  if (!server) return;

  app.commandLine.appendSwitch('proxy-server', server);
  if (noProxy) {
    const bypass = noProxy
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .join(';');
    if (bypass) app.commandLine.appendSwitch('proxy-bypass-list', bypass);
  }
}

applyProxyFromEnv();

let mainWindow = null;

// src/base/main → src/ → assets/ / preload / renderer
const SRC_ROOT = path.join(__dirname, '..', '..');
const APP_ROOT = path.join(SRC_ROOT, '..');

function createWindow() {
  const win = new BrowserWindow({
    width: 1600,
    height: 950,
    title: 'Agent Dashboard',
    // dist:portable の exe アイコンと同じものを開発起動（electron .）時にも使う
    icon: path.join(APP_ROOT, 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(SRC_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // preload.js が features（src/features/*）をローカル require するため、
      // sandbox 化された preload（Electron 20+ の既定）では読み込めない。
      // フル Node コンテキストで preload を走らせて require('./features') を許可する。
      // 読み込むのはローカル index.html のみ（リモートコンテンツなし）なので安全。
      sandbox: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(SRC_ROOT, 'renderer', 'index.html'));
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
  });
  mainWindow = win;
  return win;
}

// agent-dashboard://open?root=...&project=... のディープリンクで
// 特定プロジェクトを直接開けるようにしておく（他ツールからの起動口）。
function handleDeepLink(url) {
  if (!url || !url.startsWith('agent-dashboard://')) return;
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
  mainWindow.webContents.send('app:openTarget', { url });
}

function deepLinkFromArgv(argv) {
  return argv.find((a) => typeof a === 'string' && a.startsWith('agent-dashboard://'));
}

function registerProtocolClient() {
  // 開発起動（electron .）でも OS にプロトコルを登録できるようにする
  if (process.defaultApp) {
    if (process.argv.length >= 2) {
      app.setAsDefaultProtocolClient('agent-dashboard', process.execPath, [
        path.resolve(process.argv[1]),
      ]);
    }
  } else {
    app.setAsDefaultProtocolClient('agent-dashboard');
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  registerProtocolClient();

  app.on('second-instance', (_event, argv) => {
    const url = deepLinkFromArgv(argv);
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
    if (url) handleDeepLink(url);
  });

  // macOS のディープリンク
  app.on('open-url', (_event, url) => handleDeepLink(url));

  app.whenReady().then(() => {
    registerIpcHandlers();
    createWindow();
    const url = deepLinkFromArgv(process.argv);
    if (url) {
      mainWindow.webContents.once('did-finish-load', () => handleDeepLink(url));
    }
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}
