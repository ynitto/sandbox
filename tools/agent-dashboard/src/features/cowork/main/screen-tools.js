'use strict';

// 画面操作の道具（`playwright-cli` / `winauto`）を**どちら側で呼ぶか**を決める 1 か所。
//
// この 2 つは **Windows のデスクトップを触る道具**である。AI の解釈・拡張（エージェント CLI・
// agent-loop・statemachine-use のハーネス）が WSL 側にあるのとは立場が逆で、WSL から呼ぶと
// 遠回りになる:
//
//   * `winauto` … WSL のラッパーは結局 Windows の `python.exe` を exec する。Electron →
//     `wsl.exe` → bash → `python.exe` と 2 回跨ぐぶん、パス変換とロケールの取り違えが増える。
//     **Windows にしか入れていない端末では、WSL 側に実体が無いので「未準備」に見えてしまう。**
//   * `playwright-cli` … WSL の中でブラウザを開こうとする。WSLg の無い環境では見える形で
//     開けず、人の操作を記録できない。Windows 側なら普段使いのブラウザがそのまま出る。
//
// そこで **win32 では Windows 側の実体を先に探し、見つかればそれを直接呼ぶ**。見つからなければ
// 従来どおり `wsl.exe` 経由へ落ちる——片側にしか入れていない端末でも動き続ける。
// 非 Windows（Linux / macOS の dashboard）は従来どおりその場の PATH から呼ぶ。
//
// **どちら側かは一時ファイルの綴りも決める。** Windows 側の `winauto` に `/tmp/...` を渡しても
// 書けないし、WSL 側へ `C:\...` を渡しても同じである。置き場（`tempDir`）と読み書き
// （`fileOps`）をこのモジュールが一緒に配るのはそのためで、呼ぶ側が両者を取り違えられない。
//
// 決めるのはここだけで、ここは Electron に触れない（プラットフォームを差し替えて検査できる）。

const fs = require('fs');

// この扱いをする道具。**AI の解釈・拡張の道具は入れない**（あちらは WSL 側が正しい）。
const SCREEN_TOOLS = ['playwright-cli', 'winauto'];

const TEMP_LEAF = 'agent-dashboard';
const DEFAULT_PATHEXT = '.COM;.EXE;.BAT;.CMD';

function defaultExists(file) {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

// Windows の PATH から実体を探す。npm のグローバル導入は `playwright-cli.cmd`、
// winauto の Windows インストーラは `winauto.bat` を置くので、**拡張子を補って探す**
// （拡張子なしの名前だけを見る POSIX 流の探索では、どちらも見つからない）。
// 優先順は PATHEXT のまま——Windows 自身がその順で解決するので、ここで独自の順を作らない。
function windowsExecutable(name, { env = process.env, exists = defaultExists } = {}) {
  const exts = String(env.PATHEXT || DEFAULT_PATHEXT)
    .split(';').map((e) => e.trim()).filter(Boolean);
  const dirs = String(env.PATH || env.Path || '').split(';').map((d) => d.trim()).filter(Boolean);
  for (const dir of dirs) {
    const base = `${dir.replace(/[\\/]+$/, '')}\\${name}`;
    for (const ext of [...exts, '']) {
      const file = `${base}${ext.toLowerCase()}`;
      if (exists(file)) return file;
    }
  }
  return '';
}

// 道具 1 つの呼び方。`where` は人へ見せる用（「Windows 側で見つかりました」）。
function resolveScreenTool(name, { platform = process.platform, env = process.env, exists } = {}) {
  if (platform !== 'win32') {
    return { name, native: false, command: name, where: 'local' };
  }
  const file = windowsExecutable(name, { env, exists });
  return file
    ? { name, native: true, command: file, where: 'windows' }
    : { name, native: false, command: name, where: 'wsl' };
}

const WHERE_LABEL = { windows: 'Windows 側', wsl: 'WSL 側', local: '' };

function whereLabel(resolved) {
  return WHERE_LABEL[(resolved && resolved.where) || 'local'] || '';
}

// 一時ファイルの置き場。**実体を呼ぶ側の綴り**で返す。
function tempDir(resolved, { env = process.env } = {}) {
  if (!resolved || !resolved.native) return `/tmp/${TEMP_LEAF}`;
  const base = String(env.TEMP || env.TMP || 'C:\\Windows\\Temp').replace(/[\\/]+$/, '');
  return `${base}\\${TEMP_LEAF}`;
}

// 一時ファイルの読み書き。ネイティブ側は main プロセスから直接触れる（同じ Windows なので
// fs で足りる）。WSL 側は実体があちらにあるので、実行と同じ `wsl.exe` 越しのコマンドで触る
// ——読む側と書く側が別の実体を見ないように、経路を実行に揃える。
function fileOps(resolved, { capture, cwd = '', env = process.env, fsImpl = fs, timeoutMs = 20000 } = {}) {
  const dir = tempDir(resolved, { env });
  const native = !!(resolved && resolved.native);
  if (native) {
    return {
      dir,
      native,
      join: (name) => `${dir}\\${name}`,
      prepare: async () => { fsImpl.mkdirSync(dir, { recursive: true }); },
      touch: async (file) => { fsImpl.writeFileSync(file, ''); },
      read: async (file) => {
        try {
          return fsImpl.readFileSync(file, 'utf8');
        } catch {
          return '';        // まだ無い・読めないは「空」。呼ぶ側が空を判断する
        }
      },
      remove: async (files) => {
        for (const file of files) {
          try { fsImpl.unlinkSync(file); } catch { /* 消せなくても実行は続ける */ }
        }
      },
    };
  }
  if (typeof capture !== 'function') throw new Error('WSL 側の一時ファイルを触る実行関数がありません');
  const run = (command, args) => capture(command, args, { cwd, timeoutMs });
  return {
    dir,
    native,
    join: (name) => `${dir}/${name}`,
    prepare: async () => {
      const res = await run('mkdir', ['-p', dir]);
      if (!res || !res.ok) {
        throw new Error(`記録の置き場を作れませんでした: ${(res && res.error) || dir}`);
      }
    },
    touch: async (file) => { await run('touch', [file]); },
    read: async (file) => {
      const res = await run('cat', [file]);
      return res && res.ok ? String(res.stdout || '') : '';
    },
    remove: async (files) => { await run('rm', ['-f', ...files]); },
  };
}

module.exports = {
  SCREEN_TOOLS,
  TEMP_LEAF,
  windowsExecutable,
  resolveScreenTool,
  whereLabel,
  tempDir,
  fileOps,
};
