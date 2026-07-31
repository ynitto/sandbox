'use strict';

// WSL 起動の事前検査。
//
// Windows から定常業務／CLIチャットの窓を開く経路は
//   Node → cmd /c start "" → wsl.exe → bash → tmux → エージェントCLI
// と段が深い。**どの段で失敗してもコンソールは一瞬で閉じ、原因を持ち去る**。
// tmux から先（セッションが作成直後に消える等）はスクリプト側の生存チェックが受け持つので、
// ここが見るのは **その手前** —— wsl.exe / ディストロ / bash / 起動スクリプトの可視性。
//
// 窓を開く前に Node 側で確かめ、失敗ならウィンドウを開かずアプリの画面へ理由を返す。
// 検査は **起動時と同じ形**（`wsl.exe [-d X] -e bash -lc …`）で撃つ。形が違うと
// 「検査は通るのに本番だけ落ちる」が起こる。
//
// ディストロは推測しない。呼び出し側が cwd（WSL UNC）や ⚙ 設定から決めた値をそのまま検査し、
// 通らなければ黙って別のディストロへ倒さずに報告する——別ディストロで起動しても、
// そこに対象フォルダは無い。

const { spawnSync } = require('child_process');

const NUL_RE = new RegExp(String.fromCharCode(0), 'g');

// bash は動いたが起動スクリプトが見えなかったことを表す終了コード。
// wsl / bash 自体が起動できなかったとき（1・127 等）と区別するために専用の値を使う。
const SCRIPT_MISSING_EXIT = 3;

function sq(s) {
  return `'${String(s).replace(/'/g, `'"'"'`)}'`;
}

// wsl.exe 自身のメッセージは UTF-16LE、ディストロ内のコマンド出力は UTF-8。
// **UTF-8 として妥当かで見分ける**。NUL の多さで見分けると、UTF-16LE の日本語
// （「指定された名前の…」は上位バイトが埋まり NUL がほぼ出ない）を UTF-8 と誤判定し、
// 読ませたい肝心のメッセージほど化ける。
function decodeWslOutput(buf) {
  const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf || '');
  if (!b.length) return '';
  const utf8 = b.toString('utf8');
  const text = utf8.includes('\uFFFD') ? b.toString('utf16le') : utf8;
  return text.replace(NUL_RE, '').replace(/\uFEFF/g, '').trim();
}

function defaultRunner(command, args) {
  const r = spawnSync(command, args, {
    encoding: 'buffer', timeout: 30000, windowsHide: true,
  });
  return {
    status: r.status,
    stdout: decodeWslOutput(r.stdout),
    stderr: decodeWslOutput(r.stderr),
    error: r.error ? r.error.message : '',
  };
}

// 起動時と同じ argv の形。`test -f` まで見るのは、/mnt が見えない（automount 無効）環境で
// 「wsl も bash も動くのにスクリプトだけ読めない」が起こるため。
function launchArgs(distro, scriptPath) {
  return [
    ...(distro ? ['-d', distro] : []),
    '-e', 'bash', '-lc', `test -f ${sq(scriptPath)} || exit ${SCRIPT_MISSING_EXIT}`,
  ];
}

function verifyWslLaunch(scriptPath, distro = '', run = defaultRunner) {
  const r = run('wsl.exe', launchArgs(distro, scriptPath));
  if (r.status === 0) return { ok: true, error: '' };
  return { ok: false, error: explainFailure(r, distro, scriptPath, run) };
}

function explainFailure(r, distro, scriptPath, run) {
  if (r.status === SCRIPT_MISSING_EXIT) {
    return `起動スクリプトが WSL 側から見えません: ${scriptPath}\n`
      + 'WSL から Windows のドライブが見えていない可能性があります'
      + '（/etc/wsl.conf の automount が無効になっていないか確認してください）。';
  }
  const where = distro ? `-d ${distro}` : 'wsl の既定ディストロ';
  const lines = [`WSL でエージェントCLIを起動できませんでした（${where}）。`];
  if (r.error) {
    lines.push('wsl.exe を実行できません（WSL がインストールされているか確認してください）。');
  }
  lines.push(r.error || r.stderr || r.stdout || `終了コード ${r.status}`);
  let listed = '';
  try {
    const l = run('wsl.exe', ['--list', '--verbose']);
    listed = String(l.stdout || l.stderr || '').trim();
  } catch { /* 一覧が取れなくても、上の結果だけで報告する */ }
  if (listed) lines.push(`インストール済みのディストロ:\n${listed}`);
  return lines.join('\n');
}

module.exports = {
  SCRIPT_MISSING_EXIT,
  decodeWslOutput,
  launchArgs,
  verifyWslLaunch,
};
