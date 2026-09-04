'use strict';

// 変更ビュー用。作業ツリーの状態と差分を読むだけで、git に書き込む操作は持たない。
// git はホスト（Linux / macOS はローカル、Windows は WSL）のシェルで動かす。CLI が
// 触っているのと同じ git・同じパス表記で見るためで、-C にはホスト表記のパスを渡す。

const host = require('./host');

async function git(shell, cwd, args) {
  const r = await shell.exec(['git', '-C', cwd, ...args], { timeoutMs: 30000 });
  return { ok: r.ok, stdout: r.ok ? r.output : '', stderr: r.ok ? '' : (r.error || r.output) };
}

function parseStatus(text) {
  return text.split('\n').filter(Boolean).map((line) => {
    const code = line.slice(0, 2);
    const file = line.slice(3).replace(/^"(.*)"$/, '$1');
    const label = code === '??' ? '新規' : /D/.test(code) ? '削除' : /R/.test(code) ? '改名' : /A/.test(code) ? '追加' : '変更';
    return { code, file, label };
  });
}

function target(repo, distro) {
  const h = host.hostOf(repo, distro);
  return { shell: h.shell, cwd: h.cwd };
}

async function changes(repo, distro = '') {
  const { shell, cwd } = target(repo, distro);
  const status = await git(shell, cwd, ['status', '--porcelain', '--untracked-files=all']);
  if (!status.ok) return { files: [], diff: '', error: status.stderr.trim() || 'git リポジトリではありません' };
  const files = parseStatus(status.stdout);
  const tracked = await git(shell, cwd, ['diff', 'HEAD', '--no-color', '--no-ext-diff']);
  // HEAD が無い（初回コミット前）ときは index との差分に倒す
  const diff = tracked.ok ? tracked.stdout : (await git(shell, cwd, ['diff', '--no-color', '--no-ext-diff'])).stdout;
  const branch = await git(shell, cwd, ['rev-parse', '--abbrev-ref', 'HEAD']);
  return { files, diff, branch: branch.ok ? branch.stdout.trim() : '', error: '' };
}

async function fileDiff(repo, file, distro = '') {
  const { shell, cwd } = target(repo, distro);
  const tracked = await git(shell, cwd, ['diff', 'HEAD', '--no-color', '--no-ext-diff', '--', file]);
  if (tracked.ok && tracked.stdout) return tracked.stdout;
  // 未追跡はそのまま「全行追加」として見せる（--no-index は差分ありで rc=1 を返す）
  const raw = await shell.run(`git -C ${host.sq(cwd)} diff --no-color --no-index -- /dev/null ${host.sq(file)}; true`, { timeoutMs: 30000 });
  return raw.output;
}

module.exports = { changes, fileDiff, parseStatus };
