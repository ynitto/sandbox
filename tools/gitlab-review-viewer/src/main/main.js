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

function createWindow() {
  const win = new BrowserWindow({
    width: 1600,
    height: 950,
    title: 'GitLab Review Viewer',
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
  return win;
}

app.whenReady().then(() => {
  registerIpcHandlers();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
