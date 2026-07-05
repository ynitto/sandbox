'use strict';

const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const { registerIpcHandlers } = require('./ipc');

// 環境変数のプロキシ設定を Chromium に引き継ぐ。webview の表示と
// net.fetch 経由の GitLab API 呼び出しの両方がこの設定を経由する。
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

// gitlab-review-viewer://open?url=<GitLab の web_url> のディープリンク。
// kiro-projects-viewer などの外部ツールが「このイシュー / MR をレビューで
// 開いて」と指示するための入り口。renderer に転送して対象を解決させる。
function handleDeepLink(url) {
  if (!url || !url.startsWith('gitlab-review-viewer://')) return;
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
  mainWindow.webContents.send('app:openTarget', { url });
}

function deepLinkFromArgv(argv) {
  return argv.find((a) => typeof a === 'string' && a.startsWith('gitlab-review-viewer://'));
}

function registerProtocolClient() {
  // 開発起動（electron .）でも OS にプロトコルを登録できるようにする
  if (process.defaultApp) {
    if (process.argv.length >= 2) {
      app.setAsDefaultProtocolClient('gitlab-review-viewer', process.execPath, [
        path.resolve(process.argv[1]),
      ]);
    }
  } else {
    app.setAsDefaultProtocolClient('gitlab-review-viewer');
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1600,
    height: 950,
    title: 'GitLab Review Viewer',
    // dist:portable の exe アイコンと同じものを開発起動（electron .）時にも使う
    icon: path.join(__dirname, '..', '..', 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // GitLab ページの埋め込み表示に <webview> を使う
      webviewTag: true,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  // webview 内から window.open されたリンクは OS 既定ブラウザで開く
  win.webContents.on('did-attach-webview', (_event, contents) => {
    contents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//.test(url)) shell.openExternal(url);
      return { action: 'deny' };
    });
  });
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
  });
  mainWindow = win;
  return win;
}

// ディープリンクを 1 つのウィンドウで受けるためシングルインスタンスにする。
// 2 個目の起動は既存ウィンドウへ URL を転送して終了する。
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
