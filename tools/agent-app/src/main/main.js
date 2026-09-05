'use strict';

const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const { registerIpcHandlers } = require('./ipc');

const SRC_ROOT = path.join(__dirname, '..');

let mainWindow = null;

function createWindow() {
  const win = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 900,
    minHeight: 600,
    title: 'Agent App',
    backgroundColor: '#f6f7f9',
    webPreferences: {
      preload: path.join(SRC_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.setMenuBarVisibility(false);
  // Markdown プレビューや応答の中のリンクで画面が遷移しないように。外部 URL は既定のブラウザへ。
  win.webContents.on('will-navigate', (e, url) => { e.preventDefault(); if (/^https?:/i.test(url)) shell.openExternal(url); });
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https?:/i.test(url)) shell.openExternal(url); return { action: 'deny' }; });
  win.loadFile(path.join(SRC_ROOT, 'renderer', 'index.html'));
  win.on('closed', () => { if (mainWindow === win) mainWindow = null; });
  mainWindow = win;
  return win;
}

app.whenReady().then(() => {
  registerIpcHandlers(() => mainWindow);
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
