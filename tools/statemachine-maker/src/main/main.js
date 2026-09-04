'use strict';

const { app, BrowserWindow } = require('electron');
const path = require('path');
const { registerIpcHandlers } = require('./ipc');

const SRC_ROOT = path.join(__dirname, '..');
const APP_ROOT = path.join(SRC_ROOT, '..');

let mainWindow = null;

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 980,
    minHeight: 640,
    title: 'Statemachine Maker',
    backgroundColor: '#f6f7f9',
    icon: path.join(APP_ROOT, 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(SRC_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.setMenuBarVisibility(false);
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
