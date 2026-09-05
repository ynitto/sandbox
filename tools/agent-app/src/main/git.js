'use strict';

// 変更ビュー用。作業ツリーの状態と差分を読むだけで、git に書き込む操作は持たない
// （書くのは worktree.js の worktree 追加・削除だけ）。
// git はホスト（Linux / macOS はローカル、Windows は WSL）のシェルで動かす。CLI が
// 触っているのと同じ git・同じパス表記で見るためで、-C にはホスト表記のパスを渡す。
//
// 見る対象は「会話の作業フォルダ」。worktree を使う会話ではそのフォルダを指すので、
// 本体の変更と混ざらない。worktree では作業ツリーの変更だけでなく、分岐元から積んだ
// コミット（`<base>...HEAD`）も見られる——それがブランチを分けた成果そのものだから。

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

// `git diff <base>...HEAD` = 分岐元（merge-base）から HEAD まで。base が無い・共通祖先が
// 無いときは空にする（`...` は片方が解決できないと全体を出してしまう）。
async function branchDiff(shell, cwd, base) {
  if (!base) return { files: [], diff: '', error: '' };
  const mb = await git(shell, cwd, ['merge-base', base, 'HEAD']);
  if (!mb.ok || !mb.stdout.trim()) return { files: [], diff: '', error: `分岐元を決められません（${base}）` };
  const from = mb.stdout.trim();
  const names = await git(shell, cwd, ['diff', '--name-status', '--no-color', from, 'HEAD']);
  const files = names.stdout.split('\n').filter(Boolean).map((line) => {
    const [code, ...rest] = line.split('\t');
    const file = rest[rest.length - 1] || '';
    const label = code.startsWith('A') ? '追加' : code.startsWith('D') ? '削除' : code.startsWith('R') ? '改名' : '変更';
    return { code, file, label };
  });
  const diff = await git(shell, cwd, ['diff', '--no-color', '--no-ext-diff', from, 'HEAD']);
  return { files, diff: diff.stdout, error: '' };
}

// scope: 'worktree'（作業ツリーの変更）| 'branch'（分岐元から積んだコミット）
async function changes(hostDir, distro = '', { scope = 'worktree', base = '' } = {}) {
  const shell = host.shellFor(distro);
  const head = await git(shell, hostDir, ['rev-parse', '--abbrev-ref', 'HEAD']);
  if (!head.ok) return { files: [], diff: '', branch: '', ahead: 0, error: head.stderr.trim() || 'git リポジトリではありません' };
  const branch = head.stdout.trim();
  if (scope === 'branch') {
    const res = await branchDiff(shell, hostDir, base);
    return { ...res, branch, scope: 'branch' };
  }
  const status = await git(shell, hostDir, ['status', '--porcelain', '--untracked-files=all']);
  if (!status.ok) return { files: [], diff: '', branch, error: status.stderr.trim() || 'git リポジトリではありません' };
  const files = parseStatus(status.stdout);
  const tracked = await git(shell, hostDir, ['diff', 'HEAD', '--no-color', '--no-ext-diff']);
  // HEAD が無い（初回コミット前）ときは index との差分に倒す
  const diff = tracked.ok ? tracked.stdout : (await git(shell, hostDir, ['diff', '--no-color', '--no-ext-diff'])).stdout;
  return { files, diff, branch, scope: 'worktree', error: '' };
}

async function fileDiff(hostDir, file, distro = '', { scope = 'worktree', base = '' } = {}) {
  const shell = host.shellFor(distro);
  if (scope === 'branch' && base) {
    const mb = await git(shell, hostDir, ['merge-base', base, 'HEAD']);
    if (mb.ok && mb.stdout.trim()) {
      const r = await git(shell, hostDir, ['diff', '--no-color', '--no-ext-diff', mb.stdout.trim(), 'HEAD', '--', file]);
      return r.stdout;
    }
    return '';
  }
  const tracked = await git(shell, hostDir, ['diff', 'HEAD', '--no-color', '--no-ext-diff', '--', file]);
  if (tracked.ok && tracked.stdout) return tracked.stdout;
  // 未追跡はそのまま「全行追加」として見せる（--no-index は差分ありで rc=1 を返す）
  const raw = await shell.run(`git -C ${host.sq(hostDir)} diff --no-color --no-index -- /dev/null ${host.sq(file)}; true`, { timeoutMs: 30000 });
  return raw.output;
}

module.exports = { changes, fileDiff, parseStatus, branchDiff };
