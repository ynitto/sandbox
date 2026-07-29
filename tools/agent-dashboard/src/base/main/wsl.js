'use strict';

// WSL ディストロ名の解決。Windows 側から使う経路が 2 つあり、どちらも同じ名前を必要とする:
//   ・定常業務／CLIチャットのウィンドウ起動（`wsl.exe -d <name> -e bash -lc …`）
//   ・POSIX 絶対パス → UNC の翻訳（`\\wsl.localhost\<name>\…`。project.toViewerPath）
//
// **`wsl --list --quiet` の出力エンコーディングは一定ではない。** 既定は UTF-16LE だが、
// `WSL_UTF8=1` を設定した環境や WSL のバージョンによっては UTF-8 で出る。片方に決め打つと
// 化けた文字列（例: "Ubuntu-22.04" の UTF-8 バイト列を UTF-16LE として解くと "払湵畴㈭⸲㐰"）が
// そのまま名前として通り、
//   ・起動経路: `-d 払湵畴㈭⸲㐰` →「指定された名前のディストリビューションはありません。」で即死
//   ・翻訳経路: 存在しない UNC を指し、フォルダが見つからない
// になる。両方のエンコーディングで解いて、**ディストロ名として成立する方**を採る。

const { spawnSync } = require('child_process');

// 登録名として通す文字。ASCII に限る——化けた出力を弾くのがこの検査の目的なので、
// 判定は「厳しすぎるくらい」で良い。非 ASCII 名（`wsl --import` なら理屈上は作れる）は
// 名前無しへ倒れる＝ wsl の既定ディストロで起動する従来動作になるだけで、壊れはしない。
const DISTRO_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/;

// bash を持たない補助ディストロ。これが既定に選ばれている環境があり、そこで
// `-e bash` を撃つと即死してコンソールが一瞬で閉じる（原因が見えない）。
const UTILITY_DISTRO_RE = /^(?:docker-desktop|rancher-desktop|podman-machine)/i;

const NUL_RE = new RegExp(String.fromCharCode(0), 'g');

// `wsl --list --quiet` の出力バッファ → ディストロ名の配列（登録順。先頭が既定の想定）。
// 解読できない・1 つも名前として成立しないときは空配列（呼び出し側は「名前を決めない」へ倒す）。
function parseWslDistroList(buf) {
  const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf || '');
  if (!b.length) return [];
  // UTF-16LE を先に試す（wsl の既定）。UTF-16LE のバイト列を utf8 として解いても
  // NUL 除去で ASCII 名は復元できるので、どちらの順でも ASCII 名なら同じ答えに落ちる。
  for (const enc of ['utf16le', 'utf8']) {
    const names = b.toString(enc)
      .replace(NUL_RE, '')
      .split(/\r?\n/)
      // BOM（U+FEFF）と前後空白を落とす。JS の trim は U+FEFF も空白として扱う。
      .map((line) => line.trim())
      .filter((line) => DISTRO_NAME_RE.test(line));
    if (names.length) return names;
  }
  return [];
}

let _cache = { at: 0, names: null };
const CACHE_TTL_MS = 60000;

// インストール済みディストロ名。win32 以外・wsl.exe が無い環境では空配列。
function listWslDistros() {
  if (process.platform !== 'win32') return [];
  const now = Date.now();
  if (_cache.names && now - _cache.at < CACHE_TTL_MS) return _cache.names;
  let names = [];
  try {
    const r = spawnSync('wsl.exe', ['--list', '--quiet'], {
      encoding: 'buffer', timeout: 8000, windowsHide: true,
    });
    if (r.status === 0) names = parseWslDistroList(r.stdout);
  } catch {
    /* wsl.exe が無い環境 */
  }
  _cache = { at: now, names };
  return names;
}

function clearCache() {
  _cache = { at: 0, names: null };
}

// bash を持つ見込みの通常ディストロ（＝ `-e bash` を撃てる相手）。決められなければ ''。
// '' は「`-d` を付けない」＝ wsl の既定ディストロに任せる、という意味で使う——推測した
// 名前を押し付けるより、確実に存在する既定へ倒す方が安全側。
function defaultWslDistro() {
  return listWslDistros().find((n) => !UTILITY_DISTRO_RE.test(n)) || '';
}

module.exports = {
  DISTRO_NAME_RE,
  UTILITY_DISTRO_RE,
  parseWslDistroList,
  listWslDistros,
  defaultWslDistro,
  clearCache,
};
