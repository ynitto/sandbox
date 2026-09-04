'use strict';

// 外部コマンドの起動仕様を組む 1 か所。
//
// Windows で詰まるのはここである。npm のグローバル導入は `playwright-cli.cmd` を置き、
// winauto の Windows インストーラは `winauto.bat` を置く。ところが:
//
//   1. Node の spawn は PATH を引くとき **`.exe` しか補わない**（CreateProcess の規則）。
//      `playwright-cli` という名前だけでは `.cmd` に行き着かず ENOENT になる。
//   2. 見つけたとしても、`.cmd` / `.bat` は直接 spawn できない
//      （新しめの Node は引数の取り違えを避けるために拒む）。`cmd /d /s /c "…"` に載せる。
//
// この 2 つを踏まないと、Windows では「ブラウザを開けませんでした」で記録が始まらない。
// `shell: true` は使わない——引用が Node と cmd の 2 段で解釈され、日本語や空白入りの
// 引数が壊れる。組んだ 1 行をそのまま渡す（windowsVerbatimArguments）。

const fs = require('fs');

const DEFAULT_PATHEXT = '.COM;.EXE;.BAT;.CMD';

function defaultExists(file) {
  try { return fs.statSync(file).isFile(); } catch { return false; }
}

// Windows の PATH から実体を探す。優先順は PATHEXT のまま（Windows 自身がその順で解決する）。
function windowsExecutable(name, { env = process.env, exists = defaultExists } = {}) {
  const raw = String(name || '');
  if (!raw) return '';
  if (/[\\/]/.test(raw)) return exists(raw) ? raw : '';
  const exts = String(env.PATHEXT || DEFAULT_PATHEXT).split(';').map((e) => e.trim()).filter(Boolean);
  const dirs = String(env.PATH || env.Path || '').split(';').map((d) => d.trim()).filter(Boolean);
  for (const dir of dirs) {
    const base = `${dir.replace(/[\\/]+$/, '')}\\${raw}`;
    for (const ext of [...exts, '']) {
      const file = `${base}${ext.toLowerCase()}`;
      if (exists(file)) return file;
    }
  }
  return '';
}

// spawn へ渡す形。win32 以外はそのまま（PATH の解決は OS に任せる）。
function spawnSpec(command, args = [], { cwd = '', env = process.env, platform = process.platform, exists } = {}) {
  const list = args.map((a) => String(a));
  const base = { windowsHide: true, shell: false, env, ...(cwd ? { cwd } : {}) };
  if (platform !== 'win32') return { command, args: list, options: base };
  const file = windowsExecutable(command, { env, exists }) || command;
  if (!/\.(cmd|bat)$/i.test(file)) return { command: file, args: list, options: base };
  const quote = (s) => `"${String(s).replace(/"/g, '""')}"`;
  const line = [quote(file), ...list.map(quote)].join(' ');
  return {
    command: env.COMSPEC || 'cmd.exe',
    // `/s` + 全体を引用符で包む形が cmd の「最初と最後の引用符だけ剥がす」規則に合う。
    args: ['/d', '/s', '/c', `"${line}"`],
    options: { ...base, windowsVerbatimArguments: true },
  };
}

// 画面に出す用（この端末でその名前を起動できるか）。
function resolved(command, opts = {}) {
  const platform = opts.platform || process.platform;
  if (platform !== 'win32') return command;
  return windowsExecutable(command, opts) || '';
}

module.exports = { windowsExecutable, spawnSpec, resolved, DEFAULT_PATHEXT };
